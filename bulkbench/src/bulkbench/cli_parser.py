"""Command line interface of the `bulkbench` tool."""

import argparse

from .bulkbench import (
    DEFAULT_CONFIGS_FILE,
    DEFAULT_REPORT_SUBDIR,
    DEFAULT_RESULTS_SUBDIR,
)


def makeParser() -> argparse.ArgumentParser:
    """Makes a parser of the `bulkbench` command line arguments. The parser only collects
    the values, `BulkBench` validates them."""
    parser = argparse.ArgumentParser(
        prog="bulkbench",
        description="Runs a set of benchmarks and analyzes their results statistically.",
    )
    parser.add_argument(
        "--project_dir",
        default=None,
        help="Path to an existing directory with the project description to benchmark. "
        "Defaults to the current working directory.",
    )
    parser.add_argument(
        "--configs_file",
        default=DEFAULT_CONFIGS_FILE,
        help="Override a file enumerating benchmark_configs to execute. If it's a "
        "relative path, it's relative to --project_dir. The file must exist.",
    )
    parser.add_argument(
        "--results_dir",
        default=DEFAULT_RESULTS_SUBDIR,
        help="Override a directory to store benchmarking results in. If it's a relative "
        "path, it's relative to --project_dir. The directory must either not exist, or "
        "be empty.",
    )
    parser.add_argument(
        "--report_dir",
        default=DEFAULT_REPORT_SUBDIR,
        help="Override a directory to store the report in. If it's a relative path, it's "
        "relative to --project_dir. The directory must either not exist, or be empty.",
    )
    return parser
