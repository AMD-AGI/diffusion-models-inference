# Batch size and data parallel sweep tooling

Generate batch size and data parallel degree sweep configs from benchmark YAMLs and run them.

## 1. Generate configs

Sweep values can be specified as a range (`MIN MAX STEP`)

```bash
# Generates: flux.usp.bs1, flux.usp.bs2, flux.usp.bs3, flux.usp.bs4
python /app/.ci/tools/generate_sweep_configs.py \
  --sweep flux.usp batch_size 1 4 1 \
  --output /outputs/sweep_config.yaml
```
or an explicit list (`'[1,2,4,8]'`):
```bash
# Generates: flux.usp.dp1, flux.usp.dp2, flux.usp.dp4, flux.usp.dp8
python /app/.ci/tools/generate_sweep_configs.py \
  --sweep flux.usp data_parallel_degree '[1,2,4,8]' \
  --output /outputs/sweep_config.yaml
```

Sweep DP with a fixed batch size using JSON overrides:

```bash
# Generates: flux.usp.dp1.bs4, flux.usp.dp2.bs4, flux.usp.dp4.bs4, flux.usp.dp8.bs4
python /app/.ci/tools/generate_sweep_configs.py \
  --sweep flux.usp data_parallel_degree '[1,2,4,8]' '{"batch_size": 4}' \
  --output /outputs/sweep_config.yaml
```

Multiple sweeps (even across different base configs) in a single invocation:

```bash
# Generates: flux.usp.bs1-bs4, flux.single_gpu.dp1, dp2, dp4 (7 experiments total)
python /app/.ci/tools/generate_sweep_configs.py \
  --sweep flux.usp batch_size 1 4 1 \
  --sweep flux.single_gpu data_parallel_degree '[1,2,4]' \
  --output /outputs/sweep_config.yaml
```

### Syntax

- `--sweep NAME PARAM '[list]'` or `--sweep NAME PARAM MIN MAX STEP` — repeatable, with optional trailing `'JSON_OVERRIDES'`
- `--output` — output YAML path (default: `/outputs/sweep_config.yaml`)
- `configs` — positional, benchmark YAML directory (default: `/app/.ci/benchmark_configs`)

## 2. Run benchmarks

```bash
python /app/.ci/run.py --csv-output-path results/results.csv /outputs/sweep_config.yaml
```

## 3. Plot results

Plotting can be done externally using `plot_sweep_data.py` from the source repository:

```bash
python plot_sweep_data.py \
  --input MI300 results_mi300.csv \
  --input Hopper results_hopper.csv \
  --output plots/
```

- `--input LABEL PATH` — repeatable. Experiment names must end with `.bs{N}`, `.dp{N}`, or combined suffixes like `.dp{N}.bs{M}`.
- `--output` — output directory (default: first input's directory)
- With two inputs, adds percentage-difference subplots using (A-B)/B x 100%.
