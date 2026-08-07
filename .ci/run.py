import argparse
from collections import defaultdict
import logging
import yaml
import os
import json
import importlib.util
import sys
import csv
import shlex
import time
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import List, Dict, Any, Optional
import subprocess

import numpy as np
import pandas as pd

from huggingface_hub import snapshot_download, scan_cache_dir, DryRunFileInfo

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(
    fmt='%(asctime)s - %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
))
logger.addHandler(_handler)

# Models whose YAML "model" field is a registered alias (not a real HF repo ID)
# and which pull weights from multiple underlying HF repos at load time.
# Each entry maps the alias to list of real repos to download/cache/delete.
_COMPOSITE_MODEL_REPOS: Dict[str, List[str]] = {
    "Hunyuanvideo-1.5-Sparse": [
        "tencent/HunyuanVideo-1.5",
        "hunyuanvideo-community/HunyuanVideo-1.5-Diffusers-720p_i2v_distilled",
    ],
}


@dataclass
class Experiment:
    name: str
    tags: List[str]
    runner: str
    model: str
    args: Dict[str, Any]
    entrypoint: Optional[str] = None
    num_gpus: Optional[int] = None
    revision: Optional[str] = None


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Run benchmarks based on model, benchmark name, or tags",
    )

    selection_group = parser.add_mutually_exclusive_group()
    selection_group.add_argument(
        "--name",
        action="append",
        dest="names",
        help="Run only experiments whose name matches one of these names",
        default=[],
    )
    selection_group.add_argument(
        "--tag",
        action="append",
        dest="tags",
        help="Optional tag to filter benchmarks",
        default=[],
    )
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--clear-model-cache",
        action="store_true",
        help="Always remove model cache after finishing all model specific experiments. Default: preserve original state (delete only if we downloaded it).",
    )
    cache_group.add_argument(
        "--no-clear-model-cache",
        action="store_true",
        help="Never remove model cache (keep it after runs). Default: preserve original state (delete only if we downloaded it).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them",
    )
    parser.add_argument(
        "--print-csv",
        action="store_true",
        help="Print MAD csv rows",
    )
    parser.add_argument(
        "--override-args-json",
        type=str,
        default="{}",
        help=(
            "JSON dict of extra/override args passed to the benchmark script.\n"
            "Example:\n"
            "  --override-args-json '{\"prompt\": \"My new prompt\", \"seed\": 1234, \"use_cfg_parallel\": true}'"
        ),
    )
    parser.add_argument(
        "--results-directory",
        type=str,
        default="/outputs",
        help="Base directory for benchmark outputs (logs, metrics, artifacts)",
    )
    parser.add_argument(
        "--csv-output-path",
        type=str,
        default="/outputs/results.csv",
        help="Path to the CSV file where benchmark results for MAD will be written"
    )
    parser.add_argument(
        "--export-config-path",
        metavar="PATH",
        help="Export the filtered experiment configurations to a YAML file",
    )
    parser.add_argument(
        "--override-runner",
        type=str,
        required=False,
        help="Override the runner for the experiments",
    )
    parser.add_argument(
        "--override-entrypoint",
        type=str,
        required=False,
        help="Override the entrypoint for the experiments",
    )
    parser.add_argument(
        "--print-timing-summary",
        action=argparse.BooleanOptionalAction,
        help="Print wall-clock timing summary",
    )
    parser.add_argument(
        "--collect-hipblaslt-logs",
        action="store_true",
        help=(
            "Collect hipBLASLt GEMM logs per experiment as "
            "<results-directory>/<experiment_name>/hipblaslt_gemms_pid<PID>.yaml "
            "(one file per worker process). "
            "Adds runtime overhead, intended for ad-hoc GEMM tuning data collection only."
        ),
    )
    parser.add_argument(
        "configs",
        nargs="+",
        help="YAML config files containing experiment definitions",
    )
    return parser.parse_args()


def _is_python_script(path: str) -> bool:
    return Path(path).is_file() and Path(path).suffix == ".py"


def _filter_experiments_by_name(experiments: List[Experiment], names: List[str]) -> List[Experiment]:
    """Filter experiments based on --name arguments."""
    experiments = [
        e for e in experiments
        if e.name in names
    ]

    return experiments


