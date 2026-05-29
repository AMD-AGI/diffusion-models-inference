#!/usr/bin/env python3
"""
Online serving benchmark.

Starts the vllm-omni server, waits for readiness, sends request(s),
and reports timing from server response headers.
"""

import argparse
import logging
import os
import shlex
import signal
import subprocess
import sys
import time

import requests

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark vllm-omni text-to-video online serving")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--num_frames", type=int, required=True)
    parser.add_argument("--num_inference_steps", type=int, required=True)
    parser.add_argument("--guidance_scale", type=float, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--ulysses_degree", type=int, required=True)
    parser.add_argument("--attention_backend", required=True)
    parser.add_argument("--num_iterations", type=int, required=True)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--health_timeout", type=int, default=600, help="Seconds to wait for server readiness")
    parser.add_argument("--request_timeout", type=int, default=600, help="Seconds to wait for each inference request")
    return parser.parse_args()


def build_serve_cmd(args):
    return [
        "vllm-omni", "serve", args.model, "--omni",
        "--port", str(args.port),
        "--usp", str(args.ulysses_degree),
        "--vae-patch-parallel-size", str(args.ulysses_degree),
    ]


def build_curl_cmd(base_url, args, output_path):
    return [
        "curl", "-X", "POST", f"{base_url}/v1/videos/sync",
        "-F", f"prompt={args.prompt}",
        "-F", f"size={args.width}x{args.height}",
        "-F", f"num_frames={args.num_frames}",
        "-F", f"num_inference_steps={args.num_inference_steps}",
        "-F", f"guidance_scale={args.guidance_scale}",
        "-F", f"seed={args.seed}",
        "-o", output_path,
        "-D", "-",
    ]


def start_server(args):
    env = os.environ.copy()
    env["DIFFUSION_ATTENTION_BACKEND"] = args.attention_backend.upper()

    cmd = build_serve_cmd(args)
    logger.info(f"Starting server: {' '.join(cmd)}")
    logger.info(f"  DIFFUSION_ATTENTION_BACKEND={env['DIFFUSION_ATTENTION_BACKEND']}")

    proc = subprocess.Popen(cmd, env=env, preexec_fn=os.setsid)
    return proc


def wait_for_health(base_url, timeout, proc):
    logger.info(f"Waiting for server to be ready (timeout {timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            logger.error("Server process exited unexpectedly.")
            sys.exit(1)
        try:
            resp = requests.get(f"{base_url}/health", timeout=5)
            if resp.ok:
                elapsed = timeout - (deadline - time.time())
                logger.info(f"Server is ready (took {elapsed:.0f}s).")
                return
        except requests.ConnectionError:
            pass
        time.sleep(5)
    logger.error(f"Server did not become ready within {timeout}s.")
    sys.exit(1)


def send_request(base_url, args, iteration, output_path):
    url = f"{base_url}/v1/videos/sync"
    data = {
        "prompt": args.prompt,
        "size": f"{args.width}x{args.height}",
        "num_frames": str(args.num_frames),
        "num_inference_steps": str(args.num_inference_steps),
        "guidance_scale": str(args.guidance_scale),
        "seed": str(args.seed),
    }

    logger.info(f"=== Iteration {iteration} / {args.num_iterations} ===")
    wall_start = time.perf_counter()
    resp = requests.post(url, data=data, timeout=args.request_timeout)
    wall_time = time.perf_counter() - wall_start

    if not resp.ok:
        logger.error(f"HTTP {resp.status_code}")
        logger.error(f"{resp.text}")
        sys.exit(1)

    with open(output_path, "wb") as f:
        f.write(resp.content)

    inference_time = resp.headers.get("X-Inference-Time-S", "N/A")
    stage_durations = resp.headers.get("X-Stage-Durations", "N/A")
    peak_memory = resp.headers.get("X-Peak-Memory-MB", "N/A")

    logger.info(f"  Saved:            {output_path}")
    logger.info(f"  Wall time:        {wall_time:.3f}s")
    logger.info(f"  Inference time:   {inference_time}s")
    logger.info(f"  Stage durations:  {stage_durations}")
    logger.info(f"  Peak memory:      {peak_memory} MB")

    return wall_time, inference_time


def print_commands(args, base_url):
    serve_cmd = build_serve_cmd(args)
    serve_str = f"DIFFUSION_ATTENTION_BACKEND={args.attention_backend.upper()} {shlex.join(serve_cmd)}"

    output_path = os.path.join(args.output_directory, "iter_1.mp4")
    curl_cmd = build_curl_cmd(base_url, args, output_path)
    curl_str = shlex.join(curl_cmd)

    logger.info("=" * 60)
    logger.info("Commands used:")
    logger.info("=" * 60)
    logger.info(f"[Server]\n{serve_str}")
    logger.info(f"[Request]\n{curl_str}")


def main():
    args = parse_args()
    base_url = f"http://localhost:{args.port}"

    proc = start_server(args)
    try:
        if proc.poll() is not None:
            logger.error("Server process failed to start.")
            sys.exit(1)
        wait_for_health(base_url, args.health_timeout, proc)

        os.makedirs(args.output_directory, exist_ok=True)

        for i in range(1, args.num_iterations + 1):
            output_path = os.path.join(args.output_directory, f"iter_{i}.mp4")
            send_request(base_url, args, i, output_path)

        logger.info(f"Done. {args.num_iterations} iteration(s) completed.")
        print_commands(args, base_url)
    finally:
        logger.info("Stopping server...")
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
