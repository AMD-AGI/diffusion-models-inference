#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Remove specified Docker containers and images.
# Expects env vars: CONTAINERS (space-separated), IMAGES (space-separated)
#
# CONTAINERS holds base names (e.g. "xdit-bench"). GPU workloads run under an
# owner-prefixed name so a container that outlives its job says who to ask, so
# each base is resolved the same way here. The bare base name is removed too,
# which clears containers left by runs that predate the prefix.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# The helper path is resolved relative to this action at runtime.
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../../../scripts/container-name.sh"

for container in $CONTAINERS; do
  for name in "$(container_name "$container")" "$container"; do
    echo "Removing container: $name"
    docker rm -f "$name" || true
  done
done

for image in $IMAGES; do
  echo "Removing image: $image"
  docker rmi -f "$image" || true
done

docker image prune -f || true
