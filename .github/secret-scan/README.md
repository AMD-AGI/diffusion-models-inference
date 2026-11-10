# Secret scanning of build outputs

Every image and registry build cache that CI pushes is scanned with
[Trivy](https://trivy.dev) before anything can use it. Secrets block;
vulnerabilities and misconfigurations are reported only.

## How it works

1. A build job pushes its outputs to private candidate tags,
   `<BUILDCACHE_IMAGE>:cand-<run_id>-<run_attempt>-<label>-<tag>`, and writes
   their digests to a targets file. Image names stay in files because GitHub
   drops job outputs containing the Docker Hub organization, a secret.
2. A `Trivy scan and promote` step at the end of the same build job
   ([`.github/actions/secret-scan`](../actions/secret-scan/action.yml))
   downloads every config and layer blob by digest, verifies it, unpacks it
   and runs `trivy fs --scanners secret` with
   [`trivy-secret.yaml`](trivy-secret.yaml). Files deleted by later layers,
   `ENV` values, `.git` contents and `mode=max` cache-only stages are all
   covered. Runnable image targets also run `trivy image` for vulnerabilities,
   secrets and misconfigurations, limited to HIGH and CRITICAL severity and
   OS/library packages; unfixed vulnerabilities are ignored. Cache targets
   receive the all-blob secret scan only.
3. Only if no secret is found are the scanned digests copied to their real
   tags (staging/core images, live cache refs). Downstream jobs depend on the
   build job, so nothing consumes an unscanned artifact.

Every build stage (core, NV, final tuned) applies the same rules, and each
scans its own outputs in full, including layers shared with images an earlier
stage already scanned.

Trivy and crane are baked into the runner image at pinned versions (see the
infrastructure repository); the job fails if the runner provides a different
Trivy version. Nothing is cached on the runner between jobs: every blob is
scanned on every run, and the image checks download the Trivy vulnerability
database into the job's temporary directory.

## When the scan fails

The job summary lists secret findings with their rule, path, layer and a
masked match, and reports HIGH/CRITICAL counts for image vulnerabilities,
secrets and misconfigurations. The results are also uploaded as a 30-day
workflow artifact, `trivy-reports-<scan id>-attempt-<attempt>`: `summary.json`
holds the scanned targets, the secret findings and any scan errors. Each
scanned image platform has a readable Trivy table report (`<id>.txt`) and the
full JSON report (`<id>.json`) for automation; `summary.json` maps each `<id>`
to its image and platform. A secret found
by either scan, or an error in the per-blob secret scan, prevents promotion.
An error in the `trivy image` check is a warning only.

- **Real secret:** do not allowlist it. Revoke and rotate it, remove it from
  the build, and rerun the whole workflow. The candidate tag only lives in the
  private cache repository and is deleted by the cache cleanup workflow.
- **Secret false positive:** add the narrowest possible allow rule to
  [`trivy-secret.yaml`](trivy-secret.yaml) and merge it to the default branch.
  Then use **Re-run failed jobs** on the failed run. This reruns the build
  job, which rebuilds (mostly from the registry cache) to new candidate tags
  and scans them with the new rule, because the scan always reads the policy
  from the default branch.
- **Vulnerability or misconfiguration finding:** reported as a warning and in
  the uploaded Trivy reports; it does not fail the job. Review it and
  remediate what is in our control.

Failed candidate tags are removed by the cache cleanup workflow after its
grace period.

## Writing allow rules

```yaml
allow-rules:
  - id: short-unique-id
    description: Why this is not a secret, and where it comes from.
    regex: \bexact-public-value\b # matched against the rule match
    # or
    path: ^exact/path/to/file$ # relative, no leading slash
```

- Prefer `regex` with the exact known value. A `path` rule allows every
  secret in the matching files.
- `regex` sees the whole rule match, which may include the quote or other
  boundary character around the secret, so use `\b` instead of `^`/`$`.
- `regex` and `path` in one rule are alternatives, not a combination.
- Paths are relative to the layer root, `.git` directories appear as
  `.git-dir`, and image configs and cache metadata appear as `blob.json`.
- Trivy's built-in allow rules are disabled on purpose. Do not re-enable them.

Changes to this directory require approval from the CI code owners.
