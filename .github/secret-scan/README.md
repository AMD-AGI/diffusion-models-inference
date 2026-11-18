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
- **Secret false positive:** these should only come from third-party
  content, such as test keys or documentation examples in an upstream
  package. Our own code and build files should never need an exception; fix
  them instead. For an upstream false positive, add an allow rule as described
  in [Managing exceptions](#managing-exceptions) and push it to the branch
  that ran the build. Then use **Re-run failed jobs**. The build job uses the
  latest policy from that branch, rebuilds (mostly from the registry cache) to
  new candidate tags, and scans them with the updated rule. Since the branch
  policy also gates promotion, review policy changes carefully before running
  a build that publishes images.
- **Vulnerability or misconfiguration finding:** reported as a warning and in
  the uploaded Trivy reports; it does not fail the job. Review it and
  remediate what is in our control.

Failed candidate tags are removed by the cache cleanup workflow after its
grace period.

## Managing exceptions

An allow rule hides findings from every future build, on every branch that
contains it, including builds that publish images. Treat each one as a
security decision, not a way to get a build through.

- **Review every finding before writing a rule.** Look up the reported path
  and layer in the job summary or `summary.json`, find the upstream source
  of the file, and confirm what the matched value is. Only allowlist values
  that are demonstrably not usable credentials: test fixtures, test keys,
  documentation examples and deliberately public values. If you cannot
  confirm that, treat the finding as a real secret.
- **Never allowlist a genuine secret, not even temporarily or for testing.**
  Branch builds are promoted to public tags with the branch policy, so a
  "temporary" exception publishes the secret. If a real secret ends up in the
  build, follow the real-secret procedure above instead.
- **Add new findings as new rules; leave existing rules alone.** Each
  existing rule was reviewed for the specific files or values it covers.
  Widening one to also cover a new finding silently changes what it hides and
  skips that review. Change or remove an existing rule only when the content
  it covers changes or disappears.
- **Scope each rule as precisely as possible.** Prefer an exact `regex` for
  the known value. If a `path` rule is needed, anchor it with `^...$`,
  escape dots, and list the exact files rather than a directory. Generalise
  only what is known to vary between builds, such as the Python minor
  version.
- **Explain the rule.** The `description` names the upstream project and
  file, and says why the value is not a secret, so a reviewer can check the
  claim.
- **Add the rule in the branch where the finding appeared.** Usually that is
  the development branch whose change (for example, a dependency bump or a
  new upstream checkout) introduced the file. The rule is then reviewed in
  the same pull request as its cause, and the branch build can be rerun with
  it. Do not add exceptions for one branch's findings in another branch or
  directly on `main`.
- **Reviewers check each new rule** against the reported finding: that the
  file really is upstream test or documentation content, and that the rule
  matches nothing beyond it.

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
