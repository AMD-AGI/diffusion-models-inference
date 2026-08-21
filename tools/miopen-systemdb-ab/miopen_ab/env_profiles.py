"""Environment variable profiles for each experiment arm."""

from __future__ import annotations

from pathlib import Path

from distrituner.miopen_tuner import create_miopen_worker_environment

# Disable naive direct conv solvers during find/tune (matches data/miopen/tune.sh).
MIOPEN_DEBUG_CONV_DIRECT = 0


def arm_a_worker_envs(device_ids: list[str], user_db_path: Path) -> list[dict[str, str]]:
    """Production-like path: ENFORCE=3, default find mode, shared user DB."""
    user_db_path.mkdir(parents=True, exist_ok=True)
    envs: list[dict[str, str]] = []
    for device_id in device_ids:
        env = create_miopen_worker_environment(
            device_id.strip(),
            tuning_database_path=user_db_path,
            miopen_find_mode=None,
            miopen_find_enforce=3,
            miopen_debug_conv_direct=MIOPEN_DEBUG_CONV_DIRECT,
        )
        envs.append(env)
    return envs


def arm_b_tune_worker_envs(
    device_ids: list[str], tuning_root: Path
) -> list[dict[str, str]]:
    """Exhaustive override: ENFORCE=3, SYSTEM_DB_PATH equals USER_DB_PATH."""
    tuning_root.mkdir(parents=True, exist_ok=True)
    envs: list[dict[str, str]] = []
    for device_id in device_ids:
        device_db = tuning_root / f"device_{device_id.strip()}"
        device_db.mkdir(parents=True, exist_ok=True)
        env = create_miopen_worker_environment(
            device_id.strip(),
            tuning_database_path=device_db,
            miopen_find_mode=1,
            miopen_find_enforce=3,
            miopen_debug_conv_direct=MIOPEN_DEBUG_CONV_DIRECT,
            miopen_system_db_path=device_db,
        )
        envs.append(env)
    return envs


def arm_b_benchmark_worker_envs(
    device_ids: list[str], merged_db_path: Path
) -> list[dict[str, str]]:
    """Benchmark tuned user DB without further tuning."""
    merged_db_path.mkdir(parents=True, exist_ok=True)
    envs: list[dict[str, str]] = []
    for device_id in device_ids:
        env = create_miopen_worker_environment(
            device_id.strip(),
            tuning_database_path=merged_db_path,
            miopen_find_mode=1,
            miopen_find_enforce=1,
            miopen_debug_conv_direct=MIOPEN_DEBUG_CONV_DIRECT,
        )
        envs.append(env)
    return envs


ARM_A_METHODOLOGY = {
    "description": "Production-like incremental tuning with system DB",
    "MIOPEN_FIND_ENFORCE": "3",
    "MIOPEN_FIND_MODE": "unset (default DYNAMIC_HYBRID / 5)",
    "MIOPEN_DEBUG_CONV_DIRECT": "0",
    "MIOPEN_USER_DB_PATH": "empty at start, shared across workers",
    "MIOPEN_SYSTEM_DB_PATH": "default install path",
    "measurement": "inline timing from find/tune driver invocation",
}

ARM_B_TUNE_METHODOLOGY = {
    "description": "Exhaustive tuning with system DB override (docs Method 1)",
    "MIOPEN_FIND_ENFORCE": "3",
    "MIOPEN_FIND_MODE": "1",
    "MIOPEN_SYSTEM_DB_PATH": "same as MIOPEN_USER_DB_PATH per device",
    "MIOPEN_DEBUG_CONV_DIRECT": "0",
}

ARM_B_BENCHMARK_METHODOLOGY = {
    "description": "Benchmark merged exhaustive user DB",
    "MIOPEN_FIND_ENFORCE": "1",
    "MIOPEN_FIND_MODE": "1",
    "MIOPEN_DEBUG_CONV_DIRECT": "0",
    "MIOPEN_USER_DB_PATH": "tuning_merged/",
}
