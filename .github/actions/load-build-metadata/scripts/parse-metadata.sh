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
SHOULD_REBUILD=$(read_field should_rebuild)
WORKING_IMAGE=$(read_field working_image)
BASE_IMAGE=$(read_field base_image)
GIT_BRANCH=$(read_field git_branch)

# Validate required fields
if [ -z "$IMAGE_TAG" ] || [ "$IMAGE_TAG" = "null" ] || \
   [ -z "$CORE_IMAGE_WITH_TAG" ] || [ "$CORE_IMAGE_WITH_TAG" = "null" ] || \
   [ -z "$STAGING_IMAGE_WITH_TAG" ] || [ "$STAGING_IMAGE_WITH_TAG" = "null" ]; then
  echo "Error: One or more required metadata values are empty or invalid"
  exit 1
fi

# Output required fields
{
  echo "image_tag=${IMAGE_TAG}"
  echo "core_image=${CORE_IMAGE}"
  echo "core_image_tag=${CORE_IMAGE_TAG}"
  echo "core_image_with_tag=${CORE_IMAGE_WITH_TAG}"
  echo "staging_image=${STAGING_IMAGE}"
  echo "staging_image_tag=${STAGING_IMAGE_TAG}"
  echo "staging_image_with_tag=${STAGING_IMAGE_WITH_TAG}"
} >> "$GITHUB_OUTPUT"

# Output optional fields
output_field "should_rebuild" "$SHOULD_REBUILD"
output_field "working_image" "$WORKING_IMAGE"
output_field "base_image" "$BASE_IMAGE"
output_field "git_branch" "$GIT_BRANCH"

echo "Loaded build metadata:"
echo "  Image tag: ${IMAGE_TAG}"
echo "  Core image: ${CORE_IMAGE_WITH_TAG}"
echo "  Staging image: ${STAGING_IMAGE_WITH_TAG}"
echo "  Should rebuild: ${SHOULD_REBUILD}"
echo "  Working image: ${WORKING_IMAGE}"
echo "  Base image: ${BASE_IMAGE}"
echo "  Git branch: ${GIT_BRANCH}"
