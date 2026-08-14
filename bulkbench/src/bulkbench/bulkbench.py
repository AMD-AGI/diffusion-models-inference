"""Implementation of the `bulkbench` tool."""

import json
import os
import subprocess
import yaml  # pyright: ignore[reportMissingModuleSource]

from benchstats.common import LoggingConsole
from pathlib import Path
from typing import Any, TypedDict
from yaml.constructor import ConstructorError  # pyright: ignore[reportMissingModuleSource]
from yaml.nodes import MappingNode  # pyright: ignore[reportMissingModuleSource]

DEFAULT_RESULTS_SUBDIR = "results"
DEFAULT_REPORT_SUBDIR = "report"
DEFAULT_CONFIGS_FILE = "configs.yaml"
DEFAULT_PATCHES_FILE = "patches.yaml"

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


class PatchGroup(TypedDict):
    """Validated group of patches loaded from the patches YAML file."""

    name: str
    patches: list[PatchData]


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
        self.configs: dict[str, ConfigGroup] = self._readConfigs(_get_arg("configs_file"))
        self.patches: list[PatchGroup] = self._readPatches(_get_arg("patches_file"))
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
    def _validatedEnabled(value: Any, group_context: str) -> bool:
        """Validates and normalizes a config group's `enabled` value."""
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
            f"{group_context} attribute 'enabled' must be a YAML boolean, integer 1 or 0, "
            'or one of the strings "true", "false", "1", "0"'
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
            name = raw_group["name"]
            if not isinstance(name, str) or not (name := name.strip()):
                raise ValueError(f"{group_context} attribute 'name' must be a non-empty string")
            if name in groups:
                raise ValueError(f"{error_prefix} contains duplicate group name {name!r}")

            if "configs" not in raw_group:
                raise ValueError(f"{group_context} is missing required attribute 'configs'")
            group_configs = raw_group["configs"]
            if not isinstance(group_configs, list) or not group_configs:
                raise ValueError(f"{group_context} attribute 'configs' must be a non-empty list")

            seen_configs: set[str] = set()
            for config_index, config in enumerate(group_configs, start=1):
                if not isinstance(config, str) or not (config := config.strip()):
                    raise ValueError(
                        f"{group_context} attribute 'configs', item {config_index} "
                        "must be a non-empty string"
                    )
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

        self.Con.debug(f"Read {len(groups)} config groups from {configs_file}: {groups}")
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

    def _readPatches(self, patches_file_value: StrPath | None) -> list[PatchGroup]:
        """Reads and validates named patch groups from a YAML file."""
        patches_file = self._validatedPatchesFile(patches_file_value)
        try:
            with patches_file.open("r", encoding="utf-8") as file:
                raw_groups = yaml.load(file, Loader=_UniqueKeySafeLoader)
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise ValueError(f"failed to read patches_file '{patches_file}': {exc}") from exc

        error_prefix = f"patches_file '{patches_file}'"
        if not isinstance(raw_groups, list):
            raise ValueError(  # noqa: TRY004 - public API reports invalid patch values
                f"{error_prefix} must contain a YAML list"
            )
        if not raw_groups:
            raise ValueError(f"{error_prefix} must contain at least one patch group")

        group_attributes = {"name", "patches"}
        patch_attributes = {"enabled", "patch", "target"}
        group_names: set[str] = set()
        patch_sets: dict[frozenset[tuple[Path, Path]], str] = {}
        groups: list[PatchGroup] = []

        for group_index, raw_group in enumerate(raw_groups, start=1):
            group_context = f"{error_prefix}, patch group {group_index}"
            if not isinstance(raw_group, dict):
                raise ValueError(  # noqa: TRY004 - public API reports invalid patch values
                    f"{group_context} must be an object"
                )

            non_string_attributes = [key for key in raw_group if not isinstance(key, str)]
            if non_string_attributes:
                raise ValueError(
                    f"{group_context} contains non-string attribute "
                    f"{non_string_attributes[0]!r}"
                )

            unknown_attributes = set(raw_group) - group_attributes
            if unknown_attributes:
                unknown = ", ".join(sorted(unknown_attributes))
                raise ValueError(f"{group_context} contains unknown attribute(s): {unknown}")

            if "name" not in raw_group:
                raise ValueError(f"{group_context} is missing required attribute 'name'")
            name = raw_group["name"]
            if not isinstance(name, str) or not (name := name.strip()):
                raise ValueError(f"{group_context} attribute 'name' must be a non-empty string")
            if name in group_names:
                raise ValueError(f"{error_prefix} contains duplicate patch group name {name!r}")
            group_names.add(name)

            if "patches" not in raw_group:
                raise ValueError(f"{group_context} is missing required attribute 'patches'")
            raw_patches = raw_group["patches"]
            if not isinstance(raw_patches, list):
                raise ValueError(  # noqa: TRY004 - public API reports invalid patch values
                    f"{group_context} attribute 'patches' must be a list"
                )

            patches: list[PatchData] = []
            patch_keys: set[tuple[Path, Path]] = set()
            for patch_index, raw_patch in enumerate(raw_patches, start=1):
                patch_context = f"{group_context}, patch {patch_index}"
                if not isinstance(raw_patch, dict):
                    raise ValueError(  # noqa: TRY004 - public API reports invalid patch values
                        f"{patch_context} must be an object"
                    )

                if not self._validatedEnabled(raw_patch.get("enabled", True), patch_context):
                    continue

                non_string_attributes = [
                    key for key in raw_patch if not isinstance(key, str)
                ]
                if non_string_attributes:
                    raise ValueError(
                        f"{patch_context} contains non-string attribute "
                        f"{non_string_attributes[0]!r}"
                    )

                unknown_attributes = set(raw_patch) - patch_attributes
                if unknown_attributes:
                    unknown = ", ".join(sorted(unknown_attributes))
                    raise ValueError(
                        f"{patch_context} contains unknown attribute(s): {unknown}"
                    )

                if "patch" not in raw_patch:
                    raise ValueError(
                        f"{patch_context} is missing required attribute 'patch'"
                    )
                if "target" not in raw_patch:
                    raise ValueError(
                        f"{patch_context} is missing required attribute 'target'"
                    )

                patch_path = self._validatedPatchPath(
                    raw_patch["patch"], self.project_dir, "patch", patch_context
                )
                target_path = self._validatedPatchPath(
                    raw_patch["target"], Path("/app"), "target", patch_context
                )

                patch_key = (patch_path, target_path)
                if patch_key in patch_keys:
                    raise ValueError(
                        f"{group_context} contains duplicate patch object "
                        f"({patch_path}, {target_path})"
                    )
                patch_keys.add(patch_key)
                patches.append({"patch": patch_path, "target": target_path})

            patch_set_key = frozenset(patch_keys)
            if duplicate_group := patch_sets.get(patch_set_key):
                raise ValueError(
                    f"{error_prefix} patch groups {duplicate_group!r} and {name!r} "
                    "contain duplicate patch sets"
                )
            patch_sets[patch_set_key] = name
            groups.append({"name": name, "patches": patches})

        self.Con.debug(f"Read {len(groups)} patch groups from {patches_file}: {groups}")
        return groups

    def run(self) -> int:
        """Executes the whole benchmarking pipeline. Returns the process exit code."""

        return 0

    def _runAllConfigs(self):
        args = ["python", "/app/.ci/run.py"]
