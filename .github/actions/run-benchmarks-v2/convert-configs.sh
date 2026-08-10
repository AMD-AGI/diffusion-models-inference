#!/usr/bin/env bash
# Parse benchmark_flags, apply tag/name filters, and convert configs
# to inference-testing format.
#
# Inputs (env):
#   INPUT_BENCHMARK_FLAGS — raw benchmark flags string
#   GFX_ARCH              — GPU architecture tag (gfx942, gfx950)
#   DOCKER_IMAGE          — Docker image for the server block
#   GITHUB_WORKSPACE      — workspace root
#
# Outputs (GITHUB_OUTPUT):
#   converted_dir  — path to directory with converted configs
#   config_count   — number of converted configs
set -euo pipefail

CONVERTED_DIR="$GITHUB_WORKSPACE/.inference-testing-configs"
rm -rf "$CONVERTED_DIR"
mkdir -p "$CONVERTED_DIR"

# Parse benchmark_flags into filter args for the converter
FILTER_ARGS=()
mapfile -d '' -t FLAGS_ARRAY < <(
  INPUT_BENCHMARK_FLAGS="${INPUT_BENCHMARK_FLAGS:-}" python3 - <<'PY'
import os
import shlex
import sys

for flag in shlex.split(os.environ["INPUT_BENCHMARK_FLAGS"]):
    sys.stdout.write(flag + "\0")
PY
)

i=0
while [ $i -lt ${#FLAGS_ARRAY[@]} ]; do
  flag="${FLAGS_ARRAY[$i]}"
  case "$flag" in
    --tag)
      i=$((i + 1))
      if [ $i -ge ${#FLAGS_ARRAY[@]} ]; then
        echo "::error::--tag requires a value"
        exit 1
      fi
      FILTER_ARGS+=(--tag "${FLAGS_ARRAY[$i]}")
      ;;
    --name)
      i=$((i + 1))
      if [ $i -ge ${#FLAGS_ARRAY[@]} ]; then
        echo "::error::--name requires a value"
        exit 1
      fi
      FILTER_ARGS+=(--name "${FLAGS_ARRAY[$i]}")
      ;;
    --collect-hipblaslt-logs|--no-clear-model-cache)
      # Not consumed here — handled by post-processing or not applicable
      ;;
    *)
      # Ignore unrecognized flags
      ;;
  esac
  i=$((i + 1))
done

# Add gfx_arch as tag filter if set and --name is not used
if [ -n "${GFX_ARCH:-}" ] && [[ ! " ${FLAGS_ARRAY[*]} " =~ [[:space:]]--name[[:space:]] ]]; then
  FILTER_ARGS+=(--tag "$GFX_ARCH")
fi

# Run the converter
python3 "$(pwd)/scripts/convert_configs.py" \
  "$(pwd)/benchmark_configs/xdit/"*.yaml \
  --image "$DOCKER_IMAGE" \
  "${FILTER_ARGS[@]}" \
  -o "$CONVERTED_DIR"

CONFIG_COUNT=$(find "$CONVERTED_DIR" -name '*.yaml' | wc -l)
echo "Converted $CONFIG_COUNT config(s) to $CONVERTED_DIR"

if [ "$CONFIG_COUNT" -eq 0 ]; then
  echo "::warning::No benchmark configs matched the filter criteria"
fi

echo "converted_dir=$CONVERTED_DIR" >> "$GITHUB_OUTPUT"
echo "config_count=$CONFIG_COUNT" >> "$GITHUB_OUTPUT"
