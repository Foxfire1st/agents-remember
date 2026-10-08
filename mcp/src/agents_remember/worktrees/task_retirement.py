"""Retirement readiness: open work of the master's leaves, and an unfinished or unreadable operation of the master's own enclosure.

Read-only. A master may be retired when none of its leaves has open work and its own enclosure
has no unfinished or unreadable operation. Open work is:

* a leaf worktree directory that exists;
* a leaf work branch that exists in the code or memory repository and holds commits that are not
  reachable from the line the leaf lands on;
* an operation record that is unfinished;
* an operation record that cannot be read.

Nothing about the layout or the age of a contract is open work. The recorded worktree group, a
leaf's recorded source branch and every other cell are read as recorded, and none is compared
with what a contract written today would say. What the readiness cannot interpret is a *fact*: it
is reported and written into the retirement record, and it does not refuse. A leaf branch that
holds nothing beyond the line it lands on is such a fact, and stays in place.

The master's own line and worktree never refuse: retiring is the decision to give the master up.
They are facts as well, listed with their state and left in place.

A refusal names every open resource (the worktree path, each branch with its repository, the
operation record) and the action that works for it. While the enclosure has a live operation
locator that is the lifecycle tools. Without one every lifecycle tool asks for an adoption that
this build does not offer, so the refusal gives what an operator does by hand: the Git commands
that remove the worktree or the branch, and the move that takes an operation record out of the
directory it is read from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path, slugify
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    inspect_lifecycle_operation_locator,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.integration.terminal_enclosure_evidence import (
    terminal_operation_evidence,
)
from agents_remember.worktrees.modules.git import local_branch_ref, repository_identity, run_git
from agents_remember.worktrees.sync_transaction_state import (
    SYNC_OPERATION_RECORD_NAME,
    legacy_sync_operation_path,
    observe_sync_operation,
    sync_operation_path,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

# A locator in one of these states has no operation in flight for its enclosure.
_SETTLED_LOCATOR_STATES = frozenset({"missing", "terminal-archived"})
_MAX_FACTS = 512


@dataclass(frozen=True)
class MasterRetirementScope:
    coordination_root: Path
    repo_id: str
    code_repository: Path
    memory_repository: Path | None
    master: ResolvedTaskDocument

    @property
    def task_root(self) -> Path:
        return self.master.path.parent


@dataclass(frozen=True)
class RetirementReadiness:
    """What a ready master's readiness read, and what it found and left as it is.

    ``evidence`` is every source the answer rests on, as the bytes it was read from; a request
    compares it again when it publishes. ``facts`` are the things that do not refuse: what is
    left in place, and what could not be interpreted.
    """

    evidence: tuple[tuple[Path, bytes], ...]
    facts: tuple[str, ...]


@dataclass
class _Survey:
    """One pass over a master's contracts: sources read, facts found, open work found."""

    scope: MasterRetirementScope
    evidence: list[tuple[Path, bytes]] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    open_work: list[str] = field(default_factory=list)
    # The master's own line and the line it lands on, on either side: never a leaf's branch.
    own_lines: set[str] = field(default_factory=set)

    def name(self, path: Path) -> str:
        """A contract as the operator knows it: by its place in the master's folder."""

        return path.relative_to(self.scope.task_root).as_posix()

    @property
    def repositories(self) -> set[Path]:
        """The configured repositories: their own checkouts are nobody's worktree."""

        scope = self.scope
        return {
            repository.resolve()
            for repository in (scope.code_repository, scope.memory_repository)
            if repository is not None
        }


