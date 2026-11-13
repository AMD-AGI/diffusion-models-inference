# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import tempfile
import unittest
from pathlib import Path

from miopen_tuning.databases import DatabaseError, DatabaseIdentity, merge_pairs, read_records, validate_pair
from miopen_tuning.planner import build_plan
from miopen_tuning.workloads import load_workloads


IDENTITY = DatabaseIdentity("gfx950100", "3.6.3", "37f616f6")
COMMAND = "MIOpenDriver convbfp16 -n 1 -c 16 -H 16 -W 16 -k 16 -y 3 -x 3 -p 1 -q 1 -u 1 -v 1 -l 1 -j 1 -m conv -g 1 -F 1 -t 1"
FIND_KEY = "16-16-16-3x3-16-16-16-1-1x1-1x1-1x1-0-NCHW-BF16-F"
PERF_KEY = "2x16x16x16x1x3x3x1x16x1x1x1x0x1x1x0x1x1x0x0x1xNCHWxBF16xF"


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def write_pair(self, directory, find=None, perf=None, identity=IDENTITY):
        directory.mkdir(parents=True, exist_ok=True)
        if find is not None:
            identity.path(directory, "ufdb.txt").write_text(find)
        if perf is not None:
            identity.path(directory, "udb.txt").write_text(perf)

    def workloads(self, text=COMMAND + "\n"):
        path = self.root / "workload.txt"
        path.write_text(text)
        return load_workloads([path], self.root)

    def test_identity_selects_exact_db_stem(self):
        self.assertEqual(IDENTITY.stem, "gfx950100.HIP.3_6_3_37f616f6")

    def test_plan_skips_only_when_both_exact_records_exist(self):
        directory = self.root / "db"
        self.write_pair(directory, f"{FIND_KEY}=opaque\n", f"{PERF_KEY}=opaque\n")

        plan = build_plan(self.workloads(), directory, IDENTITY)

        self.assertEqual(plan["counts"], {"tune": 0, "skip": 1, "reject": 0})

    def test_plan_tunes_when_performance_record_is_missing(self):
        directory = self.root / "db"
        self.write_pair(directory, f"{FIND_KEY}=opaque\n", "")

        plan = build_plan(self.workloads(), directory, IDENTITY)

        self.assertEqual(plan["counts"]["tune"], 1)
        self.assertEqual(plan["entries"][0]["reasons"], ["performance DB record"])

    def test_plan_ignores_records_for_another_miopen_version(self):
        directory = self.root / "db"
        other = DatabaseIdentity("gfx950100", "3.6.2", "oldbuild")
        self.write_pair(directory, f"{FIND_KEY}=opaque\n", f"{PERF_KEY}=opaque\n", other)

        plan = build_plan(self.workloads(), directory, IDENTITY)

        self.assertEqual(plan["counts"]["tune"], 1)
        self.assertEqual(plan["other_db_versions"], ["gfx950100.HIP.3_6_2_oldbuild"])

    def test_workloads_keep_duplicate_sources_and_reject_invalid_commands(self):
        workloads = self.workloads(f"{COMMAND}\n{COMMAND}\nMIOpenDriver convbfp16 -n 1\n")

        self.assertEqual(len(workloads.problems), 1)
        self.assertEqual([source.line for source in next(iter(workloads.problems.values())).sources], [1, 2])
        self.assertEqual(len(workloads.rejected), 1)

    def test_validate_reports_missing_cross_file_records(self):
        directory = self.root / "db"
        self.write_pair(directory, f"{FIND_KEY}=opaque\n", "")

        report = validate_pair(directory, IDENTITY)

        self.assertTrue(report["valid"])
        self.assertEqual(report["find_without_perf"], [FIND_KEY])

    def test_merge_replaces_records_by_key(self):
        base = self.root / "base"
        tuned = self.root / "tuned"
        output = self.root / "output"
        self.write_pair(base, f"{FIND_KEY}=old\n", f"{PERF_KEY}=old\n")
        self.write_pair(tuned, f"{FIND_KEY}=new\n", f"{PERF_KEY}=new\n")

        report = merge_pairs(base, [tuned], output, IDENTITY)

        self.assertEqual(read_records(IDENTITY.path(output, "ufdb.txt")).records, {FIND_KEY: "new"})
        self.assertEqual(report["ufdb.txt"]["replaced"], [FIND_KEY])

    def test_merge_rejects_conflicting_tuned_records(self):
        first = self.root / "first"
        second = self.root / "second"
        self.write_pair(first, f"{FIND_KEY}=one\n", f"{PERF_KEY}=one\n")
        self.write_pair(second, f"{FIND_KEY}=two\n", f"{PERF_KEY}=two\n")

        with self.assertRaises(DatabaseError):
            merge_pairs(None, [first, second], self.root / "output", IDENTITY)

    def test_merge_without_tuned_files_raises_unless_allowed(self):
        base = self.root / "base"
        empty = self.root / "tuned"
        output = self.root / "output"
        self.write_pair(base, f"{FIND_KEY}=old\n", f"{PERF_KEY}=old\n")
        empty.mkdir()

        with self.assertRaises(DatabaseError):
            merge_pairs(base, [empty], output, IDENTITY)

        report = merge_pairs(base, [empty], output, IDENTITY, allow_no_tuned_files=True)

        self.assertEqual(read_records(IDENTITY.path(output, "ufdb.txt")).records, {FIND_KEY: "old"})
        self.assertEqual(report["ufdb.txt"], {"added": [], "removed": [], "replaced": []})

    def test_merge_failure_leaves_existing_output_unchanged(self):
        output = self.root / "output"
        tuned = self.root / "tuned"
        self.write_pair(output, f"{FIND_KEY}=existing\n", f"{PERF_KEY}=existing\n")
        self.write_pair(tuned, "malformed\n", f"{PERF_KEY}=new\n")

        with self.assertRaises(DatabaseError):
            merge_pairs(output, [tuned], output, IDENTITY)

        self.assertEqual(IDENTITY.path(output, "ufdb.txt").read_text(), f"{FIND_KEY}=existing\n")


if __name__ == "__main__":
    unittest.main()
