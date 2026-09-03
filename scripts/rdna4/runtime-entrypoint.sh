#!/bin/sh
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
#
# MIOpen opens its user find-db read-write and calls chmod on it. That works on
# a host bind-mount but fails on Docker overlayfs ("cannot set permissions"),
# which is where the image's /miopen-db lives. Relocate the seed onto a writable
# directory under /tmp in that case, leaving bind-mounts alone.

set -eu

seed=${MIOPEN_SEED_DB_PATH:-/miopen-db}
live=${MIOPEN_USER_DB_PATH:-$seed}

# GNU coreutils: --file-system -c %T is the filesystem type (overlayfs, ext2/ext3, ...).
# Do not use -c %T alone; that is the file's minor device type, not the filesystem.
fs_type() {
    stat --file-system -c %T "$1" 2>/dev/null || printf 'unknown'
}

if [ -d "$seed" ]; then
    live_fs=unknown
    if [ -d "$live" ]; then
        live_fs=$(fs_type "$live")
    fi
    case $live_fs in
        overlay|overlayfs)
            dest=${TMPDIR:-/tmp}/miopen-userdb
            mkdir -p "$dest"
            cp -a "$seed"/. "$dest"/
            chmod -R u+rwX "$dest" 2>/dev/null || true
            export MIOPEN_USER_DB_PATH="$dest"
            export MIOPEN_CUSTOM_CACHE_DIR="$dest/cache"
            mkdir -p "$MIOPEN_CUSTOM_CACHE_DIR"
            ;;
    esac
fi

exec "$@"
