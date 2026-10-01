"""Parse MIOpenDriver stdout for timing and solver metadata."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional


FORWARD_TIME_RE = re.compile(
    r"GPU Kernel Time Forward Conv\. Elapsed:\s+([\d.]+)\s+ms"
)
BACKWARD_DATA_TIME_RE = re.compile(
    r"GPU Kernel Time Backward Data Conv\. Elapsed:\s+([\d.]+)\s+ms"
)
BACKWARD_WRW_TIME_RE = re.compile(
    r"GPU Kernel Time Backward Weights Conv\. Elapsed:\s+([\d.]+)\s+ms"
)
FORWARD_ALGO_RE = re.compile(r"MIOpen Forward Conv\. Algorithm:\s+(\d+)")
BACKWARD_DATA_ALGO_RE = re.compile(
    r"MIOpen Backward Data Conv\. Algorithm:\s+(\d+)"
)
BACKWARD_WRW_ALGO_RE = re.compile(
    r"MIOpen Backward Weights Conv\. Algorithm:\s+(\d+)"
)
# Driver stdout (not the MIOpen logger). Printed for both immediate mode and Find
# when -t 1 is set: "Algorithm: 5, Solution: 98/ConvMlirIgemmFwd".
SOLUTION_LINE_RE = re.compile(
    r"MIOpen (?P<kind>Forward|Backward Data|Backward Weights) Conv\. Algorithm:\s+-?\d+,\s+Solution:\s+(?P<solution>\S.*?)\s*$",
    re.MULTILINE,
)
_KIND_TO_DIRECTION = {
    "Forward": "F",
    "Backward Data": "B",
    "Backward Weights": "W",
}
# MIOPEN_PERFORMANCE_LOGS prints a JSON object instead of the text
# "GPU Kernel Time ... Elapsed" / "Solution:" lines.
_JSON_DIRECTION = {
    "forward": "F",
    "backward": "B",
    "backward data": "B",
    "bwd": "B",
    "backward weights": "W",
    "wrw": "W",
}


@dataclass
class ParsedDriverOutput:
    time_ms: Optional[float]
    algorithm_id: Optional[str]
    solver_hint: Optional[str]
    direction: Optional[str]


_CANDIDATE_RE = re.compile(
    r"\]\s+(?P<solver>[^:\s]+):\s+Candidate Selection selected:\s+(?P<config>.+?)\s*$"
)
_HEURISTIC_KERNEL_RE = re.compile(
    r"Hard-coded heuristics selected kernel:\s+(?P<config>.+?)"
    r"(?:\s+at index:\s+\d+)?\s*$"
)
_GET_VALUES_RE = re.compile(
    r"\[GetValues\]\s+\S+=(?P<solver>[^:\s]+):(?P<config>.+?)\s*$"
)
_FIND_SOLUTION_RE = re.compile(
    r"\[FindSolutionImpl\]\s+(?P<solver>[A-Za-z][\w]*)\s*(?:\(.*\))?\s*$"
)
_RECORD_NOT_FOUND_RE = re.compile(
    r"record not found for:\s+(?P<solver>[A-Za-z][\w]*)"
)
_CHOSEN_ALGO_RE = re.compile(r"Chosen Algorithm:\s*(?P<solver>[^,]+?)\s*,")


def _hint_solver_name(solver_hint: str | None) -> str | None:
    if not solver_hint:
        return None
    token = solver_hint.split(";", 1)[0].strip()
    slash = token.find("/")
    if slash > 0 and token[:slash].isdigit() and ":" not in token[:slash]:
        token = token[slash + 1 :]
    return token.split(":", 1)[0].strip() or None


def _kernels_from_info_log(text: str) -> dict[str, str]:
    """Map solver name to the perf config printed at ``MIOPEN_LOG_LEVEL=5``.

    Three lines carry that config. ``Candidate Selection selected:`` is the AI
    heuristic. ``Hard-coded heuristics selected kernel:`` is the fallback when
    the performance DB has no record. ``GetValues`` is the record that was
    loaded and then run. The last line for a solver is the one find used.
    """
    by_solver: dict[str, str] = {}
    current_solver: str | None = None
    for line in text.splitlines():
        found = _FIND_SOLUTION_RE.search(line)
        if found:
            current_solver = found.group("solver")
        missing = _RECORD_NOT_FOUND_RE.search(line)
        if missing:
            current_solver = missing.group("solver")
        loaded = _GET_VALUES_RE.search(line)
        if loaded and loaded.group("config").strip():
            by_solver[loaded.group("solver")] = loaded.group("config").strip()
        candidate = _CANDIDATE_RE.search(line)
        if candidate and candidate.group("config").strip():
            by_solver[candidate.group("solver")] = candidate.group("config").strip()
        heuristic = _HEURISTIC_KERNEL_RE.search(line)
        if heuristic and current_solver and heuristic.group("config").strip():
            by_solver[current_solver] = heuristic.group("config").strip()
    return by_solver


def _kernel_from_info_log(text: str, solver_hint: str | None) -> str | None:
    """Perf config from ``MIOPEN_LOG_LEVEL=5`` info lines for the solver that ran."""
    chosen = _hint_solver_name(solver_hint)
    if chosen is None:
        match = _CHOSEN_ALGO_RE.search(text)
        if match:
            chosen = match.group("solver").strip()
    by_solver = _kernels_from_info_log(text)
    if chosen is not None:
        return by_solver.get(chosen)
    if not by_solver:
        return None
    return next(reversed(by_solver.values()))


def _kernel_from_performance_log(stderr: str, solver_hint: str | None) -> str | None:
    """Kernel instance from ``MIOPEN_PERFORMANCE_LOGS`` JSON, if MIOpen printed one.

    The driver Solution line is only ``id/Name``. The perf-config descriptor in
    the JSON is the kernel instance (the same text the performance DB stores
    after ``:``). Level 1 logs the executed solution and does not change find.
    """
    hint_name = _hint_solver_name(solver_hint)

    matched: str | None = None
    fallback: str | None = None
    for line in stderr.splitlines():
        line = line.strip()
        if not line.startswith('{"solution":'):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        solution = str(payload.get("solution") or "").strip()
        for config in payload.get("performance_configs") or []:
            if not isinstance(config, dict):
                continue
            descriptor = str(config.get("config_descriptor") or "").strip()
            config_name = str(config.get("config_name") or "").strip()
            kernel = descriptor or (config_name if config_name and config_name != solution else "")
            if not kernel:
                continue
            fallback = kernel
            if hint_name is None or solution == hint_name:
                matched = kernel
    return matched or fallback


def _with_kernel(solver_hint: str | None, kernel: str | None) -> str | None:
    if not kernel:
        return solver_hint
    if solver_hint is None:
        return kernel
    token = solver_hint.split(";", 1)[0]
    if ":" in token:
        return solver_hint
    return f"{solver_hint}:{kernel}"


def _solution_for_direction(text: str, direction: str) -> Optional[str]:
    """Return the last Solution id/name printed for this convolution direction."""
    matches = [
        match.group("solution").strip()
        for match in SOLUTION_LINE_RE.finditer(text)
        if _KIND_TO_DIRECTION.get(match.group("kind")) == direction
    ]
    if not matches:
        return None
    return matches[-1]


def _performance_record(text: str, direction: str) -> dict | None:
    """Last ``{"performance": ...}`` object for this convolution direction."""
    chosen: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{") or '"performance"' not in line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        perf = payload.get("performance")
        if not isinstance(perf, dict):
            continue
        raw = str(perf.get("direction") or "").strip().lower()
        if _JSON_DIRECTION.get(raw) == direction:
            chosen = perf
    return chosen


def _direction_from_command(command: str) -> str:
    match = re.search(r"(?:^|\s)-F\s+(\d+)", command)
    if not match:
        return "F"
    return {"1": "F", "2": "B", "4": "W"}.get(match.group(1), "F")


def parse_driver_output(command: str, stdout: str, stderr: str = "") -> ParsedDriverOutput:
    text = stdout + "\n" + stderr
    direction = _direction_from_command(command)

    if direction == "F":
        time_match = FORWARD_TIME_RE.search(text)
        algo_match = FORWARD_ALGO_RE.search(text)
    elif direction == "B":
        time_match = BACKWARD_DATA_TIME_RE.search(text)
        algo_match = BACKWARD_DATA_ALGO_RE.search(text)
    else:
        time_match = BACKWARD_WRW_TIME_RE.search(text)
        algo_match = BACKWARD_WRW_ALGO_RE.search(text)

    time_ms = float(time_match.group(1)) if time_match else None
    algorithm_id = algo_match.group(1) if algo_match else None
    solver_hint = _solution_for_direction(text, direction)
    record = _performance_record(text, direction)
    if record is not None:
        results = record.get("results") if isinstance(record.get("results"), dict) else {}
        if time_ms is None and results.get("average_time_ms") is not None:
            time_ms = float(results["average_time_ms"])
        if algorithm_id is None and record.get("algorithm") is not None:
            algorithm_id = str(record["algorithm"])
        if solver_hint is None and record.get("solution"):
            solver_hint = str(record["solution"]).strip()
    kernel = _kernel_from_performance_log(stderr, solver_hint) or _kernel_from_info_log(
        text, solver_hint
    )
    solver_hint = _with_kernel(solver_hint, kernel)

    return ParsedDriverOutput(
        time_ms=time_ms,
        algorithm_id=algorithm_id,
        solver_hint=solver_hint,
        direction=direction,
    )
