"""Compare two finished MIOpen A/B runs, one shape at a time.

Reads ``comparison.json`` and ``metadata.json`` only. The baseline run is the
reference. The candidate is the version under evaluation. ``delta_ms`` is
baseline time minus candidate time, so a positive delta means the candidate
is faster.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .compare import kernel_difference, solver_name
from .report import (
    _cell,
    _fmt_ms,
    _fmt_pct,
    _kernel_cell,
    _shape_label,
    _solver_cell,
    _workload_label,
)

_VERSION_RE = re.compile(r"version:\s*([^)]+)")
_ARMS = (
    ("production", "arm_a", "Production path"),
    ("exhaustive", "arm_b", "Exhaustive path"),
)
_KERNEL_LABELS = {
    "same_kernel": "same kernel",
    "different_solver": "solver changed",
    "different_kernel": "kernel changed",
    "kernel_not_recorded": "not recorded",
}


@dataclass
class VersionSide:
    run_dir: str
    run_id: str
    label: str
    short_label: str
    miopen_version: str
    rocm_version: str
    hip_version: str
    docker_image: str
    timestamp_utc: str
    hostname: str
    db_prefix: str
    system_db_path: str | None
    benchmark_repeats: int | None


@dataclass
class LoadedRun:
    side: VersionSide
    entries: dict[str, dict[str, Any]]


def _miopen_labels(metadata: dict[str, Any], run_id: str) -> tuple[str, str, str]:
    raw = (metadata.get("miopen_driver_version") or "").strip()
    match = _VERSION_RE.search(raw)
    version = match.group(1).strip() if match else ""
    label = f"MIOpen {version}" if version else (raw or run_id)
    short = version or run_id
    return label, short, raw


def _index_entries(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        command = entry.get("command")
        if command:
            indexed[command] = entry
    return indexed


def load_run(path: Path) -> LoadedRun:
    """Load a finished run. Does not write inside ``path``."""
    path = path.resolve()
    comparison_path = path / "comparison.json"
    if not comparison_path.is_file():
        raise FileNotFoundError(f"No comparison.json in {path}")
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    metadata_path = path / "metadata.json"
    metadata: dict[str, Any] = {}
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    label, short, raw_version = _miopen_labels(metadata, path.name)
    system_db = comparison.get("system_db_path") or comparison.get("system_udb_path")
    side = VersionSide(
        run_dir=str(path),
        run_id=path.name,
        label=label,
        short_label=short,
        miopen_version=raw_version,
        rocm_version=(metadata.get("rocm_version") or "").strip(),
        hip_version=(metadata.get("hip_version") or "").strip(),
        docker_image=(metadata.get("docker_image") or "").strip(),
        timestamp_utc=(metadata.get("timestamp_utc") or "").strip(),
        hostname=(metadata.get("hostname") or "").strip(),
        db_prefix=(metadata.get("db_prefix") or "").strip(),
        system_db_path=str(system_db) if system_db else None,
        benchmark_repeats=comparison.get("benchmark_repeats"),
    )
    return LoadedRun(side=side, entries=_index_entries(comparison.get("entries") or []))


def _classify(
    baseline_ms: float | None,
    candidate_ms: float | None,
    threshold_pct: float,
) -> tuple[float | None, float | None, str]:
    if baseline_ms is None or candidate_ms is None or baseline_ms <= 0:
        return None, None, "failed"
    delta_ms = baseline_ms - candidate_ms
    speedup_pct = (delta_ms / baseline_ms) * 100
    if abs(speedup_pct) <= threshold_pct:
        verdict = "similar"
    elif speedup_pct > 0:
        verdict = "improved"
    else:
        verdict = "worse"
    return delta_ms, speedup_pct, verdict


def _merge_sources(baseline: list[str] | None, candidate: list[str] | None) -> list[str]:
    merged: list[str] = []
    for path in list(baseline or []) + list(candidate or []):
        if path not in merged:
            merged.append(path)
    return merged


def _shape_row(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    prefix: str,
    threshold_pct: float,
) -> dict[str, Any]:
    baseline_ms = baseline.get(f"{prefix}_median_ms")
    candidate_ms = candidate.get(f"{prefix}_median_ms")
    delta_ms, speedup_pct, verdict = _classify(baseline_ms, candidate_ms, threshold_pct)
    baseline_solver = baseline.get(f"{prefix}_solver")
    candidate_solver = candidate.get(f"{prefix}_solver")
    return {
        "command": baseline["command"],
        "shape": baseline.get("shape") or candidate.get("shape"),
        "source_files": _merge_sources(
            baseline.get("source_files"), candidate.get("source_files")
        ),
        "baseline_ms": baseline_ms,
        "candidate_ms": candidate_ms,
        "baseline_stddev_ms": baseline.get(f"{prefix}_stddev_ms"),
        "candidate_stddev_ms": candidate.get(f"{prefix}_stddev_ms"),
        "delta_ms": delta_ms,
        "speedup_pct": speedup_pct,
        "verdict": verdict,
        "baseline_solver": baseline_solver,
        "candidate_solver": candidate_solver,
        "kernel_difference": kernel_difference(baseline_solver, candidate_solver),
        "baseline_in_system_db": bool(baseline.get("in_system_db")),
        "candidate_in_system_db": bool(candidate.get("in_system_db")),
    }


def _difference_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(row["kernel_difference"] for row in rows)
    return {
        "same_kernel": counts.get("same_kernel", 0),
        "different_solver": counts.get("different_solver", 0),
        "different_kernel": counts.get("different_kernel", 0),
        "kernel_not_recorded": counts.get("kernel_not_recorded", 0),
    }


def _by_source(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sources = row["source_files"] or ["(no workload file)"]
        for source in sources:
            grouped.setdefault(source, []).append(row)
    summaries = []
    for source in sorted(grouped):
        items = grouped[source]
        improved = [row for row in items if row["verdict"] == "improved"]
        worse = [row for row in items if row["verdict"] == "worse"]
        compared = [row for row in items if row["verdict"] != "failed"]
        summaries.append(
            {
                "source_file": source,
                "improved": len(improved),
                "similar": sum(1 for row in items if row["verdict"] == "similar"),
                "worse": len(worse),
                "failed": sum(1 for row in items if row["verdict"] == "failed"),
                "ms_faster": sum(row["delta_ms"] or 0 for row in improved),
                "ms_slower": -sum(row["delta_ms"] or 0 for row in worse),
                "net_delta_ms": sum(row["delta_ms"] or 0 for row in compared),
                "kernel_changed": sum(
                    1
                    for row in compared
                    if row["kernel_difference"] != "same_kernel"
                ),
            }
        )
    return summaries


def _summarize_arm(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "improved": [],
        "similar": [],
        "worse": [],
        "failed": [],
    }
    for row in rows:
        buckets[row["verdict"]].append(row)
    buckets["improved"].sort(key=lambda row: (-(row["delta_ms"] or 0), row["command"]))
    buckets["worse"].sort(
        key=lambda row: (
            row["delta_ms"] if row["delta_ms"] is not None else 0,
            row["command"],
        )
    )
    buckets["similar"].sort(key=lambda row: (-abs(row["delta_ms"] or 0), row["command"]))
    compared = [row for row in rows if row["verdict"] != "failed"]
    return {
        "counts": {name: len(items) for name, items in buckets.items()},
        "baseline_total_ms": sum(row["baseline_ms"] or 0 for row in compared),
        "candidate_total_ms": sum(row["candidate_ms"] or 0 for row in compared),
        "net_delta_ms": sum(row["delta_ms"] or 0 for row in compared),
        "ms_faster": sum(row["delta_ms"] or 0 for row in buckets["improved"]),
        "ms_slower": -sum(row["delta_ms"] or 0 for row in buckets["worse"]),
        "kernel_difference_counts": {
            name: _difference_counts(buckets[name])
            for name in ("improved", "worse", "similar")
        },
        "by_source": _by_source(rows),
        "improved": buckets["improved"],
        "worse": buckets["worse"],
        "similar": buckets["similar"],
        "failed": buckets["failed"],
    }


def _unmatched(entry: dict[str, Any], side: str) -> dict[str, Any]:
    return {
        "side": side,
        "command": entry.get("command"),
        "shape": entry.get("shape"),
        "source_files": list(entry.get("source_files") or []),
        "arm_a_median_ms": entry.get("arm_a_median_ms"),
        "arm_b_median_ms": entry.get("arm_b_median_ms"),
    }


def _public_side(
    side: VersionSide,
    entries: dict[str, dict[str, Any]],
    label: str,
    short_label: str,
) -> dict[str, Any]:
    payload = asdict(side)
    payload["label"] = label
    payload["short_label"] = short_label
    payload["commands"] = len(entries)
    payload["system_db_hits"] = sum(1 for entry in entries.values() if entry.get("in_system_db"))
    return payload


def _stack_differences(baseline: VersionSide, candidate: VersionSide) -> list[str]:
    differences = []
    fields = (
        ("ROCm", baseline.rocm_version, candidate.rocm_version),
        ("HIP", baseline.hip_version, candidate.hip_version),
        ("Docker image", baseline.docker_image, candidate.docker_image),
        ("system DB", baseline.system_db_path or "n/a", candidate.system_db_path or "n/a"),
    )
    for name, left, right in fields:
        if (left or "") != (right or ""):
            differences.append(f"{name} {left or 'n/a'} vs {right or 'n/a'}")
    return differences


def compare_runs(
    baseline: LoadedRun,
    candidate: LoadedRun,
    threshold_pct: float,
) -> dict[str, Any]:
    """Join the runs on MIOpenDriver command and classify each shape."""
    base_label = baseline.side.label
    cand_label = candidate.side.label
    base_short = baseline.side.short_label
    cand_short = candidate.side.short_label
    if base_short == cand_short:
        base_short = f"{base_short} ({baseline.side.run_id})"
        cand_short = f"{cand_short} ({candidate.side.run_id})"
        base_label = f"{base_label} ({baseline.side.run_id})"
        cand_label = f"{cand_label} ({candidate.side.run_id})"

    shared = sorted(set(baseline.entries) & set(candidate.entries))
    arms = {}
    for arm_name, prefix, _title in _ARMS:
        rows = [
            _shape_row(
                baseline.entries[command],
                candidate.entries[command],
                prefix,
                threshold_pct,
            )
            for command in shared
        ]
        arms[arm_name] = _summarize_arm(rows)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "threshold_pct": threshold_pct,
        "baseline": _public_side(baseline.side, baseline.entries, base_label, base_short),
        "candidate": _public_side(candidate.side, candidate.entries, cand_label, cand_short),
        "stack_differences": _stack_differences(baseline.side, candidate.side),
        "matched_commands": len(shared),
        "production": arms["production"],
        "exhaustive": arms["exhaustive"],
        "unmatched": {
            "baseline_only": [
                _unmatched(baseline.entries[command], "baseline")
                for command in sorted(set(baseline.entries) - set(candidate.entries))
            ],
            "candidate_only": [
                _unmatched(candidate.entries[command], "candidate")
                for command in sorted(set(candidate.entries) - set(baseline.entries))
            ],
        },
    }


def _kernel_phrase(value: str) -> str:
    return _KERNEL_LABELS.get(value, value or "n/a")


def _db_cell(row: dict[str, Any]) -> str:
    baseline = "hit" if row["baseline_in_system_db"] else "miss"
    candidate = "hit" if row["candidate_in_system_db"] else "miss"
    return f"{baseline} / {candidate}"


def _compared_count(arm: dict[str, Any]) -> int:
    counts = arm["counts"]
    return counts["improved"] + counts["similar"] + counts["worse"]


def _shapes(count: int) -> str:
    return "1 shape" if count == 1 else f"{count} shapes"


def _are(count: int) -> str:
    return "is" if count == 1 else "are"


def _net_sentence(candidate_label: str, arm: dict[str, Any]) -> str:
    compared = _compared_count(arm)
    if compared == 0:
        return "No shape had a timing on both runs."
    net = arm["net_delta_ms"]
    if net > 0:
        direction = f"{candidate_label} is {_fmt_ms(net)} ms faster in total"
    elif net < 0:
        direction = f"{candidate_label} is {_fmt_ms(abs(net))} ms slower in total"
    else:
        direction = f"{candidate_label} matches the baseline in total"
    return (
        f"{direction} ({_fmt_ms(arm['baseline_total_ms'])} ms to "
        f"{_fmt_ms(arm['candidate_total_ms'])} ms across {_shapes(compared)})."
    )


def _split_sentence(arm: dict[str, Any], threshold_pct: float) -> str:
    counts = arm["counts"]
    return (
        f"{_shapes(counts['improved'])} {_are(counts['improved'])} faster by {_fmt_ms(arm['ms_faster'])} ms. "
        f"{_shapes(counts['worse'])} {_are(counts['worse'])} slower by {_fmt_ms(arm['ms_slower'])} ms. "
        f"{_shapes(counts['similar'])} {_are(counts['similar'])} within {threshold_pct:.1f}%."
    )


def _breakdown_sentence(kind: str, arm: dict[str, Any]) -> str | None:
    count = arm["counts"][kind]
    if count == 0:
        return None
    counts = arm["kernel_difference_counts"][kind]
    noun = "shape" if count == 1 else "shapes"
    return (
        f"Of the {count} {kind} {noun}, {counts['different_solver']} changed solver, "
        f"{counts['different_kernel']} kept the solver and changed kernel config, "
        f"{counts['same_kernel']} kept the same kernel, and "
        f"{counts['kernel_not_recorded']} had no recorded kernel on at least one side."
    )


def _highlight(row: dict[str, Any], baseline_label: str, candidate_label: str) -> str:
    workload = _workload_label(row)
    workload_text = f" ({workload})" if workload else ""
    return (
        f"`{_cell(_shape_label(row))}`{workload_text}: "
        f"{_fmt_ms(row.get('baseline_ms'))} ms on {baseline_label}, "
        f"{_fmt_ms(row.get('candidate_ms'))} ms on {candidate_label} "
        f"({_fmt_pct(row.get('speedup_pct'))}, {_kernel_phrase(row['kernel_difference'])}, "
        f"{solver_name(row.get('baseline_solver')) or 'n/a'} -> "
        f"{solver_name(row.get('candidate_solver')) or 'n/a'})"
    )


def _md_table(
    headers: list[str],
    rows: list[list[str]],
    numeric: set[int] | None = None,
) -> list[str]:
    numeric = numeric or set()
    align = ["---:" if index in numeric else "---" for index in range(len(headers))]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(align) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _detail_table(
    rows: list[dict[str, Any]],
    baseline_label: str,
    candidate_label: str,
    include_system_db: bool,
) -> list[str]:
    headers = [
        "Workload",
        "Shape",
        f"{baseline_label} (ms)",
        f"{candidate_label} (ms)",
        "Delta (ms)",
        "Change",
        "Kernels",
        f"{baseline_label} solver",
        f"{baseline_label} kernel",
        f"{candidate_label} solver",
        f"{candidate_label} kernel",
    ]
    if include_system_db:
        headers.append("System DB")
    table_rows = []
    for row in rows:
        cells = [
            _cell(_workload_label(row)),
            _cell(_shape_label(row)),
            _fmt_ms(row.get("baseline_ms")),
            _fmt_ms(row.get("candidate_ms")),
            _fmt_ms(row.get("delta_ms")),
            _fmt_pct(row.get("speedup_pct")),
            _cell(_kernel_phrase(row["kernel_difference"])),
            _cell(_solver_cell(row.get("baseline_solver"))),
            _cell(_kernel_cell(row.get("baseline_solver"))),
            _cell(_solver_cell(row.get("candidate_solver"))),
            _cell(_kernel_cell(row.get("candidate_solver"))),
        ]
        if include_system_db:
            cells.append(_db_cell(row))
        table_rows.append(cells)
    return _md_table(headers, table_rows, numeric={2, 3, 4, 5})


def _workload_table(arm: dict[str, Any]) -> list[str]:
    headers = [
        "Workload",
        "Improved",
        "Worse",
        "Similar",
        "Failed",
        "Faster (ms)",
        "Slower (ms)",
        "Net (ms)",
        "Kernel changed",
    ]
    rows = []
    for item in arm["by_source"]:
        rows.append(
            [
                _cell(Path(item["source_file"]).name),
                str(item["improved"]),
                str(item["worse"]),
                str(item["similar"]),
                str(item["failed"]),
                _fmt_ms(item["ms_faster"]),
                _fmt_ms(item["ms_slower"]),
                _fmt_ms(item["net_delta_ms"]),
                str(item["kernel_changed"]),
            ]
        )
    return _md_table(headers, rows, numeric={1, 2, 3, 4, 5, 6, 7, 8})


def _version_table(report: dict[str, Any]) -> list[str]:
    baseline = report["baseline"]
    candidate = report["candidate"]
    base_hits = f"{baseline['system_db_hits']} / {baseline['commands']}"
    cand_hits = f"{candidate['system_db_hits']} / {candidate['commands']}"
    fields = [
        ("Run", baseline["run_id"], candidate["run_id"]),
        ("MIOpen", baseline["miopen_version"] or "n/a", candidate["miopen_version"] or "n/a"),
        ("ROCm", baseline["rocm_version"] or "n/a", candidate["rocm_version"] or "n/a"),
        ("HIP", baseline["hip_version"] or "n/a", candidate["hip_version"] or "n/a"),
        ("Docker image", baseline["docker_image"] or "n/a", candidate["docker_image"] or "n/a"),
        ("Hostname", baseline["hostname"] or "n/a", candidate["hostname"] or "n/a"),
        ("Timestamp (UTC)", baseline["timestamp_utc"] or "n/a", candidate["timestamp_utc"] or "n/a"),
        ("DB prefix", baseline["db_prefix"] or "n/a", candidate["db_prefix"] or "n/a"),
        ("System DB", baseline["system_db_path"] or "n/a", candidate["system_db_path"] or "n/a"),
        ("System DB hits", base_hits, cand_hits),
        ("Benchmark repeats", str(baseline["benchmark_repeats"]), str(candidate["benchmark_repeats"])),
        ("Run directory", baseline["run_dir"], candidate["run_dir"]),
    ]
    headers = ["", baseline["short_label"], candidate["short_label"]]
    rows = [[_cell(name), _cell(left), _cell(right)] for name, left, right in fields]
    return _md_table(headers, rows)


def _section_rows(
    lines: list[str],
    title: str,
    intro: str,
    rows: list[dict[str, Any]],
    baseline_label: str,
    candidate_label: str,
    include_system_db: bool,
) -> None:
    lines.extend(["", f"### {title}", "", intro, "", f"**Count**: {len(rows)}"])
    if rows:
        lines.append("")
        lines.extend(_detail_table(rows, baseline_label, candidate_label, include_system_db))
    else:
        lines.extend(["", "_None._"])


def _render_arm(lines: list[str], report: dict[str, Any], arm_name: str, title: str) -> None:
    arm = report[arm_name]
    baseline = report["baseline"]
    candidate = report["candidate"]
    threshold = report["threshold_pct"]
    include_system_db = arm_name == "production"
    lines.extend(["", f"## {title}", "", _net_sentence(candidate["label"], arm), "", _split_sentence(arm, threshold)])
    for kind in ("improved", "worse"):
        sentence = _breakdown_sentence(kind, arm)
        if sentence:
            lines.extend(["", sentence])
    if arm["counts"]["failed"]:
        lines.extend(["", f"**Failed**: {arm['counts']['failed']}"])
    if arm["improved"]:
        lines.extend(["", f"**Largest speedup**: {_highlight(arm['improved'][0], baseline['label'], candidate['label'])}"])
    if arm["worse"]:
        lines.extend(["", f"**Largest regression**: {_highlight(arm['worse'][0], baseline['label'], candidate['label'])}"])
    lines.extend(
        [
            "",
            "### By workload",
            "",
            "Each command is counted once in the summary above. A command listed in several "
            "workload files is included in each of those rows, so this table is not a partition "
            "of the summary. Net (ms) is baseline minus candidate across every compared shape "
            "in the file, including shapes within the threshold. Positive means the candidate is faster.",
            "",
        ]
    )
    if arm["by_source"]:
        lines.extend(_workload_table(arm))
    else:
        lines.append("_None._")

    faster = f"Candidate is faster by more than {threshold:.1f}%."
    if arm["improved"]:
        faster += (
            f" Together these shapes save {_fmt_ms(arm['ms_faster'])} ms. "
            "Sorted by milliseconds saved."
        )
    slower = f"Candidate is slower by more than {threshold:.1f}%."
    if arm["worse"]:
        slower += (
            f" Together these shapes add {_fmt_ms(arm['ms_slower'])} ms. "
            "Sorted by milliseconds added."
        )
    similar = (
        f"Median times stay within {threshold:.1f}%, and the kernel instance differs. "
        "Same-kernel rows inside the threshold are timing noise; they stay in the totals "
        "and in `report.json`."
    )
    _section_rows(
        lines,
        "Improved",
        faster,
        arm["improved"],
        baseline["short_label"],
        candidate["short_label"],
        include_system_db,
    )
    _section_rows(
        lines,
        "Worse",
        slower,
        arm["worse"],
        baseline["short_label"],
        candidate["short_label"],
        include_system_db,
    )
    different_kernel = [
        row for row in arm["similar"] if row["kernel_difference"] != "same_kernel"
    ]
    _section_rows(
        lines,
        "Similar speed, different kernel",
        similar,
        different_kernel,
        baseline["short_label"],
        candidate["short_label"],
        include_system_db,
    )
    if arm["failed"]:
        _section_rows(
            lines,
            "Failed",
            "One side has no usable median timing.",
            arm["failed"],
            baseline["short_label"],
            candidate["short_label"],
            include_system_db,
        )


def _render_unmatched(lines: list[str], report: dict[str, Any]) -> None:
    unmatched = report["unmatched"]
    rows = unmatched["baseline_only"] + unmatched["candidate_only"]
    if not rows:
        return
    lines.extend(
        [
            "",
            "## Commands present in one run",
            "",
            f"**Count**: {len(rows)}",
            "",
        ]
    )
    headers = ["Side", "Workload", "Shape", "Command"]
    table = []
    for row in rows:
        table.append(
            [
                _cell(row["side"]),
                _cell(_workload_label(row)),
                _cell(_shape_label(row)),
                _cell(row.get("command") or ""),
            ]
        )
    lines.extend(_md_table(headers, table))


def render_version_report_md(report: dict[str, Any]) -> str:
    baseline = report["baseline"]
    candidate = report["candidate"]
    threshold = report["threshold_pct"]
    lines = [
        "# MIOpen version comparison",
        "",
        f"Baseline is **{baseline['label']}** (`{baseline['run_id']}`). "
        f"Candidate is **{candidate['label']}** (`{candidate['run_id']}`).",
        "",
        "Shapes are joined on the MIOpenDriver command. Production is Arm A, the "
        "out-of-the-box path (empty user DB, installed system DB, then heuristics). "
        "Exhaustive is Arm B, the tuned upper bound. "
        f"`delta_ms` is {baseline['label']} minus {candidate['label']}. "
        f"A positive delta means {candidate['label']} is faster. "
        f"Change is that delta as a percent of the {baseline['label']} time. "
        f"The threshold is {threshold:.1f}%.",
        "",
        "Kernel identity follows the per-run report. ImplicitGEMM CK solvers "
        "(`ConvHipImplicitGemm*`) and dynamic IGEMM solvers "
        "(`ConvAsmImplicitGemmGTCDynamic*`) each cover many kernels; the instance "
        "is the perf config after `:`. Single-kernel solvers such as `GemmFwdRest` "
        "are identified by the solver name.",
        "",
        "## Summary",
        "",
        f"- **Production**: {_net_sentence(candidate['label'], report['production'])} "
        f"{_split_sentence(report['production'], threshold)}",
        f"- **Exhaustive**: {_net_sentence(candidate['label'], report['exhaustive'])} "
        f"{_split_sentence(report['exhaustive'], threshold)}",
    ]
    unmatched = report["unmatched"]
    unmatched_count = len(unmatched["baseline_only"]) + len(unmatched["candidate_only"])
    if unmatched_count:
        lines.append(f"- **Commands in only one run**: {unmatched_count}")
    if report["stack_differences"]:
        lines.extend(
            [
                "",
                "These runs also differ outside the MIOpen version string: "
                + "; ".join(report["stack_differences"])
                + ". Timing differences belong to the whole stack recorded for each run.",
            ]
        )
    lines.extend(["", "## Versions", ""])
    lines.extend(_version_table(report))
    if report["production"].get("by_source") is not None:
        lines.extend(
            [
                "",
                "On the production table, System DB is "
                f"{baseline['short_label']} / {candidate['short_label']} "
                "(hit means the shape was present in that run's installed system performance DB).",
            ]
        )
    _render_arm(lines, report, "production", "Production path")
    _render_arm(lines, report, "exhaustive", "Exhaustive path")
    _render_unmatched(lines, report)
    output_dir = report.get("output_dir")
    lines.extend(["", "## Attachments", ""])
    if output_dir:
        lines.append(f"- `{Path(output_dir) / 'report.json'}` (every shape, both arms, untruncated solvers)")
    else:
        lines.append("- `report.json` (every shape, both arms, untruncated solvers)")
    lines.append("")
    return "\n".join(lines)


def write_version_report(output_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["output_dir"] = str(output_dir.resolve())
    md_path = output_dir / "report.md"
    json_path = output_dir / "report.json"
    md_path.write_text(render_version_report_md(payload), encoding="utf-8")
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return md_path, json_path
