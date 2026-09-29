"""CLI adapter: the curator's standalone run of the knowledge validator (MIK-R22 rule 8).

    agents-remember knowledge-validate MEMORY_ROOT --code CODE_ROOT [--code-commit REV]
                                       [--base REV ...] [--json]

``MEMORY_ROOT`` is a memory working tree; its ``knowledge/`` and ``onboarding/`` files are the
candidate. ``--code`` is the paired code checkout (its working tree, or ``--code-commit`` in it).
Each ``--base`` is a memory commit to compare anchors with (rule 6): K_B, or both parents of a merge.
With no base every anchor is checked for path existence.

Every registered rule runs; there is no option that skips one. A tree whose candidate and bases all
lack the layout marker is unconverted and out of the validator's scope, which the command says.

Exit status: 0 when the candidate passes (report-only findings are printed), or is out of scope; 1
when a violation refuses it; 2 when an input cannot be read.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.memory_quality.knowledge_validator.report import ValidationReport
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodeDirectory,
    CodeTree,
    KnowledgeTreeReadError,
    code_tree_from_git,
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.knowledge_validator.validator import (
    validate_tree,
    validation_applies,
)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("memory_root", type=Path, help="The memory working tree to validate.")
    parser.add_argument(
        "--code", required=True, type=Path, help="The paired code checkout (anchor paths)."
    )
    parser.add_argument(
        "--code-commit", help="Check anchor paths at this commit of --code, not its working tree."
    )
    parser.add_argument(
        "--base",
        action="append",
        default=[],
        help="A memory commit to compare anchors with (repeat for each parent of a merge).",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")


def _print(report: ValidationReport, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_document(), indent=2, sort_keys=True))
        return
    if report.violations:
        print(report.render())
    verdict = "passes" if report.ok else "is refused"
    print(
        f"{report.candidate} {verdict}: {len(report.refusals)} violation(s), "
        f"{len(report.reports)} report-only finding(s)"
    )


def run(args: argparse.Namespace) -> int:
    memory_root: Path = args.memory_root.resolve()
    code_root: Path = args.code.resolve()
    try:
        candidate = knowledge_tree_from_directory(memory_root)
        bases = [
            knowledge_tree_from_git(memory_root, base, label=f"base {base}") for base in args.base
        ]
        code: CodeTree = (
            code_tree_from_git(code_root, args.code_commit)
            if args.code_commit
            else CodeDirectory(label=code_root.as_posix(), root=code_root)
        )
    except (OSError, KnowledgeTreeReadError, ValueError) as error:
        print(f"cannot read the validator's inputs: {error}")
        return 2
    if not validation_applies(candidate, bases):
        print(
            f"{memory_root} is unconverted (no knowledge/layout.json in it or its bases); "
            "the knowledge validator applies to converted trees only (MIK-R22 rule 8)"
        )
        return 0
    report = validate_tree(candidate, bases=bases, code=code)
    _print(report, as_json=args.json)
    return 0 if report.ok else 1
