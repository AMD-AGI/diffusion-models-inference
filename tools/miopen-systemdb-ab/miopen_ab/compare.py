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
    delta_ms: float | None = None
    parity: str = "failed"
    shape: dict[str, Any] | None = None
    arm_a_times_ms: list[float] = field(default_factory=list)
    arm_b_times_ms: list[float] = field(default_factory=list)
    arm_a_device_ids: list[str] = field(default_factory=list)
    arm_b_device_ids: list[str] = field(default_factory=list)
    arm_b_tune_device: str | None = None
    same_solver: bool = False
    same_kernel: bool = False
    kernel_difference: str = "kernel_not_recorded"
    source_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _primary_solver(value: str) -> str:
    """Primary perf-DB record, including kernel config and embedded spaces."""
    return value.split(";", 1)[0].strip()


def _same_solver(arm_a_solver: str | None, arm_b_solver: str | None) -> bool:
    """True when both arms recorded a solver and the names match.

    A matching name is not a matching kernel. Use ``kernel_difference``.
    """
    left = solver_name(arm_a_solver)
    right = solver_name(arm_b_solver)
    return bool(left and right and left == right)


# Solvers that compile one kernel. The name is the kernel. ImplicitGEMM CK
# solvers (ConvHipImplicitGemm*) and dynamic IGEMM (ConvAsmImplicitGemmGTCDynamic*)
# are absent: the text after ':' is the kernel instance.
_SINGLE_KERNEL_PREFIXES = (
    "Gemm",
    "ConvBinWinograd",
    "ConvWinoRage",
    "ConvOclDirect",
    "ConvDirectNaive",
)


def solver_name(value: str | None) -> str | None:
    """Solver id from a driver ``id/Name`` token or a perf-DB ``Name:config`` record."""
    if not value:
        return None
    token = _solver_token(value)
    return token.split(":", 1)[0].strip() or None


def kernel_config(value: str | None) -> str | None:
    """Kernel instance from a ``Name:config`` record.

    Empty when the driver only printed ``id/Name`` and no perf config was logged
    or stored. For a single-kernel solver that is expected.
    """
    if not value:
        return None
    token = _solver_token(value)
    if ":" not in token:
        return None
    config = token.split(":", 1)[1].strip()
    return config or None


def is_single_kernel_solver(name: str | None) -> bool:
    """True when this solver name identifies one kernel."""
    if not name:
        return False
    return name.startswith(_SINGLE_KERNEL_PREFIXES)


def kernel_difference(arm_a_solver: str | None, arm_b_solver: str | None) -> str:
    """How the executed kernels compare.

    ``same_kernel`` — same solver and the same kernel instance.
    ``different_solver`` — the solver name changed.
    ``different_kernel`` — one multi-kernel solver, two recorded instances.
    ``kernel_not_recorded`` — a multi-kernel solver ran, and at least one arm
    has no perf config, so the instance is unknown.
    """
    left = solver_name(arm_a_solver)
    right = solver_name(arm_b_solver)
    if not left or not right:
        return "kernel_not_recorded"
    if left != right:
        return "different_solver"
    if is_single_kernel_solver(left):
        return "same_kernel"
    left_config = kernel_config(arm_a_solver)
    right_config = kernel_config(arm_b_solver)
    if left_config is None or right_config is None:
        return "kernel_not_recorded"
    if left_config == right_config:
        return "same_kernel"
    return "different_kernel"


def _solver_token(value: str) -> str:
    token = value.split(";", 1)[0].strip()
    slash = token.find("/")
    if slash > 0 and token[:slash].isdigit() and ":" not in token[:slash]:
        token = token[slash + 1 :].strip()
    return token


def recorded_solver(driver_hint: str | None, db_candidates: list[str | None]) -> str | None:
    """Solver that ran, with its kernel config when one was recorded.

    A driver hint that already includes a perf config is the instance that
    ran, including when a database row names the same solver and a different
    kernel. The database fills in the config only when the hint is ``id/Name``
    and the row is for that same solver. A row for a different solver is not
    substituted.
    """
    available = [value for value in db_candidates if value]
    if driver_hint:
        if kernel_config(driver_hint):
            return driver_hint
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


def _is_perf_db_filename(name: str) -> bool:
    """User and system performance DBs. Find DBs (``.fdb.txt`` / ``.ufdb.txt``) are not."""
    if name.endswith(".fdb.txt") or name.endswith(".ufdb.txt"):
        return False
    return name.endswith(".db.txt") or name.endswith(".udb.txt")


