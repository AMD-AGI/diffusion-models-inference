# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import os
import imageio
import json
import torch
import argparse
import numpy as np
import subprocess
import re
from PIL import Image

from sglang.multimodal_gen import DiffGenerator


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description="Run SGLang-Diffusion",
    )
    parser.add_argument(
        "--model",
        type=str,
        required=True,
        help="The model to run",
    )
    parser.add_argument(
        "--pipeline_class_name",
        type=str,
        required=False,
        default=None,
        help="Explicit SGL-D pipeline class (e.g. LTX2Pipeline). Unset -> "
             "auto-detect, which is non-deterministic for multi-pipeline models.",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        required=True,
        help="The prompt to run",
    )
    parser.add_argument(
        "--negative_prompt",
        type=str,
        required=False,
        help="The negative prompt to run",
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=False,
        default=42,
        help="The seed to run",
    )
    parser.add_argument(
        "--guidance_scale",
        type=float,
        required=False,
        default=None,
        help="The guidance scale to run",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        required=False,
        help="The height to run",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        required=False,
        help="The width to run",
    )
    parser.add_argument(
        "--num_frames",
        type=int,
        required=False,
        default=None,
        help="The number of frames to run",
    )
    parser.add_argument(
        "--num_inference_steps",
        type=int,
        required=True,
        help="The number of inference steps to run",
    )
    parser.add_argument(
        "--ulysses_degree",
        type=int,
        required=True,
        help="The Ulysses degree to run",
    )
    parser.add_argument(
        "--ring_degree",
        type=int,
        default=1,
        help="The ring degree to run",
    )
    parser.add_argument(
        "--use_cfg_parallel",
        action="store_true",
        required=False,
        help="Use CFG parallel to run",
    )
    parser.add_argument(
        "--use_parallel_vae",
        required=False,
        action="store_true",
        help="Run with parallilized VAE decode",
    )
    # Explicit VAE parallel-decode control; unset -> upstream default.
    parser.add_argument(
        "--vae_parallel_decode_mode",
        type=str,
        required=False,
        default=None,
        choices=["tiled", "patch", "spatial_shard", "spatial", "auto"],
        help="Force the VAE parallel-decode strategy. Unset -> upstream default.",
    )
    parser.add_argument(
        "--vae_use_parallel_decode",
        type=str,
        required=False,
        default=None,
        choices=["true", "false"],
        help="Explicitly enable/disable VAE parallel decode (the only way to "
             "express OFF, since run.py drops a YAML bool false).",
    )
    # Optional diffusion VAE decoder (off by default, matching upstream).
    # Decode-side support: sglang sgl-project/sglang#40755 (SP self-attn mask) +
    # sgl-project/sglang#41240 (tile-parallel decode LPT balancing).
    parser.add_argument(
        "--use_diffusion_decoder",
        required=False,
        action="store_true",
        help="Enable the diffusion VAE decoder (--use-diffusion-decoder on the "
             "CLI path; load_diffusion_decoder + the use_diffusion_decoder "
             "sampling param on the API path).",
    )
    # Model variant; unset -> upstream default (distilled).
    parser.add_argument(
        "--model_variant",
        type=str,
        required=False,
        default=None,
        help="Model variant, e.g. 'full' -> LTX-2.5 full DiT (transformer_full). "
             "Unset -> upstream default (distilled).",
    )
    parser.add_argument(
        "--output-directory",
        type=str,
        required=True,
        help="The directory to save the output",
    )
    parser.add_argument(
        "--enable_slicing",
        required=False,
        action="store_true",
        help="Whether to use VAE slicing",
    )
    parser.add_argument(
        "--enable_tiling",
        required=False,
        action="store_true",
        help="Whether to use VAE tiling",
    )
    parser.add_argument(
        "--num_iterations",
        type=int,
        default=1,
        help="Number of times to run the prompt (sequential, for timing averages)",
    )
    parser.add_argument(
        "--use_torch_compile",
        required=False,
        action="store_true",
        help="Whether to use Torch compile",
    )
    parser.add_argument(
        "--input_images",
        nargs="+",
        required=False,
        help="The input images to run",
    )
    parser.add_argument(
        "--attention_backend",
        type=str,
        required=False,
        help="NOT IN USE",
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        required=False,
        default=1,
        help="The batch size to run",
    )
    parser.add_argument(
        "--warmup_calls",
        type=int,
        required=False,
        default=0,
        help="The number of warmup calls to run",
    )
    parser.add_argument(
        "--warmup_steps",
        type=int,
        required=False,
        default=2,
        help="The number of steps in warmup to run",
    )
    parser.add_argument(
        "--max_sequence_length",
        type=int,
        required=False,
        default=None,
        help="The max sequence length to run",
    )
    parser.add_argument(
        "--resize_input_images",
        required=False,
        action="store_true",
        help="Whether to resize the input images to match the height and width",
    )
    parser.add_argument(
        "--run_type",
        type=str,
        default="cli",
        choices=["cli", "api"],
        help="Whether to run through CLI or API",
    )
    parser.add_argument(
        "--use_hybrid_attn_schedule",
        required=False,
        action="store_true",
        help="Whether to use hybrid attention or not"
    )
    parser.add_argument(
        "--hybrid_attn_high_precision_backend",
        required=False,
        default=None,
        help="High precision backend to be used in hybrid attention"
    )
    parser.add_argument(
        "--hybrid_attn_low_precision_backend",
        required=False,
        default=None,
        help="Low precision backend to be used in hybrid attention"
    )
    parser.add_argument(
        "--use_fp8_gemms",
        required=False,
        action="store_true",
        help="Whether to use FP8 quantized GEMMs"
    )
    parser.add_argument(
        "--use_fp4_gemms",
        required=False,
        action="store_true",
        help="Whether to use MXFP4 quantized GEMMs"
    )
    parser.add_argument(
        "--config",
        type=str,
        required=False,
        default=None,
        help=(
            "Extra generation config as a JSON object (e.g. task, conditions, target). "
            "Written to <output-directory>/config.json and passed to sglang as --config."
        ),
    )
    parser.add_argument(
        "--quantization_ignored_layers",
        nargs="+",
        required=False,
        default=None,
        help="Layer name patterns to keep unquantized (e.g. blocks.0 blocks.1)"
    )
    return parser.parse_args()

