#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Resolve the HuggingFace cache path for the current node.
# Outputs hf_cache_args and run_py_flags to GITHUB_OUTPUT.
# Expects: CACHE_PATHS (JSON map of hostname->path), HF_CACHE_HOST_PATH (optional env)

HF_CACHE_ARGS=""
RUN_PY_FLAGS=""

if [ -n "${HF_CACHE_HOST_PATH:-}" ] && [ -d "${HF_CACHE_HOST_PATH:-}" ]; then
  echo "HF cache (from HF_CACHE_HOST_PATH): $HF_CACHE_HOST_PATH"
  HF_CACHE_ARGS="--mount type=bind,src=$HF_CACHE_HOST_PATH,dst=/hf_cache -e HF_HOME=/hf_cache"
  RUN_PY_FLAGS="--no-clear-model-cache"
elif [ -n "${CACHE_PATHS:-}" ]; then
  HOSTNAME=$(hostname)
  cache_path=$(echo "$CACHE_PATHS" | jq -r --arg host "$HOSTNAME" '.[$host] // empty')
  if [ -n "$cache_path" ]; then
    if [ -d "$cache_path" ]; then
      echo "HF cache (from hf_cache_map): $cache_path"
      HF_CACHE_ARGS="--mount type=bind,src=$cache_path,dst=/hf_cache -e HF_HOME=/hf_cache"
      RUN_PY_FLAGS="--no-clear-model-cache"
    else
      echo "HF cache does not exist: $cache_path"
    fi
  fi
fi

echo "hf_cache_args=$HF_CACHE_ARGS" >> "$GITHUB_OUTPUT"
echo "run_py_flags=$RUN_PY_FLAGS" >> "$GITHUB_OUTPUT"