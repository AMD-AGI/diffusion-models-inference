# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import tempfile
import unittest
from pathlib import Path

from miopen_tuning.identity import (
    GpuAgent,
    device_db_prefix,
    expected_db_version_token,
    parse_gpu_agents,
    parse_driver_version,
    parse_version_header,
    read_header_version,
)


ROCMINFO = """\
Agent 1
*******
  Name:                    AMD EPYC 9655 96-Core Processor
  Marketing Name:          AMD EPYC 9655 96-Core Processor
  Device Type:             CPU
  Compute Unit:            192
Agent 2
*******
  Name:                    gfx950
  Uuid:                    GPU-e7c3efd60b805133
  Marketing Name:          AMD Instinct MI350X
  Device Type:             GPU
  Compute Unit:            256
  ISA Info:
    ISA 1
      Name:                    amdgcn-amd-amdhsa--gfx950:sramecc+:xnack-
"""


class IdentityTest(unittest.TestCase):
    def test_parse_driver_version(self):
        self.assertEqual(parse_driver_version("MIOpen (version: 3.6.3)\n"), "3.6.3")

    def test_parse_version_header(self):
        text = """
#define MIOPEN_VERSION_MAJOR 3
#define MIOPEN_VERSION_MINOR 6
#define MIOPEN_VERSION_PATCH 3
#define MIOPEN_VERSION_TWEAK 37f616f6
"""
        self.assertEqual(parse_version_header(text), ("3.6.3", "37f616f6"))

    def test_read_header_from_core_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            header = root / "core-10.3" / "include" / "miopen" / "version.h"
            header.parent.mkdir(parents=True)
            header.write_text(
                "#define MIOPEN_VERSION_MAJOR 3\n"
                "#define MIOPEN_VERSION_MINOR 6\n"
                "#define MIOPEN_VERSION_PATCH 3\n"
                "#define MIOPEN_VERSION_TWEAK abc\n"
            )
            self.assertEqual(read_header_version([root]), ("3.6.3", "abc"))

    def test_parse_gpu_agents_skips_cpus_and_isa_names(self):
        self.assertEqual(parse_gpu_agents(ROCMINFO), [GpuAgent("gfx950", "AMD Instinct MI350X", 256)])

    def test_db_prefix_is_architecture_and_hex_compute_units(self):
        # Matches the system DB names shipped with MIOpen for MI300X, MI300A, and MI350X.
        for agent, prefix in (
            (GpuAgent("gfx942", "AMD Instinct MI300X", 304), "gfx942130"),
            (GpuAgent("gfx942", "AMD Instinct MI300A", 228), "gfx942e4"),
            (GpuAgent("gfx950", "AMD Instinct MI350X", 256), "gfx950100"),
        ):
            with self.subTest(prefix=prefix):
                self.assertEqual(agent.db_prefix, prefix)

    def test_device_db_prefix_requires_one_prefix(self):
        mi350x = GpuAgent("gfx950", "AMD Instinct MI350X", 256)
        self.assertEqual(device_db_prefix([mi350x, mi350x]), "gfx950100")
        with self.assertRaises(RuntimeError):
            device_db_prefix([])
        with self.assertRaises(RuntimeError):
            device_db_prefix([mi350x, GpuAgent("gfx950", "AMD Instinct MI350X", 32)])

    def test_db_version_token(self):
        self.assertEqual(expected_db_version_token("3.6.3", "37f616f6"), "3_6_3_37f616f6")


if __name__ == "__main__":
    unittest.main()
