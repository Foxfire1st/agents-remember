"""Enforce the repository's File Size Budget standard.

The budget exists as written policy and nothing measured a file against it until
this module.  The bands come from the standard itself: 0-300 healthy default,
300-600 acceptable but watchful, 600-900 refactor pressure, 900-1200 soft limit,
1200+ hard limit exceeded, 2000+ architectural failure, 4000+ emergency cleanup.

The detector flags every file at or above the 1,200-line hard limit and reports
the band it violates.  It runs in two modes:

* enforced (default): any finding exits non-zero, so the module can stand alone
  or be called by any gate;
* ``--report``: findings are printed with the same band lines but the exit is 0,
  which is how the quality wrapper keeps the check wired and visible while the
  tree is still being remediated (unarmed).

Line counting matches ``wc -l`` semantics (newline characters), which is how
the repository measured the violation set this leaf owns.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

FILE_SIZE_HARD_LIMIT = 1200
FILE_SIZE_ARCHITECTURAL_FAILURE = 2000
FILE_SIZE_EMERGENCY_CLEANUP = 4000
FILE_SIZE_HEALTHY_TARGET = 600

STANDARD_LABEL = "File Size Budget standard"


@dataclass(frozen=True)
class FileSizeFinding:
    """One file at or above the hard limit, with the band it violates."""

    path: Path
    line_count: int
    band: str
    limit: int = FILE_SIZE_HARD_LIMIT

    @property
    def location(self) -> str:
        return self.path.as_posix()


def line_count(path: Path) -> int:
    """Lines as ``wc -l`` counts them: the number of newline characters."""
    with path.open("rb") as handle:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))


def band_for(line_count: int) -> str:
    """The standard's band label for a file; under-limit is the healthy case."""
    if line_count < FILE_SIZE_HARD_LIMIT:
        return "under-limit"
    if line_count >= FILE_SIZE_EMERGENCY_CLEANUP:
        return "emergency-cleanup"
    if line_count >= FILE_SIZE_ARCHITECTURAL_FAILURE:
        return "architectural-failure"
    return "hard-limit-exceeded"


def measure(paths: list[Path]) -> list[FileSizeFinding]:
    """Every measured file at or above the hard limit, by path."""
    findings: list[FileSizeFinding] = []
    for path in paths:
        try:
            count = line_count(path)
        except OSError:
            raise
        if count >= FILE_SIZE_HARD_LIMIT:
            findings.append(FileSizeFinding(path=path, line_count=count, band=band_for(count)))
    return sorted(findings, key=lambda finding: finding.location)


def render(finding: FileSizeFinding) -> str:
    """One finding line naming the band the standard assigns to it."""
    return f"  {finding.line_count:6d} lines  {finding.band:<20} {finding.location}"


def scope_line(paths: list[Path]) -> str:
    return (
        f"scope: file-size | input=index-known Python plus dashboard/src TypeScript | "
        f"config={STANDARD_LABEL}; hard limit {FILE_SIZE_HARD_LIMIT}; "
        f"architectural failure {FILE_SIZE_ARCHITECTURAL_FAILURE} | "
        f"{len(paths)} measured files"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure files against the repository File Size Budget. Files at or above "
            f"{FILE_SIZE_HARD_LIMIT} lines are reported with their band; the default "
            "exit is non-zero when any file violates the hard limit. --report keeps the "
            "same output but exits 0, for wiring the check before the tree is clean."
        )
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--report",
        action="store_true",
        help="print findings without failing the run (unarmed mode)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = args.paths
    if not paths:
        print("file-size: no paths supplied; refusing to certify an empty measurement")
        return 1
    try:
        findings = measure(paths)
    except OSError as error:
        print(f"file-size could not read a measured file: {error}")
        return 1
    print(scope_line(paths))
    if findings:
        print(f"{len(findings)} file(s) at or above the {FILE_SIZE_HARD_LIMIT}-line hard limit:")
        for finding in findings:
            print(render(finding))
    else:
        print(f"0 files at or above the {FILE_SIZE_HARD_LIMIT}-line hard limit")
    if args.report:
        print("result: file-size REPORT COMPLETE (unarmed; violations do not fail this run)")
        return 0
    if findings:
        print("result: file-size FAIL (hard limit exceeded)")
        return 1
    print("result: file-size PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
