# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Plan which MIOpen workload problems need tuning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .databases import FIND_SUFFIX, PERF_SUFFIX, DatabaseIdentity, load_pair, other_versions
from .workloads import WorkloadSet


def build_plan(workloads: WorkloadSet, db_directory: Path, identity: DatabaseIdentity) -> dict[str, Any]:
    pair = load_pair(db_directory, identity)
    find_records = pair[FIND_SUFFIX].records if pair[FIND_SUFFIX] else {}
    perf_records = pair[PERF_SUFFIX].records if pair[PERF_SUFFIX] else {}
    entries: list[dict[str, Any]] = []
    for problem in workloads.problems.values():
        missing = []
        if pair[FIND_SUFFIX] is None:
            missing.append("find DB file")
        elif problem.find_db_key not in find_records:
            missing.append("find DB record")
        if pair[PERF_SUFFIX] is None:
            missing.append("performance DB file")
        elif problem.perf_db_key not in perf_records:
            missing.append("performance DB record")
        entry = problem.to_dict()
        entry.update(action="tune" if missing else "skip", reasons=missing or ["find and performance records present"])
        entries.append(entry)
    for rejected in workloads.rejected:
        entry = rejected.to_dict()
        entry.update(action="reject", reasons=[rejected.error])
        entries.append(entry)
    counts = {action: sum(entry["action"] == action for entry in entries) for action in ("tune", "skip", "reject")}
    return {
        "db_identity": identity.__dict__,
        "db_stem": identity.stem,
        "db_directory": str(db_directory),
        "db_files": {suffix: str(identity.path(db_directory, suffix)) for suffix in (FIND_SUFFIX, PERF_SUFFIX)},
        "other_db_versions": other_versions(db_directory, identity),
        "workloads": {
            key: value
            for key, value in workloads.to_dict().items()
            if key not in {"problems", "rejected"}
        },
        "counts": counts,
        "entries": entries,
    }


def tuning_commands(plan: dict[str, Any]) -> list[str]:
    return [entry["command"] for entry in plan["entries"] if entry["action"] == "tune"]
