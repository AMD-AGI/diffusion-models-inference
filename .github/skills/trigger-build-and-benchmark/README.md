# trigger-build-and-benchmark

Maintainer-only skill that dispatches the current `build-and-benchmark.yml`
workflow in the repository associated with the checkout. A single `base_image`
field plus independent step checkboxes (rebuild, tuning, benchmarks, final
image build, MIOpen DB branch) cover source builds, prebuilt image reuse, and
benchmark-only runs.
