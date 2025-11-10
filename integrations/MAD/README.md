# MAD tooling

To make these changes effective in MAD

- copy `pyt_xdit.ubuntu.amd.Dockerfile` and `run.sh` to MAD under `docker` and
`scripts/pyt_xdit` respectively
- concatenate MAD `models.json` with the contents of `models.json` in this
  directory while maintaining json-lines format

and make sure that these changes get integrated into the main branch
