# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Discover, validate, compare, and merge MIOpen user databases."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .problem import MIOpenConvolution


FIND_SUFFIX = "ufdb.txt"
PERF_SUFFIX = "udb.txt"
SUFFIXES = (FIND_SUFFIX, PERF_SUFFIX)
_PERF_KEY_RE = re.compile(r"^[23](?:x[^x]+){20}x(?:NCHW|NHWC|NCDHW|NDHWC)x[A-Z0-9]+x[FBW]$")


class DatabaseError(ValueError):
    """Raised when MIOpen DB files are invalid or cannot be merged."""


@dataclass(frozen=True)
class DatabaseIdentity:
    db_prefix: str
    miopen_version: str
    miopen_build: str

    def __post_init__(self) -> None:
        missing = [name for name, value in self.__dict__.items() if not value]
        if missing:
            raise DatabaseError(f"Missing DB identity fields: {', '.join(missing)}")

    @property
    def stem(self) -> str:
        return f"{self.db_prefix}.HIP.{self.miopen_version.replace('.', '_')}_{self.miopen_build}"

    def path(self, directory: Path, suffix: str) -> Path:
        return directory / f"{self.stem}.{suffix}"


@dataclass(frozen=True)
class RecordFile:
    path: Path
    records: dict[str, str]
    duplicate_count: int


def read_records(path: Path) -> RecordFile:
    records: dict[str, str] = {}
    duplicate_count = 0
    for line_number, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or not value:
            raise DatabaseError(f"{path}:{line_number}: malformed record")
        if key in records:
            if records[key] != value:
                raise DatabaseError(f"{path}:{line_number}: conflicting duplicate key {key}")
            duplicate_count += 1
        records[key] = value
    return RecordFile(path, records, duplicate_count)


def load_pair(directory: Path, identity: DatabaseIdentity, *, required: bool = False) -> dict[str, RecordFile | None]:
    pair: dict[str, RecordFile | None] = {}
    for suffix in SUFFIXES:
        path = identity.path(directory, suffix)
        if path.is_file():
            pair[suffix] = read_records(path)
        elif required:
            raise DatabaseError(f"Missing MIOpen DB file: {path}")
        else:
            pair[suffix] = None
    return pair


def other_versions(directory: Path, identity: DatabaseIdentity) -> list[str]:
    stems = set()
    for suffix in SUFFIXES:
        for path in directory.glob(f"{identity.db_prefix}.*.{suffix}"):
            stem = path.name[: -len(f".{suffix}")]
            if stem != identity.stem:
                stems.add(stem)
    return sorted(stems)


def problem_from_find_key(key: str) -> MIOpenConvolution:
    problem = MIOpenConvolution.from_db_key(key)
    if problem.to_find_db_key() != key:
        raise DatabaseError(f"Find DB key does not round-trip: {key}")
    return problem


def validate_pair(directory: Path, identity: DatabaseIdentity, *, require_files: bool = True) -> dict[str, Any]:
    report: dict[str, Any] = {
        "directory": str(directory),
        "stem": identity.stem,
        "files": {},
        "errors": [],
        "other_versions": other_versions(directory, identity),
        "find_without_perf": [],
        "perf_without_find": [],
    }
    loaded: dict[str, RecordFile] = {}
    for suffix in SUFFIXES:
        path = identity.path(directory, suffix)
        report["files"][suffix] = {"path": str(path), "exists": path.is_file(), "records": 0, "duplicates": 0}
        if not path.is_file():
            if require_files:
                report["errors"].append(f"Missing {path}")
            continue
        try:
            record_file = read_records(path)
        except DatabaseError as exc:
            report["errors"].append(str(exc))
            continue
        loaded[suffix] = record_file
        report["files"][suffix].update(records=len(record_file.records), duplicates=record_file.duplicate_count)

    expected_perf_keys: set[str] = set()
    if FIND_SUFFIX in loaded:
        for key in loaded[FIND_SUFFIX].records:
            try:
                expected_perf_keys.add(problem_from_find_key(key).to_perf_db_key())
            except (DatabaseError, ValueError, IndexError) as exc:
                report["errors"].append(f"{loaded[FIND_SUFFIX].path}: invalid find key {key}: {exc}")
    if PERF_SUFFIX in loaded:
        for key in loaded[PERF_SUFFIX].records:
            if not _PERF_KEY_RE.match(key):
                report["errors"].append(f"{loaded[PERF_SUFFIX].path}: invalid performance key {key}")
    if FIND_SUFFIX in loaded and PERF_SUFFIX in loaded:
        find_by_perf = {}
        for key in loaded[FIND_SUFFIX].records:
            try:
                find_by_perf[problem_from_find_key(key).to_perf_db_key()] = key
            except (DatabaseError, ValueError, IndexError):
                pass
        perf_keys = set(loaded[PERF_SUFFIX].records)
        report["find_without_perf"] = sorted(find_by_perf[key] for key in set(find_by_perf) - perf_keys)
        report["perf_without_find"] = sorted(perf_keys - set(find_by_perf))
    report["valid"] = not report["errors"]
    return report


