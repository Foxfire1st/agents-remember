"""The file-writer route of ``knowledge-ingest`` and ``knowledge-bootstrap`` (MIK-R12 rule 7).

Both commands keep their one spelling. Which writer runs is decided by the memory tree they write:

* a **converted** memory tree (it holds ``knowledge/layout.json``, MIK-R21 rule 1) is written by the
  curator file writer, :func:`~agents_remember.application.knowledge_writer.write_knowledge`;
* an **unconverted** tree keeps the database ingest exactly as before. Until the cutover (MIK-R37)
  no production memory tree is converted, so no production run changes.

On the file route ``--commit`` is still the commit word (without it the run plans, validates and
reports, and writes nothing), and ``--json`` prints the whole report. The database-only arguments --
the candidate directory, baselines and publication -- have no meaning for files in the memory
worktree and are refused by name rather than ignored.

Exit status: 0 when the operation wrote or planned, 1 when it was refused (every problem and
violation is in the report, and nothing was written), and 2 when the invocation itself is refused.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from agents_remember.application.knowledge_writer import (
    Owner,
    WriteReport,
    WriteRequest,
    write_knowledge,
)
from agents_remember.application.knowledge_writer.authoring import DecisionResolver
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.tasks.leaf_decisions import leaf_decision_refusal
from agents_remember.worktrees.knowledge_crossing import unconverted_line_refusal
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

EXIT_WRITTEN = 0
EXIT_WRITE_REFUSED = 1
EXIT_REFUSED = 2

# The ingest arguments that only mean something for the database candidate.
_DATABASE_ONLY = (
    ("candidate_directory", "--candidate-directory"),
    ("baseline", "--baseline"),
    ("rebase_baseline", "--rebase-baseline"),
    ("publish_to", "--publish-to"),
    ("publish_declared", "--publish"),
    ("expected_destination", "--expected-destination"),
)
_LEAF_SUFFIX = re.compile(r"^(?P<task>.+)-L[0-9]+$")
_WAVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_converted(memory_root: Path | None) -> bool:
    """Whether the memory tree at ``memory_root`` is converted (it holds the layout marker)."""

    return memory_root is not None and (memory_root / LAYOUT_MARKER_PATH).is_file()


def unconverted_write_refusal(contract: WorktreeContract | None) -> str | None:
    """MIK-R24 rule 9: an unconverted leaf tree is refused once its official line is converted."""

    if contract is None or contract.memory_worktree is None:
        return None
    return unconverted_line_refusal(
        memory_worktree=contract.memory_worktree,
        memory_repository=contract.memory_repo_path,
        official_branch=contract.memory_source_branch,
        operation="knowledge-ingest",
    )


def load_leaf_contract(contract_path: str) -> WorktreeContract | None:
    """The leaf contract, or ``None`` when it cannot be loaded (the database route reports why)."""

    try:
        return load_contract(Path(contract_path))
    except (ValueError, OSError):
        return None


def converted_contract(contract_path: str) -> WorktreeContract | None:
    """The leaf contract when its memory worktree is converted, else ``None`` (the database route)."""

    contract = load_leaf_contract(contract_path)
    return contract if contract is not None and is_converted(contract.memory_worktree) else None


BLANK_AUTHORIZATION = (
    "--authorization-ref must not be blank: an admitted write needs an authorization"
)


def leaf_owner(contract: WorktreeContract) -> Owner:
    """The leaf this contract encloses, and its task's ID (``task.json`` ``id``, else the leaf's prefix)."""

    leaf = contract.leaf_id or contract.task_name
    task = None
    try:
        task = json.loads((contract.task_root / "task.json").read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError, AttributeError):
        task = None
    if not isinstance(task, str) or not task.strip():
        match = _LEAF_SUFFIX.match(leaf)
        task = match["task"] if match is not None else contract.task_name
    return Owner(task=task, kind="leaf", id=leaf)


def handoff_label(list_path: Path, base: Path | None) -> str:
    """The hand-off list's name as ``origin.handoff.path`` records it: relative to ``base`` if under it."""

    resolved = list_path.resolve()
    if base is not None:
        try:
            return resolved.relative_to(base.resolve()).as_posix()
        except ValueError:
            pass
    return list_path.name


def _read_document(list_path: Path) -> object | str:
    try:
        return json.loads(list_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return f"the hand-off list {list_path} is not JSON this writer can read: {error}"


def _print(report: WriteReport, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_document(), indent=2, sort_keys=True))
    else:
        print(report.render())


def leaf_decisions(contract: WorktreeContract) -> DecisionResolver:
    """The task owner's resolution of a decision the leaf's planned ``dropped`` rows cite."""

    leaf = contract.leaf_id or contract.task_name
    return lambda at: leaf_decision_refusal(contract.task_root, leaf, at)


def run_leaf_write(args: argparse.Namespace, contract: WorktreeContract) -> int:
    """``knowledge-ingest`` on a converted memory worktree: the leaf writes through the file writer."""

    if not str(getattr(args, "authorization_ref", "") or "").strip():
        print(BLANK_AUTHORIZATION)
        return EXIT_REFUSED
    named = [flag for attribute, flag in _DATABASE_ONLY if getattr(args, attribute, None)]
    if named:
        print(
            f"{', '.join(named)} belong to the database candidate; this leaf's memory worktree is "
            "converted, so the file writer writes it in place and takes none of them"
        )
        return EXIT_REFUSED
    list_path = Path(args.hand_off_list)
    document = _read_document(list_path)
    if isinstance(document, str):
        print(document)
        return EXIT_REFUSED
    assert contract.memory_worktree is not None  # a converted contract has one
    report = write_knowledge(
        WriteRequest(
            memory_root=contract.memory_worktree,
            code_root=contract.code_worktree,
            owner=leaf_owner(contract),
            handoff_path=handoff_label(list_path, contract.task_root),
            document=document,
            commit=bool(args.commit),
            authorization=str(args.authorization_ref).strip(),
            decisions=leaf_decisions(contract),
        )
    )
    _print(report, bool(args.as_json))
    return EXIT_WRITE_REFUSED if report.refused else EXIT_WRITTEN


def run_wave_write(
    args: argparse.Namespace, *, memory_root: Path, code_root: Path, task: str
) -> int:
    """``knowledge-bootstrap`` on a converted memory tree: a wave writes through the file writer.

    A bootstrap has no leaf, so its judgment rows belong to a wave: ``--wave`` names it, and the
    wave's history file is ``knowledge/history/<wave>.json`` (MIK-R07 rule 8).
    """

    wave = str(getattr(args, "wave", None) or "").strip()
    if not _WAVE_ID.match(wave):
        print(
            "this repository's memory is converted, so the bootstrap writes files as a wave: "
            "--wave names it with [A-Za-z0-9._-] (its history file is "
            "knowledge/history/<wave>.json)"
        )
        return EXIT_REFUSED
    list_path = Path(args.hand_off_list)
    document = _read_document(list_path)
    if isinstance(document, str):
        print(document)
        return EXIT_REFUSED
    report = write_knowledge(
        WriteRequest(
            memory_root=memory_root,
            code_root=code_root,
            owner=Owner(task=task, kind="wave", id=wave),
            handoff_path=handoff_label(list_path, None),
            document=document,
            commit=bool(args.commit),
            authorization=str(getattr(args, "authorization_ref", "") or "").strip(),
        )
    )
    _print(report, bool(args.as_json))
    return EXIT_WRITE_REFUSED if report.refused else EXIT_WRITTEN
