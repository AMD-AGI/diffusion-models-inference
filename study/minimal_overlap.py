"""Minimal custom-op overlap demo for the maintained PyTorch scheduler.

A plain FlyDSL copy stands in for an asynchronous collective data plane.
The script compiles a launch/wait/GEMM graph and writes a GPU trace for visual
inspection.

Run with one process so c10d can resolve the custom op's group_name:

    torchrun --standalone --nproc-per-node=1 tutorial/minimal_overlap.py
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault(
    "FLYDSL_RUNTIME_CACHE_DIR",
    f"/tmp/tutorial-overlap-rank-{os.environ.get('LOCAL_RANK', '0')}",
)

import torch
import torch.distributed as dist
from torch.profiler import ProfilerActivity, profile

import flydsl.compiler as flyc
import flydsl.expr as fx


_SLEEP_CYCLES = 10_000_000
_MATRIX_SIZE = 4096
_TRACE_PATH = Path(__file__).resolve().with_name("minimal_overlap_trace.json.gz")


@flyc.kernel
def copy_kernel(
    source: fx.Tensor,
    destination: fx.Tensor,
    count: fx.Int32,
):
    source = fx.rocdl.make_buffer_tensor(source)
    destination = fx.rocdl.make_buffer_tensor(destination)
    index = fx.block_idx.x * 256 + fx.thread_idx.x
    if index < count:
        destination[index] = source[index]


@flyc.jit
def launch_copy(
    source: fx.Tensor,
    destination: fx.Tensor,
    count: fx.Int32,
    stream: fx.Stream = fx.Stream(None),
):
    copy_kernel(source, destination, count).launch(
        grid=((count + 255) // 256, 1, 1),
        block=(256, 1, 1),
        stream=stream,
    )


_streams: dict[int, torch.cuda.Stream] = {}


def communication_stream(device: torch.device) -> torch.cuda.Stream:
    index = device.index if device.index is not None else torch.cuda.current_device()
    return _streams.setdefault(index, torch.cuda.Stream(device=device))


@torch.library.custom_op(
    "tutorial_overlap::all_to_all_launch",
    mutates_args=(),
    device_types="cuda",
)
def all_to_all_launch(
    source: torch.Tensor,
    group_name: str,
) -> torch.Tensor:
    del group_name  # Required by the scheduler; unused by this one-rank stand-in.
    output = torch.empty_like(source)
    stream = communication_stream(source.device)
    stream.wait_stream(torch.cuda.current_stream(source.device))
    source.record_stream(stream)
    output.record_stream(stream)
    with torch.cuda.stream(stream):
        # Keep the demo operation pending without adding a fake busy loop to the copy.
        torch.cuda._sleep(_SLEEP_CYCLES)
        launch_copy(source, output, source.numel(), stream)
        completion = torch.cuda.Event()
        completion.record(stream)

    from torch._C._distributed_c10d import PythonCallbackWork, _register_work

    def wait_for_event(timeout) -> bool:
        completion.wait()
        return True

    _register_work(output, PythonCallbackWork(wait_for_event))
    return output


@all_to_all_launch.register_fake
def all_to_all_launch_fake(
    source: torch.Tensor,
    group_name: str,
) -> torch.Tensor:
    return torch.empty_like(source)


def register_inductor_lowering() -> None:
    from torch._inductor import ir
    from torch._inductor.lowering import (
        add_layout_constraint,
        constrain_to_fx_strides,
        register_lowering,
    )

    op = torch.ops.tutorial_overlap.all_to_all_launch.default
    add_layout_constraint(op, constrain_to_fx_strides)

    @register_lowering(op)
    def lower_launch(
        source: ir.TensorBox,
        group_name: str,
    ) -> ir.TensorBox:
        node = ir._CollectiveKernel.create_out_of_place(
            op,
            source,
            group_name,
        )
        return ir.TensorBox.create(node)


register_inductor_lowering()


def estimate_runtime(node, override_size=None) -> float | None:
    if node.target is torch.ops.tutorial_overlap.all_to_all_launch.default:
        return 1.0
    return None


def configure_overlap() -> None:
    torch._inductor.config.reorder_for_compute_comm_overlap = False
    torch._inductor.config.reorder_for_compute_comm_overlap_passes = []
    options = torch._inductor.config.aten_distributed_optimizations
    if hasattr(options, "enable_simple_overlap"):
        options.enable_simple_overlap = False
    options.enable_overlap_scheduling = True
    options.insert_overlap_deps = True
    options.custom_runtime_estimation = estimate_runtime
    options.compute_estimator = "analytical"
    options.pre_bucketing_fsdp_collectives = False


def overlap_candidate(
    source: torch.Tensor,
    compute_input: torch.Tensor,
    group_name: str,
) -> tuple[torch.Tensor, torch.Tensor]:
    launched = all_to_all_launch(source, group_name)
    completed = torch.ops._c10d_functional.wait_tensor.default(launched)
    independent_compute = torch.mm(compute_input, compute_input)
    return completed, independent_compute


def main() -> None:
    local_rank = int(os.environ["LOCAL_RANK"])
    torch.cuda.set_device(local_rank)
    dist.init_process_group("nccl", device_id=torch.device("cuda", local_rank))
    try:
        configure_overlap()

        source = torch.randn(256, device="cuda", dtype=torch.float32)
        matrix = torch.randn(
            _MATRIX_SIZE,
            _MATRIX_SIZE,
            device="cuda",
            dtype=torch.float32,
        )
        group_name = str(dist.group.WORLD.group_name)
        compiled = torch.compile(overlap_candidate, fullgraph=True)

        compiled(source, matrix, group_name)
        torch.cuda.synchronize()

        with profile(
            activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA],
        ) as profiler:
            compiled(source, matrix, group_name)
            torch.cuda.synchronize()
        profiler.export_chrome_trace(str(_TRACE_PATH))
        print(f"trace={_TRACE_PATH}")
    finally:
        dist.destroy_process_group()


if __name__ == "__main__":
    main()
