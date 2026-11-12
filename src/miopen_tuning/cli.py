# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Command-line interface for MIOpen tuning tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .databases import DatabaseError, DatabaseIdentity, diff_pairs, merge_pairs, validate_pair
from .identity import collect_identity
from .planner import build_plan, tuning_commands
from .workloads import load_workloads


def _write_json(value: Any, output: Path | None) -> None:
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    else:
        print(text, end="")


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--identity", type=Path, help="Identity JSON from `python -m miopen_tuning identity`")
    parser.add_argument("--db-prefix", default="")
    parser.add_argument("--miopen-version", default="")
    parser.add_argument("--miopen-build", default="")


def _db_identity(args: argparse.Namespace) -> DatabaseIdentity:
    values = {}
    if args.identity:
        values = json.loads(args.identity.read_text())
    return DatabaseIdentity(
        db_prefix=args.db_prefix or values.get("db_prefix", ""),
        miopen_version=args.miopen_version or values.get("miopen_version", ""),
        miopen_build=args.miopen_build or values.get("miopen_build", ""),
    )


def _identity(args: argparse.Namespace) -> int:
    identity = collect_identity(
        driver=args.miopendriver,
        prefix_map_path=args.prefix_map,
        image=args.image,
        image_digest=args.image_digest,
    ).to_dict()
    _write_json(identity, args.output)
    return 0


def _workloads_check(args: argparse.Namespace) -> int:
    workloads = load_workloads(args.paths, Path.cwd())
    report = workloads.to_dict()
    _write_json(report, args.output)
    return 1 if workloads.rejected or (args.strict_format and workloads.format_warnings) else 0


def _plan(args: argparse.Namespace) -> int:
    identity = _db_identity(args)
    workloads = load_workloads(args.workloads, Path.cwd())
    plan = build_plan(workloads, args.db_dir, identity)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(plan, args.output_dir / "plan.json")
    (args.output_dir / "commands.txt").write_text("".join(f"{command}\n" for command in tuning_commands(plan)))
    print(json.dumps(plan["counts"], sort_keys=True))
    return 1 if plan["counts"]["reject"] else 0


def _db_check(args: argparse.Namespace) -> int:
    identity = _db_identity(args)
    report = validate_pair(args.db_dir, identity, require_files=not args.allow_missing)
    if args.workloads:
        plan = build_plan(load_workloads(args.workloads, Path.cwd()), args.db_dir, identity)
        report["coverage"] = plan["counts"]
        report["missing_problems"] = [
            {"command_id": entry["command_id"], "command": entry["command"], "reasons": entry["reasons"]}
            for entry in plan["entries"]
            if entry["action"] == "tune"
        ]
        if args.require_complete and report["missing_problems"]:
            report["valid"] = False
    if args.require_complete and (report["find_without_perf"] or report["perf_without_find"]):
        report["valid"] = False
    _write_json(report, args.output)
    return 0 if report["valid"] else 1


def _db_merge(args: argparse.Namespace) -> int:
    identity = _db_identity(args)
    try:
        report = merge_pairs(args.base_dir, args.tuned_dir, args.output_dir, identity)
        validation = validate_pair(args.output_dir, identity)
    except DatabaseError as exc:
        _write_json({"valid": False, "error": str(exc)}, args.report)
        return 1
    result = {"diff": report, "validation": validation, "valid": validation["valid"]}
    _write_json(result, args.report)
    return 0 if validation["valid"] else 1


def _db_diff(args: argparse.Namespace) -> int:
    _write_json(diff_pairs(args.old_dir, args.new_dir, _db_identity(args)), args.output)
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

    workloads = commands.add_parser("workloads", help="Validate workload command files")
    workload_commands = workloads.add_subparsers(dest="workloads_command", required=True)
    check = workload_commands.add_parser("check", help="Parse and validate workload files")
    check.add_argument("paths", nargs="*", type=Path, default=[Path("data/miopen/workloads")])
    check.add_argument("--output", type=Path)
    check.add_argument("--strict-format", action="store_true", help="Fail on unsorted or duplicate file lines")
    check.set_defaults(handler=_workloads_check)

    plan = commands.add_parser("plan", help="Select workload problems that need tuning")
    plan.add_argument("--workloads", nargs="+", type=Path, default=[Path("data/miopen/workloads")])
    plan.add_argument("--db-dir", type=Path, default=Path("data/miopen/userdb"))
    plan.add_argument("--output-dir", type=Path, required=True)
    _add_identity_arguments(plan)
    plan.set_defaults(handler=_plan)

    db = commands.add_parser("db", help="Validate, compare, and merge MIOpen user DBs")
    db_commands = db.add_subparsers(dest="db_command", required=True)
    db_check = db_commands.add_parser("check", help="Validate the exact DB pair for an identity")
    db_check.add_argument("--db-dir", type=Path, default=Path("data/miopen/userdb"))
    db_check.add_argument("--workloads", nargs="*", type=Path)
    db_check.add_argument("--output", type=Path)
    db_check.add_argument("--allow-missing", action="store_true")
    db_check.add_argument("--require-complete", action="store_true")
    _add_identity_arguments(db_check)
    db_check.set_defaults(handler=_db_check)

    merge = db_commands.add_parser("merge", help="Merge tuned DBs into a base DB by record key")
    merge.add_argument("--base-dir", type=Path)
    merge.add_argument("--tuned-dir", type=Path, action="append", required=True)
    merge.add_argument("--output-dir", type=Path, required=True)
    merge.add_argument("--report", type=Path)
    _add_identity_arguments(merge)
    merge.set_defaults(handler=_db_merge)

    diff = db_commands.add_parser("diff", help="Compare two exact DB pairs")
    diff.add_argument("--old-dir", type=Path, required=True)
    diff.add_argument("--new-dir", type=Path, required=True)
    diff.add_argument("--output", type=Path)
    _add_identity_arguments(diff)
    diff.set_defaults(handler=_db_diff)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except DatabaseError as exc:
        parser = build_parser()
        parser.exit(1, f"{parser.prog}: error: {exc}\n")
