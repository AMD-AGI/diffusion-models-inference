"""Implementation of the `bulkbench` tool."""

import json
import os
import re
import shutil
import subprocess
import traceback
import yaml  # pyright: ignore[reportMissingModuleSource]

from .script_runner import run_with_script
from benchstats.common import LoggingConsole
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict
from yaml.constructor import ConstructorError  # pyright: ignore[reportMissingModuleSource]
from yaml.nodes import MappingNode  # pyright: ignore[reportMissingModuleSource]

DEFAULT_CONSOLE_LOG_LEVEL = LoggingConsole.LogLevel.Info
DEFAULT_RESULTS_SUBDIR = "results"
DEFAULT_REPORT_SUBDIR = "report"
DEFAULT_BACKUP_SUBDIR = "_backups"
DEFAULT_CONFIGS_FILE = "configs.yaml"
DEFAULT_PATCHES_FILE = "patches.yaml"
VALID_NAME_PATTERN = r"[-a-zA-Z0-9_+={}., ~!()\[\]]+"

_VALID_NAME_RE = re.compile(VALID_NAME_PATTERN)
_APP_DIR = Path("/app")
_BENCHMARK_CONFIGS_DIR = _APP_DIR / ".ci" / "benchmark_configs"
_BENCHMARK_RUNNER = _APP_DIR / ".ci" / "run.py"

StrPath = str | os.PathLike[str]


class ConfigGroup(TypedDict):
    """Validated benchmark config group loaded from the configs YAML file."""

    name: str
    configs: list[str]
    override_args: str | None


class PatchData(TypedDict):
    """Validated patch description loaded from the patches YAML file."""

    patch: Path
    target: Path


class PatchSet(TypedDict):
    """Validated named patch set loaded from the patches YAML file."""

    name: str
    patches: list[PatchData]


class TargetBackup(TypedDict):
    """Files needed to restore one patch target."""

    backup: Path
    path_file: Path
    target: Path


@dataclass(frozen=True)
class ConfigRunResult:
    """Captured outcome of one benchmark config-group process."""

    config_name: str
    output: str
    returncode: int | None


