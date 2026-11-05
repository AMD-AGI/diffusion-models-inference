#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Copy ROCm identity out of IMAGE into OUTPUT_DIR using a created container.
# Expects env vars: IMAGE, OUTPUT_DIR, CONTAINER_NAME

IMAGE="${IMAGE:?IMAGE is required}"
OUTPUT_DIR="${OUTPUT_DIR:?OUTPUT_DIR is required}"
CONTAINER_NAME="${CONTAINER_NAME:?CONTAINER_NAME is required}"

mkdir -p "$OUTPUT_DIR"
git rev-parse HEAD > "$OUTPUT_DIR/repository.txt"

docker pull "$IMAGE"
docker create --name "$CONTAINER_NAME" "$IMAGE"

extract_dir=$(mktemp -d)
cleanup() {
  docker rm -f "$CONTAINER_NAME" || true
  rm -rf "$extract_dir"
}
trap cleanup EXIT

mkdir -p \
  "$extract_dir/apt-sources" \
  "$extract_dir/rocm-info" \
  "$extract_dir/rocm-core-info"

docker cp "${CONTAINER_NAME}:/var/lib/dpkg/status" "$extract_dir/dpkg-status"
docker cp "${CONTAINER_NAME}:/etc/apt/sources.list.d/." "$extract_dir/apt-sources/" || true
docker cp "${CONTAINER_NAME}:/opt/rocm/.info/." "$extract_dir/rocm-info/" || true
docker cp "${CONTAINER_NAME}:/opt/rocm/core/.info/." "$extract_dir/rocm-core-info/" || true
docker cp "${CONTAINER_NAME}:/opt/rocm/share/hip/version" "$extract_dir/hip-version" || true

# TheRock debian versions encode the channel (stable X.Y.Z,
# nightly X.Y.Z~YYYYMMDD, prerelease X.Y.Z~preN, dev X.Y.Z~devYYYYMMDD).
# Classic ROCm apt uses rocm-* names; TheRock uses amdrocm-*.
packages="$(awk '
  function emit() {
    if (pkg ~ /^(amdrocm|rocm)/ && status ~ /install ok installed/)
      print pkg "\t" ver
    pkg=""; ver=""; status=""
  }
  /^Package:/ { pkg=$2 }
  /^Status:/ { status=$0 }
  /^Version:/ { ver=$2 }
  /^$/ { emit() }
  END { emit() }
' "$extract_dir/dpkg-status")"

wheel_packages="$(
  docker run --rm --entrypoint /venv/bin/python "$IMAGE" -c '
from importlib.metadata import distributions

selected = []
for distribution in distributions():
    name = distribution.metadata.get("Name", "")
    normalized = name.lower().replace("_", "-")
    if (
        normalized == "rocm"
        or normalized.startswith("rocm-")
        or normalized.startswith("amd-torch")
        or normalized in {"torch", "torchvision", "torchaudio", "triton"}
    ):
        selected.append((normalized, distribution.version))
for name, version in sorted(selected):
    print(f"{name}\t{version}")
' 2>/dev/null || true
)"
rocm_sdk_version="$(
  docker run --rm --entrypoint /venv/bin/rocm-sdk "$IMAGE" version 2>/dev/null || true
)"

if [[ -z "${packages}" && -z "${wheel_packages}" ]]; then
  echo "(no native or Python ROCm packages found)" >&2
  exit 1
fi

{
  echo "=== apt ROCm sources ==="
  shopt -s nullglob
  sources=("$extract_dir"/apt-sources/*rocm*)
  if ((${#sources[@]})); then
    cat "${sources[@]}"
  else
    echo "(none found)"
  fi
  echo
  echo "=== installed native ROCm packages ==="
  if [[ -n "${packages}" ]]; then
    echo "${packages}"
  else
    echo "(none; ROCm is installed from Python wheels)"
  fi
  echo
  echo "=== installed ROCm and PyTorch wheels ==="
  if [[ -n "${wheel_packages}" ]]; then
    echo "${wheel_packages}"
  else
    echo "(none)"
  fi
  echo
  echo "=== rocm-sdk version ==="
  echo "${rocm_sdk_version:-"(unavailable)"}"
  echo
  echo "=== ROCm version files ==="
  found=
  version_files=(
    "/opt/rocm/.info/version:$extract_dir/rocm-info/version"
    "/opt/rocm/.info/version-dev:$extract_dir/rocm-info/version-dev"
    "/opt/rocm/core/.info/version:$extract_dir/rocm-core-info/version"
    "/opt/rocm/share/hip/version:$extract_dir/hip-version"
  )
  for entry in "${version_files[@]}"; do
    src="${entry%%:*}"
    dest="${entry#*:}"
    if [[ -r "$dest" ]]; then
      echo "$src: $(cat "$dest")"
      found=1
    fi
  done
  if [[ -z "${found}" ]]; then
    echo "(none found)"
  fi
} > "$OUTPUT_DIR/rocm.txt"
