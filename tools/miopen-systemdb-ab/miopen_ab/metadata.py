"""Collect environment and version metadata for experiment reports."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from .miopendriver import find_miopendriver


def _run(command: list[str]) -> str:
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    return (proc.stdout or proc.stderr).strip()


def _miopen_driver_version() -> str:
    try:
        driver = find_miopendriver()
        return _run([str(driver), "--version"])
    except FileNotFoundError:
        return _run(["MIOpenDriver", "--version"])


def _resolve_db_prefix(repo_root: Path) -> str:
    script = repo_root / "data/miopen/resolve_prefix.sh"
    if not script.exists():
        return ""
    proc = subprocess.run(
        [str(script)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo_root,
    )
    return proc.stdout.strip()


def collect_metadata(
    repo_root: Path,
    experiment_config: dict[str, Any],
) -> dict[str, Any]:
    rocm_version = ""
    version_file = Path("/opt/rocm/.info/version")
    if version_file.exists():
        rocm_version = version_file.read_text(encoding="utf-8").strip()

    metadata = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": platform.node(),
        "docker_image": os.environ.get("DOCKER_IMAGE", ""),
        "hip_visible_devices": os.environ.get("HIP_VISIBLE_DEVICES", ""),
        "gpu_marketing_names": _run(["rocminfo"]).splitlines(),
        "rocm_version": rocm_version,
        "hip_version": _run(["hipconfig", "--version"]),
        "miopen_driver_version": _miopen_driver_version(),
        "python_version": platform.python_version(),
        "db_prefix": _resolve_db_prefix(repo_root),
        "kernel_cache_dir": os.environ.get(
            "MIOPEN_CUSTOM_CACHE_DIR", str(Path.home() / ".cache/miopen")
        ),
        "experiment_config": experiment_config,
    }
    return metadata


def write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
