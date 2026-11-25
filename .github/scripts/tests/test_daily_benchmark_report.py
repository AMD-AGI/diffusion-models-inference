import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


path = Path(__file__).parents[1] / "daily-benchmark-report.py"
spec = importlib.util.spec_from_file_location("daily_benchmark_report", path)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class DailyBenchmarkReportTests(unittest.TestCase):
    def test_helsinki_day_window_ends_now_and_handles_dst(self):
        cases = [
            ("2026-11-24T22:30:00+00:00", "2026-11-25", "2026-11-24T22:00:00+00:00"),
            ("2026-03-29T20:30:00+00:00", "2026-03-29", "2026-03-28T22:00:00+00:00"),
            ("2026-10-25T21:30:00+00:00", "2026-10-25", "2026-10-24T21:00:00+00:00"),
        ]
        for current, expected_day, expected_start in cases:
            with self.subTest(current=current):
                now = datetime.fromisoformat(current)
                day, start, end = report.day_window(now)
                self.assertEqual(str(day), expected_day)
                self.assertEqual(start, datetime.fromisoformat(expected_start))
                self.assertEqual(end, now)

    def test_release_branch_is_numeric_not_lexicographic(self):
        branches = [{"name": "release/v26.9"}, {"name": "release/v26.11"}, {"name": "release/v26.11-rc1"}]
        self.assertEqual(report.release_branch(branches), "release/v26.11")

    def test_parse_matrix_and_reusable_job_logs_and_duplicates(self):
        log = (
            "gfx950 tuning and benchmarking\tstep\tMedian latency for foo: 2.0 seconds\n"
            "caller / gfx942 tuning and benchmarking\tstep\tMedian latency for foo: 3.0 seconds\n"
            "gfx950 tuning and benchmarking\tstep\tMedian latency for foo: 4.0 seconds\n"
        )
        values, ambiguous = report.parse_latencies(log)
        self.assertEqual(values, {("gfx942", "foo"): 3.0})
        self.assertEqual(ambiguous, {("gfx950", "foo")})

    def test_comparison_identifies_unmatched_and_invalid_baselines(self):
        current = {("gfx950", "a"): 12.0, ("gfx950", "new"): 1.0, ("gfx950", "zero"): 1.0}
        baseline = {("gfx950", "a"): 10.0, ("gfx950", "old"): 2.0, ("gfx950", "zero"): 0.0}
        text = report.comparison(current, baseline, set(), set())
        self.assertIn("+20.00%", text)
        self.assertIn("Missing in run", text)
        self.assertIn("Missing in release", text)
        self.assertIn("Invalid release latency", text)

    def test_report_selects_completed_runs_and_triages_failures(self):
        def run(number, conclusion, branch="main"):
            return {
                "id": number,
                "run_number": number,
                "html_url": f"https://example.com/{number}",
                "head_branch": branch,
                "status": "completed",
                "conclusion": conclusion,
                "created_at": "2026-03-29T02:00:00Z",
                "updated_at": "2026-03-29T05:00:00Z",
                "run_started_at": "2026-03-29T02:00:00Z",
            }

        baseline = run(1, "success", "release/v26.11")
        success, failed, cancelled = run(2, "success"), run(3, "failure"), run(4, "cancelled")
        previous_day, after_cutoff = run(5, "success"), run(6, "success")
        in_progress = run(7, None)
        in_progress["status"] = "in_progress"
        jobs = [
            {
                "name": "build",
                "conclusion": "failure",
                "completed_at": "2026-03-29T04:00:00Z",
                "steps": [{"name": "compile", "conclusion": "failure"}],
            }
        ]
        log = "gfx950 tuning and benchmarking\tstep\tMedian latency for foo: 2.0 seconds"
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary"
            env = {
                "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_STEP_SUMMARY": str(summary),
                "BENCHMARK_REPORT_DIR": directory,
            }
            with (
                patch.dict(report.os.environ, env),
                patch.object(
                    report,
                    "day_window",
                    return_value=(
                        "2026-03-29",
                        datetime(2026, 3, 28, 22, tzinfo=timezone.utc),
                        datetime(2026, 3, 29, 4, 30, tzinfo=timezone.utc),
                    ),
                ),
                patch.object(report, "api_pages", return_value=[[{"name": "release/v26.11"}]]),
                patch.object(
                    report,
                    "get_runs",
                    side_effect=[[baseline, success, failed, cancelled, previous_day, after_cutoff, in_progress], []],
                ),
                patch.object(
                    report,
                    "jobs_for",
                    side_effect=lambda repo, run_id: (
                        [{"completed_at": "2026-03-28T21:59:59Z"}]
                        if run_id == 5
                        else [{"completed_at": "2026-03-29T04:30:00Z"}]
                        if run_id == 6
                        else jobs
                    ),
                ),
                patch.object(
                    report,
                    "log_for",
                    side_effect=lambda *args, **kwargs: ("compiler failed" if kwargs.get("failed") else log, None),
                ),
            ):
                report.main()
            text = summary.read_text()
            failures = json.loads((Path(directory) / "benchmark-failures.json").read_text())
        self.assertIn("Run #2", text)
        self.assertIn("+0.00%", text)
        self.assertNotIn("Run #4", text)
        self.assertNotIn("Run #5", text)
        self.assertNotIn("Run #6", text)
        self.assertNotIn("Run #7", text)
        self.assertEqual([entry["number"] for entry in failures], [3])
        self.assertEqual(failures[0]["jobs"][0]["steps"], ["compile"])
        self.assertEqual(failures[0]["log_excerpt"], "compiler failed")


if __name__ == "__main__":
    unittest.main()
