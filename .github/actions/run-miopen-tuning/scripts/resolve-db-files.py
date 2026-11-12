#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Print the validated MIOpen DB files of a passed tuning run as a GitHub output.

Usage: resolve-db-files.py REPORT >> "$GITHUB_OUTPUT"

Paths are printed relative to the current directory, as actions/upload-artifact
expects workspace-relative paths.
"""

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: resolve-db-files.py REPORT")
    report = json.loads(Path(argv[1]).read_text())
    if report["status"] != "passed":
        raise SystemExit("MIOpen tuning report did not pass")
    paths = [Path(path) for path in report["db"]["files"]]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise SystemExit(f"Validated DB files are missing: {missing}")
    print("paths<<EOF")
    for path in paths:
        print(path.relative_to(Path.cwd()))
    print("EOF")


if __name__ == "__main__":
    main(sys.argv)
