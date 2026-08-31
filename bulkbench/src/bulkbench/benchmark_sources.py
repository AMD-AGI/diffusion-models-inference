"""Discover benchmark result sources from files and directory trees."""

import os

from .bulkbench import EAGER_GROUP_PREFIX


_TIMINGS_FILENAME = "timings.json"
_ALT_DELIMITER = "|"


def _warn(message, debug_log=None) -> None:
    if debug_log:
        debug_log.warning(message)
    else:
        print(message)


def parse_filter(filter) -> set[int] | None:
    if filter is None:
        return None
    if not isinstance(filter, str):
        raise TypeError("Filter must be a comma-separated string of non-negative integers")

    filter = filter.strip()
    if not filter:
        return None

    indices: set[int] = {0}
    for value in filter.split(","):
        value = value.strip()
        if not value or not value.isdecimal():
            raise ValueError(
                f"Invalid filter {filter!r}; expected comma-separated non-negative integers"
            )
        indices.add(int(value))
    return indices


def _format_warning(message: str, paths: list[str]) -> str:
    return message + "\n" + "\n".join(f"- {path}" for path in sorted(paths))


def _walk_directories_following_symlinks(fpath: str):
    """Yield directories bottom-up while following symlinks without entering cycles."""
    entries = []
    ancestors_by_path: dict[str, frozenset[tuple[int, int]]] = {fpath: frozenset()}

    for current_dir, child_dirs, files in os.walk(fpath, topdown=True, followlinks=True):
        ancestors = ancestors_by_path.get(current_dir, frozenset())
        stat = os.stat(current_dir)
        current_identity = (stat.st_dev, stat.st_ino)
        current_ancestors = ancestors | {current_identity}

        traversable_children = []
        for child_dir in child_dirs:
            child_path = os.path.join(current_dir, child_dir)
            try:
                child_stat = os.stat(child_path)
            except OSError:
                continue
            child_identity = (child_stat.st_dev, child_stat.st_ino)
            if child_identity in current_ancestors:
                continue
            ancestors_by_path[child_path] = current_ancestors
            traversable_children.append(child_dir)

        child_dirs[:] = traversable_children
        entries.append((current_dir, traversable_children, files))

    yield from reversed(entries)


def get_benchmark_sources(
    fpath: str,
    filter_indices: set[int] | None,
    debug_log=None,
    ignore_eager: bool = True,
) -> set[tuple[str, str]]:
    """Return ``(benchmark name, result directory)`` pairs for the directories under ``fpath``.

    When ``ignore_eager`` is true, omit nested result directories whose immediate
    parent name starts with ``eager_``  (``bulkbench.EAGER_GROUP_PREFIX``).
    """
    fpath = os.fspath(fpath)

    if os.path.isfile(fpath):
        if filter_indices is not None:
            raise ValueError("A filter can only be applied when fpath is a directory")
        if os.path.splitext(fpath)[1].lower() != ".json":
            raise ValueError(f"Expected a JSON file, got {fpath!r}")
        result_dir = os.path.dirname(fpath) or os.curdir
        return {(os.path.basename(os.path.abspath(result_dir)), result_dir)}

    if not os.path.isdir(fpath):
        raise ValueError(f"fpath is neither a file nor a directory: {fpath!r}")

    benchmarks: set[tuple[str, str]] = set()
    directories_without_timings: list[str] = []
    directories_rejected_by_filter: list[str] = []
    subtree_timings: dict[str, list[str]] = {}
    subtrees_with_ignored_timings: set[str] = set()

    selected_indices = filter_indices if filter_indices is not None else {0}

    for current_dir, child_dirs, files in _walk_directories_following_symlinks(fpath):
        relative_dir = os.path.relpath(current_dir, fpath)
        path_parts = [] if relative_dir == os.curdir else relative_dir.split(os.sep)
        current_timings = os.path.join(current_dir, _TIMINGS_FILENAME)
        has_immediate_timings = _TIMINGS_FILENAME in files and os.path.isfile(current_timings)
        ignored_immediate_timings = (
            has_immediate_timings
            and ignore_eager
            and len(path_parts) >= 2
            and path_parts[-2].startswith(EAGER_GROUP_PREFIX)
        )
        has_immediate_timings = has_immediate_timings and not ignored_immediate_timings
        nested_timings = sorted(
            timing
            for child_dir in child_dirs
            for timing in subtree_timings.get(os.path.join(current_dir, child_dir), [])
        )
        has_ignored_nested_timings = any(
            os.path.join(current_dir, child_dir) in subtrees_with_ignored_timings
            for child_dir in child_dirs
        )

        if has_immediate_timings and nested_timings:
            if len(nested_timings) == 1:
                raise ValueError(
                    "The directory is malformed and contains 2 timings.json files, "
                    f"an immediate and a nested in {nested_timings[0]}"
                )
            raise ValueError(
                f"The directory is malformed and contains {len(nested_timings) + 1} "
                "timings.json files, an immediate and nested in " + ", ".join(nested_timings)
            )

        subtree_timings[current_dir] = (
            [current_timings] if has_immediate_timings else []
        ) + nested_timings
        if ignored_immediate_timings or has_ignored_nested_timings:
            subtrees_with_ignored_timings.add(current_dir)

        if not subtree_timings[current_dir] and current_dir not in subtrees_with_ignored_timings:
            directories_without_timings.append(current_dir)

        if not has_immediate_timings:
            continue

        if not path_parts:
            if filter_indices is not None:
                directories_rejected_by_filter.append(current_dir)
            continue

        if max(selected_indices) >= len(path_parts):
            directories_rejected_by_filter.append(current_dir)
            continue

        entity_parts = [path_parts[-index - 1] for index in sorted(selected_indices)]
        alternative_parts = [
            part
            for position, part in enumerate(path_parts)
            if len(path_parts) - position - 1 not in selected_indices
        ]
        benchmark_name = "/".join(entity_parts) + _ALT_DELIMITER + "/".join(alternative_parts)
        benchmarks.add((benchmark_name, current_dir))

    if directories_without_timings:
        _warn(
            _format_warning(
                "The following directories don't contain an immediate or nested timings.json, and are ignored:",
                directories_without_timings,
            ),
            debug_log,
        )

    if directories_rejected_by_filter:
        _warn(
            _format_warning(
                "These paths containing timings.json can't have the given "
                f"filter '{filter_indices}' be applied to them, and are ignored:",
                directories_rejected_by_filter,
            ),
            debug_log,
        )

    if filter_indices is None and os.path.isfile(os.path.join(fpath, _TIMINGS_FILENAME)):
        return {(os.path.basename(os.path.abspath(fpath)), fpath)}

    if not benchmarks:
        raise ValueError(f"No benchmarks were found under {fpath!r}")

    return benchmarks
