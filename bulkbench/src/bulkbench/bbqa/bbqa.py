"""Implementation of the `bbqa` tool."""

import argparse
from typing import Any

from benchstats.common import LoggingConsole
from bulkbench.bulkbench import _validatedConsole


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


    def run(self) -> int: ...