def _filter_experiments_by_tags(experiments: List[Experiment], tags: List[str]) -> List[Experiment]:
    """Filter experiments based on --tag arguments. An experiment must match all tags given."""
    experiments = [
        e for e in experiments
        if all(t in e.tags for t in tags)
    ]   

    return experiments


def _model_in_cache(model: str, revision: Optional[str] = None) -> bool:
    """
    Return True if the given model repo is already in the Hugging Face cache.

    If revision is None, returns True if the model exists with any revision.
    If revision is set, matches by commit hash or by ref (e.g. "main", "refs/pr/123").
    """
    try:
        cache_info = scan_cache_dir()
        repos = getattr(cache_info, "repos", None)
        if repos is None:
            return False
        for repo in repos:
            if getattr(repo, "repo_id", None) != model:
                continue
            if revision is None:
                return True
            # CachedRevisionInfo has commit_hash and refs (e.g. "main", tags)
            revs = getattr(repo, "revisions", ())
            for rev in revs:
                if getattr(rev, "commit_hash", None) == revision:
                    return True
                if revision in getattr(rev, "refs", ()):
                    return True
        return False
    except Exception as e:
        logger.warning("Could not scan cache for %s: %s", model, e)
        return False

def _report_download_dry_run_statistics(result: List[DryRunFileInfo]) -> None:
    n_downloaded_files = sum(1 for dryrun_info in result if dryrun_info.will_download)
    download_size_bytes = sum(dryrun_info.file_size for dryrun_info in result if dryrun_info.will_download)
    download_size_gigabytes = download_size_bytes // (1024 ** 3)
    n_skipped_files = sum(1 for dryrun_info in result if not dryrun_info.will_download)
    logger.info(f"[dry-run] Would have downloaded {n_downloaded_files} files with total size {download_size_gigabytes} GB.")
    logger.info(f"[dry-run] Would have skipped downloading {n_skipped_files} files.")

def _download_model(model: str, revision: Optional[str] = None, dry_run: bool = False) -> None:
    """
    Attempts to download a model from HuggingFace Hub. Skips download if already cached.
    """
    logger.info(f"Downloading model: {model}")
    try:
        cache_dir_or_dry_run_info = snapshot_download(repo_id=model, revision=revision, dry_run=dry_run)
    finally:
        # tqdm progress bars from huggingface_hub leave the cursor on a partially
        # written stderr line (no trailing '\n'), which causes the next log
        # record to render on the same line. Flush a newline so subsequent
        # log output starts cleanly.
        sys.stderr.write("\n")
        sys.stderr.flush()
    if not isinstance(cache_dir_or_dry_run_info, list):
        logger.info(f"Model {model} is stored in {cache_dir_or_dry_run_info}.")
    else:
        _report_download_dry_run_statistics(cache_dir_or_dry_run_info)


def _delete_model_cache(model: str, revision: Optional[str] = None, dry_run: bool = False) -> None:
    """
    Delete the given model (and optionally a specific revision) from the Hugging Face cache.
    Uses scan_cache_dir to find the repo by name; revision can be a ref (e.g. "main") or a commit hash.
    If revision is None, deletes all cached revisions of the model.
    """
    cache_info = scan_cache_dir()
    commit_hashes_to_delete = []
    for repo in getattr(cache_info, "repos", ()):
        if getattr(repo, "repo_id", None) != model:
            continue
        for rev in getattr(repo, "revisions", ()):
            commit_hash = getattr(rev, "commit_hash", None)
            if not commit_hash:
                continue
            if revision is None:
                commit_hashes_to_delete.append(commit_hash)
            elif commit_hash == revision:
                commit_hashes_to_delete.append(commit_hash)
                break
            elif revision in getattr(rev, "refs", ()):
                commit_hashes_to_delete.append(commit_hash)
                break
        break
    if not commit_hashes_to_delete:
        logger.warning("No cache entries found to delete for model %s (revision=%s).", model, revision)
        return
    try:
        delete_strategy = cache_info.delete_revisions(*commit_hashes_to_delete)
        if not dry_run:
            delete_strategy.execute()
            logger.info("Deleted model cache for %s (revision=%s).", model, revision)
        else:
            logger.info(f"[dry-run] Model cache deletion strategy: {delete_strategy}")

    except Exception as e:
        logger.error(e, stack_info=True, exc_info=True)


