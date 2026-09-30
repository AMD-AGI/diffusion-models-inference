"""Compare Arm A and Arm B results and classify outcomes."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from miopen_convolution import MIOpenConvolution

from .benchmark import CommandResult, load_results


class Outcome(str, Enum):
    IMPROVEMENT = "improvement"
    NO_CHANGE = "no_change"
    REGRESSION = "regression"
    SYSTEM_DB_MISS = "system_db_miss"
    FAILURE = "failure"
    ARCH_MISMATCH_OR_ERROR = "arch_mismatch_or_error"


@dataclass
class ComparisonEntry:
    command: str
    outcome: str
    arm_a_median_ms: float | None
    arm_b_median_ms: float | None
    arm_a_stddev_ms: float | None
    arm_b_stddev_ms: float | None
    speedup_pct: float | None
    arm_a_solver: str | None
    arm_b_solver: str | None
    system_db_solver: str | None
    in_system_db: bool
    arm_a_algorithm_id: str | None
    arm_b_algorithm_id: str | None
    source_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _primary_solver(value: str) -> str:
    """Primary perf-DB record, including kernel config and embedded spaces."""
    return value.split(";", 1)[0].strip()


def solver_name(value: str | None) -> str | None:
    """Solver id from a driver ``id/Name`` token or a perf-DB ``Name:config`` record."""
    if not value:
        return None
    token = value.split(";", 1)[0].strip()
    slash = token.find("/")
    if slash > 0 and token[:slash].isdigit() and ":" not in token[:slash]:
        token = token[slash + 1 :].strip()
    return token.split(":", 1)[0].strip() or None


def recorded_solver(driver_hint: str | None, db_candidates: list[str | None]) -> str | None:
    """Prefer the perf-DB record when it is the solution the driver actually ran.

    The database value keeps the kernel configuration. The driver line is only
    ``id/Name``. A database entry for a different solver is not substituted.
    """
    available = [value for value in db_candidates if value]
    if driver_hint:
        hint_name = solver_name(driver_hint)
        for value in available:
            if solver_name(value) == hint_name:
                return value
        return driver_hint
    return available[0] if available else None


_PERF_DB_LAYOUTS = {"NCHW", "NHWC", "NCDHW", "NDHWC"}
_PERF_DB_DIRECTIONS = {"F", "B", "W"}


def _conv_out(size: int, pad: int, dilation: int, kernel: int, stride: int) -> int:
    return (size + 2 * pad - dilation * (kernel - 1) - 1) // stride + 1


def convolution_from_perf_db_key(key: str) -> MIOpenConvolution:
    """Parse a user/system perf-DB key (``spatial x in_channels x ...``).

    Field order matches ``ProblemDescription::Visit``. For 2D, depth fields are
    omitted so the result compares equal to a driver command.
    """
    parts = key.split("=", 1)[0].split("x")
    if len(parts) != 24:
        raise ValueError(f"perf DB key has {len(parts)} fields, expected 24")
    spatial = int(parts[0])
    if spatial not in (2, 3):
        raise ValueError(f"unsupported spatial dim {spatial}")
    layout, precision, direction = parts[21], parts[22], parts[23]
    if layout not in _PERF_DB_LAYOUTS or direction not in _PERF_DB_DIRECTIONS:
        raise ValueError(f"unrecognized layout or direction in {key}")
    kwargs: dict[str, Any] = {
        "in_channels": int(parts[1]),
        "in_h": int(parts[2]),
        "in_w": int(parts[3]),
        "fil_h": int(parts[5]),
        "fil_w": int(parts[6]),
        "out_channels": int(parts[8]),
        "batchsize": int(parts[9]),
        "pad_h": int(parts[10]),
        "pad_w": int(parts[11]),
        "conv_stride_h": int(parts[13]),
        "conv_stride_w": int(parts[14]),
        "dilation_h": int(parts[16]),
        "dilation_w": int(parts[17]),
        "group_count": int(parts[20]),
        "in_layout": layout,
        "fil_layout": layout,
        "out_layout": layout,
        "precision": precision,
        "direction": direction,
    }
    if spatial == 3:
        kwargs.update(
            {
                "in_d": int(parts[4]),
                "fil_d": int(parts[7]),
                "pad_d": int(parts[12]),
                "conv_stride_d": int(parts[15]),
                "dilation_d": int(parts[18]),
            }
        )
    return MIOpenConvolution(**kwargs)


def perf_db_problem(conv: MIOpenConvolution) -> MIOpenConvolution:
    """Problem as stored in the perf DB.

    Forward entries match the driver tensors. Backward data and backward
    weights store the forward output as ``in`` and the forward input channels
    as ``out_channels``.
    """
    if conv.direction == "F":
        return conv
    kwargs: dict[str, Any] = {
        "batchsize": conv.batchsize,
        "in_channels": conv.out_channels,
        "in_h": _conv_out(
            conv.in_h, conv.pad_h, conv.dilation_h, conv.fil_h, conv.conv_stride_h
        ),
        "in_w": _conv_out(
            conv.in_w, conv.pad_w, conv.dilation_w, conv.fil_w, conv.conv_stride_w
        ),
        "out_channels": conv.in_channels,
        "fil_h": conv.fil_h,
        "fil_w": conv.fil_w,
        "pad_h": conv.pad_h,
        "pad_w": conv.pad_w,
        "conv_stride_h": conv.conv_stride_h,
        "conv_stride_w": conv.conv_stride_w,
        "dilation_h": conv.dilation_h,
        "dilation_w": conv.dilation_w,
        "group_count": conv.group_count,
        "in_layout": conv.in_layout,
        "fil_layout": conv.fil_layout,
        "out_layout": conv.out_layout,
        "precision": conv.precision,
        "direction": conv.direction,
    }
    if conv.spatial_dim == 3 and conv.in_d is not None and conv.fil_d is not None:
        kwargs.update(
            {
                "in_d": _conv_out(
                    conv.in_d, conv.pad_d, conv.dilation_d, conv.fil_d, conv.conv_stride_d
                ),
                "fil_d": conv.fil_d,
                "pad_d": conv.pad_d,
                "conv_stride_d": conv.conv_stride_d,
                "dilation_d": conv.dilation_d,
            }
        )
    return MIOpenConvolution(**kwargs)


def _convolution_from_udb_key(key: str) -> MIOpenConvolution:
    parts = key.split("=", 1)[0].split("x")
    if len(parts) == 24 and parts[0] in {"2", "3"}:
        return convolution_from_perf_db_key(key)
    return MIOpenConvolution.from_db_key(key)


def load_udb_solver_map(path: Path) -> dict[MIOpenConvolution, str]:
    mapping: dict[MIOpenConvolution, str] = {}
    if not path.exists():
        return mapping
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            try:
                conv = _convolution_from_udb_key(key)
                mapping[conv] = _primary_solver(value)
            except Exception:
                continue
    return mapping


def find_system_udb(db_prefix: str) -> Path | None:
    roots: list[Path] = []
    env_path = os.environ.get("MIOPEN_SYSTEM_DB_PATH")
    if env_path:
        roots.append(Path(env_path))
    roots.extend(
        [
            Path("/opt/rocm/share/miopen/db"),
            Path("/usr/share/miopen/db"),
        ]
    )

    for root in roots:
        if not root.exists():
            continue
        if db_prefix:
            matches = sorted(root.glob(f"{db_prefix}*.udb.txt"))
        else:
            matches = sorted(root.glob("*.udb.txt"))
        if matches:
            return matches[0]
    return None


def _db_solver(
    solver_map: dict[MIOpenConvolution, str], command: str
) -> str | None:
    try:
        conv = perf_db_problem(MIOpenConvolution.from_miopendriver_command(command))
    except Exception:
        return None
    value = solver_map.get(conv)
    if value is None:
        return None
    return _primary_solver(value)


def _solver_from_result(
    result: CommandResult | None,
    solver_maps: list[dict[MIOpenConvolution, str]],
    command: str,
) -> str | None:
    driver_hint = result.solver_hints[-1] if result and result.solver_hints else None
    return recorded_solver(
        driver_hint,
        [_db_solver(solver_map, command) for solver_map in solver_maps],
    )


def _most_common(values: list[str]) -> str | None:
    if not values:
        return None
    return max(set(values), key=values.count)


def _failed(result: CommandResult | None, repeats: int) -> bool:
    if result is None:
        return True
    if result.median_ms is None:
        result.finalize()
    if result.median_ms is None:
        return True
    if result.returncodes and any(code != 0 for code in result.returncodes):
        return True
    if len(result.times_ms) < repeats:
        return True
    return False


def classify_entry(
    command: str,
    arm_a: CommandResult | None,
    arm_b: CommandResult | None,
    system_db_map: dict[MIOpenConvolution, str],
    arm_a_db_map: dict[MIOpenConvolution, str],
    arm_b_db_map: dict[MIOpenConvolution, str],
    threshold_pct: float,
    benchmark_repeats: int,
    source_files: list[str] | None = None,
) -> ComparisonEntry:
    notes: list[str] = []
    in_system_db = False
    system_db_solver: str | None = None

    try:
        conv = perf_db_problem(MIOpenConvolution.from_miopendriver_command(command))
        system_db_solver = system_db_map.get(conv)
        in_system_db = system_db_solver is not None
    except Exception as exc:
        notes.append(f"failed to parse command: {exc}")

    arm_a_solver = _solver_from_result(
        arm_a, [arm_a_db_map, system_db_map], command
    )
    arm_b_solver = _solver_from_result(arm_b, [arm_b_db_map], command)
    arm_a_algo = _most_common(arm_a.algorithm_ids if arm_a else [])
    arm_b_algo = _most_common(arm_b.algorithm_ids if arm_b else [])

    if _failed(arm_a, benchmark_repeats) or _failed(arm_b, benchmark_repeats):
        outcome = Outcome.FAILURE
        if arm_a and arm_a.returncodes and any(code != 0 for code in arm_a.returncodes):
            outcome = Outcome.ARCH_MISMATCH_OR_ERROR
            notes.append("Arm A driver returned non-zero exit code")
        if arm_b and arm_b.returncodes and any(code != 0 for code in arm_b.returncodes):
            outcome = Outcome.ARCH_MISMATCH_OR_ERROR
            notes.append("Arm B driver returned non-zero exit code")
        return ComparisonEntry(
            command=command,
            outcome=outcome.value,
            arm_a_median_ms=arm_a.median_ms if arm_a else None,
            arm_b_median_ms=arm_b.median_ms if arm_b else None,
            arm_a_stddev_ms=arm_a.stddev_ms if arm_a else None,
            arm_b_stddev_ms=arm_b.stddev_ms if arm_b else None,
            speedup_pct=None,
            arm_a_solver=arm_a_solver,
            arm_b_solver=arm_b_solver,
            system_db_solver=system_db_solver,
            in_system_db=in_system_db,
            arm_a_algorithm_id=arm_a_algo,
            arm_b_algorithm_id=arm_b_algo,
            source_files=source_files or [],
            notes=notes,
        )

    if not in_system_db:
        notes.append("shape not in installed system UDB")

    assert arm_a is not None and arm_b is not None
    arm_a.finalize()
    arm_b.finalize()
    time_a = arm_a.median_ms or 0.0
    time_b = arm_b.median_ms or 0.0
    speedup_pct = ((time_a - time_b) / time_a) * 100 if time_a > 0 else None

    if speedup_pct is None:
        outcome = Outcome.FAILURE
    elif abs(speedup_pct) <= threshold_pct:
        outcome = Outcome.NO_CHANGE
    elif speedup_pct > threshold_pct:
        outcome = Outcome.IMPROVEMENT
    elif speedup_pct < -threshold_pct and solver_name(arm_a_solver) != solver_name(arm_b_solver):
        outcome = Outcome.REGRESSION
    else:
        outcome = Outcome.NO_CHANGE

    return ComparisonEntry(
        command=command,
        outcome=outcome.value,
        arm_a_median_ms=time_a,
        arm_b_median_ms=time_b,
        arm_a_stddev_ms=arm_a.stddev_ms,
        arm_b_stddev_ms=arm_b.stddev_ms,
        speedup_pct=speedup_pct,
        arm_a_solver=arm_a_solver,
        arm_b_solver=arm_b_solver,
        system_db_solver=system_db_solver,
        in_system_db=in_system_db,
        arm_a_algorithm_id=arm_a_algo,
        arm_b_algorithm_id=arm_b_algo,
        source_files=source_files or [],
        notes=notes,
    )


def compare_arms(
    commands: list[str],
    arm_a_results_path: Path,
    arm_b_results_path: Path,
    db_prefix: str,
    threshold_pct: float,
    benchmark_repeats: int,
    arm_a_user_db: Path | None = None,
    arm_b_user_db: Path | None = None,
    source_files_by_command: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    arm_a_results = load_results(arm_a_results_path)
    arm_b_results = load_results(arm_b_results_path)

    system_udb = find_system_udb(db_prefix)
    system_db_map = load_udb_solver_map(system_udb) if system_udb else {}

    arm_a_db_map: dict[MIOpenConvolution, str] = {}
    arm_b_db_map: dict[MIOpenConvolution, str] = {}
    if arm_a_user_db:
        for path in arm_a_user_db.glob("*.udb.txt"):
            arm_a_db_map.update(load_udb_solver_map(path))
    if arm_b_user_db:
        for path in arm_b_user_db.glob("*.udb.txt"):
            arm_b_db_map.update(load_udb_solver_map(path))

    entries: list[ComparisonEntry] = []
    for command in commands:
        entry = classify_entry(
            command=command,
            arm_a=arm_a_results.get(command),
            arm_b=arm_b_results.get(command),
            system_db_map=system_db_map,
            arm_a_db_map=arm_a_db_map,
            arm_b_db_map=arm_b_db_map,
            threshold_pct=threshold_pct,
            benchmark_repeats=benchmark_repeats,
            source_files=(source_files_by_command or {}).get(command),
        )
        entries.append(entry)

    counts = {item.value: 0 for item in Outcome}
    for entry in entries:
        counts[entry.outcome] = counts.get(entry.outcome, 0) + 1
    counts["system_db_miss"] = sum(1 for entry in entries if not entry.in_system_db)

    primary_entries = [
        e
        for e in entries
        if e.outcome
        in {Outcome.IMPROVEMENT.value, Outcome.NO_CHANGE.value, Outcome.REGRESSION.value}
    ]

    return {
        "system_udb_path": str(system_udb) if system_udb else None,
        "threshold_pct": threshold_pct,
        "benchmark_repeats": benchmark_repeats,
        "counts": counts,
        "entries": [asdict(e) for e in entries],
        "improvements": sorted(
            [asdict(e) for e in entries if e.outcome == Outcome.IMPROVEMENT.value],
            key=lambda item: item.get("speedup_pct") or 0,
            reverse=True,
        ),
        "regressions": [
            asdict(e) for e in entries if e.outcome == Outcome.REGRESSION.value
        ],
        "no_change": [asdict(e) for e in entries if e.outcome == Outcome.NO_CHANGE.value],
        "system_db_misses": [
            asdict(e) for e in entries if not e.in_system_db
        ],
        "failures": [
            asdict(e)
            for e in entries
            if e.outcome in {Outcome.FAILURE.value, Outcome.ARCH_MISMATCH_OR_ERROR.value}
        ],
        "primary_ab_count": len(primary_entries),
    }


def write_comparison(path: Path, comparison: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(comparison, handle, indent=2, sort_keys=True)
        handle.write("\n")
