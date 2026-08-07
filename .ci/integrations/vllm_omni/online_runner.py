#!/usr/bin/env python3
"""
Online serving benchmark.

Starts the vllm-omni server, waits for readiness, sends request(s).
Writes per iteration wall times to timings.json.

Endpoint is auto-detected from args:
  num_frames provided → /v1/videos/sync        (T2V or I2V)
  otherwise:
    input_images set  → /v1/images/edits       (I2I)
    no input_images   → /v1/images/generations (T2I)
"""

import argparse
import base64
import json
import logging
import os
import shlex
import signal
import subprocess
import sys
import time

import requests

logger = logging.getLogger(__name__)

ENDPOINT_VIDEO = "video"
ENDPOINT_IMAGE = "image"


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark vllm-omni online serving (T2I / I2I / T2V / I2V)",
    )

    # Required core args
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--num_inference_steps", type=int, required=True)
    parser.add_argument("--num_iterations", type=int, required=True)

    # Generation args
    parser.add_argument("--negative_prompt", default=None)
    parser.add_argument("--guidance_scale", type=float, default=None)
    parser.add_argument("--true_cfg_scale", type=float, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num_frames", type=int, default=None, help="Set for video generation")
    parser.add_argument("--fps", type=int, default=None, help="Output video frame rate")
    parser.add_argument("--max_sequence_length", type=int, default=None)
    parser.add_argument("--input_images", default=None, help="Input image path (I2V or I2I)")

    # Serve flags — passed to the vllm-omni serve command
    parser.add_argument("--attention_backend", default=None,
                        help="Override DIFFUSION_ATTENTION_BACKEND (e.g. FLASH_ATTN, TORCH_SDPA). "
                             "Defaults to platform auto-detection when unset.")
    parser.add_argument("--ulysses_degree", type=int, default=1)
    parser.add_argument("--ring_degree", type=int, default=1)
    parser.add_argument("--ulysses_mode", type=str, default=None, help="advanced_uaa enables uneven head/sequence shapes")
    parser.add_argument("--use_cfg_parallel", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--use_parallel_vae", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--use_torch_compile", action="store_true", default=False)
    parser.add_argument(
        "--diffusion_compile_reorder_comm_overlap",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Enable Inductor compute/communication overlap reordering.",
    )
    parser.add_argument("--use_hsdp", action="store_true", default=False)
    parser.add_argument("--enable_slicing", action="store_true", default=False)
    parser.add_argument("--enable_tiling", action="store_true", default=False)
    parser.add_argument("--port", type=int, default=8098)

    # Benchmark control
    parser.add_argument("--output-directory", default="./output")
    parser.add_argument("--warmup_calls", type=int, default=0)
    parser.add_argument("--health_timeout", type=int, default=600, help="Seconds to wait for server readiness")
    parser.add_argument("--request_timeout", type=int, default=600, help="Seconds to wait per inference request")
    parser.add_argument("--profile", action="store_true", default=False)
    return parser.parse_args()


# ── Endpoint detection ────────────────────────────────────────────────────────

def detect_endpoint(args: argparse.Namespace) -> str:
    """Derive which API endpoint family to use from the supplied arguments."""
    if args.num_frames is not None:
        return ENDPOINT_VIDEO
    return ENDPOINT_IMAGE


def output_extension(endpoint: str) -> str:
    return "mp4" if endpoint == ENDPOINT_VIDEO else "png"


# ── Server management ─────────────────────────────────────────────────────────

def build_serve_cmd(args: argparse.Namespace) -> list[str]:
    cfg_parallel = 2 if args.use_cfg_parallel else 1
    vae_parallel = (
        args.ulysses_degree * args.ring_degree * cfg_parallel
        if args.use_parallel_vae
        else 1
    )

    cmd = [
        "vllm-omni", "serve", args.model, "--omni",
        "--port", str(args.port),
    ]

    if args.ulysses_degree > 1:
        cmd += ["--usp", str(args.ulysses_degree)]
    if args.ring_degree > 1:
        cmd += ["--ring", str(args.ring_degree)]
    if cfg_parallel > 1:
        cmd += ["--cfg-parallel-size", str(cfg_parallel)]
    if vae_parallel > 1:
        cmd += ["--vae-patch-parallel-size", str(vae_parallel)]

    if args.ulysses_mode:
        cmd += ["--ulysses-mode", args.ulysses_mode]

    if not args.use_torch_compile:
        cmd += ["--enforce-eager"]
    if args.diffusion_compile_reorder_comm_overlap:
        cmd += ["--diffusion-compile-reorder-comm-overlap"]
    if args.use_hsdp:
        cmd += ["--use-hsdp"]
    if args.enable_slicing:
        cmd += ["--vae-use-slicing"]
    if args.enable_tiling:
        cmd += ["--vae-use-tiling"]

    return cmd


def start_server(args: argparse.Namespace) -> subprocess.Popen:
    cmd = build_serve_cmd(args)
    env = os.environ.copy()
    if args.attention_backend is not None:
        env["DIFFUSION_ATTENTION_BACKEND"] = args.attention_backend.upper()
        logger.info("Starting server: %s  [DIFFUSION_ATTENTION_BACKEND=%s]",
                    shlex.join(cmd), env["DIFFUSION_ATTENTION_BACKEND"])
    else:
        logger.info("Starting server: %s", shlex.join(cmd))
    return subprocess.Popen(cmd, env=env, preexec_fn=os.setsid)


def wait_for_health(base_url: str, timeout: int, proc: subprocess.Popen) -> None:
    logger.info("Waiting for server to be ready (timeout %ds)...", timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("Server process exited unexpectedly.")
        try:
            resp = requests.get(f"{base_url}/health", timeout=5)
            if resp.ok:
                elapsed = timeout - (deadline - time.time())
                logger.info("Server ready (took %.0fs).", elapsed)
                return
        except requests.ConnectionError:
            pass
        time.sleep(5)
    raise RuntimeError(f"Server did not become ready within {timeout}s.")


# ── Request helpers ───────────────────────────────────────────────────────────

def _send_video_request(base_url: str, args: argparse.Namespace, output_path: str) -> float:
    url = f"{base_url}/v1/videos/sync"

    data: dict = {
        "prompt": args.prompt,
        "height": str(args.height),
        "width": str(args.width),
        "num_frames": str(args.num_frames),
        "num_inference_steps": str(args.num_inference_steps),
        "seed": str(args.seed),
    }
    if args.guidance_scale is not None:
        data["guidance_scale"] = str(args.guidance_scale)
    if args.true_cfg_scale is not None:
        data["true_cfg_scale"] = str(args.true_cfg_scale)
    if args.fps is not None:
        data["fps"] = str(args.fps)
    if args.max_sequence_length is not None:
        data["extra_params"] = json.dumps({"max_sequence_length": args.max_sequence_length})
    if args.negative_prompt is not None:
        data["negative_prompt"] = args.negative_prompt
    input_file = open(args.input_images, "rb") if args.input_images else None
    files = {"input_reference": input_file} if input_file else None

    try:
        t0 = time.perf_counter()
        resp = requests.post(url, data=data, files=files, timeout=args.request_timeout)
        wall_time = time.perf_counter() - t0
    finally:
        if input_file:
            input_file.close()

    _check_response(resp)

    with open(output_path, "wb") as f:
        f.write(resp.content)

    gen_time = _stage_gen_time(resp, wall_time)

    logger.info("  Saved:           %s", output_path)
    logger.info("  Wall time:       %.3fs", wall_time)
    logger.info("  Gen time:        %.3fs", gen_time)
    logger.info("  Stage durations: %s", resp.headers.get("X-Stage-Durations", "N/A"))
    logger.info("  Peak memory:     %s MB", resp.headers.get("X-Peak-Memory-MB", "N/A"))

    return gen_time


def _send_image_request(base_url: str, args: argparse.Namespace, output_path: str) -> float:
    """Handles both T2I (/v1/images/generations) and I2I (/v1/images/edits).

    When input_images is set the request is sent as multipart form to the edits
    endpoint. Otherwise a JSON body is posted to the generations endpoint.
    """
    params: dict = {
        "prompt": args.prompt,
        "size": f"{args.width}x{args.height}",
        "num_inference_steps": args.num_inference_steps,
        "seed": args.seed,
    }
    if args.guidance_scale is not None:
        params["guidance_scale"] = args.guidance_scale
    if args.true_cfg_scale is not None:
        params["true_cfg_scale"] = args.true_cfg_scale
    if args.negative_prompt is not None:
        params["negative_prompt"] = args.negative_prompt
    if args.max_sequence_length is not None:
        params["extra_params"] = {"max_sequence_length": args.max_sequence_length}
    if args.input_images:
        url = f"{base_url}/v1/images/edits"
        form = dict(params)
        if "extra_params" in form:
            form["extra_params"] = json.dumps(form["extra_params"])
        image_file = open(args.input_images, "rb")
        try:
            t0 = time.perf_counter()
            resp = requests.post(url, data=form, files={"image": image_file}, timeout=args.request_timeout)
            wall_time = time.perf_counter() - t0
        finally:
            image_file.close()
    else:
        url = f"{base_url}/v1/images/generations"
        t0 = time.perf_counter()
        resp = requests.post(url, json=params, timeout=args.request_timeout)
        wall_time = time.perf_counter() - t0

    _check_response(resp)

    img_b64 = resp.json()["data"][0]["b64_json"]
    with open(output_path, "wb") as f:
        f.write(base64.b64decode(img_b64))

    gen_time = _stage_gen_time(resp, wall_time)

    logger.info("  Saved:      %s", output_path)
    logger.info("  Wall time:  %.3fs", wall_time)
    logger.info("  Gen time:   %.3fs", gen_time)
    logger.info("  Stage durations: %s", resp.headers.get("X-Stage-Durations", "N/A"))
    logger.info("  Peak memory:     %s MB", resp.headers.get("X-Peak-Memory-MB", "N/A"))

    return gen_time


def _check_response(resp: requests.Response) -> None:
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")


def _stage_gen_time(resp: requests.Response, wall_time: float) -> float:
    """Return stage_0_gen_ms (seconds) from X-Stage-Durations if available.

    stage_0_gen_ms is the server-side diffusion engine time: from request
    submission to output ready, excluding HTTP and serialisation overhead.
    This is the closest server-side equivalent to the offline CUDA measurement.
    Falls back to client wall time when the header is absent.
    """
    header = resp.headers.get("X-Stage-Durations")
    if header:
        try:
            durations = json.loads(header)
            gen_ms = durations.get("stage_0_gen_ms")
            if gen_ms is not None:
                return float(gen_ms) / 1000.0
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    logger.warning("X-Stage-Durations header absent — falling back to client wall time.")
    return wall_time


def send_request(endpoint: str, base_url: str, args: argparse.Namespace, output_path: str) -> float:
    if endpoint == ENDPOINT_VIDEO:
        return _send_video_request(base_url, args, output_path)
    return _send_image_request(base_url, args, output_path)


# ── Profiling ─────────────────────────────────────────────────────────────────

def _call_profile_endpoint(base_url: str, action: str, timeout: int) -> None:
    resp = requests.post(f"{base_url}/{action}", timeout=timeout)
    resp.raise_for_status()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    base_url = f"http://localhost:{args.port}"
    endpoint = detect_endpoint(args)
    ext = output_extension(endpoint)

    logger.info("Endpoint type: %s", endpoint)

    proc = start_server(args)
    try:
        wait_for_health(base_url, args.health_timeout, proc)
        os.makedirs(args.output_directory, exist_ok=True)

        output_path = os.path.join(args.output_directory, f"output.{ext}")

        # Warmup iterations (timing discarded, output overwritten)
        if args.warmup_calls > 0:
            logger.info("Running %d warmup call(s)...", args.warmup_calls)
            for i in range(args.warmup_calls):
                logger.info("=== Warmup %d/%d ===", i + 1, args.warmup_calls)
                send_request(endpoint, base_url, args, output_path)
            logger.info("Warmup complete.")

        # Timed iterations — output is overwritten each time, last one is kept
        if args.profile:
            _call_profile_endpoint(base_url, "start_profile", args.request_timeout)

        elapsed_times = []
        logger.info("Running %d timed iteration(s)...", args.num_iterations)
        for i in range(1, args.num_iterations + 1):
            logger.info("=== Iteration %d/%d ===", i, args.num_iterations)
            wall_time = send_request(endpoint, base_url, args, output_path)
            elapsed_times.append(wall_time)

        if args.profile:
            _call_profile_endpoint(base_url, "stop_profile", args.request_timeout)

        with open(os.path.join(args.output_directory, "timings.json"), "w") as f:
            json.dump(elapsed_times, f)

        logger.info("Done. %d iteration(s) completed.", args.num_iterations)
        logger.info("Server command: %s", shlex.join(build_serve_cmd(args)))

    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    finally:
        logger.info("Stopping server...")
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=30)
        except Exception as exc:
            logger.warning("Error stopping server: %s", exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
