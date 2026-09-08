import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from bulkbench.benchmark_sources import get_benchmark_sources
from bulkbench.parser_JSON import parser_JSON


class TestBenchmarkSourceDiscovery(unittest.TestCase):
    @staticmethod
    def _write_timings(directory: Path, data=None) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "timings.json").write_text(
            json.dumps([1, 2, 3] if data is None else data),
            encoding="utf-8",
        )

    def test_direct_json_file_uses_two_source_mode_name(self):
        with TemporaryDirectory() as temp_dir:
            result_dir = Path(temp_dir) / "model"
            result_dir.mkdir()
            json_path = result_dir / "custom.json"
            json_path.write_text("[1, 2]", encoding="utf-8")

            self.assertEqual(
                get_benchmark_sources(json_path, None),
                {("model", str(result_dir))},
            )

    def test_rejects_immediate_and_nested_timings_files(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "model"
            self._write_timings(root)
            self._write_timings(root / "nested")

            with self.assertRaises(ValueError) as context:
                get_benchmark_sources(root, None)

            self.assertEqual(
                str(context.exception),
                "The directory is malformed and contains 2 timings.json files, "
                f"an immediate and a nested in {root / 'nested' / 'timings.json'}",
            )

    def test_rejects_immediate_and_nested_timings_files_deep_in_tree(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            malformed_dir = root / "patch" / "group"
            nested_dir = malformed_dir / "config"
            self._write_timings(malformed_dir)
            self._write_timings(nested_dir)

            with self.assertRaises(ValueError) as context:
                get_benchmark_sources(root, None)

            self.assertEqual(
                str(context.exception),
                "The directory is malformed and contains 2 timings.json files, "
                f"an immediate and a nested in {nested_dir / 'timings.json'}",
            )

    def test_rejects_timings_file_nested_three_layers_below_immediate_file(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            malformed_dir = root / "patch"
            nested_dir = malformed_dir / "one" / "two" / "three"
            self._write_timings(malformed_dir)
            self._write_timings(nested_dir)

            with self.assertRaises(ValueError) as context:
                get_benchmark_sources(root, None)

            self.assertEqual(
                str(context.exception),
                "The directory is malformed and contains 2 timings.json files, "
                f"an immediate and a nested in {nested_dir / 'timings.json'}",
            )

    def test_accepts_a_single_immediate_timings_file(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "model"
            self._write_timings(root)

            self.assertEqual(
                get_benchmark_sources(root, None),
                {("model", str(root))},
            )

    def test_discovers_nested_timings_and_warns_about_barren_directories(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "patch1" / "group" / "model"
            second = root / "patch2" / "group" / "model"
            barren = root / "unused" / "leaf"
            self._write_timings(first)
            self._write_timings(second)
            barren.mkdir(parents=True)
            debug_log = Mock()

            sources = get_benchmark_sources(root, None, debug_log)

            self.assertEqual(
                sources,
                {
                    ("model|patch1/group", str(first)),
                    ("model|patch2/group", str(second)),
                },
            )
            debug_log.warning.assert_called_once()
            warning = debug_log.warning.call_args.args[0]
            self.assertIn(str(root / "unused"), warning)
            self.assertIn(str(barren), warning)
            self.assertNotIn(str(root / "patch1"), warning)

    def test_discovers_timings_below_directory_symlink(self):
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            root = temp_path / "results"
            target = temp_path / "external"
            result_dir = target / "config"
            root.mkdir()
            self._write_timings(result_dir)
            (root / "linked").symlink_to(target, target_is_directory=True)

            self.assertEqual(
                get_benchmark_sources(root, None),
                {("config|linked", str(root / "linked" / "config"))},
            )

    def test_filter_selects_indexed_directories_and_implicitly_selects_zero(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result_dir = root / "patch" / "group" / "config"
            self._write_timings(result_dir)

            self.assertEqual(
                get_benchmark_sources(root, {0, 1}),
                {("config/group|patch", str(result_dir))},
            )
            self.assertEqual(
                get_benchmark_sources(root, {0, 2}),
                {("config/patch|group", str(result_dir))},
            )
            self.assertEqual(
                get_benchmark_sources(root, {0, 1, 2}),
                {("config/group/patch|", str(result_dir))},
            )

    def test_rejects_invalid_filter_values(self):
        with TemporaryDirectory() as temp_dir:
            for invalid_filter in ("one", "-1", "1,,2", "1,"):
                with self.subTest(filter=invalid_filter), self.assertRaises(ValueError):
                    get_benchmark_sources(temp_dir, {invalid_filter})

    def test_warns_and_skips_paths_too_shallow_for_filter(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shallow = root / "group" / "config"
            deep = root / "platform" / "patch" / "group" / "config"
            self._write_timings(shallow)
            self._write_timings(deep)
            debug_log = Mock()

            sources = get_benchmark_sources(root, {0, 3}, debug_log)

            self.assertEqual(
                sources,
                {("config/platform|patch/group", str(deep))},
            )
            debug_log.warning.assert_called_once()
            warning = debug_log.warning.call_args.args[0]
            self.assertIn("filter '{0, 3}'", warning)
            self.assertIn(str(shallow), warning)
            self.assertNotIn(str(deep), warning)

    def test_warns_and_raises_when_no_benchmark_is_found(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            leaf = root / "empty"
            leaf.mkdir()
            debug_log = Mock()

            with self.assertRaisesRegex(ValueError, "No benchmarks"):
                get_benchmark_sources(root, None, debug_log)

            debug_log.warning.assert_called_once()
            warning = debug_log.warning.call_args.args[0]
            self.assertIn(str(root), warning)
            self.assertIn(str(leaf), warning)


class TestParserJSON(unittest.TestCase):
    @staticmethod
    def _write_timings(directory: Path, data) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "timings.json").write_text(json.dumps(data), encoding="utf-8")

    def test_loads_all_nested_benchmarks_and_enables_single_source_mode(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "patch1" / "group" / "model"
            second = root / "patch2" / "group" / "model"
            self._write_timings(first, [1, 2, 3, 4])
            self._write_timings(second, [5, 6])

            with patch("builtins.print"):
                parser = parser_JSON(root, None, ["real_time"])

            self.assertEqual(
                parser.getStats(),
                {
                    "model|patch1/group": {"real_time": [3, 4]},
                    "model|patch2/group": {"real_time": [5, 6]},
                },
            )
            self.assertEqual(parser.getAltDelimiter(), "|")

    def test_loads_direct_json_file_and_uses_two_source_mode(self):
        with TemporaryDirectory() as temp_dir:
            result_dir = Path(temp_dir) / "model"
            result_dir.mkdir()
            json_path = result_dir / "results.json"
            json_path.write_text("[10, 11]", encoding="utf-8")

            parser = parser_JSON(json_path, None, ["real_time"])

            self.assertEqual(parser.getStats(), {"model": {"real_time": [10, 11]}})
            self.assertIsNone(parser.getAltDelimiter())


if __name__ == "__main__":
    unittest.main()
