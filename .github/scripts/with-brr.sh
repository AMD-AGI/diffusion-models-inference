#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: with-brr.sh COMMAND [ARG...]" >&2
  exit 2
fi

if [ "${USE_BRR_IF_AVAILABLE:-false}" = "true" ]; then
  if command -v brr >/dev/null 2>&1; then
    echo "brr is available; requesting resources (full node, exclusive use) before running the workload" >&2
    exec brr run --gpus 8 --node --grace 0 -- "$@"
  fi
  echo "brr was requested but is not available; running the workload directly" >&2
fi

exec "$@"
