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
SHOULD_REBUILD=$(echo "$INPUT_SHOULD_REBUILD" | xargs)
WORKING_IMAGE=$(echo "$INPUT_WORKING_IMAGE" | xargs)
BASE_IMAGE=$(echo "$INPUT_BASE_IMAGE" | xargs)
GIT_BRANCH=$(echo "$INPUT_GIT_BRANCH" | xargs)

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
  "should_rebuild": "${SHOULD_REBUILD}",
  "working_image": "${WORKING_IMAGE}",
  "base_image": "${BASE_IMAGE}",
  "git_branch": "${GIT_BRANCH}"
}
EOF
