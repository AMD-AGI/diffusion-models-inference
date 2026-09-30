# ROCm-only image for the MIOpen system-DB A/B evaluation.
#
# Latest 10.1 pre-release on the native Ubuntu 24.04 channel from
# https://github.com/ROCm/TheRock/blob/main/RELEASES.md (prerelease row:
# https://rc.repo.amd.com/rocm/core/packages/). That channel is a rolling
# apt suite, not the dated nightly snapshot path in the RELEASES.md sample
# (`packages/deb/${RELEASE_ID}`). As of 2026-09-30 the suite's highest
# version is 10.1.0~pre3-36179538173 (pre0, pre1, and pre2 are also present).
#
# What actually has to be installed to run MIOpenDriver:
#   amdrocm-dnn10.1-<gfx>     device kernels (.kpack), CK grouped-conv lib,
#                             and the system DB under share/miopen/db/
#   amdrocm-rand10.1-<gfx>    MIOpenDriver links librocrand.so.1, which the
#                             dnn packages do not declare as a dependency
#   amdrocm-dnn-dev10.1       the MIOpenDriver binary
#   amdrocm-runtime-dev10.1   HIP headers used when find compiles kernels
# Those four pull libMIOpen, HIP, hipcc/hiprtc, device bitcode, rocminfo,
# rocBLAS, hipBLASLt, hipSOLVER, and rocSPARSE for the selected GPUs.
#
# Not installed, and not needed to run this evaluation:
#   amdrocm / amdrocm-core-sdk / amdrocm-dnn   every supported GPU
#   amdrocm-dnn-test10.1                       gtests and samples
#   amdrocm-ck10.1                             CK for this arch is already
#                                              inside the dnn gfx package
#   amdrocm-llvm-dev10.1                       clang/LLVM headers, ~2GB
#                                              installed. hipcc, hipconfig,
#                                              and amdgcn bitcode are in
#                                              amdrocm-llvm10.1. The dev
#                                              package is only reached
#                                              because dnn-dev depends on
#                                              runtime-dev, which depends
#                                              on llvm-dev. The two dev debs
#                                              are unpacked with
#                                              --force-depends so that edge
#                                              is not followed.
#
# The repository is not copied in. run_experiment.sh bind-mounts it.
# Packages unpack under /opt/rocm/core-10.1. dpkg creates /opt/rocm as a
# directory, so the image links bin, lib, include, and share there instead
# of replacing /opt/rocm itself.

FROM ubuntu:24.04

ARG ROCM_VERSION=10.1.0~pre3-36179538173
# Space-separated. These match data/miopen/userdb (MI300 and MI350/MI355).
ARG ROCM_GFX_TARGETS="gfx942 gfx950"

ENV DEBIAN_FRONTEND=noninteractive \
    ROCM_PATH=/opt/rocm \
    HIP_DEVICE_LIB_PATH=/opt/rocm/lib/llvm/amdgcn/bitcode \
    PATH=/opt/rocm/bin:${PATH} \
    LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/lib/rocm_sysdeps/lib

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && echo "deb [trusted=yes] https://rc.repo.amd.com/rocm/core/packages/ubuntu2404 stable main" \
        > /etc/apt/sources.list.d/rocm-10.1-prerelease.list \
    && printf 'Package: amdrocm*\nPin: version %s\nPin-Priority: 1001\n' "${ROCM_VERSION}" \
        > /etc/apt/preferences.d/rocm-10.1-prerelease \
    && apt-get update \
    && pkgs="" \
    && for gfx in ${ROCM_GFX_TARGETS}; do \
         pkgs="${pkgs} amdrocm-dnn10.1-${gfx} amdrocm-rand10.1-${gfx}"; \
       done \
    && apt-get install -y --no-install-recommends \
        python3 \
        python-is-python3 \
        python3-tqdm \
        ${pkgs} \
    && cd /tmp \
    && apt-get download \
        "amdrocm-runtime-dev10.1=${ROCM_VERSION}" \
        "amdrocm-dnn-dev10.1=${ROCM_VERSION}" \
    && dpkg --force-depends -i /tmp/amdrocm-runtime-dev10.1_*.deb /tmp/amdrocm-dnn-dev10.1_*.deb \
    && rm -f /tmp/amdrocm-*.deb \
    && for dir in bin include lib libexec share; do \
         if [ -d "/opt/rocm/core-10.1/${dir}" ]; then \
           ln -sfn "core-10.1/${dir}" "/opt/rocm/${dir}"; \
         fi; \
       done \
    && mkdir -p /opt/rocm/.info \
    && printf '%s\n' "${ROCM_VERSION}" > /opt/rocm/.info/version \
    && python -c "import tqdm" \
    && test -x /opt/rocm/bin/MIOpenDriver \
    && test -x /opt/rocm/bin/rocminfo \
    && test -x /opt/rocm/bin/hipconfig \
    && test -x /opt/rocm/bin/hipcc \
    && test -d /opt/rocm/share/miopen/db \
    && test -d /opt/rocm/lib/llvm/amdgcn/bitcode \
    && if ldd /opt/rocm/bin/MIOpenDriver /opt/rocm/lib/libMIOpen.so | grep -q "not found"; then \
         ldd /opt/rocm/bin/MIOpenDriver /opt/rocm/lib/libMIOpen.so; \
         exit 1; \
       fi \
    && rm -rf /var/lib/apt/lists/*
