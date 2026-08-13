"""Implementation of the `bulkbench` tool."""

import os
from typing import Any
from pathlib import Path

DEFAULT_RESULTS_SUBDIR = "results"
DEFAULT_REPORT_SUBDIR = "report"
DEFAULT_CONFIGS_FILE = "configs"

StrPath = str | os.PathLike[str]


def ensureDirExists(path: Path) -> Path:
    """Creates the directory `path` with all of its parents unless it already exists and
    returns `path`. Call it just before the first write into `path`."""
    path.mkdir(parents=True, exist_ok=True)
    return path


class BulkBench:
    """Runs a set of benchmarks of a project and analyzes their results statistically.

    Validates the arguments it's given, so it's equally safe to use from the `bulkbench`
    CLI and from other Python programs. Raises `ValueError` on an invalid argument.
    """

    def __init__(self, *args, **kwargs) -> None:
        """`args` and `kwargs` are used to initialize the object.
        `kwargs` takes precedence over `args`.
        `args` is any object with the `project_dir`, `configs_file`, `results_dir` and
        `report_dir` named attributes. A missing attribute is treated as `None`, i.e. the
        default value of the corresponding argument is used."""
        assert len(args) <= 1, "Only one positional argument is allowed"
        args = args[0] if args else {}  # type: ignore

        def _get_arg(name: str) -> Any:
            return kwargs.get(name, getattr(args, name, None))

        self.project_dir: Path = self._validatedProjectDir(_get_arg("project_dir"))
        self.configs: list[str] = self._readConfigs(_get_arg("configs_file"))
        self.results_dir: Path = self._validatedOutputDir(
            _get_arg("results_dir"), DEFAULT_RESULTS_SUBDIR, "results_dir"
        )
        self.report_dir: Path = self._validatedOutputDir(
            _get_arg("report_dir"), DEFAULT_REPORT_SUBDIR, "report_dir"
        )

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
        non-existing, or to an existing but empty directory. The directory isn't created
        here, see `ensureDirExists()`."""
        path = self._resolvedPath(value, default_subdir)
        if path.exists():
            if not path.is_dir():
                raise ValueError(f"{arg_name} '{path}' exists and isn't a directory")
            if any(path.iterdir()):
                raise ValueError(f"{arg_name} directory '{path}' isn't empty")
        return path

    def _readConfigs(self, configs_file_value: StrPath | None) -> list[str]:
        """Reads the configs file and returns a list of config names."""
        configs_file = self._validatedConfigsFile(configs_file_value)
        with open(configs_file, "r") as f:
            return [ln for line in f if (ln := line.strip()) and not ln.startswith("#")]

    def run(self) -> int:
        """Executes the whole benchmarking pipeline. Returns the process exit code."""
        print(f"Running benchmarks for configs: {self.configs}")
        return 0
