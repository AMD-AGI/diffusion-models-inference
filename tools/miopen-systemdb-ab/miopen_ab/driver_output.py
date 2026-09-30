"""Parse MIOpenDriver stdout for timing and solver metadata."""

from __future__ import annotations

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

    return ParsedDriverOutput(
        time_ms=time_ms,
        algorithm_id=algorithm_id,
        solver_hint=solver_hint,
        direction=direction,
    )
