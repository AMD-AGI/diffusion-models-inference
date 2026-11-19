# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

import os
import subprocess
import sys
import unittest
from pathlib import Path


class CompatibilityWrapperTest(unittest.TestCase):
    def test_wrapper_imports_package_from_repository_source(self):
        repository = Path(__file__).resolve().parents[2]
        script = (
            "import sys; "
            f"sys.path.insert(0, {str(repository / '.ci')!r}); "
            "import miopen_driver_commands as wrapper; "
            "from miopen_tuning.collection import MIOpenDriverCommandCollector; "
            "assert wrapper.MIOpenDriverCommandCollector is MIOpenDriverCommandCollector"
        )

        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        subprocess.run([sys.executable, "-c", script], cwd=repository, env=environment, check=True)


if __name__ == "__main__":
    unittest.main()