def _run_experiment(
    exp: Experiment,
    cmd: List[str],
    dry_run: bool,
    benchmark_output_directory: Path,
    collect_hipblaslt_logs: bool = False,
) -> bool:
    """Runs a single experiment."""

    if dry_run:
        logger.info(f"[dry-run] Running command: {shlex.join([str(part) for part in cmd])}")
        return True

    benchmark_output_directory.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TQDM_DISABLE"] = "1"
    if collect_hipblaslt_logs:
        # %i is substituted with the worker process ID by hipBLASLt at runtime,
        # so multi-rank benchmarks (e.g. ulysses_degree > 1) get one file per process.
        env["HIPBLASLT_LOG_MASK"] = "64"
        env["HIPBLASLT_LOG_FILE"] = str(
            benchmark_output_directory / "hipblaslt_gemms_pid%i.yaml"
        )
    stdout_path = benchmark_output_directory / "stdout.txt"
    stderr_path = benchmark_output_directory / "stderr.txt"
    with open(stdout_path, "w", buffering=1) as stdout_file, open(stderr_path, "w", buffering=1) as stderr_file:
        r = subprocess.run(
            cmd,
            stdout=stdout_file,
            stderr=stderr_file,
            text=True,
            env=env,
        )
    if r.returncode != 0:
        logger.info(f"Experiment {exp.name} failed!")
        return False
    else:
        logger.info(f"Experiment: {exp.name} completed successfully.")
        return True


def _export_config(experiments: List[Experiment], export_config_path: str) -> None:
    """
    Serialises the provided experiments to YAML format and writes them
    to the file path specified by the --export-config-path flag.
    """
    logger.info(f"Exporting experiment configurations to {export_config_path}")

    data = [asdict(exp) for exp in experiments]
    with open(export_config_path, "w") as f:
        yaml.dump(data, f, sort_keys=False)


def _get_median_latency(file_path: Path) -> Optional[float]:
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)

            median = np.median(data)
        except Exception as e:
            logger.error(f"Failed to compute median latency from {file_path}: {e}")
            return None

        return median


def _save_mad_latency_metric(csv_output_path: str, experiment_name: str, latency: float):
    """Store results to MAD-formatted CSV"""
    csv_file_path = Path(csv_output_path)
    csv_file_path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = csv_file_path.exists()

    with open(csv_file_path, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)

        if not file_exists:
            writer.writerow(["model", "performance", "metric"])

        writer.writerow([experiment_name, latency, "latency"])


def _print_timing_summary(timing: Dict[str, Any]) -> None:
    """Print a neat summary table of wall-clock times for download_model and experiments."""
    if not timing.get("download_model") and not timing.get("experiments"):
        return
    time_col_width = 10
    print("\nWall-clock time summary")
    if timing.get("download_model"):
        model_names = list(timing["download_model"].keys())
        download_sum = sum(timing["download_model"].values())
        name_width = max(len("Model"), len("Sum"), *(len(n) for n in model_names))
        sep_width = name_width + 1 + time_col_width
        print("-" * sep_width)
        print(f"{'Model':<{name_width}} {'Time (s)':>{time_col_width}}")
        for model_name, seconds in timing["download_model"].items():
            print(f"{model_name:<{name_width}} {seconds:>{time_col_width}.2f}")
        print(f"{'Sum':<{name_width}} {download_sum:>{time_col_width}.2f}")
        print("-" * sep_width)
    if timing.get("experiments"):
        exp_names = [e["name"] for e in timing["experiments"]]
        experiments_sum = sum(e["seconds"] for e in timing["experiments"])
        name_width = max(len("Experiment"), len("Sum"), *(len(n) for n in exp_names))
        sep_width = name_width + 1 + time_col_width
        print("-" * sep_width)
        print(f"{'Experiment':<{name_width}} {'Time (s)':>{time_col_width}}")
        for entry in timing["experiments"]:
            print(f"{entry['name']:<{name_width}} {entry['seconds']:>{time_col_width}.2f}")
        print(f"{'Sum':<{name_width}} {experiments_sum:>{time_col_width}.2f}")
        print("-" * sep_width)

