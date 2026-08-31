# Bulk Benchmarking Driver with Statistical Results Analysis for xDiT

`bulkbench` is a standalone CLI tool that:

- runs a single-machine benchmarking project over arbitrary combinations of xDiT model
  configurations and implementation changes expressed as standard patch files;
- analyzes the resulting latencies for statistical significance with the `benchstats` package. The
  supplied `timings.json` parser also lets run that analysis separately, on any combination of
  model output directories.

## Installation

There's no `pip` release yet, so install from a local checkout:

```bash
pip install .
```

or straight from the repo:

```bash
pip install git+https://github.com/AMD-AGI/diffusion-models-inference.git#subdirectory=bulkbench
```

Run `bulkbench -h/--help` for CLI help.

## Typical Workflow Example

Say you need to measure how several code changes affect performance in isolation: each has to be
tested on its own before you can tell which change set is fastest overall. By hand that's tedious,
error-prone, needs constant supervision and isn't reproducible - repeating the setup on another
machine means starting over. Across many models, code variants and machines it becomes
infeasible.

`bulkbench` automates it: describe the models and code changesets once, launch the project
(possibly on several machines), and the tool does the rest, including a robust statistical
comparison of the results.

The high-level workflow is:

1. Pick a directory for the project files. It needs at least:

   - `configs.yaml`, describing the grouped model configurations to benchmark;
   - `patches.yaml`, describing the patch sets to apply to any files on the system
     - (this assumes patching is enough to change run-time behavior, so compiled code isn't
       supported yet, though that's easy to add if needed);
   - optionally, the `.patch` files with the exact changes.

   These 3 entities constitute your project. Keep it in git for sharing and version control - just
   `.gitignore` the results, report and backup subdirectories, which by default also live in the
   project dir.

2. Run `bulkbench` in the project dir, or point it there with `--project_dir` (other CLI arguments
   could override everything else). It validates the files, dry-runs patch application so it won't
   fail later, and launches the `/app/.ci/run.py` runner for each model configuration under each
   patch set. From here the session needs no attention.

3. On completion you get a full console report and several new subdirectories in the project dir
   (unless you overrode their location):

   - `results` - a nested tree of each model's output under each patch set: essentially standard
     `/output` directories grouped under the respective patch_set/benchmark_group subdirectory.
   - `report` - one or more `*.html` reports on the statistical significance of the measured
     performance differences (also printed to the console). Examples:
       - [benchstats-fix-groups.html](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/report/benchstats-fix-groups.html)
       - [run-to-run.html](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/report/run-to-run.html)
   - `_backups` - appears if something prevented `bulkbench` from reverting the patched files (a
     killed process, say); holds the original copies plus their paths for manual restoration.

   Now you can inspect the generated media for artifacts and examine the performance report.

4. Optionally fix the system after failures, or update `configs.yaml` or `patches.yaml`, and rerun.
   `bulkbench` scans `results` and will NOT rerun configs that already have results, i.e. at least
   one media file plus `timings.json` in the config directory. A rerun overwrites only the
   comparison reports in `--report_dir`, which must account for the new data.

   - To force a rerun of a benchmark config, config group or patch set, delete its subdirectory
     under `--results_dir` first.
   - To regenerate everything the project produces on the current machine, use
     `--regenerate_results` (`-r`).

5. To run the whole project on another machine, copy the project files (`configs.yaml`,
   `patches.yaml` and the patch files) there and run the tool.

6. To compare latencies across platforms, say MI300 vs MI350, create a directory and copy or
   symlink each platform's `--results_dir` directory into it as `MI300` and `MI350` respectively. Then run the `benchstats` comparison utility manually:

   ```bash
   benchstats {directory} --files_parser=bulkbench.parser_JSON \
       --sample_stats 0 100 --always_show_pvalues
   ```

7. A machine can't always be properly quiesced, so false positives happen and statistical analysis
   is no silver bullet. It's therefore a very good idea to rerun the project on the same machine
   into a different `--results_dir` and `--report_dir`, then compare the same benchmarks across the
   two runs: this shows how noisy the results are, how much to trust them, and whether something
   simply needs a rerun. The easiest way:

   ```bash
   # first run, with results and report in separate dirs
   bulkbench --results_dir results/run1 --report_dir report/run1
   # second run, with its own outputs
   bulkbench --results_dir results/run2 --report_dir report/run2

   # now compare the same benchmarks across the runs
   benchstats results --files_parser=bulkbench.parser_JSON --always_show_pvalues --filter1=2
   ```

   You'd get something like:

   ```text
   │ flux.usp/00_pyt_baseline | run1/a vs run2/a    │ 777.8ms > 772.7ms {-0.7%} p=0.00001 (27 vs 27) │
   ```

   which says you should be suspicious of differences smaller than ~1% on that machine.

## Details

### Format of `--configs_file`

A YAML file listing objects that describe benchmark config groups. Each group represents a single
invocation of the `/app/.ci/run.py` runner and accepts:

- `name` (required) - the group name: unique within the file, matching
  `[-a-zA-Z0-9_+={}., ~!()\[\]]+` after stripping, and neither `.`, `..`, nor starting with the
  reserved `eager_` prefix. Prefer short names for groups holding only configs not used elsewhere.

- `configs` (required) - a non-empty list of benchmark config names to execute, each passed as the
  `--name` argument to the runner.

    Names follow the `<model>.<variant>[.<arch>]` convention, where `<model>` is the basename of the
    yaml file in `/app/.ci/benchmark_configs/` describing all of the model's configurations.
    `bulkbench` resolves every name to its yaml file, extracts the config definition, and checks the
    current GPU architecture (autodetected on AMD, overridable with `--arch`) against the config's
    tags; a config whose tags don't match is ignored with a warning. So, as long as the names
    reference real configs, you may list configs for as many architectures as the project needs.

- `override_args` (optional) - key-value overrides of specific settings for every config in the
  group, such as `num_iterations: <number>`.
- `enabled` (optional) - whether to use the group; defaults to `true`. Valid values are unquoted
  YAML `true`/`false`, the standard `yes`/`no` and `on`/`off` aliases, integers 1/0, and the quoted
  `"true"`, `"false"`, `"1"`, `"0"`. Disabled groups are omitted without validating their other
  attributes.
- `only_in_patches` (optional) - restricts the group to the listed patch sets. Names absent from the
  `--patches_file` are ignored with a warning; a present but empty list disables the group
  completely, and an absent field means 'run on all patch sets'.
- `eager_in_patches` (optional) - additionally runs the group's configs in eager mode in the listed
  patch sets; once unknown names are stripped, an empty list is equivalent to an absent field.
  Internally it creates an extra config group, `eager_<group_name>`, inheriting `configs` from its
  parent (which affects the `--results_dir` layout). The eager group's `only_in_patches` is the
  intersection of the parent's `only_in_patches` and `eager_in_patches` (an empty intersection is an
  error); its `override_args` are the parent's with `num_iterations` set to 1 and
  `use_torch_compile` to `false`. Performance results of eager groups are ignored by the statistical
  analysis.

