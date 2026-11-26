#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
#
# Install ROCm and the PyTorch stack as Python wheels from one TheRock wheel
# index, then expose the ROCm SDK at /opt/rocm.
#
# torch pins rocm[libraries] and triton to the exact ROCm wheel version, so
# ROCM_WHEEL_VERSION selects one TheRock build for all packages. The nightly
# index also mirrors the pure-Python dependencies of torch, so this step uses
# only that index. A PyPI package cannot replace a ROCm build here.
#
# The torch and torchvision "device-<gfx>" extras pull the per-architecture
# kernel wheels (amd-torch-device-<gfx>, amd-torchvision-device-<gfx>, and
# rocm-sdk-device-<gfx>). Without them, torch has no device code.
#
# "rocm-sdk init" expands rocm-sdk-devel and hardlinks the device files into
# the devel tree. Run it in the same layer as the install: a hardlink to a file
# in a lower layer makes overlayfs copy the file. It runs here at build time,
# because the compiler trampolines otherwise start it on first use, for example
# in an AITER JIT build in a running container.
#
# This script is the single source of the ROCm wheel requirement list. With
# --print-requirements, it writes that list to stdout and installs nothing.
# The nightly resolver (.github/actions/resolve-rocm-nightly) uses this mode to
# check that the newest ROCm wheel build has all the pinned packages. Only
# ROCM_WHEEL_VERSION, ROCM_GFX_TARGETS, and the TORCH*_VERSION variables are
# necessary for this mode.
#
# The install writes the pins to ROCM_WHEEL_CONSTRAINTS (a pip constraints
# file). With --check, the script compares the installed versions with this
# file and fails if a later pip run changed a ROCm wheel. Only
# ROCM_WHEEL_CONSTRAINTS is necessary for this mode.
#
# After the install, ROCM_WHEEL_BUILD_INFO receives the build identity
# (TheRock manifest, the source commits of torch, torchvision, torchaudio, and
# Triton, and the HIP and Triton versions) as "key<TAB>value" lines.
# ROCM_WHEEL_THEROCK_MANIFEST receives a copy of the full TheRock manifest.
# The record-image-versions action copies both files. docker/README.md
# ("ROCm and PyTorch provenance") tells how to read them.

set -euo pipefail

usage() {
    echo "usage: install_rocm_wheels [--print-requirements | --check]" >&2
    exit 2
}

mode=install
case "$#:${1:-}" in
    0:) ;;
    1:--print-requirements) mode=print ;;
    1:--check) mode=check ;;
    *) usage ;;
esac

if [[ "${mode}" == check ]]; then
    ROCM_WHEEL_CONSTRAINTS="${ROCM_WHEEL_CONSTRAINTS:?ROCM_WHEEL_CONSTRAINTS must be set}" python3 - <<'PY'
import importlib.metadata as metadata
import os
from pathlib import Path

path = Path(os.environ["ROCM_WHEEL_CONSTRAINTS"])
changed = []
for line in path.read_text().splitlines():
    name, _, pinned = line.strip().partition("==")
    if not name:
        continue
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        installed = "(not installed)"
    if installed != pinned:
        changed.append(f"{name}: {installed}, pinned {pinned}")
if changed:
    raise SystemExit(f"A pip run changed the ROCm wheels in {path}:\n" + "\n".join(changed))
print(f"The installed ROCm wheels agree with {path}")
PY
    exit 0
fi

readonly rocm_version="${ROCM_WHEEL_VERSION:?ROCM_WHEEL_VERSION must be set}"
readonly gfx_targets="${ROCM_GFX_TARGETS:?ROCM_GFX_TARGETS must be set}"
readonly torch_version="${TORCH_VERSION:?TORCH_VERSION must be set}"
readonly torchvision_version="${TORCHVISION_VERSION:?TORCHVISION_VERSION must be set}"
readonly torchaudio_version="${TORCHAUDIO_VERSION:?TORCHAUDIO_VERSION must be set}"
readonly local_version="+rocm${rocm_version}"

IFS=';' read -r -a listed_targets <<< "${gfx_targets}"
targets=()
for target in "${listed_targets[@]}"; do
    [[ -n "${target}" ]] || continue
    targets+=("${target}")
done
if [[ "${#targets[@]}" -eq 0 ]]; then
    echo "ROCM_GFX_TARGETS did not contain a usable target" >&2
    exit 2
fi
readonly targets
extras="$(printf 'device-%s,' "${targets[@]}")"
extras="${extras%,}"
readonly extras

