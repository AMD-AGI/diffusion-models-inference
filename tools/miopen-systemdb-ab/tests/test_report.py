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
        "parity_counts": {
            "equal": 0,
            "production_slower": 1,
            "exhaustive_slower": 0,
            "failed": 0,
        },
        "ms_left_on_table": 0.2,
        "by_source": [
            {
                "source_file": "data/miopen/workloads/flux.single_gpu.txt",
                "equal": 0,
                "production_slower": 1,
                "exhaustive_slower": 0,
                "failed": 0,
                "ms_left_on_table": 0.2,
            }
        ],
        "production_slower": [
            {
                "command": "MIOpenDriver convbfp16 -n 1 -c 1 -H 8 -W 8 -k 1 -y 1 -x 1 -F 1 -t 1",
                "arm_a_median_ms": 1.0,
                "arm_b_median_ms": 0.8,
                "delta_ms": 0.2,
                "speedup_pct": 20.0,
                "arm_a_solver": "SolverA:very-long-config-that-the-markdown-table-clips",
                "arm_b_solver": "SolverB",
                "system_db_solver": "SolverA:very-long-config-that-the-markdown-table-clips",
                "source_files": ["data/miopen/workloads/flux.single_gpu.txt"],
                "shape": {
                    "batchsize": 1,
                    "in_channels": 1,
                    "in_h": 8,
                    "in_w": 8,
                    "in_d": None,
                    "out_channels": 1,
                    "fil_h": 1,
                    "fil_w": 1,
                    "fil_d": None,
                    "direction": "F",
                    "precision": "BF16",
                    "in_layout": "NCHW",
                },
            }
        ],
        "equal": [],
        "exhaustive_slower": [],
        "improvements": [],
        "regressions": [],
        "no_change": [],
        "system_db_misses": [],
        "failures": [],
    }
    md = render_report_md(metadata, comparison, Path("/tmp/run"))
    assert "Production slower than exhaustive" in md
    assert "flux.single_gpu.txt" in md
    assert "n1 c1 8x8 k1 1x1 F BF16" in md
    assert "very-long-config-that-the-markdown-table-clips" not in md
    assert "SolverB" in md

    output = Path("/tmp/miopen_ab_report_test")
    output.mkdir(exist_ok=True)
    md_path, json_path = write_reports(output, metadata, comparison)
    assert md_path.exists()
    payload = json.loads(json_path.read_text())
    assert payload["summary"]["improvement"] == 1
    assert payload["production_slower"][0]["arm_a_solver"].endswith("clips")
    assert payload["by_source"][0]["production_slower"] == 1
