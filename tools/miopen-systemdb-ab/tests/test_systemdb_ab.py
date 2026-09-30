import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "src"))

from miopen_ab.compare import (
    classify_entry,
    compare_arms,
    find_system_udb,
    load_udb_solver_map,
    perf_db_problem,
    Outcome,
)
from miopen_ab.benchmark import CommandResult, save_results
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
    assert parsed.solver_hint is None


def test_parse_driver_output_reads_performance_json():
    stdout = """
{"performance":{"name":"fwd-conv1x11u1","algorithm":5,"solution":"137/ConvHipImplicitGemmGroupFwdXdlops","direction":"forward","operation":"conv","results":{"average_time_ms":0.145735}}}
"""
    parsed = parse_driver_output(COMMAND, stdout)
    assert parsed.time_ms == pytest.approx(0.145735)
    assert parsed.algorithm_id == "5"
    assert parsed.solver_hint == "137/ConvHipImplicitGemmGroupFwdXdlops"
    assert parsed.direction == "F"


def test_parse_driver_output_attaches_performance_log_kernel():
    stdout = """
MIOpen Forward Conv. Algorithm: 5, Solution: 137/ConvHipImplicitGemmGroupFwdXdlops
GPU Kernel Time Forward Conv. Elapsed: 0.146713 ms (average)
"""
    stderr = (
        '{"solution":"ConvHipImplicitGemmGroupFwdXdlops","solver_id":137,'
        '"workspace_bytes":0,"phase":"execution","performance_configs":['
        '{"config_name":"ConvHipImplicitGemmGroupFwdXdlops",'
        '"config_descriptor":"DeviceGroupedConvFwd<64, 64, 16>",'
        '"exec_number":1,"time_executions_ms":[0.14],"time_ms":0.14,'
        '"time_std_ms":0,"time_min_ms":0.14,"time_max_ms":0.14,'
        '"number_of_transformations":0,"kernels":null}]}'
    )
    parsed = parse_driver_output(COMMAND, stdout, stderr)
    assert (
        parsed.solver_hint
        == "137/ConvHipImplicitGemmGroupFwdXdlops:DeviceGroupedConvFwd<64, 64, 16>"
    )


def test_parse_driver_output_records_full_solution_name():
    stdout = """
MIOpen(HIP): Info [FindConvFwdAlgorithm] miopenConvolutionFwdAlgoImplicitGEMM 0.200 0
MIOpen(HIP): Info [FindConvFwdAlgorithm] FW Chosen Algorithm: ConvAsmImplicitGemmGTCDynamicFwdXdlopsNHWC , 0, 0.135
MIOpen Forward Conv. Algorithm: 5, Solution: 98/ConvAsmImplicitGemmGTCDynamicFwdXdlopsNHWC
GPU Kernel Time Forward Conv. Elapsed: 0.135265 ms (average)
MIOpen Backward Data Conv. Algorithm: 1, Solution: 2/ConvOclDirectFwd
GPU Kernel Time Backward Data Conv. Elapsed: 1.0 ms (average)
"""
    spaced = stdout.replace(
        "98/ConvAsmImplicitGemmGTCDynamicFwdXdlopsNHWC",
        "98/ConvHipImplicitGemmGroupFwdXdlops: DeviceGroupedConvFwd<256, 128, OddC>",
    )
    parsed = parse_driver_output(COMMAND, spaced)
    assert parsed.algorithm_id == "5"
    assert (
        parsed.solver_hint
        == "98/ConvHipImplicitGemmGroupFwdXdlops: DeviceGroupedConvFwd<256, 128, OddC>"
    )


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
    assert entry.parity == "production_slower"
    assert entry.delta_ms == pytest.approx(2.0)
    assert entry.in_system_db is False
    assert entry.speedup_pct == pytest.approx(20.0)
    assert entry.shape["in_channels"] == 128
    assert entry.arm_a_times_ms == [10.0, 10.0, 10.0]


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
    assert entry.in_system_db is True
    assert entry.arm_a_solver == "SolverA:params"
    assert entry.arm_b_solver == "SolverB"
    assert entry.speedup_pct == pytest.approx(20.0)


def test_recorded_solver_keeps_driver_choice_when_db_differs():
    from miopen_convolution import MIOpenConvolution

    conv = MIOpenConvolution.from_miopendriver_command(COMMAND)
    complete = (
        "ConvAsmImplicitGemmGTCDynamicFwdXdlopsNHWC:fwd,nhwc,bf16,0,1,128,256"
    )
    arm_a = CommandResult(
        command=COMMAND,
        times_ms=[10.0, 10.0, 10.0],
        returncodes=[0, 0, 0],
        solver_hints=["42/ConvHipImplicitGemmGroupFwdXdlops"],
    )
    arm_b = CommandResult(
        command=COMMAND,
        times_ms=[8.0, 8.0, 8.0],
        returncodes=[0, 0, 0],
        solver_hints=["98/ConvAsmImplicitGemmGTCDynamicFwdXdlopsNHWC"],
    )
    entry = classify_entry(
        command=COMMAND,
        arm_a=arm_a,
        arm_b=arm_b,
        system_db_map={conv: complete},
        arm_a_db_map={},
        arm_b_db_map={conv: complete + ";ConvHipImplicitGemmGroupFwdXdlops:alt"},
        threshold_pct=2.0,
        benchmark_repeats=3,
    )
    assert entry.arm_a_solver == "42/ConvHipImplicitGemmGroupFwdXdlops"
    assert entry.arm_b_solver == complete
    assert entry.outcome == Outcome.IMPROVEMENT.value