def run_sgld(generator, sampling_params):
    output = generator.generate(
        sampling_params_kwargs=sampling_params,
    )
    return output

def save_output(output, elapsed_times, args):
    with open(os.path.join(args.output_directory, "timings.json"), "w") as f:
        json.dump(elapsed_times, f)

    if not output or len(output) == 0:
        raise ValueError("No output generated from generator.generate()")

    is_image = len(output) == 1
    if is_image:
        imageio.imwrite(os.path.join(args.output_directory, "output.png"), output[0], quality=75)
    else:
        imageio.mimsave(os.path.join(args.output_directory, "output.mp4"), output, fps=24, codec="libx264")


def _parallel_degree(ulysses_degree: int, ring_degree: int, use_cfg_parallel: bool) -> int:
    return (int(use_cfg_parallel) + 1) * ulysses_degree * ring_degree


def _maybe_local_snapshot(model: str, needs_resolve: bool, reason: str) -> str:
    # model-variant and the diffusion decoder need a resolved local snapshot dir:
    # the pipeline's subfolder / component checks run against the raw path, so a
    # bare HF repo id is a false-negative. Resolve to the downloaded snapshot.
    if not needs_resolve or os.path.isdir(str(model)):
        return model
    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(model, local_files_only=True)
        print(f"Resolved {model} -> local snapshot {path} for {reason}")
        return path
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: could not resolve local snapshot for {model}: {e}")
        return model


def _write_config_file(config_json: str, output_directory: str) -> str:
    """
    Dump the --config JSON payload into the output directory and return its path.

    sglang takes the extra generation config (task, conditions, target, ...) as a
    path to a JSON file, while the benchmark YAMLs carry it inline, so the values
    are materialised here. Writing it next to the other run artifacts keeps it
    around for debugging.
    """
    config = json.loads(config_json)
    if not isinstance(config, dict):
        raise ValueError(f"--config must be a JSON object, got {type(config).__name__}")

    os.makedirs(output_directory, exist_ok=True)
    config_path = os.path.join(output_directory, "config.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Wrote generation config to {config_path}: {json.dumps(config)}")
    return config_path


