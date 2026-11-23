#!/usr/bin/env bash
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

set -uo pipefail
umask 000
cd /app/xDiT || exit 1
case "${XDIT_TEST_SUITE:-}" in
  single)
    selector="accelerator and not nvidia and not multi_gpu"
    required_gpus=1
    ;;
  multi)
    selector="accelerator and not nvidia and multi_gpu"
    required_gpus=8
    ;;
  *)
    echo "::error::XDIT_TEST_SUITE must be single or multi." >&2
    exit 1
    ;;
esac
export XDIT_TEST_REQUIRED_GPUS="$required_gpus"
python3 -c '
import os, sys, torch
required = int(os.environ["XDIT_TEST_REQUIRED_GPUS"])
count = torch.cuda.device_count()
if not torch.version.hip or not torch.cuda.is_available() or count < required:
    sys.exit(f"ROCm and at least {required} visible GPU(s) are required for this suite")
if os.environ.get("BRR_JOB") and count != required:
    sys.exit(f"brr allocated {required} GPU(s), but {count} are visible")
if required > 1 and not torch.distributed.is_nccl_available():
    sys.exit("RCCL is required for xDiT multi-GPU tests")
print("ROCm", torch.version.hip, "GPUs:", [torch.cuda.get_device_name(i) for i in range(count)])
' || exit 1

python3 -m pip install --no-cache-dir pytest pytest-timeout pytest-xdist || exit 1

export PYTHONUNBUFFERED=1
failed=0
# A single xdist worker keeps tests serial on the GPU, but a crashing test (e.g. a
# native abort) only kills the worker: it is reported as failed, a fresh worker
# continues, and the JUnit report is still written.
python3 -m pytest -m "$selector" -n 1 --max-worker-restart=10 \
  --junitxml="$XDIT_TEST_OUTPUT_DIR/$XDIT_TEST_SUITE.xml" || failed=1
python3 - "$XDIT_TEST_OUTPUT_DIR/$XDIT_TEST_SUITE.xml" "$XDIT_TEST_SUITE" <<'PY' || failed=1
import sys
import xml.etree.ElementTree as ET

try:
    root = ET.parse(sys.argv[1]).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    passed = sum(int(s.get("tests", 0)) - int(s.get("skipped", 0))
                 - int(s.get("failures", 0)) - int(s.get("errors", 0)) for s in suites)
except (OSError, ET.ParseError, ValueError) as error:
    sys.exit(f"Could not read {sys.argv[2]} test results: {error}")
if passed <= 0:
    sys.exit(f"No {sys.argv[2]} accelerator tests passed")
PY
exit "$failed"
