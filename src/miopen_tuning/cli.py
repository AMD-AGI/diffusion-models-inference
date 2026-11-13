# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Command-line interface for MIOpen tuning tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .identity import collect_identity


def _identity(args: argparse.Namespace) -> int:
    identity = collect_identity(
        driver=args.miopendriver,
        prefix_map_path=args.prefix_map,
        image=args.image,
        image_digest=args.image_digest,
    ).to_dict()
    text = json.dumps(identity, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m miopen_tuning")
    commands = parser.add_subparsers(dest="command", required=True)

    identity = commands.add_parser("identity", help="Record MIOpen, ROCm, device, and DB identity")
    identity.add_argument("--output", type=Path, help="Write JSON to this file instead of stdout")
    identity.add_argument("--miopendriver", help="Path to MIOpenDriver")
    identity.add_argument("--prefix-map", type=Path, default=Path("data/miopen/prefixes.txt"))
    identity.add_argument("--image", default="")
    identity.add_argument("--image-digest", default="")
    identity.set_defaults(handler=_identity)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)
