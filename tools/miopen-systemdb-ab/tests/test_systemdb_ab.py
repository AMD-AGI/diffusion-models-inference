import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "src"))

from miopen_ab.compare import classify_entry, Outcome
from miopen_ab.benchmark import CommandResult
from miopen_ab.driver_output import parse_driver_output
from miopen_ab.workloads import normalize_command, collect_workloads


SAMPLE_STDOUT = """
MIOpenDriver: convbfp16 -n 1 -c 128 -H 1024 -W 1024 -k 128 -y 3 -x 3 -p 1 -q 1 -u 1 -v 1 -l 1 -j 1 -m conv -g 1 -F 1 -t 1 -w 2 -V 0
MIOpen Forward Conv. Algorithm: 1
GPU Kernel Time Forward Conv. Elapsed: 0.135265 ms (average)
"""

COMMAND = (
    "MIOpenDriver convbfp16 -n 1 -c 128 -H 1024 -W 1024 -k 128 -y 3 -x 3 "
    "-p 1 -q 1 -u 1 -v 1 -l 1 -j 1 -m conv -g 1 -F 1 -t 1"
)


def test_normalize_command_adds_flags():
    normalized = normalize_command(COMMAND)
    assert "-t 1" in normalized
    assert "-w 2" in normalized
    assert "-V 0" in normalized


def test_parse_driver_output_forward():
    parsed = parse_driver_output(COMMAND, SAMPLE_STDOUT)
    assert parsed.time_ms == pytest.approx(0.135265)
    assert parsed.algorithm_id == "1"
    assert parsed.direction == "F"


def test_classify_improvement_without_system_db():
    arm_a = CommandResult(command=COMMAND, times_ms=[10.0, 10.0, 10.0], returncodes=[0, 0, 0])
    arm_b = CommandResult(command=COMMAND, times_ms=[8.0, 8.0, 8.0], returncodes=[0, 0, 0])
    entry = classify_entry(
        command=COMMAND,
        arm_a=arm_a,
        arm_b=arm_b,
        system_db_map={},
        arm_a_db_map={},
        arm_b_db_map={},
        threshold_pct=2.0,
        benchmark_repeats=3,
    )
    assert entry.outcome == Outcome.IMPROVEMENT.value
    assert entry.in_system_db is False
    assert entry.speedup_pct == pytest.approx(20.0)


def test_classify_improvement_in_system_db():
    from miopen_convolution import MIOpenConvolution

    conv = MIOpenConvolution.from_miopendriver_command(COMMAND)
    system_map = {conv: "SolverA:params"}
    arm_a = CommandResult(
        command=COMMAND,
        times_ms=[10.0, 10.0, 10.0],
        returncodes=[0, 0, 0],
        solver_hints=["SolverA"],
    )
    arm_b = CommandResult(
        command=COMMAND,
        times_ms=[8.0, 8.0, 8.0],
        returncodes=[0, 0, 0],
        solver_hints=["SolverB"],
    )
    entry = classify_entry(
        command=COMMAND,
        arm_a=arm_a,
        arm_b=arm_b,
        system_db_map=system_map,
        arm_a_db_map={},
        arm_b_db_map={},
        threshold_pct=2.0,
        benchmark_repeats=3,
    )
    assert entry.outcome == Outcome.IMPROVEMENT.value
    assert entry.speedup_pct == pytest.approx(20.0)


def test_classify_regression_requires_solver_change():
    from miopen_convolution import MIOpenConvolution

    conv = MIOpenConvolution.from_miopendriver_command(COMMAND)
    system_map = {conv: "SolverA:params"}
    arm_a = CommandResult(
        command=COMMAND,
        times_ms=[10.0, 10.0, 10.0],
        returncodes=[0, 0, 0],
        solver_hints=["SolverA"],
    )
    arm_b = CommandResult(
        command=COMMAND,
        times_ms=[11.0, 11.0, 11.0],
        returncodes=[0, 0, 0],
        solver_hints=["SolverA"],
    )
    entry = classify_entry(
        command=COMMAND,
        arm_a=arm_a,
        arm_b=arm_b,
        system_db_map=system_map,
        arm_a_db_map={},
        arm_b_db_map={},
        threshold_pct=2.0,
        benchmark_repeats=3,
    )
    assert entry.outcome == Outcome.NO_CHANGE.value


def test_collect_workloads_deduplicates(tmp_path, monkeypatch):
    workloads_dir = tmp_path / "workloads"
    workloads_dir.mkdir()
    (workloads_dir / "a.txt").write_text(
        "MIOpenDriver convbfp16 -n 1 -c 1 -H 8 -W 8 -k 1 -y 1 -x 1 -F 1 -t 1\n"
        "MIOpenDriver convbfp16 -n 1 -c 2 -H 8 -W 8 -k 1 -y 1 -x 1 -F 1 -t 1\n"
    )
    (workloads_dir / "b.txt").write_text(
        "MIOpenDriver convbfp16 -n 1 -c 1 -H 8 -W 8 -k 1 -y 1 -x 1 -F 1 -t 1\n"
    )
    monkeypatch.chdir(tmp_path)
    items = collect_workloads("workloads/*.txt")
    assert len(items) == 2
    dup = next(item for item in items if "-c 1 " in item.command)
    assert len(dup.source_files) == 2
