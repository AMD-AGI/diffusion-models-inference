"""Implementation of the `bulkbench` tool."""

import os
import shutil
import subprocess
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, TypedDict

from benchstats.common import LoggingConsole

from .benchmark_plan_loader import (
    DEFAULT_CONFIGS_FILE as _PLAN_DEFAULT_CONFIGS_FILE,
)
from .benchmark_plan_loader import (
    EAGER_GROUP_PREFIX,
    BenchmarkPlanLoader,
    ConfigGroup,
    PatchData,
    PatchSet,
    StrPath,
    benchmarkConfigPath,
)
from .benchmark_plan_loader import (
    VALID_NAME_PATTERN as _PLAN_VALID_NAME_PATTERN,
)
from .script_runner import run_with_script

DEFAULT_CONSOLE_LOG_LEVEL = LoggingConsole.LogLevel.Info
DEFAULT_RESULTS_SUBDIR = "results"
DEFAULT_REPORT_SUBDIR = "report"
DEFAULT_BACKUP_SUBDIR = "_backups"
DEFAULT_CONFIGS_FILE = _PLAN_DEFAULT_CONFIGS_FILE
VALID_NAME_PATTERN = _PLAN_VALID_NAME_PATTERN

_APP_DIR = Path("/app")
_BENCHMARK_CONFIGS_DIR = _APP_DIR / ".ci" / "benchmark_configs"
_BENCHMARK_RUNNER = _APP_DIR / ".ci" / "run.py"
_RESULT_IMAGE_SUFFIXES = {".jpg", ".png"}
_RESULT_VIDEO_SUFFIXES = {".mp4"}
_RESULT_MEDIA_SUFFIXES = _RESULT_IMAGE_SUFFIXES | _RESULT_VIDEO_SUFFIXES


def configMightHaveRunSuccessfully(workdir: Path, config_name: str | None = None) -> bool:
    """Checks whether a config's direct result files indicate a successful prior run."""
    config_dir = workdir if config_name is None else workdir / config_name
    return (config_dir / "timings.json").is_file() and any(
        child.is_file() and child.suffix in _RESULT_MEDIA_SUFFIXES for child in config_dir.iterdir()
    )


class TargetBackup(TypedDict):
    """Files needed to restore one patch target."""

    backup: Path
    path_file: Path
    target: Path


@dataclass(frozen=True)
class GroupFailureCapture:
    """Captured outcome of one benchmark config-group process."""

    group_name: str
    configs_to_run: list[str]
    output: str
    returncode: int | None


class GroupRunError(RuntimeError):
    """Raised when a benchmark config-group process fails."""

    def __init__(self, result: GroupFailureCapture) -> None:
        self.result = result
        status = (
            f"exit status {result.returncode}"
            if result.returncode is not None
            else "process start failure"
        )
        super().__init__(f"benchmark config group {result.group_name!r} failed: {status}")


def _formatDuration(duration_seconds: float) -> str:
    """Formats a nonnegative duration as unbounded hours, minutes, and seconds."""
    total_tenths = int(duration_seconds * 10 + 0.5)
    hours, remaining_tenths = divmod(total_tenths, 60 * 60 * 10)
    minutes, remaining_tenths = divmod(remaining_tenths, 60 * 10)
    seconds = remaining_tenths / 10
    return f"{hours:02d}:{minutes:02d}:{seconds:04.1f}"


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


