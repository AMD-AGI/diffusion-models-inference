# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).parents[1]))

from memory_sampler import MemorySampler  # noqa: E402


class TestMemorySamplerParsers(unittest.TestCase):
    def test_nvidia_smi_used_bytes(self):
        output = "0, 1024\n1, 2048.5\n"
        with patch("memory_sampler.subprocess.check_output", return_value=output):
            used = MemorySampler._nvidia_smi_used_bytes("nvidia-smi")
        self.assertEqual(used["gpu0"], 1024 * 1024 * 1024)
        self.assertEqual(used["gpu1"], int(2048.5) * 1024 * 1024)

    def test_rocm_smi_used_bytes(self):
        payload = {
            "card0": {"VRAM Total Used Memory (B)": "12345"},
            "card1": {"ignored": "1"},
            "system": "not-a-dict",
        }
        with patch("memory_sampler.subprocess.check_output", return_value=json.dumps(payload)):
            used = MemorySampler._rocm_smi_used_bytes("rocm-smi")
        self.assertEqual(used, {"gpu0": 12345})

    def test_cgroup_stat_reads_matching_key(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.stat"
            path.write_text("file 10\nanon 42\n")
            self.assertEqual(MemorySampler._cgroup_stat(str(path), "anon"), 42)
            self.assertIsNone(MemorySampler._cgroup_stat(str(path), "missing"))

    def test_cgroup_stat_missing_file(self):
        self.assertIsNone(MemorySampler._cgroup_stat("/no/such/memory.stat", "anon"))


class TestMemorySamplerContext(unittest.TestCase):
    def test_disabled_sampler_does_not_write(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "memory.json"
            with MemorySampler(output_path, enabled=False):
                pass
            self.assertFalse(output_path.exists())

    def test_writes_peak_payload(self):
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "memory.json"
            sampler = MemorySampler(output_path)
            sampler._smi = ("rocm-smi", lambda _binary: {"gpu0": 10})
            sampler._vram_start = {"gpu0": 1}
            sampler._peak_vram = {"gpu0": 10}
            sampler._host_start = 2
            sampler._peak_host = 20
            sampler._write()
            payload = json.loads(output_path.read_text())
        self.assertEqual(payload["vram_start_bytes"], {"gpu0": 1})
        self.assertEqual(payload["peak_vram_bytes"], {"gpu0": 10})
        self.assertEqual(payload["host_anon_start_bytes"], 2)
        self.assertEqual(payload["peak_host_anon_bytes"], 20)
