#!/usr/bin/env python3
"""Renew generated instruction facts and package copies from their authored inputs."""

from __future__ import annotations

import argparse
import json
import sys
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "mcp/src"), str(ROOT / "mcp/test_support")]

from agents_remember.errors import CapsuleCompilationError  # noqa: E402
from instruction_renewal.plan import RenewalRequest, apply, plan, targets  # noqa: E402

MISSING = object()


def stale_values(before: object, after: object, key: str = "$") -> list[str]:
    """Name each changed JSON leaf, including missing generated fields."""
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            field
            for member in sorted(before.keys() | after.keys())
            for field in stale_values(
                before.get(member, MISSING), after.get(member, MISSING), f"{key}.{member}"
            )
        ]
    if isinstance(before, list) and isinstance(after, list):
        return [
            field
            for index, (old, new) in enumerate(zip_longest(before, after, fillvalue=MISSING))
            for field in stale_values(old, new, f"{key}[{index}]")
        ]
    return [] if before == after else [key]


def changed_fields(before: bytes | None, after: bytes | None) -> list[str]:
    if before is None or after is None:
        return ["file presence"]
    try:
        return stale_values(json.loads(before), json.loads(after)) or ["file bytes"]
    except (ValueError, UnicodeDecodeError):
        return ["file bytes"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report stale values without writing.")
    parser.add_argument(
        "--list-targets", action="store_true", help="Print generated carriers and exit."
    )
    parser.add_argument(
        "--bind-unit",
        action="append",
        default=[],
        metavar="UNIT=FILE:START-END",
        help=(
            "Name a unit passage; use UNIT@evidence:N for an evidence association "
            "or UNIT@clause:PLACE:N for one clause in an authored digest place."
        ),
    )
    parser.add_argument("--bind-pointer", action="append", default=[], metavar="ID=FILE:LINE")
    args = parser.parse_args()
    if args.list_targets:
        print("canonical: skills and docs/tool-details.md")
        print("\n".join(targets(ROOT)))
        return 0
    request = RenewalRequest(ROOT, tuple(args.bind_unit), tuple(args.bind_pointer))
    try:
        planned = plan(request)
    except CapsuleCompilationError as error:
        print(error.render(), file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    for notice in planned.notices:
        print(notice)
    for change in planned.replacements:
        state = "stale" if args.check else "renewed"
        for field in changed_fields(change.before, change.after):
            print(f"{state}: {change.path.relative_to(ROOT).as_posix()} {field}")
    if args.check:
        if planned.replacements:
            print("Run: mcp/.venv/bin/python scripts/renew-instructions.py", file=sys.stderr)
            return 1
    else:
        apply(planned)
    print("Instruction facts and package copies are current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