def _merge_hipblaslt_logs(directory: Path) -> None:
    """
    Merge per-PID hipBLASLt GEMM log files into a single hipblaslt_gemms.yaml.

    Each worker process writes its own hipblaslt_gemms_pid<PID>.yaml. Across
    ranks, the GEMM problem shapes are identical, so the files are redundant
    except for call_count, which is summed across all PIDs. The per-PID files
    are deleted after merging.
    """
    pid_files = sorted(directory.glob("hipblaslt_gemms_pid*.yaml"))
    if not pid_files:
        return

    records = []
    for path in pid_files:
        with open(path) as f:
            records.extend(yaml.safe_load(f))

    df = pd.DataFrame(records)
    key_cols = [c for c in df.columns if c != "call_count"]
    merged = (
        df.groupby(key_cols, dropna=False)["call_count"]
        .sum()
        .reset_index()
    )

    out_path = directory / "hipblaslt_gemms.yaml"
    merged_records = merged.to_dict(orient="records")
    with open(out_path, "w") as f:
        for record in merged_records:
            f.write("- " + yaml.dump(record, default_flow_style=True, sort_keys=False, width=float("inf")).rstrip() + "\n")

    for path in pid_files:
        path.unlink()

    logger.info(
        "Merged %d per-PID hipBLASLt log files into %s (%d unique GEMMs).",
        len(pid_files),
        out_path,
        len(merged_records),
    )


def command(e: Experiment, override_args: dict, override_runner: Optional[str] = None, override_entrypoint: Optional[str] = None) -> List[str]:

    if override_runner is not None:
        e.runner = override_runner
    if override_entrypoint is not None:
        e.entrypoint = override_entrypoint

    if e.runner == "xdit":
        if e.entrypoint is not None:
            logger.warning(f"Entrypoint {e.entrypoint} provided not used with xdit runner.")
        cmd = ["xdit"]
        if e.num_gpus is not None:
            cmd.append(f"--nproc_per_node={e.num_gpus}")
    elif e.entrypoint is not None and _is_python_script(e.entrypoint):
        # is Python script
        if e.runner == "torchrun":
            if e.num_gpus is None:
                raise ValueError("num_gpus is required for torchrun runner")
            cmd = [
                "torchrun",
                f"--nproc_per_node={e.num_gpus}", 
                e.entrypoint
            ]
        else:
            cmd = [e.runner, e.entrypoint]
    else:
        # is Python module
        if e.entrypoint is None or importlib.util.find_spec(e.entrypoint) is None:
            raise ValueError(f"Module {e.entrypoint} not found")

        if e.runner == "torchrun":
            if e.num_gpus is None:
                raise ValueError("num_gpus is required for torchrun runner")
            cmd = [
                sys.executable,
                "-m",
                "torch.distributed.run",
                f"--nproc_per_node={e.num_gpus}",
                "-m",
                e.entrypoint
            ]
        else:
            cmd = [
                sys.executable,
                "-m",
                e.entrypoint
            ]

    cmd.extend(["--model", e.model])

    e.args.update(override_args)

    for key, value in e.args.items():
        flag = f"--{key}"

        # Handle boolean flag cases, such as --use_torch_compile,
        # which do not have an explicit value set
        if isinstance(value, bool):
            if value:
                cmd.append(flag)
            continue
        if isinstance(value, list):
            if value:
                cmd.extend([flag] + [str(v) for v in value])
            continue
        # Nested mappings (e.g. `config:`) are forwarded as a single JSON
        # string; the benchmark script is expected to parse it.
        if isinstance(value, dict):
            if value:
                cmd.extend([flag, json.dumps(value)])
            continue

        cmd.append(flag)
        cmd.append(str(value))

    return cmd


