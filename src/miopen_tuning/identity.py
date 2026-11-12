# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Detect MIOpen, ROCm, device, and DB identity."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCHEMA_VERSION = 1
_DRIVER_VERSION_RE = re.compile(r"MIOpen\s+\(version:\s*([^)]+)\)")
_HEADER_RE = re.compile(r"#define\s+MIOPEN_VERSION_(MAJOR|MINOR|PATCH|TWEAK)\s+(\S+)")
_AGENT_RE = re.compile(r"^Agent\s+\d+")
# Agent properties are indented by two spaces; deeper lines, such as ISA names, are skipped.
_AGENT_FIELD_RE = re.compile(r"^  (Name|Marketing Name|Device Type|Compute Unit):\s+(.*?)\s*$")


@dataclass(frozen=True)
class Identity:
    schema_version: int
    captured_at: str
    image: str
    image_digest: str
    rocm_release: str
    miopen_version: str
    miopen_build: str
    driver_path: str
    driver_version_output: str
    device_names: list[str]
    db_prefix: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GpuAgent:
    architecture: str
    marketing_name: str
    compute_units: int

    @property
    def db_prefix(self) -> str:
        """MIOpen names DB files after the gfx architecture and the compute unit count in hex."""
        return f"{self.architecture}{self.compute_units:x}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_miopendriver(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    found = shutil.which("MIOpenDriver")
    if found:
        candidates.append(Path(found))
    for variable in ("ROCM_PATH", "ROCM_HOME"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "bin" / "MIOpenDriver")
    candidates.append(Path("/opt/rocm/bin/MIOpenDriver"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise FileNotFoundError("Executable MIOpenDriver was not found")


def run_capture(argv: list[str], timeout: int = 60, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    try:
        process = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return process.returncode, process.stdout, process.stderr


def parse_driver_version(text: str) -> str:
    match = _DRIVER_VERSION_RE.search(text)
    return match.group(1).strip() if match else ""


def parse_version_header(text: str) -> tuple[str, str]:
    values = dict(_HEADER_RE.findall(text))
    if not all(name in values for name in ("MAJOR", "MINOR", "PATCH")):
        return "", ""
    return f"{values['MAJOR']}.{values['MINOR']}.{values['PATCH']}", values.get("TWEAK", "")


def version_header_candidates(roots: Iterable[Path] | None = None) -> list[Path]:
    if roots is None:
        roots = [Path(os.environ[name]) for name in ("ROCM_PATH", "ROCM_HOME") if os.environ.get(name)]
        roots.append(Path("/opt/rocm"))
    candidates: list[Path] = []
    for root in roots:
        candidates.append(root / "include" / "miopen" / "version.h")
        candidates.extend(sorted(root.glob("core-*/include/miopen/version.h")))
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def read_header_version(roots: Iterable[Path] | None = None) -> tuple[str, str]:
    for candidate in version_header_candidates(roots):
        if candidate.is_file():
            version, build = parse_version_header(candidate.read_text(errors="replace"))
            if version:
                return version, build
    return "", ""


def parse_gpu_agents(text: str) -> list[GpuAgent]:
    """Parse the GPU agents from rocminfo output."""
    blocks: list[dict[str, str]] = []
    for line in text.splitlines():
        if _AGENT_RE.match(line):
            blocks.append({})
            continue
        match = _AGENT_FIELD_RE.match(line)
        if blocks and match:
            blocks[-1].setdefault(match.group(1), match.group(2))
    return [
        GpuAgent(block.get("Name", ""), block.get("Marketing Name", ""), int(block["Compute Unit"]))
        for block in blocks
        if block.get("Device Type") == "GPU" and block.get("Compute Unit", "").isdigit()
    ]


def gpu_agents() -> list[GpuAgent]:
    returncode, stdout, _ = run_capture(["rocminfo"], timeout=30)
    if returncode != 0:
        return []
    return parse_gpu_agents(stdout)


def device_db_prefix(agents: list[GpuAgent]) -> str:
    """Return the MIOpen DB prefix shared by all GPUs."""
    prefixes = sorted({agent.db_prefix for agent in agents})
    if not prefixes:
        raise RuntimeError("rocminfo reported no GPU agents; cannot determine the MIOpen DB prefix")
    if len(prefixes) > 1:
        raise RuntimeError(f"GPUs use different MIOpen DB prefixes: {', '.join(prefixes)}")
    return prefixes[0]


def rocm_release() -> str:
    for variable in ("ROCM_WHEEL_VERSION", "ROCM_RELEASE_ID"):
        if os.environ.get(variable):
            return os.environ[variable]
    for path in (Path("/opt/rocm/.info/version"), Path("/opt/rocm/core/.info/version")):
        if path.is_file():
            return path.read_text().strip()
    return ""


def collect_identity(
    *,
    driver: str | None,
    image: str = "",
    image_digest: str = "",
    agents: list[GpuAgent] | None = None,
) -> Identity:
    driver_path = find_miopendriver(driver)
    returncode, stdout, stderr = run_capture([str(driver_path), "--version"])
    output = (stdout + stderr).strip()
    if returncode != 0:
        raise RuntimeError(f"MIOpenDriver --version failed with exit code {returncode}: {output}")
    header_version, build = read_header_version()
    version = parse_driver_version(output) or header_version
    agents = gpu_agents() if agents is None else agents
    prefix = device_db_prefix(agents)
    names = list(dict.fromkeys(agent.marketing_name for agent in agents if agent.marketing_name))
    return Identity(
        schema_version=SCHEMA_VERSION,
        captured_at=utc_now(),
        image=image,
        image_digest=image_digest,
        rocm_release=rocm_release(),
        miopen_version=version,
        miopen_build=build,
        driver_path=str(driver_path),
        driver_version_output=output,
        device_names=names,
        db_prefix=prefix,
    )


def fixture_version_name(miopen_version: str, miopen_build: str) -> str:
    value = f"{miopen_version}-{miopen_build}" if miopen_build else miopen_version
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def expected_db_version_token(miopen_version: str, miopen_build: str) -> str:
    if not miopen_version or not miopen_build:
        return ""
    return f"{miopen_version.replace('.', '_')}_{miopen_build}"
