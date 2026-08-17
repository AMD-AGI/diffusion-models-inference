"""Run a command in a recorded pseudo-terminal."""

import os
import shlex
import subprocess

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

_SCRIPT_EXECUTABLE = "script"
_TERMINATION_TIMEOUT_SECONDS = 5

StrPath = str | os.PathLike[str]


@dataclass(frozen=True)
class ScriptRunResult:
    """Result of a command recorded through util-linux `script`."""

    args: tuple[str, ...]
    returncode: int
    output: str


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Terminates `script` and gives it time to clean up its PTY child."""
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=_TERMINATION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _read_output(transcript: Path, timing: Path) -> str:
    """Reads child output without `script`'s transcript metadata."""
    if not transcript.is_file():
        return ""

    output_size = 0
    if timing.is_file():
        for line in timing.read_text(encoding="utf-8", errors="replace").splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[1].isdigit():
                output_size += int(fields[1])

    transcript_data = transcript.read_bytes()
    metadata_end = transcript_data.find(b"\n")
    output_start = metadata_end + 1 if metadata_end >= 0 else 0
    output_data = transcript_data[output_start : output_start + output_size]
    return output_data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def run_with_script(args: Sequence[str], *, cwd: StrPath | None = None) -> ScriptRunResult:
    """Runs a command in a PTY, mirrors it live, and captures its combined output."""
    command = tuple(args)
    if not command:
        raise ValueError("args must contain at least one item")
    if not all(isinstance(arg, str) for arg in command):
        raise TypeError("every args item must be a string")

    with TemporaryDirectory(prefix="bulkbench-script-") as temp_dir:
        transcript = Path(temp_dir) / "output.log"
        timing = Path(temp_dir) / "timing.log"
        script_args = [
            _SCRIPT_EXECUTABLE,
            "--quiet",
            "--return",
            "--flush",
            "--log-out",
            str(transcript),
            "--log-timing",
            str(timing),
            "--command",
            f"exec {shlex.join(command)}",
        ]
        process = subprocess.Popen(
            script_args,
            cwd=os.fspath(cwd) if cwd is not None else None,
            shell=False,
        )
        try:
            returncode = process.wait()
        except BaseException:
            _terminate(process)
            raise

        output = _read_output(transcript, timing)
        return ScriptRunResult(args=command, returncode=returncode, output=output)
