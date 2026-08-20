import os
import argparse
import json
import torch
import numpy as np
from PIL import Image

from vllm_omni.diffusion.data import DiffusionParallelConfig
from vllm_omni.entrypoints.omni import Omni
from vllm_omni.inputs.data import OmniDiffusionSamplingParams
from diffusers.utils import export_to_video


def parse_args() -> argparse.Namespace:

    parser = argparse.ArgumentParser(
        description="Run VLLM Omni",
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
        "--true_cfg_scale",
        type=float,
        required=False,
        default=None,
    )
    parser.add_argument(
        "--height",
        type=int,
        required=True,
        help="The height to run",
    )
    parser.add_argument(
        "--width",
        type=int,
        required=True,
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
        "--fps",
        type=int,
        required=False,
        default=16,
        help="Output video frame rate",
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
        "--ulysses_mode",
        type=str,
        required=False,
        default="strict",
        help="Ulysses sequence-parallel mode: 'strict' (default) or 'advanced_uaa' for uneven head/sequence shapes",
    )
    parser.add_argument(
        "--ring_degree",
        type=int,
        required=False,
        default=1,
        help="The ring degree for ring sequence parallelism",
    )
    parser.add_argument(
        "--use_cfg_parallel",
        required=False,
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to use CFG parallelism",
    )
    parser.add_argument(
        "--use_parallel_vae",
        required=False,
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Whether to use VAE patch parallelism",
    )
    parser.add_argument(
        "--use_hsdp",
        required=False,
        action="store_true",
        default=False,
        help="Whether to use HSDP (Hybrid Sharded Data Parallel) for model weight sharding",
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
    quantization = parser.add_mutually_exclusive_group()
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
    parser.add_argument(
        "--transformer_2_quantization_method",
        choices=("fp8", "mxfp4"),
        help="Optional quantization method for a second diffusion transformer.",
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
        default=None,
        help="Override DIFFUSION_ATTENTION_BACKEND (e.g. FLASH_ATTN, TORCH_SDPA). "
             "Defaults to platform auto-detection when unset.",
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
        "--profile",
        required=False,
        action="store_true",
        help="Whether to profile the model. User has to also set env variable VLLM_TORCH_PROFILER_DIR to the directory to save the profile.",
    )
    return parser.parse_args()


def run_omni(omni, generation_args, sampling_params):
    output = omni.generate(
        generation_args,
        OmniDiffusionSamplingParams(
            **sampling_params,
        ),
    )
    return output

def save_output(output, elapsed_times, args):
    os.makedirs(args.output_directory, exist_ok=True)
    json.dump(elapsed_times, open(os.path.join(args.output_directory, "timings.json"), "w"))
    if not output or len(output) == 0:
        raise ValueError("No output generated from omni.generate()")

    first_output = output[0]
    data = first_output.images[0]

    if isinstance(data, Image.Image):
        image = data
        image_name = f"output.png"
        image_path = os.path.join(args.output_directory, image_name)
        image.save(image_path)
        print(f"Saved image to {image_path}")
    else:
        frames = data

        ## This seems... like a mess...
        ## Copied straight from the vllm omni examples
        if isinstance(frames, torch.Tensor):
            video_tensor = frames.detach().cpu()
            if video_tensor.dim() == 5:
                # [B, C, F, H, W] or [B, F, H, W, C]
                if video_tensor.shape[1] in (3, 4):
                    video_tensor = video_tensor[0].permute(1, 2, 3, 0)
                else:
                    video_tensor = video_tensor[0]
            elif video_tensor.dim() == 4 and video_tensor.shape[0] in (3, 4):
                video_tensor = video_tensor.permute(1, 2, 3, 0)
            # If float, assume [-1,1] and normalize to [0,1]
            if video_tensor.is_floating_point():
                video_tensor = video_tensor.clamp(-1, 1) * 0.5 + 0.5
            video_array = video_tensor.float().numpy()
        else:
            video_array = frames
            if hasattr(video_array, "shape") and video_array.ndim == 5:
                video_array = video_array[0]

        # Convert 4D array (frames, H, W, C) to list of frames for export_to_video
        if isinstance(video_array, np.ndarray) and video_array.ndim == 4:
            video_array = list(video_array)

        video_name = f"output.mp4"
        video_path = os.path.join(args.output_directory, video_name)
        export_to_video(video_array, video_path, fps=args.fps)
        print(f"Saved video to {video_path}")





def main():
    args = parse_args()

    if args.attention_backend is not None:
        os.environ["DIFFUSION_ATTENTION_BACKEND"] = args.attention_backend.upper()
        print(f"DIFFUSION_ATTENTION_BACKEND={os.environ['DIFFUSION_ATTENTION_BACKEND']}")

    parallel_config = DiffusionParallelConfig(
        ulysses_degree=args.ulysses_degree,
        ring_degree=args.ring_degree,
        cfg_parallel_size=2 if args.use_cfg_parallel else 1,
        vae_patch_parallel_size=(
            args.ulysses_degree * args.ring_degree * (2 if args.use_cfg_parallel else 1)
            if args.use_parallel_vae else 1
        ),
        use_hsdp=args.use_hsdp,
        ulysses_mode=args.ulysses_mode,
    )

    profiler_config = None
    if args.profile:
        profile_dir = os.environ.get("VLLM_TORCH_PROFILER_DIR")
        if profile_dir is None:
            raise ValueError("VLLM_TORCH_PROFILER_DIR environment variable is not set")
        profiler_config = {
            "profiler": "torch",
            "torch_profiler_dir": profile_dir,
            "torch_profiler_with_stack": False,
            "torch_profiler_record_shapes": True,
        }

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
    else:
        quantization_config = None

    omni = Omni(
        model=args.model,
        vae_use_slicing=args.enable_slicing,
        vae_use_tiling=args.enable_tiling,
        parallel_config=parallel_config,
        enforce_eager=not args.use_torch_compile,
        diffusion_compile_reorder_comm_overlap=args.use_torch_compile,
        diffusion_quantization_config=quantization_config,
        profiler_config=profiler_config,
    )

    generation_args = {
        "prompt": args.prompt,
        "negative_prompt": args.negative_prompt,
    }
    if args.input_images is not None:
        images = [Image.open(image).convert("RGB") for image in args.input_images]
        generation_args["multi_modal_data"] = {"image": images[0]}

    sampling_args = {
        "generator": torch.Generator(device="cuda").manual_seed(args.seed),
        "height": args.height,
        "width": args.width,
        "num_inference_steps": args.num_inference_steps,
    }
    if args.guidance_scale is not None:
        sampling_args["guidance_scale"] = args.guidance_scale
    if args.true_cfg_scale is not None:
        sampling_args["true_cfg_scale"] = args.true_cfg_scale
    if args.num_frames is not None:
        sampling_args["num_frames"] = args.num_frames
    if args.max_sequence_length is not None:
        sampling_args["max_sequence_length"] = args.max_sequence_length


    # Warmup / compile
    print(" ======================== Warming up / compiling... ========================")
    if args.use_torch_compile:
        print(" ======================== Compiling... ========================")
        output = run_omni(omni, generation_args, sampling_args)
        print(" ======================== Compilation complete ========================")
    for i in range(args.warmup_calls):
        print(f" ======================== Warmup call {i}... ========================")
        output = run_omni(omni, generation_args, sampling_args)
        print(f" ======================== Warmup call {i} complete ========================")
    print(" ======================== Warmup / compilation complete ========================")

    if args.profile:
        print(f" ======================== Starting profile in {profiler_config['torch_profiler_dir']}... ========================")
        omni.start_profile()

    elapsed_times = []
    print(" ======================== Running inference... ========================")
    for i in range(args.num_iterations):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        output = run_omni(omni, generation_args, sampling_args)
        end.record()
        torch.cuda.synchronize()
        elapsed_time = start.elapsed_time(end) /1000
        elapsed_times.append(elapsed_time)
        print(f"Iteration {i} time taken: {elapsed_time:.2f}s")

    print(" ======================== Inference complete ========================")

    if args.profile:
        print(" ======================== Stopping profile... ========================")
        _ = omni.stop_profile()
        print(" ======================== Profile complete ========================")

    save_output(output, elapsed_times,args)

if __name__ == "__main__":
    main()
