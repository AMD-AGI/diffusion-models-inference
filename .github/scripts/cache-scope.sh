#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

# Convert a Git ref into the Docker-tag-safe scope used by the core registry
# cache. Keep this helper dependency-free so build and cleanup actions share
# exactly the same normalization behavior.
slug() {
  printf '%s' "${1#refs/heads/}" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's#[^a-z0-9._-]#-#g' \
    | cut -c1-40 \
    | sed 's#^[-._]*##; s#[-._]*$##'
}
