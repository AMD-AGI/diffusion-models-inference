#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exclude_file="$repository_root/.ci/spdx_exclude.txt"
status=0

is_excluded() {
    local file="$1"
    while IFS= read -r pattern; do
        [[ -z "$pattern" || "$pattern" == '#'* ]] && continue
        [[ "$file" == $pattern* ]] && return 0
    done < "$exclude_file"
    return 1
}

check_header() {
    local file="$1"
    if is_excluded "$file"; then
        return
    fi
    if ! head -n 3 "$repository_root/$file" | grep -qF '# Copyright Advanced Micro Devices, Inc.' ||
       ! head -n 3 "$repository_root/$file" | grep -qF '# SPDX-License-Identifier: MIT'; then
        printf 'Missing or incorrect SPDX header: %s\n' "$file" >&2
        status=1
    fi
}

# When called with arguments, check only those files (pre-commit mode).
# Otherwise scan all known paths (CI / manual mode).
if [[ $# -gt 0 ]]; then
    for file in "$@"; do
        check_header "$file"
    done
else
    while IFS= read -r file; do
        check_header "$file"
    done < <(find "$repository_root/data" -type f \( -name '*.yaml' -o -name '*.sh' \) -print | sed "s#^$repository_root/##" | sort)
fi

exit "$status"