#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
status=0

check_header() {
    local file="$1"
    local actual_header

    if [[ "$file" == 'data/miopen/resolve_prefix.sh' ]]; then
        actual_header="$(sed -n '2,3p' "$repository_root/$file")"
    else
        actual_header="$(head -n 2 "$repository_root/$file")"
    fi
    if [[ "$actual_header" != $'# Copyright Advanced Micro Devices, Inc.\n# SPDX-License-Identifier: MIT' ]]; then
        printf 'Missing or incorrect SPDX header: %s\n' "$file" >&2
        status=1
    fi
}

while IFS= read -r file; do
    check_header "$file"
done < <(find "$repository_root/data/hipblaslt" -type f -name '*.yaml' -print | sed "s#^$repository_root/##" | sort)

check_header 'data/miopen/resolve_prefix.sh'

if grep -R -n -E 'SPDX-License-Identifier:' "$repository_root/patches" --include='*.patch' 2>/dev/null; then
    printf 'SPDX headers are not allowed in upstream patches under patches/.\n' >&2
    status=1
fi

exit "$status"