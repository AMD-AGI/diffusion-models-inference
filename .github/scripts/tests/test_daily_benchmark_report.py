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


def log_lines(job, *lines):
    return "".join(f"{job}\tstep\t{line}\n" for line in lines)


def run(number, conclusion, branch="feature/x", title="Build and Benchmark xdit", started="2026-03-29T02:00:00Z"):
    return {
        "id": number,
        "run_number": number,
        "html_url": f"https://example.com/{number}",
        "head_branch": branch,
        "display_title": title,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "created_at": started,
        "updated_at": "2026-03-29T05:00:00Z",
        "run_started_at": started,
    }


class DailyBenchmarkReportTests(unittest.TestCase):
    def setUp(self):
        report.benchmarks_for.cache_clear()
        report.successful_runs.cache_clear()

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

    def test_release_branches_sort_numerically(self):
        branches = [{"name": "release/v26.9"}, {"name": "release/v26.11"}, {"name": "release/v26.11-rc1"}]
        self.assertEqual(report.release_branches(branches), ["release/v26.11", "release/v26.9"])

    def test_parse_benchmarks_tracks_planned_failed_and_duplicate_results(self):
        log = log_lines(
            "caller / gfx950 tuning and benchmarking",
            "##[group]Experiment 1/3: a (8 GPU(s))",
            "Median latency for a: 2.0 seconds",
            "##[group]Experiment 2/3: b (8 GPU(s))",
            "ERROR: Experiment b failed to complete. Reason: boom",
            "##[error]Experiment b failed with exit code 1.",
            "##[group]Experiment 3/3: c (8 GPU(s))",
            "Median latency for c: 1.0 seconds",
            "Median latency for c: 3.0 seconds",
        ) + log_lines("Build images", "Median latency for ignored: 1.0 seconds")
        data = report.parse_benchmarks(log)
        self.assertEqual(data.latencies, {("gfx950", "a"): 2.0})
        self.assertEqual(data.failed, {("gfx950", "b")})
        self.assertEqual(data.ambiguous, {("gfx950", "c")})
        self.assertEqual(data.keys(), {("gfx950", "a"), ("gfx950", "b"), ("gfx950", "c")})

    def test_table_marks_failures_missing_results_and_five_percent_highlights(self):
        current = report.Benchmarks(
            latencies={("g", "win"): 9.5, ("g", "loss"): 10.5, ("g", "flat"): 10.499},
            failed={("g", "broken")},
        )
        baseline = report.Baseline(
            "release/v1.0",
            {},
            report.Benchmarks(latencies={("g", k): 10.0 for k in ("win", "loss", "flat", "broken", "dropped")}),
        )
        text = report.benchmark_table(current, [baseline], current.keys() | baseline.data.keys())
        rows = {line.split(" | ")[1]: line for line in text.splitlines()[2:]}
        self.assertIn("**-5.00% 🏆**", rows["win"])
        self.assertIn("**+5.00% ⚠️**", rows["loss"])
        self.assertIn("| +4.99% |", rows["flat"])
        self.assertIn("**FAILED** | 10.0000 | — |", rows["broken"])
        self.assertIn("| N/A | 10.0000 | — |", rows["dropped"])
        lines = report.highlights(current, [baseline])
        self.assertEqual(len(lines), 2)
        self.assertIn("win on g (-5.00%)", lines[0])
        self.assertIn("loss on g (+5.00%)", lines[1])

    def test_baseline_skips_other_frameworks_and_runs_without_benchmarks(self):
        runs = [
            run(3, "success", "release/v1.0", "Build and Benchmark sgld", "2026-03-03T00:00:00Z"),
            run(2, "success", "release/v1.0", started="2026-03-02T00:00:00Z"),
            run(1, "success", "release/v1.0", "Build and Benchmark xDiT", "2026-03-01T00:00:00Z"),
        ]
        logs = {2: "", 1: log_lines("gfx950 tuning and benchmarking", "Median latency for a: 1.0 seconds")}
        with (
            patch.object(report, "successful_runs", return_value=runs),
            patch.object(report, "log_for", side_effect=lambda repo, run_id, failed=False: (logs[run_id], None)),
        ):
            baseline = report.benchmarked_baseline("o/r", "release/v1.0", "release/v1.0", "xdit")
        self.assertEqual(baseline.run["id"], 1)

    def test_report_groups_runs_counts_outcomes_and_limits_issue_creation(self):
        bench = log_lines("gfx950 tuning and benchmarking", "Median latency for a: 2.0 seconds")
        release_old = run(10, "success", "release/v1.0", started="2026-03-01T00:00:00Z")
        release_prev = run(11, "success", "release/v1.1", started="2026-03-02T00:00:00Z")
        build_runs = [
            run(20, "failure", "release/v1.1"),
            run(21, "success"),
            run(22, "failure"),
            run(23, "cancelled"),
            run(24, None),
        ]
        nightly_runs = [run(30, "failure", "main", "Nightly ROCm 0 0 * * *")]
        jobs = [
            {
                "id": 1,
                "name": "gfx950 tuning and benchmarking",
                "conclusion": "failure",
                "completed_at": "2026-03-29T04:00:00Z",
                "steps": [{"name": "Run benchmarks", "conclusion": "failure"}],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary"
            env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_STEP_SUMMARY": str(summary), "BENCHMARK_REPORT_DIR": directory}
            with (
                patch.dict(report.os.environ, env),
                patch.object(
                    report,
                    "day_window",
                    return_value=(
                        "2026-03-29",
                        datetime(2026, 3, 28, 22, tzinfo=timezone.utc),
                        datetime(2026, 3, 29, 6, tzinfo=timezone.utc),
                    ),
                ),
                patch.object(report, "api_pages", return_value=[[{"name": "release/v1.0"}, {"name": "release/v1.1"}]]),
                patch.object(
                    report,
                    "recent_runs",
                    side_effect=lambda repo, workflow, since: (
                        nightly_runs if workflow == "nightly-rocm.yml" else build_runs
                    ),
                ),
                patch.object(
                    report,
                    "successful_runs",
                    side_effect=lambda repo, branch: {"release/v1.0": [release_old], "release/v1.1": [release_prev]}[
                        branch
                    ],
                ),
                patch.object(report, "jobs_for", return_value=jobs),
                patch.object(
                    report,
                    "log_for",
                    side_effect=lambda repo, run_id, failed=False: ("boom" if failed else bench, None),
                ),
                patch.object(report, "dependency_version", return_value="20260328-1"),
                patch.object(report, "last_nightly_success", return_value={"number": 29, "version": "20260327-1"}),
                patch.object(report, "open_issues", return_value=[{"number": 5, "title": "known"}]),
            ):
                report.main()
            text = summary.read_text()
            failures = json.loads((Path(directory) / "benchmark-failures.json").read_text())

        self.assertIn("**5 runs: 1 succeeded, 3 failed, 1 cancelled.**", text)
        self.assertIn("1 run(s) still in progress are not included.", text)
        sections = text.split("\n## ")
        release, nightly, dev = sections[1], sections[2], sections[3]
        self.assertIn("run #20", release)
        self.assertIn("Baseline release/v1.0", release)
        self.assertIn("previous release/v1.1 run", release)
        self.assertIn("run #30", nightly)
        self.assertIn("`20260328-1`", nightly)
        self.assertIn("run #23", dev)
        self.assertIn("Cancelled; not analysed.", dev)
        self.assertNotIn("run #24", text)
        allowed = {entry["number"]: entry["create_issue_allowed"] for entry in failures}
        self.assertEqual(allowed, {20: False, 30: True, 22: False})
        nightly_entry = next(entry for entry in failures if entry["number"] == 30)
        self.assertEqual(nightly_entry["open_issues"], [{"number": 5, "title": "known"}])
        self.assertEqual(nightly_entry["last_success"]["version"], "20260327-1")


if __name__ == "__main__":
    unittest.main()
