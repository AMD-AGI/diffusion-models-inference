import pytest
import sys
import unittest

from bulkbench import BulkBench
from pathlib import Path
from tempfile import TemporaryDirectory


class TestSystem(unittest.TestCase):
    def test_configs_file(self):
        project_dir = Path(__file__).parent / "proj0"
        with TemporaryDirectory() as output_dir:
            bulk_bench = BulkBench(
                project_dir=project_dir,
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
            "- enabled: false\n- enabled: 0\n- enabled: \"false\"\n- enabled: \"0\"",
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


if __name__ == "__main__":
    sys.exit(pytest.main(sys.argv))
