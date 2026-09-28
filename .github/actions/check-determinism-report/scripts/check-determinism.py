#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Check the determinism report of one architecture.

Reads ``determinism_report.json`` from RESULTS_DIR, writes a job summary table
and annotations, and exits non-zero when any benchmark failed its determinism
check, so the run is marked failed and GitHub notifies the dispatcher.

Expects env vars: RESULTS_DIR, ARCH.
"""

import json
import os
from pathlib import Path

REPORT_FILENAME = "determinism_report.json"


def append_summary(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main() -> int:
    arch = os.environ.get("ARCH", "unknown")
    results_dir = Path(os.environ["RESULTS_DIR"])
    report_path = results_dir / REPORT_FILENAME

    if not report_path.is_file():
        print(
            f"::warning::No {REPORT_FILENAME} found in {results_dir}; determinism "
            "checks did not run or produced no results."
        )
        return 0

    report = json.loads(report_path.read_text())
    experiments = report.get("experiments", [])
    failures = [e for e in experiments if e.get("status") == "failed"]
    unavailable = [e for e in experiments if e.get("status") in ("unavailable", "error")]

    summary_lines = [
        f"\n### Determinism checks ({arch})\n\n",
        "| Benchmark | Status | Failed checks | Dump bytes |\n",
        "| --- | --- | --- | --- |\n",
    ]
    for entry in experiments:
        summary_lines.append(
            f"| {entry.get('name', '?')} | {entry.get('status', '?')} | "
            f"{entry.get('failed_checks', 0)} | {entry.get('dump_bytes', 0)} |\n"
        )
    append_summary("".join(summary_lines))

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
