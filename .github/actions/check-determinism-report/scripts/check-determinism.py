#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Check the determinism report of one architecture.

Reads ``determinism_report.json`` from RESULTS_DIR (schema defined in
.ci/determinism_report.py), writes a job summary table and annotations, and
exits non-zero when any benchmark failed its determinism check, so the run is
marked failed and GitHub notifies the dispatcher.

Expects env vars: RESULTS_DIR, ARCH, GITHUB_WORKSPACE.
"""

import os
import sys
from pathlib import Path

# .ci/ isn't a package on sys.path by default; add it to reuse run.py's report schema.
sys.path.insert(0, str(Path(os.environ["GITHUB_WORKSPACE"]) / ".ci"))
from determinism_report import (  # noqa: E402
    REPORT_FILENAME,
    failed_entries,
    load_report,
    render_summary_table,
    unavailable_entries,
)


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


def main() -> int:
    """Check one architecture's determinism report and summarise the outcome.

    Reads ``RESULTS_DIR``/``determinism_report.json``, appends a pass/fail
    matrix to the job summary, and prints GitHub Actions annotations for any
    failed or unavailable determinism checks.

    Returns:
        1 if the report is missing or any benchmark failed its determinism
        check; 0 if every benchmark passed.
    """
    arch = os.environ.get("ARCH", "unknown")
    results_dir = Path(os.environ["RESULTS_DIR"])

    report = load_report(results_dir)
    if report is None:
        # Message intentionally unchanged: it's the key signal for debugging
        # why determinism checks didn't run at all for this architecture.
        print(
            f"::warning::No {REPORT_FILENAME} found in {results_dir}; determinism "
            "checks did not run or produced no results."
        )
        return 1

    experiments = report.get("experiments", [])
    failures = failed_entries(report)
    unavailable = unavailable_entries(report)

    append_summary(f"\n### Determinism checks ({arch})\n\n{render_summary_table(report)}")

    if unavailable:
        print(f"::warning::Determinism results unavailable for {len(unavailable)} benchmark(s) on {arch}.")

    if not failures:
        print(f"All {len(experiments)} benchmark(s) passed determinism checks on {arch}.")
        return 0

    for entry in failures:
        print(
            f"::error title=Determinism check failed::{entry['name']} on {arch}: "
            f"{entry.get('failed_checks', 0)} checks failed, "
            f"{entry.get('dump_bytes', 0)} bytes of dumps."
        )
    print(
        f"{len(failures)} of {len(experiments)} benchmarks failed determinism checks "
        f"on {arch}. Logs are in the benchmark artifact for this architecture."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
