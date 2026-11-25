#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import os
import re
import subprocess
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WORKFLOWS = (
    ("Build and Benchmark", "build-and-benchmark.yml"),
    ("Nightly ROCm", "nightly-rocm.yml"),
)
LATENCY = re.compile(r"Median latency for (.+?): ([0-9]+(?:\.[0-9]+)?) seconds")
ARCH = re.compile(r"(?:^| / )(\S+) tuning and benchmarking$")
RELEASE = re.compile(r"^release/v(\d+)\.(\d+)$")


def gh(*args):
    return subprocess.run(["gh", *args], check=True, text=True, stdout=subprocess.PIPE).stdout


def api_pages(endpoint):
    return json.loads(gh("api", "--paginate", "--slurp", endpoint))


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def day_window(now):
    zone = ZoneInfo("Europe/Helsinki")
    day = now.astimezone(zone).date()
    start = datetime.combine(day, time.min, zone)
    return day, start.astimezone(timezone.utc), now.astimezone(timezone.utc)


def release_branch(branches):
    versions = [
        (tuple(map(int, match.groups())), branch["name"])
        for branch in branches
        if (match := RELEASE.fullmatch(branch["name"]))
    ]
    return max(versions)[1] if versions else None


def get_runs(repo, workflow):
    endpoint = f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=100"
    return [run for page in api_pages(endpoint) for run in page["workflow_runs"]]


def jobs_for(repo, run_id):
    endpoint = f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    return [job for page in api_pages(endpoint) for job in page["jobs"]]


def run_finished_at(jobs):
    completed = [timestamp(job["completed_at"]) for job in jobs if job["completed_at"]]
    return max(completed) if completed else None


def parse_latencies(log):
    results = {}
    ambiguous = set()
    for line in log.splitlines():
        job = line.split("\t", 1)[0]
        arch = ARCH.search(job)
        latency = LATENCY.search(line)
        if not arch or not latency:
            continue
        key = (arch.group(1), latency.group(1))
        value = float(latency.group(2))
        if key in results and results[key] != value:
            ambiguous.add(key)
        results[key] = value
    for key in ambiguous:
        del results[key]
    return results, ambiguous


def log_for(repo, run_id, failed=False):
    try:
        return gh("run", "view", str(run_id), "--repo", repo, "--log-failed" if failed else "--log"), None
    except subprocess.CalledProcessError as error:
        return "", f"Log retrieval failed (gh exited {error.returncode})."


def md(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def comparison(current, baseline, current_ambiguous, baseline_ambiguous):
    lines = ["| GPU | Experiment | Current (s) | Release (s) | Change |", "| --- | --- | ---: | ---: | ---: |"]
    keys = sorted(set(current) | set(baseline) | current_ambiguous | baseline_ambiguous)
    for key in keys:
        new, old = current.get(key), baseline.get(key)
        if key in current_ambiguous or key in baseline_ambiguous:
            change = "Ambiguous duplicate"
        elif new is None:
            change = "Missing in run"
        elif old is None:
            change = "Missing in release"
        elif old == 0:
            change = "Invalid release latency"
        else:
            change = f"{(new - old) / old * 100:+.2f}%"
        lines.append(
            f"| {md(key[0])} | {md(key[1])} | "
            f"{f'{new:.4f}' if new is not None else 'N/A'} | "
            f"{f'{old:.4f}' if old is not None else 'N/A'} | {change} |"
        )
    return "\n".join(lines) if keys else "No benchmark latency measurements found."


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    day, start, end = day_window(datetime.now(timezone.utc))
    branches = [branch for page in api_pages(f"repos/{repo}/branches?per_page=100") for branch in page]
    branch = release_branch(branches)
    runs_by_workflow = {name: get_runs(repo, workflow) for name, workflow in WORKFLOWS}
    candidates = [
        run
        for run in runs_by_workflow["Build and Benchmark"]
        if run["head_branch"] == branch and run["status"] == "completed" and run["conclusion"] == "success"
    ]
    baseline_run = max(candidates, key=lambda run: timestamp(run["run_started_at"]), default=None) if branch else None
    baseline, baseline_ambiguous = {}, set()
    baseline_error = None
    if baseline_run:
        baseline_log, baseline_error = log_for(repo, baseline_run["id"])
        if not baseline_error:
            baseline, baseline_ambiguous = parse_latencies(baseline_log)
    else:
        baseline_error = "No successful run found on the newest release branch."

    report = [
        f"# Daily build and benchmark report - {day} (Europe/Helsinki)",
        f"Completed from {start.isoformat()} through {end.isoformat()} (exclusive).",
        "Cancelled and incomplete runs are excluded. Negative latency change is faster.",
        f"Release baseline: {md(branch or 'no release branch found')}"
        + (f" [run #{baseline_run['run_number']}]({baseline_run['html_url']})" if baseline_run else ""),
    ]
    if baseline_error:
        report.append(f"**Baseline unavailable:** {baseline_error}")
    elif not baseline:
        report.append("**Baseline unavailable:** no unambiguous benchmark latencies found.")

    failures = []
    for section, _ in WORKFLOWS:
        report.append(f"\n## {section}")
        if section == "Nightly ROCm":
            report.append("Nightly failures are expected and do not by themselves imply a regression.")
        selected = []
        for run in runs_by_workflow[section]:
            if (
                run["status"] != "completed"
                or run["conclusion"] == "cancelled"
                or timestamp(run["created_at"]) >= end
                or timestamp(run["updated_at"]) < start
            ):
                continue
            jobs = jobs_for(repo, run["id"])
            finished = run_finished_at(jobs)
            if finished is None:
                report.append(
                    f"**Unable to determine completion time:** [run #{run['run_number']}]({run['html_url']})."
                )
            elif start <= finished < end:
                selected.append((run, jobs))
        if not selected:
            report.append("No non-cancelled runs with a verified completion time in this period.")
        for run, jobs in selected:
            report.append(
                f"\n### [Run #{run['run_number']}]({run['html_url']}) - "
                f"{md(run['conclusion'])} ({md(run['head_branch'])})"
            )
            if run["conclusion"] == "success":
                log, error = log_for(repo, run["id"])
                if error:
                    report.append(f"**Comparison unavailable:** {error}")
                else:
                    current, ambiguous = parse_latencies(log)
                    if not current and not ambiguous:
                        report.append("No benchmark latency measurements found; benchmarks may not have run.")
                    elif baseline_error or not baseline:
                        report.append("Comparison unavailable: release benchmark data is missing.")
                    else:
                        report.append(comparison(current, baseline, ambiguous, baseline_ambiguous))
            else:
                failed_jobs = [
                    {
                        "name": job["name"],
                        "conclusion": job["conclusion"],
                        "steps": [step["name"] for step in job.get("steps", []) if step["conclusion"] == "failure"],
                    }
                    for job in jobs
                    if job["conclusion"] in ("failure", "timed_out", "action_required")
                ]
                log, error = log_for(repo, run["id"], failed=True)
                failures.append(
                    {
                        "section": section,
                        "number": run["run_number"],
                        "url": run["html_url"],
                        "conclusion": run["conclusion"],
                        "jobs": failed_jobs,
                        "log_excerpt": log[-30000:],
                        "log_truncated": len(log) > 30000,
                        "log_error": error,
                    }
                )
                report.append("Failure triage follows below.")

    output = Path(os.environ.get("BENCHMARK_REPORT_DIR", "/tmp/gh-aw/agent"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "benchmark-failures.json").write_text(json.dumps(failures, indent=2))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write("\n\n".join(report) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(f"::error::Daily report data collection failed: {error}", file=sys.stderr)
        raise
