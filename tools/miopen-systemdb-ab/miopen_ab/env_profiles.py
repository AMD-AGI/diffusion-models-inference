"""Environment variable profiles for each experiment arm."""

from __future__ import annotations

from pathlib import Path

from distrituner.miopen_tuner import create_miopen_worker_environment

# Disable naive direct conv solvers during find/tune (matches data/miopen/tune.sh).
MIOPEN_DEBUG_CONV_DIRECT = 0

# MIOpen default when MIOPEN_FIND_ENFORCE is unset (NONE).
MIOPEN_FIND_ENFORCE_NONE = 1


def resolve_production_user_db(
    repo_root: Path, override: Path | str | None = None
) -> Path:
    """Return the prebuilt production user DB directory used by benchmark images."""
    if override is not None:
        path = Path(override).expanduser().resolve()
        if not path.is_dir():
            raise FileNotFoundError(f"Arm A user DB directory not found: {path}")
        return path

    candidates = [
        Path("/miopen_userdb"),
        repo_root / "data/miopen/userdb",
    ]
    for path in candidates:
        if path.is_dir() and any(path.glob("*.udb.txt")):
            return path.resolve()

    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Production user DB not found. Checked: "
        f"{searched}. Pass --arm-a-user-db to override."
    )


def arm_a_worker_envs(device_ids: list[str], user_db_path: Path) -> list[dict[str, str]]:
    """Production inference path: ENFORCE=1, default find mode, prebuilt user DB."""
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
    "description": "Production inference path (heuristics, no forced inline tuning)",
    "MIOPEN_FIND_ENFORCE": "1 (NONE — MIOpen default, no forced auto-tune)",
    "MIOPEN_FIND_MODE": "unset (default DYNAMIC_HYBRID / 5)",
    "MIOPEN_DEBUG_CONV_DIRECT": "0",
    "MIOPEN_USER_DB_PATH": "prebuilt production user DB (/miopen_userdb or data/miopen/userdb)",
    "MIOPEN_SYSTEM_DB_PATH": "default install path",
    "measurement": "MIOpenDriver inline timing (-t 1) without incremental DB updates",
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
