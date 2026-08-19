import unittest

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bulkbench.bbqa.bbqa import BBQA


class TestBBQAResultsDir(unittest.TestCase):
    @staticmethod
    def _create_successful_run(directory: Path) -> None:
        directory.mkdir(parents=True)
        (directory / "timings.json").touch()
        (directory / "result.png").touch()

    def test_accepts_directory_with_successful_run_nested_at_arbitrary_depth(self):
        with TemporaryDirectory() as temp_dir:
            results_dir = Path(temp_dir)
            self._create_successful_run(results_dir / "patch" / "group" / "config")

            bbqa = BBQA(results_dir=results_dir)

            self.assertEqual(bbqa.results_dir, results_dir.resolve())

    def test_stops_searching_after_first_successful_run(self):
        with TemporaryDirectory() as temp_dir:
            results_dir = Path(temp_dir)
            first = results_dir / "first"
            second = results_dir / "second"
            first.mkdir()
            second.mkdir()

            with (
                patch.object(Path, "rglob", return_value=iter((first, second))),
                patch(
                    "bulkbench.bbqa.bbqa.configMightHaveRunSuccessfully",
                    return_value=True,
                ) as config_succeeded,
            ):
                BBQA(results_dir=results_dir)

            config_succeeded.assert_called_once_with(first)

    def test_rejects_nonexistent_directory(self):
        with TemporaryDirectory() as temp_dir:
            results_dir = Path(temp_dir) / "missing"

            with self.assertRaisesRegex(
                ValueError,
                rf"^results_dir '{results_dir}' doesn't exist or isn't a directory$",
            ):
                BBQA(results_dir=results_dir)

    def test_rejects_file(self):
        with TemporaryDirectory() as temp_dir:
            results_file = Path(temp_dir) / "results"
            results_file.touch()

            with self.assertRaisesRegex(
                ValueError,
                rf"^results_dir '{results_file}' doesn't exist or isn't a directory$",
            ):
                BBQA(results_dir=results_file)

    def test_rejects_directory_without_successful_run(self):
        with TemporaryDirectory() as temp_dir:
            results_dir = Path(temp_dir)
            incomplete_run = results_dir / "incomplete"
            incomplete_run.mkdir()
            (incomplete_run / "timings.json").touch()

            with self.assertRaisesRegex(
                ValueError,
                rf"^results_dir '{results_dir}' doesn't contain a subdirectory "
                r"with a successful benchmark run$",
            ):
                BBQA(results_dir=results_dir)

    def test_does_not_treat_results_directory_itself_as_a_run(self):
        with TemporaryDirectory() as temp_dir:
            results_dir = Path(temp_dir)
            (results_dir / "timings.json").touch()
            (results_dir / "result.png").touch()

            with self.assertRaisesRegex(
                ValueError,
                rf"^results_dir '{results_dir}' doesn't contain a subdirectory "
                r"with a successful benchmark run$",
            ):
                BBQA(results_dir=results_dir)
