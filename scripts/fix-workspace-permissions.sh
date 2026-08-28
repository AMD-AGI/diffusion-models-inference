#!/bin/bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
# Source this script at the end of any container command that creates files on
# the host workspace. It chowns workspace files back to the host user.

chown -hR "${HOST_UID:-0}:${HOST_GID:-0}" "${GITHUB_WORKSPACE:?}" 2>/dev/null || true
