#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Scan registry artifacts by digest, then promote the ones that pass.

Every blob reachable from each target (image configs, image layers, cache
configs, cache layers, attestations) is scanned on its own with
``trivy fs --scanners secret``. Scanning blob by blob instead of through
``trivy image`` matters: a secret deleted in a later layer still ships in the
earlier blob, a BuildKit cache manifest is not an image, and ``trivy image``
skips secrets in layers it attributes to a base image.

Runnable image targets also receive ``trivy image`` checks for vulnerabilities,
secrets, and misconfigurations. These checks are limited to HIGH and CRITICAL,
and vulnerabilities without a fix are ignored. Secrets block; vulnerabilities and
misconfigurations are reported but do not block, since they mostly come from
third-party packages. An image scan that cannot complete is likewise only a
warning, because the all-blob secret scan already covers every layer. BuildKit
cache targets are not runnable images and receive only the all-blob secret scan.

Targets are a JSON file holding a list of objects with ``name``, ``source`` (a
``repo@sha256:...`` reference), ``image_scan`` (whether to run the image
scanners), and ``promote`` (destination tags that receive the scanned digest
once every target has passed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
TAR_LAYER_PREFIXES = (
    "application/vnd.oci.image.layer.",
    "application/vnd.docker.image.rootfs.",
)
# Trivy unconditionally skips **/.git, but a clone with a token in its URL
# leaves the token in .git/config, so extracted .git directories are renamed.
# Allow rules see the renamed path.
GIT_DIR_RENAME = ".git-dir"
MATCH_PREVIEW_CHARS = 200
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
BLOCKING_SEVERITIES = {"HIGH", "CRITICAL"}
IMAGE_SCANNERS = {
    "Vulnerabilities": "vulnerabilities",
    "Secrets": "secrets",
    "Misconfigurations": "misconfigurations",
}


class ScanError(Exception):
    pass


@dataclass
class Blob:
    repo: str
    digest: str
    media_type: str
    targets: set[str] = field(default_factory=set)

    @property
    def is_tar_layer(self) -> bool:
        return self.media_type.startswith(TAR_LAYER_PREFIXES) and not self.media_type.endswith("+json")


@dataclass
class Finding:
    blob: Blob
    path: str
    rule_id: str
    title: str
    line: int
    match: str


def log(message: str) -> None:
    print(message, flush=True)


def annotation(level: str, message: str, title: str = "") -> None:
    message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = title.replace("%", "%25").replace(",", "%2C").replace(":", "%3A")
    print(f"::{level} title={title}::{message}" if title else f"::{level}::{message}", flush=True)


def clean_env() -> dict[str, str]:
    # TRIVY_* variables would silently change what gets scanned.
    return {key: value for key, value in os.environ.items() if not key.startswith("TRIVY_")}


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, env=clean_env(), **kwargs)


def split_reference(ref: str) -> tuple[str, str]:
    repo, sep, digest = ref.partition("@")
    if not sep or not repo or not DIGEST_RE.match(digest):
        raise ScanError(f"Not a pinned repo@sha256 reference: {ref!r}")
    return repo, digest


