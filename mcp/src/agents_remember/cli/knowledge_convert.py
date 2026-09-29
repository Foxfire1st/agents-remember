"""CLI adapter: convert a memory tree into the text knowledge format (MIK-R24).

    agents-remember knowledge-convert MEMORY_ROOT --code CODE_REPOSITORY [--code-commit REV]
                                      [--version V] [--report FILE] [--check]

``MEMORY_ROOT`` is a memory working tree (its ``onboarding/`` cards and its ``knowledge.sqlite``).
``--code`` is the paired code repository: every card's citations are anchored in the tree of the
card's ``lastVerifiedCommitHash`` read from its object store, and ``--code-commit`` (default its
``HEAD``) is the paired code tree used for cards whose commit is missing and for the report's
currentness counts. ``--version`` is the conversion-format version; this build reproduces ``1``
only and refuses any other.

The conversion validates the whole converted tree before it writes anything; ``--check`` computes
and reports without writing. A tree that already holds ``knowledge/layout.json`` is left unchanged.
The command commits nothing: committing a conversion to a real memory line is the cutover's
(MIK-R37) or a crossing sync's (rule 8) business.

Exit status: 0 converted (or already converted); 1 refused (validation or an input the conversion
needs); 2 invocation or read error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.memory.conversion.code_objects import CodeObjectError, CodeObjects
from agents_remember.memory.conversion.convert import (
    CONVERSION_FORMAT_VERSION,
    ConversionRefused,
    ConversionVersionError,
    convert_memory,
)
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.memory.conversion.legacy_db import ExportError
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTreeReadError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("memory_root", type=Path, help="The memory working tree to convert.")
    parser.add_argument(
        "--code", required=True, type=Path, help="The paired code repository (its object store)."
    )
    parser.add_argument(
        "--code-commit", default="HEAD", help="The paired code commit (default: HEAD of --code)."
    )
    parser.add_argument(
        "--version",
        default=CONVERSION_FORMAT_VERSION,
        help=f"The conversion-format version (this build: {CONVERSION_FORMAT_VERSION}).",
    )
    parser.add_argument("--report", type=Path, help="Write the JSON report to this file.")
    parser.add_argument("--check", action="store_true", help="Convert and report; write nothing.")


def run(args: argparse.Namespace) -> int:
    memory_root: Path = args.memory_root.resolve()
    try:
        memory = memory_from_directory(memory_root)
        objects = CodeObjects(args.code.resolve())
        outcome = convert_memory(
            memory, objects, paired_commit=args.code_commit, version=args.version
        )
    except ConversionVersionError as error:
        print(f"knowledge-convert refused: {error}")
        return 1
    except (ConversionRefused, ExportError, CodeObjectError) as error:
        print(f"knowledge-convert refused: {error}")
        return 1
    except (OSError, KnowledgeTreeReadError, ValueError) as error:
        print(f"knowledge-convert cannot read its inputs: {error}")
        return 2
    if args.report is not None:
        args.report.write_text(json.dumps(outcome.report, indent=2, sort_keys=True) + "\n")
    if outcome.state == "already-converted":
        print(f"{memory_root} is already converted (knowledge/layout.json); nothing to do")
        return 0
    if not args.check:
        write_changed(memory_root, outcome.changed)
    after = outcome.report["after"]
    verb = "would write" if args.check else "wrote"
    print(
        f"knowledge-convert {verb} {len(outcome.changed)} file(s) in {memory_root}: "
        f"{after['references']} reference(s), {after['unresolvedTargets']} unresolved target(s), "
        f"{after['invariants']} invariant(s), {after['families']} family(ies), "
        f"{after['realizations']} realization(s)"
    )
    return 0