def _choose_system_perf_db(root: Path, db_prefix: str) -> Path | None:
    if root.is_file():
        if _is_perf_db_filename(root.name) and (not db_prefix or root.name.startswith(db_prefix)):
            return root
        return None
    if not root.is_dir():
        return None

    matches = [
        path
        for path in root.iterdir()
        if path.is_file()
        and _is_perf_db_filename(path.name)
        and (not db_prefix or path.name.startswith(db_prefix))
    ]
    if not matches:
        return None

    def rank(path: Path) -> tuple[int, int, str]:
        name = path.name
        exact_system = 0 if db_prefix and name == f"{db_prefix}.db.txt" else 1
        system_db = 0 if name.endswith(".db.txt") and not name.endswith(".udb.txt") else 1
        return (exact_system, system_db, name)

    return sorted(matches, key=rank)[0]


def find_system_udb(db_prefix: str) -> Path | None:
    """Locate the installed system performance DB.

    ROCm 10.1 ships ``{prefix}.db.txt`` (for example ``gfx950100.db.txt``).
    Older installs use ``{prefix}*.udb.txt``. Find databases are ignored.
    """
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
        chosen = _choose_system_perf_db(root, db_prefix)
        if chosen is not None:
            return chosen
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


def shape_from_command(command: str) -> dict[str, Any] | None:
    try:
        return asdict(MIOpenConvolution.from_miopendriver_command(command))
    except Exception:
        return None


def parity_for(outcome: str, speedup_pct: float | None, threshold_pct: float) -> str:
    """How Arm A (production heuristics) compares with Arm B (exhaustive)."""
    if outcome in {Outcome.FAILURE.value, Outcome.ARCH_MISMATCH_OR_ERROR.value}:
        return "failed"
    if speedup_pct is None:
        return "failed"
    if abs(speedup_pct) <= threshold_pct:
        return "equal"
    if speedup_pct > threshold_pct:
        return "production_slower"
    return "exhaustive_slower"


