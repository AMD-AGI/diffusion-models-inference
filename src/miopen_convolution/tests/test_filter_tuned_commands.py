# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import logging
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from miopen_convolution import filter_tuned_commands as filter_module
from miopen_convolution.filter_tuned_commands import (
    filter_commands,
    get_database_filename,
    load_database,
)


COMMAND = (
    "MIOpenDriver convbfp16 -n 1 -c 1024 --in_d 16 -H 34 -W 60 -k 128 "
    "--fil_d 3 -y 3 -x 3 --pad_d 1 -p 1 -q 1 --conv_stride_d 1 -u 1 -v 1 "
    "--dilation_d 1 -l 1 -j 1 --spatial_dim 3 -m conv -g 1 -F 1 -t 1"
)
KEY = "1024-16-34-60-3x3x3-128-16-34-60-1-1x1x1-1x1x1-1x1x1-0-NCDHW-BF16-F"
OTHER_KEY = "128-11-130-194-3x3x3-128-9-128-192-1-0x0x0-1x1x1-1x1x1-0-NCDHW-FP16-F"
UNPARSEABLE_COMMAND = "MIOpenDriver convbfp16 -n not-a-number"


class FilterTunedCommandsTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.db_dir = self.root / "userdb"
        self.db_dir.mkdir()
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def write(self, name, text):
        path = self.db_dir / name
        path.write_text(text)
        return path

    def run_main(self, commands, db_prefix="gfx950100"):
        commands_path = self.root / "commands.txt"
        output_path = self.root / "filtered.txt"
        commands_path.write_text("".join(f"{command}\n" for command in commands))
        argv = [
            "filter_tuned_commands.py",
            str(commands_path),
            str(output_path),
            "--db-path",
            str(self.db_dir),
            "--db-prefix",
            db_prefix,
        ]
        with mock.patch.object(sys, "argv", argv):
            filter_module.main()
        return output_path.read_text().splitlines()

    def test_load_database_uses_keys_and_ignores_opaque_payloads(self):
        db_path = self.write(
            "gfx950100.HIP.version.ufdb.txt",
            f"# comment\n\n{KEY}=opaque:payload\nnot-a-key=opaque\n",
        )

        problems = load_database(db_path)

        self.assertEqual(len(problems), 1)
        self.assertEqual(next(iter(problems)), filter_module.MIOpenConvolution.from_db_key(KEY))

    def test_filter_commands_skips_matching_problem(self):
        tuned = {filter_module.MIOpenConvolution.from_db_key(KEY)}

        untuned, skipped = filter_commands([COMMAND, "", "# comment"], tuned)

        self.assertEqual(untuned, [])
        self.assertEqual(skipped, [COMMAND])

    def test_filter_commands_keeps_unmatched_problem(self):
        tuned = {filter_module.MIOpenConvolution.from_db_key(OTHER_KEY)}

        untuned, skipped = filter_commands([COMMAND], tuned)

        self.assertEqual(untuned, [COMMAND])
        self.assertEqual(skipped, [])

    def test_main_without_prefix_keeps_all_commands(self):
        self.write("gfx950100.HIP.version.ufdb.txt", f"{KEY}=opaque\n")

        self.assertEqual(self.run_main([COMMAND], db_prefix=""), [COMMAND])

    @unittest.expectedFailure
    def test_multiple_database_versions_for_prefix_are_ambiguous(self):
        """Known defect: the first sorted DB is selected silently."""
        self.write("gfx950100.HIP.1.ufdb.txt", "")
        self.write("gfx950100.HIP.2.ufdb.txt", "")

        with self.assertRaises(ValueError):
            get_database_filename("gfx950100", self.db_dir)

    @unittest.expectedFailure
    def test_find_record_without_performance_record_requires_tuning(self):
        """Known defect: only the find DB is checked before skipping."""
        self.write("gfx950100.HIP.version.ufdb.txt", f"{KEY}=opaque-find-record\n")
        self.write("gfx950100.HIP.version.udb.txt", f"{OTHER_KEY}=opaque-performance-record\n")

        self.assertEqual(self.run_main([COMMAND]), [COMMAND])

    @unittest.expectedFailure
    def test_unparseable_command_is_rejected(self):
        """Known defect: commands that fail parsing are still tuned."""
        untuned, skipped = filter_commands([UNPARSEABLE_COMMAND], set())

        self.assertEqual(untuned, [])
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
