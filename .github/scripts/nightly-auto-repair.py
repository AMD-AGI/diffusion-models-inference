#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Collect the context for an automatic repair attempt on a nightly-failure issue."""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

spec = importlib.util.spec_from_file_location(
    "daily_benchmark_report", Path(__file__).with_name("daily-benchmark-report.py")
)
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

BRANCH_PREFIX = "auto-repair/issue-"
# Nightly workflows with repair_issue/rocm_release_id dispatch inputs; nightly-auto-repair.md dispatches these.
REPAIRABLE = {"nightly-rocm.yml"}
MAX_COMMITS = 50
MAX_FILES = 200
# Steps that the quick repair test skips unless they are where the nightly failed.
TUNING_STEP = "Run MIOpen tuning"
FINAL_JOB = "Build final tuned image"


class RepairRefused(Exception):
    pass


def issue_runs(repo, texts):
    pattern = re.compile(rf"https://github\.com/{re.escape(repo)}/actions/runs/(\d+)")
    return sorted({int(run_id) for text in texts for run_id in pattern.findall(text or "")}, reverse=True)


def nightly_for(run):
    return next(
        (nightly for nightly in report.NIGHTLIES if run.get("path", "").endswith(f"/{nightly['workflow']}")), None
    )


def failed_steps(jobs):
    return {
        (job["name"], step["name"]) for job in jobs for step in job.get("steps", []) if step["conclusion"] == "failure"
    }


def test_inputs(issue_number, version, jobs):
    steps = failed_steps(jobs)
    tuning_failed = any(step == TUNING_STEP for _, step in steps)
    # Reusable-workflow jobs are named "<caller job> / <job>", sometimes with a further " / <matrix job>".
    final_failed = any(
        FINAL_JOB in job["name"].split(" / ") and job["conclusion"] in ("failure", "timed_out") for job in jobs
    )
    return {
        "rocm_release_id": version or "",
        "repair_issue": str(issue_number),
        # The final image consumes the tuning results, so testing it requires tuning as well.
        "run_miopen_tuning": tuning_failed or final_failed,
        "build_final": final_failed,
    }


def branch_taken(repo, branch):
    try:
        report.gh("api", f"repos/{repo}/branches/{quote(branch, safe='')}", "--silent")
        return True
    except subprocess.CalledProcessError:
        pass
    owner = repo.split("/")[0]
    pulls = report.api(f"repos/{repo}/pulls?state=all&per_page=1&head={quote(f'{owner}:{branch}')}")
    return bool(pulls)


def pick_issue(repo, requested):
    if requested:
        return report.api(f"repos/{repo}/issues/{int(requested)}")
    endpoint = f"repos/{repo}/issues?state=open&per_page=50&labels={quote(report.ISSUE_LABEL)}"
    for issue in report.api(endpoint):
        if "pull_request" not in issue and not branch_taken(repo, f"{BRANCH_PREFIX}{issue['number']}"):
            return issue
    raise RepairRefused(f"No open {report.ISSUE_LABEL} issue without an earlier repair attempt.")


def last_success(repo, nightly, run):
    endpoint = (
        f"repos/{repo}/actions/workflows/{nightly['workflow']}/runs"
        f"?per_page=20&status=success&branch={quote(run['head_branch'], safe='')}"
    )
    candidates = [
        candidate
        for candidate in report.api(endpoint)["workflow_runs"]
        if report.timestamp(candidate["run_started_at"]) < report.timestamp(run["run_started_at"])
    ]
    if not candidates:
        return None
    previous = max(candidates, key=lambda candidate: report.timestamp(candidate["run_started_at"]))
    return {
        "number": previous["run_number"],
        "url": previous["html_url"],
        "head_sha": previous["head_sha"],
        "version": report.dependency_version(repo, report.jobs_for(repo, previous["id"]), nightly),
    }


def repository_changes(repo, base, head):
    if not base or base == head:
        return {"commits": [], "files": [], "note": "The failing run used the same commit as the last success."}
    compare = report.api(f"repos/{repo}/compare/{base}...{head}")
    return {
        "commits": [
            {"sha": commit["sha"][:12], "message": commit["commit"]["message"].splitlines()[0]}
            for commit in compare["commits"][-MAX_COMMITS:]
        ],
        "files": [changed["filename"] for changed in compare.get("files", [])[:MAX_FILES]],
        "note": f"{compare['total_commits']} commit(s) between the last success and the failing run.",
    }


def collect(repo, requested_issue):
    issue = pick_issue(repo, requested_issue)
    number = issue["number"]
    labels = {label["name"] for label in issue["labels"]}
    if "pull_request" in issue or issue["state"] != "open" or report.ISSUE_LABEL not in labels:
        raise RepairRefused(f"#{number} is not an open issue labelled {report.ISSUE_LABEL}.")
    branch = f"{BRANCH_PREFIX}{number}"
    if branch_taken(repo, branch):
        raise RepairRefused(f"#{number} already has a repair attempt on {branch}; delete it to retry.")

    comments = report.api(f"repos/{repo}/issues/{number}/comments?per_page=100")
    texts = [issue["body"]] + [comment["body"] for comment in comments]
    for run_id in issue_runs(repo, texts):
        run = report.api(f"repos/{repo}/actions/runs/{run_id}")
        nightly = nightly_for(run)
        if nightly and run["status"] == "completed" and run["conclusion"] == "failure":
            break
    else:
        raise RepairRefused(f"#{number} links no failed nightly run.")
    if nightly["workflow"] not in REPAIRABLE:
        raise RepairRefused(f"{nightly['name']} has no repair test mode yet.")

    jobs = report.jobs_for(repo, run["id"])
    current, error = report.benchmarks_for(repo, run["id"])
    version = report.dependency_version(repo, jobs, nightly)
    previous = last_success(repo, nightly, run)
    failure = report.failure_entry(
        repo,
        run,
        jobs,
        "nightly",
        nightly["name"],
        nightly["framework"],
        current or report.Benchmarks(),
        {"dependency": {"label": nightly["version_label"], "version": version}, "last_success": previous},
    )
    if error:
        failure["benchmark_log_error"] = error
    return {
        "issue": {
            "number": number,
            "title": issue["title"],
            "url": issue["html_url"],
            "body": issue["body"] or "",
            "comments": [{"author": c["user"]["login"], "body": c["body"]} for c in comments[-10:]],
        },
        "failure": failure,
        "changes_since_last_success": repository_changes(repo, previous and previous["head_sha"], run["head_sha"]),
        "branch": branch,
        "dispatch": {
            "ref": branch,
            "inputs": test_inputs(number, version, jobs),
        },
    }


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    output = Path(os.environ.get("REPAIR_CONTEXT_DIR", "/tmp/gh-aw/agent"))
    output.mkdir(parents=True, exist_ok=True)
    try:
        context = collect(repo, os.environ.get("ISSUE_NUMBER", "").strip())
    except RepairRefused as refusal:
        print(f"::error::Repair not attempted: {refusal}", file=sys.stderr)
        sys.exit(1)
    (output / "repair-context.json").write_text(json.dumps(context, indent=2))
    print(f"Repair context for #{context['issue']['number']} written; fix branch {context['branch']}.")


if __name__ == "__main__":
    main()