# All wheels from one TheRock build. The device wheels and the SDK parts also
# come through the torch dependencies; the explicit pins make the list
# complete for the resolver and make pip fail if torch pins other versions.
# torch also pins triton, but its version (for example
# 3.9.0+git0a6e13e6.rocm10.3.0a20261108) is not known before the install. The
# resolver checks for a triton build with the same "rocm" suffix.
requirements=(
    "torch[${extras}]==${torch_version}${local_version}"
    "torchvision[${extras}]==${torchvision_version}${local_version}"
    "torchaudio==${torchaudio_version}${local_version}"
    "rocm-sdk-core==${rocm_version}"
    "rocm-sdk-libraries==${rocm_version}"
    "rocm-sdk-devel==${rocm_version}"
    "rocm-profiler==${rocm_version}"
)
for target in "${targets[@]}"; do
    requirements+=(
        "amd-torch-device-${target}==${torch_version}${local_version}"
        "amd-torchvision-device-${target}==${torchvision_version}${local_version}"
        "rocm-sdk-device-${target}==${rocm_version}"
    )
done
readonly requirements

if [[ "${mode}" == print ]]; then
    printf '%s\n' "${requirements[@]}"
    exit 0
fi

readonly index="${ROCM_WHEEL_INDEX:?ROCM_WHEEL_INDEX must be set}"
readonly python_tag="${ROCM_WHEEL_PYTHON_TAG:?ROCM_WHEEL_PYTHON_TAG must be set}"
readonly constraints="${ROCM_WHEEL_CONSTRAINTS:?ROCM_WHEEL_CONSTRAINTS must be set}"
readonly build_info="${ROCM_WHEEL_BUILD_INFO:?ROCM_WHEEL_BUILD_INFO must be set}"
readonly therock_manifest="${ROCM_WHEEL_THEROCK_MANIFEST:?ROCM_WHEEL_THEROCK_MANIFEST must be set}"
readonly rocm_link="/opt/rocm"

# The nightly resolver checks the wheels for ROCM_WHEEL_PYTHON_TAG. The venv
# Python must agree, or the resolver checks wheels that pip does not install.
venv_python_tag="$(python3 -c 'import sys; print(f"cp{sys.version_info[0]}{sys.version_info[1]}")')"
if [[ "${venv_python_tag}" != "${python_tag}" ]]; then
    echo "ROCM_WHEEL_PYTHON_TAG is ${python_tag}, but python3 is ${venv_python_tag}" >&2
    exit 2
fi

pip install --no-cache-dir --index-url "${index}" "${requirements[@]}"

rocm-sdk init
sdk_root="$(rocm-sdk path --root)"
readonly sdk_root

# Tools and scripts (AITER fallbacks, MIOpen tuning, profiler scripts) expect
# the classic /opt/rocm root. A link keeps them working.
mkdir -p "$(dirname "${rocm_link}")"
ln -sfnT "${sdk_root}" "${rocm_link}"

echo "${rocm_link}/lib" > /etc/ld.so.conf.d/rocm.conf
if [[ -d "${rocm_link}/lib/rocm_sysdeps/lib" ]]; then
    echo "${rocm_link}/lib/rocm_sysdeps/lib" \
        > /etc/ld.so.conf.d/rocm_sysdeps.conf
fi
ldconfig

# Pins for later pip runs. A later dependency resolution must not replace the
# ROCm builds with PyPI builds. pip constraints do not accept extras, so remove
# them. torch pins rocm and triton, which are not in the requirement list.
triton_version="$(python3 -c 'import importlib.metadata as m; print(m.version("triton"))')"
{
    printf '%s\n' "${requirements[@]/\[*\]/}"
    echo "rocm==${rocm_version}"
    echo "triton==${triton_version}"
} > "${constraints}"
echo "ROCm wheel constraints (${constraints}):"
cat "${constraints}"
ROCM_WHEEL_CONSTRAINTS="${constraints}" "$0" --check

