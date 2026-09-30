import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / "src"))

from miopen_ab.env_profiles import (
    arm_a_worker_envs,
    arm_b_benchmark_worker_envs,
    arm_b_tune_worker_envs,
    prepare_empty_user_db,
)


def test_all_arms_set_miopen_debug_conv_direct_zero(tmp_path):
    device_ids = ["0", "1"]
    (tmp_path / "arm_a").mkdir()
    for envs in (
        arm_a_worker_envs(device_ids, tmp_path / "arm_a"),
        arm_b_tune_worker_envs(device_ids, tmp_path / "arm_b_tune"),
        arm_b_benchmark_worker_envs(device_ids, tmp_path / "arm_b_bench"),
    ):
        assert len(envs) == 2
        for env in envs:
            assert env["MIOPEN_DEBUG_CONV_DIRECT"] == "0"


def test_arm_a_uses_an_empty_user_db(tmp_path):
    user_db = prepare_empty_user_db(tmp_path / "arm_a" / "user_db")
    assert user_db.is_dir()
    assert list(user_db.glob("*.udb.txt")) == []
    envs = arm_a_worker_envs(["0"], user_db)
    assert envs[0]["MIOPEN_USER_DB_PATH"] == str(user_db)
    assert envs[0]["MIOPEN_FIND_ENFORCE"] == "1"
    assert envs[0]["MIOPEN_PERFORMANCE_LOGS"] == "1"
    assert "MIOPEN_FIND_MODE" not in envs[0]
    assert "MIOPEN_SYSTEM_DB_PATH" not in envs[0]


def test_arm_b_tune_uses_search_db_update(tmp_path):
    envs = arm_b_tune_worker_envs(["0"], tmp_path / "tune")
    assert envs[0]["MIOPEN_FIND_ENFORCE"] == "4"
    assert envs[0]["MIOPEN_FIND_MODE"] == "1"
    assert envs[0]["MIOPEN_SYSTEM_DB_PATH"] == envs[0]["MIOPEN_USER_DB_PATH"]
    assert "MIOPEN_PERFORMANCE_LOGS" not in envs[0]


def test_arm_b_benchmark_uses_merged_db_without_full_find(tmp_path):
    merged = tmp_path / "merged"
    envs = arm_b_benchmark_worker_envs(["0"], merged)
    assert envs[0]["MIOPEN_FIND_ENFORCE"] == "1"
    assert "MIOPEN_FIND_MODE" not in envs[0]
    assert envs[0]["MIOPEN_USER_DB_PATH"] == str(merged)
    assert envs[0]["MIOPEN_PERFORMANCE_LOGS"] == "1"
