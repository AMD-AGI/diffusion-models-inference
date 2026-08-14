# orchestrate-release

Maintainer-only orchestration for the repository associated with the current
checkout. Generates ROCm image release notes, creates a GitHub release, dispatches
`build-and-benchmark.yml`, and reports the exact run without waiting for completion.

Delegates to `create-release`, `create-release-notes`, and
`trigger-build-and-benchmark`.
