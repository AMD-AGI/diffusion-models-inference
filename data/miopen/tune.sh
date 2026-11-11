#!/bin/bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

# setup identities
HOST_GID=${HOST_GID:-$(id -g)}
HOST_UID=${HOST_UID:-$(id -u)}

# set repository root directory
WORKDIR=$(pwd)
ROOTDIR="${ROOTDIR:-/app/diffusion-models-inference}"

# set MIOpen ENVs
MIOPEN_USER_DB_PATH=${MIOPEN_USER_DB_PATH:-$ROOTDIR/data/miopen/userdb}
MIOPEN_FIND_MODE=${MIOPEN_FIND_MODE:-1}
MIOPEN_FIND_ENFORCE=${MIOPEN_FIND_ENFORCE:-4}
MIOPEN_DEBUG_CONV_DIRECT=${MIOPEN_DEBUG_CONV_DIRECT:-0}

ARCH=${ARCH:-unknown}
FORCE_RETUNING=${FORCE_RETUNING:-false}

DB_PREFIX=$("$ROOTDIR/data/miopen/resolve_prefix.sh")
if [ -n "$DB_PREFIX" ]; then
    echo "Detected MIOpen DB prefix: $DB_PREFIX"
    echo "$DB_PREFIX" > "$ROOTDIR/.miopen_db_prefix"
else
    echo "Could not resolve MIOpen DB prefix from rocminfo, tuning will run from scratch"
fi

if [ "$FORCE_RETUNING" = "true" ] && [ -n "$DB_PREFIX" ]; then
    echo "Force retuning enabled, removing existing tuning databases"
    echo "Removing database files matching: ${DB_PREFIX}*.{udb,ufdb}.txt"
    rm -f -- "${MIOPEN_USER_DB_PATH}/${DB_PREFIX}"*.udb.txt
    rm -f -- "${MIOPEN_USER_DB_PATH}/${DB_PREFIX}"*.ufdb.txt
    echo "Removed existing tuning databases"
fi

# glob, concatenate and retain unique MIOpen driver commands
echo "Extracting workload MIOpenDriver calls"
sed -s "\$a\\" "$ROOTDIR"/data/miopen/workloads/*.txt | uniq -u | sort -u > "$ROOTDIR/drivercmd.txt"

# filter out already-tuned commands
echo "Filtering already-tuned commands"
python "$ROOTDIR/src/miopen_convolution/filter_tuned_commands.py" \
    "$ROOTDIR/drivercmd.txt" \
    "$ROOTDIR/drivercmd_filtered.txt" \
    --db-path "$MIOPEN_USER_DB_PATH" \
    --db-prefix "$DB_PREFIX"

echo "$(wc -l < "$ROOTDIR/drivercmd_filtered.txt") commands need tuning"

# find MIOpenDriver executable
echo "Searching for MIOpenDriver executable"
miopendriver_path=$(find / -type f -name MIOpenDriver -executable 2> /dev/null | head -n 1)

if [ -z "$miopendriver_path" ]; then
    echo "Executable MIOpenDriver not found."
    exit 1
fi

# create a symbolic link to /bin
echo "Creating a symbolic link to MIOpenDriver"
ln -s "$miopendriver_path" /bin/MIOpenDriver

# run MIOpen tuning
echo "Executing MIOpen tuning"
cd "$ROOTDIR/src/distrituner" || exit 1
if ! python miopen_tuner.py "$ROOTDIR/drivercmd_filtered.txt" \
    --tuning-output-path "$WORKDIR/tuning" --log-dir "$WORKDIR/logs" \
    --miopen-find-mode "$MIOPEN_FIND_MODE" --miopen-find-enforce "$MIOPEN_FIND_ENFORCE" \
    --miopen-debug-conv-direct "$MIOPEN_DEBUG_CONV_DIRECT"; then
    echo "MIOpen tuning failed"
    exit 1
fi

# merge results by convolution key
tuningdirs=("$WORKDIR"/tuning/device*/)

if [ ! -d "${tuningdirs[0]}" ]; then
  echo "Tuning directories not found"
  exit 1
fi

filename=$(basename "$(find "${tuningdirs[0]}" -type f -name "*.udb.txt" | head -n 1)")
filename="${filename%.*.*}"

echo "Merging tuned results with existing databases $filename"
if ! python - "$MIOPEN_USER_DB_PATH" "$WORKDIR/tuning" "$filename" <<'PYTHON'
import sys
from pathlib import Path

userdb_dir, tuning_dir = map(Path, sys.argv[1:3])
filename = sys.argv[3]
for suffix in ("udb.txt", "ufdb.txt"):
    destination = userdb_dir / f"{filename}.{suffix}"
    tuned_files = sorted(tuning_dir.glob(f"device*/{filename}.{suffix}"))
    if not tuned_files:
        raise RuntimeError(f"No tuned database files found for {filename}.{suffix}")

    # Existing records are read first. New records replace the complete payload
    # for their key; the last device file in sorted order wins if devices overlap.
    records = {}
    sources = ([destination] if destination.exists() else []) + tuned_files
    for source in sources:
        for line in source.read_text().splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not key or not value:
                raise ValueError(f"Malformed database record in {source}: {line}")
            records[key] = value

    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text("".join(f"{key}={records[key]}\n" for key in sorted(records)))
    temporary.replace(destination)
PYTHON
then
    echo "MIOpen database merge failed"
    exit 1
fi

# signal success to the host
touch "$ROOTDIR/.tuning_successful"