def require_master_retirement_ready(scope: MasterRetirementScope) -> RetirementReadiness:
    """Refuse open work, including the master's own operations; return read evidence and facts."""

    survey = _Survey(scope)
    _read_leaf_documents(survey)
    root = scope.task_root / "series-contract.md"
    if root.exists():
        _survey_contract(survey, root, own=True)
    for path in sorted((scope.task_root / "enclosures").glob("*/series-contract.md")):
        _survey_contract(survey, path, own=False)
    if survey.open_work:
        listed = " ".join(
            f"({index}) {item}" for index, item in enumerate(survey.open_work, start=1)
        )
        raise ContractError(
            f"master {scope.master.ref.key} has open work, so it is not retired. {listed} "
            "When none of this is left, repeat task_doc.retire_master"
        )
    facts = survey.facts[:_MAX_FACTS]
    if len(survey.facts) > _MAX_FACTS:
        facts.append(f"{len(survey.facts) - _MAX_FACTS} further facts are not listed")
    return RetirementReadiness(tuple(survey.evidence), tuple(facts))


def _survey_contract(survey: _Survey, path: Path, *, own: bool) -> None:
    scope = survey.scope
    try:
        contract = load_contract(path)
        survey.evidence.append((path, path.read_bytes()))
    except (ValueError, OSError) as exc:
        # Without the contract its worktree, branches and operation records are not known, so
        # open work cannot be ruled out.
        raise ContractError(
            f"master {scope.task_root.name!r} cannot read contract {path}: {exc}; repair that file before task_doc.retire_master"
        ) from exc
    who = "the master's own contract" if own else f"leaf {contract.leaf_id or path.parent.name!r}"
    _note_recorded_identity(survey, contract, path, who, own=own)
    tools_can_act = _survey_operations(survey, contract, path)
    before = len(survey.open_work)
    for side in _sides(survey, contract, who):
        if own:
            survey.own_lines |= {side.branch, side.line}
            _note_own_side(survey, side)
        else:
            _survey_leaf_side(survey, path, side, who, tools_can_act=tools_can_act)
    if contract.cleanup not in {"completed", "abandoned"} and len(survey.open_work) == before:
        survey.facts.append(
            f"{who} ({survey.name(path)}) records cleanup {contract.cleanup!r}; no open work was "
            "found for it"
        )


@dataclass(frozen=True)
class _Side:
    """One repository side of a contract, as recorded: where to look and for what."""

    label: str
    repository: Path | None
    worktree: Path | None
    branch: str
    line: str


def _sides(survey: _Survey, contract: WorktreeContract, who: str) -> list[_Side]:
    scope = survey.scope
    sides = [
        _Side(
            "code",
            _repository(survey, contract.code_repo_path, scope.code_repository, who, "code"),
            contract.code_worktree,
            contract.code_work_branch,
            contract.code_source_branch,
        )
    ]
    if contract.memory_mode == "external":
        sides.append(
            _Side(
                "memory",
                _repository(
                    survey, contract.memory_repo_path, scope.memory_repository, who, "memory"
                ),
                contract.memory_worktree,
                contract.memory_work_branch,
                contract.memory_source_branch,
            )
        )
    return sides


def _repository(
    survey: _Survey, recorded: Path | None, configured: Path | None, who: str, label: str
) -> Path | None:
    """The repository to read a side's branches from: the recorded one when it is a repository."""

    if repository_identity(recorded) is not None:
        if configured is not None and repository_identity(recorded) != repository_identity(
            configured
        ):
            survey.facts.append(
                f"{who} records the {label} repository {recorded}, which is not the configured "
                f"{configured}; its branches were read where the contract says"
            )
        return recorded
    if repository_identity(configured) is None:
        survey.facts.append(
            f"{who}: neither the recorded {label} repository {recorded} nor a configured one is "
            "a Git repository, so its branch was not looked for"
        )
        return None
    if recorded is not None:
        survey.facts.append(
            f"{who} records the {label} repository {recorded}, which is no Git repository here; "
            f"its branch was looked for in the configured {configured}"
        )
    return configured


