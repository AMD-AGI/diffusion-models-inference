#!/usr/bin/env bash
set -euo pipefail

# Remove specified Docker containers and images.
# Expects env vars: CONTAINERS (space-separated), IMAGES (space-separated)

for container in $CONTAINERS; do
  echo "Removing container: $container"
  docker rm -f "$container" || true
done

for image in $IMAGES; do
  echo "Removing image: $image"
  docker rmi -f "$image" || true
done

docker image prune -f || true
