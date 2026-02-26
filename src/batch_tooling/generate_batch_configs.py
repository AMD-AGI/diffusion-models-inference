import argparse
import copy
import logging
import yaml
from pathlib import Path
from typing import Any, Dict, List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)

def parse_args() -> argparse.Namespace:
    """Parse CLI and return args with specs (name, min_bs, max_bs) as ints."""
    p = argparse.ArgumentParser(description="Generate batch-sweep YAML from benchmark configs.")
    p.add_argument(
        "--name",
        action="append",
        nargs=4,
        metavar=("NAME", "MIN", "MAX", "STEP"),
        help="Base config name, batch-size range (inclusive), and step (e.g. 1 20 2 for 1,3,5,...,19)",
    )
    p.add_argument(
        "--config-dir",
        type=str,
        default="/app/.ci/benchmark_configs",
        help="Directory of benchmark YAML configs (default: /app/.ci/benchmark_configs)",
    )
    p.add_argument(
        "--output",
        type=str,
        default="/outputs/batch_config.yaml",
        help="Output YAML path",
    )
    args = p.parse_args()
    args.specs = [(n, int(mn), int(mx), int(st)) for n, mn, mx, st in (args.name or [])]
    return args


def load_configs(config_dir: str) -> Dict[str, Dict[str, Any]]:
    """Load all benchmark YAMLs from config_dir."""
    by_name: Dict[str, Dict[str, Any]] = {}
    for f in sorted(Path(config_dir).glob("*.yaml")):
        for exp in yaml.safe_load(f.read_text()) or []:
            by_name[exp["name"]] = exp
    return by_name


def build_batch_experiments(
    configs: Dict[str, Dict[str, Any]],
    specs: List[Tuple[str, int, int, int]],
) -> List[Dict[str, Any]]:
    """Build batch-size experiments for each config name in specs."""
    out: List[Dict[str, Any]] = []
    for name, min_bs, max_bs, step in specs:
        base = configs[name]
        for bs in range(min_bs, max_bs + 1, step):
            exp = copy.deepcopy(base)
            exp["name"] = f"{name}.bs{bs}"
            a = exp["args"]
            a["batch_size"] = bs
            # Need bs number of prompts to run the experiment
            a["prompt"] = [a["prompt"]] * bs
            out.append(exp)
    return out


def main() -> None:
    """Load configs, build batch experiments from specs, write YAML to output."""
    args = parse_args()
    configs = load_configs(args.config_dir)
    experiments = build_batch_experiments(configs, args.specs)

    # Create output directory if it doesn't exist.
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(args.output, "w") as f:
        yaml.dump(experiments, f, sort_keys=False)

    logger.info(f"Batch sweep configurations written to {args.output}")


if __name__ == "__main__":
    main()
