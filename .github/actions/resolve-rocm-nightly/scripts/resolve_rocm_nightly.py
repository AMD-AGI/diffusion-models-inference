#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Resolve the TheRock nightly ROCm wheel build for the nightly image.

The newest rocm-sdk-core build on the index is the target. The script gets
the requirement list for each build from "install_rocm_wheels.sh
--print-requirements" and checks the wheel file names on the index. It selects
the newest build that has all the pinned wheels. If that is not the newest
build, it writes a warning with the missing wheels. It also writes notices for
newer torch, torchvision, and torchaudio releases and for a new ROCm version.

The index does not serve wheel metadata (PEP 658), so a pip dry run would
download all the wheels. File name checks do not download wheels.

Outputs: GITHUB_OUTPUT keys, a GITHUB_STEP_SUMMARY section, workflow
annotations, and a provenance JSON file (--provenance).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path

# Dockerfile ARGs that the install script needs, and the index.
DOCKERFILE_ARGS = (
    "ROCM_WHEEL_INDEX",
    "ROCM_WHEEL_VERSION",
    "ROCM_GFX_TARGETS",
    "TORCH_VERSION",
    "TORCHVISION_VERSION",
    "TORCHAUDIO_VERSION",
)
# Pinned framework releases. A newer release for the target build is a notice.
FRAMEWORK_PINS = {
    "torch": "TORCH_VERSION",
    "torchvision": "TORCHVISION_VERSION",
    "torchaudio": "TORCHAUDIO_VERSION",
}
ROCM_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)a(\d{8})$")
REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(\S+)$")
HREF_RE = re.compile(r"""href=["']([^"'#]+\.whl)(?:#[^"']*)?["']""", re.IGNORECASE)

Fetch = Callable[[str], str]


@dataclasses.dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    filename: str


@dataclasses.dataclass
class Check:
    """Result of one requirement check for one ROCm build."""

    name: str
    version: str
    filename: str | None

    @property
    def found(self) -> bool:
        return self.filename is not None

    def as_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "version": self.version, "wheel": self.filename}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def rocm_version_key(version: str) -> tuple[int, ...]:
    match = ROCM_VERSION_RE.match(version)
    if not match:
        raise ValueError(f"not a ROCm nightly wheel version: {version}")
    return tuple(int(part) for part in match.groups())


def rocm_build_date(version: str) -> date:
    stamp = ROCM_VERSION_RE.match(version).group(4)
    return date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))