def fetch_manifest(repo: str, digest: str) -> dict:
    result = run(["crane", "manifest", f"{repo}@{digest}"], capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise ScanError(f"crane manifest {repo}@{digest} failed: {stderr}")
    actual = "sha256:" + hashlib.sha256(result.stdout).hexdigest()
    if actual != digest:
        raise ScanError(f"Manifest digest mismatch for {repo}: expected {digest}, got {actual}")
    return json.loads(result.stdout)


def image_manifests(repo: str, root_digest: str) -> list[tuple[str, str]]:
    """Return runnable image manifest digests and their platform labels."""
    pending = [(root_digest, "")]
    seen: set[str] = set()
    images: list[tuple[str, str]] = []
    while pending:
        digest, platform = pending.pop()
        if digest in seen:
            continue
        seen.add(digest)
        manifest = fetch_manifest(repo, digest)
        media_type = manifest.get("mediaType", "")
        if media_type in INDEX_TYPES or (not media_type and "manifests" in manifest):
            for child in manifest.get("manifests", []):
                annotations = child.get("annotations") or {}
                if annotations.get("vnd.docker.reference.type") == "attestation-manifest":
                    continue
                child_digest = child.get("digest", "")
                if not DIGEST_RE.match(child_digest):
                    raise ScanError(f"Unsupported child digest in {repo}@{digest}")
                child_platform = child.get("platform") or {}
                os_name = child_platform.get("os", "")
                architecture = child_platform.get("architecture", "")
                if os_name and architecture and os_name != "unknown" and architecture != "unknown":
                    child_label = f"{os_name}/{architecture}"
                    variant = child_platform.get("variant")
                    if variant:
                        child_label += f"/{variant}"
                else:
                    child_label = ""
                pending.append((child_digest, child_label))
            continue
        if media_type and media_type not in MANIFEST_TYPES:
            raise ScanError(f"Unsupported image manifest type {media_type!r} for {repo}@{digest}")
        images.append((digest, platform or "unspecified"))

    if not images:
        raise ScanError(f"No runnable image manifests found for {repo}@{root_digest}")
    return images


def collect_blobs(name: str, source: str, blobs: dict[str, Blob]) -> None:
    repo, root_digest = split_reference(source)
    pending = [root_digest]
    seen: set[str] = set()
    while pending:
        digest = pending.pop()
        if digest in seen:
            continue
        seen.add(digest)
        manifest = fetch_manifest(repo, digest)
        media_type = manifest.get("mediaType", "")
        if media_type in INDEX_TYPES or (not media_type and "manifests" in manifest):
            for child in manifest.get("manifests", []):
                if child.get("mediaType", "") not in INDEX_TYPES | MANIFEST_TYPES:
                    raise ScanError(f"Unsupported child manifest type {child.get('mediaType')!r} in {repo}@{digest}")
                if not DIGEST_RE.match(child.get("digest", "")):
                    raise ScanError(f"Unsupported child digest in {repo}@{digest}")
                pending.append(child["digest"])
            continue
        if media_type and media_type not in MANIFEST_TYPES:
            raise ScanError(f"Unsupported manifest type {media_type!r} for {repo}@{digest}")
        for descriptor in [manifest["config"], *manifest.get("layers", [])]:
            blob_digest = descriptor.get("digest", "")
            if not DIGEST_RE.match(blob_digest):
                raise ScanError(f"Unsupported blob digest {blob_digest!r} in {repo}@{digest}")
            blob = blobs.setdefault(
                blob_digest,
                Blob(repo=repo, digest=blob_digest, media_type=descriptor.get("mediaType", "")),
            )
            blob.targets.add(name)


def download_blob(blob: Blob, dest: Path) -> None:
    hasher = hashlib.sha256()
    with (
        dest.open("wb") as out,
        tempfile.TemporaryFile() as stderr,
        subprocess.Popen(
            ["crane", "blob", f"{blob.repo}@{blob.digest}"],
            stdout=subprocess.PIPE,
            stderr=stderr,
            env=clean_env(),
        ) as proc,
    ):
        assert proc.stdout is not None
        while chunk := proc.stdout.read(1 << 20):
            hasher.update(chunk)
            out.write(chunk)
        if proc.wait() != 0:
            stderr.seek(0)
            raise ScanError(
                f"crane blob {blob.repo}@{blob.digest} failed: {stderr.read().decode(errors='replace').strip()}"
            )
    actual = "sha256:" + hasher.hexdigest()
    if actual != blob.digest:
        raise ScanError(f"Blob digest mismatch: expected {blob.digest}, got {actual}")


def safe_relative_path(name: str) -> PurePosixPath | None:
    parts = []
    for part in PurePosixPath(name.lstrip("/")).parts:
        if part in ("", "."):
            continue
        if part == "..":
            return None
        parts.append(GIT_DIR_RENAME if part == ".git" else part)
    return PurePosixPath(*parts) if parts else None


def extract_layer(layer: Path, root: Path) -> int:
    """Extract only regular files and directories, with sanitized paths.

    Links and device nodes carry no content of their own (a hardlink target is
    extracted as a file in its own right), and unprivileged extraction of them
    would either fail or escape the root.
    """
    with layer.open("rb") as handle:
        magic = handle.read(4)
    proc = None
    if magic == ZSTD_MAGIC:
        proc = subprocess.Popen(["zstd", "-dcq", "--", str(layer)], stdout=subprocess.PIPE)
        archive = tarfile.open(fileobj=proc.stdout, mode="r|")
    else:
        archive = tarfile.open(str(layer), mode="r|*")

    files = 0
    try:
        for member in archive:
            rel = safe_relative_path(member.name)
            if rel is None:
                continue
            target = root / rel
            if member.isdir():
                if not target.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                continue
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as out:
                shutil.copyfileobj(source, out, 1 << 20)
            files += 1
    except (tarfile.TarError, EOFError, OSError, ValueError) as exc:
        raise ScanError(f"Could not unpack layer: {exc}") from exc
    finally:
        archive.close()
        if proc is not None:
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.wait() != 0:
                raise ScanError("zstd could not decompress the layer")
    return files


def run_trivy(scan_root: Path, policy: Path, work: Path) -> dict:
    report = work / "report.json"
    result = run(
        [
            "trivy",
            "fs",
            "--scanners",
            "secret",
            # Package analyzers still run in a secret-only scan and fetch
            # parent POMs from Maven Central, whose rate limit (429) is fatal.
            "--offline-scan",
            "--secret-config",
            str(policy),
            "--cache-dir",
            str(work / "trivy-cache"),
            "--format",
            "json",
            "--output",
            str(report),
            "--exit-code",
            "0",
            "--timeout",
            "60m",
            "--quiet",
            "--skip-version-check",
            "--disable-telemetry",
            str(scan_root),
        ],
        capture_output=True,
        # A trivy.yaml in the working directory would otherwise be loaded.
        cwd=work,
    )
    if result.returncode != 0:
        raise ScanError(f"trivy failed (exit {result.returncode}): {result.stderr.decode(errors='replace').strip()}")
    try:
        return json.loads(report.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ScanError(f"Could not read the Trivy report: {exc}") from exc
    finally:
        report.unlink(missing_ok=True)


def scan_blob(blob: Blob, policy: Path, work: Path) -> list[Finding]:
    download_dir = work / "download"
    scan_root = work / "root"
    for path in (download_dir, scan_root):
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True)
    try:
        downloaded = download_dir / "blob"
        download_blob(blob, downloaded)
        if blob.is_tar_layer:
            log(f"    unpacked {extract_layer(downloaded, scan_root)} file(s)")
            downloaded.unlink()
        else:
            # Configs, cache configs and attestations are JSON. An image config
            # carries ENV, labels and RUN history, including build arguments.
            downloaded.rename(scan_root / "blob.json")
        report = run_trivy(scan_root, policy, work)
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
        shutil.rmtree(scan_root, ignore_errors=True)

    return [
        Finding(
            blob=blob,
            path=result.get("Target", ""),
            rule_id=secret.get("RuleID", ""),
            title=secret.get("Title", ""),
            line=int(secret.get("StartLine") or 0),
            # Trivy masks the matched secret, leaving only the line around it.
            match=(secret.get("Match") or "").strip()[:MATCH_PREVIEW_CHARS],
        )
        for result in report.get("Results") or []
        for secret in result.get("Secrets") or []
    ]


def image_report_counts(report: dict) -> dict[str, int]:
    if not isinstance(report, dict) or not isinstance(report.get("Results", []), list):
        raise ScanError("Trivy image report has an invalid JSON structure")
    counts = {name: 0 for name in IMAGE_SCANNERS.values()}
    for result in report.get("Results") or []:
        if not isinstance(result, dict):
            raise ScanError("Trivy image report contains an invalid result")
        for field_name, count_name in IMAGE_SCANNERS.items():
            for finding in result.get(field_name) or []:
                if not isinstance(finding, dict):
                    raise ScanError("Trivy image report contains an invalid finding")
                if finding.get("Severity") in BLOCKING_SEVERITIES:
                    counts[count_name] += 1
    return counts


def scan_image_manifest(
    name: str,
    reference: str,
    platform: str,
    policy: Path,
    work: Path,
    trivy_cache: Path,
    report_dir: Path,
) -> dict:
    report_path = report_dir / f"{hashlib.sha256((name + reference).encode()).hexdigest()[:16]}.json"
    report_path.unlink(missing_ok=True)
    result = run(
        [
            "trivy",
            "image",
            "--scanners",
            "vuln,secret,misconfig",
            "--pkg-types",
            "os,library",
            "--severity",
            "HIGH,CRITICAL",
            "--ignore-unfixed",
            "--secret-config",
            str(policy),
            "--cache-dir",
            str(trivy_cache),
            "--format",
            "json",
            "--output",
            str(report_path),
            "--exit-code",
            "0",
            "--timeout",
            "90m",
            "--quiet",
            "--skip-version-check",
            "--disable-telemetry",
            reference,
        ],
        capture_output=True,
        cwd=work,
    )
    if result.returncode != 0:
        raise ScanError(
            f"trivy image failed for {reference} ({platform}, exit {result.returncode}): "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    try:
        report = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ScanError(f"Could not read the Trivy image report for {reference}: {exc}") from exc
    counts = image_report_counts(report)
    table_path = report_path.with_suffix(".txt")
    table_path.unlink(missing_ok=True)
    converted = run(
        [
            "trivy",
            "convert",
            "--format",
            "table",
            "--scanners",
            "vuln,secret,misconfig",
            "--output",
            str(table_path),
            "--quiet",
            str(report_path),
        ],
        capture_output=True,
        cwd=work,
    )
    if converted.returncode != 0:
        annotation(
            "warning",
            f"Could not convert the Trivy report for {name} ({platform}) to a table: "
            f"{converted.stderr.decode(errors='replace').strip()}",
            "Trivy report conversion",
        )
    return {
        "target": name,
        "reference": reference,
        "platform": platform,
        "counts": counts,
        "report": report_path.name,
        "table_report": table_path.name if converted.returncode == 0 else None,
    }


def trivy_version() -> str:
    result = run(["trivy", "--version", "--format", "json"], capture_output=True, text=True)
    if result.returncode != 0:
        raise ScanError("Could not determine the Trivy version")
    return json.loads(result.stdout).get("Version", "")


def write_summary(lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)


def md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("`", "'").replace("\n", " ")


def load_targets(path: str) -> list[dict]:
    try:
        targets = json.loads(Path(path).read_text())
    except OSError as exc:
        raise ScanError(f"Could not read targets file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ScanError(f"Targets in {path} are not valid JSON: {exc}") from exc
    if not isinstance(targets, list):
        raise ScanError("Targets must be a JSON list")
    for target in targets:
        if not isinstance(target, dict) or not target.get("name") or not target.get("source"):
            raise ScanError(f"Invalid target: {target!r}")
        split_reference(target["source"])
        if "image_scan" in target and not isinstance(target["image_scan"], bool):
            raise ScanError(f"image_scan for {target['name']} must be a boolean")
        target.setdefault("image_scan", False)
        promote = target.setdefault("promote", [])
        if not isinstance(promote, list) or not all(isinstance(tag, str) and tag for tag in promote):
            raise ScanError(f"Invalid promote list for {target['name']}")
    return targets


def command_scan(args: argparse.Namespace) -> int:
    targets = load_targets(args.targets_file)
    policy = Path(args.policy).resolve()
    if not policy.is_file():
        raise ScanError(f"Policy file not found: {policy}")
    if not targets:
        write_summary(["### Trivy scan", "", "No targets to scan."])
        return 0

    version = trivy_version()
    if args.expected_trivy_version and version != args.expected_trivy_version:
        raise ScanError(
            f"Runner has Trivy {version or 'unknown'}, expected {args.expected_trivy_version}. "
            "Update the runner image or the expected version."
        )
    report_dir = Path(args.report_dir).resolve() if args.report_dir else Path(args.work_dir or tempfile.gettempdir())
    report_dir.mkdir(parents=True, exist_ok=True)

    blobs: dict[str, Blob] = {}
    for target in targets:
        log(f"Resolving {target['name']}: {target['source']}")
        collect_blobs(target["name"], target["source"], blobs)

    findings: list[Finding] = []
    errors: list[str] = []
    image_results: list[dict] = []
    image_errors: list[str] = []
    # Nothing persists on the runner between jobs: every blob is scanned and
    # the Trivy database is downloaded into this per-run directory.
    work = Path(tempfile.mkdtemp(prefix="secret-scan-", dir=args.work_dir or None))
    trivy_cache = work / "trivy-image-cache"
    trivy_cache.mkdir()
    try:
        for index, blob in enumerate(blobs.values(), start=1):
            log(f"[{index}/{len(blobs)}] {blob.digest} ({blob.media_type or 'unknown type'})")
            try:
                blob_findings = scan_blob(blob, policy, work)
            except ScanError as exc:
                errors.append(f"{blob.digest}: {exc}")
                annotation("error", f"{blob.digest}: {exc}", "Secret scan error")
                continue
            findings.extend(blob_findings)
        for target in targets:
            if not target["image_scan"]:
                continue
            repo, root_digest = split_reference(target["source"])
            try:
                manifests = image_manifests(repo, root_digest)
            except ScanError as exc:
                image_errors.append(f"{target['name']}: {exc}")
                annotation("warning", f"{target['name']}: {exc}", "Trivy image scan error")
                continue
            for digest, platform in manifests:
                reference = f"{repo}@{digest}"
                log(f"Scanning image {target['name']} ({platform}): {reference}")
                try:
                    image_result = scan_image_manifest(
                        target["name"], reference, platform, policy, work, trivy_cache, report_dir
                    )
                except ScanError as exc:
                    image_errors.append(f"{target['name']} ({platform}): {exc}")
                    annotation("warning", f"{target['name']} ({platform}): {exc}", "Trivy image scan error")
                    continue
                image_results.append(image_result)
                counts = image_result["counts"]
                if counts["secrets"]:
                    annotation(
                        "error",
                        f"{target['name']} ({platform}): {counts['secrets']} HIGH/CRITICAL secrets",
                        "Trivy image secrets",
                    )
                if counts["vulnerabilities"] or counts["misconfigurations"]:
                    annotation(
                        "warning",
                        f"{target['name']} ({platform}): {counts['vulnerabilities']} vulnerabilities, "
                        f"{counts['misconfigurations']} misconfigurations (report only)",
                        "HIGH/CRITICAL Trivy findings",
                    )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    for f in findings:
        annotation(
            "error",
            f"{f.rule_id} at {f.path}:{f.line} in blob {f.blob.digest} "
            f"({', '.join(sorted(f.blob.targets))}): {f.match}",
            f"Secret found: {f.title}",
        )

    image_secrets = sum(result["counts"]["secrets"] for result in image_results)
    reported = sum(
        result["counts"]["vulnerabilities"] + result["counts"]["misconfigurations"] for result in image_results
    )
    report_summary = {
        "trivy_version": version,
        "policy": args.policy_source or str(policy),
        "targets": [{"name": t["name"], "source": t["source"], "image_scan": t["image_scan"]} for t in targets],
        "secret_scanned_blobs": len(blobs),
        "secret_findings": [
            {
                "rule_id": f.rule_id,
                "title": f.title,
                "path": f.path,
                "line": f.line,
                "match": f.match,
                "blob": f.blob.digest,
                "media_type": f.blob.media_type,
                "targets": sorted(f.blob.targets),
            }
            for f in findings
        ],
        "image_scans": image_results,
        "scan_errors": errors,
        "image_scan_errors": image_errors,
    }
    (report_dir / "summary.json").write_text(json.dumps(report_summary, indent=2) + "\n")

    summary = [
        "### Trivy scan",
        "",
        f"- Trivy: `{version}`",
        f"- Policy: `{md_cell(args.policy_source or str(policy))}`",
        f"- Secret-scanned blobs: {len(blobs)}",
        "",
        "| Target | Source |",
        "|---|---|",
        *[f"| {md_cell(t['name'])} | `{md_cell(t['source'])}` |" for t in targets],
    ]
    image_targets = [target for target in targets if target["image_scan"]]
    if image_targets:
        summary += [
            "",
            "Image scans use `vuln,secret,misconfig`, OS and library packages, HIGH/CRITICAL severity, "
            "and `--ignore-unfixed`. Secrets block; vulnerabilities and misconfigurations are report only.",
            "",
            "| Target | Platform | HIGH/CRITICAL vulnerabilities | HIGH/CRITICAL secrets | "
            "HIGH/CRITICAL misconfigurations |",
            "|---|---|---:|---:|---:|",
        ]
        summary += [
            f"| {md_cell(result['target'])} | {md_cell(result['platform'])} | "
            f"{result['counts']['vulnerabilities']} | {result['counts']['secrets']} | "
            f"{result['counts']['misconfigurations']} |"
            for result in image_results
        ]
        if not image_results:
            summary.append("| No image scan completed | - | - | - | - |")
    if findings:
        summary += [
            "",
            f"**{len(findings)} finding(s).** Remove the secret from the build. For a false positive,",
            "add a narrow allow rule to the policy on the default branch, then use **Re-run failed jobs**.",
            "Matched values are masked.",
            "",
            "| Rule | Path | Line | Blob | Targets | Masked match |",
            "|---|---|---|---|---|---|",
            *[
                f"| {md_cell(f.rule_id)} | `{md_cell(f.path)}` | {f.line} | `{f.blob.digest[:19]}` | "
                f"{md_cell(', '.join(sorted(f.blob.targets)))} | `{md_cell(f.match)}` |"
                for f in findings
            ],
        ]
    if errors:
        summary += ["", f"**{len(errors)} scan error(s).** The gate fails closed.", ""]
        summary += [f"- {md_cell(error)}" for error in errors]
    if image_errors:
        summary += ["", f"**{len(image_errors)} image scan error(s)**, not blocking:", ""]
        summary += [f"- {md_cell(error)}" for error in image_errors]
    if image_secrets:
        summary += ["", f"**{image_secrets} HIGH/CRITICAL secret(s) in image scans.** See the Trivy report artifact."]
    if reported:
        summary += [
            "",
            f"{reported} HIGH/CRITICAL vulnerabilities/misconfigurations reported, not blocking. "
            "See the Trivy report artifact.",
        ]
    if not findings and not errors and not image_secrets:
        summary += ["", "No blocking findings."]
    write_summary(summary)

    if findings or errors or image_secrets:
        log(
            f"Trivy scan failed: {len(findings)} secret finding(s), "
            f"{image_secrets} image secret finding(s), {len(errors)} error(s)"
        )
        return 1
    log("Trivy scan passed")
    return 0


def command_promote(args: argparse.Namespace) -> int:
    targets = load_targets(args.targets_file)
    lines = ["### Promotion", ""]
    for target in targets:
        _, digest = split_reference(target["source"])
        for destination in target["promote"]:
            log(f"Promoting {target['source']} -> {destination}")
            if run(["crane", "copy", target["source"], destination]).returncode != 0:
                raise ScanError(f"crane copy to {destination} failed")
            check = run(["crane", "digest", destination], capture_output=True, text=True)
            if check.returncode != 0 or check.stdout.strip() != digest:
                raise ScanError(f"{destination} does not resolve to the scanned digest {digest}")
            lines.append(f"- `{md_cell(destination)}` ← `{digest}`")
    if len(lines) == 2:
        lines.append("Nothing to promote.")
    write_summary(lines)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan target blobs and runnable image manifests")
    scan.add_argument("--targets-file", required=True, help="JSON target list file")
    scan.add_argument("--policy", required=True, help="Trivy secret config file")
    scan.add_argument("--policy-source", default="", help="Where the policy came from, for the summary")
    scan.add_argument("--expected-trivy-version", default="")
    scan.add_argument("--report-dir", default="", help="Directory for Trivy reports and scan summary")
    scan.add_argument("--work-dir", default="")
    scan.set_defaults(func=command_scan)

    promote = sub.add_parser("promote", help="Copy scanned digests to their destination tags")
    promote.add_argument("--targets-file", required=True, help="JSON target list file")
    promote.set_defaults(func=command_promote)

    args = parser.parse_args()
    try:
        return args.func(args)
    except ScanError as exc:
        annotation("error", str(exc), "Secret scan")
        return 1


if __name__ == "__main__":
    sys.exit(main())