def main():
    experiments: List[Experiment] = []
    experiments_per_model: Dict[str, List[Experiment]] = defaultdict(list)
    errors: List[str] = []

    # Load Experiments from given config files
    for config_path in args.configs:
        with open(config_path) as f:
            workloads = yaml.safe_load(f)

        for bench in workloads:
            exp = Experiment(**bench)
            experiments.append(exp)

    # Filter Experiments based on user args
    if args.names:
        experiments = _filter_experiments_by_name(experiments, args.names)
    if args.tags:
        experiments = _filter_experiments_by_tags(experiments, args.tags)
    if not experiments:
        logger.warning("No experiments matched the given filters.")
        return

    # Write Experiment configurations to file
    if args.export_config_path:
        _export_config(experiments, args.export_config_path)

    for exp in experiments:
        experiments_per_model[exp.model].append(exp)

    # Download models and run Experiments
    preserve_original_state = not args.clear_model_cache and not args.no_clear_model_cache
    timing: Dict[str, Any] = {"download_model": {}, "experiments": []}

    for model_name, exps in experiments_per_model.items():
        logger.info(f"Running experiments for model: {model_name}")

        revision = exps[0].revision # Remark: assumes model experiments uses same revision.
        component_repos = _COMPOSITE_MODEL_REPOS.get(model_name)
        if component_repos:
            model_existed_before = all(_model_in_cache(r) for r in component_repos)
            try:
                t0 = time.monotonic()
                for repo in component_repos:
                    _download_model(repo, dry_run=args.dry_run)
                timing["download_model"][model_name] = round(time.monotonic() - t0, 2)
            except Exception as e:
                logger.error(e, stack_info=True, exc_info=True)
                msg = f"Skipped experiments for {model_name}. Failed to download model. See logs for more details."
                errors.append(msg)
                continue
        else:
            model_existed_before = _model_in_cache(model_name, revision)
            try:
                t0 = time.monotonic()
                _download_model(model_name, revision, args.dry_run)
                timing["download_model"][model_name] = round(time.monotonic() - t0, 2)
            except Exception as e:
                logger.error(e, stack_info=True, exc_info=True)
                msg = f"Skipped experiments for {model_name}. Failed to download model. See logs for more details."
                errors.append(msg)
                continue

        override_args = json.loads(args.override_args_json)

        for i, exp in enumerate(exps, 1):
            benchmark_output_directory = Path(args.results_directory) / exp.name

            logger.info(f"Running Experiment {i}/{len(exps)}: {exp.name}. See {benchmark_output_directory}/stdout.txt for stdout logs.")

            cmd = command(exp, override_args, args.override_runner, args.override_entrypoint) + ["--output-directory", benchmark_output_directory]

            t0 = time.monotonic()
            if not _run_experiment(
                exp,
                cmd,
                args.dry_run,
                benchmark_output_directory,
                collect_hipblaslt_logs=args.collect_hipblaslt_logs,
            ):
                timing["experiments"].append({"name": exp.name, "seconds": round(time.monotonic() - t0, 2)})
                msg = f"Experiment {exp.name} failed to complete. Reason: Failed to run command: {cmd}. See {benchmark_output_directory}/stderr.txt for stderr logs."
                errors.append(msg)
                logger.error(msg)
                continue

            timing["experiments"].append({"name": exp.name, "seconds": round(time.monotonic() - t0, 2)})

            if not args.dry_run:
                latency_output_filepath = Path(benchmark_output_directory) / "timings.json" # benchmark scripts are expected to write latencies to "timings.json"
                median_latency = _get_median_latency(latency_output_filepath)
                if not median_latency:
                    msg = f"Experiment {exp.name} failed to complete. Reason: Failed to compute median latency from output files. See logs for more details."
                    errors.append(msg)
                    logger.error(msg)
                    continue

                if args.collect_hipblaslt_logs:
                    _merge_hipblaslt_logs(benchmark_output_directory)

                logger.info(f"Median latency for {exp.name}: {median_latency} seconds")

                _save_mad_latency_metric(args.csv_output_path, exp.name, median_latency)

        should_clear_cache = args.clear_model_cache or (
            preserve_original_state and not model_existed_before
        )
        if should_clear_cache:
            repos_to_delete = component_repos if component_repos else [model_name]
            revisions_to_delete = [None] * len(repos_to_delete) if component_repos else [revision]
            for repo, rev in zip(repos_to_delete, revisions_to_delete):
                try:
                    _delete_model_cache(repo, rev, args.dry_run)
                except Exception as e:
                    logger.error(e, stack_info=True, exc_info=True)

    if args.print_timing_summary and (timing.get("download_model") or timing.get("experiments")):
        _print_timing_summary(timing)

    if args.print_csv:
        try:
            with open(args.csv_output_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    print(",".join(row))
        except Exception as e:
            logger.error(e, stack_info=True, exc_info=True)

    if errors:
        logger.error("One or more errors occurred during experiment runs:")
        for msg in errors:
            logger.error(f" - {msg}")
        sys.exit(1)
    else:
        logger.info("Finished running Experiments.")


if __name__ == "__main__":
    args = _parse_args()
    main()
