from pathlib import Path
import unittest

from bulkbench import BulkBench


class TestSystem(unittest.TestCase):
    def test_configs_file(self):
        project_dir = Path(__file__).parent / "proj0"
        # specify arch to avoid rocminfo call
        bulk_bench = BulkBench(project_dir=project_dir, configs_file="my_configs", arch="")
        self.assertEqual(bulk_bench.configs, ["cfg1", "cfg2", "cfg3"])


if __name__ == "__main__":
    import sys
    import pytest

    sys.exit(pytest.main(sys.argv))