test -x "${rocm_link}/bin/hipcc"
test -e "${rocm_link}/lib/libamdhip64.so"
test -d "${rocm_link}/lib/llvm/amdgcn/bitcode"
test -x "${rocm_link}/lib/llvm/bin/clang++"
test -x "${rocm_link}/lib/llvm/bin/ld.lld"
# FlyDSL and Dockerfile.rdna4 use the llvm/bin path. The SDK links llvm to
# lib/llvm.
test -x "${rocm_link}/llvm/bin/ld.lld"
test -e "${rocm_link}/lib/cmake/hip-lang/hip-lang-config.cmake"
# hipblaslt-config.cmake finds origami. Downstream CMake builds that use
# find_package(Torch), for example vLLM, need it.
test -e "${rocm_link}/lib/cmake/origami/origami-config.cmake"
test -e "${rocm_link}/lib/libMIOpen.so"
test -e "${rocm_link}/lib/librccl.so"
test -e "${rocm_link}/lib/libhipblaslt.so"
test -e "${rocm_link}/lib/librocprofiler-sdk.so"
test -e "${rocm_link}/lib/libroctracer64.so"
test -d "${rocm_link}/share/amd_smi/amdsmi"
test -x "${rocm_link}/bin/MIOpenDriver"

# The Python step reads all values from the environment.
ROCM_WHEEL_TARGETS="${targets[*]}" python3 - <<'PY'
import importlib.metadata as metadata
import json
import os
import re
import shutil
from pathlib import Path

import torch
import torchaudio.version
import torchvision.version
import triton

rocm_version = os.environ["ROCM_WHEEL_VERSION"]
targets = os.environ["ROCM_WHEEL_TARGETS"].split()
local = f"+rocm{rocm_version}"
expected = {
    torch: os.environ["TORCH_VERSION"] + local,
    torchvision: os.environ["TORCHVISION_VERSION"] + local,
    torchaudio: os.environ["TORCHAUDIO_VERSION"] + local,
}
for module, version in expected.items():
    if module.__version__ != version:
        raise SystemExit(f"{module.__name__} {module.__version__} != {version}")
if not torch.version.hip:
    raise SystemExit("torch is not a ROCm build")
for target in targets:
    for name in (f"amd-torch-device-{target}", f"rocm-sdk-device-{target}"):
        metadata.version(name)

core = metadata.distribution("rocm-sdk-core")
manifest_path = Path(core.locate_file("_rocm_sdk_core/share/therock/therock_manifest.json"))
manifest = json.loads(manifest_path.read_text())
shutil.copyfile(manifest_path, os.environ["ROCM_WHEEL_THEROCK_MANIFEST"])
# The Triton wheel has no git_version; its local version holds the short
# commit, for example 3.9.0+git0a6e13e6.rocm10.3.0a20261108.
triton_version = metadata.version("triton")
triton_commit = re.search(r"\+git([0-9a-f]+)", triton_version)
info = {
    "rocm_wheel_index": os.environ["ROCM_WHEEL_INDEX"],
    "rocm_wheel_version": rocm_version,
    "rocm_gfx_targets": " ".join(targets),
    "therock_manifest.rocm_package_version": manifest.get("rocm_package_version", ""),
    "therock_manifest.the_rock_commit": manifest.get("the_rock_commit", ""),
    "therock_manifest.github_run_id": manifest.get("github_run_id", ""),
    "torch.version.git_version": torch.version.git_version,
    "torch.version.hip": torch.version.hip,
    "torchvision.version.git_version": torchvision.version.git_version,
    "torchaudio.version.git_version": torchaudio.version.git_version,
    "triton": triton_version,
    "triton.git_version": triton_commit.group(1) if triton_commit else "",
}
# The ROCm components (rocm-systems, rocm-libraries, llvm-project, and others)
# are submodules of TheRock. Value: "<commit> <repository>", and the number
# of TheRock patches if there are patches.
for submodule in manifest.get("submodules", []):
    value = f"{submodule['pin_sha']} {submodule['submodule_url'].removesuffix('.git')}"
    if submodule.get("patches"):
        value += f" (TheRock patches: {len(submodule['patches'])})"
    info[f"therock_manifest.submodule.{submodule['submodule_path']}"] = value
if info["therock_manifest.rocm_package_version"] != rocm_version:
    raise SystemExit(f"TheRock manifest version {info['therock_manifest.rocm_package_version']} != {rocm_version}")
Path(os.environ["ROCM_WHEEL_BUILD_INFO"]).write_text(
    "".join(f"{key}\t{value}\n" for key, value in info.items())
)

print(f"torch {torch.__version__} (git {torch.version.git_version}), "
      f"HIP {torch.version.hip}, triton {triton.__version__}")
print(f"torchvision {torchvision.__version__}, torchaudio {torchaudio.__version__}")
PY
echo "ROCm wheel build info (${build_info}):"
cat "${build_info}"
test -s "${therock_manifest}"
echo "TheRock manifest: ${therock_manifest}"