class ConfigRunError(RuntimeError):
    """Raised when a benchmark config-group process fails."""

    def __init__(self, result: ConfigRunResult) -> None:
        self.result = result
        status = (
            f"exit status {result.returncode}"
            if result.returncode is not None
            else "process start failure"
        )
        super().__init__(f"benchmark config group {result.config_name!r} failed: {status}")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        self.flatten_mapping(node)
        keys: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in keys
                keys.add(key)
            except TypeError:
                # The base constructor emits a contextual error for unhashable keys.
                continue
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
        return super().construct_mapping(node, deep=deep)


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

        def _get_arg(name: str, default: Any = None) -> Any:
            return kwargs.get(name, getattr(args, name, default))

        self.Con = self._validatedConsole(_get_arg("console"), _get_arg("console_log_level"))
        self.Con.trace(f"Console log level: {self.Con.log_level}")

        self.arch: str = _get_arg("arch")
        if self.arch is None:
            self.arch = get_amd_gpu_arch_rocminfo()
        assert isinstance(self.arch, str), "arch must be a string"
        self.Con.debug(
            f"Using arch tag = {self.arch}" if self.arch else "Arch is not set, tags won't be used"
        )

        self.successful_runs: dict[str, list[str]] = {}
        self.unsuccessful_runs: dict[str, dict[str, ConfigRunResult]] = {}

        self.project_dir: Path = self._validatedProjectDir(_get_arg("project_dir"))
        self.configs: dict[str, ConfigGroup] = self._readConfigs(_get_arg("configs_file"))
        self.patches: list[PatchSet] = self._readPatches(_get_arg("patches_file"))
        self.results_dir: Path = self._validatedOutputDir(
            _get_arg("results_dir"), DEFAULT_RESULTS_SUBDIR, "results_dir"
        )

        self.report_dir: Path = self._validatedOutputDir(
            _get_arg("report_dir"), DEFAULT_REPORT_SUBDIR, "report_dir"
        )
        if any(self.report_dir.iterdir()):
            raise ValueError(f"--report_dir directory '{self.report_dir}' isn't empty")

        self.backup_dir: Path = self._validatedOutputDir(
            _get_arg("backup_dir"), DEFAULT_BACKUP_SUBDIR, "backup_dir"
        )
        if any(self.backup_dir.iterdir()):
            raise ValueError(f"--backup_dir directory '{self.backup_dir}' isn't empty")
        self._validateBackupDirIsDisjoint()

    @staticmethod
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

    def _Con_begin(self, level: LoggingConsole.LogLevel) -> None:
        if self.Con.will_log(level):
            self.Con.print("[bold bright_blue]==== BulkBench >>>>>>>>[/bold bright_blue]")

    def _Con_end(self, level: LoggingConsole.LogLevel) -> None:
        if self.Con.will_log(level):
            self.Con.print("[bold bright_blue]<<<<<<<< BulkBench ====[/bold bright_blue]")

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

    def _validatedPatchesFile(self, value: StrPath | None) -> Path:
        """Resolves `value` (defaults to `DEFAULT_PATCHES_FILE`) and makes sure it points
        to an existing file."""
        path = self._resolvedPath(value, DEFAULT_PATCHES_FILE)
        if not path.is_file():
            raise ValueError(f"patches_file '{path}' doesn't exist or isn't a file")
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
    def _validateJsonMappingKeys(
        value: Any, location: str, active_containers: set[int] | None = None
    ) -> None:
        """Makes sure every mapping nested in `value` has string keys and no cycles."""
        if not isinstance(value, (dict, list)):
            return

        active_containers = active_containers if active_containers is not None else set()
        container_id = id(value)
        if container_id in active_containers:
            raise ValueError(f"{location} contains a circular reference")

        active_containers.add(container_id)
        try:
            if isinstance(value, dict):
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ValueError(  # noqa: TRY004 - invalid public config value
                            f"{location} contains non-string mapping key {key!r}"
                        )
                    BulkBench._validateJsonMappingKeys(item, f"{location}.{key}", active_containers)
            else:
                for index, item in enumerate(value):
                    BulkBench._validateJsonMappingKeys(
                        item, f"{location}[{index}]", active_containers
                    )
        finally:
            active_containers.remove(container_id)

    @staticmethod
    def _validatedEnabled(value: Any, object_context: str) -> bool:
        """Validates and normalizes an object's `enabled` value."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            if value in ("true", "1"):
                return True
            if value in ("false", "0"):
                return False
        raise ValueError(
            f"{object_context} attribute 'enabled' must be a YAML boolean, integer 1 or 0, "
            'or one of the strings "true", "false", "1", "0"'
        )

    @staticmethod
    def _validatedName(value: Any, object_context: str) -> str:
        """Strips and validates a config-group or patch-set name."""
        if not isinstance(value, str) or not (name := value.strip()):
            raise ValueError(f"{object_context} attribute 'name' must be a non-empty string")
        if name in (".", "..") or _VALID_NAME_RE.fullmatch(name) is None:
            raise ValueError(
                f"{object_context} attribute 'name' must match "
                f"{VALID_NAME_PATTERN!r} and must not be '.' or '..'"
            )
        return name

    @staticmethod
    def _benchmarkConfigPath(config_name: str, config_context: str) -> Path:
        """Builds a benchmark YAML path from a validated config name."""
        stem = config_name.partition(".")[0]
        if not stem or stem in (".", "..") or _VALID_NAME_RE.fullmatch(stem) is None:
            raise ValueError(
                f"{config_context} prefix before the first dot must match "
                f"{VALID_NAME_PATTERN!r} and must not be '.' or '..'"
            )
        return _BENCHMARK_CONFIGS_DIR / f"{stem}.yaml"

    def _validateBenchmarkConfigPath(self, config_name: str, config_context: str) -> None:
        """Checks that a config's benchmark YAML exists."""
        path = self._benchmarkConfigPath(config_name, config_context)
        if not path.is_file():
            raise ValueError(
                f"{config_context} requires benchmark config file '{path}', "
                "which doesn't exist or isn't a file"
            )

    def _readConfigs(self, configs_file_value: StrPath | None) -> dict[str, ConfigGroup]:
        """Reads and validates benchmark config groups from a YAML file."""
        configs_file = self._validatedConfigsFile(configs_file_value)
        try:
            with configs_file.open("r", encoding="utf-8") as file:
                raw_groups = yaml.load(file, Loader=_UniqueKeySafeLoader)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ValueError(f"failed to read configs_file '{configs_file}': {exc}") from exc

        error_prefix = f"configs_file '{configs_file}'"
        if not isinstance(raw_groups, list):
            raise ValueError(  # noqa: TRY004 - public API reports invalid config values
                f"{error_prefix} must contain a YAML list"
            )

        allowed_attributes = {"configs", "enabled", "name", "override_args"}
        groups: dict[str, ConfigGroup] = {}
        for group_index, raw_group in enumerate(raw_groups, start=1):
            group_context = f"{error_prefix}, group {group_index}"
            if not isinstance(raw_group, dict):
                raise ValueError(  # noqa: TRY004 - public API reports invalid config values
                    f"{group_context} must be an object"
                )

            if not self._validatedEnabled(raw_group.get("enabled", True), group_context):
                continue

            non_string_attributes = [key for key in raw_group if not isinstance(key, str)]
            if non_string_attributes:
                raise ValueError(
                    f"{group_context} contains non-string attribute {non_string_attributes[0]!r}"
                )

            unknown_attributes = set(raw_group) - allowed_attributes
            if unknown_attributes:
                unknown = ", ".join(sorted(unknown_attributes))
                raise ValueError(f"{group_context} contains unknown attribute(s): {unknown}")

            if "name" not in raw_group:
                raise ValueError(f"{group_context} is missing required attribute 'name'")
            name = self._validatedName(raw_group["name"], group_context)
            if name in groups:
                raise ValueError(f"{error_prefix} contains duplicate group name {name!r}")

            if "configs" not in raw_group:
                raise ValueError(f"{group_context} is missing required attribute 'configs'")
            group_configs = raw_group["configs"]
            if not isinstance(group_configs, list) or not group_configs:
                raise ValueError(f"{group_context} attribute 'configs' must be a non-empty list")

            seen_configs: set[str] = set()
            for config_index, config in enumerate(group_configs, start=1):
                config_context = f"{group_context} attribute 'configs', item {config_index}"
                if not isinstance(config, str) or not (config := config.strip()):
                    raise ValueError(f"{config_context} must be a non-empty string")
                self._validateBenchmarkConfigPath(config, config_context)
                if config in seen_configs:
                    raise ValueError(f"{group_context} contains duplicate config name {config!r}")
                seen_configs.add(config)

            override_args = raw_group.get("override_args")
            serialized_override_args: str | None = None
            if override_args is not None:
                if not isinstance(override_args, dict):
                    raise ValueError(
                        f"{group_context} attribute 'override_args' must be an object or null"
                    )
                try:
                    self._validateJsonMappingKeys(
                        override_args, f"{group_context} attribute 'override_args'"
                    )
                    serialized_override_args = json.dumps(
                        override_args, allow_nan=False, separators=(",", ":")
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{group_context} attribute 'override_args' isn't valid JSON: {exc}"
                    ) from exc

            groups[name] = {
                "name": name,
                "configs": group_configs,
                "override_args": serialized_override_args,
            }

        if not groups:
            raise ValueError(f"{error_prefix} must contain at least one enabled config group")

        self.Con.debug(f"Read {len(groups)} config groups from {configs_file}: ", groups)
        return groups

    @staticmethod
    def _validatedPatchPath(value: Any, base_dir: Path, attribute: str, context: str) -> Path:
        """Resolves and validates a patch or target path."""
        if not isinstance(value, str) or not (value := value.strip()):
            raise ValueError(f"{context} attribute '{attribute}' must be a non-empty string")

        path = Path(value).expanduser()
        path = (path if path.is_absolute() else base_dir / path).resolve()
        if not path.is_file():
            raise ValueError(
                f"{context} attribute '{attribute}' path '{path}' doesn't exist or isn't a file"
            )
        return path

    def _readPatches(self, patches_file_value: StrPath | None) -> list[PatchSet]:
        """Reads and validates named patch sets from a YAML file."""
        patches_file = self._validatedPatchesFile(patches_file_value)
        try:
            with patches_file.open("r", encoding="utf-8") as file:
                raw_patch_sets = yaml.load(file, Loader=_UniqueKeySafeLoader)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ValueError(f"failed to read patches_file '{patches_file}': {exc}") from exc

        error_prefix = f"patches_file '{patches_file}'"
        if not isinstance(raw_patch_sets, list):
            raise ValueError(  # noqa: TRY004 - public API reports invalid patch values
                f"{error_prefix} must contain a YAML list"
            )
        if not raw_patch_sets:
            raise ValueError(f"{error_prefix} must contain at least one patch set")

        patch_set_attributes = {"name", "patches"}
        patch_attributes = {"enabled", "patch", "target"}
        patch_set_names: set[str] = set()
        seen_patch_sets: dict[frozenset[tuple[Path, Path]], str] = {}
        loaded_patch_sets: list[PatchSet] = []

        for patch_set_index, raw_patch_set in enumerate(raw_patch_sets, start=1):
            patch_set_context = f"{error_prefix}, patch set {patch_set_index}"
            if not isinstance(raw_patch_set, dict):
                raise ValueError(  # noqa: TRY004 - public API reports invalid patch values
                    f"{patch_set_context} must be an object"
                )

            non_string_attributes = [key for key in raw_patch_set if not isinstance(key, str)]
            if non_string_attributes:
                raise ValueError(
                    f"{patch_set_context} contains non-string attribute "
                    f"{non_string_attributes[0]!r}"
                )

            unknown_attributes = set(raw_patch_set) - patch_set_attributes
            if unknown_attributes:
                unknown = ", ".join(sorted(unknown_attributes))
                raise ValueError(f"{patch_set_context} contains unknown attribute(s): {unknown}")

            if "name" not in raw_patch_set:
                raise ValueError(f"{patch_set_context} is missing required attribute 'name'")
            name = self._validatedName(raw_patch_set["name"], patch_set_context)
            if name in patch_set_names:
                raise ValueError(f"{error_prefix} contains duplicate patch set name {name!r}")
            patch_set_names.add(name)

            if "patches" not in raw_patch_set:
                raise ValueError(f"{patch_set_context} is missing required attribute 'patches'")
            raw_patches = raw_patch_set["patches"]
            if not isinstance(raw_patches, list):
                raise ValueError(  # noqa: TRY004 - public API reports invalid patch values
                    f"{patch_set_context} attribute 'patches' must be a list"
                )

            patches: list[PatchData] = []
            patch_keys: set[tuple[Path, Path]] = set()
            for patch_index, raw_patch in enumerate(raw_patches, start=1):
                patch_context = f"{patch_set_context}, patch {patch_index}"
                if not isinstance(raw_patch, dict):
                    raise ValueError(  # noqa: TRY004 - public API reports invalid patch values
                        f"{patch_context} must be an object"
                    )

                if not self._validatedEnabled(raw_patch.get("enabled", True), patch_context):
                    continue

                non_string_attributes = [key for key in raw_patch if not isinstance(key, str)]
                if non_string_attributes:
                    raise ValueError(
                        f"{patch_context} contains non-string attribute "
                        f"{non_string_attributes[0]!r}"
                    )

                unknown_attributes = set(raw_patch) - patch_attributes
                if unknown_attributes:
                    unknown = ", ".join(sorted(unknown_attributes))
                    raise ValueError(f"{patch_context} contains unknown attribute(s): {unknown}")

                if "patch" not in raw_patch:
                    raise ValueError(f"{patch_context} is missing required attribute 'patch'")
                if "target" not in raw_patch:
                    raise ValueError(f"{patch_context} is missing required attribute 'target'")

                patch_path = self._validatedPatchPath(
                    raw_patch["patch"], self.project_dir / name, "patch", patch_context
                )
                target_path = self._validatedPatchPath(
                    raw_patch["target"], Path("/app"), "target", patch_context
                )

                patch_key = (patch_path, target_path)
                if patch_key in patch_keys:
                    raise ValueError(
                        f"{patch_set_context} contains duplicate patch object "
                        f"({patch_path}, {target_path})"
                    )
                patch_keys.add(patch_key)
                patches.append({"patch": patch_path, "target": target_path})

            patch_set_key = frozenset(patch_keys)
            if duplicate_patch_set := seen_patch_sets.get(patch_set_key):
                raise ValueError(
                    f"{error_prefix} patch sets {duplicate_patch_set!r} and {name!r} "
                    "contain duplicate patch sets"
                )
            seen_patch_sets[patch_set_key] = name
            loaded_patch_sets.append({"name": name, "patches": patches})

        # validate applicability early to prevent failures during long running work.
        for patch_set in loaded_patch_sets:
            self._dryRunPatches(patch_set)

        self.Con.debug(
            f"Read {len(loaded_patch_sets)} patch sets from {patches_file}:", loaded_patch_sets
        )
        return loaded_patch_sets

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
        self.successful_runs: dict[str, list[str]] = {}
        self.unsuccessful_runs: dict[str, dict[str, ConfigRunResult]] = {}
        for patch_set in self.patches:
            with self._appliedPatchSet(patch_set):
                self._runAllConfigs(patch_set["name"])
        return 0

    def _logConfigRunError(
        self, patch_set_name: str, rr: ConfigRunResult, err_pfx: str = ""
    ) -> None:
        self.Con.error(
            f"{err_pfx}Patch set '{patch_set_name}', config group '{rr.config_name}' "
            f"({', '.join(self.configs[rr.config_name]['configs'])}) failed."
        )
        self.Con.debug(f"Return code: {rr.returncode}")

    def _runAllConfigs(self, patch_set_name: str) -> None:
        """Runs every config group and records its outcome for the patch set."""
        self.Con.info(f"Running all config groups for patch set '{patch_set_name}'")
        successful: list[str] = []
        unsuccessful: dict[str, ConfigRunResult] = {}
        for cfg in self.configs.values():
            try:
                self._runConfig(patch_set_name, cfg)
            except ConfigRunError as exc:
                unsuccessful[cfg["name"]] = exc.result
                self._logConfigRunError(patch_set_name, exc.result)

            except Exception as exc:  # noqa: BLE001 - one config must not stop the remaining runs
                exc_result = ConfigRunResult(
                    config_name=cfg["name"],
                    output="".join(traceback.format_exception(exc)),
                    returncode=None,
                )
                unsuccessful[cfg["name"]] = exc_result
                self._logConfigRunError(patch_set_name, exc_result, "[UNEXPECTED ERROR] ")
            else:
                successful.append(cfg["name"])
                self.Con.info(
                    f"Config '{cfg['name']}' succeeded"
                    + (f" with --tag={self.arch}" if self.arch else "")
                )
                self.Con.trace(cfg)

        self.successful_runs[patch_set_name] = successful
        self.unsuccessful_runs[patch_set_name] = unsuccessful

    def _runConfig(self, patch_set_name: str, cfg: ConfigGroup) -> None:
        """Runs one config group and raises ConfigRunError on process failure."""
        self.Con.debug(f"Running '{cfg['name']}' config group for patch set '{patch_set_name}'")
        self.Con.trace(cfg)
        workdir = self.results_dir / patch_set_name / cfg["name"]
        workdir.mkdir(parents=True, exist_ok=True)

        args = ["python", str(_BENCHMARK_RUNNER)]
        for config_name in cfg["configs"]:
            args.extend(("--name", config_name))
        if cfg["override_args"] is not None:
            args.extend(("--override-args-json", cfg["override_args"]))
        if self.arch:
            args.extend(("--tag", self.arch))
        args.extend(("--results-directory", str(workdir)))

        benchmark_configs = dict.fromkeys(
            self._benchmarkConfigPath(
                config_name,
                f"config group {cfg['name']!r}, config {config_name!r}",
            )
            for config_name in cfg["configs"]
        )
        args.extend(str(path) for path in benchmark_configs)
        self.Con.trace(f"Running command: {' '.join(args)}")

        try:
            completed = run_with_script(args, cwd=_APP_DIR)
        except KeyboardInterrupt as exc:
            self.Con.warning(
                "Caught KeyboardInterrupt. If you want to abort BulkBench too, hit Ctrl-C again."
            )
            raise ConfigRunError(
                ConfigRunResult(
                    config_name=cfg["name"],
                    output="User interrupted!",
                    returncode=None,
                )
            ) from exc
        except OSError as exc:
            result = ConfigRunResult(
                config_name=cfg["name"],
                output=str(exc),
                returncode=None,
            )
            raise ConfigRunError(result) from exc

        if completed.returncode != 0:
            raise ConfigRunError(
                ConfigRunResult(
                    config_name=cfg["name"],
                    output=completed.output,
                    returncode=completed.returncode,
                )
            )
