# RDNA4 image: build, tune, finalise

Three scripts produce a gfx1201 image that includes host-collected TunableOp
and MIOpen data. Run them from the repository root. Use the same `STATE_DIR`
for tune and finalise.

```bash
export STATE_DIR="${HOME}/rdna4-finalise"
export HF_CACHE="${HOME}/huggingface"
CANDIDATE=diffusion-models-inference:rdna4-candidate
FINAL=amdsiloai/pytorch-xdit-custom:rdna4-rocm10.0.0-ubuntu24.04-py3.12-torch2.13.0-xdit-b714b35-YYYYMMDD
```

Tune needs four gfx1201 GPUs on one NUMA node, Docker access to `/dev/kfd` and
the render nodes, and a Hugging Face cache (plus `HF_TOKEN` if the models are
gated). GPU device nodes default to
`/dev/dri/renderD130 /dev/dri/renderD132 /dev/dri/renderD131 /dev/dri/renderD133`
with `HIP_DEVICE_ORDER=0,2,1,3`. Override `GPU_DEVICES` / `HIP_DEVICE_ORDER` if
this machine differs. Full tune knobs: `scripts/rdna4/tune.sh --help`.

## 1. Build the candidate

```bash
scripts/rdna4/build.sh "${CANDIDATE}"
```

Builds `docker/Dockerfile.rdna4` (ROCm, PyTorch, aiter, xDiT pins). No GPUs
required.

## 2. Tune on local GPUs

```bash
scripts/rdna4/tune.sh "${CANDIDATE}"
```

Runs every `rdna4`-tagged workload under `benchmark_configs/xdit/` (or
`CONFIG` / `WORKLOAD_NAMES` for a subset). Resume with `--skip-completed`.

Writes into `${STATE_DIR}`:

| Path | What it is |
|---|---|
| `tunableop/` | Per-rank PyTorch TunableOp CSVs (bf16/fp32 GEMM) |
| `miopen-db/` | MIOpen user find-db (convolutions, mainly VAE) |
| `outputs/` | Per-workload logs, timings, sample images |
| `aiter-tune/` | Present for the mount; currently unused on gfx1201 |

FP8 GEMM tuning is not collected here. gfx1201 `gemm_a8w8_blockscale` runs
through aiter Triton, not the CK CSV this repo can install into an image.

## 3. Create the final image

```bash
scripts/rdna4/finalise.sh "${CANDIDATE}" "${FINAL}"
```

Merges the TunableOp CSVs, copies MIOpen if present, and builds
`scripts/rdna4/Dockerfile.rdna4.finalise` on top of the candidate. The final image
runs with `PYTORCH_TUNABLEOP_TUNING=0` and `AITER_ONLINE_TUNE=0`. An
entrypoint copies the MIOpen user DB off overlayfs at start so MIOpen can
open it read-write.

`finalise.sh` refuses to tag an image if `STATE_DIR` has no TunableOp, MIOpen,
or aiter artifacts.
