import argparse
import copy
import json
import yaml
from pathlib import Path

PARAM_SHORT = {"batch_size": "bs", "data_parallel_degree": "dp"}


def parse_values(raw):
    """Parse '[1,2,4,8]' or 'MIN MAX STEP' into a list of ints."""
    if raw[0].startswith("["):
        return json.loads(raw[0]), raw[1:]
    lo, hi, step = int(raw[0]), int(raw[1]), int(raw[2])
    return list(range(lo, hi + 1, step)), raw[3:]


def load_configs(path):
    configs = {}
    for f in sorted(Path(path).glob("*.yaml")):
        for exp in yaml.safe_load(f.read_text()) or []:
            configs[exp["name"]] = exp
    return configs


def expand(base, param, values, overrides):
    short = PARAM_SHORT[param]
    override_suffix = "".join(
        f".{PARAM_SHORT[k]}{v}" for k, v in overrides.items() if k in PARAM_SHORT
    )
    out = []
    for val in values:
        exp = copy.deepcopy(base)
        exp["name"] = f"{base['name']}.{short}{val}{override_suffix}"
        args = exp["args"]
        prompt = args["prompt"]
        args[param] = val
        args.update(overrides)
        args["prompt"] = [prompt] * args.get("batch_size", 1)
        out.append(exp)
    return out


def main():
    p = argparse.ArgumentParser(description="Generate sweep configs from benchmark configs.")
    p.add_argument("--sweep", action="append", nargs="+", metavar="ARG",
                   help="NAME PARAM {[list] | MIN MAX STEP} [JSON_OVERRIDES]")
    p.add_argument("--output", default="/outputs/sweep_config.yaml")
    p.add_argument("configs", nargs="?", default="/app/.ci/benchmark_configs")
    cli = p.parse_args()

    configs = load_configs(cli.configs)
    experiments = []
    for parts in (cli.sweep or []):
        if len(parts) < 3:
            p.error(f"--sweep requires NAME PARAM and values, got: {' '.join(parts)}")
        name, param = parts[0], parts[1]
        values, rest = parse_values(parts[2:])
        overrides = json.loads(rest[0]) if rest else {}
        if param not in PARAM_SHORT:
            p.error(f"Unknown parameter '{param}'. Allowed: {list(PARAM_SHORT)}")
        if name not in configs:
            p.error(f"Config '{name}' not found.")
        experiments += expand(configs[name], param, values, overrides)

    out = Path(cli.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.dump(experiments, sort_keys=False))
    print(f"Wrote {len(experiments)} experiments to {out}")


if __name__ == "__main__":
    main()