### Format of `--patches_file`

A YAML file with a non-empty list of patch sets. Each patch set requires:

- `name` - the patch set name: unique within the file, matching
  `[-a-zA-Z0-9_+={}., ~!()\[\]]+` after stripping, and not `.` or `..`,
- `patches` - a list of patch objects, each patching a single file. An object may occur only once in
  its set, and the patch lists themselves must be unique across the file regardless of object order;
  only one empty baseline is allowed.

Each patch object has the following attributes:

- `patch` (required) - a path to the patch file; relative paths are resolved under
  `{--project_dir}/{patch set name}`, absolute ones are used as-is. Generate it with
  `git diff > changes.patch`, `diff -u original_file modified_file > changes.patch` or similar.
  **Using a patch file that modifies several files is undefined behavior (UB)!**
- `target` (required) - a path to the file the `patch` applies to; relative paths are resolved under
  the `/app` directory, absolute ones are used as-is. Both files must exist. **Applying several
  patches to the same target file is UB.**
- `enabled` (optional) - whether to apply the patch; defaults to `true`. Accepts the same values as
  the config group's `enabled` above. Disabled patches are omitted without validating their other
  attributes.

### Benchmarks Statistical Testing

Once the project finishes, `bulkbench` automatically runs pairwise comparisons of the measured
latencies for statistical significance using `benchstats` tool.

#### How to Interpret `benchstats` Output

When `bulkbench` produces a statistical significance report, or when using the suggested CLI
commands, `benchstats` yields a table with rows that looks about like this:

```
...
│ flux.usp/a | 00_original vs 10_full_timings       │ 726.4ms < 732.1ms {+0.8%} [720.6m,748.2m] < [725.1m,826.8m] {+0.6%,+10.5%} p=0.00005 (30 vs 30) │
│ flux.usp/a | 00_original vs 20_barrier_start      │ 726.4ms < 729.0ms {+0.4%} [720.6m,748.2m] < [723.9m,732.6m] {+0.4%,-2.1%} p=0.00000+(30 vs 30)  │
│ flux.usp/a | 10_full_timings vs 20_barrier_start  │ 732.1ms ~ 729.0ms {-0.4%} [725.1m,826.8m] ~ [723.9m,732.6m] {-0.2%,-11.4%} p=0.05980 (30 vs 30) │
...
```

