#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import html.parser
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass


INDEX_ROOT = "https://nightly.repo.amd.com/rocm/whl-next"
PLATFORM_TAG = "linux_x86_64"
PYTHON_TAG = "cp312-cp312"


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.links.append(value)


@dataclass(frozen=True)
class Wheel:
    version: str
    python_tag: str
    abi_tag: str
    platform_tag: str

    @property
    def release(self) -> tuple[int, ...]:
        match = re.match(r"(\d+(?:\.\d+)+)", self.version)
        if not match:
            raise ValueError(f"Version has no numeric release: {self.version}")
        return tuple(int(part) for part in match.group(1).split("."))


def package_wheels(distribution: str) -> list[Wheel]:
    url = f"{INDEX_ROOT}/{distribution}/"
    with urllib.request.urlopen(url) as response:
        body = response.read().decode()
    parser = LinkParser()
    parser.feed(body)

    pattern = re.compile(
        rf"^{re.escape(distribution)}-(?P<version>.+)-"
        rf"(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$"
    )
    wheels: list[Wheel] = []
    for link in parser.links:
        filename = urllib.parse.unquote(
            urllib.parse.urlparse(link).path.rsplit("/", maxsplit=1)[-1]
        )
        match = pattern.match(filename)
        if match:
            wheels.append(
                Wheel(
                    version=match.group("version"),
                    python_tag=match.group("python"),
                    abi_tag=match.group("abi"),
                    platform_tag=match.group("platform"),
                )
            )
    if not wheels:
        raise RuntimeError(f"No wheels found at {url}")
    return wheels


def latest_torch() -> tuple[Wheel, str, str]:
    candidates: list[tuple[str, Wheel, str]] = []
    for wheel in package_wheels("torch"):
        if (
            f"{wheel.python_tag}-{wheel.abi_tag}" != PYTHON_TAG
            or wheel.platform_tag != PLATFORM_TAG
        ):
            continue
        match = re.search(r"\+rocm(?P<rocm>\d+\.\d+\.\d+a(?P<date>\d{8}))$", wheel.version)
        if match:
            candidates.append((match.group("date"), wheel, match.group("rocm")))
    if not candidates:
        raise RuntimeError("No Python 3.12 Linux ROCm torch wheels found")
    date, wheel, rocm_version = max(
        candidates,
        key=lambda candidate: (
            candidate[1].release,
            candidate[0],
            candidate[1].version,
        ),
    )
    return wheel, rocm_version, date


def matching_framework_version(distribution: str, rocm_version: str) -> str:
    suffix = f"+rocm{rocm_version}"
    candidates = [
        wheel
        for wheel in package_wheels(distribution)
        if wheel.platform_tag == PLATFORM_TAG
        and wheel.version.endswith(suffix)
        and (
            wheel.python_tag == "py3"
            or f"{wheel.python_tag}-{wheel.abi_tag}" == PYTHON_TAG
        )
    ]
    if not candidates:
        raise RuntimeError(
            f"No {distribution} wheel matches ROCm {rocm_version}, "
            f"Python 3.12, and {PLATFORM_TAG}"
        )
    return max(candidates, key=lambda wheel: (wheel.release, wheel.version)).version


def main() -> int:
    try:
        torch, rocm_version, date = latest_torch()
        torchvision_version = matching_framework_version("torchvision", rocm_version)
        torchaudio_version = matching_framework_version("torchaudio", rocm_version)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Could not resolve a coherent TheRock nightly: {error}", file=sys.stderr)
        return 1

    # Keep the historical YYYYMMDD-run-number release_id shape for image tags
    # and reusable-workflow consumers. Wheel versions are the source of truth.
    print(f"release_id={date}-0")
    print(f"rocm_version={rocm_version}")
    print(f"pytorch_version={torch.version}")
    print(f"pytorch_vision_version={torchvision_version}")
    print(f"pytorch_audio_version={torchaudio_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
