import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from miopen_ab.report import render_report_md, write_reports


def test_render_report_md_includes_improvements():
    metadata = {
        "timestamp_utc": "2026-08-21T00:00:00+00:00",
        "docker_image": "test:image",
        "hostname": "testhost",
        "hip_visible_devices": "0",
        "db_prefix": "gfx942130",
        "rocm_version": "6.4.0",
        "hip_version": "6.4.0",
        "miopen_driver_version": "3.5.0",
        "kernel_cache_dir": "/root/.cache/miopen",
        "experiment_config": {"command_count": 1},
    }
    comparison = {
        "threshold_pct": 2.0,
        "benchmark_repeats": 3,
        "primary_ab_count": 1,
        "system_udb_path": "/opt/rocm/share/miopen/db/test.udb.txt",
        "counts": {
            "improvement": 1,
            "no_change": 0,
            "regression": 0,
            "system_db_miss": 0,
            "failure": 0,
            "arch_mismatch_or_error": 0,
        },
        "improvements": [
            {
                "command": "MIOpenDriver convbfp16 -n 1 -c 1 -H 8 -W 8 -k 1 -y 1 -x 1 -F 1 -t 1",
                "arm_a_median_ms": 1.0,
                "arm_b_median_ms": 0.8,
                "speedup_pct": 20.0,
                "arm_a_solver": "SolverA",
                "arm_b_solver": "SolverB",
                "system_db_solver": "SolverA",
            }
        ],
        "regressions": [],
        "no_change": [],
        "system_db_misses": [],
        "failures": [],
    }
    md = render_report_md(metadata, comparison, Path("/tmp/run"))
    assert "Improvements (exhaustive faster than production heuristics)" in md
    assert "SolverB" in md

    output = Path("/tmp/miopen_ab_report_test")
    output.mkdir(exist_ok=True)
    md_path, json_path = write_reports(output, metadata, comparison)
    assert md_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["summary"]["improvement"] == 1
