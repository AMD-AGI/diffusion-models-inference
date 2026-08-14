import pytest
import shutil
import sys
import unittest

from bulkbench import BulkBench
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


class TestSystem(unittest.TestCase):
    def test_configs_file(self):
        project_dir = Path(__file__).parent / "proj0"
        with TemporaryDirectory() as output_dir:
            bulk_bench = BulkBench(
                project_dir=project_dir,
                backup_dir=Path(output_dir) / "backups",
                configs_file="my_configs",
                results_dir=Path(output_dir) / "results",
                report_dir=Path(output_dir) / "report",
                # Specify arch to avoid a rocminfo call.
                arch="",
            )

        self.assertEqual(
            bulk_bench.configs,
            {
                "group1": {
                    "name": "group1",
                    "configs": ["cfg1", "cfg2"],
                    "override_args": (
                        '{"num_iterations":5,"use_cfg_parallel":true,"nested":{"values":[1,null]}}'
                    ),
                },
                "group2": {
                    "name": "group2",
                    "configs": ["cfg3"],
                    "override_args": None,
                },
                "group3": {
                    "name": "group3",
                    "configs": ["cfg4"],
                    "override_args": None,
                },
            },
        )

    def _read_configs(self, contents: str) -> BulkBench:
        with TemporaryDirectory() as project_dir_value:
            project_dir = Path(project_dir_value)
            (project_dir / "configs.yaml").write_text(contents, encoding="utf-8")
            (project_dir / "patches.yaml").write_text(
                "- name: baseline\n  patches: []\n", encoding="utf-8"
            )
            return BulkBench(project_dir=project_dir, arch="")

    def assertInvalidConfigs(self, contents: str, expected_message: str) -> None:
        with self.assertRaises(ValueError) as context:
            self._read_configs(contents)
        self.assertIn(expected_message, str(context.exception))

    def test_configs_file_schema_errors(self):
        cases = (
            ("", "must contain a YAML list"),
            ("[]", "must contain at least one enabled config group"),
            ("{}", "must contain a YAML list"),
            ("- configs: [cfg]", "missing required attribute 'name'"),
            ("- name: group\n  configs: [cfg]\n  extra: true", "unknown attribute(s): extra"),
            ("- name: '  '\n  configs: [cfg]", "'name' must be a non-empty string"),
            ("- name: group", "missing required attribute 'configs'"),
            ("- name: group\n  configs: []", "'configs' must be a non-empty list"),
            ("- name: group\n  configs: [cfg, 1]", "item 2 must be a non-empty string"),
            ("- name: group\n  configs: [cfg, cfg]", "duplicate config name 'cfg'"),
            (
                "- name: group\n  configs: [cfg]\n- name: group\n  configs: [other]",
                "duplicate group name 'group'",
            ),
        )
        for contents, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                self.assertInvalidConfigs(contents, expected_message)

    def test_enabled_values_are_normalized_and_disabled_groups_are_ignored(self):
        bulk_bench = self._read_configs(
            """
- name: bool_true
  configs: [cfg]
  enabled: true
- name: int_one
  configs: [cfg]
  enabled: 1
- name: string_true
  configs: [cfg]
  enabled: "true"
- name: string_one
  configs: [cfg]
  enabled: "1"
- name: alias_yes
  configs: [cfg]
  enabled: yes
- name: alias_on
  configs: [cfg]
  enabled: on
- enabled: false
  unknown: ignored
- enabled: 0
- enabled: "false"
- enabled: "0"
- enabled: no
- enabled: off
"""
        )

        self.assertEqual(
            set(bulk_bench.configs),
            {"bool_true", "int_one", "string_true", "string_one", "alias_yes", "alias_on"},
        )
        self.assertTrue(
            all("enabled" not in config_group for config_group in bulk_bench.configs.values())
        )

    def test_invalid_enabled_values_are_rejected(self):
        for value in ("2", "-1", "null", "[]", "{}", '"True"', '"yes"', '""'):
            with self.subTest(value=value):
                self.assertInvalidConfigs(
                    f"- name: group\n  configs: [cfg]\n  enabled: {value}",
                    "attribute 'enabled' must be a YAML boolean",
                )

    def test_all_groups_disabled_is_rejected(self):
        self.assertInvalidConfigs(
            '- enabled: false\n- enabled: 0\n- enabled: "false"\n- enabled: "0"',
            "must contain at least one enabled config group",
        )

    def test_override_args_errors(self):
        cases = (
            (
                "- name: group\n  configs: [cfg]\n  override_args: [value]",
                "'override_args' must be an object or null",
            ),
            (
                "- name: group\n  configs: [cfg]\n  override_args:\n    1: value",
                "contains non-string mapping key 1",
            ),
            (
                ("- name: group\n  configs: [cfg]\n  override_args:\n    generated_at: 2026-08-14"),
                "isn't valid JSON",
            ),
            (
                "- name: group\n  configs: [cfg]\n  override_args:\n    value: .nan",
                "isn't valid JSON",
            ),
            (
                ("- name: group\n  configs: [cfg]\n  override_args: &args\n    self: *args"),
                "contains a circular reference",
            ),
        )
        for contents, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                self.assertInvalidConfigs(contents, expected_message)

    def test_duplicate_yaml_keys_are_rejected(self):
        self.assertInvalidConfigs(
            "- enabled: false\n  name: group\n  name: other\n"
            "- name: enabled_group\n  configs: [cfg]",
            "found duplicate key 'name'",
        )

    def test_malformed_yaml_is_reported_as_value_error(self):
        self.assertInvalidConfigs("- name: [", "failed to read configs_file")

    def _read_patches(self, contents: str, files: tuple[str, ...] = ()) -> BulkBench:
        with TemporaryDirectory() as project_dir_value:
            project_dir = Path(project_dir_value)
            (project_dir / "configs.yaml").write_text(
                "- name: configs\n  configs: [cfg]\n", encoding="utf-8"
            )
            for relative_path in files:
                path = project_dir / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            (project_dir / "patches.yaml").write_text(
                contents.replace("$PROJECT", str(project_dir)), encoding="utf-8"
            )
            return BulkBench(project_dir=project_dir, arch="")

    def assertInvalidPatches(self, contents: str, expected_message: str, *files: str) -> None:
        with self.assertRaises(ValueError) as context:
            self._read_patches(contents, files)
        self.assertIn(expected_message, str(context.exception))

    def test_patches_file(self):
        bulk_bench = self._read_patches(
            """
- name: baseline
  patches: []
- name: changes
  patches:
    - patch: " change.patch "
      target: " $PROJECT/target.py "
    - enabled: false
      unknown: ignored
""",
            ("change.patch", "target.py"),
        )

        self.assertEqual(
            bulk_bench.patches,
            [
                {"name": "baseline", "patches": []},
                {
                    "name": "changes",
                    "patches": [
                        {
                            "patch": (bulk_bench.project_dir / "change.patch").resolve(),
                            "target": (bulk_bench.project_dir / "target.py").resolve(),
                        }
                    ],
                },
            ],
        )

    def test_patches_file_schema_errors(self):
        cases = (
            ("", "must contain a YAML list"),
            ("[]", "must contain at least one patch set"),
            ("{}", "must contain a YAML list"),
            ("- patches: []", "missing required attribute 'name'"),
            ("- name: baseline\n  patches: []\n  extra: true", "unknown attribute(s): extra"),
            ("- name: '  '\n  patches: []", "'name' must be a non-empty string"),
            ("- name: baseline", "missing required attribute 'patches'"),
            ("- name: baseline\n  patches: {}", "'patches' must be a list"),
            ("- name: baseline\n  patches: [value]", "patch 1 must be an object"),
            (
                "- name: baseline\n  patches:\n    - patch: missing.patch",
                "missing required attribute 'target'",
            ),
            (
                (
                    "- name: baseline\n  patches:\n"
                    "    - patch: missing.patch\n      target: missing.py\n      extra: true"
                ),
                "unknown attribute(s): extra",
            ),
            (
                (
                    "- name: baseline\n  patches:\n"
                    "    - patch: missing.patch\n      target: missing.py\n      enabled: 2"
                ),
                "attribute 'enabled' must be a YAML boolean",
            ),
        )
        for contents, expected_message in cases:
            with self.subTest(expected_message=expected_message):
                self.assertInvalidPatches(contents, expected_message)

    def test_patch_and_target_must_be_existing_files(self):
        self.assertInvalidPatches(
            "- name: changes\n  patches:\n"
            "    - patch: missing.patch\n      target: $PROJECT/target.py",
            "attribute 'patch' path",
            "target.py",
        )
        self.assertInvalidPatches(
            "- name: changes\n  patches:\n"
            "    - patch: change.patch\n      target: $PROJECT/missing.py",
            "attribute 'target' path",
            "change.patch",
        )

    def test_duplicate_patch_objects_are_rejected(self):
        self.assertInvalidPatches(
            """
- name: changes
  patches:
    - patch: change.patch
      target: $PROJECT/target.py
    - patch: ./change.patch
      target: $PROJECT/./target.py
""",
            "contains duplicate patch object",
            "change.patch",
            "target.py",
        )

    def test_duplicate_patch_sets_are_order_independent(self):
        self.assertInvalidPatches(
            """
- name: first
  patches:
    - patch: a.patch
      target: $PROJECT/a.py
    - patch: b.patch
      target: $PROJECT/b.py
- name: second
  patches:
    - patch: b.patch
      target: $PROJECT/b.py
    - patch: a.patch
      target: $PROJECT/a.py
""",
            "patch sets 'first' and 'second' contain duplicate patch sets",
            "a.patch",
            "a.py",
            "b.patch",
            "b.py",
        )

    def test_patch_object_can_be_shared_by_distinct_patch_sets(self):
        bulk_bench = self._read_patches(
            """
- name: first
  patches:
    - patch: shared.patch
      target: $PROJECT/shared.py
    - patch: a.patch
      target: $PROJECT/a.py
- name: second
  patches:
    - patch: shared.patch
      target: $PROJECT/shared.py
    - patch: b.patch
      target: $PROJECT/b.py
""",
            ("shared.patch", "shared.py", "a.patch", "a.py", "b.patch", "b.py"),
        )

        self.assertEqual(
            [patch_set["name"] for patch_set in bulk_bench.patches],
            ["first", "second"],
        )
        self.assertEqual(
            bulk_bench.patches[0]["patches"][0],
            bulk_bench.patches[1]["patches"][0],
        )

    def test_only_one_empty_patch_set_is_allowed(self):
        self.assertInvalidPatches(
            """
- name: baseline
  patches: []
- name: disabled
  patches:
    - enabled: false
      unknown: ignored
""",
            "patch sets 'baseline' and 'disabled' contain duplicate patch sets",
        )

    def test_patch_set_names_are_unique(self):
        self.assertInvalidPatches(
            "- name: group\n  patches: []\n- name: ' group '\n  patches: []",
            "contains duplicate patch set name 'group'",
        )

    def _make_runnable_bulk_bench(
        self, project_dir: Path
    ) -> tuple[BulkBench, tuple[Path, Path]]:
        (project_dir / "configs.yaml").write_text(
            "- name: configs\n  configs: [cfg]\n", encoding="utf-8"
        )
        targets = (project_dir / "first.py", project_dir / "second.py")
        for index, target in enumerate(targets):
            target.write_text(f"original {index}", encoding="utf-8")
            (project_dir / f"{index}.patch").write_text(
                f"patch {index}", encoding="utf-8"
            )
        (project_dir / "patches.yaml").write_text(
            (
                "- name: changes\n"
                "  patches:\n"
                f"    - patch: 0.patch\n      target: {targets[0]}\n"
                f"    - patch: 1.patch\n      target: {targets[1]}\n"
            ),
            encoding="utf-8",
        )
        return BulkBench(project_dir=project_dir, arch=""), targets

    def test_backup_dir_must_be_empty(self):
        with TemporaryDirectory() as project_dir_value:
            project_dir = Path(project_dir_value)
            (project_dir / "configs.yaml").write_text(
                "- name: configs\n  configs: [cfg]\n", encoding="utf-8"
            )
            (project_dir / "patches.yaml").write_text(
                "- name: baseline\n  patches: []\n", encoding="utf-8"
            )
            backup_dir = project_dir / "backups"
            backup_dir.mkdir()
            (backup_dir / "existing").touch()

            with self.assertRaisesRegex(ValueError, "--backup_dir directory .* isn't empty"):
                BulkBench(project_dir=project_dir, backup_dir=backup_dir, arch="")

    def test_backup_dir_must_not_overlap_output_dirs(self):
        with TemporaryDirectory() as project_dir_value:
            project_dir = Path(project_dir_value)
            (project_dir / "configs.yaml").write_text(
                "- name: configs\n  configs: [cfg]\n", encoding="utf-8"
            )
            (project_dir / "patches.yaml").write_text(
                "- name: baseline\n  patches: []\n", encoding="utf-8"
            )
            shared_dir = project_dir / "shared"

            with self.assertRaisesRegex(ValueError, "must not overlap --results_dir"):
                BulkBench(
                    project_dir=project_dir,
                    backup_dir=shared_dir,
                    results_dir=shared_dir,
                    arch="",
                )

    def test_run_snapshots_targets_by_patch_index_and_restores_them(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, targets = self._make_runnable_bulk_bench(
                Path(project_dir_value)
            )
            original_contents = [
                target.read_text(encoding="utf-8") for target in targets
            ]

            def run_all_configs():
                self.assertEqual(
                    {path.name for path in bulk_bench.backup_dir.iterdir()},
                    {"00000", "00000.path", "00001", "00001.path"},
                )
                for index, target in enumerate(targets):
                    self.assertEqual(
                        (bulk_bench.backup_dir / f"{index:05d}.path").read_text(
                            encoding="utf-8"
                        ),
                        str(target.resolve()),
                    )
                    target.write_text(f"modified {index}", encoding="utf-8")
                return 37

            bulk_bench._runAllConfigs = Mock(side_effect=run_all_configs)

            self.assertEqual(bulk_bench.run(), 0)
            self.assertEqual(bulk_bench._runAllConfigs.call_count, 1)
            self.assertEqual(
                [target.read_text(encoding="utf-8") for target in targets],
                original_contents,
            )
            self.assertEqual(list(bulk_bench.backup_dir.iterdir()), [])

    def test_run_restores_targets_when_run_all_configs_raises(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, targets = self._make_runnable_bulk_bench(
                Path(project_dir_value)
            )
            original_contents = [
                target.read_text(encoding="utf-8") for target in targets
            ]
            primary_error = RuntimeError("run failed")

            def run_all_configs():
                targets[0].write_text("modified", encoding="utf-8")
                raise primary_error

            bulk_bench._runAllConfigs = Mock(side_effect=run_all_configs)

            with self.assertRaises(RuntimeError) as context:
                bulk_bench.run()
            self.assertIs(context.exception, primary_error)
            self.assertEqual(
                [target.read_text(encoding="utf-8") for target in targets],
                original_contents,
            )
            self.assertEqual(list(bulk_bench.backup_dir.iterdir()), [])

    def test_run_restores_targets_when_patch_application_raises(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, targets = self._make_runnable_bulk_bench(
                Path(project_dir_value)
            )
            original_contents = [
                target.read_text(encoding="utf-8") for target in targets
            ]
            primary_error = RuntimeError("apply failed")

            def apply_patches(_patch_set):
                targets[0].write_text("partially patched", encoding="utf-8")
                raise primary_error

            bulk_bench._applyPatches = Mock(side_effect=apply_patches)
            bulk_bench._runAllConfigs = Mock()

            with self.assertRaises(RuntimeError) as context:
                bulk_bench.run()
            self.assertIs(context.exception, primary_error)
            bulk_bench._runAllConfigs.assert_not_called()
            self.assertEqual(
                [target.read_text(encoding="utf-8") for target in targets],
                original_contents,
            )
            self.assertEqual(list(bulk_bench.backup_dir.iterdir()), [])

    def test_run_groups_primary_and_restoration_failures(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, targets = self._make_runnable_bulk_bench(
                Path(project_dir_value)
            )
            primary_error = RuntimeError("run failed")

            def run_all_configs():
                targets[0].write_text("modified", encoding="utf-8")
                raise primary_error

            real_copy2 = shutil.copy2
            copy_count = 0

            def fail_restoration(source, destination):
                nonlocal copy_count
                copy_count += 1
                if copy_count <= len(targets):
                    return real_copy2(source, destination)
                raise OSError(f"can't restore {destination}")

            bulk_bench._runAllConfigs = Mock(side_effect=run_all_configs)
            with (
                patch(
                    "bulkbench.bulkbench.shutil.copy2",
                    side_effect=fail_restoration,
                ),
                self.assertRaises(BaseExceptionGroup) as context,
            ):
                bulk_bench.run()

            self.assertIs(context.exception.exceptions[0], primary_error)
            self.assertEqual(
                {path.name for path in bulk_bench.backup_dir.iterdir()},
                {"00000", "00000.path", "00001", "00001.path"},
            )


if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv))
