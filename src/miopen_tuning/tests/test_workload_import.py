# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import tempfile
import unittest
from pathlib import Path

from miopen_tuning.workloads import import_collected_workloads


GOOD = "MIOpenDriver convbfp16 -n 1 -c 16 -H 16 -W 16 -k 16 -y 3 -x 3 -p 1 -q 1 -u 1 -v 1 -l 1 -j 1 -m conv -g 1 -F 1 -t 1"
OTHER = "MIOpenDriver convfp16 -n 1 -c 32 -H 17 -W 19 -k 32 -y 3 -x 3 -p 1 -q 1 -u 2 -v 2 -l 1 -j 1 -m conv -g 2 -F 1 -t 1"


class WorkloadImportTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.workloads = self.root / "workloads"
        self.workloads.mkdir()
        self.artifact = self.root / "123-miopen-workloads-gfx950"
        self.artifact.mkdir()

    def manifest(self, *entries):
        (self.artifact / "manifest.json").write_text(json.dumps({"workloads": list(entries)}))

    def test_reports_and_writes_changed_architecture_file(self):
        (self.workloads / "flux.gfx950.txt").write_text(f"{OTHER}\n")
        (self.artifact / "flux.txt").write_text(f"{GOOD}\n")
        self.manifest({"name": "flux", "file": "flux.txt", "benchmark_status": "succeeded", "collection_status": "collected"})

        report = import_collected_workloads([self.artifact], self.workloads, write=True)

        entry = report["imports"][0]
        self.assertEqual(entry["architecture"], "gfx950")
        self.assertEqual(entry["added"], [GOOD])
        self.assertEqual(entry["removed"], [OTHER])
        self.assertEqual((self.workloads / "flux.gfx950.txt").read_text(), f"{GOOD}\n")

    def test_flags_partial_collection_without_writing(self):
        (self.artifact / "flux.partial.txt").write_text(f"{GOOD}\n")
        self.manifest({"name": "flux", "file": "flux.partial.txt", "benchmark_status": "process_failed", "collection_status": "collected"})

        report = import_collected_workloads([self.artifact], self.workloads, write=True)

        self.assertEqual(report["counts"]["flagged"], 1)
        self.assertFalse((self.workloads / "flux.txt").exists())

    def test_rejects_invalid_collected_commands(self):
        (self.artifact / "flux.txt").write_text("MIOpenDriver convbfp16 -n 1\n")
        self.manifest({"name": "flux", "file": "flux.txt", "benchmark_status": "succeeded", "collection_status": "collected"})

        report = import_collected_workloads([self.artifact], self.workloads, write=True)

        self.assertEqual(report["counts"]["rejected"], 1)
        self.assertFalse((self.workloads / "flux.txt").exists())

    def test_reports_workloads_without_commands(self):
        self.manifest({"name": "empty", "file": None, "benchmark_status": "succeeded", "collection_status": "no_commands"})

        report = import_collected_workloads([self.artifact], self.workloads)

        self.assertEqual(report["counts"]["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
