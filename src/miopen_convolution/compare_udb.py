#!/usr/bin/env python3
# Copyright Advanced Micro Devices, Inc.
# SPDX-License-Identifier: MIT

"""Compare two MIOpen user database text files (.udb.txt only).

Use this script to diff exported MIOpen `.udb.txt` user DBs (one `key=value` record
per line). Do not use it for other DB formats.

Each non-empty, non-comment line is split on the first '=' into a string key and
a value (solver / record). Keys are compared as literal strings (no parsing).

Value comparison can use the full string after '=' or only the primary solver:
the substring before the first ';' (remainder are fallback solvers).

Optional reporting: detect changes in C++ template names (identifiers immediately
before a '<') within the primary solver string.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class DbLoadResult:
    path: Path
    keys: Set[str]
    value_for: Dict[str, str]
    content_lines: int
    loaded_lines: int
    lines_without_equals: List[Tuple[int, str]] = field(default_factory=list)
    duplicate_key_lines: int = 0

    @property
    def unique_keys(self) -> int:
        return len(self.keys)


def _split_line(line: str) -> Tuple[str, str]:
    line = line.strip()
    if "=" in line:
        k, _, rest = line.partition("=")
        return k.strip(), rest
    return line, ""


def load_db(path: Path) -> DbLoadResult:
    """Load records from a `.udb.txt` file (key=value lines)."""
    keys: Set[str] = set()
    value_for: Dict[str, str] = {}
    content_lines = 0
    loaded_lines = 0
    lines_without_equals: List[Tuple[int, str]] = []
    duplicate_key_lines = 0
    seen: Set[str] = set()

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            content_lines += 1
            if "=" not in raw:
                lines_without_equals.append((line_num, raw[:120]))
                continue
            key, value = _split_line(raw)
            loaded_lines += 1
            if key in seen:
                duplicate_key_lines += 1
            else:
                seen.add(key)
                value_for[key] = value
            keys.add(key)

    return DbLoadResult(
        path=path.resolve(),
        keys=keys,
        value_for=value_for,
        content_lines=content_lines,
        loaded_lines=loaded_lines,
        lines_without_equals=lines_without_equals,
        duplicate_key_lines=duplicate_key_lines,
    )


def _print_list(title: str, items: List[str], max_items: int) -> None:
    print(f"\n{title} ({len(items)}):")
    show = items if max_items <= 0 else items[:max_items]
    for s in show:
        print(f"  {s}")
    if max_items > 0 and len(items) > max_items:
        print(f"  ... and {len(items) - max_items} more (use --max-list 0 to print all)")


def _primary_solver(value: str) -> str:
    """Primary solver in a `.udb.txt` value: text before the first ';'."""
    return value.split(";", 1)[0]


# Identifier immediately before '<' (exclude ':' so we don't merge "Solver:Class<").
# Nested templates yield multiple names in left-to-right order (e.g. Outer<Inner<...>>).
_CPP_TEMPLATE_HEAD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*<")


def _cpp_template_names(primary_solver: str) -> Tuple[str, ...]:
    """Ordered C++ template names (text before each '<') in a primary solver string."""
    return tuple(m.group(1) for m in _CPP_TEMPLATE_HEAD.finditer(primary_solver))


def _format_template_tuple(names: Tuple[str, ...]) -> str:
    return ", ".join(names) if names else "(none)"


def _format_value_pair(va: str, vb: str, full_values: bool) -> Tuple[str, str]:
    if full_values:
        return va, vb
    limit = 200
    return (
        va[:limit] + ("…" if len(va) > limit else ""),
        vb[:limit] + ("…" if len(vb) > limit else ""),
    )


def compare(
    a: DbLoadResult,
    b: DbLoadResult,
    max_list: int,
    compare_values: bool,
    full_values: bool,
    first_solver_only: bool,
    report_cpp_template_changes: bool,
) -> int:
    """Compare two loaded `.udb.txt` databases; return 0 if identical per configured rules."""
    only_a = sorted(a.keys - b.keys)
    only_b = sorted(b.keys - a.keys)
    common = a.keys & b.keys

    print("=== Per-file stats ===")
    for label, r in ("A", a), ("B", b):
        dup_note = (
            f", duplicate keys (same key on another line): {r.duplicate_key_lines}"
            if r.duplicate_key_lines
            else ""
        )
        bad_note = ""
        if r.lines_without_equals:
            bad_note = f", lines without '=': {len(r.lines_without_equals)}"
        print(
            f"  {label} {r.path}\n"
            f"    non-empty non-comment lines: {r.content_lines}\n"
            f"    key=value lines loaded:      {r.loaded_lines}{dup_note}{bad_note}\n"
            f"    unique keys:                 {r.unique_keys}"
        )

    print("\n=== Comparison (string equality of key before '=') ===")
    print(f"  keys only in A: {len(only_a)}")
    print(f"  keys only in B: {len(only_b)}")
    print(f"  keys in both:   {len(common)}")

    if a.unique_keys or b.unique_keys:
        union = len(a.keys | b.keys)
        jaccard = len(common) / union if union else 1.0
        print(f"  Jaccard similarity (|∩|/|∪|): {jaccard:.6f}")

    if compare_values and common:
        val_mismatch: List[str] = []
        a_label = "A primary solver" if first_solver_only else "A value"
        b_label = "B primary solver" if first_solver_only else "B value"
        for k in sorted(common):
            va, vb = a.value_for.get(k, ""), b.value_for.get(k, "")
            ca, cb = (
                (_primary_solver(va), _primary_solver(vb))
                if first_solver_only
                else (va, vb)
            )
            if ca != cb:
                sa, sb = _format_value_pair(ca, cb, full_values)
                val_mismatch.append(f"{k}\n    {a_label}: {sa}\n    {b_label}: {sb}")
        same_val = len(common) - len(val_mismatch)
        scope = (
            "primary solver (text before first ';')"
            if first_solver_only
            else "full value (after first '=')"
        )
        print(f"\n  {scope.capitalize()}: identical for {same_val}, different for {len(val_mismatch)}")
        title = (
            "Primary solver mismatches (before first ';')"
            if first_solver_only
            else "Value mismatches"
        )
        if val_mismatch:
            _print_list(title, val_mismatch, max_list)

        if report_cpp_template_changes:
            tpl_mismatch: List[str] = []
            for k in sorted(common):
                va, vb = a.value_for.get(k, ""), b.value_for.get(k, "")
                pa, pb = _primary_solver(va), _primary_solver(vb)
                ta, tb = _cpp_template_names(pa), _cpp_template_names(pb)
                if ta != tb:
                    tpl_mismatch.append(
                        f"{k}\n"
                        f"    A template(s): {_format_template_tuple(ta)}\n"
                        f"    B template(s): {_format_template_tuple(tb)}"
                    )
            same_tpl = len(common) - len(tpl_mismatch)
            print(
                f"\n  Primary solver C++ template name sequence: "
                f"unchanged for {same_tpl}, different for {len(tpl_mismatch)}"
            )
            if tpl_mismatch:
                _print_list(
                    "C++ template name changes (ordered Name<…> in primary solver)",
                    tpl_mismatch,
                    max_list,
                )

    if only_a:
        _print_list("Only in A (not in B) — key string", only_a, max_list)
    if only_b:
        _print_list("Only in B (not in A) — key string", only_b, max_list)

    for label, r in ("A", a), ("B", b):
        if r.lines_without_equals:
            print(f"\nLines without '=' in {label} ({len(r.lines_without_equals)}):")
            for ln, snip in r.lines_without_equals[:20]:
                print(f"  line {ln}: {snip}")
            if len(r.lines_without_equals) > 20:
                print(f"  ... {len(r.lines_without_equals) - 20} more")

    identical = not only_a and not only_b and not a.lines_without_equals and not b.lines_without_equals
    if compare_values and common:
        for k in common:
            va, vb = a.value_for.get(k, ""), b.value_for.get(k, "")
            ca, cb = (
                (_primary_solver(va), _primary_solver(vb))
                if first_solver_only
                else (va, vb)
            )
            if ca != cb:
                identical = False
                break

    return 0 if identical else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two MIOpen .udb.txt user database exports (key=value lines per record)."
        )
    )
    parser.add_argument("file_a", type=Path, help="First .udb.txt file")
    parser.add_argument("file_b", type=Path, help="Second .udb.txt file")
    parser.add_argument(
        "--max-list",
        type=int,
        default=50,
        help="Max lines to print per list section (0 = no limit)",
    )
    parser.add_argument(
        "--compare-values",
        action="store_true",
        help="For keys present in both .udb.txt files, compare the substring after the first '='",
    )
    parser.add_argument(
        "--full-values",
        action="store_true",
        help="With --compare-values, print complete compared segments for mismatches (default: 200-char preview)",
    )
    parser.add_argument(
        "--first-solver-only",
        action="store_true",
        help=(
            "With --compare-values, compare only the primary solver: text before the first ';' "
            "(fallback solvers after ';' are ignored)"
        ),
    )
    parser.add_argument(
        "--report-cpp-template-changes",
        action="store_true",
        help=(
            "With --compare-values, list keys where the ordered C++ template names "
            "(identifiers before '<') differ in the primary solver"
        ),
    )
    args = parser.parse_args(argv)

    if args.full_values and not args.compare_values:
        parser.error("--full-values requires --compare-values")
    if args.first_solver_only and not args.compare_values:
        parser.error("--first-solver-only requires --compare-values")
    if args.report_cpp_template_changes and not args.compare_values:
        parser.error("--report-cpp-template-changes requires --compare-values")

    for p in (args.file_a, args.file_b):
        if not p.exists():
            print(f"Error: file not found: {p}", file=sys.stderr)
            return 2

    ra = load_db(args.file_a)
    rb = load_db(args.file_b)
    return compare(
        ra,
        rb,
        args.max_list,
        args.compare_values,
        args.full_values,
        args.first_solver_only,
        args.report_cpp_template_changes,
    )


if __name__ == "__main__":
    sys.exit(main())
