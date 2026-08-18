"""
This is a module to feed the results of xDiT benchmarking to `benchstats` CLI utility
for statistical analysis of the results.



"""

import json
import os

import numpy as np
from benchstats.common import ParserBase


_TIMINGS_FILENAME = "timings.json"
_ALT_DELIMITER = "|"


def _warn(message, debug_log=None) -> None:
    if debug_log is not None:
        debug_log.warning(message)
    else:
        print(message)


def _parse_filter(filter) -> set[int] | None:
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


def get_benchmark_sources(fpath: str, filter: str | None, debug_log=None) -> set[tuple[str, str]]:
    """Return ``(benchmark name, result directory)`` pairs selected by the inputs."""
    filter_indices = _parse_filter(filter)
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

    selected_indices = filter_indices if filter_indices is not None else {0}

    for current_dir, child_dirs, files in _walk_directories_following_symlinks(fpath):
        current_timings = os.path.join(current_dir, _TIMINGS_FILENAME)
        has_immediate_timings = _TIMINGS_FILENAME in files and os.path.isfile(current_timings)
        nested_timings = sorted(
            timing
            for child_dir in child_dirs
            for timing in subtree_timings.get(os.path.join(current_dir, child_dir), [])
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
            ([current_timings] if has_immediate_timings else []) + nested_timings
        )

        if not subtree_timings[current_dir]:
            directories_without_timings.append(current_dir)

        if not has_immediate_timings:
            continue

        relative_dir = os.path.relpath(current_dir, fpath)
        path_parts = [] if relative_dir == os.curdir else relative_dir.split(os.sep)

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
                "The following directories don't contain an immediate or nested timings.json:",
                directories_without_timings,
            ),
            debug_log,
        )

    if directories_rejected_by_filter:
        _warn(
            _format_warning(
                "these paths containing timings.json can't have the given "
                f"filter '{filter}' be applied to them:",
                directories_rejected_by_filter,
            ),
            debug_log,
        )

    if filter_indices is None and os.path.isfile(os.path.join(fpath, _TIMINGS_FILENAME)):
        return {(os.path.basename(os.path.abspath(fpath)), fpath)}

    if not benchmarks:
        raise ValueError(f"No benchmarks were found under {fpath!r}")

    return benchmarks


