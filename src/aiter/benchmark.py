import sys
from typing import Dict, Optional, Tuple, Union

import pandas
import inspect
import torch
from triton.testing import do_bench

try:
    import aiter.ops.mha
#    from aiter.ops.mha import flash_attn_func as aiter_flash_attn_func
#    from aiter.ops.mha import flash_attn_varlen_func as aiter_varlen_flash_attn_func
    has_aiter = True
except ImportError:
    has_aiter = False

MAP_DTYPE = {
    "bfloat16": torch.bfloat16,
}

SIGNATURE_DEFAULT = {
    "tag": "default",
    'dtype': "bfloat16",
    'bs': 1,
    'seqlen_q': 75600,
    'nheads_q': 3,
    'seqlen_kv': 75600,
    'nheads_kv': 3,
    'headdim_qk': 128,
    'headdim_v': 128,
}

WARMUP = 10  # Warmup iterations
REPS = 100  # Number of repetitions for benchmarking


def create_inputs(config: Dict[str, Union[int, str]], use_bshd: bool = True) -> Tuple[torch.Tensor]:
    """Create input tensors for flash attention with proper dimensions."""
    batch_size = config['bs']
    dtype = MAP_DTYPE[config['dtype']]
    seqlen_q = config['seqlen_q']
    nheads_q = config['nheads_q']
    seqlen_kv = config['seqlen_kv']
    nheads_kv = config['nheads_kv']
    headdim_qk = config['headdim_qk']
    headdim_v = config['headdim_v']

    shape_q = (batch_size, seqlen_q, nheads_q, headdim_qk) if use_bshd else (batch_size, nheads_q, seqlen_q, headdim_qk)
    shape_k = (batch_size, seqlen_kv, nheads_kv, headdim_qk) if use_bshd else (batch_size, nheads_kv, seqlen_kv, headdim_qk)
    shape_v = (batch_size, seqlen_kv, nheads_kv, headdim_v) if use_bshd else (batch_size, nheads_kv, seqlen_kv, headdim_v)

    q = torch.randn(shape_q, dtype=dtype, device="cuda")
    k = torch.randn(shape_k, dtype=dtype, device="cuda")
    v = torch.randn(shape_v, dtype=dtype, device="cuda")

    return q, k, v


def calculate_flops(config: Dict[str, Union[int, str]]) -> int:
    """Calculate FLOPs for flash attention."""
    if config["nheads_q"] != config["nheads_kv"]:
        raise NotImplementedError("FLOPs cannot be calculated for MQA or GQA")
    return (
        config["bs"] *
        config["nheads_q"] *
        config["seqlen_q"] * config["seqlen_kv"] *
        (2 * config["headdim_qk"] + 2 * config["headdim_v"] + 1)
    )


