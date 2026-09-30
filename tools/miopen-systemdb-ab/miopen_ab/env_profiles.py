"""Environment variable profiles for each experiment arm."""

from __future__ import annotations

from pathlib import Path

from distrituner.miopen_tuner import create_miopen_worker_environment

# Disable naive direct conv solvers during find/tune (matches data/miopen/tune.sh).
MIOPEN_DEBUG_CONV_DIRECT = 0

# MIOpen default when MIOPEN_FIND_ENFORCE is unset (NONE).
MIOPEN_FIND_ENFORCE_NONE = 1
# SEARCH_DB_UPDATE: force a full search and write the user performance DB.
MIOPEN_FIND_ENFORCE_SEARCH_DB_UPDATE = 4


def prepare_empty_user_db(path: Path) -> Path:
    """Create an empty Arm A user DB directory.

    The directory is not seeded from the repository or from /miopen_userdb.
    With MIOPEN_FIND_ENFORCE=1, MIOpen then uses the installed system DB and
    production heuristics.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def arm_a_worker_envs(device_ids: list[str], user_db_path: Path) -> list[dict[str, str]]:
    """Out-of-the-box path: ENFORCE=1, default find mode, empty user DB."""
    if not user_db_path.is_dir():
        raise FileNotFoundError(f"Arm A user DB directory not found: {user_db_path}")
    envs: list[dict[str, str]] = []
    for device_id in device_ids:
        env = create_miopen_worker_environment(
            device_id.strip(),
            tuning_database_path=user_db_path,
            miopen_find_mode=None,
            miopen_find_enforce=MIOPEN_FIND_ENFORCE_NONE,
            miopen_debug_conv_direct=MIOPEN_DEBUG_CONV_DIRECT,
        )
        envs.append(env)
    return envs


def arm_b_tune_worker_envs(
    device_ids: list[str], tuning_root: Path
) -> list[dict[str, str]]:
    """Exhaustive override: SEARCH_DB_UPDATE, SYSTEM_DB_PATH equals USER_DB_PATH."""
    tuning_root.mkdir(parents=True, exist_ok=True)
    envs: list[dict[str, str]] = []
    for device_id in device_ids:
        device_db = tuning_root / f"device_{device_id.strip()}"
        device_db.mkdir(parents=True, exist_ok=True)
        env = create_miopen_worker_environment(
            device_id.strip(),
            tuning_database_path=device_db,
            miopen_find_mode=1,
            miopen_find_enforce=MIOPEN_FIND_ENFORCE_SEARCH_DB_UPDATE,
            miopen_debug_conv_direct=MIOPEN_DEBUG_CONV_DIRECT,
            miopen_system_db_path=device_db,
        )
        envs.append(env)
    return envs


def arm_b_benchmark_worker_envs(
    device_ids: list[str], merged_db_path: Path
) -> list[dict[str, str]]:
    """Time the merged user DB with the default find mode, without another full search."""
    merged_db_path.mkdir(parents=True, exist_ok=True)
    envs: list[dict[str, str]] = []
    for device_id in device_ids:
        env = create_miopen_worker_environment(
            device_id.strip(),
            tuning_database_path=merged_db_path,
            miopen_find_mode=None,
            miopen_find_enforce=MIOPEN_FIND_ENFORCE_NONE,
            miopen_debug_conv_direct=MIOPEN_DEBUG_CONV_DIRECT,
        )
        envs.append(env)
    return envs


ARM_A_METHODOLOGY = {
    "description": "Out-of-the-box path (empty user DB, system DB, heuristics)",
    "MIOPEN_FIND_ENFORCE": "1 (NONE — MIOpen default, no forced auto-tune)",
    "MIOPEN_FIND_MODE": "unset (default DYNAMIC_HYBRID / 5)",
    "MIOPEN_DEBUG_CONV_DIRECT": "0",
    "MIOPEN_USER_DB_PATH": "empty directory created for this run (arm_a/user_db)",
    "MIOPEN_SYSTEM_DB_PATH": "default install path",
    "measurement": "MIOpenDriver inline timing (-t 1) without incremental DB updates",
}

ARM_B_TUNE_METHODOLOGY = {
    "description": "Exhaustive tuning with system DB override (SEARCH_DB_UPDATE)",
    "MIOPEN_FIND_ENFORCE": "4 (SEARCH_DB_UPDATE — search and write the user DB)",
    "MIOPEN_FIND_MODE": "1 (NORMAL — benchmark applicable solvers)",
    "MIOPEN_SYSTEM_DB_PATH": "same as MIOPEN_USER_DB_PATH per device",
    "MIOPEN_DEBUG_CONV_DIRECT": "0",
}

ARM_B_BENCHMARK_METHODOLOGY = {
    "description": "Time the merged exhaustive user DB without a second full search",
    "MIOPEN_FIND_ENFORCE": "1 (NONE)",
    "MIOPEN_FIND_MODE": "unset (default DYNAMIC_HYBRID / 5)",
    "MIOPEN_DEBUG_CONV_DIRECT": "0",
    "MIOPEN_USER_DB_PATH": "tuning_merged/",
}
