#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""
Online serving benchmark.

Starts the vllm-omni server, waits for readiness, sends request(s).
Writes per-iteration server generation times to timings.json, falling back to
client wall times when server metrics are unavailable.

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


def _parse_json_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid JSON object: {value!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError(
            f"expected a JSON object, got {type(parsed).__name__}"
        )
    return parsed


# ── Argument parsing ──────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark vllm-omni online serving (T2I / I2I / T2V / I2V)",
    )

    request = parser.add_argument_group("generation request")
    request.add_argument("--model", required=True)
    request.add_argument("--prompt", required=True)
    request.add_argument("--height", type=int, required=True)
    request.add_argument("--width", type=int, required=True)
    request.add_argument("--num_inference_steps", type=int, required=True)
    request.add_argument("--negative_prompt")
    request.add_argument("--guidance_scale", type=float)
    request.add_argument("--true_cfg_scale", type=float)
    request.add_argument("--seed", type=int, default=42)
    request.add_argument("--num_frames", type=int, help="Set for video generation")
    request.add_argument("--fps", type=int, help="Output video frame rate")
    request.add_argument(
        "--aspect_ratio",
        help='Named output ratio (e.g. "16:9"). MiniMax-H3 T2VA requires one.',
    )
    request.add_argument(
        "--extra_params",
        type=_parse_json_object,
        help=(
            "JSON object posted as extra_params. Model-specific request fields live "
            "here, e.g. '{\"task\":\"t2va\"}' or '{\"flow_shift\":12.0}'."
        ),
    )
    request.add_argument("--input_images", help="Input image path (I2V or I2I)")

    server = parser.add_argument_group("server")
    server.add_argument(
        "--attention_backend",
        help=(
            "Override the diffusion attention backend (e.g. FLASH_ATTN, TORCH_SDPA). "
            "Passed as --diffusion-attention-backend. Defaults to platform auto-detection when unset."
        ),
    )
    server.add_argument(
        "--diffusion_attention_config",
        type=_parse_json_object,
        help=(
            "JSON object passed to vllm-omni --diffusion-attention-config. "
            'Example: \'{"default":{"backend":"AITER_QUANT_ATTN",'
            '"aiter_quant":{"format":"mxfp4"}}}\'.'
        ),
    )
    server.add_argument("--ulysses_degree", type=int, default=1)
    server.add_argument("--ring_degree", type=int, default=1)
    server.add_argument("--ulysses_mode", help="advanced_uaa enables uneven head/sequence shapes")
    server.add_argument("--use_cfg_parallel", action=argparse.BooleanOptionalAction, default=False)
    server.add_argument("--use_parallel_vae", action=argparse.BooleanOptionalAction, default=False)
    server.add_argument("--use_torch_compile", action="store_true")
    quantization = server.add_mutually_exclusive_group()
    quantization.add_argument(
        "--use_fp4_gemms",
        action="store_true",
        help="Quantize the diffusion transformer to online MXFP4 W4A4.",
    )
    quantization.add_argument(
        "--use_fp8_gemms",
        action="store_true",
        help="Quantize the diffusion transformer with native online FP8 W8A8.",
    )
    server.add_argument(
        "--transformer_2_quantization_method",
        choices=("fp8", "mxfp4"),
        help="Optional quantization method for a second diffusion transformer.",
    )
    server.add_argument("--use_hsdp", action="store_true")
    server.add_argument("--enable_slicing", action="store_true")
    server.add_argument("--enable_tiling", action="store_true")
    server.add_argument(
        "--task_type",
        help="Startup task partition for modular models (e.g. fl2va for MiniMax-H3)",
    )
    server.add_argument(
        "--vae_parallel_mode",
        choices=("tile", "spatial_shard_height", "spatial_shard_width"),
        help="VAE parallel decode strategy passed as --vae-parallel-mode",
    )
    server.add_argument("--port", type=int, default=8098)

    benchmark = parser.add_argument_group("benchmark")
    benchmark.add_argument("--num_iterations", type=int, required=True)
    benchmark.add_argument("--output-directory", default="./output")
    benchmark.add_argument("--warmup_calls", type=int, default=0)
    benchmark.add_argument(
        "--health_timeout",
        type=int,
        default=800,
        help="Seconds to wait for server readiness",
    )
    benchmark.add_argument(
        "--request_timeout",
        type=int,
        default=800,
        help="Seconds to wait per inference request",
    )
    benchmark.add_argument(
        "--profile",
        action="store_true",
        help=(
            "Call /start_profile and /stop_profile around timed iterations. "
            "Requires VLLM_TORCH_PROFILER_DIR and starts the server with "
            "--profiler-config so those endpoints are registered."
        ),
    )
    return parser.parse_args()


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
    if args.task_type:
        cmd += ["--task-type", args.task_type]

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
    else:
        cmd += ["--diffusion-compile-reorder-comm-overlap"]
    if args.use_fp4_gemms:
        transformer_quantization_config = {"method": "mxfp4"}
    elif args.use_fp8_gemms:
        transformer_quantization_config = {"method": "fp8"}
    else:
        transformer_quantization_config = None
    transformer_2_quantization_method = args.transformer_2_quantization_method
    if transformer_quantization_config is not None or transformer_2_quantization_method is not None:
        quantization_config = {"text_encoder": None, "vae": None}
        if transformer_quantization_config is not None:
            quantization_config["transformer"] = transformer_quantization_config
        if transformer_2_quantization_method is not None:
            quantization_config["transformer_2"] = {"method": transformer_2_quantization_method}
        cmd += [
            "--diffusion-quantization-config",
            json.dumps(quantization_config, separators=(",", ":")),
        ]
    if args.use_hsdp:
        cmd += ["--use-hsdp"]
    if args.enable_slicing:
        cmd += ["--vae-use-slicing"]
    if args.enable_tiling:
        cmd += ["--vae-use-tiling"]
    if args.vae_parallel_mode:
        cmd += ["--vae-parallel-mode", args.vae_parallel_mode]
    if args.attention_backend:
        cmd += ["--diffusion-attention-backend", args.attention_backend.upper()]
    if args.diffusion_attention_config is not None:
        cmd += [
            "--diffusion-attention-config",
            json.dumps(args.diffusion_attention_config, separators=(",", ":")),
        ]
    if args.profile:
        profile_dir = os.environ.get("VLLM_TORCH_PROFILER_DIR")
        if profile_dir is None:
            raise ValueError("VLLM_TORCH_PROFILER_DIR environment variable is not set")
        os.makedirs(profile_dir, exist_ok=True)
        profiler_config = {
            "profiler": "torch",
            "torch_profiler_dir": profile_dir,
            "torch_profiler_with_stack": False,
            "torch_profiler_record_shapes": True,
        }
        cmd += [
            "--profiler-config",
            json.dumps(profiler_config, separators=(",", ":")),
        ]

    return cmd


