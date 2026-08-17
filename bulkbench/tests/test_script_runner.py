import signal
import subprocess
import sys
import unittest

from bulkbench.script_runner import (
    _TERMINATION_TIMEOUT_SECONDS,
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

    def test_terminates_script_when_parent_wait_is_interrupted(self):
        process = Mock()
        process.wait.side_effect = [KeyboardInterrupt, 0]
        process.poll.return_value = None

        with (
            patch("bulkbench.script_runner.subprocess.Popen", return_value=process),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_with_script(["command"])

        process.terminate.assert_called_once_with()
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
