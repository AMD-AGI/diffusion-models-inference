# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

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
import shutil
import threading
import time
from pathlib import Path
from dataclasses import asdict, dataclass
from typing import List, Dict, Any, Optional
import subprocess

import numpy as np
import pandas as pd

from huggingface_hub import snapshot_download, scan_cache_dir, DryRunFileInfo
from miopen_driver_commands import MIOpenDriverCommandCollector, env_enabled


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(
    fmt='%(asctime)s - %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
))
logger.addHandler(_handler)


def import_xfuser_determinism_check_results():
    """Imports the determinism check results functions from xfuser.
    That roundtrip is necessary instead of a simple

    ```
    from xfuser.core.utils.determinism_check_results import (
        determinism_check_results,
        readable_bytes,
    )
    ```

    to avoid importing all parent modules of xfuser, that typically import pytorch,
    which takes a long time and could produce lot's of spam to the console.
    """
    import importlib.util

    determinism_check_results, readable_bytes = None, None
    suberror = "Report on determinism check results will not be available."

    try:
        package = importlib.util.find_spec("xfuser")
        if package is not None:
            package_dir = Path(next(iter(package.submodule_search_locations)))
            path = package_dir / "core/utils/determinism_check_results.py"

            spec = importlib.util.spec_from_file_location("_xfuser_core_utils_determinism_check_results", path)
            if spec is not None:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                determinism_check_results, readable_bytes = (
                    module.determinism_check_results,
                    module.readable_bytes,
                )
            else:
                logger.warning("xfuser.core.utils.determinism_check_results module not found.", suberror)
        else:
            logger.warning("xfuser package not found.", suberror)
    except Exception as exc:  # ruff: ignore[blind-except]
        logger.warning("Error importing xfuser determinism check results.", suberror, exc)

    return determinism_check_results, readable_bytes

