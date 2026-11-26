# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT
"""Tests for resolve_rocm_nightly.py with a fake wheel index.

Run from the repository root:
  python3 -m unittest discover -s .github/actions/resolve-rocm-nightly/scripts
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import resolve_rocm_nightly as resolver  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
INSTALL_SCRIPT = REPO_ROOT / "docker" / "install_rocm_wheels.sh"
INDEX = "https://example.invalid/whl"
PINS = {
    "ROCM_WHEEL_INDEX": INDEX,
    "ROCM_WHEEL_VERSION": "10.3.0a20261108",
    "ROCM_GFX_TARGETS": "gfx942;gfx950",
    "TORCH_VERSION": "2.14.0",
    "TORCHVISION_VERSION": "0.29.0",
    "TORCHAUDIO_VERSION": "2.11.0.3",
}


def sdk_wheels(rocm):
    names = ["rocm_sdk_core", "rocm_sdk_libraries", "rocm_sdk_devel", "rocm_profiler"]
    names += [f"rocm_sdk_device_{gfx}" for gfx in ("gfx942", "gfx950")]
    return [f"{name}-{rocm}-py3-none-linux_x86_64.whl" for name in names]


def framework_wheels(rocm, torch="2.14.0", vision="0.29.0", audio="2.11.0.3"):
    local = f"%2Brocm{rocm}"
    wheels = [
        f"torch-{torch}{local}-cp312-cp312-linux_x86_64.whl",
        f"torchvision-{vision}{local}-cp312-cp312-linux_x86_64.whl",
        f"torchaudio-{audio}{local}-cp312-cp312-linux_x86_64.whl",
        f"triton-3.9.0%2Bgit0a6e13e6.rocm{rocm}-cp312-cp312-linux_x86_64.whl",
    ]
    for gfx in ("gfx942", "gfx950"):
        wheels += [
            f"amd_torch_device_{gfx}-{torch}{local}-py3-none-linux_x86_64.whl",
            f"amd_torchvision_device_{gfx}-{vision}{local}-py3-none-linux_x86_64.whl",
        ]
    return wheels


def project_of(filename):
    return resolver.normalize(filename.split("-", 1)[0])


class FakeIndex:
    def __init__(self, filenames):
        self.pages = {}
        for filename in filenames:
            self.pages.setdefault(project_of(filename), []).append(filename)

    def __call__(self, url):
        project = url.rstrip("/").rsplit("/", 1)[-1]
        links = "".join(f'<a href="{name}#sha256=00">{name}</a><br/>' for name in self.pages.get(project, []))
        return f"<html><body>{links}</body></html>"


def resolve(filenames, pins=PINS):
    index = resolver.Index(INDEX, FakeIndex(filenames), "cp312", "linux_x86_64")
    return resolver.resolve(index, pins, INSTALL_SCRIPT, max_builds=10)


class ResolveTest(unittest.TestCase):
    def test_newest_build_with_all_pins(self):
        files = sdk_wheels("10.3.0a20261125") + framework_wheels("10.3.0a20261125")
        files += sdk_wheels("10.3.0a20261126") + framework_wheels("10.3.0a20261126")
        result = resolve(files)
        self.assertEqual(result.selected, "10.3.0a20261126")
        self.assertEqual(result.newest, "10.3.0a20261126")
        self.assertEqual(result.missing_in_newest, [])
        self.assertEqual(result.annotations(), [])
        wheels = {check.name: check.filename for check in result.selected_checks}
        self.assertEqual(wheels["torch"], "torch-2.14.0+rocm10.3.0a20261126-cp312-cp312-linux_x86_64.whl")
        self.assertIn("rocm-sdk-device-gfx950", wheels)
        self.assertIn("amd-torchvision-device-gfx942", wheels)

    def test_rocm_line_bump_with_dropped_torch_warns_and_falls_back(self):
        files = sdk_wheels("10.3.0a20261126") + framework_wheels("10.3.0a20261126")
        # ROCm 10.4.0 starts without torch 2.14.0, only 2.15.0.
        files += sdk_wheels("10.4.0a20261201") + framework_wheels("10.4.0a20261201", torch="2.15.0")
        result = resolve(files)
        self.assertEqual(result.newest, "10.4.0a20261201")
        self.assertEqual(result.selected, "10.3.0a20261126")
        missing = {check.name for check in result.missing_in_newest}
        self.assertEqual(missing, {"torch", "amd-torch-device-gfx942", "amd-torch-device-gfx950"})
        levels = [level for level, _ in result.annotations()]
        self.assertEqual(levels, ["warning", "notice", "notice"])
        self.assertEqual(result.newer_releases, {"torch": ["2.15.0"]})
        self.assertIn("**missing**", result.summary())

    def test_missing_torchaudio_and_device_wheel_are_detected(self):
        files = sdk_wheels("10.3.0a20261125") + framework_wheels("10.3.0a20261125")
        newest = [
            name for name in sdk_wheels("10.3.0a20261126") + framework_wheels("10.3.0a20261126", audio="2.11.0.4")
            if not name.startswith("rocm_sdk_device_gfx950")
        ]
        result = resolve(files + newest)
        self.assertEqual(result.selected, "10.3.0a20261125")
        missing = {check.name for check in result.missing_in_newest}
        self.assertEqual(missing, {"torchaudio", "rocm-sdk-device-gfx950"})
        self.assertEqual(result.newer_releases, {"torchaudio": ["2.11.0.4"]})

    def test_no_build_with_all_pins_fails(self):
        files = sdk_wheels("10.3.0a20261126") + framework_wheels("10.3.0a20261126", torch="2.15.0")
        with self.assertRaisesRegex(LookupError, "torch==2.14.0"):
            resolve(files)

    def test_other_python_tags_do_not_count(self):
        files = sdk_wheels("10.3.0a20261126")
        files += [name.replace("cp312", "cp313") for name in framework_wheels("10.3.0a20261126")]
        with self.assertRaises(LookupError):
            resolve(files)


class HelpersTest(unittest.TestCase):
    def test_parse_dockerfile_args(self):
        text = "\n".join(f'ARG {key}="{value}"' for key, value in PINS.items())
        text += '\nFROM base\nARG TORCH_VERSION="9.9.9"\n'
        self.assertEqual(resolver.parse_dockerfile_args(text), PINS)
        with self.assertRaisesRegex(ValueError, "TORCH_VERSION"):
            resolver.parse_dockerfile_args(text.replace("ARG TORCH_VERSION", "ARG OLD_TORCH_VERSION"))

    def test_repository_dockerfile_has_all_pins(self):
        pins = resolver.parse_dockerfile_args((REPO_ROOT / "docker" / "Dockerfile.ci").read_text())
        resolver.rocm_version_key(pins["ROCM_WHEEL_VERSION"])

    def test_rocm_version_order(self):
        versions = ["10.3.0a20261126", "10.10.0a20270101", "10.4.0a20261201"]
        self.assertEqual(
            sorted(versions, key=resolver.rocm_version_key),
            ["10.3.0a20261126", "10.4.0a20261201", "10.10.0a20270101"],
        )

    def test_main_writes_outputs_and_provenance(self):
        files = sdk_wheels("10.3.0a20261126") + framework_wheels("10.3.0a20261126")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            dockerfile = tmp / "Dockerfile"
            dockerfile.write_text("\n".join(f'ARG {key}="{value}"' for key, value in PINS.items()))
            output, summary = tmp / "output", tmp / "summary"
            env = {"GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)}
            with (
                mock.patch.dict("os.environ", env),
                mock.patch.object(resolver, "http_fetch", FakeIndex(files)),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = resolver.main([
                    "--dockerfile", str(dockerfile),
                    "--install-script", str(INSTALL_SCRIPT),
                    "--provenance", str(tmp / "provenance.json"),
                ])
            self.assertEqual(code, 0)
            self.assertIn("rocm_wheel_version=10.3.0a20261126\n", output.read_text())
            self.assertIn("pins_available_in_newest=true\n", output.read_text())
            self.assertIn("ROCm nightly wheel resolution", summary.read_text())
            provenance = json.loads((tmp / "provenance.json").read_text())
            self.assertEqual(provenance["dockerfile_pins"], PINS)
            self.assertEqual(len(provenance["selected_wheels"]), 14)


if __name__ == "__main__":
    unittest.main()
