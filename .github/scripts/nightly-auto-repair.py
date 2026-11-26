#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Collect the context for an automatic repair attempt on a nightly-failure issue or the latest failed nightly."""

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
RUN_BRANCH_PREFIX = "auto-repair/run-"
# Nightly workflows with repair_issue/rocm_release_id dispatch inputs; nightly-auto-repair.md dispatches these.
REPAIRABLE = {"nightly-rocm.yml"}
MAX_COMMITS = 50
MAX_FILES = 200
# Steps that the quick repair test skips unless they are where the nightly failed.
TUNING_STEP = "Run MIOpen tuning"
FINAL_JOB = "Build final tuned image"
# The log tail of a failed image build is mostly fallout, so the context also carries the first error with surroundings.
LOG_PREFIX = re.compile(r"^[^\t]*\t[^\t]*\t\S+Z ")
BUILDKIT_FAILURE = re.compile(r"^(#\d+) ERROR: ")
DIAGNOSTIC = re.compile(r"(?:^|[\s:])(?:fatal )?error:|^(?:#\d+ [\d.]+ )?(?:ERROR|Error):? |Traceback ")
SHELL_ECHO = "\x1b[36;1m"
LINES_BEFORE_ERROR = 30
LINES_AFTER_ERROR = 120


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
        "repair_issue": str(issue_number or ""),
        # The final image consumes the tuning results, so testing it requires tuning as well.
        "run_miopen_tuning": tuning_failed or final_failed,
        "build_final": final_failed,
    }


def first_error_excerpt(log):
    lines = [LOG_PREFIX.sub("", line) for line in log.splitlines() if SHELL_ECHO not in line]
    step = next((match.group(1) for line in lines if (match := BUILDKIT_FAILURE.match(line))), None)
    if step:
        lines = [line for line in lines if line.startswith(f"{step} ")]
    first = next((index for index, line in enumerate(lines) if DIAGNOSTIC.search(line)), None)
    if first is None:
        return ""
    return "\n".join(lines[max(0, first - LINES_BEFORE_ERROR) : first + LINES_AFTER_ERROR])


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


def issue_target(repo, requested_issue):
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
    summary = {
        "number": number,
        "title": issue["title"],
        "url": issue["html_url"],
        "body": issue["body"] or "",
        "comments": [{"author": c["user"]["login"], "body": c["body"]} for c in comments[-10:]],
    }
    return summary, run, nightly, branch


def latest_run_target(repo):
    nightly = next(nightly for nightly in report.NIGHTLIES if nightly["workflow"] in REPAIRABLE)
    default_branch = report.api(f"repos/{repo}")["default_branch"]
    endpoint = (
        f"repos/{repo}/actions/workflows/{nightly['workflow']}/runs"
        f"?per_page=1&status=completed&branch={quote(default_branch, safe='')}"
    )
    runs = report.api(endpoint)["workflow_runs"]
    if not runs:
        raise RepairRefused(f"{nightly['name']} has no completed run on {default_branch}.")
    run = runs[0]
    if run["conclusion"] != "failure":
        raise RepairRefused(f"The latest {nightly['name']} run ({run['html_url']}) concluded {run['conclusion']}.")
    branch = f"{RUN_BRANCH_PREFIX}{run['id']}"
    if branch_taken(repo, branch):
        raise RepairRefused(f"Run {run['id']} already has a repair attempt on {branch}; delete it to retry.")
    return None, run, nightly, branch


def collect(repo, requested_issue, latest_nightly=False):
    if latest_nightly:
        issue, run, nightly, branch = latest_run_target(repo)
    else:
        issue, run, nightly, branch = issue_target(repo, requested_issue)
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
    log, _ = report.log_for(repo, run["id"], failed=True)
    failure["first_error_excerpt"] = first_error_excerpt(log)
    return {
        "issue": issue,
        "failure": failure,
        "changes_since_last_success": repository_changes(repo, previous and previous["head_sha"], run["head_sha"]),
        "branch": branch,
        "dispatch": {
            "ref": branch,
            "inputs": test_inputs(issue and issue["number"], version, jobs),
        },
    }


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    output = Path(os.environ.get("REPAIR_CONTEXT_DIR", "/tmp/gh-aw/agent"))
    output.mkdir(parents=True, exist_ok=True)
    try:
        context = collect(
            repo,
            os.environ.get("ISSUE_NUMBER", "").strip(),
            latest_nightly=os.environ.get("REPAIR_LATEST_NIGHTLY") == "true",
        )
    except RepairRefused as refusal:
        print(f"::error::Repair not attempted: {refusal}", file=sys.stderr)
        sys.exit(1)
    (output / "repair-context.json").write_text(json.dumps(context, indent=2))
    target = f"#{context['issue']['number']}" if context["issue"] else context["failure"]["url"]
    print(f"Repair context for {target} written; fix branch {context['branch']}.")


if __name__ == "__main__":
    main()