def start_server(args: argparse.Namespace) -> subprocess.Popen:
    cmd = build_serve_cmd(args)
    env = os.environ.copy()
    env.setdefault("VLLM_OMNI_VIDEO_SYNC_TIMEOUT", str(args.request_timeout))
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

def _timed_post(url: str, timeout: int, **kwargs) -> tuple[requests.Response, float]:
    t0 = time.perf_counter()
    resp = requests.post(url, timeout=timeout, **kwargs)
    wall_time = time.perf_counter() - t0
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    return resp, wall_time


def _log_result(
    output_path: str,
    wall_time: float,
    gen_time: float,
    stage_durations: dict,
    peak_memory: str | float | None,
) -> None:
    logger.info("  Saved:           %s", output_path)
    logger.info("  Wall time:       %.3fs", wall_time)
    logger.info("  Gen time:        %.3fs", gen_time)
    logger.info("  Stage durations: %s", json.dumps(stage_durations) if stage_durations else "N/A")
    logger.info("  Peak memory:     %s MB", peak_memory if peak_memory is not None else "N/A")


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
    if args.aspect_ratio is not None:
        data["aspect_ratio"] = args.aspect_ratio
    if args.extra_params:
        data["extra_params"] = json.dumps(args.extra_params)
    if args.negative_prompt is not None:
        data["negative_prompt"] = args.negative_prompt
    input_file = open(args.input_images, "rb") if args.input_images else None
    files = {"input_reference": input_file} if input_file else None

    try:
        resp, wall_time = _timed_post(
            url,
            args.request_timeout,
            data=data,
            files=files,
        )
    finally:
        if input_file:
            input_file.close()

    with open(output_path, "wb") as f:
        f.write(resp.content)

    gen_time, stage_durations, peak_memory = _response_metrics(resp, wall_time)
    _log_result(output_path, wall_time, gen_time, stage_durations, peak_memory)

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
    if args.extra_params:
        params["extra_params"] = args.extra_params
    if args.input_images:
        url = f"{base_url}/v1/images/edits"
        form = dict(params)
        if "extra_params" in form:
            form["extra_params"] = json.dumps(form["extra_params"])
        image_file = open(args.input_images, "rb")
        try:
            resp, wall_time = _timed_post(
                url,
                args.request_timeout,
                data=form,
                files={"image": image_file},
            )
        finally:
            image_file.close()
    else:
        url = f"{base_url}/v1/images/generations"
        resp, wall_time = _timed_post(
            url,
            args.request_timeout,
            json=params,
        )

    response_json = resp.json()
    img_b64 = response_json["data"][0]["b64_json"]
    with open(output_path, "wb") as f:
        f.write(base64.b64decode(img_b64))

    gen_time, stage_durations, peak_memory = _response_metrics(resp, wall_time, response_json)
    _log_result(output_path, wall_time, gen_time, stage_durations, peak_memory)

    return gen_time