def run_cli(args):
    cmd = ["sglang", "generate"]


    num_gpus = _parallel_degree(
        args.ulysses_degree, args.ring_degree, args.use_cfg_parallel
    )

    reason = (f"--model-variant {args.model_variant}" if args.model_variant is not None
              else "--use_diffusion_decoder")
    model_path_value = _maybe_local_snapshot(
        args.model, args.model_variant is not None or args.use_diffusion_decoder, reason
    )

    values = {
        "model-path": model_path_value,
        "height": args.height,
        "width": args.width,
        "ulysses-degree": args.ulysses_degree,
        "ring-degree": args.ring_degree,
        "num-gpus": num_gpus,
        "num-inference-steps": args.num_inference_steps,
        "guidance-scale": args.guidance_scale,
        "dit-cpu-offload": "False",
        "dit-layerwise-offload": "False",
        "text-encoder-cpu-offload": "False",
        "image-encoder-cpu-offload": "False",
        "offload-during-compile": "False",
        "vae-cpu-offload": "False",
        "warmup-mode": "request",
        "warmup-steps": args.warmup_steps,
        "vae-precision": "bf16",
        "image-encoder-precision": "bf16",
        "output-path": args.output_directory,
    }

    values = {key:value for key, value in values.items() if value != None}

    for key, value in values.items():
        cmd.extend([f"--{key}", str(value)])

    # Repeat the prompt num_iterations times so sglang runs it sequentially.
    # Each prompt must be its own argv entry (nargs="+"); no shell quoting since
    # subprocess is invoked with a list (shell=False).
    cmd.extend(["--prompt", *([args.prompt] * args.num_iterations)])

    # Pin the pipeline class when requested (auto-detection is non-deterministic).
    if args.pipeline_class_name is not None:
        cmd.extend(["--pipeline-class-name", args.pipeline_class_name])

    # Select the model variant (e.g. LTX-2.5 "full"). Only emitted when set.
    if args.model_variant is not None:
        cmd.extend(["--model-variant", args.model_variant])

    # VAE parallel-decode -> --vae-config.*. Explicit vae_use_parallel_decode
    # ("true"/"false") wins; else use_parallel_vae (store_true) turns it ON;
    # else nothing is emitted (upstream default: on).
    if args.vae_parallel_decode_mode is not None:
        cmd.extend(["--vae-config.parallel-decode-mode", args.vae_parallel_decode_mode])
    vae_use_parallel_decode = args.vae_use_parallel_decode
    if vae_use_parallel_decode is None and args.use_parallel_vae:
        vae_use_parallel_decode = "true"
    if vae_use_parallel_decode is not None:
        cmd.extend(["--vae-config.use-parallel-decode", vae_use_parallel_decode])

    # Diffusion VAE decoder (sglang loads the component when this flag is present).
    if args.use_diffusion_decoder:
        cmd.append("--use-diffusion-decoder")

    if args.num_frames is not None:
        cmd.extend(["--num-frames", str(args.num_frames)])

    if args.seed is not None:
        cmd.extend(["--seed", str(args.seed)])

    if args.negative_prompt is not None:
        cmd.extend(["--negative-prompt", args.negative_prompt])

    if args.input_images is not None:
        cmd.extend(["--image-path"] + args.input_images)

    if args.use_torch_compile:
        cmd.append("--enable-torch-compile")

    if args.use_cfg_parallel:
        cmd.append("--enable-cfg-parallel")

    if args.enable_slicing:
        cmd.append("--vae-slicing")

    if args.enable_tiling:
        cmd.append("--vae-tiling")

    if args.use_hybrid_attn_schedule:
        num_high_start_steps, num_high_end_steps = 5, 5 # TODO: configurable
        hybrid_cmd_value = f"{args.hybrid_attn_high_precision_backend}:{args.hybrid_attn_low_precision_backend}:{num_high_start_steps}:{num_high_end_steps}"
        cmd.extend(["--hybrid-attention-schedule", hybrid_cmd_value])

    if args.use_fp8_gemms:
        cmd.extend(["--quantization", "fp8"])

    if args.use_fp4_gemms:
        cmd.extend(["--quantization", "mxfp4"])

    if args.quantization_ignored_layers:
        cmd.extend(["--quantization-ignored-layers"] + args.quantization_ignored_layers)

    if args.config is not None:
        cmd.extend(["--config", _write_config_file(args.config, args.output_directory)])

    print(f"Running command: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True
    )

    command_output = result.stdout + result.stderr
    print(f"Command output: {command_output}")

    ansi_escape = r'\x1b\[[0-9;]*m'
    timings = [
        float(m)
        for m in re.findall(
            rf'Warmed-up request processed in {ansi_escape}?([\d.]+){ansi_escape}? seconds',
            command_output,
        )
    ]
    if not timings:
        raise ValueError("Could not find post-warmup time in command output")
    print(f"Extracted post-warmup times: {timings} seconds")

    with open(os.path.join(args.output_directory, "timings.json"), "w") as f:
        json.dump(timings, f)

