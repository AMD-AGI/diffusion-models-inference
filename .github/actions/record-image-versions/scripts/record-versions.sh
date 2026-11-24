#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Copy ROCm identity out of IMAGE into OUTPUT_DIR using a created container.
# Images with ROCm wheels (TheRock rocm-sdk-*) also get the wheel versions and
# the TheRock build manifest, read through a short python3 run in the image.
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

# Wheel images: installed ROCm/torch wheels and the TheRock build identity.
# The python3 run does not need a GPU. Deb images print nothing here.
wheels="$(docker run --rm -i --entrypoint python3 "$IMAGE" - <<'PY' || true
import importlib.metadata as metadata
import json
import re

names = re.compile(r"^(torch|torchvision|torchaudio|triton|rocm|rocm[-_].+|amd[-_]torch.*)$", re.I)
for dist in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
    if names.match(dist.metadata["Name"]):
        print(f"{dist.metadata['Name']}\t{dist.version}")
try:
    core = metadata.distribution("rocm-sdk-core")
except metadata.PackageNotFoundError:
    raise SystemExit(0)
manifest = core.locate_file("_rocm_sdk_core/share/therock/therock_manifest.json")
data = json.loads(manifest.read_text())
for key in ("rocm_package_version", "the_rock_commit", "github_run_id"):
    print(f"therock_manifest.{key}\t{data.get(key, '')}")
try:
    torch = metadata.distribution("torch")
    for line in torch.locate_file("torch/version.py").read_text().splitlines():
        if line.startswith(("git_version", "hip")):
            print(f"torch.version.{line.split()[0].rstrip(':')}\t{line.split('=', 1)[1].strip()}")
except metadata.PackageNotFoundError:
    pass
PY
)"

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
# Source-built ROCm images can provide version files without Debian packages
# or ROCm wheels.
if [[ -z "${packages}" && -z "${wheels}" ]]; then
  found_version=
  for entry in "${version_files[@]}"; do
    dest="${entry#*:}"
    if [[ -r "$dest" && -s "$dest" ]]; then
      found_version=1
      break
    fi
  done
  if [[ -z "$found_version" ]]; then
    echo "::error::No ROCm packages, ROCm wheels, or version files found in ${IMAGE}" >&2
    exit 1
  fi
  echo "::notice::No ROCm Debian packages or wheels found; recording version files instead." >&2
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
  echo "${packages:-(none found)}"
  echo
  echo "=== installed ROCm wheels ==="
  echo "${wheels:-(none found)}"
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
