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
                self.assertIn("COPY .ci/miopen_driver_commands.py /app/.ci/miopen_driver_commands.py", text)
                self.assertIn("COPY src/miopen_tuning /app/.ci/miopen_tuning", text)
        self.assertEqual(len(checked), 8)


if __name__ == "__main__":
    unittest.main()
