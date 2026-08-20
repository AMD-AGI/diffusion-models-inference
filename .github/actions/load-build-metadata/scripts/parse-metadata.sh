#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Parse build metadata JSON and output values for GitHub Actions.
# Expects: build-metadata/tags.json to exist, GITHUB_OUTPUT to be set.

METADATA="build-metadata/tags.json"

# Helper: read a field from JSON, outputting empty string for null
read_field() {
  jq -r ".$1" "$METADATA"
}

# Helper: output a field to GITHUB_OUTPUT, clearing null values
output_field() {
  local key="$1"
  local value="$2"
  if [ -n "$value" ] && [ "$value" != "null" ]; then
    echo "${key}=${value}" >> "$GITHUB_OUTPUT"
  else
    echo "${key}=" >> "$GITHUB_OUTPUT"
  fi
}

# Read all fields
IMAGE_TAG=$(read_field image_tag)
CORE_IMAGE=$(read_field core_image)
CORE_IMAGE_TAG=$(read_field core_image_tag)
CORE_IMAGE_WITH_TAG=$(read_field core_image_with_tag)
STAGING_IMAGE=$(read_field staging_image)
STAGING_IMAGE_TAG=$(read_field staging_image_tag)
STAGING_IMAGE_WITH_TAG=$(read_field staging_image_with_tag)
BENCHMARK_IMAGE=$(read_field benchmark_image)
BENCHMARK_IMAGE_TAG=$(read_field benchmark_image_tag)
PREBUILT_CORE_IMAGE_TAG=$(read_field prebuilt_core_image_tag)
PREBUILT_UNTUNED_IMAGE_TAG=$(read_field prebuilt_untuned_image_tag)
GIT_BRANCH=$(read_field git_branch)
TUNE_AND_BENCHMARK_IMAGE=$(read_field tune_and_benchmark_image)
UNTUNED_IMAGE=$(read_field untuned_image)

# Validate required fields
if [ -z "$IMAGE_TAG" ] || [ "$IMAGE_TAG" = "null" ] || \
   [ -z "$CORE_IMAGE_WITH_TAG" ] || [ "$CORE_IMAGE_WITH_TAG" = "null" ] || \
   [ -z "$STAGING_IMAGE_WITH_TAG" ] || [ "$STAGING_IMAGE_WITH_TAG" = "null" ]; then
  echo "Error: One or more required metadata values are empty or invalid"
  exit 1
fi

# Output required fields
echo "image_tag=${IMAGE_TAG}" >> "$GITHUB_OUTPUT"
echo "core_image=${CORE_IMAGE}" >> "$GITHUB_OUTPUT"
echo "core_image_tag=${CORE_IMAGE_TAG}" >> "$GITHUB_OUTPUT"
echo "core_image_with_tag=${CORE_IMAGE_WITH_TAG}" >> "$GITHUB_OUTPUT"
echo "staging_image=${STAGING_IMAGE}" >> "$GITHUB_OUTPUT"
echo "staging_image_tag=${STAGING_IMAGE_TAG}" >> "$GITHUB_OUTPUT"
echo "staging_image_with_tag=${STAGING_IMAGE_WITH_TAG}" >> "$GITHUB_OUTPUT"

# Output optional fields
output_field "benchmark_image" "$BENCHMARK_IMAGE"
output_field "benchmark_image_tag" "$BENCHMARK_IMAGE_TAG"
output_field "prebuilt_core_image_tag" "$PREBUILT_CORE_IMAGE_TAG"
output_field "prebuilt_untuned_image_tag" "$PREBUILT_UNTUNED_IMAGE_TAG"
output_field "git_branch" "$GIT_BRANCH"
output_field "tune_and_benchmark_image" "$TUNE_AND_BENCHMARK_IMAGE"
output_field "untuned_image" "$UNTUNED_IMAGE"

echo "Loaded build metadata:"
echo "  Image tag: ${IMAGE_TAG}"
echo "  Core image: ${CORE_IMAGE_WITH_TAG}"
echo "  Staging image: ${STAGING_IMAGE_WITH_TAG}"
echo "  Benchmark image: ${BENCHMARK_IMAGE}"
echo "  Prebuilt core image tag: ${PREBUILT_CORE_IMAGE_TAG}"
echo "  Prebuilt untuned image tag: ${PREBUILT_UNTUNED_IMAGE_TAG}"
echo "  Git branch: ${GIT_BRANCH}"
echo "  Tune and benchmark image: ${TUNE_AND_BENCHMARK_IMAGE}"
echo "  Untuned image: ${UNTUNED_IMAGE}"