def _validatedConsole(Con: Any | None, console_log_level: int | None) -> LoggingConsole:
    if Con is None:
        Con = LoggingConsole(
            log_level=LoggingConsole.LogLevel(
                console_log_level
                if isinstance(console_log_level, int) and (0 <= console_log_level <= 5)
                else DEFAULT_CONSOLE_LOG_LEVEL.value
            )
        )
    else:
        assert isinstance(Con, LoggingConsole), "console must be a LoggingConsole"
    return Con


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

        def _get_arg(name: str, default: Any = None) -> Any:
            return kwargs.get(name, getattr(args, name, default))

        self.Con = _validatedConsole(_get_arg("console"), _get_arg("console_log_level"))
        self.Con.trace(f"Console log level: {self.Con.log_level}")

        self.regenerate_results = _get_arg("regenerate_results", False)
        assert isinstance(self.regenerate_results, bool), "regenerate_results must be a boolean"

        self.arch: str = _get_arg("arch")
        if self.arch is None:
            self.arch = get_amd_gpu_arch_rocminfo()
        assert isinstance(self.arch, str), "arch must be a string"
        self.Con.debug(
            f"Using arch tag = {self.arch}" if self.arch else "Arch is not set, tags won't be used"
        )

        self.successful_runs: dict[str, list[tuple[str, float, list[str]]]] = {}
        self.unsuccessful_runs: dict[str, list[tuple[GroupFailureCapture, float]]] = {}

        self.project_dir: Path = self._validatedProjectDir(_get_arg("project_dir"))
        plan_loader = BenchmarkPlanLoader(
            project_dir=self.project_dir,
            arch=self.arch,
            console=self.Con,
            benchmark_configs_dir=_BENCHMARK_CONFIGS_DIR,
        )
        patches_file_value = _get_arg("patches_file")
        patches_file, self.patches = plan_loader.readPatches(patches_file_value)
        # Validate applicability early to prevent failures during long-running work.
        for patch_set in self.patches:
            self._dryRunPatches(patch_set)
        self.Con.debug(
            f"Read {len(self.patches)} patch sets from {patches_file}:",
            self.patches,
        )
        self.configs: dict[str, ConfigGroup] = plan_loader.readConfigs(
            _get_arg("configs_file"),
            {patch_set["name"] for patch_set in self.patches},
        )
        self.results_dir: Path = self._validatedOutputDir(
            _get_arg("results_dir"), DEFAULT_RESULTS_SUBDIR, "results_dir"
        )
        if not self.results_dir.exists():
            self.results_dir.mkdir(parents=True, exist_ok=True)

        self.report_dir: Path = self._validatedOutputDir(
            _get_arg("report_dir"), DEFAULT_REPORT_SUBDIR, "report_dir"
        )  # create it on demand when needed

        self.backup_dir: Path = self._validatedOutputDir(
            _get_arg("backup_dir"), DEFAULT_BACKUP_SUBDIR, "backup_dir"
        )
        if not self.backup_dir.exists():
            self.backup_dir.mkdir(parents=True, exist_ok=True)
        if any(self.backup_dir.iterdir()):
            raise ValueError(f"--backup_dir directory '{self.backup_dir}' isn't empty")
        self._validateBackupDirIsDisjoint()

    def _Con_begin(self, level: LoggingConsole.LogLevel = LoggingConsole.LogLevel.Critical) -> None:
        if self.Con.will_log(level):
            self.Con.print("[bold bright_blue]==== BulkBench >>>>>>>>[/bold bright_blue]")

    def _Con_end(self, level: LoggingConsole.LogLevel = LoggingConsole.LogLevel.Critical) -> None:
        if self.Con.will_log(level):
            self.Con.print("[bold bright_blue]<<<<<<<< BulkBench ====[/bold bright_blue]")

    def _cleanBackupDir(self) -> None:
        if self.backup_dir.exists():
            if any(self.backup_dir.iterdir()):
                self.Con.warning(
                    f"--backup_dir '{self.backup_dir}' is not empty, will NOT delete it.\n"
                    "Inspect it manually and ensure all patched files are properly reverted."
                )
            else:
                self.Con.debug(f"Deleting --backup_dir '{self.backup_dir}'")
                try:
                    self.backup_dir.rmdir()
                except Exception as exc:  # ruff: ignore[blind-except]
                    self.Con.error(f"Failed to delete --backup_dir '{self.backup_dir}':", exc)

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

    def _validatedOutputDir(
        self, value: StrPath | None, default_subdir: str, arg_name: str
    ) -> Path:
        """Resolves `value` (defaults to `default_subdir`) and makes sure it points to a
        non-existing, or to an existing but empty directory."""
        path = self._resolvedPath(value, default_subdir)
        if path.exists() and not path.is_dir():
            raise ValueError(f"--{arg_name} '{path}' exists and isn't a directory")
        return path

    @staticmethod
    def _pathsOverlap(first: Path, second: Path) -> bool:
        return first == second or first in second.parents or second in first.parents

    def _validateBackupDirIsDisjoint(self) -> None:
        for arg_name, path in (
            ("results_dir", self.results_dir),
            ("report_dir", self.report_dir),
        ):
            if self._pathsOverlap(self.backup_dir, path):
                raise ValueError(
                    f"--backup_dir '{self.backup_dir}' must not overlap --{arg_name} '{path}'"
                )

    @staticmethod
    def _benchmarkConfigPath(config_name: str, config_context: str) -> Path:
        return benchmarkConfigPath(_BENCHMARK_CONFIGS_DIR, config_name, config_context)

    def _removeBackupArtifacts(self, backups: list[TargetBackup]) -> list[BaseException]:
        errors: list[BaseException] = []
        for target_backup in reversed(backups):
            try:
                # Keep the path metadata when deleting the backup data fails.
                target_backup["backup"].unlink(missing_ok=True)
                target_backup["path_file"].unlink(missing_ok=True)
            except BaseException as exc:  # noqa: BLE001 - continue all cleanup attempts
                errors.append(exc)
        return errors

    def _snapshotTargets(self, patch_set: PatchSet) -> list[TargetBackup]:
        """Copies every patch target to an indexed backup file before any mutation."""
        if any(self.backup_dir.iterdir()):
            raise RuntimeError(f"backup_dir '{self.backup_dir}' must be empty before snapshotting")

        backups: list[TargetBackup] = []
        try:
            for patch_index, patch_data in enumerate(patch_set["patches"]):
                backup = self.backup_dir / f"{patch_index:05d}"
                path_file = self.backup_dir / f"{patch_index:05d}.path"
                target_backup: TargetBackup = {
                    "backup": backup,
                    "path_file": path_file,
                    "target": patch_data["target"],
                }
                backups.append(target_backup)

                shutil.copy2(patch_data["target"], backup)
                path_file.write_text(str(patch_data["target"]), encoding="utf-8")
        except BaseException as primary_error:
            cleanup_errors = self._removeBackupArtifacts(backups)
            if cleanup_errors:
                raise BaseExceptionGroup(
                    "target snapshot and backup cleanup both failed",
                    [primary_error, *cleanup_errors],
                ) from None
            raise

        return backups

    def _restoreAllTargets(self, backups: list[TargetBackup]) -> list[BaseException]:
        """Restores all targets and returns failures after attempting every restoration."""
        self.Con.debug(f"Restoring all {len(backups)} targets from backups")
        restore_errors: list[BaseException] = []
        restored_backups: list[TargetBackup] = []

        for target_backup in reversed(backups):
            try:
                shutil.copy2(target_backup["backup"], target_backup["target"])
            except BaseException as exc:  # noqa: BLE001 - continue all restore attempts
                restore_errors.append(exc)
            else:
                restored_backups.append(target_backup)

        restore_errors.extend(self._removeBackupArtifacts(restored_backups))
        return restore_errors

    def _runPatchCommand(self, patch_set_name: str, patch_data: PatchData, dry_run: bool) -> None:
        phase = "dry-run" if dry_run else "application"
        command = ["patch", "--batch"]
        if dry_run:
            command.append("--dry-run")
        command.extend((str(patch_data["target"]), str(patch_data["patch"])))

        try:
            subprocess.run(
                command,
                capture_output=True,
                check=True,
                shell=False,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ValueError(
                f"patch {phase} failed for patch set '{patch_set_name!r}', "
                f"patch '{patch_data['patch']}', target '{patch_data['target']}', "
                f"exit status {exc.returncode}; stdout={exc.stdout!r}; stderr={exc.stderr!r}"
            ) from exc
        except OSError as exc:
            raise ValueError(
                f"patch {phase} failed for patch set '{patch_set_name!r}', "
                f"patch '{patch_data['patch']}', target '{patch_data['target']}': {exc}"
            ) from exc

    def _dryRunPatches(self, patch_set: PatchSet) -> None:
        """Checks that every patch can be applied before any target is backed up."""
        for patch_data in patch_set["patches"]:
            self._runPatchCommand(patch_set["name"], patch_data, dry_run=True)

    def _applyPatches(self, patch_set: PatchSet) -> None:
        """Applies every patch in a patch set in list order."""
        self.Con.debug(f"Applying patch set '{patch_set['name']}'")
        for patch_data in patch_set["patches"]:
            self._runPatchCommand(patch_set["name"], patch_data, dry_run=False)

    @contextmanager
    def _appliedPatchSet(self, patch_set: PatchSet) -> Iterator[None]:
        self._dryRunPatches(patch_set)
        backups = self._snapshotTargets(patch_set)
        try:
            self._applyPatches(patch_set)
            yield
        except BaseException as primary_error:
            restore_errors = self._restoreAllTargets(backups)
            if restore_errors:
                raise BaseExceptionGroup(
                    "patch-set execution and target restoration both failed",
                    [primary_error, *restore_errors],
                ) from None
            raise
        else:
            restore_errors = self._restoreAllTargets(backups)
            if restore_errors:
                raise BaseExceptionGroup("target restoration failed", restore_errors)

    def run(self) -> int:
        """Executes the whole benchmarking pipeline. Returns the process exit code."""
        self.successful_runs: dict[str, list[tuple[str, float, list[str]]]] = {}
        self.unsuccessful_runs: dict[str, list[tuple[GroupFailureCapture, float]]] = {}
        for patch_set in self.patches:
            with self._appliedPatchSet(patch_set):
                self._runAllConfigs(patch_set["name"])

        self._makeReport()

        success = self._printQuickStats()
        if success:
            self.Con.info("BulkBench session completed successfully")
        else:
            self.Con.error(
                "BulkBench session completed with some failures. Inspect the report and "
                "the console output for details."
            )

        self._cleanBackupDir()  # in the end to make important errors visible if they present
        return 0 if success else 1

    def _makeReport(self) -> None:
        self.report_dir.mkdir(parents=True, exist_ok=True)

        # each subsequent run of the tool will overwrite the previous report, which should be
        # fine, as we typically are only interested in the final report that combines everything.

        def _makeBSFile(basename: str) -> Path:
            bs_path = self.report_dir / basename
            if bs_path.exists():
                self.Con.warning(
                    "Benchstats report '", str(bs_path), "' already exists and will be overwritten."
                )
            return bs_path

        disjoint = self._areGroupsDisjoint()
        if disjoint:
            # since each benchmark config is defined in exactly one group, in benchmark comparisons
            # we could make the group name part of benchmarking entity id from the very beginning,
            # i.e. run only a single comparison with --filter1=1 only.
            self.Con.info(
                "All benchmark config groups are disjoint. Comparison with groups fixed is enough."
            )
            bs_file = None
        else:
            self.Con.info(
                "Found a non-disjoint benchmark config group. Will run a global all-to-all "
                "comparison, and a fixed-groups comparison."
            )
            bs_file = _makeBSFile("benchstats-all-to-all.html")
        bs_filter1_file = _makeBSFile("benchstats-fix-groups.html")

        if bs_file:
            self.Con.info(f"Running global all-to-all comparison into {bs_file}")
            self._compareBenchmarks(export_to=str(bs_file))
        if bs_filter1_file:
            self.Con.info(f"Running per-group comparison into {bs_filter1_file}")
            self._compareBenchmarks(filter_val="1", export_to=str(bs_filter1_file))

    def _printQuickStats(self) -> bool:
        self.Con.info("Session statistics:")
        n_successful_groups = sum(len(r) for r in self.successful_runs.values())
        n_configs_run = sum(len(c) for r in self.successful_runs.values() for (_, _, c) in r)
        if n_configs_run:
            # dict[str, list[tuple[str, float, list[str]]]]            
            self.Con.info(
                n_successful_groups,
                "groups,",
                n_configs_run,
                "individual configs in total ran successfully. Details by patch set:",
            )
            for patch_set_name, data in self.successful_runs.items():
                self.Con.info(
                    f"  {patch_set_name}, {len(data)} config groups: ",
                    ", ".join(
                        f"'{gn}' ({_formatDuration(d)}, run configs: {', '.join(cr)})"
                        if cr
                        else f"'{gn}' (cached)"
                        for (gn, d, cr) in data
                    ),
                )
        else:
            self.Con.info("All", n_successful_groups, "groups didn't actually run due to results already present.")

        n_unsuccessful_groups = sum(len(r) for r in self.unsuccessful_runs.values())
        if n_unsuccessful_groups:
            # dict[str, list[tuple[GroupFailureCapture, float]]]
            n_configs_run = sum(
                len(gc.configs_to_run) for pd in self.unsuccessful_runs.values() for (gc, _) in pd
            )
            self.Con.error(
                "Total number of config groups run unsuccessfully is",
                n_unsuccessful_groups,
                "groups, up to",
                n_configs_run,
                "individual configs in total failed. Details by patch set:",
            )
            for patch_set_name, data in self.unsuccessful_runs.items():
                if len(data) > 0:
                    self.Con.warning(
                        f"  in patch set '{patch_set_name}' these", len(data), "config groups failed:"
                    )
                    for (run_result, duration) in data:
                        assert isinstance(run_result, GroupFailureCapture)
                        self.Con.error(
                            f"    '{run_result.group_name}' ({_formatDuration(duration)}, "
                            f"tried to run: {', '.join(run_result.configs_to_run)}). "
                            f"Got return code {run_result.returncode}",
                        )
                        self.Con.debug(f"Registered output:\n{run_result.output}")
        return n_unsuccessful_groups == 0  # all groups ran successfully

    def _areGroupsDisjoint(self) -> bool:
        """Infers from directory structure if each config belong to exactly one group.

        Using ground truth from the filesystem enables support for multiple runs of the tool
        over arbitrary set of configs and patches into the same --results_dir.
        """
        configs_by_group: dict[str, set[str]] = {}
        for directory, subdirectories, filenames in os.walk(self.results_dir):
            relative_parts = Path(directory).relative_to(self.results_dir).parts
            if len(relative_parts) == 1:
                subdirectories[:] = [
                    name for name in subdirectories if not name.startswith(EAGER_GROUP_PREFIX)
                ]

            if "timings.json" not in filenames:
                continue
            if len(relative_parts) != 3:
                self.Con.warning(
                    f"Found unexpectedly nested timings.json in {directory}. "
                    "Cancelling '--filter1' argument estimation"
                )
                return False

            _, group_name, config_name = relative_parts
            configs_by_group.setdefault(group_name, set()).add(config_name)

        seen_configs: set[str] = set()
        for configs in configs_by_group.values():
            if not seen_configs.isdisjoint(configs):
                return False
            seen_configs.update(configs)
        return True

    def _compareBenchmarks(
        self, filter_val: str | None = None, export_to: str | None = None
    ) -> None:
        """Compares benchmark results in an interactive terminal."""
        args = [
            "benchstats",
            str(self.results_dir),
            "--files_parser=bulkbench.parser_JSON",
            "--sample_stats",
            "0",
            "100",
            "--always_show_pvalues",
        ]
        if self.Con.will_log(LoggingConsole.LogLevel.Debug):
            args.append("--show_debug")

        if filter_val:
            args.append(f"--filter1={filter_val}")
        if export_to:
            args.append(f"--export_to={export_to}")

        self.Con.info("Running command: '", " ".join(args), "'")

        try:
            self._Con_end()
            _ = run_with_script(args, cwd=self.results_dir, ignore_output=True)
        except KeyboardInterrupt:
            self._Con_begin()
            self.Con.warning(
                "Caught KeyboardInterrupt. If you want to abort BulkBench too, hit Ctrl-C again."
            )
        except OSError as exc:
            self._Con_begin()
            self.Con.error(f"Error running command: {exc}")
        else:
            self._Con_begin()

    def _logConfigRunError(
        self,
        patch_set_name: str,
        fc: GroupFailureCapture,
        duration: float,
        err_pfx: str = "",
    ) -> None:
        self.Con.error(
            f"{err_pfx}Config group '{fc.group_name}' "
            f"(run configs:{', '.join(fc.configs_to_run)}) "
            f"on patch set '{patch_set_name}' failed in {_formatDuration(duration)}."
        )
        self.Con.debug(f"Return code: {fc.returncode}")

    def _runAllConfigs(self, patch_set_name: str) -> None:
        """Runs every config group and records its outcome for the patch set."""
        self.Con.info(f"Running all config groups for patch set '{patch_set_name}'")
        successful: list[tuple[str, float, list[str]]] = []
        unsuccessful: list[tuple[GroupFailureCapture, float]] = []
        for cfg in self.configs.values():
            group_name = cfg["name"]
            only_in_patches = cfg["only_in_patches"]
            if only_in_patches is not None and patch_set_name not in only_in_patches:
                self.Con.info(
                    f"Config group '{group_name}' is disabled for patch set '{patch_set_name}'. Ignoring it."
                )
                continue
            configs_to_run: list[str] = []
            started = monotonic()
            try:
                try:
                    workdir = self.results_dir / patch_set_name / group_name
                    for config_name in cfg["configs"]:
                        if not self.regenerate_results and configMightHaveRunSuccessfully(
                            workdir, config_name
                        ):
                            self.Con.info(
                                f"Skipping config '{config_name}' in config group "
                                f"'{group_name}' for patch set '{patch_set_name}': "
                                "its existing results might be successful"
                            )
                        else:
                            configs_to_run.append(config_name)

                    if configs_to_run:
                        self._runConfig(
                            patch_set_name,
                            workdir,
                            group_name,
                            configs_to_run,
                            cfg["override_args"],
                        )
                    else:
                        self.Con.info(
                            f"All configs in config group '{group_name}' for patch set "
                            f"'{patch_set_name}' might already have run successfully; "
                            "treating the config group as successful"
                        )
                finally:
                    duration = monotonic() - started
            except GroupRunError as exc:
                unsuccessful.append((exc.result, duration))
                self._logConfigRunError(patch_set_name, exc.result, duration)

            except Exception as exc:  # noqa: BLE001 - one config must not stop the remaining runs
                exc_result = GroupFailureCapture(
                    group_name=group_name,
                    configs_to_run=configs_to_run,
                    output="".join(traceback.format_exception(exc)),
                    returncode=None,
                )
                unsuccessful.append((exc_result, duration))
                self._logConfigRunError(
                    patch_set_name,
                    exc_result,
                    duration,
                    "[UNEXPECTED ERROR] ",
                )
            else:
                successful.append((group_name, duration, configs_to_run))
                self.Con.info(
                    f"Config '{group_name}' (run configs:{', '.join(configs_to_run)}) "
                    f"succeeded in {_formatDuration(duration)}"
                )

        self.successful_runs[patch_set_name] = successful
        self.unsuccessful_runs[patch_set_name] = unsuccessful

    def _runConfig(
        self,
        patch_set_name: str,
        workdir: Path,
        group_name: str,
        configs_to_run: list[str],
        cfg_override_args: str | None,
    ) -> None:
        """Runs one config group and raises GroupRunError on process failure."""
        self.Con.info(
            f"Running '{group_name}' config group ({', '.join(configs_to_run)}) "
            f"for patch set '{patch_set_name}'"
        )
        workdir.mkdir(parents=True, exist_ok=True)

        args = ["python", str(_BENCHMARK_RUNNER)]
        for config_name in configs_to_run:
            args.extend(("--name", config_name))
        if cfg_override_args is not None:
            args.extend(("--override-args-json", cfg_override_args))
        args.extend(("--results-directory", str(workdir)))

        benchmark_configs = dict.fromkeys(
            self._benchmarkConfigPath(
                config_name,
                f"config group {group_name!r}, config {config_name!r}",
            )
            for config_name in configs_to_run
        )
        args.extend(str(path) for path in benchmark_configs)
        self.Con.trace("Running command: '", " ".join(args), "'")

        try:
            self._Con_end()
            completed = run_with_script(args, cwd=_APP_DIR)
        except KeyboardInterrupt as exc:
            self._Con_begin()
            self.Con.warning(
                "Caught KeyboardInterrupt. If you want to abort BulkBench too, hit Ctrl-C again."
            )
            raise GroupRunError(
                GroupFailureCapture(
                    group_name=group_name,
                    configs_to_run=configs_to_run,
                    output="User interrupted!",
                    returncode=None,
                )
            ) from exc
        except OSError as exc:
            self._Con_begin()
            result = GroupFailureCapture(
                group_name=group_name,
                configs_to_run=configs_to_run,
                output=str(exc),
                returncode=None,
            )
            raise GroupRunError(result) from exc
        else:
            self._Con_begin()

        if completed.returncode != 0:
            raise GroupRunError(
                GroupFailureCapture(
                    group_name=group_name,
                    configs_to_run=configs_to_run,
                    output=completed.output,
                    returncode=completed.returncode,
                )
            )