def run_api(args):

    num_gpus = args.ulysses_degree * args.ring_degree

    model_path_value = _maybe_local_snapshot(
        args.model, args.use_diffusion_decoder, "--use_diffusion_decoder (api path)"
    )

    from_pretrained_kwargs = dict(
        model_path=model_path_value,
        num_gpus=num_gpus,
        sp_degree=num_gpus,
        ulysses_degree=args.ulysses_degree,
        ring_degree=args.ring_degree,
        dit_layerwise_offload=False,
        text_encoder_cpu_offload=False,
        image_encoder_cpu_offload=False,
        dit_cpu_offload=False,
        vae_cpu_offload=False,
        pin_cpu_memory=True,
        warmup=True,
        vae_precision="bf16",
        enable_torch_compile=args.use_torch_compile,
    )
    if args.attention_backend:
        from_pretrained_kwargs["attention_backend"] = args.attention_backend
    if args.pipeline_class_name is not None:
        from_pretrained_kwargs["pipeline_class_name"] = args.pipeline_class_name

    # VAE parallel-decode via dotted vae_config.* kwargs; precedence mirrors
    # run_cli (explicit override wins, else use_parallel_vae, else upstream default).
    vae_use_parallel_decode = args.vae_use_parallel_decode
    if vae_use_parallel_decode is None and args.use_parallel_vae:
        vae_use_parallel_decode = "true"
    if vae_use_parallel_decode is not None:
        from_pretrained_kwargs["vae_config.use_parallel_decode"] = (
            vae_use_parallel_decode == "true"
        )
    if args.vae_parallel_decode_mode is not None:
        from_pretrained_kwargs["vae_config.parallel_decode_mode"] = args.vae_parallel_decode_mode

    # Diffusion decoder needs both: load the component here, and set the
    # use_diffusion_decoder sampling param below.
    if args.use_diffusion_decoder:
        from_pretrained_kwargs["load_diffusion_decoder"] = True

    generator = DiffGenerator.from_pretrained(**from_pretrained_kwargs)

    sampling_args = {
        "prompt": args.prompt,
        "seed": args.seed,
        "num_inference_steps": args.num_inference_steps,
        "num_frames": args.num_frames,
        "adjust_frames": False,
        "return_frames": True,
        "save_output": False,
    }
    if args.input_images is not None:
        sampling_args["image_path"] = args.input_images
    if args.guidance_scale is not None:
        sampling_args["guidance_scale"] = args.guidance_scale
    if args.num_frames is not None:
        sampling_args["num_frames"] = args.num_frames
    if args.height is not None:
        sampling_args["height"] = args.height
    if args.width is not None:
        sampling_args["width"] = args.width
    if args.negative_prompt is not None:
        sampling_args["negative_prompt"] = args.negative_prompt
    # Route decode through the diffusion decoder loaded above.
    if args.use_diffusion_decoder:
        sampling_args["use_diffusion_decoder"] = True


    # Warmup / compile
    print(" ======================== Warming up / compiling... ========================")
    if args.use_torch_compile:
        print(" ======================== Compiling... ========================")
        output = run_sgld(generator, sampling_args)
        print(" ======================== Compilation complete ========================")
    for i in range(args.warmup_calls):
        print(f" ======================== Warmup call {i}... ========================")
        output = run_sgld(generator, sampling_args)
        print(f" ======================== Warmup call {i} complete ========================")
    print(" ======================== Warmup / compilation complete ========================")


    elapsed_times = []
    print(" ======================== Running inference... ========================")
    for i in range(args.num_iterations):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        output = run_sgld(generator, sampling_args)
        end.record()
        torch.cuda.synchronize()
        elapsed_time = start.elapsed_time(end) /1000
        elapsed_times.append(elapsed_time)
        print(f"Iteration {i} time taken: {elapsed_time:.2f}s")
    print(" ======================== Inference complete ========================")
    print(f"Average time taken: {np.mean(elapsed_times):.2f}s")

    save_output(output, elapsed_times,args)


def main():
    args = parse_args()

    if args.run_type == "cli":
        run_cli(args)
    elif args.run_type == "api":
        run_api(args)
    else:
        raise ValueError(f"Invalid run type: {args.run_type}")


if __name__ == "__main__":
    main()
