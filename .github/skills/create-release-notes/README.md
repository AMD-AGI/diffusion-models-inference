# create-release-notes

Generates categorized release notes by analyzing merged PRs between two release
tags in the repository associated with the current checkout. Notes are scoped to
the ROCm image built from `docker/Dockerfile.ci`. Public PR metadata is used when
available; migration-era changes without a public PR are represented by commits.
