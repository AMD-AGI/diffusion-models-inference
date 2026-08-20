# diagnose-run

Checks a GitHub Actions workflow run in the repository associated with the
current checkout. The user selects one of the workflows in `.github/workflows`,
excluding `*-reusable.yml` workflows. The skill reports job status and inspects
failed logs. For `build-and-benchmark.yml` runs, it also inspects benchmark
artifacts when available.
