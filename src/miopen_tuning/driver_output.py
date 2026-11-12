# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Parse MIOpenDriver and MIOpen log output into structured events."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


_KIND_TO_DIRECTION = {"Forward": "F", "Backward Data": "B", "Backward Weights": "W"}
_TIMING_RE = re.compile(
    r"GPU Kernel Time (?P<kind>Forward|Backward Data|Backward Weights) Conv\. Elapsed:\s+(?P<ms>[\d.]+)\s+ms"
)
_SOLUTION_RE = re.compile(
    r"MIOpen (?P<kind>Forward|Backward Data|Backward Weights) Conv\. Algorithm:\s+(?P<algorithm>-?\d+)"
    r"(?:,\s+Solution:\s+(?P<solution>\S.*?))?\s*$"
)
_CANDIDATE_RE = re.compile(r"\]\s+(?P<solver>[^:\s]+):\s+Candidate Selection selected:\s+(?P<config>.+?)\s*$")
_HEURISTIC_RE = re.compile(r"Hard-coded heuristics selected kernel:\s+(?P<config>.+?)(?:\s+at index:\s+\d+)?\s*$")
_GET_VALUES_RE = re.compile(r"\[GetValues\]\s+(?P<key>\S+)=(?P<solver>[^:\s]+):(?P<config>.+?)\s*$")
_FIND_SOLUTION_RE = re.compile(r"\[FindSolutionImpl\]\s+(?P<solver>[A-Za-z]\w*)")
_RECORD_MISSING_RE = re.compile(r"record not found for:\s+(?P<solver>[A-Za-z]\w*)")
_CHOSEN_RE = re.compile(r"Chosen Algorithm:\s*(?P<solver>[^,]+?)\s*,")


def solver_name(solution: str | None) -> str | None:
    if not solution:
        return None
    token = solution.split(";", 1)[0].strip()
    head, separator, tail = token.partition("/")
    if separator and head.isdigit():
        token = tail
    return token.split(":", 1)[0].strip() or None


def _json_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    performance = payload.get("performance")
    if isinstance(performance, dict):
        results = performance.get("results") if isinstance(performance.get("results"), dict) else {}
        events.append(
            {
                "event": "performance_record",
                "direction": str(performance.get("direction") or ""),
                "algorithm": performance.get("algorithm"),
                "solution": performance.get("solution"),
                "solver": solver_name(str(performance.get("solution") or "")),
                "average_time_ms": results.get("average_time_ms"),
            }
        )
    if "solution" in payload and isinstance(payload.get("performance_configs"), list):
        for config in payload["performance_configs"]:
            if not isinstance(config, dict):
                continue
            events.append(
                {
                    "event": "performance_config",
                    "solver": solver_name(str(payload.get("solution") or "")),
                    "config": config.get("config_descriptor") or config.get("config_name"),
                }
            )
    return events


def parse_line(line: str, *, source: str, line_number: int) -> list[dict[str, Any]]:
    text = line.rstrip("\n")
    stripped = text.strip()
    events: list[dict[str, Any]] = []
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            events.extend(_json_events(payload))

    match = _TIMING_RE.search(text)
    if match:
        events.append(
            {
                "event": "timing",
                "direction": _KIND_TO_DIRECTION[match.group("kind")],
                "time_ms": float(match.group("ms")),
            }
        )
    match = _SOLUTION_RE.search(text)
    if match:
        solution = match.group("solution")
        events.append(
            {
                "event": "solution",
                "direction": _KIND_TO_DIRECTION[match.group("kind")],
                "algorithm": int(match.group("algorithm")),
                "solution": solution,
                "solver": solver_name(solution),
            }
        )
    match = _CANDIDATE_RE.search(text)
    if match:
        events.append({"event": "kernel_selected", "selection": "candidate", **match.groupdict()})
    match = _HEURISTIC_RE.search(text)
    if match:
        events.append({"event": "kernel_selected", "selection": "heuristic", "config": match.group("config")})
    match = _GET_VALUES_RE.search(text)
    if match:
        events.append({"event": "db_record_loaded", **match.groupdict()})
    match = _FIND_SOLUTION_RE.search(text)
    if match:
        events.append({"event": "solver_considered", "solver": match.group("solver")})
    match = _RECORD_MISSING_RE.search(text)
    if match:
        events.append({"event": "db_record_missing", "solver": match.group("solver")})
    match = _CHOSEN_RE.search(text)
    if match:
        events.append({"event": "solution", "solver": match.group("solver").strip(), "source_kind": "chosen_algorithm"})
    marker = text.find("MIOpenDriver ")
    if marker >= 0:
        events.append({"event": "driver_command", "command": text[marker:].strip()})

    lowered = stripped.lower()
    if "error" in lowered and not any(event["event"] == "performance_record" for event in events):
        events.append({"event": "error", "message": stripped})
    elif "warning" in lowered:
        events.append({"event": "warning", "message": stripped})

    for event in events:
        event["source"] = source
        event["line"] = line_number
    return events


def parse_log_files(paths: Iterable[Path]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        with path.open(errors="replace") as handle:
            for line_number, line in enumerate(handle, 1):
                events.extend(parse_line(line, source=path.name, line_number=line_number))
    return events


def summarize_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "event_count": 0,
        "solutions": [],
        "selected_solver": None,
        "timings_ms": [],
        "errors": 0,
        "warnings": 0,
    }
    for event in events:
        summary["event_count"] += 1
        kind = event.get("event")
        if kind in {"solution", "performance_record"} and event.get("solver"):
            summary["solutions"].append(event["solver"])
            summary["selected_solver"] = event["solver"]
        elif kind == "timing":
            summary["timings_ms"].append(event["time_ms"])
        elif kind == "error":
            summary["errors"] += 1
        elif kind == "warning":
            summary["warnings"] += 1
    return summary
