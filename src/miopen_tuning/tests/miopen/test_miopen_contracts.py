# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""GPU tests for MIOpen behavior used by the tuning tools.

Set MIOPEN_CONTRACT_TESTS=1 to run these tests inside a ROCm image on a GPU.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from miopen_tuning.problem import MIOpenConvolution
from miopen_tuning.identity import collect_identity, expected_db_version_token
from miopen_tuning.logs import append_index, run_logged


RUN_CONTRACTS = os.environ.get("MIOPEN_CONTRACT_TESTS") == "1"
ROOT = Path(__file__).resolve().parents[4]
PROBES = Path(__file__).with_name("probes.txt")
FIND_VARIABLES = ("MIOPEN_FIND_MODE", "MIOPEN_FIND_ENFORCE")


@dataclass(frozen=True)
class Probe:
    command_id: str
    command: str
    problem: MIOpenConvolution
    db_dir: Path
    result: object


def records(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or not value:
            raise AssertionError(f"{path}:{line_number}: malformed DB record")
        if key in values and values[key] != value:
            raise AssertionError(f"{path}:{line_number}: conflicting duplicate key {key}")
        values[key] = value
    return values


def db_files(path: Path, suffix: str) -> list[Path]:
    return sorted(path.glob(f"*.{suffix}"))


def db_hashes(path: Path) -> dict[str, str]:
    return {
        file.name: hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(path.iterdir())
        if file.is_file()
    }


def find_solvers(value: str) -> list[str]:
    return [entry.split(":", 1)[0] for entry in value.split(";") if entry]


def merge_dbs(sources: list[Path], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    names = sorted({file.name for source in sources for file in source.glob("*.u*db.txt")})
    for name in names:
        merged: dict[str, str] = {}
        for source in sources:
            path = source / name
            if not path.exists():
                continue
            for key, value in records(path).items():
                if key in merged and merged[key] != value:
                    raise AssertionError(f"Conflicting {name} record for {key}")
                merged[key] = value
        (destination / name).write_text("".join(f"{key}={merged[key]}\n" for key in sorted(merged)))


@unittest.skipUnless(RUN_CONTRACTS, "set MIOPEN_CONTRACT_TESTS=1 to run MIOpen GPU contracts")
class MIOpenContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        artifact_root = os.environ.get("MIOPEN_CONTRACT_ARTIFACTS")
        cls.temporary_directory = None
        if artifact_root:
            cls.artifacts = Path(artifact_root)
            # Never delete a caller's directory: a mistyped path could point at real data.
            if cls.artifacts.exists() and (not cls.artifacts.is_dir() or any(cls.artifacts.iterdir())):
                raise RuntimeError(f"MIOPEN_CONTRACT_ARTIFACTS must name a new or empty directory: {cls.artifacts}")
            cls.artifacts.mkdir(parents=True, exist_ok=True)
        else:
            cls.temporary_directory = tempfile.TemporaryDirectory(prefix="miopen-contracts-")
            cls.artifacts = Path(cls.temporary_directory.name)
        cls.cache = Path(tempfile.mkdtemp(prefix="miopen-contract-cache-"))
        # These tests check that tuning writes user DB records. With MIOPEN_FIND_ENFORCE=3,
        # MIOpen does not search a problem whose performance record exists in the system DB,
        # so it writes no user record for it. A ROCm release that ships records for a probe
        # shape would then fail these tests even though MIOpen behaves as the tuning tools
        # expect. Pointing MIOpen at an empty system DB keeps the probes independent of the
        # system DB contents; test_probes_do_not_load_system_db_records checks that it works.
        cls.system_db = Path(tempfile.mkdtemp(prefix="miopen-contract-system-db-"))
        cls.timeout = int(os.environ.get("MIOPEN_CONTRACT_TIMEOUT", "1800"))
        cls.log_profile = os.environ.get("MIOPEN_CONTRACT_LOG_PROFILE", "standard")
        cls.find_mode = os.environ.get("MIOPEN_CONTRACT_FIND_MODE", "1")
        cls.find_enforce = os.environ.get("MIOPEN_CONTRACT_FIND_ENFORCE", "3")
        cls.identity = collect_identity(
            driver=os.environ.get("MIOPEN_DRIVER"),
            prefix_map_path=ROOT / "data" / "miopen" / "prefixes.txt",
            image=os.environ.get("MIOPEN_CONTRACT_IMAGE", ""),
            image_digest=os.environ.get("MIOPEN_CONTRACT_IMAGE_DIGEST", ""),
        )
        (cls.artifacts / "identity.json").write_text(json.dumps(cls.identity.to_dict(), indent=2, sort_keys=True) + "\n")
        cls.probes = cls._tune_probes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.cache, ignore_errors=True)
        shutil.rmtree(cls.system_db, ignore_errors=True)
        if cls.temporary_directory is not None:
            cls.temporary_directory.cleanup()

    @classmethod
    def _driver_command(cls, command: str) -> list[str]:
        argv = shlex.split(command)
        argv[0] = cls.identity.driver_path
        return argv

    @classmethod
    def _tune_probes(cls) -> list[Probe]:
        probes: list[Probe] = []
        commands = [line.strip() for line in PROBES.read_text().splitlines() if line.strip()]
        for index, command in enumerate(commands, 1):
            command_id = f"probe-{index:02d}"
            db_dir = cls.artifacts / "tune" / "db" / command_id
            db_dir.mkdir(parents=True)
            result = run_logged(
                cls._driver_command(command),
                command_id=command_id,
                log_dir=cls.artifacts / "tune" / "logs" / command_id,
                environment={
                    "MIOPEN_USER_DB_PATH": str(db_dir),
                    "MIOPEN_CUSTOM_CACHE_DIR": str(cls.cache),
                    "MIOPEN_SYSTEM_DB_PATH": str(cls.system_db),
                    "MIOPEN_FIND_MODE": cls.find_mode,
                    "MIOPEN_FIND_ENFORCE": cls.find_enforce,
                },
                log_profile=cls.log_profile,
                timeout=cls.timeout,
                phase="tune",
            )
            append_index(cls.artifacts / "tune" / "logs" / "index.jsonl", result, cls.artifacts)
            probes.append(Probe(command_id, command, MIOpenConvolution.from_miopendriver_command(command), db_dir, result))
        return probes

    def _read(self, probe: Probe, db_dir: Path, name: str, log_profile: str | None = None):
        result = run_logged(
            self._driver_command(probe.command),
            command_id=f"{probe.command_id}-{name}",
            log_dir=self.artifacts / "read" / "logs" / f"{probe.command_id}-{name}",
            environment={
                "MIOPEN_USER_DB_PATH": str(db_dir),
                "MIOPEN_CUSTOM_CACHE_DIR": str(self.cache),
                "MIOPEN_SYSTEM_DB_PATH": str(self.system_db),
            },
            log_profile=log_profile or self.log_profile,
            timeout=self.timeout,
            phase="read",
            unset_environment=FIND_VARIABLES,
        )
        append_index(self.artifacts / "read" / "logs" / "index.jsonl", result, self.artifacts)
        return result

    def test_driver_reports_miopen_version(self):
        self.assertTrue(self.identity.miopen_version, self.identity.driver_version_output)

    def test_probe_commands_tune_successfully(self):
        for probe in self.probes:
            with self.subTest(probe=probe.command_id):
                self.assertEqual(probe.result.exit_code, 0, probe.result.log_dir)

    def test_probes_do_not_load_system_db_records(self):
        # The user DB is empty while probes tune, so any loaded performance record came
        # from the system DB and MIOPEN_SYSTEM_DB_PATH did not take effect.
        for probe in self.probes:
            with self.subTest(probe=probe.command_id):
                self.assertEqual(probe.result.summary["loaded_perf_db_keys"], [], probe.result.log_dir)
                self.assertTrue(probe.result.summary["searched_perf_db_keys"], probe.result.log_dir)

    def test_tuning_writes_find_and_performance_dbs(self):
        for probe in self.probes:
            with self.subTest(probe=probe.command_id):
                self.assertEqual(len(db_files(probe.db_dir, "ufdb.txt")), 1)
                self.assertEqual(len(db_files(probe.db_dir, "udb.txt")), 1)
                self.assertTrue(records(db_files(probe.db_dir, "ufdb.txt")[0]))
                self.assertTrue(records(db_files(probe.db_dir, "udb.txt")[0]))

    def test_db_names_match_device_prefix_and_miopen_version(self):
        stems = {path.name.removesuffix(".udb.txt").removesuffix(".ufdb.txt") for probe in self.probes for path in probe.db_dir.glob("*.u*db.txt")}
        self.assertEqual(len(stems), 1, stems)
        stem = next(iter(stems))
        if self.identity.db_prefix:
            self.assertTrue(stem.startswith(f"{self.identity.db_prefix}."), stem)
        token = expected_db_version_token(self.identity.miopen_version, self.identity.miopen_build)
        if token:
            self.assertIn(token, stem)

    def test_find_db_key_maps_to_driver_command(self):
        for probe in self.probes:
            with self.subTest(probe=probe.command_id):
                keys = list(records(db_files(probe.db_dir, "ufdb.txt")[0]))
                self.assertEqual(len(keys), 1, keys)
                self.assertEqual(MIOpenConvolution.from_db_key(keys[0]), probe.problem)

    def test_performance_log_reports_written_db_keys(self):
        for probe in self.probes:
            with self.subTest(probe=probe.command_id):
                find_keys = set(records(db_files(probe.db_dir, "ufdb.txt")[0]))
                performance_keys = set(records(db_files(probe.db_dir, "udb.txt")[0]))
                self.assertEqual(set(probe.result.summary["db_keys"]), find_keys, probe.result.log_dir)
                self.assertTrue(set(probe.result.summary["perf_db_keys"]).issubset(performance_keys), probe.result.log_dir)
                self.assertTrue(probe.result.summary["perf_db_keys"], probe.result.log_dir)

    def test_tuned_db_is_used_without_db_changes(self):
        for probe in self.probes:
            with self.subTest(probe=probe.command_id):
                read_db = self.artifacts / "read" / "db" / probe.command_id
                shutil.copytree(probe.db_dir, read_db)
                before = db_hashes(read_db)
                result = self._read(probe, read_db, "tuned")
                self.assertEqual(result.exit_code, 0, result.log_dir)
                self.assertEqual(db_hashes(read_db), before)
                self.assertEqual(result.summary["db_keys"], list(records(db_files(read_db, "ufdb.txt")[0])))
                find_record = next(iter(records(db_files(read_db, "ufdb.txt")[0]).values()))
                self.assertIn(result.summary["selected_solver"], find_solvers(find_record), result.log_dir)

    def test_standard_and_diagnostic_logs_are_parseable(self):
        probe = self.probes[0]
        for profile in ("standard", "diagnostic"):
            with self.subTest(profile=profile):
                read_db = self.artifacts / "read" / "db" / f"{probe.command_id}-{profile}"
                shutil.copytree(probe.db_dir, read_db)
                result = self._read(probe, read_db, profile, profile)
                self.assertEqual(result.exit_code, 0, result.log_dir)
                self.assertTrue((result.log_dir / "stdout.log").is_file())
                self.assertTrue((result.log_dir / "stderr.log").is_file())
                self.assertIsNotNone(result.summary["selected_solver"], result.log_dir)
                for line in (result.log_dir / "events.jsonl").read_text().splitlines():
                    json.loads(line)

    def test_merged_db_is_readable(self):
        merged = self.artifacts / "read" / "db" / "merged"
        merge_dbs([probe.db_dir for probe in self.probes], merged)
        for probe in self.probes:
            with self.subTest(probe=probe.command_id):
                result = self._read(probe, merged, "merged")
                self.assertEqual(result.exit_code, 0, result.log_dir)
                self.assertIsNotNone(result.summary["selected_solver"], result.log_dir)


if __name__ == "__main__":
    unittest.main()
