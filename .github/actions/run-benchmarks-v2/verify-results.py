#!/usr/bin/env python3
"""Verify that every converted xDiT benchmark produced timing results."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath

import yaml


def expected_result_paths(config_dir: Path, result_dir: Path) -> list[Path]:
    expected = []
    for config_path in sorted(config_dir.glob("*.yaml")):
        with config_path.open() as config_file:
            config = yaml.safe_load(config_file)

        for benchmark in config.get("benchmarks", []):
            if benchmark.get("type") != "xdit":
                continue
            output_directory = PurePosixPath(benchmark.get("args", {}).get("output_directory", ""))
            if not output_directory.is_absolute() or output_directory.parts[:2] != ("/", "outputs"):
                raise ValueError(f"Invalid xDiT output_directory in {config_path}: {output_directory}")
            expected.append(result_dir.joinpath(*output_directory.parts[2:], "timings.json"))
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_dir", type=Path)
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()

    expected = expected_result_paths(args.config_dir, args.result_dir)
    if not expected:
        raise RuntimeError("No xDiT benchmark results were expected")

    missing = [path for path in expected if not path.is_file()]
    if missing:
        formatted = "\n".join(f"- {path}" for path in missing)
        raise RuntimeError(f"Missing timing results for {len(missing)} benchmark(s):\n{formatted}")

    print(f"Verified {len(expected)} benchmark result(s)")


if __name__ == "__main__":
    main()