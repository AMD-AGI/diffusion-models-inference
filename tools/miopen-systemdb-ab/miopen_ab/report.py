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


def _table_rows(entries: list[dict[str, Any]]) -> list[str]:
    rows = [
        "| Command | Arm A (ms) | Arm B (ms) | Speedup | Arm A solver | Arm B solver | System DB solver |",
        "| --- | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for entry in entries:
        cmd = entry["command"]
        if len(cmd) > 100:
            cmd = cmd[:97] + "..."
        rows.append(
            "| `{cmd}` | {a} | {b} | {speedup} | {sa} | {sb} | {ss} |".format(
                cmd=cmd,
                a=_fmt_ms(entry.get("arm_a_median_ms")),
                b=_fmt_ms(entry.get("arm_b_median_ms")),
                speedup=_fmt_pct(entry.get("speedup_pct")),
                sa=(entry.get("arm_a_solver") or "n/a")[:60],
                sb=(entry.get("arm_b_solver") or "n/a")[:60],
                ss=(entry.get("system_db_solver") or "n/a")[:60],
            )
        )
    return rows


def render_report_md(
    metadata: dict[str, Any],
    comparison: dict[str, Any],
    output_dir: Path,
) -> str:
    counts = comparison["counts"]
    config = metadata.get("experiment_config", {})

    lines = [
        "# MIOpen System DB vs Exhaustive Tuning Report",
        "",
        "## Summary",
        "",
        f"- **Total commands**: {config.get('command_count', 'n/a')}",
        f"- **Primary A/B comparisons**: {comparison.get('primary_ab_count', 0)}",
        f"- **Improvements** (exhaustive faster than production heuristics): {counts.get('improvement', 0)}",
        f"- **No change**: {counts.get('no_change', 0)}",
        f"- **Regressions** (solver changed + slower): {counts.get('regression', 0)}",
        f"- **System DB misses** (shape absent from installed system UDB; still compared): {counts.get('system_db_miss', 0)}",
        f"- **Failures / arch errors**: {counts.get('failure', 0) + counts.get('arch_mismatch_or_error', 0)}",
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
        f"- **System UDB path**: {comparison.get('system_udb_path', 'n/a')}",
        "",
        "## Methodology",
        "",
        f"- **Threshold**: {comparison.get('threshold_pct')}% relative median timing difference",
        f"- **Benchmark repeats**: {comparison.get('benchmark_repeats')} (median reported)",
        "- **Arm A**: production inference path (`MIOPEN_FIND_ENFORCE=1`, default find mode, prebuilt user DB, system DB enabled)",
        "- **Arm A measurement**: MIOpenDriver inline timing (`-t 1`) without forced incremental tuning",
        "- **Arm B tuning**: exhaustive override (`MIOPEN_FIND_ENFORCE=3`, `MIOPEN_SYSTEM_DB_PATH=$MIOPEN_USER_DB_PATH`)",
        "- **Arm B benchmark**: `MIOPEN_FIND_ENFORCE=1` with merged exhaustive user DB",
        "- **Regression rule**: counted only when solver changed AND exhaustive median is slower beyond threshold",
        "- **Shared kernel cache** across arms (default `~/.cache/miopen`)",
        "- **`MIOPEN_DEBUG_CONV_DIRECT=0`** on all arms (naive direct conv solvers excluded from find/tune)",
        "",
        "## Improvements (exhaustive faster than production heuristics)",
        "",
    ]

    improvements = comparison.get("improvements", [])
    if improvements:
        lines.extend(_table_rows(improvements))
    else:
        lines.append("_None detected._")

    lines.extend(["", "## Regressions", ""])
    regressions = comparison.get("regressions", [])
    if regressions:
        lines.extend(_table_rows(regressions))
    else:
        lines.append("_None detected._")

    lines.extend(["", "## No change", ""])
    no_change = comparison.get("no_change", [])
    lines.append(f"**Count**: {len(no_change)}")
    if no_change:
        lines.append("")
        lines.extend(_table_rows(no_change))

    lines.extend(["", "## System DB misses", ""])
    misses = comparison.get("system_db_misses", [])
    lines.append(f"**Count**: {len(misses)}")
    if misses:
        lines.append("")
        for entry in misses:
            lines.append(f"- `{entry['command']}`")

    lines.extend(["", "## Failures / arch mismatch", ""])
    failures = comparison.get("failures", [])
    lines.append(f"**Count**: {len(failures)}")
    if failures:
        lines.append("")
        for entry in failures:
            note = "; ".join(entry.get("notes") or [])
            lines.append(f"- `{entry['command']}` ({entry['outcome']}{': ' + note if note else ''})")

    lines.extend(
        [
            "",
            "## Attachments",
            "",
            f"- `{output_dir / 'comparison.json'}`",
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
        "summary": comparison["counts"],
        "threshold_pct": comparison["threshold_pct"],
        "benchmark_repeats": comparison["benchmark_repeats"],
        "system_udb_path": comparison.get("system_udb_path"),
        "improvements": comparison.get("improvements", []),
        "regressions": comparison.get("regressions", []),
        "no_change_count": len(comparison.get("no_change", [])),
        "system_db_miss_count": len(comparison.get("system_db_misses", [])),
        "failure_count": len(comparison.get("failures", [])),
    }
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")

    return md_path, json_path