def _response_metrics(
    resp: requests.Response,
    wall_time: float,
    response_json: dict | None = None,
) -> tuple[float, dict, str | float | None]:
    """Extract server-side generation metrics from the response.

    Video endpoints return metrics in response headers, while image endpoints
    return them in the JSON body. Prefer headers when present, and fall back to
    client wall time if stage_0_gen_ms is unavailable.
    """

    metrics = (response_json or {}).get("metrics") or {}

    stage_durations_header = resp.headers.get("X-Stage-Durations")
    if stage_durations_header:
        stage_durations = json.loads(stage_durations_header)
    else:
        stage_durations = metrics.get("stage_durations") or {}

    peak_memory = resp.headers.get(
        "X-Peak-Memory-MB",
        metrics.get("peak_memory_mb"),
    )

    gen_ms = stage_durations.get("stage_0_gen_ms")
    if gen_ms is None:
        logger.warning("stage_0_gen_ms unavailable — falling back to client wall time.")
        gen_time = wall_time
    else:
        gen_time = float(gen_ms) / 1000.0

    return gen_time, stage_durations, peak_memory


def send_request(base_url: str, args: argparse.Namespace, output_path: str) -> float:
    if args.num_frames is not None:
        return _send_video_request(base_url, args, output_path)
    return _send_image_request(base_url, args, output_path)


def _run_iterations(
    label: str,
    count: int,
    base_url: str,
    args: argparse.Namespace,
    output_path: str,
) -> list[float]:
    gen_times = []
    for i in range(1, count + 1):
        logger.info("=== %s %d/%d ===", label, i, count)
        gen_time = send_request(base_url, args, output_path)
        gen_times.append(gen_time)
    return gen_times


# ── Profiling ─────────────────────────────────────────────────────────────────

def _call_profile_endpoint(base_url: str, action: str, timeout: int) -> None:
    resp = requests.post(f"{base_url}/{action}", timeout=timeout)
    resp.raise_for_status()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    base_url = f"http://localhost:{args.port}"
    ext = "mp4" if args.num_frames is not None else "png"

    proc = start_server(args)
    try:
        wait_for_health(base_url, args.health_timeout, proc)
        os.makedirs(args.output_directory, exist_ok=True)

        output_path = os.path.join(args.output_directory, f"output.{ext}")

        # Warmup iterations (timing discarded, output overwritten)
        if args.warmup_calls > 0:
            logger.info("Running %d warmup call(s)...", args.warmup_calls)
            _run_iterations("Warmup", args.warmup_calls, base_url, args, output_path)
            logger.info("Warmup complete.")

        # Timed iterations — output is overwritten each time, last one is kept
        if args.profile:
            _call_profile_endpoint(base_url, "start_profile", args.request_timeout)

        try:
            logger.info("Running %d timed iteration(s)...", args.num_iterations)
            gen_times = _run_iterations("Iteration", args.num_iterations, base_url, args, output_path)
        finally:
            if args.profile:
                _call_profile_endpoint(base_url, "stop_profile", args.request_timeout)

        with open(os.path.join(args.output_directory, "timings.json"), "w") as f:
            json.dump(gen_times, f)

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
