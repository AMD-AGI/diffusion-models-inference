# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Render MIOpen tuning run reports."""

from __future__ import annotations

from typing import Any


def _escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _diff_counts(diff: dict[str, Any]) -> tuple[int, int, int]:
    added = sum(len(item.get("added", [])) for item in diff.values())
    replaced = sum(len(item.get("replaced", [])) for item in diff.values())
    removed = sum(len(item.get("removed", [])) for item in diff.values())
    return added, replaced, removed


def summary_markdown(report: dict[str, Any]) -> str:
    identity = report["identity"]
    plan = report["plan"]["counts"]
    tuning = report["tuning"]
    added, replaced, removed = _diff_counts(report["db"]["diff"])
    contracts = report["contracts"]
    rows = [
        ("Result", report["status"]),
        ("Image", identity.get("image") or "not recorded"),
        ("MIOpen", f"{identity.get('miopen_version', '')} ({identity.get('miopen_build', '')})"),
        ("Device", f"{', '.join(identity.get('device_names', [])) or 'unknown'} / `{identity.get('db_prefix', '')}`"),
        ("Contracts", contracts["status"]),
        ("Workloads", f"{report['workloads']['command_count']} commands, {report['workloads']['unique_problem_count']} unique problems"),
        ("Plan", f"{plan['tune']} tune, {plan['skip']} already tuned, {plan['reject']} rejected"),
        ("Tuning", f"{tuning['succeeded']} succeeded, {tuning['failed']} failed"),
        ("DB changes", f"{added} added, {replaced} replaced, {removed} removed"),
        ("DB check", "valid" if report["db"]["validation"]["valid"] else "invalid"),
    ]
    lines = ["## MIOpen tuning", "", "| Field | Value |", "| --- | --- |"]
    lines.extend(f"| {name} | {_escape(value)} |" for name, value in rows)
    if report["errors"]:
        lines.extend(["", "### Required actions", ""])
        lines.extend(f"- {_escape(error)}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def report_markdown(report: dict[str, Any]) -> str:
    lines = [summary_markdown(report).rstrip(), ""]
    failed = [entry for entry in report["tuning"]["results"] if not entry["succeeded"]]
    lines.extend(["## Failed tuning commands", ""])
    if failed:
        lines.extend(["| Command ID | GPU | Exit | Log | Sources |", "| --- | --- | ---: | --- | --- |"])
        for entry in failed:
            sources = ", ".join(f"{source['path']}:{source['line']}" for source in entry["sources"])
            lines.append(
                f"| `{entry['command_id']}` | {entry['device_id']} | {entry['exit_code']} | "
                f"`{entry['log_dir']}` | {_escape(sources)} |"
            )
    else:
        lines.append("None.")

    lines.extend(["", "## Rejected workload commands", ""])
    rejected = [entry for entry in report["plan"]["entries"] if entry["action"] == "reject"]
    if rejected:
        for entry in rejected:
            source = entry["source"]
            lines.append(f"- `{source['path']}:{source['line']}`: {_escape(entry['reasons'][0])}")
    else:
        lines.append("None.")

    lines.extend(["", "## Database changes", ""])
    for suffix, diff in report["db"]["diff"].items():
        lines.append(
            f"- `{suffix}`: {len(diff['added'])} added, {len(diff['replaced'])} replaced, "
            f"{len(diff['removed'])} removed"
        )
    missing = report["db"].get("missing_after_tuning", [])
    lines.extend(["", "## Missing records after successful tuning", ""])
    if missing:
        lines.extend(f"- `{entry['command_id']}`: {', '.join(entry['missing'])}" for entry in missing)
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def annotations(report: dict[str, Any]) -> list[str]:
    messages = [f"::error title=MIOpen tuning::{error}" for error in report["errors"]]
    for entry in report["plan"]["entries"]:
        if entry["action"] == "reject":
            source = entry["source"]
            messages.append(
                f"::error file={source['path']},line={source['line']},title=Invalid MIOpen workload::{entry['reasons'][0]}"
            )
    return messages
