import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "src"))

from lib.env_profiles import (
    arm_a_worker_envs,
    arm_b_benchmark_worker_envs,
    arm_b_tune_worker_envs,
)


def test_all_arms_set_miopen_debug_conv_direct_zero(tmp_path):
    device_ids = ["0", "1"]
    for envs in (
        arm_a_worker_envs(device_ids, tmp_path / "arm_a"),
        arm_b_tune_worker_envs(device_ids, tmp_path / "arm_b_tune"),
        arm_b_benchmark_worker_envs(device_ids, tmp_path / "arm_b_bench"),
    ):
        assert len(envs) == 2
        for env in envs:
            assert env["MIOPEN_DEBUG_CONV_DIRECT"] == "0"
