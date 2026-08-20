---
name: Release issue
about: Track a pytorch-xdit release from planning through validation.
title: "[Release] vYY.M.P"
labels: release
---

<!--
Maintainers: replace every placeholder below. Release versions follow vYY.M.P (for example,
v26.5.1). The orchestrate-release skill creates the GitHub release and separately dispatches the
Build and Benchmark xDiT workflow; creating a release branch does not trigger that workflow.
-->

Release `vYY.M.P` of the pytorch-xdit image.

#### Schedule

- Target date: YYYY-MM-DD
- Previous release: vYY.M.P
- Target branch or commit: `main`

#### Scope

<!-- Link the issues or pull requests planned for this release. Keep unchecked items visible. -->

#### Features

- [ ] Feature (#PR)

#### Fixes

- [ ] Fix (#PR)

#### Release checklist

- [ ] Release notes generated and reviewed
- [ ] GitHub release `vYY.M.P` created
- [ ] Build and Benchmark xDiT workflow dispatched for the release ref
- [ ] Workflow run linked: <!-- URL -->
- [ ] Staging image tag or digest recorded: <!-- image reference -->
- [ ] Functional and output-quality validation completed
- [ ] Performance compared with the previous release
- [ ] Supported GPU architectures validated
- [ ] Known issues documented
- [ ] Release image published
- [ ] Published image smoke-tested

#### Validation results

<!-- Record hardware, workflow artifacts, benchmark comparisons, and any accepted regressions. -->

| GPU architecture | Functional and quality result | Performance result | Evidence |
| --- | --- | --- | --- |
| gfx942 | Pending | Pending | <!-- Link --> |
| gfx950 | Pending | Pending | <!-- Link --> |

#### Known issues

<!-- Write "None" when there are no known issues. -->
