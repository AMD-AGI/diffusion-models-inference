# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import tempfile
import unittest
from pathlib import Path

from miopen_tuning.identity import (
    expected_db_version_token,
    load_prefix_map,
    parse_driver_version,
    parse_version_header,
    read_header_version,
    resolve_prefix,
)


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

    def test_prefix_map_resolves_marketing_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixes.txt"
            path.write_text("# comment\nMI300X=gfx942130\nMI350X=gfx950100\n")
            prefix_map = load_prefix_map(path)
        self.assertEqual(resolve_prefix(["AMD Instinct MI350X"], prefix_map), "gfx950100")
        self.assertEqual(resolve_prefix(["Unknown GPU"], prefix_map), "")

    def test_invalid_prefix_map_line_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefixes.txt"
            path.write_text("MI350X\n")
            with self.assertRaises(ValueError):
                load_prefix_map(path)

    def test_db_version_token(self):
        self.assertEqual(expected_db_version_token("3.6.3", "37f616f6"), "3_6_3_37f616f6")


if __name__ == "__main__":
    unittest.main()
