#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Check the determinism report of one architecture.

Reads ``determinism_report.json`` from RESULTS_DIR (schema defined in
.ci/determinism_report.py), decides which results count as a failure, writes
a job summary table and annotations, and exits non-zero when any benchmark's
determinism result is failed or missing, so the run is marked failed and
GitHub notifies the dispatcher.

Expects env vars: RESULTS_DIR, ARCH, GITHUB_WORKSPACE.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

# .ci/ isn't a package on sys.path by default; add it to reuse run.py's report schema.
sys.path.insert(0, str(Path(os.environ["GITHUB_WORKSPACE"]) / ".ci"))
from determinism_report import (  # noqa: E402
    REPORT_FILENAME,
    STATUS_ERROR,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_UNAVAILABLE,
    load_report,
)

_STATUS_ICONS = {
    STATUS_PASSED: "✅",
    STATUS_FAILED: "❌",
    STATUS_ERROR: "⚠️",
    STATUS_UNAVAILABLE: "❓",
}


def status_icon(status: str) -> str:
    """Return the icon used to represent a determinism check status.

    Args:
        status: One of the report's ``STATUS_*`` values. Any other value
            falls back to the warning icon.

    Returns:
        A single emoji character summarising the status.
    """
    return _STATUS_ICONS.get(status, "⚠️")


def render_summary_table(report: Dict[str, Any]) -> str:
    """Render a Markdown table mapping benchmark name to determinism result.

    Args:
        report: A report as returned by `load_report`.

    Returns:
        A Markdown table with one row per experiment, with the status
        rendered as an icon (see `status_icon`).
    """
    lines = ["| Benchmark | Result |", "| --- | --- |"]
    for entry in report.get("experiments", []):
        lines.append(f"| {entry.get('name', '?')} | {status_icon(entry.get('status', ''))} |")
    return "\n".join(lines) + "\n"


def failing_entries(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the entries that should fail the determinism check job.

    A result is treated as a failure if the determinism check actually ran
    and found a problem (`STATUS_FAILED`), or if no result could be obtained
    for it at all (`STATUS_UNAVAILABLE`/`STATUS_ERROR`) — a missing result is
    not a pass.

    Args:
        report: A report as returned by `load_report`.

    Returns:
        The subset of `report`'s ``experiments`` that count as a failure.
    """
    return [
        e
        for e in report.get("experiments", [])
        if e.get("status") in (STATUS_FAILED, STATUS_ERROR, STATUS_UNAVAILABLE)
    ]


def append_summary(text: str) -> None:
    """Append text to the current GitHub Actions job summary, if configured.

    Args:
        text: Markdown text to append.

    Returns:
        None.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(text)


def _warn_missing_report(results_dir: Path) -> int:
    """Warn that no determinism report was found and fail the check.

    Args:
        results_dir: Directory that was expected to hold
            ``determinism_report.json``.

    Returns:
        1, since a missing report counts as a failed determinism check.
    """
    # Message intentionally unchanged: it's the key signal for debugging
    # why determinism checks didn't run at all for this architecture.
    print(
        f"::warning::No {REPORT_FILENAME} found in {results_dir}; determinism "
        "checks did not run or produced no results."
    )
    return 1


def _report_passed(experiment_count: int, arch: str) -> int:
    """Report that every benchmark passed its determinism check.

    Args:
        experiment_count: Number of benchmarks checked.
        arch: Architecture the benchmarks ran on.

    Returns:
        0.
    """
    print(f"All {experiment_count} benchmark(s) passed determinism checks on {arch}.")
    return 0


def _report_failures(failures: List[Dict[str, Any]], experiment_count: int, arch: str) -> int:
    """Print an annotation per failing benchmark and fail the check.

    Args:
        failures: Entries returned by `failing_entries`.
        experiment_count: Total number of benchmarks checked.
        arch: Architecture the benchmarks ran on.

    Returns:
        1.
    """
    for entry in failures:
        status = entry.get("status")
        if status == STATUS_FAILED:
            print(
                f"::error title=Determinism check failed::{entry['name']} on {arch}: "
                f"{entry.get('failed_checks', 0)} checks failed, "
                f"{entry.get('dump_bytes', 0)} bytes of dumps."
            )
        else:
            print(
                f"::error title=Determinism result missing::{entry.get('name', '?')} on {arch}: "
                f"no determinism check result was recorded (status={status})."
            )
    print(
        f"{len(failures)} of {experiment_count} benchmarks failed or are missing determinism "
        f"results on {arch}. Logs are in the benchmark artifact for this architecture."
    )
    return 1


def main() -> int:
    """Check one architecture's determinism report and summarise the outcome.

    Reads ``RESULTS_DIR``/``determinism_report.json``, appends a pass/fail
    matrix to the job summary, and prints GitHub Actions annotations for any
    benchmark whose determinism result is failed, missing, or errored.

    Returns:
        1 if the report is missing, or any benchmark's determinism result is
        failed, missing, or errored; 0 if every benchmark passed.
    """
    arch = os.environ.get("ARCH", "unknown")
    results_dir = Path(os.environ["RESULTS_DIR"])

    report = load_report(results_dir)
    if report is None:
        return _warn_missing_report(results_dir)

    experiments = report.get("experiments", [])
    failures = failing_entries(report)
    append_summary(f"\n### Determinism checks ({arch})\n\n{render_summary_table(report)}")

    if not failures:
        return _report_passed(len(experiments), arch)
    return _report_failures(failures, len(experiments), arch)


if __name__ == "__main__":
    raise SystemExit(main())

