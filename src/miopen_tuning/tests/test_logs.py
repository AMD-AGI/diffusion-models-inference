# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import sys
import tempfile
import unittest
from pathlib import Path

from miopen_tuning.logs import append_index, run_logged


class LogsTest(unittest.TestCase):
    def test_streams_readable_logs_and_writes_metadata(self):
        script = (
            "import sys; "
            "print('MIOpen Forward Conv. Algorithm: 5, Solution: 1/SolverA'); "
            "print('second line'); "
            "print('MIOpen Error: example', file=sys.stderr)"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_logged(
                [sys.executable, "-c", script],
                command_id="probe",
                log_dir=root / "logs" / "probe",
                environment={
                    "HIP_VISIBLE_DEVICES": "3",
                    "MIOPEN_CONTRACT_TESTS": "1",
                    "MIOPEN_USER_DB_PATH": str(root / "db"),
                },
                log_profile="standard",
                timeout=10,
                phase="tune",
            )
            append_index(root / "logs" / "index.jsonl", result, root)

            self.assertEqual((result.log_dir / "stdout.log").read_text(), "MIOpen Forward Conv. Algorithm: 5, Solution: 1/SolverA\nsecond line\n")
            metadata = json.loads((result.log_dir / "metadata.json").read_text())
            self.assertEqual(metadata["exit_code"], 0)
            self.assertEqual(metadata["environment"]["MIOPEN_LOG_LEVEL"], "5")
            self.assertNotIn("MIOPEN_CONTRACT_TESTS", metadata["environment"])
            self.assertEqual(metadata["gpu_id"], "3")
            self.assertEqual(metadata["summary"]["selected_solver"], "SolverA")
            self.assertTrue(all(entry["sha256"] for entry in metadata["files"]))
            events = [json.loads(line) for line in (result.log_dir / "events.jsonl").read_text().splitlines()]
            self.assertIn("solution", [event["event"] for event in events])
            index = json.loads((root / "logs" / "index.jsonl").read_text())
            self.assertEqual(index["log_dir"], "logs/probe")

    def test_unset_environment_removes_inherited_variables(self):
        script = "import os; print(os.environ.get('MIOPEN_FIND_MODE', 'unset'))"
        with tempfile.TemporaryDirectory() as directory:
            result = run_logged(
                [sys.executable, "-c", script],
                command_id="probe",
                log_dir=Path(directory),
                environment={},
                log_profile="off",
                timeout=10,
                unset_environment=("MIOPEN_FIND_MODE",),
            )
            self.assertEqual((result.log_dir / "stdout.log").read_text(), "unset\n")


if __name__ == "__main__":
    unittest.main()
