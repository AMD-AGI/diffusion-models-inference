# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Schema, I/O, and presentation for the xFuser determinism check report.

Written by ``run.py`` after each experiment that ran with the determinism
check enabled, and read by ``.github/actions/check-determinism-report`` to
decide whether a benchmark run's architecture should be treated as failed.
Both sides import this module so the report's shape, and how it's rendered,
has a single definition.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

REPORT_FILENAME = "determinism_report.json"

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_ERROR = "error"
STATUS_UNAVAILABLE = "unavailable"

_STATUS_ICONS = {
    STATUS_PASSED: "✅",
    STATUS_FAILED: "❌",
    STATUS_ERROR: "⚠️",
    STATUS_UNAVAILABLE: "❓",
}

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s - %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(_handler)
    logger.propagate = False


def make_entry(
    name: str,
    status: str = STATUS_UNAVAILABLE,
    failed_checks: int = 0,
    dump_bytes: int = 0,
    output_directory: str = "",
) -> Dict[str, Any]:
    """Build one experiment's entry in the report's ``experiments`` list.

    Args:
        name: Name of the experiment the entry describes.
        status: One of the ``STATUS_*`` constants.
        failed_checks: Number of determinism checks that failed.
        dump_bytes: Bytes occupied by dumps produced by failed checks.
        output_directory: Path to the experiment's output directory.

    Returns:
        A dictionary with the entry's fields, ready to be placed in the
        report's ``experiments`` list.
    """
    return {
        "name": name,
        "status": status,
        "failed_checks": failed_checks,
        "dump_bytes": dump_bytes,
        "output_directory": output_directory,
    }


def write_report(results_directory: Path, entries: List[Dict[str, Any]]) -> Optional[Path]:
    """Build and write the determinism report, logging the outcome.

    Aggregates `entries` into a report and writes it to
    ``<results_directory>/determinism_report.json``, creating the directory
    if necessary. Failures are logged rather than raised, so this can be
    called as a fire-and-forget step at the end of a benchmark run.

    Args:
        results_directory: Directory the report is written into.
        entries: Per-experiment entries, as built by `make_entry`.

    Returns:
        The path the report was written to, or None if writing failed.
    """
    results_directory = Path(results_directory)
    path = results_directory / REPORT_FILENAME
    report = {
        "experiments": entries,
        "failed_experiments": sum(1 for e in entries if e["status"] == STATUS_FAILED),
        "failed_checks": sum(e["failed_checks"] for e in entries),
    }
    try:
        results_directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
    except OSError as exc:
        logger.error("Failed to write determinism report to %s: %s", path, exc)
        return None

    logger.info(
        "Determinism report written to %s (%d of %d experiments failed).",
        path,
        report["failed_experiments"],
        len(entries),
    )
    return path


def load_report(results_directory: Path) -> Optional[Dict[str, Any]]:
    """Load the determinism report from a results directory.

    Args:
        results_directory: Directory expected to hold
            ``determinism_report.json``.

    Returns:
        The parsed report, or None if the file doesn't exist.
    """
    path = Path(results_directory) / REPORT_FILENAME
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def failed_entries(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the entries whose determinism check failed.

    Args:
        report: A report as returned by `load_report`.

    Returns:
        The subset of `report`'s ``experiments`` with status `STATUS_FAILED`.
    """
    return [e for e in report.get("experiments", []) if e.get("status") == STATUS_FAILED]


def unavailable_entries(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return entries whose determinism check result couldn't be obtained.

    Args:
        report: A report as returned by `load_report`.

    Returns:
        The subset of `report`'s ``experiments`` with status
        `STATUS_UNAVAILABLE` or `STATUS_ERROR`.
    """
    return [e for e in report.get("experiments", []) if e.get("status") in (STATUS_UNAVAILABLE, STATUS_ERROR)]


def status_icon(status: str) -> str:
    """Return the icon representing a determinism check status.

    Args:
        status: One of the ``STATUS_*`` constants. `STATUS_ERROR` gets a
            warning icon (something went wrong while checking);
            `STATUS_UNAVAILABLE` gets a question mark (a result is missing
            when one was expected). Any other value falls back to the
            warning icon.

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
        rendered as an icon (see `status_icon`), suitable for appending to a
        GitHub Actions job summary.
    """
    lines = ["| Benchmark | Result |", "| --- | --- |"]
    for entry in report.get("experiments", []):
        lines.append(f"| {entry.get('name', '?')} | {status_icon(entry.get('status', ''))} |")
    return "\n".join(lines) + "\n"
