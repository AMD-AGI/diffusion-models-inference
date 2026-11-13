# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Run MIOpen tuning from workload files to a validated user DB."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .databases import FIND_SUFFIX, PERF_SUFFIX, DatabaseIdentity, diff_pairs, load_pair, merge_pairs, validate_pair
from .executor import TuningTask, device_ids, execute_tasks
from .identity import Identity, collect_identity
from .planner import build_plan, tuning_commands
from .report import annotations, report_markdown, summary_markdown
from .workloads import load_workloads


@dataclass(frozen=True)
class RunOptions:
    output_dir: Path
    workloads: list[Path]
    db_dir: Path
    prefix_map: Path
    miopendriver: str | None = None
    identity_path: Path | None = None
    devices: str | None = None
    force_retuning: bool = False
    install_db: bool = False
    run_contracts: bool = True
    find_mode: str = "1"
    find_enforce: str = "3"
    debug_conv_direct: str | None = None
    log_profile: str = "standard"
    timeout: int = 7200
    image: str = ""
    image_digest: str = ""


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _identity(options: RunOptions) -> dict[str, Any]:
    if options.identity_path:
        return json.loads(options.identity_path.read_text())
    return collect_identity(
        driver=options.miopendriver,
        prefix_map_path=options.prefix_map,
        image=options.image,
        image_digest=options.image_digest,
    ).to_dict()


def _run_contracts(options: RunOptions) -> dict[str, Any]:
    if not options.run_contracts:
        return {"status": "skipped", "exit_code": None, "log": None}
    contracts_dir = options.output_dir / "contracts"
    # The contract tests refuse a non-empty artifact directory. This directory belongs to
    # the run, so results from an earlier run with the same output directory are replaced.
    shutil.rmtree(contracts_dir, ignore_errors=True)
    contracts_dir.mkdir(parents=True)
    environment = os.environ.copy()
    environment.update(
        {
            "MIOPEN_CONTRACT_TESTS": "1",
            "MIOPEN_CONTRACT_ARTIFACTS": str(contracts_dir / "artifacts"),
            "MIOPEN_CONTRACT_IMAGE": options.image,
            "MIOPEN_CONTRACT_IMAGE_DIGEST": options.image_digest,
            "MIOPEN_CONTRACT_LOG_PROFILE": options.log_profile,
        }
    )
    if options.miopendriver:
        environment["MIOPEN_DRIVER"] = options.miopendriver
    devices = options.devices or environment.get("HIP_VISIBLE_DEVICES", "")
    if devices:
        environment["HIP_VISIBLE_DEVICES"] = device_ids(devices)[0]
    log_path = contracts_dir / "test-output.log"
    with log_path.open("w") as log:
        process = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", "miopen_tuning.tests.miopen.test_miopen_contracts"],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            check=False,
        )
    return {"status": "passed" if process.returncode == 0 else "failed", "exit_code": process.returncode, "log": str(log_path.relative_to(options.output_dir))}


def _copy_pair(source: Path, destination: Path, identity: DatabaseIdentity) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for suffix in (FIND_SUFFIX, PERF_SUFFIX):
        path = identity.path(source, suffix)
        if path.is_file():
            shutil.copy2(path, identity.path(destination, suffix))


def _check_records_after_tuning(
    results: list[Any], merged: Path, identity: DatabaseIdentity, run_directory: Path
) -> dict[str, list[dict[str, Any]]]:
    """Classify records that successful tuning commands did not add to the merged user DB.

    A missing performance record is expected when MIOpen loaded that exact key from an
    existing (system) performance DB and did not search: with MIOPEN_FIND_ENFORCE=3 it
    only tunes solvers without a record. Any other missing performance record blocks.

    A missing find record is reported as a warning: MIOpen completed the find but did not
    write the record, which is an upstream MIOpen issue rather than a tuning failure.
    """
    pair = load_pair(merged, identity)
    find_records = pair[FIND_SUFFIX].records if pair[FIND_SUFFIX] else {}
    perf_records = pair[PERF_SUFFIX].records if pair[PERF_SUFFIX] else {}
    checks: dict[str, list[dict[str, Any]]] = {"missing": [], "missing_find": [], "perf_from_existing_db": []}
    for result in results:
        if not result.succeeded:
            continue
        summary = result.process.summary
        details = {
            "command_id": result.task.command_id,
            "command": result.task.command,
            "device_id": result.device_id,
            "log_dir": str(result.process.log_dir.relative_to(run_directory)),
        }
        find_key = result.task.problem["find_db_key"]
        perf_key = result.task.problem["perf_db_key"]
        if find_key not in find_records:
            checks["missing_find"].append(
                details
                | {
                    "find_db_key": find_key,
                    "selected_solver": summary.get("selected_solver"),
                    "insufficient_workspace": summary.get("insufficient_workspace", []),
                }
            )
        if perf_key not in perf_records:
            loaded = perf_key in summary.get("loaded_perf_db_keys", [])
            searched = perf_key in summary.get("searched_perf_db_keys", [])
            if loaded and not searched:
                checks["perf_from_existing_db"].append(details | {"perf_db_key": perf_key})
            else:
                checks["missing"].append(details | {"perf_db_key": perf_key, "missing": ["performance DB record"]})
    return checks


