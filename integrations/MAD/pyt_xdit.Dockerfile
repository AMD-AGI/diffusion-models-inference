# CONTEXT {'gpu_vendor': 'AMD', 'guest_os': 'UBUNTU'}
FROM amdsiloai/pytorch-xdit:latest

RUN apt-get update && apt install -y lshw && rm -rf /var/lib/apt/lists/*
