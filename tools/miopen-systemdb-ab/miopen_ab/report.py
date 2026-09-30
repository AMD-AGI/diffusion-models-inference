"""Generate markdown and JSON reports from comparison results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _fmt_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ")


def _clip(value: str | None, limit: int) -> str:
    text = value or "n/a"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _shape_label(entry: dict[str, Any]) -> str:
    shape = entry.get("shape") or {}
    if not shape:
        return _clip(entry.get("command"), 72)
    if shape.get("in_d") is not None:
        spatial = f"{shape['in_d']}x{shape['in_h']}x{shape['in_w']}"
        kernel = f"{shape.get('fil_d')}x{shape['fil_h']}x{shape['fil_w']}"
    else:
        spatial = f"{shape['in_h']}x{shape['in_w']}"
        kernel = f"{shape['fil_h']}x{shape['fil_w']}"
    layout = shape.get("in_layout") or ""
    return (
        f"n{shape['batchsize']} c{shape['in_channels']} {spatial} "
        f"k{shape['out_channels']} {kernel} "
        f"{shape['direction']} {shape['precision']} {layout}"
    ).strip()


def _workload_label(entry: dict[str, Any]) -> str:
    files = entry.get("source_files") or []
    if not files:
        return ""
    return ", ".join(Path(path).name for path in files)


def _unique_devices(ids: list[str] | None) -> str:
    seen: list[str] = []
    for device_id in ids or []:
        if device_id and device_id not in seen:
            seen.append(device_id)
    return ",".join(seen) if seen else "n/a"


def _gpu_label(entry: dict[str, Any]) -> str:
    label = (
        f"A {_unique_devices(entry.get('arm_a_device_ids'))} / "
        f"B {_unique_devices(entry.get('arm_b_device_ids'))}"
    )
    tune = entry.get("arm_b_tune_device") or ""
    if tune:
        label += f" / tune {tune}"
    return label


def _detail_rows(entries: list[dict[str, Any]]) -> list[str]:
    rows = [
        "| Workload | Shape | GPUs | Arm A (ms) | Arm B (ms) | Delta (ms) | Speedup | Arm A solver | Arm B solver |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for entry in entries:
        cells = [
            _cell(_workload_label(entry)),
            _cell(_shape_label(entry)),
            _cell(_gpu_label(entry)),
            _fmt_ms(entry.get("arm_a_median_ms")),
            _fmt_ms(entry.get("arm_b_median_ms")),
            _fmt_ms(entry.get("delta_ms")),
            _fmt_pct(entry.get("speedup_pct")),
            _cell(_clip(entry.get("arm_a_solver"), 48)),
            _cell(_clip(entry.get("arm_b_solver"), 48)),
        ]
        rows.append("| " + " | ".join(cells) + " |")
    return rows


def _section(lines: list[str], title: str, entries: list[dict[str, Any]]) -> None:
    lines.extend(["", f"## {title}", ""])
    lines.append(f"**Count**: {len(entries)}")
    if entries:
        lines.append("")
        lines.extend(_detail_rows(entries))
    else:
        lines.append("")
        lines.append("_None._")


def render_report_md(
    metadata: dict[str, Any],
    comparison: dict[str, Any],
    output_dir: Path,
) -> str:
    parity = comparison.get("parity_counts") or {}
    config = metadata.get("experiment_config", {})
    threshold = comparison.get("threshold_pct")
    system_db_path = comparison.get("system_db_path") or comparison.get("system_udb_path")
    misses = comparison.get("system_db_misses", [])
    recorded = comparison.get("entries") or []
    if recorded:
        system_db_hits = f"{len(recorded) - len(misses)} / {len(misses)}"
    else:
        system_db_hits = f"n/a / {len(misses)}"
    faster = comparison.get("different_solver_production_slower", [])
    similar = comparison.get("different_solver_similar", [])
    slower = comparison.get("different_solver_exhaustive_slower", [])
    same_solver_count = comparison.get("same_solver_count")
    if same_solver_count is None:
        same_solver_count = len(comparison.get("same_solver", []))

    lines = [
        "# MIOpen System DB vs Exhaustive Tuning Report",
        "",
        "## Summary",
        "",
        f"- **Total commands**: {config.get('command_count', comparison.get('primary_ab_count', 'n/a'))}",
        f"- **Same solver**: {same_solver_count}",
        f"- **Different solver, exhaustive faster**: {len(faster)}",
        f"- **Milliseconds left on the table**: {_fmt_ms(comparison.get('different_solver_ms_left_on_table'))}",
        f"- **Different solver, similar speed** (within {threshold}%): {len(similar)}",
        f"- **Different solver, exhaustive slower**: {len(slower)}",
        f"- **Failed**: {parity.get('failed', len(comparison.get('failures', [])))}",
        f"- **System DB hits / misses**: {system_db_hits}",
        "",
        "The tables below include only shapes where exhaustive search and the production",
        "heuristic recorded different solver names. When the names match, there is no",
        "reason to expect a different kernel time; any median gap is timing noise and is",
        "omitted here. `delta_ms` is Arm A − Arm B. Milliseconds left on the table sums",
        "that gap over different-solver shapes where exhaustive was faster than the",
        f"{threshold}% threshold. Full timings, including same-solver rows, GPU ids for",
        "each repeat, and untruncated solver strings, are in `comparison.json`.",
        "Names in the tables below are shortened.",
        "",
        "## By workload file",
        "",
    ]

    by_source = comparison.get("by_source") or []
    if by_source:
        lines.extend(
            [
                "| Workload | Same solver | Exhaustive faster | Similar | Exhaustive slower | Failed | ms left on table |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in by_source:
            lines.append(
                "| {name} | {same} | {faster} | {similar} | {slower} | {failed} | {ms} |".format(
                    name=_cell(Path(row["source_file"]).name),
                    same=row.get("same_solver", 0),
                    faster=row.get("different_solver_production_slower", 0),
                    similar=row.get("different_solver_similar", 0),
                    slower=row.get("different_solver_exhaustive_slower", 0),
                    failed=row.get("failed", 0),
                    ms=_fmt_ms(row.get("different_solver_ms_left_on_table")),
                )
            )
    else:
        lines.append("_No workload-file grouping recorded._")

    lines.extend(
        [
            "",
            "## Environment",
            "",
            f"- **Timestamp (UTC)**: {metadata.get('timestamp_utc', 'n/a')}",
            f"- **Docker image**: {metadata.get('docker_image', 'n/a')}",
            f"- **Hostname**: {metadata.get('hostname', 'n/a')}",
            f"- **HIP_VISIBLE_DEVICES**: {metadata.get('hip_visible_devices', 'n/a')}",
            f"- **DB prefix**: {metadata.get('db_prefix', 'n/a')}",
            f"- **ROCm version**: {metadata.get('rocm_version', 'n/a')}",
            f"- **HIP version**: {metadata.get('hip_version', 'n/a')}",
            f"- **MIOpenDriver version**: {metadata.get('miopen_driver_version', 'n/a')}",
            f"- **Kernel cache**: {metadata.get('kernel_cache_dir', 'n/a')}",
            f"- **System DB path**: {system_db_path or 'n/a'}",
            "",
            "## Methodology",
            "",
            f"- **Threshold**: {threshold}% relative median timing difference",
            f"- **Benchmark repeats**: {comparison.get('benchmark_repeats')} (median reported)",
            "- **Arm A**: out-of-the-box path (`MIOPEN_FIND_ENFORCE=1`, default find mode, empty user DB, system DB enabled)",
            "- **Arm A measurement**: MIOpenDriver inline timing (`-t 1`) without forced incremental tuning",
            "- **Arm B tuning**: exhaustive override (`MIOPEN_FIND_ENFORCE=4` SEARCH_DB_UPDATE, `MIOPEN_FIND_MODE=1`, `MIOPEN_SYSTEM_DB_PATH=$MIOPEN_USER_DB_PATH`)",
            "- **Arm B benchmark**: `MIOPEN_FIND_ENFORCE=1` and default find mode, reading the merged exhaustive user DB",
            "- **Shared kernel cache** across arms (default `~/.cache/miopen`)",
            "- **`MIOPEN_DEBUG_CONV_DIRECT=0`** on all arms (naive direct conv solvers excluded from find/tune)",
            "- **System DB miss** is recorded on each entry (`in_system_db`) and does not replace the timing comparison",
        ]
    )

    _section(lines, "Different solver, exhaustive faster", faster)
    _section(lines, "Different solver, similar speed", similar)
    _section(lines, "Different solver, exhaustive slower", slower)

    lines.extend(["", "## Failures / arch mismatch", ""])
    failures = comparison.get("failures", [])
    lines.append(f"**Count**: {len(failures)}")
    if failures:
        lines.append("")
        for entry in failures:
            note = "; ".join(entry.get("notes") or [])
            command = entry["command"]
            if len(command) > 120:
                command = command[:117] + "..."
            lines.append(f"- `{command}` ({entry['outcome']}{': ' + note if note else ''})")

    lines.extend(
        [
            "",
            "## Attachments",
            "",
            f"- `{output_dir / 'comparison.json'}` (full per-command record)",
            f"- `{output_dir / 'report.json'}`",
            f"- `{output_dir / 'metadata.json'}`",
            f"- `{output_dir / 'artifacts.json'}`",
            f"- `{output_dir / 'arm_a/results.jsonl'}`",
            f"- `{output_dir / 'arm_b/results.jsonl'}`",
            f"- `{output_dir / 'arm_a/logs/'}` (Arm A benchmark logs)",
            f"- `{output_dir / 'arm_b/tuning/'}` (per-GPU exhaustive tuning DBs)",
            f"- `{output_dir / 'arm_b/tuning_merged/'}` (merged exhaustive user DB)",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    output_dir: Path,
    metadata: dict[str, Any],
    comparison: dict[str, Any],
) -> tuple[Path, Path]:
    md_path = output_dir / "report.md"
    json_path = output_dir / "report.json"

    md_content = render_report_md(metadata, comparison, output_dir)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(md_content)

    summary = {
        "metadata": metadata,
        "summary": comparison.get("counts", {}),
        "parity_counts": comparison.get("parity_counts", {}),
        "ms_left_on_table": comparison.get("ms_left_on_table"),
        "different_solver_ms_left_on_table": comparison.get("different_solver_ms_left_on_table"),
        "same_solver_count": comparison.get("same_solver_count"),
        "threshold_pct": comparison.get("threshold_pct"),
        "benchmark_repeats": comparison.get("benchmark_repeats"),
        "system_db_path": comparison.get("system_db_path") or comparison.get("system_udb_path"),
        "by_source": comparison.get("by_source", []),
        "different_solver_production_slower": comparison.get("different_solver_production_slower", []),
        "different_solver_similar": comparison.get("different_solver_similar", []),
        "different_solver_exhaustive_slower": comparison.get("different_solver_exhaustive_slower", []),
        "same_solver": comparison.get("same_solver", []),
        "production_slower": comparison.get("production_slower", []),
        "equal": comparison.get("equal", []),
        "exhaustive_slower": comparison.get("exhaustive_slower", []),
        "failures": comparison.get("failures", []),
        "system_db_miss_count": len(comparison.get("system_db_misses", [])),
        "improvements": comparison.get("improvements", []),
        "regressions": comparison.get("regressions", []),
        "no_change_count": len(comparison.get("no_change", [])),
        "failure_count": len(comparison.get("failures", [])),
    }
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return md_path, json_path
