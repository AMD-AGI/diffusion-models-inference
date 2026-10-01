import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "src"))

from compare_versions import main
from miopen_ab.version_compare import (
    LoadedRun,
    VersionSide,
    compare_runs,
    load_run,
    render_version_report_md,
    write_version_report,
)


def _shape(height: int) -> dict:
    return {
        "batchsize": 1,
        "in_channels": 128,
        "in_h": height,
        "in_w": 64,
        "in_d": None,
        "out_channels": 128,
        "fil_h": 3,
        "fil_w": 3,
        "fil_d": None,
        "direction": "F",
        "precision": "FP16",
        "in_layout": "NHWC",
    }


def _entry(
    command: str,
    arm_a_ms: float | None,
    arm_b_ms: float | None,
    *,
    arm_a_solver: str = "GemmFwdRest",
    arm_b_solver: str = "GemmFwdRest",
    sources: list[str] | None = None,
    in_system_db: bool = False,
    height: int = 8,
) -> dict:
    return {
        "command": command,
        "arm_a_median_ms": arm_a_ms,
        "arm_b_median_ms": arm_b_ms,
        "arm_a_stddev_ms": 0.01,
        "arm_b_stddev_ms": 0.01,
        "arm_a_solver": arm_a_solver,
        "arm_b_solver": arm_b_solver,
        "in_system_db": in_system_db,
        "source_files": sources or ["data/miopen/workloads/flux.single_gpu.txt"],
        "shape": _shape(height),
    }


def _side(run_id: str, version: str, **overrides) -> VersionSide:
    payload = {
        "run_dir": f"/tmp/{run_id}",
        "run_id": run_id,
        "label": f"MIOpen {version}",
        "short_label": version,
        "miopen_version": f"MIOpen (version: {version})",
        "rocm_version": "6.0.0",
        "hip_version": "6.0.0",
        "docker_image": "image:test",
        "timestamp_utc": "2026-10-01T00:00:00+00:00",
        "hostname": "host",
        "db_prefix": "gfx950100",
        "system_db_path": None,
        "benchmark_repeats": 3,
    }
    payload.update(overrides)
    return VersionSide(**payload)


def _runs(baseline_entries: dict, candidate_entries: dict, **candidate_overrides):
    baseline = LoadedRun(_side("baseline_run", "3.5.2"), baseline_entries)
    candidate = LoadedRun(
        _side("candidate_run", "3.6.1", **candidate_overrides),
        candidate_entries,
    )
    return compare_runs(baseline, candidate, 2.0)


def test_classifies_speedup_regression_and_threshold_boundary():
    command_fast = "MIOpenDriver conv -n 1 -c 128 -H 8 -W 64"
    command_slow = "MIOpenDriver conv -n 1 -c 128 -H 16 -W 64"
    command_edge = "MIOpenDriver conv -n 1 -c 128 -H 32 -W 64"
    long_kernel = "ConvHipImplicitGemmGroupFwdXdlops:" + ("very-long-kernel-config-" * 8)
    report = _runs(
        {
            command_fast: _entry(command_fast, 2.0, 2.0, height=8),
            command_slow: _entry(
                command_slow,
                1.0,
                1.0,
                arm_a_solver="GemmFwdRest",
                height=16,
            ),
            command_edge: _entry(command_edge, 100.0, 100.0, height=32),
        },
        {
            command_fast: _entry(
                command_fast,
                1.0,
                2.0,
                arm_a_solver=long_kernel,
                height=8,
            ),
            command_slow: _entry(
                command_slow,
                1.5,
                1.0,
                arm_a_solver="ConvHipImplicitGemmGroupFwdXdlops:cfg-a",
                height=16,
            ),
            command_edge: _entry(command_edge, 98.0, 100.0, height=32),
        },
    )
    production = report["production"]
    assert production["counts"] == {"improved": 1, "similar": 1, "worse": 1, "failed": 0}
    assert production["improved"][0]["command"] == command_fast
    assert production["improved"][0]["delta_ms"] == pytest.approx(1.0)
    assert production["improved"][0]["speedup_pct"] == pytest.approx(50.0)
    assert production["worse"][0]["command"] == command_slow
    assert production["worse"][0]["delta_ms"] == pytest.approx(-0.5)
    assert production["similar"][0]["command"] == command_edge
    assert production["net_delta_ms"] == pytest.approx(2.5)
    assert production["kernel_difference_counts"]["improved"]["different_solver"] == 1
    assert production["kernel_difference_counts"]["worse"]["different_solver"] == 1

    md = render_version_report_md(report)
    assert "MIOpen 3.6.1 is 2.5000 ms faster in total" in md
    assert "n1 c128 8x64 k128 3x3 F FP16 NHWC" in md
    assert "very-long-kernel-config-" * 8 not in md
    assert "miss / miss" in md
    assert "solver changed" in md


