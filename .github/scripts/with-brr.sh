#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Runs a command under brr when brr is both requested and available, and runs it
# directly otherwise, so the same call site works on nodes with and without brr.
#
# Env vars:
#   USE_BRR_IF_AVAILABLE  'true' to use brr when it is installed.
#   BRR_GPU_REQUEST       Number of GPUs to claim. Unset or empty claims the
#                         whole node, which is the right request for a workload
#                         that manages all GPUs itself.
#
# Sized requests are exclusive: brr defaults to shared mode, which would let
# another job land on the same GPU and quietly distort benchmark numbers.

if [ "$#" -eq 0 ]; then
  echo "usage: with-brr.sh COMMAND [ARG...]" >&2
  exit 2
fi

if [ "${USE_BRR_IF_AVAILABLE:-false}" = "true" ]; then
  if command -v brr >/dev/null 2>&1; then
    if [ -n "${BRR_GPU_REQUEST:-}" ]; then
      echo "brr is available; requesting $BRR_GPU_REQUEST GPU(s), exclusive use, before running the workload" >&2
      exec brr run --gpus "$BRR_GPU_REQUEST" --exclusive --grace 0 -- "$@"
    fi
    echo "brr is available; requesting resources (full node, exclusive use) before running the workload" >&2
    exec brr run --node --grace 0 -- "$@"
  fi
  echo "brr was requested but is not available; running the workload directly" >&2
fi

exec "$@"
