#!/usr/bin/env python3
"""Convert benchmark configs and inject the runtime settings CI controls.

Filters the repo's xDiT configs, converts them to inference-testing format, and
injects settings that only the workflow knows:
  - bind mounts, restricted to sources the runner authz policy allows
  - HF cache mount (when the deployment provides one)
  - MIOpen user DB path (unless in benchmark-only mode)
  - hipBLASLt log collection

Bind sources must be the exact paths the policy allows, mapped to the same path
in the container, so that host, driver, and benchmark container all agree on
where results live.

Writes ``converted_dir`` and ``config_count`` to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path
from typing import Any


def load_converter(repo_root: Path) -> Any:
    """Import the repo-level converter, which is not an installed package."""
    sys.path.insert(0, str(repo_root / "scripts"))
    import convert_configs  # noqa: PLC0415

    return convert_configs


def parse_benchmark_flags(raw: str) -> tuple[list[str], list[str]]:
    """Extract --tag/--name filters from the workflow's benchmark_flags string.

    Unrecognised flags are ignored: several are consumed elsewhere in the action
    and the rest are inert for config selection.
    """
    tags: list[str] = []
    names: list[str] = []

    tokens = shlex.split(raw or "")
    index = 0
    while index < len(tokens):
        flag = tokens[index]
        if flag in {"--tag", "--name"}:
            if index + 1 >= len(tokens):
                sys.exit(f"::error::{flag} requires a value")
            (tags if flag == "--tag" else names).append(tokens[index + 1])
            index += 2
            continue
        index += 1

    return tags, names


def inject_ci_settings(
    config: dict,
    *,
    config_name: str,
    work_root: str,
    output_root: str,
    hf_cache_path: str | None,
    miopen_user_db: str | None,
    collect_hipblaslt_logs: bool,
) -> None:
    server_args = config.get("server", {}).get("args")
    if not isinstance(server_args, dict):
        return

    volumes = server_args.setdefault("volumes", [])
    volumes.append(f"{work_root}:{work_root}")

    environment = server_args.setdefault("environment", {})

    if hf_cache_path:
        volumes.append(f"{hf_cache_path}:{hf_cache_path}")
        environment["HF_HOME"] = hf_cache_path

    if miopen_user_db:
        environment["MIOPEN_USER_DB_PATH"] = miopen_user_db

    if collect_hipblaslt_logs:
        environment["HIPBLASLT_LOG_MASK"] = "64"
        environment["HIPBLASLT_LOG_FILE"] = f"{output_root}/{config_name}/hipblaslt_gemms_pid%i.yaml"


def write_output(name: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        return
    with open(output_file, "a") as handle:
        handle.write(f"{name}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True, help="Workspace root")
    parser.add_argument("--output-dir", type=Path, required=True, help="Where converted configs are written")
    parser.add_argument("--image", required=True, help="Docker image for the server block")
    parser.add_argument("--work-root", required=True, help="Runner work root, bind-mounted at the same path")
    parser.add_argument("--output-root", required=True, help="Directory results are written to")
    parser.add_argument("--benchmark-flags", default="", help="Raw benchmark_flags string from the workflow")
    parser.add_argument("--gfx-arch", default="", help="GPU architecture, used as a tag filter and run-name label")
    parser.add_argument("--mlflow-uri", default="", help="MLflow tracking URI")
    parser.add_argument("--experiment-prefix", default="", help="Workspace folder holding the experiment")
    parser.add_argument("--hf-cache-path", default="", help="HF cache path; ignored when it does not exist")
    parser.add_argument("--miopen-user-db", default="", help="MIOPEN_USER_DB_PATH to inject")
    parser.add_argument("--collect-hipblaslt-logs", action="store_true", help="Enable hipBLASLt log collection")
    args = parser.parse_args()

    converter = load_converter(args.repo_root)

    tags, names = parse_benchmark_flags(args.benchmark_flags)
    if tags and names:
        sys.exit("::error::--tag and --name are mutually exclusive")
    # An explicit --name selects exact configs, so architecture filtering would
    # only ever narrow that to nothing.
    if args.gfx_arch and not names:
        tags.append(args.gfx_arch)

    hf_cache_path = args.hf_cache_path.rstrip("/")
    if hf_cache_path and not Path(hf_cache_path).is_dir():
        print(f"HF cache does not exist, continuing without it: {hf_cache_path}")
        hf_cache_path = ""

    work_root = args.work_root.rstrip("/")
    output_root = args.output_root.rstrip("/")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted((args.repo_root / "benchmark_configs" / "xdit").glob("*.yaml"))

    config_count = 0
    for source in sources:
        for name, config in converter.convert_file(
            source,
            image=args.image,
            tags=tags or None,
            names=names or None,
            output_root=output_root,
            mlflow_uri=args.mlflow_uri,
            experiment_prefix=args.experiment_prefix,
            arch_label=args.gfx_arch,
        ):
            safe_name = converter.sanitize_name(name)
            inject_ci_settings(
                config,
                config_name=safe_name,
                work_root=work_root,
                output_root=output_root,
                hf_cache_path=hf_cache_path or None,
                miopen_user_db=args.miopen_user_db or None,
                collect_hipblaslt_logs=args.collect_hipblaslt_logs,
            )
            (args.output_dir / f"{safe_name}.yaml").write_text(converter.dump_yaml(config))
            config_count += 1

    print(f"Prepared {config_count} config(s) in {args.output_dir}")
    if config_count == 0:
        print("::warning::No benchmark configs matched the filter criteria")

    write_output("converted_dir", str(args.output_dir))
    write_output("config_count", str(config_count))


if __name__ == "__main__":
    main()
