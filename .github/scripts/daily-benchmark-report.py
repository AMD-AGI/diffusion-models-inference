#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from functools import cache
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

BUILD_WORKFLOW = "build-and-benchmark.yml"
# Add the upcoming xDiT nightly here; version_job/version_pattern identify the dependency under test.
NIGHTLIES = (
    {
        "name": "Nightly ROCm",
        "workflow": "nightly-rocm.yml",
        "framework": "xdit",
        "version_label": "ROCm nightly",
        "version_job": "Resolve latest ROCm nightly",
        "version_pattern": re.compile(r"Resolved ROCm nightly release: (\d{8}-\d+)"),
    },
)
ISSUE_LABEL = "nightly-failure"
HIGHLIGHT_PERCENT = 5.0
LOOKBACK_DAYS = 3
LOG_EXCERPT_CHARS = 30000
# Successful runs may skip benchmarking, so baselines search this many recent runs for latency results.
BASELINE_CANDIDATES = 5

LATENCY = re.compile(r"Median latency for (\S+): ([0-9]+(?:\.[0-9]+)?) seconds")
PLANNED = re.compile(r"##\[group\]Experiment \d+/\d+: (\S+)")
FAILED = re.compile(r"\bExperiment (\S+?) (?:failed\b|asked for )")
ARCH = re.compile(r"(?:^| / )(\S+) tuning and benchmarking$")
RELEASE = re.compile(r"^release/v(\d+)\.(\d+)$")
TITLE = re.compile(r"^Build and Benchmark (\S+)$")


@dataclass
class Benchmarks:
    latencies: dict = field(default_factory=dict)
    failed: set = field(default_factory=set)
    planned: set = field(default_factory=set)
    ambiguous: set = field(default_factory=set)

    def keys(self):
        return self.planned | self.failed | self.ambiguous | set(self.latencies)


@dataclass
class Baseline:
    label: str
    run: dict | None
    data: Benchmarks | None
    error: str | None = None


def gh(*args):
    return subprocess.run(["gh", *args], check=True, text=True, stdout=subprocess.PIPE).stdout


def api_pages(endpoint):
    return json.loads(gh("api", "--paginate", "--slurp", endpoint))


def api(endpoint):
    return json.loads(gh("api", endpoint))


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def day_window(now):
    zone = ZoneInfo("Europe/Helsinki")
    day = now.astimezone(zone).date()
    start = datetime.combine(day, time.min, zone)
    return day, start.astimezone(timezone.utc), now.astimezone(timezone.utc)


def release_version(branch):
    match = RELEASE.fullmatch(branch or "")
    return tuple(map(int, match.groups())) if match else None


def release_branches(branches):
    names = [branch["name"] for branch in branches if release_version(branch["name"])]
    return sorted(names, key=release_version, reverse=True)


def framework(run, default=None):
    match = TITLE.fullmatch(run.get("display_title") or "")
    return match.group(1).lower() if match else default


def recent_runs(repo, workflow, since):
    endpoint = f"repos/{repo}/actions/workflows/{workflow}/runs?per_page=100&created={quote('>=' + since)}"
    return [run for page in api_pages(endpoint) for run in page["workflow_runs"]]


@cache
def successful_runs(repo, branch):
    endpoint = f"repos/{repo}/actions/workflows/{BUILD_WORKFLOW}/runs?per_page=50&status=success&branch={quote(branch, safe='')}"
    return api(endpoint)["workflow_runs"]


def jobs_for(repo, run_id):
    endpoint = f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    return [job for page in api_pages(endpoint) for job in page["jobs"]]


def run_finished_at(run, jobs):
    completed = [timestamp(job["completed_at"]) for job in jobs if job.get("completed_at")]
    return max(completed) if completed else timestamp(run["updated_at"])


def parse_benchmarks(log):
    data = Benchmarks()
    for line in log.splitlines():
        arch = ARCH.search(line.split("\t", 1)[0])
        if not arch:
            continue
        arch = arch.group(1)
        if match := PLANNED.search(line):
            data.planned.add((arch, match.group(1)))
        elif match := FAILED.search(line):
            data.failed.add((arch, match.group(1)))
        elif match := LATENCY.search(line):
            key, value = (arch, match.group(1)), float(match.group(2))
            if data.latencies.get(key, value) != value:
                data.ambiguous.add(key)
            data.latencies[key] = value
    for key in data.ambiguous:
        data.latencies.pop(key, None)
    return data


def log_for(repo, run_id, failed=False):
    try:
        return gh("run", "view", str(run_id), "--repo", repo, "--log-failed" if failed else "--log"), None
    except subprocess.CalledProcessError as error:
        return "", f"Log retrieval failed (gh exited {error.returncode})."


