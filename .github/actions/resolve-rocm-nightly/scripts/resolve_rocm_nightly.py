#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Resolve the TheRock nightly ROCm wheel build for the nightly image.

The newest rocm-sdk-core build on the index is the target. The script gets
the requirement list for that build from "install_rocm_wheels.sh
--print-requirements" and checks the wheel file names on the index. If a
pinned wheel is missing, the script writes an error and fails. Then update the
pins in the Dockerfile to follow the newest build. The script also writes
notices for newer torch, torchvision, and torchaudio releases and for a new
ROCm version.

The index does not serve wheel metadata (PEP 658), so a pip dry run would
download all the wheels. File name checks do not download wheels. The index
redirects project pages to different back ends; urllib follows the redirects.

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
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

# Dockerfile ARGs that the resolver itself reads. The install script reads
# more ARGs; it gets all ARG defaults and stops if one that it needs is empty.
REQUIRED_ARGS = ("ROCM_WHEEL_INDEX", "ROCM_WHEEL_VERSION", "ROCM_WHEEL_PYTHON_TAG")
# Pinned framework releases (ARG <PROJECT>_VERSION). A newer release for the
# build is a notice.
FRAMEWORKS = ("torch", "torchvision", "torchaudio")
ROCM_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)a(\d{8})$")
REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==(\S+)$")
HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)

Fetch = Callable[[str], str]


def framework_pin(project: str) -> str:
    return f"{project.upper()}_VERSION"


@dataclasses.dataclass(frozen=True)
class Wheel:
    name: str
    version: str
    filename: str


@dataclasses.dataclass(frozen=True)
class Requirement:
    """A pinned wheel: an exact version, or any version with a given suffix."""

    name: str
    version: str | None = None
    suffix: str | None = None

    def matches(self, version: str) -> bool:
        if self.version is not None:
            return version == self.version
        return version.endswith(self.suffix)

    def __str__(self) -> str:
        if self.version is not None:
            return f"{self.name}=={self.version}"
        return f"{self.name}==*{self.suffix}"


@dataclasses.dataclass(frozen=True)
class WheelCheck:
    """The wheels on the index that agree with one requirement."""

    requirement: Requirement
    filenames: tuple[str, ...]

    @property
    def found(self) -> bool:
        return bool(self.filenames)

    def as_dict(self) -> dict:
        return {"requirement": str(self.requirement), "wheels": list(self.filenames)}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def rocm_version_key(version: str) -> tuple[int, ...]:
    match = ROCM_VERSION_RE.match(version)
    if not match:
        raise ValueError(f"not a ROCm nightly wheel version: {version}")
    return tuple(int(part) for part in match.groups())


def rocm_release(version: str) -> str:
    """The ROCm release of a nightly wheel version, for example 10.3.0."""
    return version.split("a", 1)[0]


