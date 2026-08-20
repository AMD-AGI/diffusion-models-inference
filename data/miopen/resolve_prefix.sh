#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
# Resolve the MIOpen DB file prefix by matching rocminfo marketing names
# against data/miopen/prefixes.txt. Prints the prefix or nothing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAP_FILE="${1:-$SCRIPT_DIR/prefixes.txt}"

if [ ! -f "$MAP_FILE" ]; then
    exit 0
fi

while IFS= read -r name; do
    while IFS='=' read -r model prefix; do
        [[ -z "$model" || "$model" == \#* ]] && continue
        if [[ "$name" == *"$model"* ]]; then
            echo "$prefix"
            exit 0
        fi
    done < "$MAP_FILE"
done < <(rocminfo 2>/dev/null | grep "Marketing Name" | sed 's/.*: *//' || true)
