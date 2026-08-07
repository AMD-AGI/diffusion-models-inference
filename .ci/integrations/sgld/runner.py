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
        help="The input videos to run",
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

    values = {
        "model-path": args.model,
        "height": args.height,
        "width": args.width,
        "ulysses-degree": args.ulysses_degree,
        "ring-degree": args.ring_degree,
        "num-gpus": num_gpus,
        "num-inference-steps": args.num_inference_steps,
        "guidance-scale": args.guidance_scale,
        "prompt": f'"{args.prompt}"',
        "dit-cpu-offload": "False",
        "dit-layerwise-offload": "False",
        "text-encoder-cpu-offload": "False",
        "image-encoder-cpu-offload": "False",
        "vae-cpu-offload": "False",
        "warmup": "True",
        "warmup-steps": 2,
        "vae-precision": "bf16",
        "image-encoder-precision": "bf16",
        "output-path": args.output_directory,
    }

    for key, value in values.items():
        cmd.extend([f"--{key}", str(value)])

    if args.num_frames is not None:
        cmd.extend(["--num-frames", str(args.num_frames)])

    if args.seed is not None:
        cmd.extend(["--seed", str(args.seed)])

    if args.negative_prompt is not None:
        cmd.extend(["--negative-prompt", f'"{args.negative_prompt}"'])

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

    timing = None
    ansi_escape = r'\x1b\[[0-9;]*m'
    match = re.search(rf'Warmed-up request processed in {ansi_escape}?([\d.]+){ansi_escape}? seconds', command_output)
    if match:
        timing = float(match.group(1))
        print(f"Extracted post-warmup time: {timing} seconds")
    else:
        raise ValueError("Could not find post-warmup time in command output")

    with open(os.path.join(args.output_directory, "timings.json"), "w") as f:
        json.dump([timing], f)

def run_api(args):

    num_gpus = args.ulysses_degree  # TODO: Add more parallelization strategies

    generator = DiffGenerator.from_pretrained(
        model_path=args.model,
        num_gpus=num_gpus,
        dit_layerwise_offload=False,
        text_encoder_cpu_offload=False,
        image_encoder_cpu_offload=False,
        sp_degree=num_gpus,
        dit_cpu_offload=False,
        vae_cpu_offload=False,
        pin_cpu_memory=True,
        warmup=True,
        vae_precision="bf16",
        enable_torch_compile=args.use_torch_compile,
    )

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
