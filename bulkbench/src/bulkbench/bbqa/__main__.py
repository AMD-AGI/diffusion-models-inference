"""Entry point for the ``bbqa`` command."""

import sys

from .cli_parser import create_parser
from .bbqa import BBQA


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()
    try:
        return BBQA(args).run()
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
