# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional


logger = logging.getLogger(__name__)


class MemorySampler:
    """Background peak of per-GPU VRAM and host anonymous memory for one benchmark."""

    _INTERVAL_S = 0.2

    def __init__(self, output_path: Path, enabled: bool = True) -> None:
        self._output_path = output_path
        self._enabled = enabled
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._smi: Optional[tuple] = None
        self._vram_start: Dict[str, int] = {}
        self._peak_vram: Dict[str, int] = {}
        self._host_start: Optional[int] = None
        self._peak_host: Optional[int] = None

    def __enter__(self) -> "MemorySampler":
        if not self._enabled:
            return self
        self._smi = self._find_smi()
        if self._smi is None:
            logger.warning("No usable rocm-smi or nvidia-smi; VRAM will be omitted.")
        self._vram_start = self._read_vram()
        self._peak_vram = dict(self._vram_start)
        self._host_start = self._host_anon_bytes()
        self._peak_host = self._host_start
        self._thread = threading.Thread(target=self._track, name="memory-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        if not self._enabled:
            return False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._write()
        return False

    def _track(self) -> None:
        while not self._stop.is_set():
            self._update_peaks()
            self._stop.wait(self._INTERVAL_S)
        self._update_peaks()

    def _update_peaks(self) -> None:
        try:
            if self._smi is not None:
                for gpu, used in self._read_vram().items():
                    if used > self._peak_vram.get(gpu, 0):
                        self._peak_vram[gpu] = used
            host = self._host_anon_bytes()
            if host is not None and (self._peak_host is None or host > self._peak_host):
                self._peak_host = host
        except Exception:
            pass

    def _find_smi(self) -> Optional[tuple]:
        for name, read in (
            ("rocm-smi", self._rocm_smi_used_bytes),
            ("nvidia-smi", self._nvidia_smi_used_bytes),
        ):
            binary = shutil.which(name)
            if binary is None:
                continue
            try:
                if read(binary):
                    return (binary, read)
            except Exception:
                continue
        return None

    def _read_vram(self) -> Dict[str, int]:
        if self._smi is None:
            return {}
        binary, read = self._smi
        return read(binary)

    @staticmethod
    def _nvidia_smi_used_bytes(binary: str) -> Dict[str, int]:
        output = subprocess.check_output(
            [binary, "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        used = {}
        for line in output.strip().splitlines():
            index, mebibytes = (field.strip() for field in line.split(","))
            used[f"gpu{index}"] = int(float(mebibytes)) * 1024 * 1024
        return used

    @staticmethod
    def _rocm_smi_used_bytes(binary: str) -> Dict[str, int]:
        output = subprocess.check_output(
            [binary, "--showmeminfo", "vram", "--json"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        used = {}
        for card, fields in json.loads(output).items():
            if not isinstance(fields, dict):
                continue
            index = card.removeprefix("card")
            for key, value in fields.items():
                lowered = key.lower()
                if "used" in lowered and "memory" in lowered:
                    used[f"gpu{index}"] = int(str(value).strip())
                    break
        return used

    @staticmethod
    def _cgroup_stat(path: str, key: str) -> Optional[int]:
        try:
            with open(path) as handle:
                for line in handle:
                    name, _, value = line.partition(" ")
                    if name == key:
                        return int(value)
        except OSError:
            return None
        return None

    @classmethod
    def _host_anon_bytes(cls) -> Optional[int]:
        # memory.current is anon + file. file is reclaimable checkpoint cache; anon is the allocation.
        anon = cls._cgroup_stat("/sys/fs/cgroup/memory.stat", "anon")
        if anon is not None:
            return anon
        for key in ("total_rss", "rss"):
            rss = cls._cgroup_stat("/sys/fs/cgroup/memory/memory.stat", key)
            if rss is not None:
                return rss
        return None

    def _write(self) -> None:
        payload = {
            "vram_start_bytes": self._vram_start,
            "peak_vram_bytes": self._peak_vram,
            "host_anon_start_bytes": self._host_start,
            "peak_host_anon_bytes": self._peak_host,
        }
        try:
            with open(self._output_path, "w") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as error:
            logger.warning(f"Failed to write memory stats to {self._output_path}: {error}")