def test_classify_regression_requires_kernel_change():
    from miopen_convolution import MIOpenConvolution

    conv = MIOpenConvolution.from_miopendriver_command(COMMAND)
    system_map = {conv: "GemmFwdRest:params"}
    arm_a = CommandResult(
        command=COMMAND,
        times_ms=[10.0, 10.0, 10.0],
        returncodes=[0, 0, 0],
        solver_hints=["GemmFwdRest"],
        device_ids=["0", "0", "3"],
    )
    arm_b = CommandResult(
        command=COMMAND,
        times_ms=[11.0, 11.0, 11.0],
        returncodes=[0, 0, 0],
        solver_hints=["GemmFwdRest"],
        device_ids=["1", "1", "1"],
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
    assert entry.parity == "exhaustive_slower"
    assert entry.same_solver is True
    assert entry.same_kernel is True
    assert entry.kernel_difference == "same_kernel"
    assert entry.arm_a_device_ids == ["0", "0", "3"]
    assert entry.arm_b_device_ids == ["1", "1", "1"]
    assert entry.delta_ms == pytest.approx(-1.0)
    assert entry.arm_a_solver == "GemmFwdRest:params"
    assert entry.in_system_db is True


def test_same_solver_different_kernel_is_not_the_same_kernel():
    from miopen_convolution import MIOpenConvolution

    conv = MIOpenConvolution.from_miopendriver_command(COMMAND)
    solver = "ConvHipImplicitGemmGroupFwdXdlops"
    arm_a_record = f"{solver}:DeviceGroupedConvFwd<64, 64, 16>"
    arm_b_record = f"{solver}:DeviceGroupedConvFwd<256, 256, 128>"
    arm_a = CommandResult(
        command=COMMAND,
        times_ms=[10.0, 10.0, 10.0],
        returncodes=[0, 0, 0],
        solver_hints=[f"137/{solver}"],
    )
    arm_b = CommandResult(
        command=COMMAND,
        times_ms=[8.0, 8.0, 8.0],
        returncodes=[0, 0, 0],
        solver_hints=[f"137/{solver}"],
    )
    entry = classify_entry(
        command=COMMAND,
        arm_a=arm_a,
        arm_b=arm_b,
        system_db_map={},
        arm_a_db_map={conv: arm_a_record},
        arm_b_db_map={conv: arm_b_record},
        threshold_pct=2.0,
        benchmark_repeats=3,
    )
    assert entry.same_solver is True
    assert entry.same_kernel is False
    assert entry.kernel_difference == "different_kernel"
    assert entry.outcome == Outcome.IMPROVEMENT.value
    assert entry.arm_a_solver == arm_a_record
    assert entry.arm_b_solver == arm_b_record


def test_missing_implicit_gemm_config_is_not_called_the_same_kernel():
    arm_a = CommandResult(
        command=COMMAND,
        times_ms=[10.0, 10.0, 10.0],
        returncodes=[0, 0, 0],
        solver_hints=["137/ConvHipImplicitGemmGroupFwdXdlops"],
    )
    arm_b = CommandResult(
        command=COMMAND,
        times_ms=[8.0, 8.0, 8.0],
        returncodes=[0, 0, 0],
        solver_hints=[
            "ConvHipImplicitGemmGroupFwdXdlops:DeviceGroupedConvFwd<256, 256, 128>"
        ],
    )
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
    assert entry.same_solver is True
    assert entry.same_kernel is False
    assert entry.kernel_difference == "kernel_not_recorded"
    assert entry.outcome == Outcome.IMPROVEMENT.value


def test_compare_arms_keeps_same_solver_out_of_the_report_lists(tmp_path, monkeypatch):
    monkeypatch.setattr("miopen_ab.compare.find_system_udb", lambda _prefix: None)
    same = COMMAND
    other = COMMAND.replace("-c 128", "-c 64")
    arm_a = {
        same: CommandResult(
            command=same,
            times_ms=[10.0, 10.0, 10.0],
            returncodes=[0, 0, 0],
            solver_hints=["42/GemmFwdRest"],
            device_ids=["0", "0", "0"],
        ),
        other: CommandResult(
            command=other,
            times_ms=[10.0, 10.0, 10.0],
            returncodes=[0, 0, 0],
            solver_hints=["7/ConvHipImplicitGemmGroupFwdXdlops"],
            device_ids=["1", "1", "1"],
        ),
    }
    arm_b = {
        same: CommandResult(
            command=same,
            times_ms=[12.0, 12.0, 12.0],
            returncodes=[0, 0, 0],
            solver_hints=["GemmFwdRest:some-config"],
            device_ids=["4", "4", "4"],
        ),
        other: CommandResult(
            command=other,
            times_ms=[8.0, 8.0, 8.0],
            returncodes=[0, 0, 0],
            solver_hints=["9/GemmFwdRest"],
            device_ids=["5", "5", "5"],
        ),
    }
    arm_a_path = tmp_path / "arm_a.jsonl"
    arm_b_path = tmp_path / "arm_b.jsonl"
    save_results(arm_a_path, arm_a)
    save_results(arm_b_path, arm_b)
    comparison = compare_arms(
        commands=[same, other],
        arm_a_results_path=arm_a_path,
        arm_b_results_path=arm_b_path,
        db_prefix="gfx950100",
        threshold_pct=2.0,
        benchmark_repeats=3,
        arm_b_tune_devices={other: "2"},
    )
    assert comparison["same_solver_count"] == 1
    assert comparison["different_solver_exhaustive_slower"] == []
    faster = comparison["different_solver_production_slower"]
    assert len(faster) == 1
    assert faster[0]["command"] == other
    assert faster[0]["arm_a_device_ids"] == ["1", "1", "1"]
    assert faster[0]["arm_b_tune_device"] == "2"
    assert comparison["different_solver_ms_left_on_table"] == pytest.approx(2.0)
    same_row = next(item for item in comparison["entries"] if item["command"] == same)
    assert same_row["same_solver"] is True
    assert same_row["same_kernel"] is True
    assert same_row["parity"] == "exhaustive_slower"


def test_load_udb_solver_map_keeps_primary_config(tmp_path):
    from miopen_convolution import MIOpenConvolution

    udb = tmp_path / "gfx.udb.txt"
    primary = "ConvAsmImplicitGemmGTCDynamicFwdXdlopsNHWC:fwd,nhwc,bf16,0,1,128,256"
    alternative = "ConvHipImplicitGemmGroupFwdXdlops:DeviceGroupedConvFwd<256, 128, OddC>"
    key = "2x128x1024x1024x1x3x3x1x128x1x1x1x0x1x1x0x1x1x0x0x1xNCHWxBF16xF"
    udb.write_text(f"{key}={primary};{alternative}\n")
    loaded = load_udb_solver_map(udb)
    conv = MIOpenConvolution.from_miopendriver_command(COMMAND)
    assert loaded[conv] == primary


def test_load_udb_solver_map_matches_backward_data_problem(tmp_path):
    from miopen_convolution import MIOpenConvolution

    udb = tmp_path / "gfx.udb.txt"
    primary = "ConvAsmImplicitGemmGTCDynamicBwdXdlopsNHWC:bwd,nhwc,fp32,0,1,64"
    # Driver -F 2 stores x; the perf DB stores y as "in" and x as "out".
    # out_w = (10350 + 2*1 - 4) / 2 + 1 = 5175
    key = "2x256x1x5175x1x1x4x1x128x2x0x1x0x1x2x0x1x1x0x0x1xNCHWxFP32xB"
    udb.write_text(f"{key}={primary}\n")
    loaded = load_udb_solver_map(udb)
    command = (
        "MIOpenDriver conv -n 2 -c 128 -H 1 -W 10350 -k 256 -y 1 -x 4 "
        "-p 0 -q 1 -u 1 -v 2 -l 1 -j 1 -m conv -g 1 -F 2 -t 1"
    )
    conv = MIOpenConvolution.from_miopendriver_command(command)
    assert loaded[perf_db_problem(conv)] == primary


def test_find_system_udb_prefers_installed_perf_db(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    (db_dir / "gfx950100.HIP.fdb.txt").write_text("find-db\n")
    (db_dir / "gfx950100.udb.txt").write_text("user-copy\n")
    installed = db_dir / "gfx950100.db.txt"
    installed.write_text(
        "2x128x1024x1024x1x3x3x1x128x1x1x1x0x1x1x0x1x1x0x0x1xNCHWxBF16xF=SolverA:cfg\n"
    )
    monkeypatch.setenv("MIOPEN_SYSTEM_DB_PATH", str(db_dir))
    found = find_system_udb("gfx950100")
    assert found == installed
    loaded = load_udb_solver_map(found)
    assert any(solver == "SolverA:cfg" for solver in loaded.values())


def test_find_system_udb_falls_back_to_udb(tmp_path, monkeypatch):
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    (db_dir / "gfx942130.HIP.fdb.txt").write_text("find-db\n")
    legacy = db_dir / "gfx942130.udb.txt"
    legacy.write_text("k=v\n")
    monkeypatch.setenv("MIOPEN_SYSTEM_DB_PATH", str(db_dir))
    assert find_system_udb("gfx942130") == legacy


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