def diff_records(old: dict[str, str], new: dict[str, str]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(new) - set(old)),
        "removed": sorted(set(old) - set(new)),
        "replaced": sorted(key for key in set(old) & set(new) if old[key] != new[key]),
    }


def diff_pairs(old_directory: Path, new_directory: Path, identity: DatabaseIdentity) -> dict[str, Any]:
    old = load_pair(old_directory, identity)
    new = load_pair(new_directory, identity)
    return {
        suffix: diff_records(
            old[suffix].records if old[suffix] else {},
            new[suffix].records if new[suffix] else {},
        )
        for suffix in SUFFIXES
    }


def _write_records(path: Path, records: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    with os.fdopen(handle, "w") as output:
        output.write("".join(f"{key}={records[key]}\n" for key in sorted(records)))
    return Path(temporary)


def merge_pairs(
    base_directory: Path | None,
    tuned_directories: Iterable[Path],
    output_directory: Path,
    identity: DatabaseIdentity,
    *,
    allow_no_tuned_files: bool = False,
) -> dict[str, Any]:
    """Merge tuned DB pairs into the base pair by record key and write the result.

    Without tuned files the merge raises, since a standalone merge is then likely given the
    wrong directories. The tuning runner sets ``allow_no_tuned_files`` because MIOpen can
    legitimately write nothing (failed commands, or records served by the system DB); the
    output is then the base pair, and the runner's own checks decide the outcome.
    """
    base = load_pair(base_directory, identity) if base_directory else {suffix: None for suffix in SUFFIXES}
    merged: dict[str, dict[str, str]] = {
        suffix: dict(base[suffix].records) if base[suffix] else {} for suffix in SUFFIXES
    }
    original = {suffix: dict(records) for suffix, records in merged.items()}
    tuned_values: dict[str, dict[str, tuple[str, Path]]] = {suffix: {} for suffix in SUFFIXES}
    tuned_files = 0
    for directory in tuned_directories:
        pair = load_pair(directory, identity)
        for suffix in SUFFIXES:
            record_file = pair[suffix]
            if record_file is None:
                continue
            tuned_files += 1
            for key, value in record_file.records.items():
                previous = tuned_values[suffix].get(key)
                if previous and previous[0] != value:
                    raise DatabaseError(
                        f"Conflicting tuned {suffix} records for {key} in {previous[1]} and {record_file.path}"
                    )
                tuned_values[suffix][key] = (value, record_file.path)
                merged[suffix][key] = value
    if tuned_files == 0 and not allow_no_tuned_files:
        raise DatabaseError(f"No tuned DB files found for {identity.stem}")

    temporary_files: list[tuple[Path, Path]] = []
    try:
        for suffix in SUFFIXES:
            destination = identity.path(output_directory, suffix)
            temporary_files.append((_write_records(destination, merged[suffix]), destination))
        for temporary, _destination in temporary_files:
            read_records(temporary)
        for temporary, destination in temporary_files:
            temporary.replace(destination)
    finally:
        for temporary, _destination in temporary_files:
            temporary.unlink(missing_ok=True)
    return {suffix: diff_records(original[suffix], merged[suffix]) for suffix in SUFFIXES}