def benchmark_attention(
    backend: str, signatures: Optional[pandas.DataFrame] = None, verbose: bool = False
) -> pandas.DataFrame:
    """Benchmark the flash attention operation."""

    if verbose:
        print(f"Benchmarking attention with backend: {backend}...")
        print(f"Signatures: {signatures}")
    sys.stdout.flush()

    def run_sdpa(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor):
        # Use PyTorch's built-in scaled dot product attention
        output = torch.nn.functional.scaled_dot_product_attention(
            q, k, v,
            attn_mask=None,
            dropout_p=0.0,
            is_causal=False
        )
        torch.cuda.synchronize()

        return output

    def run_aiter_flash_attn(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, has_round_mode: bool = False):
        # Note this will JIT compile on first invocation
        # [aiter] start build [module_fmha_v3_fwd] under /opt/aiter/aiter/jit/build/module_fmha_v3_fwd
        # Successfully preprocessed all matching files.
        # [aiter] finish build [module_fmha_v3_fwd], cost 53.76911977s
        # [aiter] type hints mismatch, override to --> fmha_v3_fwd(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, dropout_p: float, softmax_scale: float, is_causal: bool, window_size_left: int, window_size_right: int, return_softmax_lse: bool, return_dropout_randval: bool, out: Optional[torch.Tensor] = None, bias: Optional[torch.Tensor] = None, alibi_slopes: Optional[torch.Tensor] = None, gen: Optional[torch.Generator] = None) -> list[torch.Tensor]

        if has_round_mode:
            output = aiter.ops.mha.flash_attn_func(
                q, k, v,
                dropout_p=0.0,
                causal=False,
                return_attn_probs=False,
                how_v3_bf16_cvt=2
            )
        else:
            output = aiter.ops.mha.flash_attn_func(
                q, k, v,
                dropout_p=0.0,
                causal=False,
                return_attn_probs=False
        )
        torch.cuda.synchronize()
        return output

    def run_aiter_flash_attn_varlen(q: torch.Tensor, k: torch.Tensor, v: torch.Tensor, has_round_mode: bool = False):
        out_dtype = q.dtype
        b, lq, _, _ = q.shape
        _, lk, _, _ = k.shape
        q_lens = torch.tensor([lq] * b, dtype=torch.int32).to(q.device, non_blocking=True)
        k_lens = torch.tensor([lk] * b, dtype=torch.int32).to(q.device, non_blocking=True)
        cu_seqlens_q=torch.cat([q_lens.new_zeros([1]), q_lens]).cumsum(
                0, dtype=torch.int32).to(q.device, non_blocking=True)
        cu_seqlens_k=torch.cat([k_lens.new_zeros([1]), k_lens]).cumsum(
                0, dtype=torch.int32).to(q.device, non_blocking=True)

        if has_round_mode:
            output = aiter.ops.mha.flash_attn_varlen_func(
                q.flatten(0, 1), k.flatten(0, 1), v.flatten(0, 1),
                cu_seqlens_q, cu_seqlens_k,
                lq, lk,
                dropout_p=0.0,
                causal=False,
                return_attn_probs=False,
                how_v3_bf16_cvt=2
            )
        else:
            output = aiter.ops.mha.flash_attn_varlen_func(
                q.flatten(0, 1), k.flatten(0, 1), v.flatten(0, 1),
                cu_seqlens_q, cu_seqlens_k,
                lq, lk,
                dropout_p=0.0,
                causal=False,
                return_attn_probs=False
        )
        torch.cuda.synchronize()
        return output

    # Select the appropriate function based on the backend
    match backend:
        case "sdpa":
            bench_func = run_sdpa
        case "aiter":
            bench_func = run_aiter_flash_attn
        case "aiter-varlen":
            bench_func = run_aiter_flash_attn_varlen
        case _:
            raise ValueError(f"Unknown backend: {backend}")

    torch.manual_seed(42)

    if signatures is None:
        signatures = pandas.DataFrame([SIGNATURE_DEFAULT])

    data = []
    for idx, signature in signatures.iterrows():
        if signature["nheads_q"] != signature["nheads_kv"]:
            raise NotImplementedError("q and kv head counts must equal, no support for MQA or GQA implemented")

        if verbose:
            print(f"Signature:\n{signature}")

        torch.cuda.empty_cache()

        q, k, v = create_inputs(signature, use_bshd=not (backend == "sdpa"))
        q_, k_, v_ = create_inputs(signature, use_bshd=False)  # for sdpa comparison
        if backend == "aiter":
            has_round_mode = inspect.signature(aiter.ops.mha.flash_attn_func).parameters.get("how_v3_bf16_cvt") is not None
        elif backend == "aiter-varlen":
            has_round_mode = inspect.signature(aiter.ops.mha.flash_attn_varlen_func).parameters.get("how_v3_bf16_cvt") is not None

        try:
            # Run once to get the output and compile any kernels
            output = bench_func(q, k, v, has_round_mode) if backend == "aiter" or backend == "aiter-varlen" else bench_func(q, k, v)
            if backend != "sdpa":
                output_spda = run_sdpa(q_, k_, v_).swapaxes(1, 2)
                mean_abs_diff = (output - output_spda).abs().mean().item()
            else:
                mean_abs_diff = 0.0
            # Run the benchmark
            avg_time_ms = do_bench(
                lambda:  bench_func(q, k, v, has_round_mode) if backend == "aiter" or backend == "aiter-varlen" else bench_func(q, k, v),  # type: ignore
                warmup=WARMUP,
                rep=REPS
            )
        except RuntimeError as e:
            print(f"Failed to benchmark backend={backend} with signature index={idx}: {e}")
            avg_time_ms = pandas.NA
            mean_abs_diff = pandas.NA

        # Calculate metrics
        flops = calculate_flops(signature)
        avg_time = avg_time_ms / 1000.0  # convert to seconds
        throughput = (flops / avg_time) / 1e12

        item = pandas.Series(
            {
                "backend": backend,
                "flops": flops,
                'avg_time_ms': avg_time_ms,
                'throughput': throughput,
                'mean_abs_diff_vs_sdpa': mean_abs_diff
            }
        )
        data.append(
            pandas.concat([signature, item]).to_frame().T
        )
    data = pandas.concat(data).reset_index(drop=True)

    return data


if __name__ == "__main__":

    results = {}
    results["sdpa"] = benchmark_attention("sdpa")

    if has_aiter:
        results["aiter"] = benchmark_attention("aiter")
        results["aiter-varlen"] = benchmark_attention("aiter-varlen")
    else:
        print("Aiter is not available. Skipping Aiter benchmark.")

    print("\nBenchmark Results:")
    print("=" * 50)
    print(results)