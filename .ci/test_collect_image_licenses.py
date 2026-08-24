#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from email.header import Header
from pathlib import Path


COLLECTOR_PATH = Path(__file__).with_name("collect_image_licenses.py")


def load_collector():
    specification = importlib.util.spec_from_file_location(
        "collect_image_licenses", COLLECTOR_PATH
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


collector = load_collector()


class CollectImageLicensesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_repository(self, relative_path: str, license_name: str | None) -> Path:
        repository = self.root / "app" / relative_path
        repository.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", repository], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                repository,
                "remote",
                "add",
                "origin",
                "https://build-user:secret@example.com/example/project.git",
            ],
            check=True,
        )
        (repository / "README.md").write_text("fixture\n", encoding="utf-8")
        if license_name is not None:
            license_path = repository / license_name
            license_path.parent.mkdir(parents=True, exist_ok=True)
            license_path.write_text("fixture license\n", encoding="utf-8")
        subprocess.run(["git", "-C", repository, "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                repository,
                "-c",
                "user.name=License Test",
                "-c",
                "user.email=license-test@example.com",
                "commit",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        return repository

    def run_collector(
        self, required_depth: int = 2, evidence_root: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        output = self.root / "output"
        command = [
            "python3",
            str(COLLECTOR_PATH),
            "--output",
            str(output),
            "--scan-root",
            f"app={self.root / 'app'}",
            "--git-root",
            str(self.root / "app"),
            "--fail-on-missing-git-license",
            "--required-git-depth",
            str(required_depth),
        ]
        if evidence_root is not None:
            command.extend(["--evidence-root", f"builder={evidence_root}"])
        return subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_normalizes_non_string_metadata_values(self) -> None:
        class Metadata:
            @staticmethod
            def get_all(key: str, default: list[str]) -> list[object]:
                self.assertEqual(key, "License")
                self.assertEqual(default, [])
                return [Header("MIT"), " Apache-2.0 ", "  "]

        self.assertEqual(
            collector.metadata_values(Metadata(), "License"),
            ["MIT", "Apache-2.0"],
        )

    def test_collects_nested_license_and_redacts_origin_credentials(self) -> None:
        repository = self.create_repository("external/project", "docs/NOTICE.txt")

        result = self.run_collector()

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.root / "output/MANIFEST.json").read_text())
        self.assertEqual(len(manifest["git_repositories"]), 1)
        record = manifest["git_repositories"][0]
        self.assertEqual(record["path"], repository.as_posix())
        self.assertEqual(record["origin"], "https://example.com/example/project.git")
        self.assertEqual(
            record["legal_files"],
            ["files/app/external/project/docs/NOTICE.txt"],
        )
        collected = self.root / "output" / record["legal_files"][0]
        self.assertEqual(collected.read_text(encoding="utf-8"), "fixture license\n")

    def test_fails_when_required_repository_has_no_license(self) -> None:
        repository = self.create_repository("external/unlicensed", None)

        result = self.run_collector()

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            f"missing required Git license: {repository}",
            result.stderr,
        )
        manifest = json.loads((self.root / "output/MANIFEST.json").read_text())
        self.assertIn(
            f"Git repository has no root-level legal document: {repository}",
            manifest["warnings"],
        )

    def test_imports_complete_builder_evidence_bundle(self) -> None:
        self.create_repository("project", "LICENSE")
        evidence_root = self.root / "builder-evidence"
        (evidence_root / "files/source").mkdir(parents=True)
        (evidence_root / "files/source/LICENSE").write_text(
            "builder license\n", encoding="utf-8"
        )
        (evidence_root / "MANIFEST.json").write_text(
            '{"git_repositories": [{"revision": "abc123"}]}\n', encoding="utf-8"
        )

        result = self.run_collector(evidence_root=evidence_root)

        self.assertEqual(result.returncode, 0, result.stderr)
        output = self.root / "output"
        manifest = json.loads((output / "MANIFEST.json").read_text())
        self.assertEqual(
            manifest["evidence_bundles"],
            [
                {
                    "label": "builder",
                    "manifest": "files/evidence/builder/MANIFEST.json",
                    "source": evidence_root.as_posix(),
                }
            ],
        )
        self.assertTrue((output / "files/evidence/builder/MANIFEST.json").is_file())
        self.assertEqual(
            (output / "files/evidence/builder/files/source/LICENSE").read_text(),
            "builder license\n",
        )


if __name__ == "__main__":
    unittest.main()