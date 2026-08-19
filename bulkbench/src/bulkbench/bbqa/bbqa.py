"""Implementation of the `bbqa` tool."""

from pathlib import Path
from typing import Any

from benchstats.common import LoggingConsole
from bulkbench.bulkbench import StrPath, _validatedConsole, configMightHaveRunSuccessfully


DEFAULT_CONSOLE_LOG_LEVEL = LoggingConsole.LogLevel.Info


class BBQA:
    """Quality assessment of bulkbench results."""

    def __init__(self, *args, **kwargs) -> None:
        """`args` and `kwargs` are used to initialize the object. `args` if set, must
        be a single object containing attributes obtainable by the CLI parser. `kwargs`
        takes precedence over `args`.

        A missing attribute is treated as `None`, i.e. the default value of the
        corresponding argument is used.
        """
        assert len(args) <= 1, "Only one positional argument is allowed"
        args = args[0] if args else {}  # type: ignore

        def _get_arg(name: str, default: Any = None) -> Any:
            return kwargs.get(name, getattr(args, name, default))

        self.Con = _validatedConsole(_get_arg("console"), _get_arg("console_log_level"))
        self.Con.trace(f"Console log level: {self.Con.log_level}")

        self.results_dir: Path = self._validatedResultsDir(_get_arg("results_dir"))

    @staticmethod
    def _validatedResultsDir(value: StrPath) -> Path:
        """Resolves `value` and makes sure it contains a successful benchmark run."""
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"results_dir '{path}' doesn't exist or isn't a directory")
        for directory in path.rglob("*"):
            if directory.is_dir() and configMightHaveRunSuccessfully(directory):
                return path
        raise ValueError(
            f"results_dir '{path}' doesn't contain a subdirectory with a successful benchmark run"
        )


    def run(self) -> int: ...
