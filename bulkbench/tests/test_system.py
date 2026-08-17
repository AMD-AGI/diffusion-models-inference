import difflib
import pytest
import shutil
import subprocess
import sys
import unittest

from bulkbench import BulkBench, ConfigRunError, ConfigRunResult
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch


class TestSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._benchmark_configs_temp = TemporaryDirectory()
        cls.benchmark_configs_dir = Path(cls._benchmark_configs_temp.name)
        for stem in ("cfg", "cfg1", "cfg2", "cfg3", "cfg4"):
            (cls.benchmark_configs_dir / f"{stem}.yaml").touch()
        cls._benchmark_configs_patch = patch(
            "bulkbench.bulkbench._BENCHMARK_CONFIGS_DIR",
            cls.benchmark_configs_dir,
        )
        cls._benchmark_configs_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._benchmark_configs_patch.stop()
        cls._benchmark_configs_temp.cleanup()

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

    def test_config_prefix_selects_an_existing_benchmark_yaml(self):
        bulk_bench = self._read_configs(
            "- name: group\n  configs: [cfg.variant, cfg2]\n"
        )
        self.assertEqual(
            bulk_bench.configs["group"]["configs"],
            ["cfg.variant", "cfg2"],
        )

    def test_invalid_config_prefix_is_rejected(self):
        for config_name in (".variant", "has/slash.variant", "has:colon.variant"):
            with self.subTest(config_name=config_name):
                self.assertInvalidConfigs(
                    f"- name: group\n  configs: [{config_name!r}]\n",
                    "prefix before the first dot must match",
                )

    def test_benchmark_yaml_must_be_a_file(self):
        (self.benchmark_configs_dir / "directory.yaml").mkdir()
        for config_name in ("missing.variant", "directory.variant"):
            with self.subTest(config_name=config_name):
                self.assertInvalidConfigs(
                    f"- name: group\n  configs: [{config_name}]\n",
                    "doesn't exist or isn't a file",
                )

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
            with patch.object(BulkBench, "_dryRunPatches"):
                return BulkBench(project_dir=project_dir, arch="")

    def assertInvalidPatches(self, contents: str, expected_message: str, *files: str) -> None:
        with self.assertRaises(ValueError) as context:
            self._read_patches(contents, files)
        self.assertIn(expected_message, str(context.exception))

    def test_config_group_and_patch_set_name_validation(self):
        valid_name = "aZ09-., ~!()[]_+={}"
        bulk_bench = self._read_configs(f"- name: ' {valid_name} '\n  configs: [cfg]")
        self.assertEqual(list(bulk_bench.configs), [valid_name])

        bulk_bench = self._read_patches(f"- name: ' {valid_name} '\n  patches: []")
        self.assertEqual(
            [patch_set["name"] for patch_set in bulk_bench.patches],
            [valid_name],
        )

        for invalid_name in (".", "..", "has/slash", "has:colon", "has?question", "has\\backslash"):
            with self.subTest(invalid_name=invalid_name, object_type="config group"):
                self.assertInvalidConfigs(
                    f"- name: '{invalid_name}'\n  configs: [cfg]",
                    "attribute 'name' must match",
                )
            with self.subTest(invalid_name=invalid_name, object_type="patch set"):
                self.assertInvalidPatches(
                    f"- name: '{invalid_name}'\n  patches: []",
                    "attribute 'name' must match",
                )

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

    def _make_config_runner_bulk_bench(
        self,
        project_dir: Path,
        configs: str,
        *,
        arch: str = "gfx942",
    ) -> tuple[BulkBench, Mock]:
        (project_dir / "configs.yaml").write_text(configs, encoding="utf-8")
        (project_dir / "patches.yaml").write_text(
            "- name: baseline\n  patches: []\n",
            encoding="utf-8",
        )
        bulk_bench = BulkBench(
            project_dir=project_dir,
            results_dir=project_dir / "results",
            report_dir=project_dir / "report",
            backup_dir=project_dir / "backups",
            arch=arch,
        )
        console = Mock()
        bulk_bench.Con = console
        return bulk_bench, console

    def test_run_config_builds_command_and_logs_output(self):
        with TemporaryDirectory() as project_dir_value:
            project_dir = Path(project_dir_value)
            bulk_bench, console = self._make_config_runner_bulk_bench(
                project_dir,
                (
                    "- name: group\n"
                    "  configs: [cfg.first, cfg.second, cfg2]\n"
                    "  override_args:\n"
                    "    iterations: 5\n"
                ),
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="benchmark output",
                stderr="benchmark warning",
            )

            with patch(
                "bulkbench.bulkbench.subprocess.run",
                return_value=completed,
            ) as run_process:
                bulk_bench._runConfig("changes", bulk_bench.configs["group"])

            workdir = project_dir / "results" / "changes"
            self.assertTrue(workdir.is_dir())
            run_process.assert_called_once_with(
                [
                    "python",
                    "/app/.ci/run.py",
                    "--name",
                    "cfg.first",
                    "--name",
                    "cfg.second",
                    "--name",
                    "cfg2",
                    "--override-args-json",
                    '{"iterations":5}',
                    "--tag",
                    "gfx942",
                    "--results-directory",
                    str(workdir),
                    str(self.benchmark_configs_dir / "cfg.yaml"),
                    str(self.benchmark_configs_dir / "cfg2.yaml"),
                ],
                capture_output=True,
                check=False,
                cwd="/app",
                shell=False,
                text=True,
            )
            self.assertEqual(
                console.trace.call_args_list,
                [
                    call("\ngroup stdout:\nbenchmark output"),
                    call("\ngroup stderr:\nbenchmark warning"),
                ],
            )

    def test_run_config_raises_with_captured_nonzero_result(self):
        with TemporaryDirectory() as project_dir_value:
            project_dir = Path(project_dir_value)
            bulk_bench, _ = self._make_config_runner_bulk_bench(
                project_dir,
                "- name: group\n  configs: [cfg]\n",
                arch="",
            )
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=7,
                stdout="partial output",
                stderr="runner failed",
            )

            with (
                patch(
                    "bulkbench.bulkbench.subprocess.run",
                    return_value=completed,
                ) as run_process,
                self.assertRaises(ConfigRunError) as context,
            ):
                bulk_bench._runConfig("baseline", bulk_bench.configs["group"])

            self.assertEqual(
                context.exception.result,
                ConfigRunResult(
                    config_name="group",
                    stdout="partial output",
                    stderr="runner failed",
                    returncode=7,
                ),
            )
            command = run_process.call_args.args[0]
            self.assertNotIn("--tag", command)
            self.assertNotIn("--override-args-json", command)

    def test_run_config_raises_with_process_start_failure(self):
        with TemporaryDirectory() as project_dir_value:
            project_dir = Path(project_dir_value)
            bulk_bench, _ = self._make_config_runner_bulk_bench(
                project_dir,
                "- name: group\n  configs: [cfg]\n",
            )
            failure = OSError("python isn't executable")

            with (
                patch(
                    "bulkbench.bulkbench.subprocess.run",
                    side_effect=failure,
                ),
                self.assertRaises(ConfigRunError) as context,
            ):
                bulk_bench._runConfig("baseline", bulk_bench.configs["group"])

            self.assertIs(context.exception.__cause__, failure)
            self.assertEqual(
                context.exception.result,
                ConfigRunResult(
                    config_name="group",
                    stdout="",
                    stderr="python isn't executable",
                    returncode=None,
                ),
            )

    def test_run_all_configs_preserves_group_order(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, _ = self._make_config_runner_bulk_bench(
                Path(project_dir_value),
                (
                    "- name: first\n"
                    "  configs: [cfg]\n"
                    "- name: second\n"
                    "  configs: [cfg2]\n"
                ),
            )
            bulk_bench._runConfig = Mock()
            bulk_bench.successful_runs = {}
            bulk_bench.unsuccessful_runs = {}

            bulk_bench._runAllConfigs("changes")

            self.assertEqual(
                bulk_bench._runConfig.call_args_list,
                [
                    call("changes", bulk_bench.configs["first"]),
                    call("changes", bulk_bench.configs["second"]),
                ],
            )
            self.assertEqual(
                bulk_bench.successful_runs,
                {"changes": ["first", "second"]},
            )
            self.assertEqual(
                bulk_bench.unsuccessful_runs,
                {"changes": {}},
            )

    def test_run_all_configs_records_expected_and_unexpected_failures(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, _ = self._make_config_runner_bulk_bench(
                Path(project_dir_value),
                (
                    "- name: successful\n"
                    "  configs: [cfg]\n"
                    "- name: process_failure\n"
                    "  configs: [cfg2]\n"
                    "- name: unexpected_failure\n"
                    "  configs: [cfg3]\n"
                ),
            )
            process_result = ConfigRunResult(
                config_name="process_failure",
                stdout="partial output",
                stderr="runner failed",
                returncode=3,
            )
            bulk_bench._runConfig = Mock(
                side_effect=(
                    None,
                    ConfigRunError(process_result),
                    ValueError("invalid runtime state"),
                )
            )
            bulk_bench.successful_runs = {}
            bulk_bench.unsuccessful_runs = {}

            bulk_bench._runAllConfigs("changes")

            self.assertEqual(
                bulk_bench.successful_runs,
                {"changes": ["successful"]},
            )
            failures = bulk_bench.unsuccessful_runs["changes"]
            self.assertIs(failures["process_failure"], process_result)
            unexpected_result = failures["unexpected_failure"]
            self.assertEqual(unexpected_result.config_name, "unexpected_failure")
            self.assertEqual(unexpected_result.stdout, "")
            self.assertIsNone(unexpected_result.returncode)
            self.assertIn("ValueError: invalid runtime state", unexpected_result.stderr)
            self.assertEqual(bulk_bench._runConfig.call_count, 3)

    def test_run_all_configs_does_not_catch_keyboard_interrupt(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, _ = self._make_config_runner_bulk_bench(
                Path(project_dir_value),
                "- name: group\n  configs: [cfg]\n",
            )
            bulk_bench._runConfig = Mock(side_effect=KeyboardInterrupt)
            bulk_bench.successful_runs = {}
            bulk_bench.unsuccessful_runs = {}

            with self.assertRaises(KeyboardInterrupt):
                bulk_bench._runAllConfigs("changes")

            self.assertNotIn("changes", bulk_bench.successful_runs)
            self.assertNotIn("changes", bulk_bench.unsuccessful_runs)

    def test_run_reinitializes_result_dictionaries(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, _ = self._make_config_runner_bulk_bench(
                Path(project_dir_value),
                "- name: group\n  configs: [cfg]\n",
            )
            bulk_bench.successful_runs = {"old": ["group"]}
            bulk_bench.unsuccessful_runs = {
                "old": {
                    "group": ConfigRunResult(
                        config_name="group",
                        stdout="",
                        stderr="old failure",
                        returncode=1,
                    )
                }
            }

            def verify_reset(_patch_set_name):
                self.assertEqual(bulk_bench.successful_runs, {})
                self.assertEqual(bulk_bench.unsuccessful_runs, {})

            bulk_bench._runAllConfigs = Mock(side_effect=verify_reset)

            self.assertEqual(bulk_bench.run(), 0)
            bulk_bench._runAllConfigs.assert_called_once_with("baseline")

    def _make_runnable_bulk_bench(
        self,
        project_dir: Path,
        mock_patch_commands: bool = True,
        mock_constructor_dry_run: bool = True,
    ) -> tuple[BulkBench, tuple[Path, Path]]:
        (project_dir / "configs.yaml").write_text(
            "- name: configs\n  configs: [cfg]\n", encoding="utf-8"
        )
        targets = (project_dir / "first.py", project_dir / "second.py")
        for index, target in enumerate(targets):
            target.write_text(f"original {index}", encoding="utf-8")
            (project_dir / f"{index}.patch").write_text(f"patch {index}", encoding="utf-8")
        (project_dir / "patches.yaml").write_text(
            (
                "- name: changes\n"
                "  patches:\n"
                f"    - patch: 0.patch\n      target: {targets[0]}\n"
                f"    - patch: 1.patch\n      target: {targets[1]}\n"
            ),
            encoding="utf-8",
        )
        if mock_constructor_dry_run:
            with patch.object(BulkBench, "_dryRunPatches"):
                bulk_bench = BulkBench(project_dir=project_dir, arch="")
        else:
            bulk_bench = BulkBench(project_dir=project_dir, arch="")
        if mock_patch_commands:
            bulk_bench._dryRunPatches = Mock()
            bulk_bench._applyPatches = Mock()
        return bulk_bench, targets

    def _make_patch_integration_project(
        self, project_dir: Path
    ) -> tuple[BulkBench, tuple[Path, Path], tuple[str, str], tuple[str, str]]:
        (project_dir / "configs.yaml").write_text(
            "- name: configs\n  configs: [cfg]\n", encoding="utf-8"
        )

        targets = (project_dir / "first.py", project_dir / "second.py")
        original_contents = ("alpha\ncommon\n", "one\ntwo\n")
        patched_contents = ("patched alpha\ncommon\n", "one\npatched two\n")
        for index, (target, original, patched_content) in enumerate(
            zip(targets, original_contents, patched_contents, strict=True)
        ):
            target.write_text(original, encoding="utf-8")
            patch_contents = "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    patched_content.splitlines(keepends=True),
                    fromfile=str(target),
                    tofile=str(target),
                )
            )
            (project_dir / f"{index}.patch").write_text(
                patch_contents, encoding="utf-8"
            )

        (project_dir / "patches.yaml").write_text(
            (
                "- name: first\n"
                "  patches:\n"
                f"    - patch: 0.patch\n      target: {targets[0]}\n"
                "- name: second\n"
                "  patches:\n"
                f"    - patch: 0.patch\n      target: {targets[0]}\n"
                f"    - patch: 1.patch\n      target: {targets[1]}\n"
            ),
            encoding="utf-8",
        )
        return (
            BulkBench(project_dir=project_dir, arch=""),
            targets,
            original_contents,
            patched_contents,
        )

    def _assert_real_patch_lifecycle(self, failure: BaseException | None) -> None:
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, targets, originals, patched = (
                self._make_patch_integration_project(Path(project_dir_value))
            )
            invocation = 0

            def run_all_configs(_patch_set_name):
                nonlocal invocation
                expected = (
                    (patched[0], originals[1]) if invocation == 0 else patched
                )
                self.assertEqual(
                    tuple(
                        target.read_text(encoding="utf-8")
                        for target in targets
                    ),
                    expected,
                )
                invocation += 1
                if failure is not None and invocation == 2:
                    raise failure

            bulk_bench._runAllConfigs = Mock(side_effect=run_all_configs)

            if failure is None:
                self.assertEqual(bulk_bench.run(), 0)
            else:
                with self.assertRaises(type(failure)) as context:
                    bulk_bench.run()
                self.assertIs(context.exception, failure)

            self.assertEqual(bulk_bench._runAllConfigs.call_count, 2)
            self.assertEqual(
                tuple(
                    target.read_text(encoding="utf-8") for target in targets
                ),
                originals,
            )
            self.assertEqual(list(bulk_bench.backup_dir.iterdir()), [])

    def test_real_patches_are_visible_to_benchmarks_and_always_reverted(self):
        for failure in (
            None,
            RuntimeError("benchmark failed"),
            AssertionError("benchmark assertion failed"),
        ):
            with self.subTest(failure_type=type(failure).__name__):
                self._assert_real_patch_lifecycle(failure)

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

    def test_constructor_dry_runs_every_loaded_patch(self):
        with TemporaryDirectory() as project_dir_value:
            commands = []

            def run_patch(command, **_kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("bulkbench.bulkbench.subprocess.run", side_effect=run_patch):
                bulk_bench, targets = self._make_runnable_bulk_bench(
                    Path(project_dir_value),
                    mock_patch_commands=False,
                    mock_constructor_dry_run=False,
                )

            self.assertEqual(
                commands,
                [
                    [
                        "patch",
                        "--batch",
                        "--dry-run",
                        str(targets[0]),
                        str(bulk_bench.project_dir / "0.patch"),
                    ],
                    [
                        "patch",
                        "--batch",
                        "--dry-run",
                        str(targets[1]),
                        str(bulk_bench.project_dir / "1.patch"),
                    ],
                ],
            )

    def test_constructor_dry_run_failure_prevents_output_directory_creation(self):
        with TemporaryDirectory() as project_dir_value:
            project_dir = Path(project_dir_value)
            patch_error = subprocess.CalledProcessError(
                2,
                ["patch"],
                output="dry-run stdout",
                stderr="dry-run stderr",
            )

            with (
                patch(
                    "bulkbench.bulkbench.subprocess.run",
                    side_effect=patch_error,
                ),
                self.assertRaises(ValueError) as context,
            ):
                self._make_runnable_bulk_bench(
                    project_dir,
                    mock_patch_commands=False,
                    mock_constructor_dry_run=False,
                )

            self.assertIs(context.exception.__cause__, patch_error)
            self.assertFalse((project_dir / "results").exists())
            self.assertFalse((project_dir / "report").exists())
            self.assertFalse((project_dir / "__backups").exists())

    def test_run_dry_runs_all_patches_before_snapshot_and_applies_in_order(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, targets = self._make_runnable_bulk_bench(
                Path(project_dir_value), mock_patch_commands=False
            )
            commands = []

            def run_patch(command, **kwargs):
                commands.append(command)
                self.assertEqual(
                    kwargs,
                    {
                        "capture_output": True,
                        "check": True,
                        "shell": False,
                        "text": True,
                    },
                )
                if "--dry-run" in command:
                    self.assertEqual(list(bulk_bench.backup_dir.iterdir()), [])
                else:
                    self.assertEqual(
                        {path.name for path in bulk_bench.backup_dir.iterdir()},
                        {"00000", "00000.path", "00001", "00001.path"},
                    )
                return subprocess.CompletedProcess(command, 0, "", "")

            bulk_bench._runAllConfigs = Mock()
            with patch("bulkbench.bulkbench.subprocess.run", side_effect=run_patch):
                self.assertEqual(bulk_bench.run(), 0)

            patch_paths = (
                bulk_bench.project_dir / "0.patch",
                bulk_bench.project_dir / "1.patch",
            )
            self.assertEqual(
                commands,
                [
                    [
                        "patch",
                        "--batch",
                        "--dry-run",
                        str(targets[0]),
                        str(patch_paths[0]),
                    ],
                    [
                        "patch",
                        "--batch",
                        "--dry-run",
                        str(targets[1]),
                        str(patch_paths[1]),
                    ],
                    ["patch", "--batch", str(targets[0]), str(patch_paths[0])],
                    ["patch", "--batch", str(targets[1]), str(patch_paths[1])],
                ],
            )
            bulk_bench._runAllConfigs.assert_called_once_with("changes")
            self.assertEqual(list(bulk_bench.backup_dir.iterdir()), [])

    def test_dry_run_failure_prevents_backups_patching_and_benchmarks(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, targets = self._make_runnable_bulk_bench(
                Path(project_dir_value), mock_patch_commands=False
            )
            patch_error = subprocess.CalledProcessError(
                2,
                ["patch"],
                output="dry-run stdout",
                stderr="dry-run stderr",
            )
            bulk_bench._runAllConfigs = Mock()

            with (
                patch(
                    "bulkbench.bulkbench.subprocess.run",
                    side_effect=patch_error,
                ),
                self.assertRaises(ValueError) as context,
            ):
                bulk_bench.run()

            message = str(context.exception)
            self.assertIn("patch dry-run failed for patch set 'changes'", message)
            self.assertIn(str(bulk_bench.project_dir / "0.patch"), message)
            self.assertIn(str(targets[0]), message)
            self.assertIn("exit status 2", message)
            self.assertIn("dry-run stdout", message)
            self.assertIn("dry-run stderr", message)
            self.assertIs(context.exception.__cause__, patch_error)
            bulk_bench._runAllConfigs.assert_not_called()
            self.assertEqual(list(bulk_bench.backup_dir.iterdir()), [])

    def test_run_snapshots_targets_by_patch_index_and_restores_them(self):
        with TemporaryDirectory() as project_dir_value:
            bulk_bench, targets = self._make_runnable_bulk_bench(Path(project_dir_value))
            original_contents = [target.read_text(encoding="utf-8") for target in targets]

            def run_all_configs(_patch_set_name):
                self.assertEqual(
                    {path.name for path in bulk_bench.backup_dir.iterdir()},
                    {"00000", "00000.path", "00001", "00001.path"},
                )
                for index, target in enumerate(targets):
                    self.assertEqual(
                        (bulk_bench.backup_dir / f"{index:05d}.path").read_text(encoding="utf-8"),
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
            bulk_bench, targets = self._make_runnable_bulk_bench(Path(project_dir_value))
            original_contents = [target.read_text(encoding="utf-8") for target in targets]
            primary_error = RuntimeError("run failed")

            def run_all_configs(_patch_set_name):
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
            bulk_bench, targets = self._make_runnable_bulk_bench(Path(project_dir_value))
            original_contents = [target.read_text(encoding="utf-8") for target in targets]
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
            bulk_bench, targets = self._make_runnable_bulk_bench(Path(project_dir_value))
            primary_error = RuntimeError("run failed")

            def run_all_configs(_patch_set_name):
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