@cache
def benchmarks_for(repo, run_id):
    log, error = log_for(repo, run_id)
    return (None, error) if error else (parse_benchmarks(log), None)


def dependency_version(repo, jobs, nightly):
    job = next((job for job in jobs if job["name"] == nightly["version_job"]), None)
    if not job:
        return None
    try:
        log = gh("api", f"repos/{repo}/actions/jobs/{job['id']}/logs", "--allow-escape-sequences")
    except subprocess.CalledProcessError:
        return None
    match = nightly["version_pattern"].search(log)
    return match.group(1) if match else None


def benchmarked_baseline(repo, label, branch, fw, before=None):
    runs = sorted(
        (
            run
            for run in successful_runs(repo, branch)
            if run["conclusion"] == "success"
            and framework(run, "xdit") == fw
            and (before is None or timestamp(run["run_started_at"]) < before)
        ),
        key=lambda run: timestamp(run["run_started_at"]),
        reverse=True,
    )
    failed_logs = 0
    for run in runs[:BASELINE_CANDIDATES]:
        data, error = benchmarks_for(repo, run["id"])
        failed_logs += bool(error)
        if data and data.latencies:
            return Baseline(label, run, data)
    message = f"No successful {fw} run with benchmark results on {branch}."
    if failed_logs:
        message += f" Logs could not be downloaded for {failed_logs} candidate run(s)."
    return Baseline(label, None, None, message)


def release_baselines(repo, run, fw, releases):
    branch = run["head_branch"]
    previous = [name for name in releases if release_version(name) < release_version(branch)]
    prior_release = Baseline("previous release", None, None, f"No previous release branch has {fw} benchmark results.")
    for name in previous[:3]:
        if (candidate := benchmarked_baseline(repo, name, name, fw)).run:
            prior_release = candidate
            break
    prior_run = benchmarked_baseline(repo, f"previous {branch} run", branch, fw, timestamp(run["run_started_at"]))
    return [prior_release, prior_run]


def md(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def change(new, old):
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / old * 100, 2)


def change_cell(pct):
    if pct is None:
        return "—"
    if pct <= -HIGHLIGHT_PERCENT:
        return f"**{pct:+.2f}% 🏆**"
    if pct >= HIGHLIGHT_PERCENT:
        return f"**{pct:+.2f}% ⚠️**"
    return f"{pct:+.2f}%"


def current_cell(data, key):
    if key in data.failed:
        return "**FAILED**"
    if key in data.ambiguous:
        return "Ambiguous duplicate"
    value = data.latencies.get(key)
    return f"{value:.4f}" if value is not None else "N/A"


def benchmark_table(current, baselines, keys):
    if not keys:
        return "No benchmark experiments found in the run logs."
    header = "| GPU | Benchmark | Current (s) |" + "".join(f" {md(b.label)} (s) | Change |" for b in baselines)
    lines = [header, "| --- | --- | ---: |" + " ---: | ---: |" * len(baselines)]
    for key in sorted(keys):
        row = f"| {md(key[0])} | {md(key[1])} | {current_cell(current, key)} |"
        for baseline in baselines:
            old = baseline.data.latencies.get(key) if baseline.data else None
            new = None if key in current.failed else current.latencies.get(key)
            row += f" {f'{old:.4f}' if old is not None else 'N/A'} | {change_cell(change(new, old))} |"
        lines.append(row)
    return "\n".join(lines)


def highlights(current, baselines):
    lines = []
    for baseline in baselines:
        if not baseline.data:
            continue
        wins, losses = [], []
        for key in sorted(set(current.latencies) - current.failed):
            pct = change(current.latencies[key], baseline.data.latencies.get(key))
            if pct is None:
                continue
            entry = f"{md(key[1])} on {md(key[0])} ({pct:+.2f}%)"
            if pct <= -HIGHLIGHT_PERCENT:
                wins.append(entry)
            elif pct >= HIGHLIGHT_PERCENT:
                losses.append(entry)
        if wins:
            lines.append(
                f"🏆 **Victories vs {md(baseline.label)}** (≥{HIGHLIGHT_PERCENT:g}% faster): " + ", ".join(wins)
            )
        if losses:
            lines.append(
                f"⚠️ **Regressions vs {md(baseline.label)}** (≥{HIGHLIGHT_PERCENT:g}% slower): " + ", ".join(losses)
            )
    return lines


def baseline_lines(baselines):
    lines = []
    for baseline in baselines:
        if baseline.run:
            run = baseline.run
            lines.append(
                f"- Baseline {md(baseline.label)}: [{md(run['head_branch'])} run #{run['run_number']}]({run['html_url']})"
            )
        else:
            lines.append(f"- Baseline {md(baseline.label)}: **unavailable:** {baseline.error}")
    return ["\n".join(lines)]


