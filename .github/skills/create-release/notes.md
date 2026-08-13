# Notes — create-release

## 1. Draft vs published

Published releases create the git tag immediately, which pins future builds to
the exact tagged commit. Only use `--draft` if the user explicitly requests it —
drafts do not create a tag until published.