def run_tuning(options: RunOptions) -> tuple[int, dict[str, Any]]:
    options.output_dir.mkdir(parents=True, exist_ok=True)
    identity_values = _identity(options)
    _write_json(options.output_dir / "identity.json", identity_values)
    identity = DatabaseIdentity(
        identity_values["db_prefix"], identity_values["miopen_version"], identity_values["miopen_build"]
    )
    contracts = _run_contracts(options)
    errors: list[str] = []
    warnings: list[str] = []
    if contracts["status"] == "failed":
        errors.append(f"MIOpen contract tests failed; see {contracts['log']}")

    workloads = load_workloads(options.workloads, Path.cwd())
    base_db = options.db_dir
    planning_db = options.output_dir / "db" / "empty" if options.force_retuning else base_db
    planning_db.mkdir(parents=True, exist_ok=True)
    plan = build_plan(workloads, planning_db, identity)
    _write_json(options.output_dir / "plan.json", plan)
    (options.output_dir / "commands.txt").write_text("".join(f"{command}\n" for command in tuning_commands(plan)))
    if plan["counts"]["reject"]:
        errors.append(f"{plan['counts']['reject']} workload commands were rejected")

    results = []
    if not errors and plan["counts"]["tune"]:
        driver = identity_values.get("driver_path") or options.miopendriver
        if not driver:
            raise ValueError("MIOpenDriver path is required")
        environment = {
            "MIOPEN_FIND_MODE": options.find_mode,
            "MIOPEN_FIND_ENFORCE": options.find_enforce,
        }
        if options.debug_conv_direct not in (None, ""):
            environment["MIOPEN_DEBUG_CONV_DIRECT"] = options.debug_conv_direct
        tasks = [
            TuningTask(entry["command_id"], entry["command"], entry)
            for entry in plan["entries"]
            if entry["action"] == "tune"
        ]
        results = execute_tasks(
            tasks,
            devices=device_ids(options.devices or os.environ.get("HIP_VISIBLE_DEVICES", "0")),
            driver=driver,
            run_directory=options.output_dir,
            environment=environment,
            log_profile=options.log_profile,
            timeout=options.timeout,
        )
    failed = [result for result in results if not result.succeeded]
    if failed:
        errors.append(f"{len(failed)} MIOpen tuning commands failed")

    db_base = options.output_dir / "db" / "base"
    merged = options.output_dir / "db" / "merged"
    if not options.force_retuning:
        _copy_pair(base_db, db_base, identity)
    tuned_dirs = sorted(path for path in (options.output_dir / "db" / "tuned").glob("device-*") if path.is_dir())
    if results:
        db_diff = merge_pairs(None if options.force_retuning else db_base, tuned_dirs, merged, identity)
    else:
        _copy_pair(db_base, merged, identity)
        db_diff = diff_pairs(db_base, merged, identity)
    validation = validate_pair(merged, identity, require_files=bool(results) or not options.force_retuning)
    if not validation["valid"]:
        errors.append("Merged MIOpen DB is invalid")
    record_checks = _check_records_after_tuning(results, merged, identity, options.output_dir)
    missing = record_checks["missing"]
    if missing:
        errors.append(f"{len(missing)} successful tuning commands are missing performance DB records")
    if record_checks["missing_find"]:
        warnings.append(
            f"MIOpen wrote no find DB record for {len(record_checks['missing_find'])} successfully tuned problems; "
            "see the MIOpen tuning report for commands and logs"
        )
    if options.install_db and not errors:
        _copy_pair(merged, options.db_dir, identity)

    tuning_results = [
        {
            "command_id": result.task.command_id,
            "command": result.task.command,
            "sources": result.task.problem["sources"],
            "device_id": result.device_id,
            "exit_code": result.process.exit_code,
            "timed_out": result.process.timed_out,
            "succeeded": result.succeeded,
            "duration_ms": round(result.process.duration_ms, 3),
            "log_dir": str(result.process.log_dir.relative_to(options.output_dir)),
            "selected_solver": result.process.summary.get("selected_solver"),
        }
        for result in results
    ]
    report = {
        "schema_version": 1,
        "status": "failed" if errors else "passed",
        "errors": errors,
        "warnings": warnings,
        "identity": identity_values,
        "db_identity": identity.__dict__,
        "contracts": contracts,
        "workloads": plan["workloads"],
        "plan": plan,
        "tuning": {
            "succeeded": sum(entry["succeeded"] for entry in tuning_results),
            "failed": sum(not entry["succeeded"] for entry in tuning_results),
            "results": tuning_results,
        },
        "db": {
            "base": str(db_base),
            "merged": str(merged),
            "files": [str(identity.path(merged, suffix)) for suffix in (FIND_SUFFIX, PERF_SUFFIX)],
            "diff": db_diff,
            "validation": validation,
            "missing_after_tuning": missing,
            "missing_find_records": record_checks["missing_find"],
            "perf_records_from_existing_db": record_checks["perf_from_existing_db"],
            "installed": options.install_db and not errors,
        },
    }
    _write_json(options.output_dir / "report.json", report)
    (options.output_dir / "summary.md").write_text(summary_markdown(report))
    (options.output_dir / "report.md").write_text(report_markdown(report))
    (options.output_dir / "annotations.txt").write_text("".join(f"{line}\n" for line in annotations(report)))
    return (1 if errors else 0), report


def identity_from_dict(values: dict[str, Any]) -> Identity:
    return Identity(**values)
