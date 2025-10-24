# CONTEXT {'gpu_vendor': 'AMD', 'guest_os': 'UBUNTU'}
ARG BASE_DOCKER=amdsiloai/pytorch-xdit:v25.10
FROM ${BASE_DOCKER} as base

RUN apt-get update && apt install -y lshw && rm -rf /var/lib/apt/lists/*
