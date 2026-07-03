# vLLM Omni Integration

The `online_runner.py` in this folder benchmarks vLLM Omni in online serving mode — it starts the vllm-omni server, waits for readiness, and sends requests to the appropriate endpoint (`/v1/images/generations`, `/v1/images/edits`, or `/v1/videos/sync`) depending on the benchmark config. It uses the same CLI args as xDiT and writes per-iteration wall times to `timings.json`.

`runner.py` is an alternative that runs vLLM Omni in-process via the Python API instead of through the server.

## Benchmarking

### ROCm

`https://hub.docker.com/r/amdsiloai/pytorch-xdit-omni`

```
docker run \
  --cap-add=SYS_PTRACE \
  --security-opt seccomp=unconfined \
  --user root \
  --device=/dev/kfd \
  --device=/dev/dri \
  --group-add video \
  --ipc=host \
  --network host \
  -it --rm \
  --shm-size 128G \
  --name xdit-omni \
  -e HSA_NO_SCRATCH_RECLAIM=1 \
  -e CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  -e OMP_NUM_THREADS=16 \
  -e HF_HUB_CACHE=/.cache/huggingface/hub \
  amdsiloai/pytorch-xdit-omni:v26.6-pt_8f4963d-vllm_ad7125a-omni_5f9aee1
```

```bash
python .ci/run.py \
  --name wan2_2.default \
  --results-directory /app/results \
  .ci/benchmark_configs/*.yaml
```

## Profiling

Pass `--profile` to call `/start_profile` before and `/stop_profile` after the timed iterations (warmup is excluded). Set `VLLM_TORCH_PROFILER_DIR` to control where traces are saved.

```bash
VLLM_TORCH_PROFILER_DIR=/app/results/profile python .ci/run.py \
  --name wan2_2.default \
  --results-directory /app/results \
  --override-args-json '{"num_inference_steps": 5, "num_iterations": 1, "profile": true}' \
  .ci/benchmark_configs/*.yaml
```
