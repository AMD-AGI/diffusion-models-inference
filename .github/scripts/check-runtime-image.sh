#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -euo pipefail

# Check runtime files used by .ci/run.py without starting a GPU workload.
# Usage: check-runtime-image.sh IMAGE

IMAGE=${1:?usage: check-runtime-image.sh IMAGE}
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/container-name.sh"

docker run --rm \
  --name "$(container_name runtime-check)" \
  --entrypoint bash \
  "$IMAGE" \
  -c '
    set -euo pipefail
    python /app/.ci/run.py --help >/dev/null
    python - <<'"'"'PY'"'"'
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app/.ci")
from miopen_driver_commands import MIOpenDriverCommandCollector, env_enabled
from miopen_tuning.collection import MIOpenDriverCommandCollector as PackageCollector

assert MIOpenDriverCommandCollector is PackageCollector
assert not env_enabled(os.environ.get("CI_RUN_PY_COLLECT_MIOPEN_DRIVER_COMMANDS", "0"))
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    stderr = root / "stderr.log"
    stderr.write_text("MIOpen: Command [LogCmdConvolution] MIOpenDriver convbfp16 -n 1\n")
    collector = MIOpenDriverCommandCollector(root, ["runtime-check"])
    collector.collect("runtime-check", stderr, True)
    output = root / "miopen_workloads" / "runtime-check.txt"
    assert output.read_text() == "MIOpenDriver convbfp16 -n 1\n"
PY
  '
