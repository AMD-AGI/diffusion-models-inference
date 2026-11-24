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

copy_optional() {
  local source="$1" destination="$2" error_file="$extract_dir/docker-cp-error"
  # Docker does not provide a distinct exit code for a missing image path, and
  # its wording differs between engine versions; other errors still fail.
  if docker cp "${CONTAINER_NAME}:${source}" "$destination" 2>"$error_file"; then
    cat "$error_file" >&2
  elif grep -qiE 'could not find the file|no such file or directory|not found' "$error_file" \
    && ! grep -qiE 'no such container|permission denied|cannot connect' "$error_file"; then
    echo "Optional image path not found: ${source}"
  else
    echo "::error::Failed to copy image path ${source}" >&2
    cat "$error_file" >&2
    return 1
  fi
}

docker cp "${CONTAINER_NAME}:/var/lib/dpkg/status" "$extract_dir/dpkg-status"
copy_optional "/etc/apt/sources.list.d/." "$extract_dir/apt-sources/"
copy_optional "/opt/rocm/.info/." "$extract_dir/rocm-info/"
copy_optional "/opt/rocm/core/.info/." "$extract_dir/rocm-core-info/"
copy_optional "/opt/rocm/share/hip/version" "$extract_dir/hip-version"

version_files=(
  "/opt/rocm/.info/version:$extract_dir/rocm-info/version"
  "/opt/rocm/.info/version-dev:$extract_dir/rocm-info/version-dev"
  "/opt/rocm/core/.info/version:$extract_dir/rocm-core-info/version"
  "/opt/rocm/share/hip/version:$extract_dir/hip-version"
)

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
# Source-built ROCm images can provide version files without Debian packages.
if [[ -z "${packages}" ]]; then
  found_version=
  for entry in "${version_files[@]}"; do
    dest="${entry#*:}"
    if [[ -r "$dest" && -s "$dest" ]]; then
      found_version=1
      break
    fi
  done
  if [[ -z "$found_version" ]]; then
    echo "::error::No ROCm packages or version files found in ${IMAGE}" >&2
    exit 1
  fi
  echo "::notice::No ROCm Debian packages found; recording version files instead." >&2
  packages="(no amdrocm-* or rocm-* packages found)"
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
  echo "=== installed ROCm packages ==="
  echo "${packages}"
  echo
  echo "=== ROCm version files ==="
  found=
  for entry in "${version_files[@]}"; do
    src="${entry%%:*}"
    dest="${entry#*:}"
    if [[ -r "$dest" && -s "$dest" ]]; then
      echo "$src: $(cat "$dest")"
      found=1
    fi
  done
  if [[ -z "${found}" ]]; then
    echo "(none found)"
  fi
} > "$OUTPUT_DIR/rocm.txt"
