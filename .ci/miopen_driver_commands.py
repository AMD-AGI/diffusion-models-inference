# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Compatibility import for MIOpenDriver command collection."""

import sys
from pathlib import Path

try:
    from miopen_tuning.collection import MIOpenDriverCommandCollector, env_enabled
except ModuleNotFoundError as exc:
    if exc.name != "miopen_tuning":
        raise
    source_root = Path(__file__).resolve().parents[1] / "src"
    if not (source_root / "miopen_tuning").is_dir():
        raise
    sys.path.insert(0, str(source_root))
    from miopen_tuning.collection import MIOpenDriverCommandCollector, env_enabled

__all__ = ["MIOpenDriverCommandCollector", "env_enabled"]
