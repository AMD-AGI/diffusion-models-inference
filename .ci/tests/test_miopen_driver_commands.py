# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


sys.path.insert(0, str(Path(__file__).parents[1]))

from miopen_driver_commands import (  # noqa: E402
    MIOpenDriverCommandCollector,
    env_enabled,
)


class TestEnvEnabled(unittest.TestCase):
    def test_accepts_common_truthy_values(self):
        for value in ("1", "true", "TRUE", "yes", "on", " On "):
            with self.subTest(value=value):
                self.assertTrue(env_enabled(value))

    def test_defaults_and_other_values_to_disabled(self):
        for value in ("", "0", "false", "enabled"):
            with self.subTest(value=value):
                self.assertFalse(env_enabled(value))


class TestMIOpenDriverCommandCollector(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.results_directory = Path(self.temporary_directory.name)

    def _manifest(self):
        path = self.results_directory / "miopen_workloads" / "manifest.json"
        return json.loads(path.read_text())

    def test_collects_sorted_unique_commands_without_log_prefix(self):
        collector = MIOpenDriverCommandCollector(
            self.results_directory, ["wan2_2.default"]
        )
        stderr_path = self.results_directory / "stderr.txt"
        stderr_path.write_text(
            "unrelated output\n"
            "MIOpen(HIP): Info [CommandLogging] MIOpenDriver convfp16 -n 2\n"
            "rank 1: MIOpenDriver convfp16 -n 1\n"
            "rank 2: MIOpenDriver convfp16 -n 2\n"
        )

        collector.collect("wan2_2.default", stderr_path, process_succeeded=True)
        collector.mark_succeeded("wan2_2.default")

        output_path = (
            self.results_directory / "miopen_workloads" / "wan2_2.default.txt"
        )
        self.assertEqual(
            output_path.read_text(),
            "MIOpenDriver convfp16 -n 1\nMIOpenDriver convfp16 -n 2\n",
        )
        self.assertEqual(
            self._manifest()["workloads"],
            [
                {
                    "name": "wan2_2.default",
                    "benchmark_status": "succeeded",
                    "collection_status": "collected",
                    "command_count": 2,
                    "file": "wan2_2.default.txt",
                }
            ],
        )

    def test_marks_nonzero_process_output_as_partial(self):
        collector = MIOpenDriverCommandCollector(
            self.results_directory, ["flux.default"]
        )
        stderr_path = self.results_directory / "stderr.txt"
        stderr_path.write_text("MIOpenDriver conv -n 1\n")

        collector.collect("flux.default", stderr_path, process_succeeded=False)

        output_path = (
            self.results_directory / "miopen_workloads" / "flux.default.partial.txt"
        )
        self.assertEqual(output_path.read_text(), "MIOpenDriver conv -n 1\n")
        entry = self._manifest()["workloads"][0]
        self.assertEqual(entry["benchmark_status"], "process_failed")
        self.assertEqual(entry["file"], "flux.default.partial.txt")

    def test_omits_empty_file_and_keeps_not_run_workloads(self):
        collector = MIOpenDriverCommandCollector(
            self.results_directory, ["empty.default", "not_run.default"]
        )
        stderr_path = self.results_directory / "stderr.txt"
        stderr_path.write_text("no MIOpen calls\n")

        collector.collect("empty.default", stderr_path, process_succeeded=True)
        collector.mark_metrics_failed("empty.default")

        self.assertFalse(
            (self.results_directory / "miopen_workloads" / "empty.default.txt").exists()
        )
        entries = self._manifest()["workloads"]
        self.assertEqual(entries[0]["benchmark_status"], "metrics_failed")
        self.assertEqual(entries[0]["collection_status"], "no_commands")
        self.assertEqual(entries[1]["benchmark_status"], "not_run")
        self.assertEqual(entries[1]["collection_status"], "not_started")

    def test_records_collection_error_without_raising(self):
        collector = MIOpenDriverCommandCollector(
            self.results_directory, ["missing.default"]
        )

        with self.assertLogs("miopen_driver_commands", level="WARNING"):
            collector.collect(
                "missing.default",
                self.results_directory / "missing-stderr.txt",
                process_succeeded=True,
            )

        entry = self._manifest()["workloads"][0]
        self.assertEqual(entry["benchmark_status"], "process_succeeded")
        self.assertEqual(entry["collection_status"], "error")
        self.assertEqual(entry["command_count"], 0)
        self.assertIsNone(entry["file"])


if __name__ == "__main__":
    unittest.main()
