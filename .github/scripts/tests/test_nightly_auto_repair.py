import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

path = Path(__file__).parents[1] / "nightly-auto-repair.py"
spec = importlib.util.spec_from_file_location("nightly_auto_repair", path)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)
report = repair.report

REPO = "o/r"


def job(name, conclusion="success", failed_step=None):
    steps = [{"name": failed_step, "conclusion": "failure"}] if failed_step else []
    return {"id": 1, "name": name, "conclusion": conclusion, "steps": steps}


def workflow_run(run_id, conclusion, workflow="nightly-rocm.yml", started="2026-03-29T00:00:00Z", sha="bbb"):
    return {
        "id": run_id,
        "run_number": run_id,
        "html_url": f"https://github.com/{REPO}/actions/runs/{run_id}",
        "path": f".github/workflows/{workflow}",
        "head_branch": "main",
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "run_started_at": started,
    }


class TestInputsTests(unittest.TestCase):
    def test_quick_test_unless_tuning_or_final_image_failed(self):
        build = [job("Nightly / Build core and untuned images / Build images", "failure", "Build core image")]
        tuning = [job("Nightly / gfx950 tuning and benchmarking", "failure", "Run MIOpen tuning")]
        final = [job("Nightly / Build final tuned image", "failure", "Build image")]
        self.assertEqual(
            repair.test_inputs(5, "20260328-1", build),
            {"rocm_release_id": "20260328-1", "repair_issue": "5", "run_miopen_tuning": False, "build_final": False},
        )
        self.assertEqual(
            (
                repair.test_inputs(5, None, tuning)["run_miopen_tuning"],
                repair.test_inputs(5, None, tuning)["build_final"],
            ),
            (True, False),
        )
        self.assertEqual(
            (
                repair.test_inputs(5, None, final)["run_miopen_tuning"],
                repair.test_inputs(5, None, final)["build_final"],
            ),
            (True, True),
        )


class CollectTests(unittest.TestCase):
    def collect(self, issue_number="5", branch_exists=False, labels=("nightly-failure",)):
        issue = {
            "number": 5,
            "title": "ROCm nightly: build fails",
            "html_url": f"https://github.com/{REPO}/issues/5",
            "state": "open",
            "labels": [{"name": name} for name in labels],
            "body": f"Failed in https://github.com/{REPO}/actions/runs/100",
        }
        comments = [
            {"user": {"login": "bot"}, "body": f"Again: https://github.com/{REPO}/actions/runs/300"},
            {"user": {"login": "bot"}, "body": f"Unrelated build https://github.com/{REPO}/actions/runs/400"},
        ]
        responses = {
            f"repos/{REPO}/issues/5": issue,
            f"repos/{REPO}/issues/5/comments?per_page=100": comments,
            f"repos/{REPO}/actions/runs/400": workflow_run(400, "failure", "build-and-benchmark.yml"),
            f"repos/{REPO}/actions/runs/300": workflow_run(300, "failure", started="2026-03-29T00:00:00Z", sha="ccc"),
            f"repos/{REPO}/actions/runs/100": workflow_run(100, "failure", started="2026-03-20T00:00:00Z"),
            f"repos/{REPO}/actions/workflows/nightly-rocm.yml/runs?per_page=20&status=success&branch=main": {
                "workflow_runs": [workflow_run(250, "success", started="2026-03-28T00:00:00Z", sha="aaa")]
            },
            f"repos/{REPO}/compare/aaa...ccc": {
                "total_commits": 1,
                "commits": [{"sha": "c" * 40, "commit": {"message": "Bump xDiT\n\nbody"}}],
                "files": [{"filename": "docker/Dockerfile"}],
            },
            f"repos/{REPO}/pulls?state=all&per_page=1&head=o%3Aauto-repair/issue-5": [],
        }

        def fake_gh(*args):
            if branch_exists:
                return ""
            raise subprocess.CalledProcessError(1, args)

        with (
            patch.object(report, "api", side_effect=lambda endpoint: responses[endpoint]),
            patch.object(report, "gh", side_effect=fake_gh),
            patch.object(report, "jobs_for", return_value=[job("Build core and untuned images", "failure", "Build")]),
            patch.object(report, "log_for", return_value=("error: boom", None)),
            patch.object(report, "dependency_version", return_value="20260328-1"),
        ):
            return repair.collect(REPO, issue_number)

    def test_uses_newest_failed_nightly_and_prepares_dispatch(self):
        context = self.collect()
        self.assertEqual(context["failure"]["number"], 300)
        self.assertEqual(context["failure"]["last_success"]["number"], 250)
        self.assertEqual(context["changes_since_last_success"]["commits"], [{"sha": "c" * 12, "message": "Bump xDiT"}])
        self.assertEqual(context["changes_since_last_success"]["files"], ["docker/Dockerfile"])
        self.assertEqual(context["branch"], "auto-repair/issue-5")
        self.assertEqual(context["dispatch"]["ref"], "auto-repair/issue-5")
        self.assertEqual(context["dispatch"]["inputs"]["rocm_release_id"], "20260328-1")
        self.assertEqual(context["dispatch"]["inputs"]["repair_issue"], "5")

    def test_refuses_repeat_attempts_and_unlabelled_issues(self):
        with self.assertRaisesRegex(repair.RepairRefused, "already has a repair attempt"):
            self.collect(branch_exists=True)
        with self.assertRaisesRegex(repair.RepairRefused, "not an open issue labelled"):
            self.collect(labels=("bug",))


if __name__ == "__main__":
    unittest.main()
