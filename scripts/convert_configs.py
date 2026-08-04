#!/usr/bin/env python3
"""Convert legacy xDiT benchmark configs to inference-testing format.

Handles two formats transparently:
  - Legacy (flat YAML list with name/tags/model/runner/args) → converted on the fly
  - Native inference-testing (has server/benchmarks keys) → passed through unchanged

Usage:
    # Convert a single file, print to stdout:
    python scripts/convert_configs.py benchmark_configs/xdit/flux.yaml

    # Convert multiple files into an output directory:
    python scripts/convert_configs.py benchmark_configs/xdit/*.yaml -o /tmp/converted/

    # Filter by tag before converting (AND logic):
    python scripts/convert_configs.py benchmark_configs/xdit/*.yaml --tag gfx942 --tag release -o /tmp/converted/

    # Filter by name:
    python scripts/convert_configs.py benchmark_configs/xdit/*.yaml --name flux.usp --name flux.usp_2k -o /tmp/converted/

    # Inject Docker image for the server block:
    python scripts/convert_configs.py benchmark_configs/xdit/*.yaml --image amdsiloai/pytorch-xdit:abc123

Environment variables referenced in the output (${HF_TOKEN}, etc.) are
resolved at runtime by inference-testing, not by this script.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


def is_native_format(data: Any) -> bool:
    """Return True if the parsed YAML is already in inference-testing format."""
    if isinstance(data, dict):
        return "server" in data or "benchmarks" in data
    return False


def is_legacy_format(data: Any) -> bool:
    """Return True if the parsed YAML is a legacy flat-list config."""
    if not isinstance(data, list):
        return False
    if len(data) == 0:
        return True
    first = data[0]
    return isinstance(first, dict) and "runner" in first


# ---------------------------------------------------------------------------
# Filtering (mirrors .ci/run.py logic)
# ---------------------------------------------------------------------------


def filter_by_tags(entries: list[dict], tags: list[str]) -> list[dict]:
    """Keep entries that have ALL specified tags (AND logic)."""
    return [e for e in entries if all(t in e.get("tags", []) for t in tags)]


def filter_by_names(entries: list[dict], names: list[str]) -> list[dict]:
    """Keep entries whose name is in the provided list."""
    return [e for e in entries if e.get("name") in names]


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

# Default Docker server configuration matching the current run-benchmarks action
DEFAULT_SERVER_ARGS: dict[str, Any] = {
    "command": "sleep infinity",
    "devices": ["/dev/dri:/dev/dri", "/dev/kfd:/dev/kfd"],
    "environment": {
        "HF_TOKEN": "${HF_TOKEN}",
        "CUDA_VISIBLE_DEVICES": "${CUDA_VISIBLE_DEVICES:-0,1,2,3,4,5,6,7}",
        "OMP_NUM_THREADS": "16",
    },
    "security_opt": ["seccomp=unconfined"],
    "shm_size": "128G",
}


def convert_entry(entry: dict, image: str) -> dict:
    """Convert a single legacy config entry to inference-testing format.

    Args:
        entry: A legacy config dict with name/tags/model/runner/args.
        image: Docker image to use in the server block.

    Returns:
        An inference-testing config dict with server/benchmarks sections.
    """
    args = dict(entry.get("args", {}))
    args["model"] = entry["model"]

    server_args = dict(DEFAULT_SERVER_ARGS)
    server_args["image"] = image

    config: dict[str, Any] = {
        "server": {
            "type": "docker",
            "stop_between_runs": True,
            "args": server_args,
        },
        "benchmarks": [
            {
                "type": "xdit",
                "args": args,
            }
        ],
    }

    return config


def convert_file(
    path: Path,
    image: str,
    tags: list[str] | None = None,
    names: list[str] | None = None,
) -> list[tuple[str, dict]]:
    """Load a YAML config file and convert/filter as needed.

    Returns:
        List of (name, config_dict) tuples. For native format files, name is
        the stem of the input file. For legacy files, name is the entry's name
        field.
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return []

    # Native format: pass through unchanged
    if is_native_format(data):
        return [(path.stem, data)]

    if not is_legacy_format(data):
        print(f"WARNING: Skipping {path} — unrecognized format", file=sys.stderr)
        return []

    # Filter
    entries = data
    if names:
        entries = filter_by_names(entries, names)
    elif tags:
        entries = filter_by_tags(entries, tags)

    # Convert each entry
    results = []
    for entry in entries:
        name = entry.get("name", path.stem)
        converted = convert_entry(entry, image)
        results.append((name, converted))

    return results


# ---------------------------------------------------------------------------
# YAML output helpers
# ---------------------------------------------------------------------------

# Use a custom representer so long strings (prompts) are block-style
class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str) -> Any:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_representer)


def dump_yaml(config: dict) -> str:
    return yaml.dump(
        config, Dumper=_Dumper, default_flow_style=False, sort_keys=False, width=4096,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_IMAGE = "${BENCHMARK_IMAGE}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert legacy xDiT benchmark configs to inference-testing format.",
    )
    parser.add_argument(
        "configs",
        nargs="+",
        type=Path,
        help="YAML config files to convert",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory for converted files. If omitted, prints to stdout.",
    )
    parser.add_argument(
        "--image",
        default=os.environ.get("BENCHMARK_IMAGE", DEFAULT_IMAGE),
        help="Docker image for the server block (default: $BENCHMARK_IMAGE or placeholder)",
    )
    parser.add_argument(
        "--tag",
        dest="tags",
        action="append",
        default=[],
        help="Filter by tag (AND logic, repeatable). Mutually exclusive with --name.",
    )
    parser.add_argument(
        "--name",
        dest="names",
        action="append",
        default=[],
        help="Filter by experiment name (repeatable). Mutually exclusive with --tag.",
    )

    args = parser.parse_args()

    if args.tags and args.names:
        parser.error("--tag and --name are mutually exclusive")

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    converted_count = 0
    for config_path in args.configs:
        if not config_path.is_file():
            print(f"WARNING: Skipping {config_path} — not a file", file=sys.stderr)
            continue

        results = convert_file(
            config_path,
            image=args.image,
            tags=args.tags or None,
            names=args.names or None,
        )

        for name, config in results:
            output = dump_yaml(config)
            if args.output_dir:
                # Sanitize name for filesystem
                safe_name = name.replace("/", "_").replace(" ", "_")
                out_path = args.output_dir / f"{safe_name}.yaml"
                out_path.write_text(output)
                converted_count += 1
            else:
                if converted_count > 0:
                    print("---")
                print(f"# {name}")
                print(output)
                converted_count += 1

    if args.output_dir:
        print(f"Converted {converted_count} config(s) to {args.output_dir}")


if __name__ == "__main__":
    main()