def _delta_ms(arm_a: CommandResult | None, arm_b: CommandResult | None) -> float | None:
    if arm_a is None or arm_b is None:
        return None
    if arm_a.median_ms is None or arm_b.median_ms is None:
        return None
    return arm_a.median_ms - arm_b.median_ms


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
    shape = shape_from_command(command)
    if arm_a is not None:
        arm_a.finalize()
    if arm_b is not None:
        arm_b.finalize()

    def _entry(outcome: str, speedup_pct: float | None) -> ComparisonEntry:
        difference = kernel_difference(arm_a_solver, arm_b_solver)
        return ComparisonEntry(
            command=command,
            outcome=outcome,
            arm_a_median_ms=arm_a.median_ms if arm_a else None,
            arm_b_median_ms=arm_b.median_ms if arm_b else None,
            arm_a_stddev_ms=arm_a.stddev_ms if arm_a else None,
            arm_b_stddev_ms=arm_b.stddev_ms if arm_b else None,
            speedup_pct=speedup_pct,
            arm_a_solver=arm_a_solver,
            arm_b_solver=arm_b_solver,
            system_db_solver=system_db_solver,
            in_system_db=in_system_db,
            arm_a_algorithm_id=arm_a_algo,
            arm_b_algorithm_id=arm_b_algo,
            delta_ms=_delta_ms(arm_a, arm_b),
            parity=parity_for(outcome, speedup_pct, threshold_pct),
            shape=shape,
            arm_a_times_ms=list(arm_a.times_ms) if arm_a else [],
            arm_b_times_ms=list(arm_b.times_ms) if arm_b else [],
            arm_a_device_ids=list(arm_a.device_ids) if arm_a else [],
            arm_b_device_ids=list(arm_b.device_ids) if arm_b else [],
            same_solver=_same_solver(arm_a_solver, arm_b_solver),
            same_kernel=difference == "same_kernel",
            kernel_difference=difference,
            source_files=source_files or [],
            notes=notes,
        )

    if _failed(arm_a, benchmark_repeats) or _failed(arm_b, benchmark_repeats):
        outcome = Outcome.FAILURE
        if arm_a and arm_a.returncodes and any(code != 0 for code in arm_a.returncodes):
            outcome = Outcome.ARCH_MISMATCH_OR_ERROR
            notes.append("Arm A driver returned non-zero exit code")
        if arm_b and arm_b.returncodes and any(code != 0 for code in arm_b.returncodes):
            outcome = Outcome.ARCH_MISMATCH_OR_ERROR
            notes.append("Arm B driver returned non-zero exit code")
        return _entry(outcome.value, None)

    if not in_system_db:
        notes.append("shape not in installed system performance DB")

    assert arm_a is not None and arm_b is not None
    time_a = arm_a.median_ms or 0.0
    time_b = arm_b.median_ms or 0.0
    speedup_pct = ((time_a - time_b) / time_a) * 100 if time_a > 0 else None

    if speedup_pct is None:
        outcome = Outcome.FAILURE
    elif abs(speedup_pct) <= threshold_pct:
        outcome = Outcome.NO_CHANGE
    elif speedup_pct > threshold_pct:
        outcome = Outcome.IMPROVEMENT
    elif speedup_pct < -threshold_pct and kernel_difference(
        arm_a_solver, arm_b_solver
    ) in {"different_solver", "different_kernel"}:
        outcome = Outcome.REGRESSION
    else:
        outcome = Outcome.NO_CHANGE

    return _entry(outcome.value, speedup_pct)


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
    arm_b_tune_devices: dict[str, str] | None = None,
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
        tune_device = (arm_b_tune_devices or {}).get(command)
        if tune_device:
            entry.arm_b_tune_device = tune_device
        entries.append(entry)

    counts = {item.value: 0 for item in Outcome}
    parity_counts = {"equal": 0, "production_slower": 0, "exhaustive_slower": 0, "failed": 0}
    for entry in entries:
        counts[entry.outcome] = counts.get(entry.outcome, 0) + 1
        parity_counts[entry.parity] = parity_counts.get(entry.parity, 0) + 1
    # Informational overlap with the outcome buckets above. A miss is still compared.
    counts["system_db_miss"] = sum(1 for entry in entries if not entry.in_system_db)

    def _dump(selected: list[ComparisonEntry]) -> list[dict[str, Any]]:
        return [asdict(entry) for entry in selected]

    def _reported(entry: ComparisonEntry) -> bool:
        """A timing row that is not the same kernel and did not fail."""
        return entry.parity != "failed" and entry.kernel_difference != "same_kernel"

    def _select(entries_for_parity: list[ComparisonEntry], difference: str) -> list[ComparisonEntry]:
        return [
            entry
            for entry in entries_for_parity
            if entry.kernel_difference == difference
        ]

    production_slower = sorted(
        (entry for entry in entries if entry.parity == "production_slower"),
        key=lambda entry: entry.delta_ms or 0,
        reverse=True,
    )
    equal = [entry for entry in entries if entry.parity == "equal"]
    exhaustive_slower = sorted(
        (entry for entry in entries if entry.parity == "exhaustive_slower"),
        key=lambda entry: entry.delta_ms or 0,
    )
    same_solver = [entry for entry in entries if entry.same_solver and entry.parity != "failed"]
    same_kernel = [entry for entry in entries if entry.same_kernel and entry.parity != "failed"]
    different_solver_production_slower = _select(production_slower, "different_solver")
    different_solver_similar = _select(equal, "different_solver")
    different_solver_exhaustive_slower = _select(exhaustive_slower, "different_solver")
    different_kernel_production_slower = _select(production_slower, "different_kernel")
    different_kernel_similar = _select(equal, "different_kernel")
    different_kernel_exhaustive_slower = _select(exhaustive_slower, "different_kernel")
    unrecorded_kernel_production_slower = _select(production_slower, "kernel_not_recorded")
    unrecorded_kernel_similar = _select(equal, "kernel_not_recorded")
    unrecorded_kernel_exhaustive_slower = _select(exhaustive_slower, "kernel_not_recorded")
    different_solver_ms_left = sum(
        entry.delta_ms or 0 for entry in different_solver_production_slower
    )
    reported_faster = [entry for entry in production_slower if _reported(entry)]
    reported_ms_left = sum(entry.delta_ms or 0 for entry in reported_faster)
    by_source: dict[str, list[ComparisonEntry]] = {}
    for entry in entries:
        sources = entry.source_files or ["(no workload file)"]
        for source in sources:
            by_source.setdefault(source, []).append(entry)
    workload_summaries = []
    for source in sorted(by_source):
        grouped = by_source[source]
        slower = [entry for entry in grouped if entry.parity == "production_slower"]
        reported_grouped = [entry for entry in slower if _reported(entry)]

        def _n(difference: str, parity: str) -> int:
            return sum(
                1
                for entry in grouped
                if entry.kernel_difference == difference and entry.parity == parity
            )

        workload_summaries.append(
            {
                "source_file": source,
                "equal": sum(1 for entry in grouped if entry.parity == "equal"),
                "production_slower": len(slower),
                "exhaustive_slower": sum(
                    1 for entry in grouped if entry.parity == "exhaustive_slower"
                ),
                "failed": sum(1 for entry in grouped if entry.parity == "failed"),
                "ms_left_on_table": sum(entry.delta_ms or 0 for entry in slower),
                "same_solver": sum(
                    1 for entry in grouped if entry.same_solver and entry.parity != "failed"
                ),
                "same_kernel": sum(
                    1 for entry in grouped if entry.same_kernel and entry.parity != "failed"
                ),
                "different_solver_production_slower": _n("different_solver", "production_slower"),
                "different_solver_similar": _n("different_solver", "equal"),
                "different_solver_exhaustive_slower": _n("different_solver", "exhaustive_slower"),
                "different_solver_ms_left_on_table": sum(
                    entry.delta_ms or 0
                    for entry in grouped
                    if entry.kernel_difference == "different_solver"
                    and entry.parity == "production_slower"
                ),
                "different_kernel_production_slower": _n("different_kernel", "production_slower"),
                "different_kernel_similar": _n("different_kernel", "equal"),
                "different_kernel_exhaustive_slower": _n("different_kernel", "exhaustive_slower"),
                "unrecorded_kernel_production_slower": _n(
                    "kernel_not_recorded", "production_slower"
                ),
                "unrecorded_kernel_similar": _n("kernel_not_recorded", "equal"),
                "unrecorded_kernel_exhaustive_slower": _n(
                    "kernel_not_recorded", "exhaustive_slower"
                ),
                "reported_similar": sum(
                    1 for entry in grouped if entry.parity == "equal" and _reported(entry)
                ),
                "reported_exhaustive_slower": sum(
                    1
                    for entry in grouped
                    if entry.parity == "exhaustive_slower" and _reported(entry)
                ),
                "reported_ms_left_on_table": sum(
                    entry.delta_ms or 0 for entry in reported_grouped
                ),
            }
        )

    primary_entries = [
        entry
        for entry in entries
        if entry.outcome
        in {Outcome.IMPROVEMENT.value, Outcome.NO_CHANGE.value, Outcome.REGRESSION.value}
    ]
    ms_left_on_table = sum(entry.delta_ms or 0 for entry in production_slower)

    return {
        "system_udb_path": str(system_udb) if system_udb else None,
        "system_db_path": str(system_udb) if system_udb else None,
        "threshold_pct": threshold_pct,
        "benchmark_repeats": benchmark_repeats,
        "counts": counts,
        "parity_counts": parity_counts,
        "ms_left_on_table": ms_left_on_table,
        "different_solver_ms_left_on_table": different_solver_ms_left,
        "reported_ms_left_on_table": reported_ms_left,
        "same_solver_count": len(same_solver),
        "same_kernel_count": len(same_kernel),
        "by_source": workload_summaries,
        "entries": _dump(entries),
        "equal": _dump(equal),
        "production_slower": _dump(production_slower),
        "exhaustive_slower": _dump(exhaustive_slower),
        "same_solver": _dump(same_solver),
        "same_kernel": _dump(same_kernel),
        "different_solver_production_slower": _dump(different_solver_production_slower),
        "different_solver_similar": _dump(different_solver_similar),
        "different_solver_exhaustive_slower": _dump(different_solver_exhaustive_slower),
        "different_kernel_production_slower": _dump(different_kernel_production_slower),
        "different_kernel_similar": _dump(different_kernel_similar),
        "different_kernel_exhaustive_slower": _dump(different_kernel_exhaustive_slower),
        "unrecorded_kernel_production_slower": _dump(unrecorded_kernel_production_slower),
        "unrecorded_kernel_similar": _dump(unrecorded_kernel_similar),
        "unrecorded_kernel_exhaustive_slower": _dump(unrecorded_kernel_exhaustive_slower),
        "improvements": sorted(
            _dump([entry for entry in entries if entry.outcome == Outcome.IMPROVEMENT.value]),
            key=lambda item: item.get("speedup_pct") or 0,
            reverse=True,
        ),
        "regressions": _dump(
            [entry for entry in entries if entry.outcome == Outcome.REGRESSION.value]
        ),
        "no_change": _dump(
            [entry for entry in entries if entry.outcome == Outcome.NO_CHANGE.value]
        ),
        "system_db_misses": _dump([entry for entry in entries if not entry.in_system_db]),
        "failures": _dump(
            [
                entry
                for entry in entries
                if entry.outcome
                in {Outcome.FAILURE.value, Outcome.ARCH_MISMATCH_OR_ERROR.value}
            ]
        ),
        "primary_ab_count": len(primary_entries),
    }


def write_comparison(path: Path, comparison: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(comparison, handle, indent=2, sort_keys=True)
        handle.write("\n")
