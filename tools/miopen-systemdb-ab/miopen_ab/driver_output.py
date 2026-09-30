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


@dataclass
class ParsedDriverOutput:
    time_ms: Optional[float]
    algorithm_id: Optional[str]
    solver_hint: Optional[str]
    direction: Optional[str]


def _kernel_from_performance_log(stderr: str, solver_hint: str | None) -> str | None:
    """Kernel instance from ``MIOPEN_PERFORMANCE_LOGS`` JSON, if MIOpen printed one.

    The driver Solution line is only ``id/Name``. The perf-config descriptor in
    the JSON is the kernel instance (the same text the performance DB stores
    after ``:``). Level 1 logs the executed solution and does not change find.
    """
    hint_name = None
    if solver_hint:
        token = solver_hint.split(";", 1)[0].strip()
        slash = token.find("/")
        if slash > 0 and token[:slash].isdigit() and ":" not in token[:slash]:
            token = token[slash + 1 :]
        hint_name = token.split(":", 1)[0].strip() or None

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
    solver_hint = _with_kernel(solver_hint, _kernel_from_performance_log(stderr, solver_hint))

    return ParsedDriverOutput(
        time_ms=time_ms,
        algorithm_id=algorithm_id,
        solver_hint=solver_hint,
        direction=direction,
    )
