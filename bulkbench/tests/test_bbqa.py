import math
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from bulkbench.bbqa.bbqa import BBQA
from bulkbench.bbqa.metric_psnr import _compute_psnr_images, _compute_psnr_videos


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


class TestPSNRMetric(unittest.TestCase):
    def test_computes_image_psnr_with_scikit_image(self):
        reference = Mock(shape=(16, 16, 3))
        distorted = Mock(shape=(16, 16, 3))
        logger = Mock()

        with (
            patch(
                "bulkbench.bbqa.metric_psnr.imread",
                side_effect=(reference, distorted),
            ) as imread,
            patch(
                "bulkbench.bbqa.metric_psnr.peak_signal_noise_ratio",
                return_value=31.25,
            ) as compute_psnr,
        ):
            result = _compute_psnr_images(Path("reference"), Path("distorted"), logger)

        self.assertEqual(result, 31.25)
        self.assertEqual(imread.call_count, 2)
        compute_psnr.assert_called_once_with(reference, distorted)

    def test_returns_nan_and_logs_mismatched_image_shapes(self):
        logger = Mock()
        with (
            patch(
                "bulkbench.bbqa.metric_psnr.imread",
                side_effect=(Mock(shape=(16, 16, 3)), Mock(shape=(8, 16, 3))),
            ),
            patch("bulkbench.bbqa.metric_psnr.peak_signal_noise_ratio") as compute_psnr,
        ):
            result = _compute_psnr_images(Path("reference"), Path("distorted"), logger)

        self.assertTrue(math.isnan(result))
        logger.error.assert_called_once()
        compute_psnr.assert_not_called()

    def test_parses_overall_video_psnr(self):
        probe_output = (
            '{"streams": [{"width": 16, "height": 16, "nb_read_frames": "4"}]}'
        )
        logger = Mock()
        with patch(
            "bulkbench.bbqa.metric_psnr.subprocess.run",
            side_effect=(
                Mock(stdout=probe_output),
                Mock(stdout=probe_output),
                Mock(stderr="PSNR y:30.0 average:28.125 min:27.0 max:29.0"),
            ),
        ) as run:
            result = _compute_psnr_videos(Path("reference"), Path("distorted"), logger)

        self.assertEqual(result, 28.125)
        self.assertEqual(run.call_count, 3)

    def test_returns_nan_and_logs_mismatched_video_frame_counts(self):
        logger = Mock()
        with patch(
            "bulkbench.bbqa.metric_psnr.subprocess.run",
            side_effect=(
                Mock(
                    stdout='{"streams": [{"width": 16, "height": 16, "nb_read_frames": "4"}]}'
                ),
                Mock(
                    stdout='{"streams": [{"width": 16, "height": 16, "nb_read_frames": "3"}]}'
                ),
            ),
        ) as run:
            result = _compute_psnr_videos(Path("reference"), Path("distorted"), logger)

        self.assertTrue(math.isnan(result))
        logger.error.assert_called_once()
        self.assertEqual(run.call_count, 2)

    def test_returns_nan_and_logs_video_processing_failure(self):
        logger = Mock()
        with patch(
            "bulkbench.bbqa.metric_psnr.subprocess.run",
            side_effect=FileNotFoundError("ffprobe is unavailable"),
        ):
            result = _compute_psnr_videos(Path("reference"), Path("distorted"), logger)

        self.assertTrue(math.isnan(result))
        logger.error.assert_called_once()
