"""Loading and validation of benchmark plans."""

import json
import os
import re
from pathlib import Path
from typing import Any, TypedDict

import yaml  # pyright: ignore[reportMissingModuleSource]
from benchstats.common import LoggingConsole
from yaml.constructor import ConstructorError  # pyright: ignore[reportMissingModuleSource]
from yaml.nodes import MappingNode  # pyright: ignore[reportMissingModuleSource]

DEFAULT_CONFIGS_FILE = "configs.yaml"
DEFAULT_PATCHES_FILE = "patches.yaml"
EAGER_GROUP_PREFIX = "eager_"
VALID_NAME_PATTERN = r"[-a-zA-Z0-9_+={}., ~!()\[\]]+"

_VALID_NAME_RE = re.compile(VALID_NAME_PATTERN)
_PATCH_TARGETS_BASE_DIR = Path("/app")

StrPath = str | os.PathLike[str]


class ConfigGroup(TypedDict):
    """Validated benchmark config group loaded from the configs YAML file."""

    name: str
    configs: list[str]
    override_args: str | None
    only_in_patches: frozenset[str] | None


class PatchData(TypedDict):
    """Validated patch description loaded from the patches YAML file."""

    patch: Path
    target: Path


class PatchSet(TypedDict):
    """Validated named patch set loaded from the patches YAML file."""

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


def benchmarkConfigPath(
    benchmark_configs_dir: Path, config_name: str, config_context: str
) -> Path:
    """Builds a benchmark YAML path from a validated config name."""
    stem = config_name.partition(".")[0]
    if not stem or stem in (".", "..") or _VALID_NAME_RE.fullmatch(stem) is None:
        raise ValueError(
            f"{config_context} prefix before the first dot must match "
            f"{VALID_NAME_PATTERN!r} and must not be '.' or '..'"
        )
    return benchmark_configs_dir / f"{stem}.yaml"


