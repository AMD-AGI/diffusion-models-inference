"""Implementation of the `bbqa` tool."""

from pathlib import Path
from typing import Any

from benchstats.common import LoggingConsole

from ..bulkbench import StrPath, _validatedConsole, configMightHaveRunSuccessfully
from .metric_psnr import metric_psnr

DEFAULT_CONSOLE_LOG_LEVEL = LoggingConsole.LogLevel.Info

# the first item is the default
SUPPORTED_METRICS: dict = {
    "psnr": "Computes and display pair-wise PSNR values between images and videos in the results "
    "directory. Images and videos from a benchmark group directory starting with `eager_` prefix "
    "are used as the reference images and videos, respectively.\n"
    "The metric supports a single argument, corresponding to a `filter` argument of "
    "bulkbench.parser_JSON.parser_JSON::__init__ method (see its documentation for details)."
}


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

        self.metric: str = _get_arg("metric", next(iter(SUPPORTED_METRICS.keys())))
        assert self.metric in SUPPORTED_METRICS, "Invalid metric"

        self.args: list[str] = _get_arg("args", [])
        assert isinstance(self.args, list), "Arguments must be a list"
        assert all(isinstance(arg, str) for arg in self.args), "Arguments must be strings"

        self.results_dir: Path = self._validatedResultsDir(_get_arg("results_dir"))

    @staticmethod
    def _validatedResultsDir(value: StrPath|None) -> Path:
        """Resolves `value` and makes sure it contains a successful benchmark run."""
        if value is None:
            return Path.cwd()
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"results_dir '{path}' doesn't exist or isn't a directory")
        for directory in path.rglob("*"):
            if directory.is_dir() and configMightHaveRunSuccessfully(directory):
                return path
        raise ValueError(
            f"results_dir '{path}' doesn't contain a subdirectory with a successful benchmark run"
        )

    def run(self) -> int:
        self.Con.debug(f"Running metric: {self.metric}")
        if self.metric == "psnr":
            return metric_psnr(self.Con, self.results_dir, self.args)
        else:
            raise ValueError(f"Invalid metric: {self.metric}")
