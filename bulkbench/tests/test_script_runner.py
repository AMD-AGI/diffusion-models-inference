import signal
import subprocess
import sys
import unittest

from bulkbench.script_runner import (
    _TERMINATION_TIMEOUT_SECONDS,
    _read_output,
    _terminate,
    run_with_script,
)
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch


class TestScriptRunner(unittest.TestCase):
    def test_mirrors_output_to_the_current_console(self):
        helper = (
            "import sys\n"
            "from bulkbench.script_runner import run_with_script\n"
            "run_with_script([sys.executable, '-c', "
            "\"print('live output', flush=True)\"])\n"
        )

        completed = subprocess.run(
            [sys.executable, "-c", helper],
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "live output\n")
        self.assertEqual(completed.stderr, "")

    def test_runs_in_pty_and_captures_ordered_combined_output(self):
        code = (
            "import os, sys\n"
            "print(f'tty={os.isatty(1)}', flush=True)\n"
            "print('stdout-1', flush=True)\n"
            "print('stderr-1', file=sys.stderr, flush=True)\n"
            "print('stdout-2', flush=True)\n"
        )

        result = run_with_script([sys.executable, "-c", code])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.args, (sys.executable, "-c", code))
        self.assertEqual(
            result.output,
            "tty=True\nstdout-1\nstderr-1\nstdout-2\n",
        )

    def test_preserves_arguments_and_working_directory(self):
        with TemporaryDirectory() as cwd:
            argument = "value with spaces; $(not-a-command)"
            code = (
                "import os, sys\n"
                "print(os.getcwd(), flush=True)\n"
                "print(sys.argv[1], flush=True)\n"
            )

            result = run_with_script(
                [sys.executable, "-c", code, argument],
                cwd=cwd,
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(
                result.output,
                f"{Path(cwd).resolve()}\n{argument}\n",
            )

    def test_returns_signal_exit_status(self):
        code = (
            "import os, signal\n"
            "print('before signal', flush=True)\n"
            "os.kill(os.getpid(), signal.SIGTERM)\n"
        )

        result = run_with_script([sys.executable, "-c", code])

        self.assertEqual(result.returncode, 128 + signal.SIGTERM)
        self.assertEqual(result.output, "before signal\n")

    def test_existing_empty_timing_data_means_empty_output(self):
        result = run_with_script([sys.executable, "-c", ""])

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.output, "")

    def test_rejects_malformed_timing_data(self):
        malformed_values = (
            "invalid\n",
            "nan 1\n",
            "0.1 -1\n",
            "0.1 invalid\n",
            "0.1 1 extra\n",
        )
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            transcript = temp_path / "output.log"
            timing = temp_path / "timing.log"
            transcript.write_bytes(b"metadata\nx\ntrailer]\n")

            for value in malformed_values:
                with self.subTest(value=value):
                    timing.write_text(value, encoding="ascii")
                    with self.assertRaisesRegex(ValueError, "malformed script timing data"):
                        _read_output(transcript, timing)
            timing.write_bytes(b"\xff")
            with self.assertRaisesRegex(ValueError, "malformed script timing data"):
                _read_output(transcript, timing)

    def test_rejects_malformed_transcript(self):
        cases = (
            (None, "doesn't exist or isn't a file"),
            (b"metadata without newline", "missing its metadata header"),
            (b"metadata\nx", "fewer than the recorded"),
            (b"metadata\nxyzbad trailer", "malformed trailing metadata"),
        )
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            transcript = temp_path / "output.log"
            timing = temp_path / "timing.log"
            timing.write_text("0.1 3\n", encoding="ascii")

            for transcript_data, expected_message in cases:
                with self.subTest(expected_message=expected_message):
                    transcript.unlink(missing_ok=True)
                    if transcript_data is not None:
                        transcript.write_bytes(transcript_data)
                    with self.assertRaisesRegex(ValueError, expected_message):
                        _read_output(transcript, timing)

    def test_removes_intermediate_files_after_success(self):
        with TemporaryDirectory() as parent_dir:
            created_paths = []

            def tracked_temporary_directory(*args, **kwargs):
                temporary_directory = TemporaryDirectory(
                    *args,
                    dir=parent_dir,
                    **kwargs,
                )
                created_paths.append(Path(temporary_directory.name))
                return temporary_directory

            with patch(
                "bulkbench.script_runner.TemporaryDirectory",
                side_effect=tracked_temporary_directory,
            ):
                result = run_with_script([sys.executable, "-c", "print('output')"])

            self.assertEqual(result.output, "output\n")
            self.assertTrue(created_paths)
            self.assertTrue(all(not path.exists() for path in created_paths))
            self.assertEqual(list(Path(parent_dir).iterdir()), [])

    def test_terminates_script_when_parent_wait_is_interrupted(self):
        process = Mock()
        process.wait.side_effect = [KeyboardInterrupt, 0]
        process.poll.return_value = None

        with TemporaryDirectory() as parent_dir:
            created_paths = []

            def tracked_temporary_directory(*args, **kwargs):
                temporary_directory = TemporaryDirectory(
                    *args,
                    dir=parent_dir,
                    **kwargs,
                )
                created_paths.append(Path(temporary_directory.name))
                return temporary_directory

            with (
                patch(
                    "bulkbench.script_runner.subprocess.Popen",
                    return_value=process,
                ) as process_manager,
                patch(
                    "bulkbench.script_runner.TemporaryDirectory",
                    side_effect=tracked_temporary_directory,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                run_with_script(["command"])

            self.assertTrue(created_paths)
            self.assertTrue(all(not path.exists() for path in created_paths))
            self.assertEqual(list(Path(parent_dir).iterdir()), [])

        process.terminate.assert_called_once_with()
        script_args = process_manager.call_args.args[0]
        self.assertIn("--logging-format", script_args)
        self.assertEqual(
            script_args[script_args.index("--logging-format") + 1],
            "classic",
        )
        self.assertEqual(
            process.wait.call_args_list[1].kwargs,
            {"timeout": _TERMINATION_TIMEOUT_SECONDS},
        )
        process.kill.assert_not_called()

    def test_kills_script_if_graceful_termination_times_out(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired(["script"], _TERMINATION_TIMEOUT_SECONDS),
            0,
        ]

        _terminate(process)

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)


if __name__ == "__main__":
    sys.exit(unittest.main())
