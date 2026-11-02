#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

# Runs a command under brr when brr is enabled and available, and runs it
# directly otherwise, so the same call site works on nodes with and without brr.
#
# Env vars:
#   DISABLE_BRR      'true' to bypass brr even when it is installed.
#   BRR_EXECUTABLE   Client path; defaults to brr on PATH.
#   BRR_GPU_REQUEST  Number of GPUs to claim. Unset or empty claims the whole
#                    node, which is the right request for a workload that
#                    manages all GPUs itself.
#
# Sized requests are exclusive: brr defaults to shared mode, which would let
# another job land on the same GPU and quietly distort benchmark numbers.
# Pin the first N amd-smi indices so repeated benchmarks use the same devices.

brr_is_usable() {
  [ "${DISABLE_BRR:-false}" != "true" ] && command -v brr >/dev/null 2>&1
}

# shellcheck disable=SC2034
# Source on the host. The caller adds these mounts/envs to docker run and passes
# BRR_CONTAINER_LAUNCH as the container-side command prefix.
# Requires the host Unix socket (BRR_SOCKET or /run/brr/brrd.sock) and Python 3
# in the image. Mount its directory so daemon restarts can replace the socket.
prepare_brr_container() {
  BRR_DOCKER_ARGS=()
  BRR_CONTAINER_LAUNCH=()
  if ! brr_is_usable; then
    return 0
  fi

  local client socket socket_dir
  client=$(readlink -f "$(command -v brr)") || return
  socket=${BRR_SOCKET:-/run/brr/brrd.sock}
  if [ ! -S "$socket" ]; then
    echo "brr is installed but its daemon socket is unavailable: $socket" >&2
    return 11
  fi
  socket=$(readlink -f "$socket") || return
  socket_dir=$(dirname "$socket")

  BRR_DOCKER_ARGS=(
    --mount "type=bind,src=$client,dst=/opt/ci-brr/brr,readonly"
    --mount "type=bind,src=$socket_dir,dst=/opt/ci-brr/run,readonly"
    -e "BRR_SOCKET=/opt/ci-brr/run/$(basename "$socket")"
    -e BRR_EXECUTABLE=/opt/ci-brr/brr
    -e DISABLE_BRR=false
    -e BRR_GPU_REQUEST
  )
  # Installed clients are self-contained zipapps; source-tree launchers also
  # need their adjacent brrlib package.
  if [ -d "$(dirname "$client")/brrlib" ]; then
    BRR_DOCKER_ARGS+=(--mount "type=bind,src=$(dirname "$client")/brrlib,dst=/opt/ci-brr/brrlib,readonly")
  fi
  BRR_CONTAINER_LAUNCH=("$WITH_BRR")
}

# Sourcing exposes the helpers without changing shell options or launching work.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  return 0
fi

set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "usage: with-brr.sh COMMAND [ARG...]" >&2
  exit 2
fi

if [ "${DISABLE_BRR:-false}" != "true" ]; then
  BRR_EXECUTABLE=${BRR_EXECUTABLE:-brr}
  if command -v "$BRR_EXECUTABLE" >/dev/null 2>&1; then
    if [ -n "${BRR_GPU_REQUEST:-}" ]; then
      if [[ ! "$BRR_GPU_REQUEST" =~ ^[1-9][0-9]*$ ]]; then
        echo "BRR_GPU_REQUEST must be a positive integer: $BRR_GPU_REQUEST" >&2
        exit 2
      fi
      BRR_GPU_LIST=$(seq -s, 0 "$((BRR_GPU_REQUEST - 1))")
      echo "brr is available; requesting GPU(s) $BRR_GPU_LIST, exclusive use, before running the workload" >&2
      exec "$BRR_EXECUTABLE" run --gpu-list "$BRR_GPU_LIST" --exclusive --grace 0 -- "$@"
    fi
    echo "brr is available; requesting resources (full node, exclusive use) before running the workload" >&2
    exec "$BRR_EXECUTABLE" run --node --grace 0 -- "$@"
  fi
  if [ "$BRR_EXECUTABLE" != "brr" ]; then
    echo "Configured brr client is unavailable: $BRR_EXECUTABLE" >&2
    exit 11
  fi
  echo "brr is not available; running the workload directly" >&2
else
  echo "brr is disabled; running the workload directly" >&2
fi

exec "$@"