STATUS_ICON = {"success": "✅", "cancelled": "⏹️"}


def outcome(run):
    if run["conclusion"] == "success":
        return "succeeded"
    return "cancelled" if run["conclusion"] == "cancelled" else "failed"


def counts_table(groups):
    lines = ["| Category | Runs | Succeeded | Failed | Cancelled |", "| --- | ---: | ---: | ---: | ---: |"]
    totals = [0, 0, 0, 0]
    for name, runs in groups:
        row = [len(runs)] + [sum(outcome(run) == kind for run in runs) for kind in ("succeeded", "failed", "cancelled")]
        totals = [a + b for a, b in zip(totals, row)]
        lines.append(f"| {md(name)} | " + " | ".join(map(str, row)) + " |")
    lines.append("| **All** | " + " | ".join(f"**{value}**" for value in totals) + " |")
    total, ok, failed, cancelled = totals
    return f"**{total} runs: {ok} succeeded, {failed} failed, {cancelled} cancelled.**", "\n".join(lines)


def open_issues(repo):
    endpoint = f"repos/{repo}/issues?state=open&per_page=100&labels={quote(ISSUE_LABEL)}"
    return [
        {
            "number": issue["number"],
            "title": issue["title"],
            "url": issue["html_url"],
            "body": (issue["body"] or "")[:3000],
        }
        for page in api_pages(endpoint)
        for issue in page
        if "pull_request" not in issue
    ]


def select_runs(repo, runs, start, end):
    selected, in_progress = [], 0
    for run in runs:
        if timestamp(run["created_at"]) >= end:
            continue
        if run["status"] != "completed":
            in_progress += 1
            continue
        if timestamp(run["updated_at"]) < start:
            continue
        jobs = jobs_for(repo, run["id"])
        if start <= run_finished_at(run, jobs) < end:
            selected.append((run, jobs))
    return selected, in_progress


def failure_entry(repo, run, jobs, category, section, fw, current, extra):
    log, error = log_for(repo, run["id"], failed=True)
    return {
        "category": category,
        "section": section,
        "create_issue_allowed": category == "nightly",
        "number": run["run_number"],
        "url": run["html_url"],
        "branch": run["head_branch"],
        "framework": fw,
        "conclusion": run["conclusion"],
        "jobs": [
            {
                "name": job["name"],
                "conclusion": job["conclusion"],
                "steps": [step["name"] for step in job.get("steps", []) if step["conclusion"] == "failure"],
            }
            for job in jobs
            if job["conclusion"] in ("failure", "timed_out", "action_required", "startup_failure")
        ],
        "failed_benchmarks": [f"{name} ({arch})" for arch, name in sorted(current.failed)],
        "log_excerpt": log[-LOG_EXCERPT_CHARS:],
        "log_truncated": len(log) > LOG_EXCERPT_CHARS,
        "log_error": error,
        **extra,
    }