def release_key(version: str) -> tuple[int, ...]:
    """Sort key for the release part of a version such as 2.14.0, 2.11.0.3, or 2.16.0a0."""
    match = re.match(r"\d+(?:\.\d+)*", version)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def parse_dockerfile_args(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        match = re.match(r'^ARG\s+([A-Z0-9_]+)=(?:"([^"]*)"|(\S+))\s*$', line)
        if match and match.group(1) not in values:
            values[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
    missing = [name for name in DOCKERFILE_ARGS if not values.get(name)]
    if missing:
        raise ValueError(f"Dockerfile has no default for: {', '.join(missing)}")
    return {name: values[name] for name in DOCKERFILE_ARGS}


def parse_wheel(filename: str, python_tag: str, platform: str) -> Wheel | None:
    """Return the wheel if it installs on python_tag and platform."""
    parts = filename[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        return None
    name, version = parts[0], parts[1]
    pythons, abis, platforms = (set(tag.split(".")) for tag in parts[-3:])
    if not pythons & {"py3", python_tag}:
        return None
    if not abis & {"none", "abi3", python_tag}:
        return None
    if not platforms & {platform, "any"}:
        return None
    return Wheel(normalize(name), version, filename)


class Index:
    """Wheel lists of the projects on a PEP 503 simple index."""

    def __init__(self, url: str, fetch: Fetch, python_tag: str, platform: str):
        self.url = url.rstrip("/")
        self.fetch = fetch
        self.python_tag = python_tag
        self.platform = platform
        self._projects: dict[str, list[Wheel]] = {}

    def wheels(self, project: str) -> list[Wheel]:
        project = normalize(project)
        if project not in self._projects:
            page = self.fetch(f"{self.url}/{project}/")
            wheels = []
            for href in HREF_RE.findall(page):
                filename = urllib.parse.unquote(href.rsplit("/", 1)[-1])
                wheel = parse_wheel(filename, self.python_tag, self.platform)
                if wheel and wheel.name == project:
                    wheels.append(wheel)
            self._projects[project] = wheels
        return self._projects[project]

    def find(self, project: str, version: str) -> str | None:
        for wheel in self.wheels(project):
            if wheel.version == version:
                return wheel.filename
        return None

    def rocm_builds(self) -> list[str]:
        """rocm-sdk-core versions, newest first."""
        versions = {w.version for w in self.wheels("rocm-sdk-core") if ROCM_VERSION_RE.match(w.version)}
        return sorted(versions, key=rocm_version_key, reverse=True)

    def releases_for_build(self, project: str, rocm_version: str) -> list[str]:
        """Public versions of project built for rocm_version, newest first."""
        suffix = f"+rocm{rocm_version}"
        versions = {w.version[: -len(suffix)] for w in self.wheels(project) if w.version.endswith(suffix)}
        return sorted(versions, key=release_key, reverse=True)


def read_requirements(install_script: Path, pins: dict[str, str], rocm_version: str) -> list[tuple[str, str]]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("ROCM_", "TORCH"))}
    env.update(pins)
    env["ROCM_WHEEL_VERSION"] = rocm_version
    result = subprocess.run(
        ["bash", str(install_script), "--print-requirements"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    requirements = []
    for line in result.stdout.splitlines():
        match = REQUIREMENT_RE.match(line.strip())
        if not match:
            raise ValueError(f"{install_script} printed an unexpected requirement: {line!r}")
        requirements.append((normalize(match.group(1)), match.group(2)))
    if not requirements:
        raise ValueError(f"{install_script} printed no requirements")
    return requirements


def check_build(index: Index, requirements: list[tuple[str, str]], rocm_version: str) -> list[Check]:
    checks = [Check(name, version, index.find(name, version)) for name, version in requirements]
    # torch pins triton to an exact version that is known only from the torch
    # metadata. Look for a triton wheel with the same build suffix.
    suffix = f"rocm{rocm_version}"
    tritons = [w.filename for w in index.wheels("triton") if w.version.endswith(suffix)]
    checks.append(Check("triton", f"*{suffix} (pinned by torch)", sorted(tritons)[-1] if tritons else None))
    return checks


@dataclasses.dataclass
class Resolution:
    index: str
    pins: dict[str, str]
    python_tag: str
    platform: str
    newest: str
    selected: str
    newest_checks: list[Check]
    selected_checks: list[Check]
    newer_releases: dict[str, list[str]]

    @property
    def missing_in_newest(self) -> list[Check]:
        return [check for check in self.newest_checks if not check.found]

    @property
    def lag_days(self) -> int:
        return (rocm_build_date(self.newest) - rocm_build_date(self.selected)).days

    def annotations(self) -> list[tuple[str, str]]:
        notes = []
        if self.selected != self.newest:
            missing = ", ".join(f"{c.name}=={c.version}" for c in self.missing_in_newest)
            notes.append((
                "warning",
                f"Pinned wheels are not available for the newest ROCm build {self.newest}: {missing}. "
                f"Selected {self.selected} ({self.lag_days} days older). "
                "Update the pins in docker/Dockerfile.ci to follow the newest build.",
            ))
        pinned_line = self.pins["ROCM_WHEEL_VERSION"].split("a", 1)[0]
        newest_line = self.newest.split("a", 1)[0]
        if newest_line != pinned_line:
            notes.append((
                "notice",
                f"The newest ROCm build is ROCm {newest_line}; docker/Dockerfile.ci pins "
                f"ROCM_WHEEL_VERSION={self.pins['ROCM_WHEEL_VERSION']} (ROCm {pinned_line}).",
            ))
        for project, versions in self.newer_releases.items():
            notes.append((
                "notice",
                f"Newer {project} releases exist for ROCm build {self.newest}: {', '.join(versions)} "
                f"(pinned {FRAMEWORK_PINS[project]}={self.pins[FRAMEWORK_PINS[project]]}).",
            ))
        return notes

    def provenance(self) -> dict:
        return {
            "rocm_wheel_index": self.index,
            "python_tag": self.python_tag,
            "platform": self.platform,
            "dockerfile_pins": self.pins,
            "newest_rocm_wheel_version": self.newest,
            "rocm_wheel_version": self.selected,
            "pins_available_in_newest": not self.missing_in_newest,
            "selected_wheels": [check.as_dict() for check in self.selected_checks],
            "missing_in_newest": [check.as_dict() for check in self.missing_in_newest],
            "newer_releases": self.newer_releases,
            "annotations": [{"level": level, "message": message} for level, message in self.annotations()],
        }

    def summary(self) -> str:
        newest = {(c.name, c.version.replace(self.newest, self.selected)): c for c in self.newest_checks}
        lines = [
            "### ROCm nightly wheel resolution",
            "",
            f"Index: {self.index} ({self.python_tag}, {self.platform})",
            "",
            "| Build | ROCm wheel version |",
            "| --- | --- |",
            f"| Newest on index | `{self.newest}` |",
            f"| **Selected** | `{self.selected}` |",
            f"| Pinned in docker/Dockerfile.ci | `{self.pins['ROCM_WHEEL_VERSION']}` |",
            "",
            "| Package | Selected wheel | In newest build |",
            "| --- | --- | --- |",
        ]
        for check in self.selected_checks:
            in_newest = newest.get((check.name, check.version))
            mark = "yes" if in_newest is None or in_newest.found else "**missing**"
            lines.append(f"| {check.name} | `{check.filename}` | {mark} |")
        notes = self.annotations()
        if notes:
            lines += ["", *(f"- **{level}:** {message}" for level, message in notes)]
        return "\n".join(lines) + "\n"


def resolve(
    index: Index,
    pins: dict[str, str],
    install_script: Path,
    max_builds: int,
) -> Resolution:
    builds = index.rocm_builds()
    if not builds:
        raise LookupError(f"No rocm-sdk-core {index.python_tag}/{index.platform} builds on {index.url}")
    newest = builds[0]
    newest_checks = None
    for rocm_version in builds[:max_builds]:
        checks = check_build(index, read_requirements(install_script, pins, rocm_version), rocm_version)
        if newest_checks is None:
            newest_checks = checks
        if all(check.found for check in checks):
            break
    else:
        missing = ", ".join(f"{c.name}=={c.version}" for c in newest_checks if not c.found)
        raise LookupError(
            f"None of the {min(len(builds), max_builds)} newest ROCm builds has all pinned wheels. "
            f"Missing in the newest build {newest}: {missing}"
        )
    newer_releases = {}
    for project, pin in FRAMEWORK_PINS.items():
        newer = [v for v in index.releases_for_build(project, newest) if release_key(v) > release_key(pins[pin])]
        if newer:
            newer_releases[project] = newer
    return Resolution(
        index=index.url,
        pins=pins,
        python_tag=index.python_tag,
        platform=index.platform,
        newest=newest,
        selected=rocm_version,
        newest_checks=newest_checks,
        selected_checks=checks,
        newer_releases=newer_releases,
    )


def http_fetch(url: str, attempts: int = 3) -> str:
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read().decode("utf-8")
        except OSError as error:
            if attempt == attempts:
                raise RuntimeError(f"Cannot read {url}: {error}") from error
            time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def append_file(env_name: str, text: str) -> None:
    path = os.environ.get(env_name)
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dockerfile", type=Path, default=Path("docker/Dockerfile.ci"))
    parser.add_argument("--install-script", type=Path, default=Path("docker/install_rocm_wheels.sh"))
    parser.add_argument("--index", help="wheel index URL (default: ROCM_WHEEL_INDEX in the Dockerfile)")
    parser.add_argument("--python-tag", default="cp312", help="CPython tag of the image venv")
    parser.add_argument("--platform", default="linux_x86_64")
    parser.add_argument("--max-builds", type=int, default=60, help="number of newest builds to check")
    parser.add_argument("--provenance", type=Path, help="write the provenance JSON to this file")
    args = parser.parse_args(argv)

    pins = parse_dockerfile_args(args.dockerfile.read_text())
    index = Index(args.index or pins["ROCM_WHEEL_INDEX"], http_fetch, args.python_tag, args.platform)
    try:
        resolution = resolve(index, pins, args.install_script, args.max_builds)
    except LookupError as error:
        print(f"::error::{error}")
        return 1

    for level, message in resolution.annotations():
        print(f"::{level}::{message}")
    summary = resolution.summary()
    print(summary)
    append_file("GITHUB_STEP_SUMMARY", summary)
    append_file(
        "GITHUB_OUTPUT",
        f"rocm_wheel_version={resolution.selected}\n"
        f"newest_rocm_wheel_version={resolution.newest}\n"
        f"pins_available_in_newest={str(not resolution.missing_in_newest).lower()}\n",
    )
    if args.provenance:
        args.provenance.parent.mkdir(parents=True, exist_ok=True)
        args.provenance.write_text(json.dumps(resolution.provenance(), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
