"""The file writer's route: what a leaf, a crossing or a wave writes, as a report (MIK-R54).

Both writer entry points run the functions here: ``agents-remember knowledge-ingest`` /
``knowledge-bootstrap`` (:mod:`agents_remember.cli.knowledge_write_route`) and the mounted
``knowledge_write`` tool (MIK-R73 rule 1). They pass one :class:`WriteSubmission`, so the two
routes cannot disagree about the contract guard, the authorization reference, the worklist
checks or the refusals -- the tool can do nothing the command cannot.

The writer is the curator file writer (:func:`~agents_remember.application.knowledge_writer.
write_knowledge`), into a **converted** memory tree (one that holds ``knowledge/layout.json``).
The canonical knowledge database is retired (MIK-R26), so there is no other writer: an
**unconverted** tree is refused by name, and the refusal says how that tree converts.

This module holds no argument parser and prints nothing: a caller gets the
:class:`~agents_remember.application.knowledge_writer.WriteReport` itself, or a string naming why
the invocation is refused. A ``raise`` row reaches ``task_doc`` through what the caller supplies: the
mounted server's own settings, or a :class:`CommandRoute` whose loader either resolves settings or
names, in one fixed clause, why this command cannot reach the task document. The two routes agree on
when a write is refused; only a refusal's remedy names the route the refusal came from (MIK-R194
rule 9).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_worklist.leaf import (
    WORKLIST_FILE_NAME,
    read_leaf_worklist,
)
from agents_remember.application.knowledge_worklist.recorded_base import (
    RecordedMemoryBaseError,
    recorded_memory_base,
)
from agents_remember.application.knowledge_write_command import CommandRoute, mounted_route
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
from agents_remember.application.knowledge_writer.worklist_checks import worklist_shape_problem
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH, history_path
from agents_remember.tasks.leaf_decisions import leaf_decision_refusal
from agents_remember.worktrees.cutover_lock import legacy_format_refusal
from agents_remember.worktrees.knowledge_crossing import unconverted_line_refusal
from agents_remember.worktrees.sync_transaction_authority import require_record_contract
from agents_remember.worktrees.sync_transaction_git import SyncGitProofError, side_branch_head
from agents_remember.worktrees.sync_transaction_state import (
    SyncJournalReadError,
    SyncOperationRecord,
    SyncOperationStore,
    SyncSideRecord,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

_LEAF_SUFFIX = re.compile(r"^(?P<task>.+)-L[0-9]+$")
_WAVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CROSSING_ID = re.compile(r"^(?P<task>[A-Za-z0-9][A-Za-z0-9._-]*)-crossing-[1-9][0-9]*$")

__all__ = [
    "BLANK_AUTHORIZATION",
    "CommandRoute",
    "LeafQuestions",
    "WaveTarget",
    "WriteSubmission",
    "crossing_write_report",
    "handoff_label",
    "is_converted",
    "leaf_write_report",
    "load_leaf_contract",
    "mounted_route",
    "read_handoff_document",
    "unconverted_write_refusal",
    "wave_write_report",
]


def is_converted(memory_root: Path | None) -> bool:
    """Whether the memory tree at ``memory_root`` is converted (it holds the layout marker)."""

    return memory_root is not None and (memory_root / LAYOUT_MARKER_PATH).is_file()


def unconverted_write_refusal(contract: WorktreeContract) -> str:
    """Why ``knowledge-ingest`` does not write this contract's unconverted memory, by name.

    Unconverted memory converts first (MIK-R46 rule 1): a line that descends from a converted
    line crosses through the crossing sync (MIK-R24 rule 8); any other unconverted tree makes
    its first conversion (MIK-R46 rule 6). The database writer is retired (MIK-R26).
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


