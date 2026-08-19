"""Command line interface of the `bbqa` tool."""

import argparse
from benchstats.common import LoggingConsole

from .bbqa import DEFAULT_CONSOLE_LOG_LEVEL


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="bbqa",
        description="Implements some quality assessment of bulkbench results.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--console_log_level",
        default=DEFAULT_CONSOLE_LOG_LEVEL.value,
        choices=[level.value for level in LoggingConsole.LogLevel],
        type=int,
        help="Set the logging level for the console output verbosity (as an integer).\nValid values are: "
        + ", ".join([f"{level.value} ({level.name})" for level in LoggingConsole.LogLevel])
        + ".\nDefaults to `%(default)s`.",
    )

    parser.add_argument(
        "results_dir",
        help="Directory with xDiT outputs, typically created by a `bulkbench` tool under its "
        "--results_dir argument (i.e. at "
        "least 2 levels deep with the deepest directories containing the results of benchmark "
        "runs).",
    )

    return parser
