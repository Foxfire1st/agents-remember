"""CLI adapter for the file writer's route (MIK-R12 rule 7, MIK-R54).

    agents-remember knowledge-ingest --contract <leaf enclosure contract> --list <hand-off list>
        --authorization-ref <ref> [--commit] [--json]
    agents-remember knowledge-ingest --contract <master series contract>
        --crossing <task-id>-crossing-<n> --list <hand-off list> --authorization-ref <ref>
        [--commit] [--json]

``--contract`` is REQUIRED and is the write guard: the run reads the code worktree and writes the
memory worktree that contract names, so no argument list can aim a knowledge write at another leaf's
line. ``--authorization-ref`` is REQUIRED: an admitted write records the authorization it ran under,
and a blank reference is refused.

PLANNING IS THE DEFAULT, AND PLANNING IS ALSO THE DRY RUN. Without ``--commit`` the run reads the
list, resolves every target, validates the tree it would produce, reports, and writes nothing.
``--commit`` is the commit word and the only mode that writes files. The writer commits nothing to
Git: the leaf's closeout commits its memory worktree.

The body this adapter runs lives in :mod:`agents_remember.application.knowledge_write_route`, which
the mounted ``knowledge_write`` tool runs too (MIK-R73 rule 1): the same contract guard, the same
authorization reference and the same refusals. This module adds the argument list and the printed
report, and decides nothing itself. A ``raise`` row needs the leaf's task document, which this
command reaches only through settings it trusts; where it has none, the refusal names the cause in
one fixed clause and the mounted ``knowledge_write`` call that can publish the same hand-off
(MIK-R194 rule 9).

Exit status: 0 when the operation wrote or planned; 1 when the writer refused it (every problem and
violation is in the report, and nothing was written); 2 when the invocation itself is refused (a
contract that cannot be loaded, a blank authorization reference, a list that cannot be read, an
unconverted memory worktree).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agents_remember.application import knowledge_write_command
from agents_remember.application.knowledge_write_route import (
    BLANK_AUTHORIZATION,
    CommandRoute,
    LeafQuestions,
    WaveTarget,
    WriteSubmission,
    crossing_write_report,
    handoff_label,
    is_converted,
    leaf_write_report,
    load_leaf_contract,
    read_handoff_document,
    unconverted_write_refusal,
    wave_write_report,
)
from agents_remember.application.knowledge_writer import WriteReport
from agents_remember.cli.discovery import discover_config
from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.primitives.checkout_coordination import (
    checkout_cli_location,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.worktrees.worktree_contract import WorktreeContract

EXIT_WRITTEN = 0
EXIT_WRITE_REFUSED = 1
EXIT_REFUSED = 2
CHECKOUT_GUARD_CLAUSE = (
    "the checkout guard refuses settings to an undeclared run from the primary checkout"
)

__all__ = [
    "BLANK_AUTHORIZATION",
    "EXIT_REFUSED",
    "EXIT_WRITE_REFUSED",
    "EXIT_WRITTEN",
    "LeafQuestions",
    "command_settings",
    "is_converted",
    "load_leaf_contract",
    "run_crossing_write",
    "run_leaf_write",
    "run_wave_write",
    "unconverted_write_refusal",
]


def _print(report: WriteReport, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_document(), indent=2, sort_keys=True))
    else:
        print(report.render())


def run_leaf_write(args: argparse.Namespace, contract: WorktreeContract) -> int:
    """``knowledge-ingest`` on a converted memory worktree: the leaf writes through the file writer."""

    if not str(getattr(args, "authorization_ref", "") or "").strip():
        print(BLANK_AUTHORIZATION)
        return EXIT_REFUSED
    list_path = Path(args.hand_off_list)
    document = read_handoff_document(list_path)
    if isinstance(document, str):
        print(document)
        return EXIT_REFUSED
    name = handoff_label(list_path, contract.task_root)
    report = leaf_write_report(
        contract,
        WriteSubmission(
            document=document,
            handoff_path=name,
            authorization_ref=str(args.authorization_ref),
            commit=bool(args.commit),
        ),
        command=CommandRoute(
            settings=command_settings,
            handoff_path=str(list_path.resolve()),
            handoff_name=name,
            contract_path=str(contract.contract_path.resolve()),
            authorization_ref=str(args.authorization_ref),
        ),
    )
    if isinstance(report, str):
        print(report)
        return EXIT_REFUSED
    _print(report, bool(args.as_json))
    return EXIT_WRITE_REFUSED if report.refused else EXIT_WRITTEN


def run_crossing_write(args: argparse.Namespace, contract: WorktreeContract | str) -> int:
    """``knowledge-ingest --crossing``: a master line's crossing sync records its rows."""

    crossing = str(getattr(args, "crossing", "") or "").strip()
    list_path = Path(args.hand_off_list)
    document = read_handoff_document(list_path)
    if isinstance(document, str):
        print(document)
        return EXIT_REFUSED
    base = contract.task_root if isinstance(contract, WorktreeContract) else None
    report = crossing_write_report(
        contract,
        crossing,
        WriteSubmission(
            document=document,
            handoff_path=handoff_label(list_path, base),
            authorization_ref=str(getattr(args, "authorization_ref", "") or ""),
            commit=bool(args.commit),
        ),
    )
    if isinstance(report, str):
        print(report)
        return EXIT_REFUSED
    _print(report, bool(args.as_json))
    return EXIT_WRITE_REFUSED if report.refused else EXIT_WRITTEN


def run_wave_write(
    args: argparse.Namespace,
    *,
    memory_root: Path,
    code_root: Path,
    task: str,
    coordination_root: Path | None = None,
) -> int:
    """``knowledge-bootstrap``: a wave writes a converted memory tree through the file writer."""

    list_path = Path(args.hand_off_list)
    document = read_handoff_document(list_path)
    if isinstance(document, str):
        print(document)
        return EXIT_REFUSED
    report = wave_write_report(
        WaveTarget(
            memory_root=memory_root,
            code_root=code_root,
            wave=str(getattr(args, "wave", None) or ""),
            task=task,
            coordination_root=coordination_root,
        ),
        WriteSubmission(
            document=document,
            handoff_path=handoff_label(list_path, None),
            authorization_ref=str(getattr(args, "authorization_ref", "") or ""),
            commit=bool(args.commit),
        ),
    )
    if isinstance(report, str):
        print(report)
        return EXIT_REFUSED
    _print(report, bool(args.as_json))
    return EXIT_WRITE_REFUSED if report.refused else EXIT_WRITTEN


def command_settings() -> McpRuntimeConfig | str:
    """The settings this command line would use, or the one fixed clause naming why it cannot.

    The sorting of the causes lives in :mod:`application.knowledge_write_command`; this function hands
    it the command's own three moves. No error's own text is embedded, so the refusal names one clause
    and the mounted call that works (MIK-R194 rule 9).
    """

    return knowledge_write_command.discovered_settings(
        Path.cwd(),
        discover=discover_config,
        guard=checkout_cli_location,
        load=load_config,
        unreadable=(AgentsRememberError, OSError, ValueError),
    )
