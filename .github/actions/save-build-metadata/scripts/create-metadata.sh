#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Create build metadata JSON from environment variables.
# All INPUT_* env vars are expected to be set by the caller.

# Strip whitespace from all input values
IMAGE_TAG=$(echo "$INPUT_IMAGE_TAG" | xargs)
CORE_IMAGE=$(echo "$INPUT_CORE_IMAGE" | xargs)
CORE_IMAGE_WITH_TAG=$(echo "$INPUT_CORE_IMAGE_WITH_TAG" | xargs)
STAGING_IMAGE=$(echo "$INPUT_STAGING_IMAGE" | xargs)
STAGING_IMAGE_WITH_TAG=$(echo "$INPUT_STAGING_IMAGE_WITH_TAG" | xargs)
BENCHMARK_IMAGE=$(echo "$INPUT_BENCHMARK_IMAGE" | xargs)
BENCHMARK_IMAGE_TAG=$(echo "$INPUT_BENCHMARK_IMAGE_TAG" | xargs)
PREBUILT_CORE_TAG=$(echo "$INPUT_PREBUILT_CORE_IMAGE_TAG" | xargs)
PREBUILT_UNTUNED_TAG=$(echo "$INPUT_PREBUILT_UNTUNED_IMAGE_TAG" | xargs)
GIT_BRANCH=$(echo "$INPUT_GIT_BRANCH" | xargs)

# Determine tune and benchmark image
if [ -n "${BENCHMARK_IMAGE}" ]; then
  TUNE_AND_BENCHMARK_IMAGE="${BENCHMARK_IMAGE}"
elif [ -n "${PREBUILT_UNTUNED_TAG}" ]; then
  TUNE_AND_BENCHMARK_IMAGE="${STAGING_IMAGE}:${PREBUILT_UNTUNED_TAG}"
else
  TUNE_AND_BENCHMARK_IMAGE="${STAGING_IMAGE_WITH_TAG}-temp"
fi

# Determine untuned image
if [ -n "${PREBUILT_UNTUNED_TAG}" ]; then
  UNTUNED_IMAGE="${STAGING_IMAGE}:${PREBUILT_UNTUNED_TAG}"
else
  UNTUNED_IMAGE="${STAGING_IMAGE_WITH_TAG}-temp"
fi

mkdir -p build-metadata
cat > build-metadata/tags.json << EOF
{
  "image_tag": "${IMAGE_TAG}",
  "core_image": "${CORE_IMAGE}",
  "core_image_tag": "${IMAGE_TAG}",
  "core_image_with_tag": "${CORE_IMAGE_WITH_TAG}",
  "staging_image": "${STAGING_IMAGE}",
  "staging_image_tag": "${IMAGE_TAG}",
  "staging_image_with_tag": "${STAGING_IMAGE_WITH_TAG}",
  "benchmark_image": "${BENCHMARK_IMAGE}",
  "benchmark_image_tag": "${BENCHMARK_IMAGE_TAG}",
  "prebuilt_core_image_tag": "${PREBUILT_CORE_TAG}",
  "prebuilt_untuned_image_tag": "${PREBUILT_UNTUNED_TAG}",
  "git_branch": "${GIT_BRANCH}",
  "tune_and_benchmark_image": "${TUNE_AND_BENCHMARK_IMAGE}",
  "untuned_image": "${UNTUNED_IMAGE}"
}
EOF
