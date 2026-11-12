# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from miopen_tuning.databases import DatabaseIdentity, read_records
from miopen_tuning.runner import RunOptions, run_tuning


IDENTITY = DatabaseIdentity("gfx950100", "3.6.3", "37f616f6")
GOOD = "MIOpenDriver convbfp16 -n 1 -c 16 -H 16 -W 16 -k 16 -y 3 -x 3 -p 1 -q 1 -u 1 -v 1 -l 1 -j 1 -m conv -g 1 -F 1 -t 1"
OTHER = "MIOpenDriver convfp16 -n 1 -c 32 -H 17 -W 19 -k 32 -y 3 -x 3 -p 1 -q 1 -u 2 -v 2 -l 1 -j 1 -m conv -g 2 -F 1 -t 1"
FAIL = "MIOpenDriver convbfp16 -n 99 -c 16 -H 16 -W 16 -k 16 -y 3 -x 3 -p 1 -q 1 -u 1 -v 1 -l 1 -j 1 -m conv -g 1 -F 1 -t 1"


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.driver = self.root / "MIOpenDriver"
        source = Path(__file__).resolve().parents[2]
        self.driver.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import json
                import os
                import sys
                from pathlib import Path
                sys.path.insert(0, {str(source)!r})
                from miopen_tuning.problem import MIOpenConvolution

                if sys.argv[1:] == ["--version"]:
                    print("MIOpen (version: 3.6.3)")
                    raise SystemExit(0)
                command = "MIOpenDriver " + " ".join(sys.argv[1:])
                print("MIOpen: Info [Do] 8: warning: argument unused during compilation: '-dD' [-Wunused-command-line-argument]", file=sys.stderr)
                if " -n 99 " in command:
                    print("MIOpen Error: synthetic failure", file=sys.stderr)
                    raise SystemExit(7)
                problem = MIOpenConvolution.from_miopendriver_command(command)
                find_key = problem.to_find_db_key()
                perf_key = problem.to_perf_db_key()
                directory = Path(os.environ["MIOPEN_USER_DB_PATH"])
                directory.mkdir(parents=True, exist_ok=True)
                stem = "{IDENTITY.stem}"
                with (directory / f"{{stem}}.ufdb.txt").open("a") as handle:
                    handle.write(f"{{find_key}}=SolverA:0.1,0,miopenConvolutionFwdAlgoGEMM\\n")
                with (directory / f"{{stem}}.udb.txt").open("a") as handle:
                    handle.write(f"{{perf_key}}=SolverA:config\\n")
                print(json.dumps({{"performance": {{"direction": "forward", "solution": "1/SolverA", "db_key": find_key, "perf_db_key": perf_key, "results": {{"average_time_ms": 0.1}}}}}}))
                """
            )
        )
        self.driver.chmod(0o755)
        self.identity = self.root / "identity.json"
        self.identity.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "captured_at": "2026-11-12T00:00:00Z",
                    "image": "test-image",
                    "image_digest": "",
                    "rocm_release": "test",
                    "miopen_version": IDENTITY.miopen_version,
                    "miopen_build": IDENTITY.miopen_build,
                    "driver_path": str(self.driver),
                    "driver_version_output": "MIOpen (version: 3.6.3)",
                    "device_names": ["AMD Instinct MI350X"],
                    "db_prefix": IDENTITY.db_prefix,
                }
            )
        )
        self.db_dir = self.root / "userdb"
        self.db_dir.mkdir()

    def options(self, workloads, **overrides):
        values = {
            "output_dir": self.root / "run",
            "workloads": workloads,
            "db_dir": self.db_dir,
            "prefix_map": self.root / "prefixes.txt",
            "identity_path": self.identity,
            "devices": "0,1",
            "install_db": True,
            "run_contracts": False,
            "timeout": 30,
        }
        values.update(overrides)
        return RunOptions(**values)

    def test_adjacent_duplicate_commands_are_tuned_once(self):
        first = self.root / "a.txt"
        second = self.root / "b.txt"
        first.write_text(f"{GOOD}\n")
        second.write_text(f"{GOOD}\n{OTHER}\n")

        exit_code, report = run_tuning(self.options([first, second]))

        self.assertEqual(exit_code, 0, report["errors"])
        self.assertEqual(report["plan"]["counts"]["tune"], 2)
        good = next(entry for entry in report["plan"]["entries"] if entry["command"] == GOOD)
        self.assertEqual([source["path"] for source in good["sources"]], [str(first), str(second)])
        self.assertEqual(report["tuning"]["succeeded"], 2)
        self.assertEqual(len(read_records(IDENTITY.path(self.db_dir, "ufdb.txt")).records), 2)
        log = self.root / "run" / report["tuning"]["results"][0]["log_dir"] / "stderr.log"
        self.assertIn("\n", log.read_text())
        self.assertTrue((self.root / "run" / "summary.md").is_file())
        self.assertTrue((self.root / "run" / "report.md").is_file())

    def test_failures_are_reported_after_all_commands_finish(self):
        workloads = self.root / "workloads.txt"
        workloads.write_text(f"{FAIL}\n{GOOD}\n")

        exit_code, report = run_tuning(self.options([workloads]))

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["tuning"]["succeeded"], 1)
        self.assertEqual(report["tuning"]["failed"], 1)
        self.assertFalse(IDENTITY.path(self.db_dir, "ufdb.txt").exists())
        self.assertIn("1 MIOpen tuning commands failed", report["errors"])

    def test_skips_problem_when_exact_pair_has_both_records(self):
        workloads = self.root / "workloads.txt"
        workloads.write_text(f"{GOOD}\n")
        exit_code, _report = run_tuning(self.options([workloads]))
        self.assertEqual(exit_code, 0)

        exit_code, report = run_tuning(self.options([workloads], output_dir=self.root / "second-run"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["plan"]["counts"], {"tune": 0, "skip": 1, "reject": 0})


if __name__ == "__main__":
    unittest.main()