def _note_own_side(survey: _Survey, side: _Side) -> None:
    """The master's own worktree and line: listed with their state, never a refusal."""

    if side.worktree is not None and side.worktree.exists():
        what = (
            "is the repository's own checkout"
            if side.worktree.resolve() in survey.repositories
            else "exists"
        )
        survey.facts.append(
            f"the master's own {side.label} worktree {side.worktree} {what}; the retirement "
            "leaves it in place"
        )
        survey.evidence.append((side.worktree, b"exists"))
    count = _unlanded(survey, side)
    if count is None:
        return
    beyond = (
        f"holds {count} commit(s) that are not reachable from {side.line!r}"
        if count >= 0
        else f"cannot be compared with {side.line!r}, which does not resolve"
    )
    survey.facts.append(
        f"the master's own {side.label} branch {side.branch!r} in {side.repository} {beyond}; "
        "the retirement leaves it in place"
    )


def _survey_leaf_side(
    survey: _Survey,
    path: Path,
    side: _Side,
    who: str,
    *,
    tools_can_act: bool,
) -> None:
    """A leaf's worktree directory and work branch: open work when there is work in them."""

    tools = (
        f"finish it with worktree_cleanup, or give it up with worktree_abandon "
        f"(contract_path={path})"
    )
    worktree = side.worktree
    if worktree is not None and worktree.exists():
        survey.evidence.append((worktree, b"exists"))
        if worktree.resolve() in survey.repositories:
            survey.facts.append(
                f"{who} records the repository's own checkout {worktree} as its {side.label} "
                "worktree; it is no leaf worktree and stays in place"
            )
        else:
            action = tools if tools_can_act else _remove_worktree_command(side, worktree)
            survey.open_work.append(
                f"{who}: the {side.label} worktree directory {worktree} exists; {action}."
            )
    count = _unlanded(survey, side)
    if count is None:
        return
    held = f"the {side.label} branch {side.branch!r} in {side.repository}"
    if side.branch == side.line or side.branch in survey.own_lines:
        survey.facts.append(
            f"{who} names {side.branch!r} as its {side.label} work branch, which is the master's "
            "own line or the line it lands on; it is no leaf branch and stays in place"
        )
    elif count > 0:
        action = (
            tools
            if tools_can_act
            else (
                "the cleanup tools cannot act on this enclosure (it has no live operation "
                f"locator), so remove the branch with: git -C {side.repository} branch -D "
                f"{side.branch} (this gives those commits up; land them first to keep them)"
            )
        )
        survey.open_work.append(
            f"{who}: {held} holds {count} commit(s) that are not reachable from {side.line!r}, "
            f"the line it lands on; {action}."
        )
    elif count == 0:
        survey.facts.append(
            f"{who}: {held} exists and holds nothing beyond {side.line!r}; the retirement leaves "
            "it in place"
        )
    else:
        survey.facts.append(
            f"{who}: {held} exists, and the line it lands on, {side.line!r}, does not resolve "
            "there, so its commits could not be compared; the retirement leaves it in place"
        )


def _remove_worktree_command(side: _Side, worktree: Path) -> str:
    return (
        "the cleanup tools cannot act on this enclosure (it has no live operation locator), so "
        f"remove it with: git -C {side.repository} worktree remove {worktree} (add --force to "
        "discard uncommitted changes in it; if Git does not know it as a worktree, delete the "
        "directory)"
    )


def _unlanded(survey: _Survey, side: _Side) -> int | None:
    """How many commits the side's branch holds beyond its line.

    ``None`` when the branch does not exist or was not looked for; ``-1`` when it exists and its
    line does not resolve.
    """

    if side.repository is None or not side.branch:
        return None
    tip = run_git(
        side.repository, ["rev-parse", "--verify", "--quiet", local_branch_ref(side.branch)]
    )
    if tip.returncode != 0 or not tip.stdout.strip():
        return None
    survey.evidence.append(
        (side.repository / local_branch_ref(side.branch), tip.stdout.strip().encode("utf-8"))
    )
    if not side.line:
        return -1
    line = local_branch_ref(side.line)
    counted = run_git(
        side.repository, ["rev-list", "--count", local_branch_ref(side.branch), "--not", line]
    )
    if counted.returncode != 0 or not counted.stdout.strip().isdigit():
        return -1
    return int(counted.stdout.strip())