def read_handoff_document(list_path: Path) -> object | str:
    try:
        return json.loads(list_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return f"the hand-off list {list_path} is not JSON this writer can read: {error}"


def leaf_decisions(contract: WorktreeContract) -> DecisionResolver:
    """The task owner's resolution of a decision the leaf's planned ``dropped`` rows cite."""

    leaf = contract.leaf_id or contract.task_name
    return lambda at: leaf_decision_refusal(contract.task_root, leaf, at)


@dataclass
class LeafQuestions:
    """The leaf task document's ``openQuestions`` through ``task_doc``, for ``raise`` rows.

    The mounted ``knowledge_write`` passes the server's own settings (MIK-R73 rule 1). A command line
    passes a :class:`CommandRoute` instead: when it cannot reach the task document, its refusal names
    the cause and the mounted route that works (MIK-R194 rule 9).
    """

    contract: WorktreeContract
    config: McpRuntimeConfig | None = None
    command: CommandRoute | None = None
    _port: OpenQuestions | None = None

    def check(self, key: str, question: str) -> str | None:
        return self._resolved().check(key, question)

    def append(self, key: str, question: str) -> str | None:
        return self._resolved().append(key, question)

    def append_many(self, raised: list[tuple[str, str]]) -> str | None:
        return self._resolved().append_many(raised)

    @property
    def destination(self) -> str:
        return str(getattr(self._resolved(), "destination", self.contract.task_root))

    def _resolved(self) -> OpenQuestions:
        if self._port is None:
            self._port = self._open()
        return self._port

    def _open(self) -> OpenQuestions:
        if self.config is not None:
            return self._mounted(self.config)
        if self.command is not None:
            return self._command(self.command)
        return UnavailableOpenQuestions(
            "no MCP authority settings to reach task_doc, and no command route naming the mounted "
            "knowledge_write"
        )

    def _mounted(self, config: McpRuntimeConfig) -> OpenQuestions:
        contract = self.contract
        if config.coordination_root.resolve() != contract.coordination_root.resolve():
            return UnavailableOpenQuestions(
                f"settings {config.config_path} name coordination root {config.coordination_root}, "
                f"but the contract names {contract.coordination_root}; load settings for the contract's root"
            )
        return self._task_doc(config)

    def _command(self, route: CommandRoute) -> OpenQuestions:
        config = route.settings()
        if isinstance(config, str):
            return UnavailableOpenQuestions(mounted_route(route, config))
        contract = self.contract
        if config.coordination_root.resolve() != contract.coordination_root.resolve():
            return UnavailableOpenQuestions(
                mounted_route(
                    route,
                    f"the settings {config.config_path} name coordination root "
                    f"{config.coordination_root}, but the contract names {contract.coordination_root}",
                )
            )
        return self._task_doc(config)

    def _task_doc(self, config: McpRuntimeConfig) -> OpenQuestions:
        contract = self.contract
        return TaskDocOpenQuestions(
            config=config,
            repo_id=contract.repo_name,
            contract_path=contract.contract_path,
            task_root=contract.task_root,
            leaf=contract.leaf_id or contract.task_name,
        )


def _leaf_memory_base(contract: WorktreeContract) -> str | None:
    """Establish the recorded base for card comparison, if it is readable and in leaf history."""

    try:
        return recorded_memory_base(contract)
    except RecordedMemoryBaseError:
        return None


@dataclass(frozen=True)
class WriteSubmission:
    """One write as a caller hands it in: the document, its name, the authorization, commit.

    Both ``knowledge-ingest`` and the mounted ``knowledge_write`` pass this one value, so the
    two routes cannot disagree about what a submission is.
    """

    document: object
    handoff_path: str
    authorization_ref: str
    commit: bool = False


@dataclass(frozen=True)
class WaveTarget:
    """The tree, code checkout and owner one taskless wave write names (MIK-R78 rule 9)."""

    memory_root: Path
    code_root: Path
    wave: str
    task: str
    coordination_root: Path | None = None


def leaf_write_report(
    contract: WorktreeContract,
    submission: WriteSubmission,
    config: McpRuntimeConfig | None = None,
    command: CommandRoute | None = None,
) -> WriteReport | str:
    """One leaf write's report, or why the invocation itself is refused (MIK-R73 rule 1).

    The one body both ``knowledge-ingest`` and the mounted ``knowledge_write`` run: the
    same contract guard, the same worklist checks and the same writer. A string is an
    invocation refusal (a blank authorization, an unreadable worklist); a refused
    ``WriteReport`` is the writer's own answer and carries every problem. ``config`` is the mounted
    server's settings; ``command`` is the command line's own route, whose refusal names the mounted
    call that can publish a raise (MIK-R194 rule 9).
    """

    authorization = str(submission.authorization_ref or "").strip()
    if not authorization:
        return BLANK_AUTHORIZATION
    assert contract.memory_worktree is not None  # a converted contract has one
    try:
        worklist = read_leaf_worklist(contract.contract_path)
    except (OSError, ValueError) as error:
        return (
            f"{contract.contract_path.parent / WORKLIST_FILE_NAME}: unreadable worklist: "
            f"{error}; recompute with memory_quality_check"
        )
    if shape := worklist_shape_problem(worklist):
        return f"{contract.contract_path.parent / WORKLIST_FILE_NAME}: {shape.message}"
    return write_knowledge(
        WriteRequest(
            memory_root=contract.memory_worktree,
            code_root=contract.code_worktree,
            owner=leaf_owner(contract),
            handoff_path=submission.handoff_path,
            document=submission.document,
            commit=submission.commit,
            authorization=authorization,
            decisions=leaf_decisions(contract),
            coordination_root=contract.coordination_root,
            questions=LeafQuestions(contract, config=config, command=command),
            worklist=worklist,
            code_base=contract.code_base_commit or None,
            memory_base=_leaf_memory_base(contract),
        )
    )


def _crossing_contract_refusal(contract: WorktreeContract | str, crossing: str) -> str | None:
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
    return None


def _crossing_operation(contract: WorktreeContract, crossing: str) -> SyncOperationRecord | str:
    """Read the open sync that owns the requested crossing."""

    try:
        record = SyncOperationStore(contract.worktree_group).read()
        if (
            not isinstance(record, SyncOperationRecord)
            or record.phase != "memory-resolution-required"
        ):
            return (
                f"No memory sync is open for {contract.task_id}. Run worktree_sync on "
                f"{contract.contract_path} before writing crossing history"
            )
        require_record_contract(contract, record)
    except (OSError, SyncJournalReadError, SyncGitProofError) as error:
        return f"the master's open sync operation cannot be read: {error}"
    owner = record.memory.crossingHistoryOwner if record.memory is not None else ""
    if owner != crossing:
        return (
            f"The open memory sync owns {owner or 'no crossing history'}. "
            f"It cannot write history for {crossing!r}"
        )
    return record


def _crossing_history_refusal(memory_root: Path, crossing: str) -> str | None:
    path = memory_root / history_path(crossing)
    document = read_handoff_document(path) if path.is_file() else None
    if not isinstance(document, dict) or document.get("crossing") != crossing:
        return (
            f"{path} is not open. Worktree_sync opens crossing history when the memory merge "
            "parks for resolution"
        )
    if document.get("closed") is not False:
        return f"{path} is closed: the crossing sync's commit froze it (MIK-R07 rule 7)"
    return None


def _crossing_code_snapshot(code: SyncSideRecord) -> CodeSnapshot | str:
    if code.state != "completed" or not code.resultHead:
        return "The sync's code side has not completed. Finish the code sync before writing history"
    try:
        head = side_branch_head(code)
        if head != code.resultHead:
            return (
                f"The sync's code branch {code.workBranch!r} moved to {head}. "
                f"It must point to its settled commit {code.resultHead} to write history"
            )
        return CodeSnapshot.at_commit(Path(code.repository), code.resultHead)
    except (AnchorResolutionError, RuntimeError, OSError) as error:
        return f"the crossing's paired code cannot be read: {error}"


def crossing_write_report(
    contract: WorktreeContract | str,
    crossing: str,
    submission: WriteSubmission,
) -> WriteReport | str:
    """One crossing write's report, or why it is refused (MIK-R24 rule 8 step 4).

    The rows go into the ``<task-id>-crossing-<n>.json`` file the sync opened in its memory
    worktree, and a conflicted record named by its ``id`` is resolved at one more than the
    higher side's revision; C is the settled code commit recorded by that sync.
    """

    authorization = str(submission.authorization_ref or "").strip()
    if not authorization:
        return BLANK_AUTHORIZATION
    if refusal := _crossing_contract_refusal(contract, crossing):
        return refusal
    assert not isinstance(contract, str)  # a contract that cannot be loaded is refused above
    record = _crossing_operation(contract, crossing)
    if isinstance(record, str):
        return record
    assert record.memory is not None  # the crossing belongs to the memory side
    memory_root = Path(record.memory.worktree)
    if refusal := _crossing_history_refusal(memory_root, crossing):
        return refusal
    code = _crossing_code_snapshot(record.code)
    if isinstance(code, str):
        return code
    return write_knowledge(
        WriteRequest(
            memory_root=memory_root,
            code_root=contract.code_repo_path,
            owner=Owner(task=leaf_owner(contract).task, kind="crossing", id=crossing),
            handoff_path=submission.handoff_path,
            document=submission.document,
            commit=submission.commit,
            authorization=authorization,
            coordination_root=contract.coordination_root,
            code_base=record.code.resultHead,
        ),
        code=code,
    )


def wave_write_report(target: WaveTarget, submission: WriteSubmission) -> WriteReport | str:
    """One wave write's report, or why it is refused (MIK-R78 rule 9, MIK-R73 rule 1).

    A bootstrap has no leaf, so its judgment rows belong to a wave: ``wave`` names it, and
    the wave's history file is ``knowledge/history/<wave>.json`` (MIK-R07 rule 8).
    """

    wave = str(target.wave or "").strip()
    if not _WAVE_ID.match(wave):
        return (
            "the bootstrap writes knowledge files as a wave: --wave names it with "
            "[A-Za-z0-9._-] (its history file is knowledge/history/<wave>.json)"
        )
    return write_knowledge(
        WriteRequest(
            memory_root=target.memory_root,
            code_root=target.code_root,
            owner=Owner(task=target.task, kind="wave", id=wave),
            handoff_path=submission.handoff_path,
            document=submission.document,
            commit=submission.commit,
            authorization=str(submission.authorization_ref or "").strip(),
            coordination_root=target.coordination_root,
        )
    )