def test_same_kernel_within_threshold_stays_out_of_the_kernel_section():
    command = "MIOpenDriver conv -n 1 -c 128 -H 8 -W 64"
    report = _runs(
        {command: _entry(command, 1.0, 1.0)},
        {command: _entry(command, 1.01, 1.0)},
    )
    md = render_version_report_md(report)
    assert "### Similar speed, different kernel" in md
    assert md.split("### Similar speed, different kernel", 1)[1].split("###", 1)[0].count(
        "n1 c128"
    ) == 0


def test_workload_rows_repeat_shared_commands_and_summary_does_not():
    command = "MIOpenDriver conv -n 1 -c 128 -H 8 -W 64"
    sources = [
        "data/miopen/workloads/flux.single_gpu.txt",
        "data/miopen/workloads/flux.usp.txt",
    ]
    report = _runs(
        {command: _entry(command, 2.0, 2.0, sources=sources)},
        {command: _entry(command, 1.0, 2.0, sources=sources)},
    )
    assert report["production"]["counts"]["improved"] == 1
    by_source = {item["source_file"]: item for item in report["production"]["by_source"]}
    assert set(by_source) == set(sources)
    assert by_source[sources[0]]["improved"] == 1
    assert by_source[sources[1]]["ms_faster"] == pytest.approx(1.0)
    md = render_version_report_md(report)
    assert "flux.single_gpu.txt" in md
    assert "flux.usp.txt" in md


def test_unmatched_failed_and_identical_version_labels():
    shared = "MIOpenDriver conv -n 1 -c 128 -H 8 -W 64"
    only_base = "MIOpenDriver conv -n 1 -c 128 -H 16 -W 64"
    only_cand = "MIOpenDriver conv -n 1 -c 128 -H 32 -W 64"
    failed = "MIOpenDriver conv -n 1 -c 128 -H 48 -W 64"
    baseline = LoadedRun(
        _side("run_a", "3.6.1"),
        {
            shared: _entry(shared, 1.0, 1.0, height=8),
            only_base: _entry(only_base, 1.0, 1.0, height=16),
            failed: _entry(failed, None, 1.0, height=48),
        },
    )
    candidate = LoadedRun(
        _side("run_b", "3.6.1"),
        {
            shared: _entry(shared, 1.0, None, height=8),
            only_cand: _entry(only_cand, 1.0, 1.0, height=32),
            failed: _entry(failed, 1.0, 1.0, height=48),
        },
    )
    report = compare_runs(baseline, candidate, 2.0)
    assert report["baseline"]["short_label"] == "3.6.1 (run_a)"
    assert report["candidate"]["short_label"] == "3.6.1 (run_b)"
    assert report["production"]["counts"]["failed"] == 1
    assert report["exhaustive"]["counts"]["failed"] == 1
    assert [item["command"] for item in report["unmatched"]["baseline_only"]] == [only_base]
    assert [item["command"] for item in report["unmatched"]["candidate_only"]] == [only_cand]
    md = render_version_report_md(report)
    assert "Commands present in one run" in md
    assert "3.6.1 (run_a)" in md