def _survey_operations(survey: _Survey, contract: WorktreeContract, path: Path) -> bool:
    """The enclosure's operation records: unfinished or unreadable ones are open work.

    Returns whether the lifecycle tools can act on the enclosure, which they can only while it
    has a live operation locator. Each refusal names the action that works for its enclosure:
    the tools where they can act, and otherwise what an operator does by hand, because every
    lifecycle tool answers an enclosure without a locator with a request for adoption.
    """

    scope = survey.scope
    key = scope.master.ref.key
    group = contract.worktree_group
    tools_can_act = False
    try:
        locator = inspect_lifecycle_operation_locator(scope.coordination_root, path)
    except LifecycleOperationLocationError as error:
        survey.open_work.append(
            f"master {scope.task_root.name!r} enclosure {path}: its operation locator cannot be "
            f"looked for ({error.detail}), so it is not known whether an operation of the "
            "enclosure is unfinished; repair what that message names."
        )
    else:
        tools_can_act = locator.state not in {*_SETTLED_LOCATOR_STATES, "unreadable"}
        if tools_can_act:
            survey.open_work.append(
                f"master {scope.task_root.name!r} enclosure {path} locator {locator.path} is "
                f"{locator.state}: {locator.detail or 'lifecycle authority remains open'}; "
                "complete worktree_cleanup or worktree_abandon for that enclosure, or resolve "
                "its operation with worktree_operation_control."
            )
        elif locator.state == "unreadable":
            survey.open_work.append(
                f"master {scope.task_root.name!r} enclosure {path}: its operation locator "
                f"{locator.path} cannot be read ({locator.detail}), so it is not known whether "
                "an operation of the enclosure is unfinished; repair that file, or remove it "
                f"when no operation of the enclosure is to be continued: rm {locator.path} (the "
                "enclosure then has no locator)."
            )
    try:
        entries = terminal_operation_evidence(group)
    except (LifecycleOperationLocationError, OSError, RuntimeError, ValueError) as error:
        record = _refused_record(error)
        action = (
            "resolve the named generation through worktree_operation_control or restore its "
            "admitted evidence"
            if tools_can_act
            else _by_hand(
                "restore the record's admitted bytes, or give the operation up", record, group
            )
        )
        survey.open_work.append(
            f"master {key} contract {path}: retained operation evidence refused: {error}; {action}."
        )
        entries = []
    survey.evidence.extend(
        (group / ".lifecycle" / entry.relativePath, entry.content.encode("utf-8"))
        for entry in entries
    )
    sync = observe_sync_operation(group, contract_path=path)
    if sync is not None and sync.state not in {"completed", "cancelled", "quarantined"}:
        journal = next(
            (
                candidate
                for candidate in (sync_operation_path(group), legacy_sync_operation_path(group))
                if candidate.exists()
            ),
            None,
        )
        action = (
            "resolve it with worktree_sync"
            if tools_can_act
            else _by_hand("give the sync up", journal, group)
        )
        survey.open_work.append(
            f"master {key} contract {path}: unfinished sync operation is {sync.state!r}; {action}."
        )
    _note_older_operation_reports(survey, group)
    return tools_can_act


def _refused_record(error: Exception) -> Path | None:
    """The operation record a refusal of the retained evidence names, when it names one."""

    if not isinstance(error, LifecycleOperationLocationError):
        return None
    named = error.expected.get("operationRecord")
    return Path(named) if isinstance(named, str) else None


