"""Run a command in a recorded pseudo-terminal."""

import math
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
    if not timing.is_file():
        raise ValueError(f"script timing '{timing}' doesn't exist or isn't a file")
    if not transcript.is_file():
        raise ValueError(f"script transcript '{transcript}' doesn't exist or isn't a file")

    try:
        timing_lines = timing.read_text(encoding="ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("malformed script timing data: file is not ASCII") from exc

    output_size = 0
    for line_number, line in enumerate(timing_lines, start=1):
        fields = line.split()
        if len(fields) != 2:
            raise ValueError(f"malformed script timing data at line {line_number}: {line!r}")
        try:
            elapsed = float(fields[0])
            byte_count = int(fields[1])
        except ValueError as exc:
            raise ValueError(
                f"malformed script timing data at line {line_number}: {line!r}"
            ) from exc
        if (
            not math.isfinite(elapsed)
            or elapsed < 0
            or not fields[1].isascii()
            or not fields[1].isdigit()
            or byte_count < 0
        ):
            raise ValueError(f"malformed script timing data at line {line_number}: {line!r}")
        output_size += byte_count

    transcript_data = transcript.read_bytes()
    metadata_end = transcript_data.find(b"\n")
    if metadata_end < 0:
        raise ValueError("script transcript is missing its metadata header")
    output_start = metadata_end + 1
    output_end = output_start + output_size
    if output_end > len(transcript_data):
        raise ValueError(
            f"script transcript contains fewer than the recorded {output_size} output bytes"
        )
    trailer = transcript_data[output_end:]
    if not trailer.startswith(b"\n") or not trailer.endswith(b"]\n"):
        raise ValueError("script transcript is missing or has malformed trailing metadata")

    output_data = transcript_data[output_start:output_end]
    return output_data.decode("utf-8", errors="replace").replace("\r\n", "\n")


def run_with_script(
    args: Sequence[str], *, cwd: StrPath | None = None, ignore_output: bool = False
) -> ScriptRunResult:
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
            "--logging-format",
            "classic",
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

        output = _read_output(transcript, timing) if not ignore_output else None
        return ScriptRunResult(args=command, returncode=returncode, output=output)
