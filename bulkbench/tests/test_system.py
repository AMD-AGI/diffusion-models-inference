import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from bulkbench import BulkBench


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
            ("[]", "must contain at least one config group"),
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
            "- name: group\n  name: other\n  configs: [cfg]",
            "found duplicate key 'name'",
        )

    def test_malformed_yaml_is_reported_as_value_error(self):
        self.assertInvalidConfigs("- name: [", "failed to read configs_file")


if __name__ == "__main__":
    import sys
    import pytest

    sys.exit(pytest.main(sys.argv))
