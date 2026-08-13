"""Entry point of the `bulkbench` tool."""

import sys

from .bulkbench import BulkBench
from .cli_parser import makeParser


def main() -> int:
    parser = makeParser()
    args = parser.parse_args()
    try:
        bulk_bench = BulkBench(args)
    except ValueError as exc:
        parser.error(str(exc))
    return bulk_bench.run()


if __name__ == "__main__":
    sys.exit(main())