class BenchmarkPlanLoader:
    """Loads and validates patch sets and benchmark config groups."""

    def __init__(
        self,
        project_dir: Path,
        arch: str,
        console: LoggingConsole,
        benchmark_configs_dir: Path,
    ) -> None:
        self.project_dir = project_dir
        self.arch = arch
        self.console = console
        self.benchmark_configs_dir = benchmark_configs_dir

    def _resolvedPath(self, value: StrPath | None, default: str) -> Path:
        """Resolves a path, taking a relative path relative to the project directory."""
        path = Path(default if value is None else value).expanduser()
        return (path if path.is_absolute() else self.project_dir / path).resolve()

    def _validatedConfigsFile(self, value: StrPath | None) -> Path:
        """Resolves `value` and makes sure it points to an existing configs file."""
        path = self._resolvedPath(value, DEFAULT_CONFIGS_FILE)
        if not path.is_file():
            raise ValueError(f"configs_file '{path}' doesn't exist or isn't a file")
        return path

    def _validatedPatchesFile(self, value: StrPath | None) -> Path:
        """Resolves `value` and makes sure it points to an existing patches file."""
        path = self._resolvedPath(value, DEFAULT_PATCHES_FILE)
        if not path.is_file():
            raise ValueError(f"patches_file '{path}' doesn't exist or isn't a file")
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
                    BenchmarkPlanLoader._validateJsonMappingKeys(
                        item, f"{location}.{key}", active_containers
                    )
            else:
                for index, item in enumerate(value):
                    BenchmarkPlanLoader._validateJsonMappingKeys(
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

    def _benchmarkConfigTags(
        self,
        config_name: str,
        config_context: str,
        benchmark_configs_cache: dict[Path, list[Any]],
    ) -> Any:
        """Checks that a benchmark config exists and returns its tags."""
        path = benchmarkConfigPath(
            self.benchmark_configs_dir,
            config_name,
            config_context,
        )
        if not path.is_file():
            raise ValueError(
                f"{config_context} requires benchmark config file '{path}', "
                "which doesn't exist or isn't a file"
            )

        if path not in benchmark_configs_cache:
            try:
                with path.open("r", encoding="utf-8") as file:
                    raw_benchmark_configs = yaml.safe_load(file)
            except (OSError, UnicodeError, yaml.YAMLError) as exc:
                raise ValueError(f"failed to read benchmark config file '{path}': {exc}") from exc
            if not isinstance(raw_benchmark_configs, list):
                raise ValueError(f"benchmark config file '{path}' must contain a YAML list")
            benchmark_configs_cache[path] = raw_benchmark_configs

        for benchmark_config in benchmark_configs_cache[path]:
            if isinstance(benchmark_config, dict) and benchmark_config.get("name") == config_name:
                return benchmark_config.get("tags")

        raise ValueError(f"{config_context} config {config_name!r} wasn't found in '{path}'")

    def _validatedConfigPatchNames(
        self,
        value: Any,
        attribute_name: str,
        group_context: str,
        patch_set_names: set[str],
    ) -> frozenset[str]:
        """Validates, deduplicates, and resolves a config group's patch-set references."""
        if value is None:
            raise ValueError(f"{attribute_name} field is set but empty")
        if not isinstance(value, list):
            raise ValueError(  # noqa: TRY004 - public API reports invalid config values
                f"{group_context} attribute '{attribute_name}' must be a list"
            )

        referenced_names: set[str] = set()
        seen_names: set[str] = set()
        for patch_index, patch_name in enumerate(value, start=1):
            patch_context = f"{group_context} attribute '{attribute_name}', item {patch_index}"
            if not isinstance(patch_name, str) or not (patch_name := patch_name.strip()):
                raise ValueError(f"{patch_context} must be a non-empty string")
            if patch_name in seen_names:
                continue
            seen_names.add(patch_name)
            if patch_name not in patch_set_names:
                self.console.warning(
                    f"Patch set '{patch_name}' referenced in {group_context} attribute "
                    f"'{attribute_name}' isn't defined. Ignoring it."
                )
                continue
            referenced_names.add(patch_name)

        return frozenset(referenced_names)

    def readConfigs(
        self, configs_file_value: StrPath | None, patch_set_names: set[str]
    ) -> dict[str, ConfigGroup]:
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

        allowed_attributes = {
            "configs",
            "eager_in_patches",
            "enabled",
            "name",
            "only_in_patches",
            "override_args",
        }
        benchmark_configs_cache: dict[Path, list[Any]] = {}
        enabled_group_names: set[str] = set()
        applicable_group_names: set[str] = set()
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
            if name.startswith(EAGER_GROUP_PREFIX):
                raise ValueError(
                    f"{group_context} attribute 'name' must not start with reserved prefix "
                    f"'{EAGER_GROUP_PREFIX}'"
                )
            if name in enabled_group_names:
                raise ValueError(f"{error_prefix} contains duplicate group name {name!r}")
            enabled_group_names.add(name)

            if "configs" not in raw_group:
                raise ValueError(f"{group_context} is missing required attribute 'configs'")
            group_configs = raw_group["configs"]
            if not isinstance(group_configs, list) or not group_configs:
                raise ValueError(f"{group_context} attribute 'configs' must be a non-empty list")

            seen_configs: set[str] = set()
            supported_configs: list[str] = []
            for config_index, config in enumerate(group_configs, start=1):
                config_context = f"{group_context} attribute 'configs', item {config_index}"
                if not isinstance(config, str) or not (config := config.strip()):
                    raise ValueError(f"{config_context} must be a non-empty string")
                tags = self._benchmarkConfigTags(config, config_context, benchmark_configs_cache)
                if config in seen_configs:
                    raise ValueError(f"{group_context} contains duplicate config name {config!r}")
                seen_configs.add(config)
                if self.arch and tags and self.arch not in tags:
                    self.console.warning(
                        f"Config '{config}' is not supported for the current architecture "
                        f"(--tag={self.arch} mismatch)"
                    )
                    continue
                supported_configs.append(config)

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

            only_in_patches = (
                self._validatedConfigPatchNames(
                    raw_group["only_in_patches"],
                    "only_in_patches",
                    group_context,
                    patch_set_names,
                )
                if "only_in_patches" in raw_group
                else None
            )
            if only_in_patches is not None and not only_in_patches:
                self.console.warning(
                    f"Group '{name}' was fully omitted; attribute 'only_in_patches' "
                    "contains no defined patch set names"
                )
                continue
            applicable_group_names.add(name)

            eager_in_patches = (
                self._validatedConfigPatchNames(
                    raw_group["eager_in_patches"],
                    "eager_in_patches",
                    group_context,
                    patch_set_names,
                )
                if "eager_in_patches" in raw_group
                else None
            )
            eager_only_in_patches: frozenset[str] | None = None
            if eager_in_patches:
                if only_in_patches is None:
                    eager_only_in_patches = eager_in_patches
                else:
                    eager_only_in_patches = only_in_patches & eager_in_patches
                    if not eager_only_in_patches:
                        self.console.warning(
                            f"Eager group '{EAGER_GROUP_PREFIX + name}' was fully omitted; "
                            f"{group_context} attributes 'only_in_patches' and "
                            "'eager_in_patches' have an empty intersection"
                        )
                        eager_only_in_patches = None

            if not supported_configs:
                self.console.warning(
                    f"Group '{name}' was fully omitted; "
                    f"no config matches the architecture (--tag={self.arch} mismatch)"
                )
                continue

            groups[name] = {
                "name": name,
                "configs": supported_configs,
                "override_args": serialized_override_args,
                "only_in_patches": only_in_patches,
            }
            if eager_only_in_patches is not None:
                eager_override_args = dict(override_args or {})
                eager_override_args["num_iterations"] = 1
                eager_override_args["use_torch_compile"] = False
                eager_name = EAGER_GROUP_PREFIX + name
                groups[eager_name] = {
                    "name": eager_name,
                    "configs": supported_configs.copy(),
                    "override_args": json.dumps(
                        eager_override_args, allow_nan=False, separators=(",", ":")
                    ),
                    "only_in_patches": eager_only_in_patches,
                }

        if not applicable_group_names:
            raise ValueError(f"{error_prefix} must contain at least one enabled config group")

        self.console.debug(f"Read {len(groups)} config groups from {configs_file}: ", groups)
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

    def readPatches(self, patches_file_value: StrPath | None) -> tuple[Path, list[PatchSet]]:
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
                    raw_patch["target"], _PATCH_TARGETS_BASE_DIR, "target", patch_context
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

        return patches_file, loaded_patch_sets
