#!/usr/bin/env python3
"""Apply MPOL_PREFERRED, then exec the remaining argv.

Used by tune.sh so host allocations prefer the fattest NUMA node and spill to
the others. GPU memory is unaffected. A missing or invalid NUMA_PREFERRED is
ignored so a policy failure cannot block a run.
"""
from __future__ import annotations

import ctypes
import os
import sys

MPOL_PREFERRED = 1
# x86_64: __NR_set_mempolicy. glibc does not export the name on all distros.
SYS_SET_MEMPOLICY = 238
MAXNODE = 64


def apply_preferred(node: int) -> None:
    if node < 0 or node >= MAXNODE:
        return
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.syscall.restype = ctypes.c_long
    mask = ctypes.c_ulong(1 << node)
    if libc.syscall(SYS_SET_MEMPOLICY, MPOL_PREFERRED, ctypes.byref(mask), MAXNODE) != 0:
        err = ctypes.get_errno()
        print(f"numa_preferred_exec: set_mempolicy({node}) failed errno={err}", file=sys.stderr)


def main() -> None:
    raw = os.environ.get("NUMA_PREFERRED", "").strip()
    if raw != "":
        try:
            apply_preferred(int(raw))
        except ValueError:
            print(f"numa_preferred_exec: ignoring NUMA_PREFERRED={raw!r}", file=sys.stderr)
    if len(sys.argv) < 2:
        print("usage: numa_preferred_exec.py COMMAND [ARGS...]", file=sys.stderr)
        sys.exit(2)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
