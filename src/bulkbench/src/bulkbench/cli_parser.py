"""Command line interface of the `bulkbench` tool."""

import argparse

from benchstats.common import LoggingConsole

from .benchmark_plan_loader import DEFAULT_CONFIGS_FILE, DEFAULT_PATCHES_FILE, VALID_NAME_PATTERN
from .bulkbench import (
    DEFAULT_BACKUP_SUBDIR,
    DEFAULT_CONSOLE_LOG_LEVEL,
    DEFAULT_REPORT_SUBDIR,
    DEFAULT_RESULTS_SUBDIR,
)


# WARNING: argparse doesn't guarantee its private API stability. WTH?!
class BetterHelpFormatter(argparse.HelpFormatter):
    kMaxWidth = 100

    @staticmethod
    def _useWidth(width):
        return min(BetterHelpFormatter.kMaxWidth, width)

    def _fill_text(self, text, width, indent):
        width = self._useWidth(width)
        return "\n".join(
            indent + ("" if s == "%" else s)
            for line in text.splitlines()
            for s in argparse.HelpFormatter._split_lines(self, line or "%", width)
        )

    def _split_lines(self, text, width):
        width = self._useWidth(width)
        return [
            "" if s == "%" else s
            for line in text.splitlines()
            for s in argparse.HelpFormatter._split_lines(self, line or "%", width)
        ]


