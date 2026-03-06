#!/bin/bash

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

# glob, concatenate and retain unique MIOpen driver commands
echo "Extracting workload MIOpenDriver calls"
cat $ROOTDIR/data/miopen/workloads/*.txt $ROOTDIR/src/*/miopen/drivercmd/*.txt | sort -u > $WORKDIR/drivercmd.txt

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
cd $ROOTDIR/src/distrituner
python miopen_tuner.py $WORKDIR/drivercmd.txt \
    --tuning-output-path $WORKDIR/tuning --log-dir $WORKDIR/logs \
    --miopen-find-mode $MIOPEN_FIND_MODE --miopen-find-enforce $MIOPEN_FIND_ENFORCE

# concatenate results
tuningdirs=($WORKDIR/tuning/device*/)

if [ ! -d "${tuningdirs[0]}" ]; then
  echo "Tuning directories not found"
  exit 1
fi

filename=$(basename "$(find "${tuningdirs[0]}" -type f -name "*.udb.txt" | head -n 1)")
filename="${filename%.*.*}"

echo "Concatenating results with basename $filename"
cat $WORKDIR/tuning/device*/$filename.udb.txt > $MIOPEN_USER_DB_PATH/$filename.udb.txt
cat $WORKDIR/tuning/device*/$filename.ufdb.txt > $MIOPEN_USER_DB_PATH/$filename.ufdb.txt

# change permissions to host user
chown -hR $HOST_UID:$HOST_GID $MIOPEN_USER_DB_PATH
