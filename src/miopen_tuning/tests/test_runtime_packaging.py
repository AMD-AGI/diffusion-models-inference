# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class RuntimePackagingTest(unittest.TestCase):
    def test_images_with_run_py_include_miopen_tuning(self):
        dockerfiles = sorted((ROOT / "docker").glob("Dockerfile*"))
        checked = []
        for dockerfile in dockerfiles:
            text = dockerfile.read_text()
            if "COPY .ci/run.py /app/.ci/run.py" not in text:
                continue
            checked.append(dockerfile.name)
            with self.subTest(dockerfile=dockerfile.name):
                if dockerfile.name == "Dockerfile.rdna4":
                    self.assertIn("COPY .ci/miopen_driver_commands.py /app/.ci/miopen_driver_commands.py", text)
                else:
                    self.assertNotIn(".ci/miopen_driver_commands.py", text)
                self.assertIn("COPY src/miopen_tuning /app/.ci/miopen_tuning", text)
        self.assertEqual(len(checked), 8)

    def test_run_py_imports_collector_from_package(self):
        text = (ROOT / ".ci" / "run.py").read_text()
        self.assertIn("from miopen_tuning.collection import MIOpenDriverCommandCollector, env_enabled", text)
        self.assertNotIn("from miopen_driver_commands import", text)


if __name__ == "__main__":
    unittest.main()