The leftmost column contain a benchmark comparison name. It's generated from relative paths of two
`timings.json` files being compared by a statistical test (usually it's the most suitable for
benchmarks [Brunner Munzel Test](https://en.wikipedia.org/wiki/Brunner_Munzel_Test)). A comparison
name has two parts: before the pipe `|` character goes a name of the entity under a benchmark, and
after it goes two alternatives that are compared one to the other. In the example above, the name of
entity under benchmark is `flux.usp` config from config group `a`, and comparisons are between
patches named `00_original` vs `10_full_timings` vs `20_barrier_start`. Lot's of variants of
comparisons are also possible and the next section describes that in details.

The next column contain the result of a given comparison and some statistical information about
underlying data samples. The result of statistical test is encoded in the symbol between the first
two numbers (and set of numbers in square braces): `<`, `>`, or `~` for equivalency. Output in
console and html report file are also colored depending on the result of the test.

For the first row of the example above, assuming that `A` is a set of numbers from `timings.json`
of the first alternative (`00_original/a/flux.usp/timings.json`), and `B` is the set of number from
`timings.json` of the second alternative (`10_full_timings/a/flux.usp/timings.json`):

- `726.4ms < 732.1ms {+0.8%}` just show means of `A` and `B` separated by the result of the
  statistical test (yes, it's entirely possible to see results like `2 < 1`!), and the number in
  curly `{}` brackets is simply their relative difference, i.e. $ \frac{mean(B) - mean(A)}{mean(A)}*100\% $
- The numbers in square brackets `[]` show values of respective percentiles (requested in
  `--sample_stats` argument) for both sets. So for `--sample_stats 0 100`, values `[720.6m,748.2m]`
  are simply a \[`min(A)`,`max(A)`\] for the set `A`, and `[725.1m,826.8m]` are the same for the `B`.
  Two numbers in curly `{}` brackets after that are simply a relative difference between these
  numbers respectively (not having `s`-seconds suffix, since it's the same everywhere) (so,
  for example, the last number `+10.5%` is simply $ \frac{826.8-748.2}{748.2}*100\% $)
- `p=0.00005` is p-value for the comparison (mainly for informational purposes, see the last section
  of this readme)
- Finally, the numbers in round `()` brackets show how many samples set `A` and set `B` respectively
  had, i.e. (`A.size` vs `B.size`)


#### Custom Comparisons

This section explains how to craft practically any comparison your project needs. If comparisons
the tool make by default are enough for you, you can skip this section.

The backbone of the testing and report generation is the `benchstats` CLI tool - essentially a fancy
wrapper around `scipy` statistical tests that takes two sets of numbers (each holding measured
runtime durations of some code) and tells whether they differ significantly. Such a set is
called a "benchmark" in `benchstats` terminology and is identified by a name. `benchstats` itself
knows nothing about how the `/app/.ci/run.py` runner stores latencies, or which latencies to compare
against which, but it offers a standard interface for data source parsers plus a few conventions
that, once satisfied, let you arrange a statistical comparison of any source data.

The `bulkbench.parser_JSON` module implements such a parser. It handles both an individual
`timings.json` file generated by the runner for a single benchmark config, and an arbitrary nested
tree of such files, deriving benchmark names from the directory names in their paths.

For example, a single run of a `bulkbench` project produces this hierarchy inside `--results_dir`:

  `<patch_name>/<bench_group_name>/<bench_config_name>/timings.json`.

so the command

```bash
benchstats {--results_dir} --files_parser=bulkbench.parser_JSON \
    --sample_stats 0 100 --always_show_pvalues --export_to=all_to_all.html
```

compares each `<bench_config_name>` with every other identically named config in the tree, across different `<patch_name>`s and `<bench_group_name>`s, and saves the report to `all_to_all.html` in
the current directory. For instance, this `--results_dir` tree:

```
.
├── baseline
│   ├── group1
│   │   ├── modelA
│   │   └── modelB
│   └── group2
│       └── modelA
└── code_patch
    ├── group1
    │   ├── modelA
    │   └── modelB
    └── group2
        └── modelA
```

(each leaf directory holding its own `timings.json`) yields these comparisons:

```
modelA | baseline/group1 vs baseline/group2
modelA | baseline/group1 vs code_patch/group1
modelA | baseline/group1 vs code_patch/group2
modelA | baseline/group2 vs code_patch/group1
modelA | baseline/group2 vs code_patch/group2
modelA | code_patch/group1 vs code_patch/group2
modelB | baseline/group1 vs code_patch/group1
```

`bulkbench` runs this comparison into `{--report_dir}/benchstats-all-to-all.html` when it detects
the same `<bench_config_name>` under different `<bench_group_name>` parent directories; otherwise it
skips the comparison entirely.

That's quite a few comparisons. Sometimes that's exactly what you need, but sometimes comparisons
within the same patch set may be uninteresting. `benchstats` passes its `--filter1` value verbatim
to the parser, and `bulkbench.parser_JSON` reads it as a comma-separated list of directory indices,
counted from `timings.json`, to include in the benchmark name. The default (and always implied)
value is `0`, the directory holding `timings.json`, i.e. `<bench_config_name>`. `--filter1=1`
additionally "freezes" the next hierarchy level, `<bench_group_name>`, which is what we need here:

```
modelA/group1 | baseline vs code_patch
modelA/group2 | baseline vs code_patch
modelB/group1 | baseline vs code_patch
```

`bulkbench` always saves this comparison into `{--report_dir}/benchstats-fix-groups.html`
([example](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/report/benchstats-fix-groups.html)).

Now suppose we have results from different GPUs for the same project and want all the statistics in
one report. We just create a dedicated top-level directory and copy or symlink the `--results_dir`
directories of each `bulkbench` execution into it:

```
.
├── GPU1
│   ├── baseline
│   │   ├── group1
│   │   │   ├── modelA
│   │   │   └── modelB
│   │   └── group2
│   │       └── modelA
│   └── code_patch
│       ├── group1
│       │   ├── modelA
│       │   └── modelB
│       └── group2
│           └── modelA
└── GPU2
    ├── baseline
    │   ├── group1
    │   │   ├── modelA
    │   │   └── modelB
    │   └── group2
    │       └── modelA
    └── code_patch
        ├── group1
        │   ├── modelA
        │   └── modelB
        └── group2
            └── modelA
```

The GPU identifier now sits 3 levels above each `timings.json`, so `--filter1=1,3` gives this neat
comparison:

```
modelA/group1/GPU1 | baseline vs code_patch
modelA/group2/GPU1 | baseline vs code_patch
modelB/group1/GPU1 | baseline vs code_patch

modelA/group1/GPU2 | baseline vs code_patch
modelA/group2/GPU2 | baseline vs code_patch
modelB/group1/GPU2 | baseline vs code_patch
```

`--filter1=1,2` instead "freezes" the patch set names and "unfreezes" the GPU identifiers, giving
comparisons across GPUs:

```
modelA/group1/baseline   | GPU1 vs GPU2
modelA/group2/baseline   | GPU1 vs GPU2
modelA/group1/code_patch | GPU1 vs GPU2
modelA/group2/code_patch | GPU1 vs GPU2
modelB/group1/baseline   | GPU1 vs GPU2
modelB/group1/code_patch | GPU1 vs GPU2
```

As mentioned above, the same filter is useful for sanity checking of benchmarking results: you can run the same
project twice or more times on the same machine and then compare these runs one to the other to identify systematically
biased results (this happens when the machine was busy doing something else when the benchmark was run, - you can't
typically notice that in another way). See [run-to-run.html](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/report/run-to-run.html) for an example.

#### A Word of Warning

The core assumption of statistical tests is that individual samples are **independent** (of each
other) and are **identically distributed** (i.e. generated by the same "generator"), i.e. the
**IID assumption**. Only when it holds, the p-values have expected meaning. But unless you're
extremely careful in how you do benchmarking, these assumptions are broken:

- samples are typically NOT independent: something running in parallel while samples N and N+1 are
measured, "lengthens" the measured latencies and this makes them interdependent (knowing the one
was "lengthened" automatically mean the other was too)
- and for the same reason, samples are NOT identically distributed, since a parallel running
something injects a bias into measurements, which mean it was generated with a different "generator".

So the p-values alone doesn't mean much, and you shouldn't blindly trust results of statistical
significance test alone. Even if you tighten `alpha` to a mind blowingly small value, like `1e-6`,
your false positive rate will not be one in a million, - it'll be order of magnitudes higher.

So the single by far the most important thing for benchmarking is a properly quiesced machine and you
controlling the determinism of execution, sanity of all measurements and lack of outliers.
