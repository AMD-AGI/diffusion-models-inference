# Batch config generation and plotting

Scripts for defining batch-size sweeps from benchmark configs and plotting latency/throughput from the resulting CSVs.

## 1. Generate batch config (YAML)

`generate_batch_configs.py` reads benchmark configs (e.g. from `.ci/benchmark_configs/`) and outputs a single YAML with one experiment per batch size (e.g. `flux.usp.bs1`, `flux.usp.bs2`, …). Each experiment has `batch_size` and repeated `prompt` set; the YAML is suitable as input to `.ci/run.py`.

```bash
python generate_batch_configs.py \
  --name flux.usp 1 10 \
  --name flux.usp_2k 1 5 \
  --config-dir /app/.ci/benchmark_configs \
  --output /outputs/batch_config.yaml
```

- **`--name NAME MIN MAX`** — Base experiment name (must exist in configs) and inclusive batch-size range. Can be repeated.
- **`--config-dir`** — Directory of benchmark YAMLs (default: `/app/.ci/benchmark_configs`).
- **`--output`** — Output YAML path.

## 2. Run benchmarks

Use `.ci/run.py` with the generated YAML. It will run each experiment and append rows to a results CSV (e.g. `model`, `performance`, `metric` with `metric=latency`).

```bash
python .ci/run.py /outputs/batch_config.yaml
```

The CSV path is set via `--csv-output-path` (default typically under the results directory).

## 3. Plot results

`plot_batch_data.py` reads the results CSV and, for each model, produces one figure: latency and throughput vs batch size. If a second CSV is provided with `--compare`, it adds comparison series (e.g. another architecture) and difference subplots.

```bash
python plot_batch_data.py \
  --input /outputs/results.csv \
  --output-dir /outputs/plots
```

With a comparison CSV (e.g. Hopper vs MI300):

```bash
python plot_batch_data.py \
  --input /outputs/results_mi300.csv \
  --compare /outputs/results_hopper.csv \
  --output-dir /outputs/plots
```

- **`--input`** — Primary results CSV (required). Expects columns `model`, `performance`, `metric`; model names must end with `.bsN` (e.g. `flux.usp.bs4`).
- **`--compare`** — Optional second CSV. Comparison series are matched automatically: any name that is the primary model name plus extra dotted segments (e.g. `flux.usp.hopper` for `flux.usp`, or `flux2.fp8gemm.hopper.h100` for `flux2.fp8gemm`) is plotted on the same figure with difference subplots.
- **`--output-dir`** — Where to save PNGs (default: same directory as the input file).

Output: one PNG per model (e.g. `flux_usp.png`, `flux_usp_2k.png`) with latency and throughput subplots, and when `--compare` is used, additional subplots for latency and throughput difference with formula (A − B)/B × 100%.
