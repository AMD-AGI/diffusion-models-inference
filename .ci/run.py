import argparse
from collections import defaultdict
import logging
import yaml
import os
import json
import sys
import csv
import shutil
from pathlib import Path
from dataclasses import asdict, dataclass, field
from typing import List, Dict, Any, Tuple, Optional
import subprocess

from huggingface_hub import snapshot_download, scan_cache_dir
from huggingface_hub.utils import HfHubHTTPError, RepositoryNotFoundError

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

@dataclass
class Experiment:
    name: str
    model: str
    revision: str
    entrypoint: str
    runner: str
    num_gpus: int
    tags: List[str]
    args: Dict[str, Any]


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
    parser.add_argument(
        "--clear-model-cache",
        action="store_true",
        help="Remove model cache after finishing all model specific experiments",
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
        "configs",
        nargs="+",
        help="YAML config files containing experiment definitions",
    )
    return parser.parse_args()


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


def _download_model(model: str, revision: Optional[str] = None, dry_run: bool = False) -> Optional[str]:
    """
    Attempts to download a model from HuggingFace Hub. Skips download if already cached.
    """
    if dry_run: # TODO using dry_run with snapshot_download requires huggingface_hub => 1.0 but version < 1.0 is needed for transformers 4.57.3
        logger.info(f"[dry-run] Skipping download of model: {model}")
        return None
    
    logger.info(f"Downloading model: {model}")
    cache_dir = snapshot_download(repo_id=model, revision=revision)
    logger.info(f"Model {model} is stored in {cache_dir}.")

    return cache_dir


def _run_experiment(exp: Experiment, cmd: List[str], dry_run: bool, benchmark_output_directory: Path) -> bool:
    """Runs a single experiment."""

    if dry_run:
        logger.info(f"[dry-run] Running command: {cmd}")
        return True

    benchmark_output_directory.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    with open(f'{benchmark_output_directory}/stdout.txt', 'w') as f:
        f.write(result.stdout)
    with open(f'{benchmark_output_directory}/stderr.txt', 'w') as f:
        f.write(result.stderr)
    if result.returncode != 0:
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


def _get_average_latency(file_path: Path) -> Optional[float]:
        try:
            with open(file_path, 'r') as f:
                data = json.load(f)

            pipe_times = [entry['pipe_time'] for entry in data]

            average = sum(pipe_times) / len(pipe_times)

        except Exception as e:
            logger.error(f"Failed to compute average latency from {file_path}: {e}")
            return None

        return average


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


def command(e: Experiment, override_args: dict) -> List[str]:
    cmd = [e.runner]

    if e.runner == "torchrun":
        cmd.extend(["--nproc_per_node", str(e.num_gpus)])

    cmd.append(e.entrypoint)

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
    for model_name, exps in experiments_per_model.items():
        logger.info(f"Running experiments for model: {model_name}")
        

        revision = exps[0].revision # Remark: assumes model experiments uses same revision.
        try:
            cache_dir = _download_model(model_name, revision, args.dry_run)
        except Exception as e:
            logger.error(e, stack_info=True, exc_info=True)
            msg = f"Skipped experiments for {model_name}. Failed to download model. See logs for more details."
            errors.append(msg)
            continue

        override_args = json.loads(args.override_args_json)

        for i, exp in enumerate(exps, 1):
            benchmark_output_directory = Path(args.results_directory) / exp.name
            
            logger.info(f"Running Experiment {i}/{len(exps)}: {exp.name}. See {benchmark_output_directory}/stdout.txt for stdout logs.")

            cmd = command(exp, override_args) + ["--benchmark-output-directory", benchmark_output_directory]

            if not _run_experiment(exp, cmd, args.dry_run, benchmark_output_directory):
                msg = f"Experiment {exp.name} failed to complete. Reason: Failed to run command: {cmd}. See {benchmark_output_directory}/stderr.txt for stderr logs."
                errors.append(msg)
                logger.error(msg)
                continue

            if not args.dry_run:
                latency_output_filepath = Path(benchmark_output_directory) / "timing.json" # benchmark scripts are expected to write latencies to "timing.json"
                avg_latency = _get_average_latency(latency_output_filepath)
                if not avg_latency:
                    msg = f"Experiment {exp.name} failed to complete. Reason: Failed to compute average latency from output files. See logs for more details."
                    errors.append(msg)
                    logger.error(msg)
                    continue

                logger.info(f"Average latency for {exp.name}: {avg_latency} seconds")

                _save_mad_latency_metric(args.csv_output_path, exp.name, avg_latency)

        if args.clear_model_cache and cache_dir:
            logging.info(f"Deleting model cache at {cache_dir}")
            try:
                delete_strategy = scan_cache_dir().delete_revisions(os.path.basename(cache_dir))
                delete_strategy.execute()
            except Exception as e:
                logger.error(e, stack_info=True, exc_info=True)
                continue

    if args.print_csv:
        try:
            with open(args.csv_output_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    print(",".join(row))
        except Exception as e:
            logger.error(e, stack_info=True, exc_info=True)

    if errors:
        logger.error("One ore more errors occured during experiment runs:")
        for msg in errors:
            logger.error(f" - {msg}")
        sys.exit(1)
    else:
        logger.info("Finished running Experiments.")


if __name__ == "__main__":
    args = _parse_args()
    main()
