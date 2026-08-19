"""Entry point for the ``bbqa`` command."""

import argparse
import sys


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser()
    parser.add_argument("dir", help="directory containing bulkbench results")
    return parser


def main() -> int:
    create_parser().parse_args()
    return 0


if __name__ == "__main__":
    sys.exit(main())