def release_key(version: str) -> tuple[int, ...]:
    """Sort key for the release part of a version such as 2.14.0, 2.11.0.3, or 2.16.0a0."""
    match = re.match(r"\d+(?:\.\d+)*", version)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def parse_dockerfile_args(text: str) -> dict[str, str]:
    """Return the default of each ARG. The first ARG with a name sets its default."""
    values = {}
    for line in text.splitlines():
        match = re.match(r'^ARG\s+([A-Z0-9_]+)=(?:"([^"]*)"|(\S+))\s*$', line)
        if match and match.group(1) not in values:
            values[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
    needed = (*REQUIRED_ARGS, *(framework_pin(project) for project in FRAMEWORKS))
    missing = [name for name in needed if not values.get(name)]
    if missing:
        raise ValueError(f"Dockerfile has no default for: {', '.join(missing)}")
    return values


def platform_matches(tag: str, arch: str) -> bool:
    """True for "any", linux_<arch>, and the manylinux tags of arch."""
    return tag == "any" or re.fullmatch(rf"(?:many)?linux(?:_\d+_\d+|\d+)?_{re.escape(arch)}", tag) is not None


def parse_wheel(filename: str, python_tag: str, arch: str) -> Wheel | None:
    """Return the wheel if it installs on python_tag and arch."""
    parts = filename[: -len(".whl")].split("-")
    if len(parts) not in (5, 6):
        return None
    name, version = parts[0], parts[1]
    pythons, abis, platforms = (set(tag.split(".")) for tag in parts[-3:])
    if not pythons & {"py3", python_tag}:
        return None
    if not abis & {"none", "abi3", python_tag}:
        return None
    if not any(platform_matches(tag, arch) for tag in platforms):
        return None
    return Wheel(normalize(name), version, filename)


class Index:
    """Wheel lists of the projects on a PEP 503 simple index."""

    def __init__(self, url: str, fetch: Fetch, python_tag: str, arch: str):
        self.url = url.rstrip("/")
        self.fetch = fetch
        self.python_tag = python_tag
        self.arch = arch
        self._projects: dict[str, list[Wheel]] = {}

    def wheels(self, project: str) -> list[Wheel]:
        project = normalize(project)
        if project not in self._projects:
            page = self.fetch(f"{self.url}/{project}/")
            wheels = []
            for href in HREF_RE.findall(page):
                path = urllib.parse.urlsplit(href).path
                if not path.endswith(".whl"):
                    continue
                filename = urllib.parse.unquote(path.rsplit("/", 1)[-1])
                wheel = parse_wheel(filename, self.python_tag, self.arch)
                if wheel and wheel.name == project:
                    wheels.append(wheel)
            self._projects[project] = wheels
        return self._projects[project]

    def find(self, requirement: Requirement) -> tuple[str, ...]:
        return tuple(sorted(w.filename for w in self.wheels(requirement.name) if requirement.matches(w.version)))

    def rocm_builds(self) -> list[str]:
        """rocm-sdk-core versions, newest first."""
        versions = {w.version for w in self.wheels("rocm-sdk-core") if ROCM_VERSION_RE.match(w.version)}
        return sorted(versions, key=rocm_version_key, reverse=True)

    def releases_for_build(self, project: str, rocm_version: str) -> list[str]:
        """Public versions of project built for rocm_version, newest first."""
        suffix = f"+rocm{rocm_version}"
        versions = {w.version[: -len(suffix)] for w in self.wheels(project) if w.version.endswith(suffix)}
        return sorted(versions, key=release_key, reverse=True)


def read_requirements(install_script: Path, pins: dict[str, str], rocm_version: str) -> list[Requirement]:
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
        requirements.append(Requirement(normalize(match.group(1)), version=match.group(2)))
    if not requirements:
        raise ValueError(f"{install_script} printed no requirements")
    return requirements


@dataclasses.dataclass
class Resolution:
    index: Index
    dockerfile: Path
    pins: dict[str, str]
    build: str
    checks: list[WheelCheck]
    newer_releases: dict[str, list[str]]

    @property
    def missing(self) -> list[WheelCheck]:
        return [check for check in self.checks if not check.found]

    def annotations(self) -> list[tuple[str, str]]:
        notes = []
        if self.missing:
            missing = ", ".join(str(check.requirement) for check in self.missing)
            notes.append(
                (
                    "error",
                    f"The newest ROCm build {self.build} does not have these pinned wheels: {missing}. "
                    f"Update the pins in {self.dockerfile} to follow the newest build.",
                )
            )
        pinned = self.pins["ROCM_WHEEL_VERSION"]
        if rocm_release(self.build) != rocm_release(pinned):
            notes.append(
                (
                    "notice",
                    f"The newest ROCm build is ROCm {rocm_release(self.build)}; {self.dockerfile} pins "
                    f"ROCM_WHEEL_VERSION={pinned} (ROCm {rocm_release(pinned)}).",
                )
            )
        for project, versions in self.newer_releases.items():
            pin = framework_pin(project)
            notes.append(
                (
                    "notice",
                    f"Newer {project} releases exist for ROCm build {self.build}: {', '.join(versions)} "
                    f"(pinned {pin}={self.pins[pin]}).",
                )
            )
        return notes

    def provenance(self) -> dict:
        return {
            "rocm_wheel_index": self.index.url,
            "python_tag": self.index.python_tag,
            "arch": self.index.arch,
            "dockerfile_pins": {key: value for key, value in self.pins.items() if key.startswith(("ROCM_", "TORCH"))},
            "rocm_wheel_version": self.build,
            "pins_available": not self.missing,
            "wheels": [check.as_dict() for check in self.checks],
            "newer_releases": self.newer_releases,
            "annotations": [{"level": level, "message": message} for level, message in self.annotations()],
        }

    def summary(self) -> str:
        lines = [
            "### ROCm nightly wheel resolution",
            "",
            f"Index: {self.index.url} ({self.index.python_tag}, {self.index.arch})",
            "",
            f"Newest ROCm build: `{self.build}` (pinned in {self.dockerfile}: `{self.pins['ROCM_WHEEL_VERSION']}`)",
            "",
            "| Requirement | Wheels |",
            "| --- | --- |",
        ]
        for check in self.checks:
            wheels = ", ".join(f"`{name}`" for name in check.filenames) or "**missing**"
            lines.append(f"| `{check.requirement}` | {wheels} |")
        notes = self.annotations()
        if notes:
            lines += ["", *(f"- **{level}:** {message}" for level, message in notes)]
        return "\n".join(lines) + "\n"


def resolve(index: Index, pins: dict[str, str], install_script: Path, dockerfile: Path) -> Resolution:
    builds = index.rocm_builds()
    if not builds:
        raise LookupError(f"No rocm-sdk-core {index.python_tag}/{index.arch} builds on {index.url}")
    build = builds[0]
    requirements = read_requirements(install_script, pins, build)
    # torch pins triton to an exact version that is known only from the torch
    # metadata. The index can have more than one triton wheel for a build.
    requirements.append(Requirement("triton", suffix=f"rocm{build}"))
    checks = [WheelCheck(requirement, index.find(requirement)) for requirement in requirements]
    newer_releases = {}
    for project in FRAMEWORKS:
        pinned = release_key(pins[framework_pin(project)])
        newer = [v for v in index.releases_for_build(project, build) if release_key(v) > pinned]
        if newer:
            newer_releases[project] = newer
    return Resolution(index, dockerfile, pins, build, checks, newer_releases)


def http_fetch(url: str, attempts: int = 3) -> str:
    """Return the page at url. Return "" if the index has no such project (404)."""
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return ""
            if error.code < 500 or attempt == attempts:
                raise RuntimeError(f"Cannot read {url}: {error}") from error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == attempts:
                raise RuntimeError(f"Cannot read {url}: {error}") from error
        time.sleep(5 * attempt)
    raise AssertionError("unreachable")


def annotate(level: str, message: str) -> None:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}")


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
    parser.add_argument("--arch", default="x86_64", help="CPU architecture of the image")
    parser.add_argument("--provenance", type=Path, help="write the provenance JSON to this file")
    args = parser.parse_args(argv)

    try:
        pins = parse_dockerfile_args(args.dockerfile.read_text())
        index = Index(args.index or pins["ROCM_WHEEL_INDEX"], http_fetch, pins["ROCM_WHEEL_PYTHON_TAG"], args.arch)
        resolution = resolve(index, pins, args.install_script, args.dockerfile)
    except subprocess.CalledProcessError as error:
        annotate("error", f"{args.install_script} --print-requirements failed: {error.stderr.strip()}")
        return 1
    except (LookupError, OSError, RuntimeError, ValueError) as error:
        annotate("error", str(error))
        return 1

    for level, message in resolution.annotations():
        annotate(level, message)
    summary = resolution.summary()
    print(summary)
    append_file("GITHUB_STEP_SUMMARY", summary)
    if args.provenance:
        args.provenance.parent.mkdir(parents=True, exist_ok=True)
        args.provenance.write_text(json.dumps(resolution.provenance(), indent=2) + "\n")
    if resolution.missing:
        return 1
    append_file("GITHUB_OUTPUT", f"rocm_wheel_version={resolution.build}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
