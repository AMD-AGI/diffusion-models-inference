#!/usr/bin/env python3
"""Post-process converted inference-testing configs.

Injects runtime settings that depend on CI action inputs:
  - HF cache volume mount (if available)
  - MIOpen user DB path (if not in benchmark-only mode)
  - hipBLASLt log collection flag

Usage:
    python3 post-process-configs.py <config_dir> [options]

Options:
    --hf-cache-volume VOLUME   Add HF cache volume to server.args.volumes
    --miopen-user-db           Inject MIOPEN_USER_DB_PATH into server environment
    --collect-hipblaslt-logs   Add collect_hipblaslt_logs to benchmark args
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import yaml


def post_process(config_path: Path, *, hf_cache_volume: str | None, miopen_user_db: bool,
                 collect_hipblaslt_logs: bool) -> None:
    with open(config_path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return

    # HF cache volume
    if hf_cache_volume and "server" in data and "args" in data["server"]:
        volumes = data["server"]["args"].setdefault("volumes", [])
        volumes.append(hf_cache_volume)

    # MIOpen user DB path
    if miopen_user_db and "server" in data and "args" in data["server"]:
        env = data["server"]["args"].setdefault("environment", {})
        env["MIOPEN_USER_DB_PATH"] = "/app/diffusion-models-inference-private/data/miopen/userdb"

    # hipBLASLt log collection
    if collect_hipblaslt_logs and "benchmarks" in data:
        for bench in data["benchmarks"]:
            bench.setdefault("args", {})["collect_hipblaslt_logs"] = True

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Post-process converted inference-testing configs")
    parser.add_argument("config_dir", type=Path, help="Directory containing converted YAML configs")
    parser.add_argument("--hf-cache-volume", default=None, help="HF cache volume string (host:container)")
    parser.add_argument("--miopen-user-db", action="store_true", help="Inject MIOPEN_USER_DB_PATH")
    parser.add_argument("--collect-hipblaslt-logs", action="store_true", help="Enable hipBLASLt log collection")
    args = parser.parse_args()

    configs = sorted(args.config_dir.glob("*.yaml"))
    if not configs:
        print("No configs to post-process", file=sys.stderr)
        return

    for config_path in configs:
        post_process(
            config_path,
            hf_cache_volume=args.hf_cache_volume,
            miopen_user_db=args.miopen_user_db,
            collect_hipblaslt_logs=args.collect_hipblaslt_logs,
        )

    print(f"Post-processed {len(configs)} config(s)")


if __name__ == "__main__":
    main()