def _by_hand(what: str, record: Path | None, group: Path) -> str:
    """The action for an operation record of an enclosure that no lifecycle tool can act on."""

    reason = (
        "the lifecycle tools cannot act on this enclosure (it has no live operation locator), so "
    )
    adoption = " A later build's adoption route replaces this manual step."
    if record is None:
        return (
            f"{reason}{what} by moving the named record out of the directory it is in, to a "
            f"place outside {group / '.lifecycle'} and {group / 'reports'}; choose an unused "
            f"destination and do not overwrite any earlier given-up record.{adoption}"
        )
    return (
        f"{reason}{what} by moving the record out of the directory it is read from: mv -n {record} "
        f"{group / (record.name + '.given-up')} (that operation is then given up for good). "
        "The command does not overwrite an existing destination; if it exists, choose an unused "
        f"destination. Verify that the original record has moved before repeating.{adoption}"
    )


def _note_older_operation_reports(survey: _Survey, group: Path) -> None:
    """Operation reports where an older layout kept them: facts, because no reader acts on them.

    This build keeps operation records under ``.lifecycle`` and reads no other place. A report
    of an older layout that does not say ``completed`` is told to the operator with what it
    records; it is not an operation any tool of this build can finish or resolve.
    """

    for report in sorted((group / "reports").glob("*-operation.json")):
        if report.name == SYNC_OPERATION_RECORD_NAME:
            continue
        try:
            content = report.read_bytes()
            loaded = json.loads(content)
            recorded = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError) as error:
            survey.facts.append(
                f"{report} is an operation report of an older layout and cannot be read "
                f"({type(error).__name__}); this build keeps operation records under .lifecycle "
                "and does not read it"
            )
            continue
        survey.evidence.append((report, content))
        if recorded.get("status") == "completed":
            continue
        survey.facts.append(
            f"{report} is an operation report of an older layout and records status "
            f"{recorded.get('status')!r}, phase {recorded.get('phase')!r}; this build keeps "
            "operation records under .lifecycle and does not read it as one"
        )


def _note_recorded_identity(
    survey: _Survey, contract: WorktreeContract, path: Path, who: str, *, own: bool
) -> None:
    """Cells that do not name this master's own place are facts: they are read as recorded."""

    scope = survey.scope
    recorded = {
        "repo_name": (contract.repo_name, scope.repo_id),
        "coordination.root": (
            contract.coordination_root.resolve(),
            scope.coordination_root.resolve(),
        ),
        "coordination.task_root": (contract.task_root.resolve(), scope.task_root.resolve()),
        "task_name": (slugify(contract.task_name), scope.task_root.name),
        "kind": (contract.kind, "series" if own else "leaf"),
    }
    differing = [name for name, (found, wanted) in recorded.items() if found != wanted]
    if not own:
        rows = {row.number for row in scope.master.document.subTasks}
        if contract.leaf_id not in rows:
            differing.append("coordination.leaf_id (no row of the master)")
        elif leaf_enclosure_path(scope.task_root, contract.leaf_id).resolve() != path.resolve():
            differing.append("coordination.leaf_id (another enclosure folder)")
    if differing:
        survey.facts.append(
            f"{who} ({survey.name(path)}) records {', '.join(differing)} differently from this "
            "master's own place; the contract was read as recorded"
        )


def _read_leaf_documents(survey: _Survey) -> None:
    """The documents of the master's rows, as bytes: a later change of one is noticed.

    They are not parsed and nothing is asked of them: a document that is gone, unreadable or
    outside the task folder is a fact. An abandoned row is asked for nothing at all.
    """

    scope = survey.scope
    for row in scope.master.document.subTasks:
        if not row.file or row.status == "abandoned":
            continue
        document = (scope.task_root / row.file).with_suffix(".json").resolve()
        if document.parent != scope.task_root.resolve():
            survey.facts.append(
                f"row {row.number!r} points outside the master's folder at {document}; the "
                "document was not read"
            )
            continue
        for source in (document, document.with_suffix(".md")):
            try:
                survey.evidence.append((source, source.read_bytes()))
            except FileNotFoundError:
                if source == document:
                    survey.facts.append(f"row {row.number!r}: its document {document} is gone")
            except OSError as error:
                survey.facts.append(
                    f"row {row.number!r}: its document {source} cannot be read ({error})"
                )