class parser_JSON(ParserBase):
    def __init__(self, fpath, filter, metrics, debug_log=None) -> None:
        """Initialization of the parser.

        A short recap of use context first:
        - xDiT's /app/.ci/run.py script produces an outputs directory with subdirectories for each
        model, each containing a `timings.json` file with the results of the benchmarking.
        - `benchstats` is a wrapper around a statistical test that compares two sets of numbers
        (each set contains measured runtime durations of the same code) and tells if results are
        significantly different. Each such a set is called a "benchmark" in `benchstats` terminology
        and is identified by a name.
        - `file1` and `--filter1` (or `--filter`) arguments of `benchstats` are passed verbatim to
        `fpath` and `filter` parameters of a parser constructor respectively. When a two-source mode
        is used, another parser instance is created with `file2` and `--filter2` (or `--filter`)
        arguments passed to it.
        - `benchstats` has two modes to find benchmarks to compare one against the other among
        all benchmarks it sees:
            1. find same benchmark names in two different sources (two-source mode)
            2. pool all benchmarks into several disjoint sets, find matching benchmarks in each set
            and compare them pairwise (single-source mode). This enables N-way comparisons, but
            a single source (parser/loader object) must return all benchmarks at once. This work
            thanks to a convention that each benchmark name returned by a parser/loader is composed
            of two parts with a configurable separator (typically a pipe `|` symbol) between them:
                - an identifier of an entity under a benchmark (such as a model name)
                - an identifier of a benchmark configuration (such an identifier of a set of flags
                used to get particular benchmark result, or software stack versions, or anything
                you'd like to vary and compare how it influences the results)
            For example, this set of benchmark names:
                {"bm1|var1", "bm1|var2", "bm2|opt1", "bm2|opt2", "bm2|opt3"}
            Makes `benchstats` to do the following 4 comparisons:
                - bm1|var1 vs bm1|var2 (shown as bm1 | var1 vs var2)
                - bm2|opt1 vs bm2|opt2 (shown as bm2 | opt1 vs opt2)
                - bm2|opt1 vs bm2|opt3 (shown as bm2 | opt1 vs opt3)
                - bm2|opt2 vs bm2|opt3 (shown as bm2 | opt2 vs opt3)
            So by choosing how you name the benchmarks the parser returns, you control what
            `benchstats` will compare against what.

        This parser enables the following comparisons depending on user's inputs:
        A. when `filter` argument isn't set or empty:
        A.1. When `fpath` is a single `.json` file, or it's a directory having an immediate child
        `timings.json`, the parser enables in a two-source mode, i.e. user has to supply two such
        sources to benchstats CLI, and two such parser objects will create a single benchmark each,
        and benchstats will compare them against each other.
        A.2 Otherwise, the `fpath` must be a directory having subdirectories, with `timings.json`
        file being nested somewhere deep inside `path/to/model1/timings.json`, the parser enables a
        single-source mode and for each `timings.json` it finds, it creates a single benchmark named
        `model1|path/to`. There'll be as many comparisons as there are same named directories having
        `timings.json` as an immediate child.

        B. when `filter` argument is set, it must be a comma separated set of numbers each of which
        denoting an index of a nested subdirectory to include in the identifier of the entity under
        benchmark name (`fpath` must be a directory having subdirectories). Index counts from the most
        nested directory (in xDiT it's a benchmark config name) upwards.
        For example, bulkbench runner typically produces the following directory structure for a
        project: `results/<patch_name>/<bench_group_name>/<bench_config_name>/timings.json`, so
        assuming one pass `results` as `fpath`, the following filter value will make the following
        comparisons:
        - --filter=0 is exactly the A.2 case: benchmarks will be named like
        `<bench_config_name>|<patch_name>/<bench_group_name>` which will compare configs across all
        combinations of patches and groups. I.e. for a given model, it'll compare all combinations
        of groups and patches to each other.
        - --filter=1 adds `<bench_group_name>` to the benchmark name (with `<bench_config_name>`
        already being there by default, like it was --filter=0,1), naming benchmarks like
        `<bench_config_name>/<bench_group_name>|<patch_name>` comparing the same model+group_name
        combination across patches.
        - --filter=2 adds `<patch_name>` to the benchmark name (with `<bench_config_name>`
        already being there by default, like it was --filter=0,2), naming benchmarks like
        `<bench_config_name>/<patch_name>|<bench_group_name>` comparing the same models+patch
        combination across groups (not useful if a single config isn't a member of multiple groups).
        - --filter=1,2 (or --filter=0,1,2) makes everything count as an entifier of an entity under
        a benchmark, and that will break the name pooling, because a single benchmark won't have
        an alternative to compare against. However, if the `fpath` refers to a dir with the
        following structure:
        `<platform_name>/<patch_name>/<bench_group_name>/<bench_config_name>/timings.json`, this
        filter value will allow to compare results of the same combinations of
        <patch_name>/<bench_group_name>/<bench_config_name> across platforms.

        Note that directory symlinks are supported, so you can narrow the scope of comparisons by
        crafting a top-level directory with symlinks to the actually interesting results directories
        and analyzing such a directory instead.
        """

        assert metrics == ["real_time"], (
            "Only default metrics are supported for xDiT SingleModel parser"
        )

        filter_indices = _parse_filter(filter)
        fpath = os.fspath(fpath)
        is_direct_file = os.path.isfile(fpath)
        has_immediate_timings = os.path.isfile(os.path.join(fpath, _TIMINGS_FILENAME))
        self.alt_delimiter = (
            None
            if filter_indices is None and (is_direct_file or has_immediate_timings)
            else _ALT_DELIMITER
        )

        sources = get_benchmark_sources(fpath, filter, debug_log)
        self.stats = {}
        for bmname, result_dir in sources:
            json_path = fpath if is_direct_file else os.path.join(result_dir, _TIMINGS_FILENAME)
            with open(json_path, "r") as file:
                data = json.load(file)
                if len(data) > 2:
                    print(
                        "Dropping another 2 first elements from the JSON "
                        "to ignore first 3 iterations as warmup"
                    )
                    data = data[2:]
            assert bmname not in self.stats, f"Benchmark {bmname} already exists"  # sanity check
            self.stats[bmname] = {"real_time": data}

    def getStats(self) -> dict[str, dict[str, np.ndarray]]:
        return self.stats

    def getAltDelimiter(self) -> str | None:
        return self.alt_delimiter
