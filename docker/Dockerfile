# Build example image with
#
# docker build -f docker/Dockerfile \
#              -t amdsiloai/pytorch-xdit:latest \
#              --build-arg GPU_ARCH=gfx950 \
#              --build-arg THEROCK_ROCM_NIGHTLY="20250820" .
#
# when running from repository root.
#

# core image arguments
ARG GPU_ARCH="gfx950"
ARG THEROCK_COMMIT="4d69f64"
ARG THEROCK_ROCM_NIGHTLY="20250820"
ARG TRITON_BRANCH="pytorch/rocm7.1_internal_testing"
ARG PYTORCH_BRANCH="triton_kernel_wrap_fix"
ARG PYTORCH_VISION_COMMIT="824e8c8726b65fd9d5abdc9702f81c2b0c4c0dc8"
# deps image arguments
ARG AITER_COMMIT="9700732"
ARG LONGCONTEXTATTENTION_COMMIT="e59b8f8"
ARG DIFFUSERS_COMMIT="5dbe4f5"
ARG ACCELERATE_COMMIT="34c1779"
ARG XDIT_COMMIT="a440a6e"
ARG AO_COMMIT="8b72284"
# workload specific arguments
ARG WAN1_REPO="https://github.com/Wan-Video/Wan2.1.git"
ARG WAN1_COMMIT="204f899"
ARG WAN2_REPO="https://github.com/Wan-Video/Wan2.2.git"
ARG WAN2_COMMIT="031a9be"

FROM ubuntu:24.04 AS base
ARG GPU_ARCH
ARG THEROCK_ROCM_NIGHTLY

