"""Utilities for locating and invoking MIOpenDriver."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_MIOpenDriver_PATH: Path | None = None


def find_miopendriver() -> Path:
    global _MIOpenDriver_PATH
    if _MIOpenDriver_PATH is not None:
        return _MIOpenDriver_PATH

    found = shutil.which("MIOpenDriver")
    if found:
        _MIOpenDriver_PATH = Path(found)
        return _MIOpenDriver_PATH

    proc = subprocess.run(
        ["find", "/opt", "/usr", "-type", "f", "-name", "MIOpenDriver", "-executable"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in proc.stdout.splitlines():
        path = Path(line.strip())
        if path.is_file():
            _MIOpenDriver_PATH = path
            return path

    raise FileNotFoundError("Executable MIOpenDriver not found")


def ensure_miopendriver_on_path() -> Path:
    path = find_miopendriver()
    link = Path("/bin/MIOpenDriver")
    if not link.exists():
        try:
            link.symlink_to(path)
            logger.info("Created symlink %s -> %s", link, path)
        except OSError:
            logger.debug("Could not create %s symlink; using %s directly", link, path)
    return path


def command_with_driver_path(command: str) -> str:
    if command.strip().startswith("MIOpenDriver"):
        driver = find_miopendriver()
        return command.replace("MIOpenDriver", str(driver), 1)
    return command
