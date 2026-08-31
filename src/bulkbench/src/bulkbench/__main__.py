"""Entry point of the `bulkbench` tool."""

import sys

from .bulkbench import BulkBench
from .cli_parser import makeParser


def main() -> int:
    parser = makeParser()
    args = parser.parse_args()
    try:
        return BulkBench(args).run()
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