# install depedencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libgfortran5 \
    libgl1 \
    libglib2.0-dev \
    ninja-build \
    python3 \
    python3-dev \
    python3-packaging \
    python3-pip \
    python3-venv \
    wget \
    && rm -rf /var/lib/apt/lists/*

# setup environment
RUN python3 -m venv venv
ENV PATH="/venv/bin:$PATH"
RUN git config --global user.name "TheRock Builder" && \
    git config --global user.email "builder@rocm.ai"

# Install ROCm stack
RUN python -m pip install \
    --no-cache-dir \
    --index-url "https://d2awnip2yjpvqn.cloudfront.net/v2/${GPU_ARCH}-dcgpu/" \
    "rocm[libraries,devel]==7.0.0rc${THEROCK_ROCM_NIGHTLY}"
# Running `rocm-sdk path --root` ensures `/app/.venv/lib/python3.12/site-packages/_rocm_sdk_devel` is populated 
RUN rocm-sdk path --root

FROM base AS build_torch
ARG THEROCK_COMMIT
ARG TRITON_BRANCH
ARG PYTORCH_BRANCH
ARG PYTORCH_VISION_COMMIT

WORKDIR /app
RUN git clone https://github.com/ROCm/TheRock.git \
    && cd TheRock/external-builds/pytorch \
    && git checkout ${THEROCK_COMMIT} \
    && python pytorch_torch_repo.py checkout --gitrepo-origin https://github.com/ROCm/pytorch.git --repo-hashtag ${PYTORCH_BRANCH} \
    && python pytorch_triton_repo.py checkout --gitrepo-origin https://github.com/ROCm/triton.git --repo-hashtag ${TRITON_BRANCH} \
    && python pytorch_vision_repo.py checkout --gitrepo-origin https://github.com/pytorch/vision.git --repo-hashtag ${PYTORCH_VISION_COMMIT} \
    && python build_prod_wheels.py build --output-dir /install

FROM base AS core
COPY --from=build_torch /install/*.whl /install/
RUN python -m pip install \
    --no-cache-dir \
    /install/*.whl
COPY docker/libkineto.conf /config/libkineto.conf
ENV PATH="/venv/lib/python3.12/site-packages/_rocm_sdk_devel/bin:$PATH" \
    ROCM_HOME=/venv/lib/python3.12/site-packages/_rocm_sdk_devel \
    LD_LIBRARY_PATH=/venv/lib/python3.12/site-packages/_rocm_sdk_devel/lib \
    TRITON_HIP_LLD_PATH=/venv/lib/python3.12/site-packages/_rocm_sdk_core/lib/llvm/bin/ld.lld \
    KINETO_CONFIG="/config/libkineto.conf"

FROM core AS private-deps

WORKDIR /app/external
# build opt_groupnorm
COPY src/opt_groupnorm opt_groupnorm/
RUN pip install setuptools && \
    cd opt_groupnorm && \
    MAX_JOBS=${MAX_JOBS} pip wheel --no-build-isolation . --wheel-dir dist/

FROM core AS deps
ARG AITER_COMMIT
ARG LONGCONTEXTATTENTION_COMMIT
ARG DIFFUSERS_COMMIT
ARG ACCELERATE_COMMIT
ARG XDIT_COMMIT
ARG AO_COMMIT

# setup
WORKDIR /app/external
COPY ./src/patches /patches
# install opt_groupnorm
COPY --from=private-deps /app/external/opt_groupnorm/dist/*.whl /install/opt_groupnorm/
RUN pip install /install/opt_groupnorm/*.whl
# install aiter
RUN git clone https://github.com/ROCm/aiter.git && \
    cd aiter && \
    git reset --hard ${AITER_COMMIT} && \
    git submodule update --init --recursive && \
    python setup.py develop
# install yunchang
RUN git clone https://github.com/feifeibear/long-context-attention.git && \
    cd long-context-attention && \
    git reset --hard ${LONGCONTEXTATTENTION_COMMIT} && \
    git apply /patches/longcontextattention.patch && \
    pip install -e .
# install diffusers
RUN git clone https://github.com/huggingface/diffusers.git && \
    cd diffusers && \
    git reset --hard ${DIFFUSERS_COMMIT} && \
    git apply /patches/diffusers.patch && \
    pip install -e ".[torch]"
# install accelerate
RUN git clone https://github.com/huggingface/accelerate.git && \
    cd accelerate && \
    git reset --hard ${ACCELERATE_COMMIT} && \
    pip install -e .
# install xfuser
RUN git clone https://github.com/xdit-project/xDiT.git && \
    cd xDiT && \
    git reset --hard ${XDIT_COMMIT} && \
    git apply /patches/xdit.patch && \
    pip freeze | grep numpy > constraints.txt && \
    pip install -c constraints.txt -e .
# install torchao
RUN git clone https://github.com/pytorch/ao.git && \
    cd ao && \
    git reset --hard ${AO_COMMIT} && \
    git apply /patches/ao.patch && \
    pip install -e . --no-build-isolation
# cleanup
RUN rm -fr /patches

FROM deps AS hunyuanvideo

WORKDIR /app/Hunyuanvideo
COPY src/xDiT/ci/README.huvideo /app/Hunyuanvideo/run.md
COPY src/xDiT/hunyuan_video_scripts/huvideo_*.py /app/Hunyuanvideo/
COPY src/xDiT/hunyuan_video_scripts/hunyuan_video_usp_example_custom.py /app/Hunyuanvideo/run.py

FROM deps AS wan2.1
ARG WAN1_REPO
ARG WAN1_COMMIT

WORKDIR /app
RUN pip install "peft>=0.17.0" && \
    git clone ${WAN1_REPO} Wan2.1 && \
    cd Wan2.1 && \
    git reset --hard ${WAN1_COMMIT} && \
    sed -i '/^flash_attn/d' requirements.txt && \
    sed -i '/^torch/d' requirements.txt && \
    sed -i '/^torchvision/d' requirements.txt
COPY src/wan_2_1/ci/README.md /app/Wan2.1/run.md
COPY src/wan_2_1/scripts/wan_i2v.py /app/Wan2.1/run.py
COPY src/wan_2_1/scripts/list_*.py /app/Wan2.1/
COPY src/wan_2_1/scripts/timer.py /app/Wan2.1/
COPY src/wan_2_1/patches /patches
RUN git -C ./Wan2.1 apply /patches/image2video.patch && \
    git -C ./Wan2.1 apply /patches/supported_sizes.patch && \
    git -C ./Wan2.1 apply /patches/fsdp.patch && \
    git -C ./Wan2.1 apply /patches/attention.patch && \
    git -C ./Wan2.1 apply /patches/rmsnorm.patch 

FROM deps AS wan2.2
ARG WAN2_REPO
ARG WAN2_COMMIT

WORKDIR /app
RUN pip install "peft>=0.17.0" && \
    git clone ${WAN2_REPO} Wan2.2 && \
    cd Wan2.2 && \
    git reset --hard ${WAN2_COMMIT} && \
    sed -i '/^flash_attn/d' requirements.txt && \
    sed -i '/^torch/d' requirements.txt && \
    sed -i '/^torchvision/d' requirements.txt
COPY src/wan_2_2/ci/README.md /app/Wan2.2/run.md
COPY src/wan_2_2/scripts/wan_i2v.py /app/Wan2.2/run.py
COPY src/wan_2_1/scripts/list_*.py /app/Wan2.2/
COPY src/wan_2_1/scripts/timer.py /app/Wan2.2/
COPY src/wan_2_1/patches /patches
RUN git -C ./Wan2.2 apply /patches/attention.patch && \
    patch -d ./Wan2.2 -p1 < /patches/fsdp.patch 
COPY src/wan_2_2/patches /patches
RUN git -C ./Wan2.2 apply /patches/image2video.patch && \
    git -C ./Wan2.2 apply /patches/supported_sizes.patch && \
    git -C ./Wan2.2 apply /patches/rmsnorm.patch

FROM deps AS final

WORKDIR /app    
COPY --from=hunyuanvideo /app/Hunyuanvideo /app/Hunyuanvideo
COPY --from=wan2.1 /app/Wan2.1 /app/Wan2.1
RUN cd /app/Wan2.1 && \
    pip install --no-cache-dir -r requirements.txt
COPY --from=wan2.2 /app/Wan2.2 /app/Wan2.2
RUN cd /app/Wan2.2 && \
    pip install --no-cache-dir -r requirements.txt
# use shared MIOpen databases
COPY ./data/miopen/userdb /miopen_userdb
ENV MIOPEN_USER_DB_PATH="/miopen_userdb"