tried_import_xfuser_determinism_check_results = False
determinism_check_results, readable_bytes = None, None # import_xfuser_determinism_check_results()


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
    parser.add_argument(
        "--name",
        action="append",
        dest="names",
        help="Run only experiments whose name matches one of these names",
        default=[],
    )
    parser.add_argument(
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
        "--collect-memory-stats",
        action="store_true",
        help=(
            "Record peak per-GPU VRAM and peak host anonymous memory for each benchmark in "
            "<results-directory>/<experiment_name>/memory.json. "
            "Also enabled by CI_RUN_PY_COLLECT_MEMORY=1."
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


def _is_determinism_check_enabled(exp: Experiment) -> bool:
    """Assuming that xFuser doesn't enable determinism check by default, and that the `exp.args`
    has configuration arguments AND `--override-args-json` already merged, checks if the check
    is enabled by the experiment config.
    """
    # global overrider
    if os.environ.get("CI_RUN_PY_FORCE_DETERMINISM_REPORT", "0") == "1":
        return True

    ret = False
    if (
        "determinism_check" in exp.args
        and exp.args["determinism_check"]
        and (
            (isinstance(exp.args["determinism_check"], str) and exp.args["determinism_check"].isdigit())
            or isinstance(exp.args["determinism_check"], int)
        )
    ):
        ret = int(exp.args["determinism_check"]) > 0

    return ret


def _report_determinism_check_results(exp: Experiment, benchmark_output_directory: Path) -> None:
    global tried_import_xfuser_determinism_check_results, determinism_check_results, readable_bytes
    if not tried_import_xfuser_determinism_check_results:
        tried_import_xfuser_determinism_check_results = True
        determinism_check_results, readable_bytes = import_xfuser_determinism_check_results()
        if readable_bytes is None:
            readable_bytes = lambda x: f"{x} bytes"  # noqa: E731

    if determinism_check_results is None:  # import must have failed
        return

    try:
        det_check = determinism_check_results(benchmark_output_directory)
        if not isinstance(det_check, dict):
            logger.error(f"Expected a dictionary in results report, got {type(det_check)}")
            return
        if 0 == len(det_check):
            logger.info(f"Determinism checks passed for {exp.name}")
            return

        if 1 != len(det_check):
            logger.error("Expected exactly one top-level directory in results report")
        if "" in det_check:
            res = det_check[""]
            logger.warning(
                f"Determinism check failed for {exp.name}: {res[0]} checks failed, "
                f"{readable_bytes(res[1])} is occupied by dumps"
            )
        else:
            logger.error("Results for the top-level directory not found. Skipping report.")
    except Exception:  # ruff: ignore[blind-except]
        logger.error("Error getting determinism check results.", exc_info=True)


class MemorySampler:
    """Background peak of per-GPU VRAM and host anonymous memory for one benchmark."""

    _INTERVAL_S = 0.2

    def __init__(self, output_path: Path, enabled: bool = True) -> None:
        self._output_path = output_path
        self._enabled = enabled
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._smi: Optional[tuple] = None
        self._vram_start: Dict[str, int] = {}
        self._peak_vram: Dict[str, int] = {}
        self._host_start: Optional[int] = None
        self._peak_host: Optional[int] = None

    def __enter__(self) -> "MemorySampler":
        if not self._enabled:
            return self
        self._smi = self._find_smi()
        if self._smi is None:
            logger.warning("No usable rocm-smi or nvidia-smi; VRAM will be omitted.")
        self._vram_start = self._read_vram()
        self._peak_vram = dict(self._vram_start)
        self._host_start = self._host_anon_bytes()
        self._peak_host = self._host_start
        self._thread = threading.Thread(target=self._track, name="memory-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> bool:
        if not self._enabled:
            return False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._write()
        return False

    def _track(self) -> None:
        while not self._stop.is_set():
            self._update_peaks()
            self._stop.wait(self._INTERVAL_S)
        self._update_peaks()

    def _update_peaks(self) -> None:
        try:
            if self._smi is not None:
                for gpu, used in self._read_vram().items():
                    if used > self._peak_vram.get(gpu, 0):
                        self._peak_vram[gpu] = used
            host = self._host_anon_bytes()
            if host is not None and (self._peak_host is None or host > self._peak_host):
                self._peak_host = host
        except Exception:
            pass

    def _find_smi(self) -> Optional[tuple]:
        for name, read in (
            ("rocm-smi", self._rocm_smi_used_bytes),
            ("nvidia-smi", self._nvidia_smi_used_bytes),
        ):
            binary = shutil.which(name)
            if binary is None:
                continue
            try:
                if read(binary):
                    return (binary, read)
            except Exception:
                continue
        return None

    def _read_vram(self) -> Dict[str, int]:
        if self._smi is None:
            return {}
        binary, read = self._smi
        return read(binary)

    @staticmethod
    def _nvidia_smi_used_bytes(binary: str) -> Dict[str, int]:
        output = subprocess.check_output(
            [binary, "--query-gpu=index,memory.used", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        used = {}
        for line in output.strip().splitlines():
            index, mebibytes = (field.strip() for field in line.split(","))
            used[f"gpu{index}"] = int(float(mebibytes)) * 1024 * 1024
        return used

    @staticmethod
    def _rocm_smi_used_bytes(binary: str) -> Dict[str, int]:
        output = subprocess.check_output(
            [binary, "--showmeminfo", "vram", "--json"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        used = {}
        for card, fields in json.loads(output).items():
            if not isinstance(fields, dict):
                continue
            index = card.removeprefix("card")
            for key, value in fields.items():
                lowered = key.lower()
                if "used" in lowered and "memory" in lowered:
                    used[f"gpu{index}"] = int(str(value).strip())
                    break
        return used

    @staticmethod
    def _cgroup_stat(path: str, key: str) -> Optional[int]:
        try:
            with open(path) as handle:
                for line in handle:
                    name, _, value = line.partition(" ")
                    if name == key:
                        return int(value)
        except OSError:
            return None
        return None

    @classmethod
    def _host_anon_bytes(cls) -> Optional[int]:
        # memory.current is anon + file. file is reclaimable checkpoint cache; anon is the allocation.
        anon = cls._cgroup_stat("/sys/fs/cgroup/memory.stat", "anon")
        if anon is not None:
            return anon
        for key in ("total_rss", "rss"):
            rss = cls._cgroup_stat("/sys/fs/cgroup/memory/memory.stat", key)
            if rss is not None:
                return rss
        return None

    def _write(self) -> None:
        payload = {
            "vram_start_bytes": self._vram_start,
            "peak_vram_bytes": self._peak_vram,
            "host_anon_start_bytes": self._host_start,
            "peak_host_anon_bytes": self._peak_host,
        }
        try:
            with open(self._output_path, "w") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as error:
            logger.warning(f"Failed to write memory stats to {self._output_path}: {error}")


def _run_experiment(
    exp: Experiment,
    cmd: List[str],
    dry_run: bool,
    benchmark_output_directory: Path,
    collect_hipblaslt_logs: bool = False,
    collect_miopen_driver_commands: bool = False,
    collect_memory_stats: bool = False,
) -> bool:
    """Runs a single experiment."""

    if dry_run:
        logger.info(f"[dry-run] Running command: {shlex.join([str(part) for part in cmd])}")
        return True

    benchmark_output_directory.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TQDM_DISABLE"] = "1"
    if collect_miopen_driver_commands:
        env["MIOPEN_ENABLE_LOGGING_CMD"] = "1"
    if collect_hipblaslt_logs:
        # %i is substituted with the worker process ID by hipBLASLt at runtime,
        # so multi-rank benchmarks (e.g. ulysses_degree > 1) get one file per process.
        env["HIPBLASLT_LOG_MASK"] = "64"
        env["HIPBLASLT_LOG_FILE"] = str(
            benchmark_output_directory / "hipblaslt_gemms_pid%i.yaml"
        )
    stdout_path = benchmark_output_directory / "stdout.txt"
    stderr_path = benchmark_output_directory / "stderr.txt"
    memory_path = benchmark_output_directory / "memory.json"
    with open(stdout_path, "w", buffering=1) as stdout_file, open(stderr_path, "w", buffering=1) as stderr_file:
        with MemorySampler(memory_path, enabled=collect_memory_stats):
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

    if _is_determinism_check_enabled(exp):
        _report_determinism_check_results(exp, benchmark_output_directory)

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

    collect_memory_stats = args.collect_memory_stats or env_enabled(
        os.environ.get("CI_RUN_PY_COLLECT_MEMORY", "0")
    )
    collect_miopen_driver_commands = env_enabled(
        os.environ.get("CI_RUN_PY_COLLECT_MIOPEN_DRIVER_COMMANDS", "0")
    )
    miopen_command_collector = None
    if collect_miopen_driver_commands:
        try:
            miopen_command_collector = MIOpenDriverCommandCollector(
                Path(args.results_directory), (exp.name for exp in experiments)
            )
        except OSError as exc:
            logger.warning(
                "Failed to initialize MIOpenDriver command collection: %s", exc
            )

    # Write Experiment configurations to file
    if args.export_config_path:
        _export_config(experiments, args.export_config_path)

    for exp in experiments:
        experiments_per_model[exp.model].append(exp)

    # Download models and run Experiments
    preserve_original_state = not args.clear_model_cache and not args.no_clear_model_cache
    timing: Dict[str, Any] = {"download_model": {}, "experiments": []}

    override_args = json.loads(args.override_args_json)
    # assumes `override_args` aren't mutated in the loop below

    if os.environ.get("CI_RUN_PY_FORCE_DETERMINISM_CHECK", "0") == "1":
        if "determinism_check" not in override_args:
            override_args["determinism_check"] = 1  # this will enable the check
        if "determinism_check_report_ranks" not in override_args:
            # but this will disable dumping files for failed checks
            override_args["determinism_check_report_ranks"] = "none"

    for model_name, exps in experiments_per_model.items():
        logger.info(f"Running experiments for model: {model_name}")

        revision = exps[0].revision # Remark: assumes model experiments uses same revision.
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

        for i, exp in enumerate(exps, 1):
            benchmark_output_directory = Path(args.results_directory) / exp.name

            logger.info(f"Running Experiment {i}/{len(exps)}: {exp.name}. See {benchmark_output_directory}/stdout.txt for stdout logs.")

            cmd = command(exp, override_args, args.override_runner, args.override_entrypoint) + ["--output-directory", benchmark_output_directory]

            t0 = time.monotonic()
            process_succeeded = _run_experiment(
                exp,
                cmd,
                args.dry_run,
                benchmark_output_directory,
                collect_hipblaslt_logs=args.collect_hipblaslt_logs,
                collect_miopen_driver_commands=collect_miopen_driver_commands,
                collect_memory_stats=collect_memory_stats,
            )
            seconds = round(time.monotonic() - t0, 2)
            timing["experiments"].append({"name": exp.name, "seconds": seconds})
            if not args.dry_run:
                wall_clock_path = benchmark_output_directory / "wall_clock.json"
                try:
                    with open(wall_clock_path, "w") as handle:
                        json.dump({"seconds": seconds}, handle, indent=2)
                except OSError as error:
                    logger.warning(f"Failed to write wall clock to {wall_clock_path}: {error}")
            if miopen_command_collector is not None and not args.dry_run:
                miopen_command_collector.collect(
                    exp.name,
                    benchmark_output_directory / "stderr.txt",
                    process_succeeded,
                )

            if not process_succeeded:
                msg = f"Experiment {exp.name} failed to complete. Reason: Failed to run command: {cmd}. See {benchmark_output_directory}/stderr.txt for stderr logs."
                errors.append(msg)
                logger.error(msg)
                continue

            if not args.dry_run:
                latency_output_filepath = Path(benchmark_output_directory) / "timings.json" # benchmark scripts are expected to write latencies to "timings.json"
                median_latency = _get_median_latency(latency_output_filepath)
                if not median_latency:
                    if miopen_command_collector is not None:
                        miopen_command_collector.mark_metrics_failed(exp.name)
                    msg = f"Experiment {exp.name} failed to complete. Reason: Failed to compute median latency from output files. See logs for more details."
                    errors.append(msg)
                    logger.error(msg)
                    continue

                if args.collect_hipblaslt_logs:
                    _merge_hipblaslt_logs(benchmark_output_directory)

                logger.info(f"Median latency for {exp.name}: {median_latency} seconds")

                _save_mad_latency_metric(args.csv_output_path, exp.name, median_latency)
                if miopen_command_collector is not None:
                    miopen_command_collector.mark_succeeded(exp.name)

        should_clear_cache = args.clear_model_cache or (
            preserve_original_state and not model_existed_before
        )
        if should_clear_cache:
            try:
                _delete_model_cache(model_name, revision, args.dry_run)
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