def test_stack_difference_lists_rocm_and_system_db():
    command = "MIOpenDriver conv -n 1 -c 128 -H 8 -W 64"
    report = _runs(
        {command: _entry(command, 2.0, 1.0, in_system_db=False)},
        {command: _entry(command, 1.0, 1.0, in_system_db=True)},
        rocm_version="10.1.0",
        system_db_path="/opt/rocm/share/miopen/db/gfx950100.db.txt",
    )
    assert any(item.startswith("ROCm ") for item in report["stack_differences"])
    assert any(item.startswith("system DB ") for item in report["stack_differences"])
    md = render_version_report_md(report)
    assert "outside the MIOpen version string" in md
    assert "miss / hit" in md
    assert report["candidate"]["system_db_hits"] == 1


def test_write_report_keeps_full_solver_and_leaves_runs_untouched(tmp_path: Path):
    baseline_dir = tmp_path / "runs" / "old"
    candidate_dir = tmp_path / "runs" / "new"
    command = "MIOpenDriver conv -n 1 -c 128 -H 8 -W 64"
    long_kernel = "SolverA:" + ("very-long-kernel-config-" * 6)
    _write_run(
        baseline_dir,
        [_entry(command, 2.0, 2.0, arm_a_solver="GemmFwdRest")],
        miopen="MIOpen (version: 3.5.2)",
        rocm="7.14.0",
    )
    _write_run(
        candidate_dir,
        [_entry(command, 1.0, 2.0, arm_a_solver=long_kernel)],
        miopen="MIOpen (version: 3.6.1)",
        rocm="10.1.0",
    )
    before = {
        path: path.read_bytes()
        for path in (baseline_dir / "comparison.json", candidate_dir / "comparison.json")
    }
    output_dir = tmp_path / "comparisons" / "old_vs_new"
    exit_code = main(
        [
            "--baseline",
            str(baseline_dir),
            "--candidate",
            str(candidate_dir),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == 0
    assert before == {
        path: path.read_bytes()
        for path in (baseline_dir / "comparison.json", candidate_dir / "comparison.json")
    }
    assert not (baseline_dir / "report.md").exists()
    payload = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    solver = payload["production"]["improved"][0]["candidate_solver"]
    assert solver.endswith("very-long-kernel-config-")
    markdown = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "very-long-kernel-config-" * 6 not in markdown
    assert "MIOpen 3.6.1 is 1.0000 ms faster in total" in markdown

    with pytest.raises(SystemExit):
        main(
            [
                "--baseline",
                str(baseline_dir),
                "--candidate",
                str(candidate_dir),
                "--output-dir",
                str(baseline_dir / "nested"),
            ]
        )


def test_load_run_reads_version_from_metadata(tmp_path: Path):
    run_dir = tmp_path / "20261001_011510"
    _write_run(
        run_dir,
        [_entry("MIOpenDriver conv -n 1", 1.0, 1.0)],
        miopen="MIOpen (version: 3.5.2)",
        rocm="7.14.0",
    )
    loaded = load_run(run_dir)
    assert loaded.side.label == "MIOpen 3.5.2"
    assert loaded.side.short_label == "3.5.2"
    assert loaded.side.rocm_version == "7.14.0"
    assert len(loaded.entries) == 1


def test_missing_comparison_is_an_error(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        load_run(empty)
    with pytest.raises(SystemExit):
        main(["--baseline", str(empty), "--candidate", str(empty)])


def _write_run(path: Path, entries: list[dict], miopen: str, rocm: str) -> None:
    path.mkdir(parents=True)
    (path / "comparison.json").write_text(
        json.dumps({"benchmark_repeats": 3, "entries": entries}),
        encoding="utf-8",
    )
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "miopen_driver_version": miopen,
                "rocm_version": rocm,
                "docker_image": "image:test",
                "timestamp_utc": "2026-10-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def test_write_version_report_round_trip(tmp_path: Path):
    command = "MIOpenDriver conv -n 1 -c 128 -H 8 -W 64"
    report = _runs(
        {command: _entry(command, 1.0, 1.0)},
        {command: _entry(command, 1.0, 1.0)},
    )
    md_path, json_path = write_version_report(tmp_path / "out", report)
    assert md_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["matched_commands"] == 1
    assert payload["output_dir"].endswith("/out")
