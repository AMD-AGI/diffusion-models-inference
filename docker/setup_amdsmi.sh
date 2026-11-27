#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
#
# Make `import amdsmi` work inside the venv.
#
# The amd-smi CLI is in ${ROCM_HOME}/bin and /venv/bin and needs no shim.
# The rocm-sdk-core wheel installs the Python bindings in its private tree
# (_rocm_sdk_core/share/amd_smi/amdsmi), and `rocm-sdk init` mirrors them to
# ${ROCM_HOME}/share/amd_smi/amdsmi. Neither location is on sys.path, so
# `import amdsmi` fails. torch.cuda wants them for device_count() and for
# utilization/memory_usage/temperature/power_draw/clock_rate; accelerate wants
# them for set_numa_affinity under ACCELERATE_CPU_AFFINITY.
#
# The fix is a symlink, never a copy. amdsmi_wrapper._load_library() finds the
# shared object through Path(__file__).resolve(), so a link resolves back into
# the ROCm tree and loads the matching lib/libamd_smi.so.<SOVERSION>; a copy
# breaks that and falls through to a bare-SONAME linker lookup. Putting
# share/amd_smi on sys.path instead (PYTHONPATH or a .pth file) is also wrong,
# because it exposes the sibling tools/, tests/ and example/ directories as
# implicit namespace packages.
#
# This shim is expected to expire. Once a release ships the bindings on sys.path
# on its own, the script changes nothing and prints a RETIRE banner instead --
# the signal to delete it along with its COPY/RUN in docker/Dockerfile.ci.

set -euo pipefail

readonly rocm_root="${ROCM_HOME:-/opt/rocm}"
readonly src="${rocm_root}/share/amd_smi/amdsmi"

site_packages="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
readonly site_packages
readonly link="${site_packages}/amdsmi"

# Where does `import amdsmi` resolve from right now, if anywhere?
probe_origin() {
    python3 -c \
        'import importlib.util as u; print(getattr(u.find_spec("amdsmi"), "origin", "") or "")' \
        2>/dev/null || true
}

# Drop a link from an earlier run so the probe sees only what packaging
# provides. Anything else at that path came from elsewhere and is left alone.
if [[ -L "${link}" ]]; then
    rm -f "${link}"
fi

origin="$(probe_origin)"
if [[ -n "${origin}" ]]; then
    echo "==============================================================" >&2
    # Only the PyPI amdsmi wheel ships libamd_smi_python.so next to the
    # wrapper. A path outside ${rocm_root} alone does not identify it: a
    # rocm-sdk-core that installs amdsmi into site-packages is also outside.
    if [[ -e "$(dirname "$(readlink -f "${origin}")")/libamd_smi_python.so" ]]; then
        echo " setup_amdsmi: PYPI AMDSMI WHEEL FOUND" >&2
        echo " \`import amdsmi\` resolves to a PyPI amdsmi wheel, from" >&2
        echo "     ${origin}" >&2
        echo " That wheel bundles its own libamd_smi and can skew from ROCm." >&2
        echo " Find the requirement that installs amdsmi and remove it." >&2
        echo " Keep docker/setup_amdsmi.sh." >&2
    else
        echo " setup_amdsmi: RETIRE THIS SHIM" >&2
        echo " \`import amdsmi\` already resolves without it, from" >&2
        echo "     ${origin}" >&2
        echo " No symlink was created. Delete docker/setup_amdsmi.sh and its" >&2
        echo " COPY/RUN in docker/Dockerfile.ci." >&2
    fi
    echo "==============================================================" >&2
    exit 0
fi

if [[ ! -d "${src}" ]]; then
    echo "amdsmi is not importable and ${src} does not exist;" >&2
    echo "are rocm-sdk-core and rocm-sdk-devel installed, and did" >&2
    echo "\`rocm-sdk init\` run in this layer?" >&2
    exit 1
fi

ln -sfT "${src}" "${link}"

# Precompile in the ROCm tree so containers do not each rebuild __pycache__ on
# first import. Purely a nicety, so a failure here must not fail the build.
python3 -m compileall -q "${src}" > /dev/null ||
    echo "warning: could not precompile ${src}; imports compile at runtime" >&2

# The wrapper is import-tolerant: it swaps in a _MissingLibrary sentinel when
# the .so cannot be loaded, so a bare `import amdsmi` proves nothing. Check the
# library the wrapper actually resolved.
python3 - <<'PY'
import amdsmi
from amdsmi import amdsmi_wrapper

missing = object()
lib = getattr(amdsmi_wrapper, "_loaded_lib_path", missing)
if lib is None:
    raise SystemExit(f"linked {amdsmi.__file__} but libamd_smi.so failed to load")
if lib is missing:
    print(f"linked {amdsmi.__file__} (library unverified: the wrapper no longer "
          "exposes _loaded_lib_path)")
else:
    print(f"linked {amdsmi.__file__} -> {lib}")
PY