def makeParser() -> argparse.ArgumentParser:
    """Makes a parser of the `bulkbench` command line arguments. The parser only collects
    the values, `BulkBench` validates them."""
    parser = argparse.ArgumentParser(
        prog="bulkbench",
        description="Runs a set of benchmarks and analyzes their results statistically.",
        # formatter_class=argparse.RawTextHelpFormatter,
        formatter_class=BetterHelpFormatter,
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
        "--project_dir",
        default=None,
        help="Path to an existing directory describing a benchmark project. "
        "Defaults to the current working directory.",
    )
    parser.add_argument(
        "--configs_file",
        default=DEFAULT_CONFIGS_FILE,
        help="Override yaml file describing benchmark configs to execute. Relative paths "
        "are resolved under --project_dir. The file must exist.\n"
        "The file must be a valid YAML file containing a list of objects describing benchmark "
        "config groups with attributes:\n"
        "- name (required) - name of the benchmark config group (must be unique within the file, "
        f"match `{VALID_NAME_PATTERN}` after stripping, and not be `.` `..`, or starting with an "
        "`eager_` literal). "
        "It's recommended to keep the name short whenever the group contain only unique configs "
        "not used in other groups.\n"
        "- configs (required) - a non empty list of strings naming benchmark configs to execute "
        "(these are passed as `--name` argument to the `/app/.ci/run.py` script),\n"
        "- override_args (optional) - an optional key-value object to override specific settings "
        "for all the configs in the group, such as setting `num_iterations: <number>` or similar.\n"
        "- enabled (optional) - a boolean flag indicating whether the group should be used. "
        "Valid values are unquoted YAML `true`/`false`, standard aliases "
        "(`yes`/`no` and `on`/`off`), integers 1/0, quoted values "
        '"true", "false", "1", and "0". Defaults to `true`. Disabled groups are omitted; '
        "their other attributes aren't validated.\n"
        "- only_in_patches (optional) - an optional list of patch names, that when "
        "present, enables execution of the group configs only in these patch sets. "
        "The list can also contain patch set names not present in the --patches_file (these are "
        "ignored with a warning). If the list is present but empty, it disables the group completely. "
        "Absence of the field is an equivalent to 'run on all patch sets'.\n"
        "- eager_in_patches (optional) - an optional list of patch names, that when present, "
        "additionally enables execution of the group configs in eager mode in these patch sets "
        "Empty list (after stripping unknown patch set names) is an equivalent of "
        "absence of the field. "
        "Internally (which affects --results_dir layout) it creates an additional config group "
        "named `eager_<group_name>` inheriting `configs` from this group. "
        "Eager group's `only_in_patches` is set to an intersection of the parent's `only_in_patches` "
        "and `eager_in_patches` lists (it's an error if the intersection is empty). "
        "Parent group's `override_args` are copied and their `num_iterations` "
        "are set to 1, and `use_torch_compile` is set to `false`.\nEager groups performance results "
        "are ignored by the statistical analysis.",
    )
    parser.add_argument(
        "--patches_file",
        default=DEFAULT_PATCHES_FILE,
        help="Override yaml file describing which code needs to be patched for each set of "
        "benchmark runs. Relative paths are resolved under --project_dir. The file must exist.\n"
        "Every enabled patch must pass `patch --batch --dry-run` validation.\n"
        "The file must be a valid YAML file containing a non-empty list of patch sets. "
        "A patch set object has the following required attributes:\n"
        "- name - name of the patch set (must be unique within the file, "
        f"match `{VALID_NAME_PATTERN}` after stripping, and not be `.` or `..`),\n"
        "- patches - a list of patch objects, each describing a patch to apply to a single file. "
        "Patch lists must be unique regardless of object order; only one empty baseline is allowed. "
        "A patch object may occur only once in its set. Each patch object has the following attributes:\n"
        "- patch (required) - a path to a file containing the patch to apply. Relative paths are "
        "resolved under --project_dir/<patch-set name>; absolute paths are used as-is.\n"
        "    The file must be generated with `diff -u original_file modified_file > changes.patch` "
        "command or similar. Using a patch file that modifies several files is UB.\n"
        "- target (required) - a path to a file to apply the patch to. Relative paths are resolved under "
        "the `/app` directory; absolute paths are used as-is. Both patch and target files must exist. "
        "Applying several patches to the same target file is UB.\n"
        "- enabled (optional) - a boolean flag indicating whether the patch should be applied. "
        "Valid values are unquoted YAML `true`/`false`, standard aliases "
        "(`yes`/`no` and `on`/`off`), integers 1/0, quoted values "
        '"true", "false", "1", and "0". Defaults to `true`. Disabled patches are omitted; '
        "their other attributes aren't validated.\n",
    )
    parser.add_argument(
        "--backup_dir",
        default=DEFAULT_BACKUP_SUBDIR,
        help="Override the directory used to back up target files subjected to patches. Relative paths "
        "are resolved under --project_dir. The directory must either not exist, or be empty, "
        "and must not overlap with --results_dir or --report_dir.",
    )
    parser.add_argument(
        "--results_dir",
        default=DEFAULT_RESULTS_SUBDIR,
        help="Override a directory to store benchmarking results in. Relative paths are "
        "resolved under --project_dir.\n"
        "The directory may contain results from previous "
        "runs, they will be overwritten if their paths coincide with new runs, or they "
        "will be used for the statistical analysis.\n"
        "The directory has the following nested structure:\n"
        "<code_patch>/<benchmark_group>/<benchmark_config>/<result_files>\n"
        "where:\n"
        "- <code_patch> is the name of respective code patch (patch directory name in --project_dir),\n"
        "- <benchmark_group> and <benchmark_config> are the names of respective benchmark "
        "group and config from the --configs_file,\n"
        "- <result_files> are the files containing the results of the benchmark run, suchs "
        "as generated images/videos and timings.json file used for statistics analysis.",
    )
    parser.add_argument(
        "-r",
        "--regenerate_results",
        action="store_true",
        help="Regenerate all config results, including results that might already be successful. "
        "By default, a config is skipped when its result directory directly contains "
        "timings.json and at least one .jpg, .mp4, or .png file.",
    )
    parser.add_argument(
        "--report_dir",
        default=DEFAULT_REPORT_SUBDIR,
        help="Override a directory to store the report in. Relative paths are resolved "
        "under --project_dir.",
    )
    parser.add_argument(
        "--arch",
        default=None,
        help="String identifying the GPU architecture. More specifically a value for a single "
        "--tag argument of the `/app/.ci/run.py` script to filter benchmark configs. By default "
        "tries to get value from `rocminfo`. Passing empty string disables `--tag` use.",
    )
    return parser
