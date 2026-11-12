# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import tempfile
import unittest
from pathlib import Path

from miopen_tuning.cli import main
from miopen_tuning.databases import DatabaseIdentity
from miopen_tuning.tests.test_databases import COMMAND, FIND_KEY, PERF_KEY


class CliTest(unittest.TestCase):
    def test_plan_writes_json_and_commands(self):
        identity = DatabaseIdentity("gfx950100", "3.6.3", "37f616f6")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workloads = root / "workloads.txt"
            database = root / "db"
            output = root / "plan"
            database.mkdir()
            workloads.write_text(COMMAND + "\n")
            identity.path(database, "ufdb.txt").write_text(f"{FIND_KEY}=opaque\n")

            exit_code = main([
                "plan",
                "--workloads",
                str(workloads),
                "--db-dir",
                str(database),
                "--output-dir",
                str(output),
                "--db-prefix",
                identity.db_prefix,
                "--miopen-version",
                identity.miopen_version,
                "--miopen-build",
                identity.miopen_build,
            ])

            self.assertEqual(exit_code, 0)
            self.assertEqual((output / "commands.txt").read_text(), COMMAND + "\n")
            plan = json.loads((output / "plan.json").read_text())
            self.assertEqual(plan["counts"]["tune"], 1)
            self.assertEqual(plan["entries"][0]["perf_db_key"], PERF_KEY)


if __name__ == "__main__":
    unittest.main()
