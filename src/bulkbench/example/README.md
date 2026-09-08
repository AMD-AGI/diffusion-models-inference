# Example `bulkbench` project

This directory provides an example of how a `bulkbench` project could look like and is based on a
real-world investigation on how a certain PyTorch pull request (PR) behaves under different set of
PyTorch flags compared to raw PyTorch, on `gfx942` and `gfx950` GPUs.

The project has [`configs.yaml`](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/configs.yaml)
defining 3 model configurations in 2 groups (each group could have own `override_args`):

- `flux.usp`
- `flux2.quantgemm`
- `wan2_2.quantgemm_fp8attn`

The last 2 configs are specified in their full form mentioning platform with `.gfx942` or
`.gfx950` suffix. Only one of each is chosen in runtime depending on autodetection result (or
`--arch` CLI argument value, if autodetection fails which is more applicable to other platforms).

Raw PyTorch instance and the instance with the applied PR expected to live in separate
docker containers with the project directory mounted into each of them. Depending on a
container (base code instance), one could select which patch sets to apply with `--patches_file` argument.

Two patch files are defined: [`patches_PR.yaml`](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/patches_PR.yaml)
with 5 patch variants for the PR instance, and [`patches_py213.yaml`](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/patches_py213.yaml)
with 6 patch variants for the raw PyTorch instance.

[`report`](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/report) directory shows two reports
made from obtained results:

- [`benchstats-fix-groups.html`](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/report/benchstats-fix-groups.html) was
generated automatically by `bulkbench` and shows relative performance of models for `gfx950`,
- [`run-to-run.html`](https://github.com/AMD-AGI/diffusion-models-inference/tree/main/bulkbench/example/report/run-to-run.html) was generated manually
on results from 2 runs of the same project on the machine to estimate machine's noise level with command:

```
benchstats . --files_parser=bulkbench.parser_JSON --sample_stats 0 100 --always_show_pvalues \
    --filter1=1,2 --export_to=./run-to-run.html
```
