"""The file-writer route of ``knowledge-ingest`` and ``knowledge-bootstrap`` (MIK-R12 rule 7).

Both commands write knowledge as files, through the curator file writer
(:func:`~agents_remember.application.knowledge_writer.write_knowledge`), into a **converted** memory
tree (one that holds ``knowledge/layout.json``, MIK-R21 rule 1). The canonical knowledge database is
retired (MIK-R26), so there is no other writer: an **unconverted** tree is refused by name, and the
refusal says how that tree converts (the crossing sync, or the conversion command).

``knowledge-ingest --crossing <task-id>-crossing-<n>`` is the route of a master line's crossing sync
(MIK-R24 rule 8 step 4): with the master's series contract, the curator resolves a record both sides
changed (the writer sets its revision to one more than the higher side's) and records the rows about
it into the crossing history file the sync opened, in the sync's memory worktree, against the series'
code work branch as C. Such an owner authors no entry, ruling or new record.

``--commit`` is the commit word (without it the run plans, validates and reports, and writes
nothing), and ``--json`` prints the whole report.

Exit status: 0 when the operation wrote or planned, 1 when it was refused (every problem and
violation is in the report, and nothing was written), and 2 when the invocation itself is refused.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_worklist.leaf import read_leaf_worklist
from agents_remember.application.knowledge_writer import (
    Owner,
    WriteReport,
    WriteRequest,
    write_knowledge,
)
from agents_remember.application.knowledge_writer.authoring import DecisionResolver
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.application.knowledge_writer.open_questions import (
    TaskDocOpenQuestions,
    UnavailableOpenQuestions,
)
from agents_remember.application.knowledge_writer.reconsideration import OpenQuestions
from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.primitives.runtime_config import load_config, require_config_path
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH, history_path
from agents_remember.tasks.leaf_decisions import leaf_decision_refusal
from agents_remember.worktrees.cutover_lock import legacy_format_refusal
from agents_remember.worktrees.knowledge_crossing import unconverted_line_refusal
from agents_remember.worktrees.modules.git import local_branch_ref
from agents_remember.worktrees.sync_transaction_authority import side_locations
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

EXIT_WRITTEN = 0
EXIT_WRITE_REFUSED = 1
EXIT_REFUSED = 2

_LEAF_SUFFIX = re.compile(r"^(?P<task>.+)-L[0-9]+$")
_WAVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CROSSING_ID = re.compile(r"^(?P<task>[A-Za-z0-9][A-Za-z0-9._-]*)-crossing-[1-9][0-9]*$")


def is_converted(memory_root: Path | None) -> bool:
    """Whether the memory tree at ``memory_root`` is converted (it holds the layout marker)."""

    return memory_root is not None and (memory_root / LAYOUT_MARKER_PATH).is_file()


def unconverted_write_refusal(contract: WorktreeContract) -> str:
    """Why ``knowledge-ingest`` does not write this contract's unconverted memory, by name.

    A leaf whose official line is converted, or whose repository holds converted memory anywhere,
    converts through the crossing sync (MIK-R24 rule 9, MIK-R09 rule 6). Any other unconverted
    tree is memory in the legacy format: the database writer is retired (MIK-R26).
    """

    memory = contract.memory_worktree
    if memory is None:
        return (
            f"knowledge-ingest refuses {contract.contract_path}: the contract names no memory "
            "worktree, so there is no memory tree to write knowledge files into"
        )
    return unconverted_line_refusal(
        memory_worktree=memory,
        memory_repository=contract.memory_repo_path,
        official_branch=contract.memory_source_branch,
        operation="knowledge-ingest",
    ) or legacy_format_refusal(
        contract.memory_repo_path,
        operation="knowledge-ingest",
        subject=f"the memory worktree {memory.as_posix()}",
    )


def load_leaf_contract(contract_path: str) -> WorktreeContract | str:
    """The contract ``--contract`` names, or why it cannot be loaded."""

    try:
        return load_contract(Path(contract_path))
    except (ValueError, OSError) as error:
        return f"--contract {contract_path} cannot be loaded as a worktree contract: {error}"


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


@dataclass
class LeafQuestions:
    """The leaf task document's ``openQuestions`` through ``task_doc``, for ``raise`` rows.

    The MCP authority settings are read only when a ``raise`` needs them.
    """

    args: argparse.Namespace
    contract: WorktreeContract
    _port: OpenQuestions | None = None

    def check(self, key: str, question: str) -> str | None:
        return self._resolved().check(key, question)

    def append(self, key: str, question: str) -> str | None:
        return self._resolved().append(key, question)

    def _resolved(self) -> OpenQuestions:
        if self._port is None:
            self._port = self._open()
        return self._port

    def _open(self) -> OpenQuestions:
        configured = getattr(self.args, "config", None)
        try:
            config = load_config(
                require_config_path(configured) if configured else discover_config(Path.cwd())
            )
        except (AgentsRememberError, ConfigDiscoveryError, OSError, ValueError) as error:
            return UnavailableOpenQuestions(
                f"no MCP authority settings to reach task_doc ({error}); pass --config"
            )
        contract = self.contract
        return TaskDocOpenQuestions(
            config=config,
            repo_id=contract.repo_name,
            contract_path=contract.contract_path,
            task_root=contract.task_root,
            leaf=contract.leaf_id or contract.task_name,
        )


def run_leaf_write(args: argparse.Namespace, contract: WorktreeContract) -> int:
    """``knowledge-ingest`` on a converted memory worktree: the leaf writes through the file writer."""

    if not str(getattr(args, "authorization_ref", "") or "").strip():
        print(BLANK_AUTHORIZATION)
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
            coordination_root=contract.coordination_root,
            questions=LeafQuestions(args, contract),
            worklist=read_leaf_worklist(contract.contract_path),
            code_base=contract.code_base_commit or None,
        )
    )
    _print(report, bool(args.as_json))
    return EXIT_WRITE_REFUSED if report.refused else EXIT_WRITTEN


def _crossing_memory_root(contract: WorktreeContract | str, crossing: str) -> Path | str:
    """The sync worktree holding the open crossing history file, or why the run is refused."""

    if isinstance(contract, str):
        return f"--crossing needs the master's series contract: {contract}"
    if contract.kind != "series":
        return (
            "--crossing records a master line's crossing sync, so --contract names the master's "
            f"series contract; {contract.contract_path} is a {contract.kind} contract"
        )
    match = _CROSSING_ID.match(crossing)
    if match is None or match["task"] != contract.task_id:
        return (
            f"--crossing names this master's crossing history file, {contract.task_id}-crossing-<n> "
            f"(MIK-R24 rule 8 step 4); {crossing!r} is not one"
        )
    memory_root = side_locations(contract, "memory")[1]
    path = memory_root / history_path(crossing)
    document = _read_document(path) if path.is_file() else None
    if not isinstance(document, dict) or document.get("crossing") != crossing:
        return (
            f"{path} is not an open crossing history file: the crossing sync opens it when a record "
            "conflicts, and its rows are recorded while that sync is being resolved"
        )
    if document.get("closed") is not False:
        return f"{path} is closed: the crossing sync's commit froze it (MIK-R07 rule 7)"
    return memory_root


def run_crossing_write(args: argparse.Namespace, contract: WorktreeContract | str) -> int:
    """``knowledge-ingest --crossing``: a master line's crossing sync records its rows.

    The rows go into the ``<task-id>-crossing-<n>.json`` file the sync opened in its memory
    worktree, and a conflicted record named by its ``id`` is resolved at one more than the higher
    side's revision (MIK-R24 rule 8 step 4); C is the series' code work branch, the sync's paired
    code.
    """

    crossing = str(getattr(args, "crossing", "") or "").strip()
    blank = not str(getattr(args, "authorization_ref", "") or "").strip()
    memory_root = BLANK_AUTHORIZATION if blank else _crossing_memory_root(contract, crossing)
    if isinstance(memory_root, str):
        print(memory_root)
        return EXIT_REFUSED
    assert not isinstance(contract, str)  # a contract that cannot be loaded is refused above
    list_path = Path(args.hand_off_list)
    document = _read_document(list_path)
    if isinstance(document, str):
        print(document)
        return EXIT_REFUSED
    try:
        code = CodeSnapshot.at_commit(
            contract.code_repo_path, local_branch_ref(contract.code_work_branch)
        )
    except (AnchorResolutionError, RuntimeError) as error:
        print(f"the crossing's paired code cannot be read: {error}")
        return EXIT_REFUSED
    report = write_knowledge(
        WriteRequest(
            memory_root=memory_root,
            code_root=contract.code_repo_path,
            owner=Owner(task=leaf_owner(contract).task, kind="crossing", id=crossing),
            handoff_path=handoff_label(list_path, contract.task_root),
            document=document,
            commit=bool(args.commit),
            authorization=str(args.authorization_ref).strip(),
            coordination_root=contract.coordination_root,
            code_base=local_branch_ref(contract.code_work_branch),
        ),
        code=code,
    )
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
    """``knowledge-bootstrap``: a wave writes a converted memory tree through the file writer.

    A bootstrap has no leaf, so its judgment rows belong to a wave: ``--wave`` names it, and the
    wave's history file is ``knowledge/history/<wave>.json`` (MIK-R07 rule 8).
    """

    wave = str(getattr(args, "wave", None) or "").strip()
    if not _WAVE_ID.match(wave):
        print(
            "the bootstrap writes knowledge files as a wave: "
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
            coordination_root=coordination_root,
        )
    )
    _print(report, bool(args.as_json))
    return EXIT_WRITE_REFUSED if report.refused else EXIT_WRITTEN
