# vLLM Omni Integration

The `runner.py` in this folder is a wrapper around vLLM Omni using the same CLI args that xDiT uses. It acts as a translation layer between the two frameworks and handles compiling/benchmarking the models.

## Benchmarking

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
  --privileged \
  --shm-size 128G \
  --name xdit-omni \
  -e HSA_NO_SCRATCH_RECLAIM=1 \
  -e CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
  -e OMP_NUM_THREADS=16 \
  -e HF_HOME=/.cache/huggingface \
  amdsiloai/pytorch-xdit-omni:v26.6-pt_8f4963d-vllm_ad7125a-omni_5f9aee1
```

```bash
python .ci/run.py \
  --name wan2_2.default \
  --results-directory /app/results/omni \
  .ci/benchmark_configs/omni/*.yaml
```

## Profiling

Pass `--override-args-json` with `"profile": true` and set `VLLM_TORCH_PROFILER_DIR` to where the traces should be saved. 

```bash
VLLM_TORCH_PROFILER_DIR=/app/results/omni/wan2_2.default/profile python .ci/run.py \
  --name wan2_2.default \
  --results-directory /app/results/omni \
  --override-args-json '{"num_inference_steps": 5, "num_iterations": 1, "profile": true}' \
  .ci/benchmark_configs/omni/*.yaml
```
