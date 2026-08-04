#!/usr/bin/env bash
# Resolve the HuggingFace cache path for the current node.
#
# Inputs (env):
#   HF_CACHE_HOST_PATH  — direct host path override (optional)
#   CACHE_PATHS         — JSON map of hostname→path (optional)
#
# Outputs (GITHUB_OUTPUT):
#   hf_cache_args       — Docker CLI args for mounting HF cache
#   hf_cache_volume     — volume string for inference-testing server config
set -euo pipefail

HF_CACHE_ARGS=""
HF_CACHE_VOLUME=""

if [ -n "${HF_CACHE_HOST_PATH:-}" ] && [ -d "$HF_CACHE_HOST_PATH" ]; then
  echo "HF cache (from HF_CACHE_HOST_PATH): $HF_CACHE_HOST_PATH"
  HF_CACHE_ARGS="--mount type=bind,src=$HF_CACHE_HOST_PATH,dst=/hf_cache -e HF_HOME=/hf_cache"
  HF_CACHE_VOLUME="$HF_CACHE_HOST_PATH:/root/.cache/huggingface/hub"
elif [ -n "${CACHE_PATHS:-}" ]; then
  HOSTNAME=$(hostname)
  cache_path=$(echo "$CACHE_PATHS" | jq -r --arg host "$HOSTNAME" '.[$host] // empty')
  if [ -n "$cache_path" ]; then
    if [ -d "$cache_path" ]; then
      echo "HF cache (from hf_cache_map): $cache_path"
      HF_CACHE_ARGS="--mount type=bind,src=$cache_path,dst=/hf_cache -e HF_HOME=/hf_cache"
      HF_CACHE_VOLUME="$cache_path:/root/.cache/huggingface/hub"
    else
      echo "HF cache does not exist: $cache_path"
    fi
  fi
fi

echo "hf_cache_args=$HF_CACHE_ARGS" >> "$GITHUB_OUTPUT"
echo "hf_cache_volume=$HF_CACHE_VOLUME" >> "$GITHUB_OUTPUT"
