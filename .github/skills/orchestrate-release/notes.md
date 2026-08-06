# Notes — orchestrate-release

## 1. Single prompt

All questions are asked in a single message. Auto-resolve what you can from the
repository first (repository, default branch, previous tag, and workflow defaults),
then present those values alongside the remaining questions. Do not infer an
untuned image tag from a core workflow SHA because that run may have used a
custom image tag.

## 2. Unattended execution

Phase 2 runs all steps without asking for confirmation. The user already approved
everything in phase 1. Do not pause between creating the release, generating
notes, and triggering the build.

## 3. Partial failure

Release creation and workflow dispatch are separate mutations. If release
creation succeeds but dispatch fails, report the release URL and provide the
validated dispatch command as the recovery action. If release creation fails,
do not dispatch. Before any retry, query the release and tag state again.
