"""Implementation of the `bulkbench` tool."""

import os
import subprocess

from benchstats.common import LoggingConsole
from pathlib import Path
from typing import Any

DEFAULT_RESULTS_SUBDIR = "results"
DEFAULT_REPORT_SUBDIR = "report"
DEFAULT_CONFIGS_FILE = "configs.yaml"

StrPath = str | os.PathLike[str]


_gArch = None


def get_amd_gpu_arch_rocminfo():
    global _gArch
    if _gArch is not None:
        return _gArch

    try:
        output = subprocess.check_output(["rocminfo"], text=True)
        for line in output.splitlines():
            if "Name:" in line and "gfx" in line:
                # Extract the architecture string (e.g., gfx950)
                _gArch = line.split()[-1].strip()
                break
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    if _gArch is None:
        raise ValueError("Failed to get AMD GPU architecture from rocminfo")
    return _gArch


class BulkBench:
    """Runs a set of benchmarks of a project and analyzes their results statistically.

    Validates the arguments it's given, so it's equally safe to use from the `bulkbench`
    CLI and from other Python programs. Raises `ValueError` on an invalid argument.
    """

    def __init__(self, *args, **kwargs) -> None:
        """`args` and `kwargs` are used to initialize the object. `args` if set, must
        be a single object containing attributes obtainable by the CLI parser. `kwargs`
        takes precedence over `args`.

        A missing attribute is treated as `None`, i.e. the default value of the
        corresponding argument is used.
        """

        assert len(args) <= 1, "Only one positional argument is allowed"
        args = args[0] if args else {}  # type: ignore

        def _get_arg(name: str) -> Any:
            return kwargs.get(name, getattr(args, name, None))

        Con = _get_arg("console")
        if Con is None:
            Con = LoggingConsole()
        assert isinstance(Con, LoggingConsole), "console must be a LoggingConsole"
        self.Con = Con

        self.arch: str = _get_arg("arch")
        if self.arch is None:
            self.arch = get_amd_gpu_arch_rocminfo()
        assert isinstance(self.arch, str), "arch must be a string"
        self.Con.debug(
            f"Using arch tag = {self.arch}" if self.arch else "Arch is not set, tags won't be used"
        )

        self.project_dir: Path = self._validatedProjectDir(_get_arg("project_dir"))
        self.configs: list[str] = self._readConfigs(_get_arg("configs_file"))
        self.results_dir: Path = self._validatedOutputDir(
            _get_arg("results_dir"), DEFAULT_RESULTS_SUBDIR, "results_dir"
        )

        self.report_dir: Path = self._validatedOutputDir(
            _get_arg("report_dir"), DEFAULT_REPORT_SUBDIR, "report_dir"
        )
        if any(self.report_dir.iterdir()):
            raise ValueError(f"--report_dir directory '{self.report_dir}' isn't empty")

    @staticmethod
    def _validatedProjectDir(value: StrPath | None) -> Path:
        """Resolves `value` (defaults to the current working directory) and makes sure it
        points to an existing directory."""
        path = (Path.cwd() if value is None else Path(value).expanduser()).resolve()
        if not path.is_dir():
            raise ValueError(f"project_dir '{path}' doesn't exist or isn't a directory")
        return path

    def _resolvedPath(self, value: StrPath | None, default: str) -> Path:
        """Turns `value` (or `default`, if `value` is `None`) into a resolved absolute
        path, taking a relative one relative to `self.project_dir`. Note that `.resolve()`
        can't be applied earlier, as it anchors a relative path to the current working
        directory."""
        path = Path(default if value is None else value).expanduser()
        return (path if path.is_absolute() else self.project_dir / path).resolve()

    def _validatedConfigsFile(self, value: StrPath | None) -> Path:
        """Resolves `value` (defaults to `DEFAULT_CONFIGS_FILE`) and makes sure it points
        to an existing file."""
        path = self._resolvedPath(value, DEFAULT_CONFIGS_FILE)
        if not path.is_file():
            raise ValueError(f"configs_file '{path}' doesn't exist or isn't a file")
        return path

    def _validatedOutputDir(
        self, value: StrPath | None, default_subdir: str, arg_name: str
    ) -> Path:
        """Resolves `value` (defaults to `default_subdir`) and makes sure it points to a
        non-existing, or to an existing but empty directory."""
        path = self._resolvedPath(value, default_subdir)
        if path.exists():
            if not path.is_dir():
                raise ValueError(f"--{arg_name} '{path}' exists and isn't a directory")
        else:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def _readConfigs(self, configs_file_value: StrPath | None) -> list[str]:
        """Reads the configs file and returns a list of config names."""
        configs_file = self._validatedConfigsFile(configs_file_value)
        with open(configs_file, "r") as f:
            configs = [ln for line in f if (ln := line.strip()) and not ln.startswith("#")]

        self.Con.debug(f"Read {len(configs)} configs from {configs_file}: {configs}")
        if not configs:
            raise ValueError(f"configs_file '{configs_file}' is empty")

        return configs

    def run(self) -> int:
        """Executes the whole benchmarking pipeline. Returns the process exit code."""

        return 0

    def _runAllConfigs(self):
        args = ["python", "/app/.ci/run.py"]
