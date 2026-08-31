"""
This is a module to feed the results of xDiT benchmarking to `benchstats` CLI utility
for statistical analysis of the results.

Typical use on a bulkbench --results_dir directory is:

```bash
benchstats . --files_parser=bulkbench.parser_JSON \
    --sample_stats 0 100 --always_show_pvalues --filter1=1
```

But there's much more. For the usage details see the documentation in the parser_JSON class
docstring below.

"""

import json
import os

import numpy as np
from benchstats.common import ParserBase

from .benchmark_sources import (
    _ALT_DELIMITER,
    _TIMINGS_FILENAME,
    get_benchmark_sources,
    parse_filter,
)


class parser_JSON(ParserBase):
    def __init__(self, fpath, filter, metrics, debug_log=None) -> None:
        """Initialization of the parser.

        A short recap of use context first:
        - xDiT's /app/.ci/run.py script produces an outputs directory with subdirectories for each
            model, each containing a `timings.json` file with the results of the benchmarking.
        - `benchstats` is a wrapper around a statistical test method that compares two sets of numbers
            (each set contains measured runtime durations of the same code) and tells if results are
            significantly different. Each such a set is called a "benchmark" in `benchstats`
            terminology and is identified by a name.
        - `file1` and `--filter1` arguments of `benchstats` are passed verbatim to
            `fpath` and `filter` parameters of a parser constructor respectively. When a two-source
            mode is used, another parser instance is created with `file2` and `--filter2` arguments
            passed to it.
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
        - `bulkbench` runner structures model results in a directory hierarchy inside --results_dir
            directory like this:
            `<patch_name>/<bench_group_name>/<bench_config_name>/timings.json`.
        - This parser accepts an arbitrary directory as `fpath` (not necessary produced by a
            `bulkbench` runner) assuming it contains `<bench_config_name>/timings.json` files nested
            somewhere deep inside. Then it constructs benchmark names based on the directory
            structure and the value of the `filter` argument, enabling different kinds of N-way
            comparisons.

        Comparison modes:
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
        benchmark name (`fpath` must be a directory having subdirectories). Index counts from the
        most nested directory (in xDiT it's a benchmark config name) upwards.
        For example, bulkbench runner typically produces the following directory structure for a
        project: `results/<patch_name>/<bench_group_name>/<bench_config_name>/timings.json`, so
        assuming one pass `results` as `fpath`, the following filter value will make the following
        comparisons:
        - --filter1=0 is exactly the A.2 case: benchmarks will be named like
            `<bench_config_name>|<patch_name>/<bench_group_name>` which will compare
            configs across all combinations of patches and groups. I.e. for a given model,
            it'll compare all combinations of groups and patches to each other.
        - --filter1=1 adds `<bench_group_name>` to the benchmark name (with `<bench_config_name>`
            already being there by default, like it was --filter1=0,1), naming benchmarks like
            `<bench_config_name>/<bench_group_name>|<patch_name>` comparing the same
            model+group_name combination across patches.
        - --filter1=2 adds `<patch_name>` to the benchmark name (with `<bench_config_name>`
            already being there by default, like it was --filter1=0,2), naming benchmarks like
            `<bench_config_name>/<patch_name>|<bench_group_name>` comparing the same models+patch
            combination across groups (not useful if a single config isn't a member of
            multiple groups).
        - --filter1=1,2 (or --filter1=0,1,2) makes everything count as an entifier of an
            entity under a benchmark, and that will break the name pooling, because a single
            benchmark won't have an alternative to compare against. However, if the `fpath`
            refers to a dir with the following structure:
            `<platform_name>/<patch_name>/<bench_group_name>/<bench_config_name>/timings.json`, this
            filter value will allow to compare results of the same combinations of
            <patch_name>/<bench_group_name>/<bench_config_name> across platforms. (Remember that
            `benchstats` supports independent modification of read benchmark names using regular
            expressions in --from, --to arguments. This could be handy if one wants to
            compare results of different configs: for example, `flux2.quantgemm.gfx942` vs
            `flux2.quantgemm.gfx950` can't be compared directly since names differ, but we
            can simply strip `gfx..` suffixes with `--from \\.gfx\\d+ --to ""` arguments to enable
            the comparison.)

        Since, bulkbench runner produce eager mode results in a separate group directory starting
        with "eager_", such results are ignored by the statistical analysis.

        Note that directory symlinks are supported, so you can narrow the scope of comparisons by
        crafting a top-level directory with symlinks to the actually interesting results directories
        and analyzing such a directory instead.
        """

        assert metrics == ["real_time"], (
            "Only default metrics are supported for xDiT SingleModel parser"
        )

        filter_indices = parse_filter(filter)
        fpath = os.fspath(fpath)
        is_direct_file = os.path.isfile(fpath)
        has_immediate_timings = os.path.isfile(os.path.join(fpath, _TIMINGS_FILENAME))
        self.alt_delimiter = (
            None
            if filter_indices is None and (is_direct_file or has_immediate_timings)
            else _ALT_DELIMITER
        )

        sources = get_benchmark_sources(fpath, filter_indices, debug_log)
        if debug_log:
            debug_log.debug("parser_JSON: reading the following benchmarks:", sources)

        self.stats = {}
        warned = False
        for bmname, result_dir in sources:
            json_path = fpath if is_direct_file else os.path.join(result_dir, _TIMINGS_FILENAME)
            with open(json_path, "r") as file:
                data = json.load(file)
                if len(data) > 2:
                    if not warned:
                        warned = True
                        lgr = debug_log.debug if debug_log else print
                        lgr(
                            "parser_JSON: Dropping 2 first elements of each latency set "
                            "to ignore first 3 iterations (in total) as a warmup."
                        )
                        lgr = None
                    data = data[2:]
            assert bmname not in self.stats, f"Benchmark {bmname} already exists"  # sanity check
            self.stats[bmname] = {"real_time": data}

    def getStats(self) -> dict[str, dict[str, np.ndarray]]:
        return self.stats

    def getAltDelimiter(self) -> str | None:
        return self.alt_delimiter