def run_heading(run, fw, extra=""):
    icon = STATUS_ICON.get(run["conclusion"], "❌")
    return (
        f"\n### {icon} [{md(run['head_branch'])} · {md(fw or 'unknown framework')} · run #{run['run_number']}]"
        f"({run['html_url']}) — {md(run['conclusion'])}{extra}"
    )


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    day, start, end = day_window(datetime.now(timezone.utc))
    since = (start - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    releases = release_branches(
        [branch for page in api_pages(f"repos/{repo}/branches?per_page=100") for branch in page]
    )
    newest_release = releases[0] if releases else None

    build_runs, build_in_progress = select_runs(repo, recent_runs(repo, BUILD_WORKFLOW, since), start, end)
    default_branch = api(f"repos/{repo}")["default_branch"]
    nightly_runs = []
    in_progress = build_in_progress
    for nightly in NIGHTLIES:
        selected, pending = select_runs(repo, recent_runs(repo, nightly["workflow"], since), start, end)
        # Nightly workflows run on other branches (e.g. auto-repair tests) are watched by hand, so report them as development runs.
        nightly_runs.append((nightly, [item for item in selected if item[0]["head_branch"] == default_branch]))
        build_runs += [item for item in selected if item[0]["head_branch"] != default_branch]
        in_progress += pending
    release_runs = [(run, jobs) for run, jobs in build_runs if release_version(run["head_branch"])]
    dev_runs = [(run, jobs) for run, jobs in build_runs if not release_version(run["head_branch"])]

    summary, table = counts_table(
        [("Release branches", [run for run, _ in release_runs])]
        + [(nightly["name"], [run for run, _ in selected]) for nightly, selected in nightly_runs]
        + [("Development branches", [run for run, _ in dev_runs])]
    )
    report = [
        f"# Daily build and benchmark report - {day} (Europe/Helsinki)",
        f"Runs completed from {start.isoformat()} through {end.isoformat()} (exclusive).",
        summary,
        table,
    ]
    if in_progress:
        report.append(f"{in_progress} run(s) still in progress are not included.")
    report.append(
        "Latency change is relative to the baseline; negative is faster. "
        f"Changes of {HIGHLIGHT_PERCENT:g}% or more are highlighted (🏆 faster, ⚠️ slower). "
        "Comparisons only use runs of the same framework."
    )

    failures = []
    issues_cache = None

    report.append("\n## Release branches")
    if not release_runs:
        report.append("No release branch runs completed in this period.")
    for run, jobs in sorted(
        release_runs, key=lambda item: (release_version(item[0]["head_branch"]), item[0]["run_number"]), reverse=True
    ):
        fw = framework(run, "xdit")
        report.append(run_heading(run, fw))
        if run["conclusion"] == "cancelled":
            report.append("Cancelled; not analysed.")
            continue
        current, error = benchmarks_for(repo, run["id"])
        if error:
            report.append(f"**Benchmark results unavailable:** {error}")
            current = Benchmarks()
        baselines = release_baselines(repo, run, fw, releases)
        report.extend(baseline_lines(baselines))
        if current.keys():
            report.extend(highlights(current, baselines))
            keys = current.keys().union(*(baseline.data.keys() for baseline in baselines if baseline.data))
            report.append(benchmark_table(current, baselines, keys))
        else:
            report.append("**No benchmarks ran in this run**, so there is nothing to compare.")
        if run["conclusion"] != "success":
            failures.append(failure_entry(repo, run, jobs, "release", "Release branches", fw, current, {}))
            report.append("Failure triage follows below.")

    def compatibility_section(title, runs, category, nightly=None):
        nonlocal issues_cache
        report.append(f"\n## {title}")
        if not runs:
            report.append("No runs completed in this period.")
        for run, jobs in sorted(runs, key=lambda item: item[0]["run_number"], reverse=True):
            fw = nightly["framework"] if nightly else framework(run, "xdit")
            extra = {}
            heading = ""
            if nightly:
                version = dependency_version(repo, jobs, nightly)
                extra["dependency"] = {"label": nightly["version_label"], "version": version}
                heading = f" · {nightly['version_label']} `{md(version or 'unknown')}`"
            report.append(run_heading(run, fw, heading))
            if run["conclusion"] == "cancelled":
                report.append("Cancelled; not analysed.")
                continue
            current, error = benchmarks_for(repo, run["id"])
            if error:
                report.append(f"**Benchmark results unavailable:** {error}")
                current = Benchmarks()
            baseline = (
                benchmarked_baseline(repo, newest_release, newest_release, fw)
                if newest_release
                else Baseline("release", None, None, "No release branch found.")
            )
            report.extend(baseline_lines([baseline]))
            report.extend(highlights(current, [baseline]))
            report.append(benchmark_table(current, [baseline], current.keys()))
            if run["conclusion"] != "success":
                if nightly:
                    if issues_cache is None:
                        issues_cache = open_issues(repo)
                    extra["open_issues"] = issues_cache
                    extra["last_success"] = last_nightly_success(repo, nightly, run)
                failures.append(failure_entry(repo, run, jobs, category, title, fw, current, extra))
                report.append("Failure triage follows below.")

    for nightly, selected in nightly_runs:
        compatibility_section(nightly["name"], selected, "nightly", nightly)
    compatibility_section("Development branches", dev_runs, "development")

    output = Path(os.environ.get("BENCHMARK_REPORT_DIR", "/tmp/gh-aw/agent"))
    output.mkdir(parents=True, exist_ok=True)
    (output / "benchmark-failures.json").write_text(json.dumps(failures, indent=2))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary_file:
        summary_file.write("\n\n".join(report) + "\n")


def last_nightly_success(repo, nightly, run):
    endpoint = (
        f"repos/{repo}/actions/workflows/{nightly['workflow']}/runs"
        f"?per_page=20&status=success&branch={quote(run['head_branch'], safe='')}"
    )
    candidates = [
        candidate
        for candidate in api(endpoint)["workflow_runs"]
        if timestamp(candidate["run_started_at"]) < timestamp(run["run_started_at"])
    ]
    if not candidates:
        return None
    previous = max(candidates, key=lambda candidate: timestamp(candidate["run_started_at"]))
    return {
        "number": previous["run_number"],
        "url": previous["html_url"],
        "version": dependency_version(repo, jobs_for(repo, previous["id"]), nightly),
    }


if __name__ == "__main__":
    try:
        main()
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        print(f"::error::Daily report data collection failed: {error}", file=sys.stderr)
        raise
