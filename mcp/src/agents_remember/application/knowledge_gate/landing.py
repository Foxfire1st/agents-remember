"""The gate at the landing routes: master and checkpoint landing, and record landing (MIK-R09).

**Master routes (rule 4).** A master-to-parent landing and a checkpoint landing

* run the validator (MIK-R22) on the master's memory commit, against the parent line's memory tip
  (the worktree layer runs it through ``memory_commit_refusal``, the one route entry point);
* compute the MIK-R03 state, at the master's code commit, of every entry of the master's memory
  tree at a path the master's **net** code diff changed (the merge base of the parent line's code
  tip and the master's commit, to that commit). A ``stale`` entry refuses the landing; the remedy
  is a knowledge-maintenance leaf within the master. An ``unverifiable`` entry refuses it too,
  naming why (L09 review R1, finding 4): a Git read that failed or timed out, or a tree that cannot
  be read, blocks it as an unreadable input; any other reason (a locator kind that cannot be
  re-resolved) is named as unverifiable at landing. Nothing is let through unobserved.

**Master history files (MIK-R52 rule 5).** Every history file the landed memory commit holds and
no base holds closed must be closed, name an owner the master accounts for, and be byte-identical
to the same path in the owner's recorded publication. Owners are resolved in one function
(:func:`resolve_history_owners`): a leaf with a landed enclosure under the master, a direct
landing recorded in the series' operation journal, or a crossing sync whose merge the chain proof
admits. Each failing file is one finding with its reason.

**Record landing (rule 3).** It commits no memory: it checks that the landed memory commit passes
the validator (again through ``memory_commit_refusal``) against the parent line the task synced
from and, for a leaf, that the leaf's history file is ``closed`` in it. A memory commit made before the repository was converted carries no layout marker and is
exempt; the worktree layer decides that with its marker probe before it asks.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_currentness.observe import (
    CodeTree,
    EntryObservation,
    open_code_tree,
)
from agents_remember.application.knowledge_gate.gate import RUN_INCOMPLETE, GateFinding
from agents_remember.application.knowledge_gate.predicates import GateContext, GitReadFailed
from agents_remember.application.knowledge_worklist.knowledge import (
    KnowledgeSide,
    KnowledgeSideUnreadable,
)
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    read_git_blobs_bytes,
    run_git,
)
from agents_remember.memory.knowledge_index import MemoryTreeError, git_tree_snapshot
from agents_remember.models.knowledge_files.documents import (
    KNOWLEDGE_ROOT,
    history_path,
    owner_history_attempt,
)
from agents_remember.models.knowledge_files.history import is_closed_history
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    direct_landing_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationReadError,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.series_leaf_contracts import exact_atomic_leaf_contracts
from agents_remember.worktrees.services import LandingGateRequest
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

__all__ = [
    "STALE_AT_LANDING",
    "UNVERIFIABLE_AT_LANDING",
    "landing_refusal",
    "net_stale_entries",
    "resolve_history_owners",
]

STALE_AT_LANDING = "knowledge-stale-at-landing"
UNVERIFIABLE_AT_LANDING = "knowledge-unverifiable-at-landing"
HISTORY_NOT_CLOSED = "knowledge-history-not-closed"
HISTORY_UNOWNED = "knowledge-history-unowned"
HISTORY_NOT_IDENTICAL = "knowledge-history-not-identical"


def _git(repository: Path, *args: str) -> str | None:
    result = run_git(repository, list(args), GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS))
    return result.stdout if result.returncode == 0 else None


def landing_refusal(request: LandingGateRequest) -> str | None:
    """Why this landing is refused, or ``None``: every finding is named."""

    try:
        findings = _findings(request)
    except subprocess.SubprocessError as error:
        findings = [
            GateFinding(
                RUN_INCOMPLETE,
                "git",
                f"a Git call failed or timed out ({type(error).__name__}: {error}); the landing "
                "is blocked until the trees can be read",
            )
        ]
    if not findings:
        return None
    lines = [
        f"the mandatory invariant gate (MIK-R09) refuses this landing of memory commit "
        f"{request.memory_commit}: {len(findings)} finding(s)"
    ]
    lines += [
        f"- {finding.path or '-'}: [{finding.code}] {finding.message}" for finding in findings
    ]
    if any(finding.code in (STALE_AT_LANDING, UNVERIFIABLE_AT_LANDING) for finding in findings):
        lines.append(
            "The remedy for stale entries is a knowledge-maintenance leaf within this master "
            "(knowledgeMaintenanceScope: true) whose rows and re-anchors make them current."
        )
    return "\n".join(lines)


def _findings(request: LandingGateRequest) -> list[GateFinding]:
    findings: list[GateFinding] = []
    if request.leaf_owner:
        findings += _history_closed(request)
    if request.code_base:
        findings += net_stale_entries(request)
    if request.series_contract is not None and request.leaf_owner is None:
        try:
            findings += _master_history_ownership(request)
        except (RuntimeError, subprocess.SubprocessError) as error:
            findings.append(
                GateFinding(
                    RUN_INCOMPLETE,
                    "history-ownership",
                    f"the history owner check cannot be read ({type(error).__name__}: {error}); "
                    "the landing is blocked until the trees can be read",
                )
            )
    return findings


def _history_closed(request: LandingGateRequest) -> list[GateFinding]:
    """Rule 3: in the landed memory commit the leaf's history file exists and is ``closed``."""

    assert request.leaf_owner is not None
    path = _latest_history_path(request)
    if isinstance(path, GateFinding):
        return [path]
    listed = _git(
        request.memory_repository,
        "rev-parse",
        "--verify",
        "--quiet",
        f"{request.memory_commit}:{path}",
    )
    data = None
    if listed is not None and listed.strip():
        data = read_git_blobs_bytes(request.memory_repository, [listed.strip()]).get(listed.strip())
        if data is None:  # listed but unreadable: an unreadable input, never "absent"
            return [_unreadable(path, f"the history blob {listed.strip()} cannot be read")]
    if is_closed_history(data):
        return []
    state = "is absent" if data is None else "is not closed"
    return [
        GateFinding(
            HISTORY_NOT_CLOSED,
            path,
            f"the leaf's history file {state} in the landed memory commit; the closeout's memory "
            "commit closes it (MIK-R07 rule 7), so this commit is not a closed-out leaf's",
        )
    ]


def _latest_history_path(request: LandingGateRequest) -> str | GateFinding:
    """The leaf's latest history file in the landed commit (a reopened leaf's later attempt).

    A listing Git cannot give is an unreadable input, never "only the first attempt": reading the
    plain file then could pass a reopened leaf whose later attempt is still open.
    """

    assert request.leaf_owner is not None
    directory = f"{KNOWLEDGE_ROOT}/history/"
    listed = _git(
        request.memory_repository, "ls-tree", "--name-only", request.memory_commit, "--", directory
    )
    if listed is None:
        return _unreadable(
            directory, f"the landed memory commit's history files cannot be listed ({directory})"
        )
    attempts = {
        attempt: line
        for line in listed.splitlines()
        if (attempt := owner_history_attempt(line.strip(), request.leaf_owner))
    }
    return attempts[max(attempts)] if attempts else history_path(request.leaf_owner)


def net_stale_entries(request: LandingGateRequest) -> list[GateFinding]:
    """Rule 4: every entry at a path the master's net code diff changed is ``current`` at C."""

    net = _net_diff(request)
    if isinstance(net, GateFinding):
        return [net]
    changed, tree = net
    try:
        side = KnowledgeSide.from_snapshot(
            "K_C", git_tree_snapshot(request.memory_repository, request.memory_commit)
        )
    except (KnowledgeSideUnreadable, MemoryTreeError, OSError, ValueError) as error:
        return [_unreadable("K_C", f"{type(error).__name__}: {error}")]
    code = open_code_tree(CodeTree(request.code_repository, tree))
    if code.problem is not None:
        return [_unreadable("C", code.problem)]
    context = GateContext(owner=None, history=None, candidate=side, code=code)
    try:
        return [
            _not_current(request, path, entry_id, observed)
            for path in changed
            for entry_id in side.entries_by_path.get(path, ())
            if (observed := context.entry_state(entry_id)) is not None
            and observed.state != "current"
        ]
    except GitReadFailed as error:  # a failed or timed-out read blocks, as an unreadable input
        return [_unreadable("git", f"a Git read failed at the master's code commit: {error}")]


def _net_diff(request: LandingGateRequest) -> tuple[list[str], str] | GateFinding:
    """The paths the master's net diff changed, and its code tree; or why they cannot be read."""

    assert request.code_base is not None
    merge_base = _git(request.code_repository, "merge-base", request.code_base, request.code_commit)
    tree = _git(
        request.code_repository,
        "rev-parse",
        "--verify",
        "--quiet",
        f"{request.code_commit}^{{tree}}",
    )
    if merge_base is None or tree is None:
        return _unreadable(
            "C", f"the master's net code diff from {request.code_base} cannot be read"
        )
    changed = _git(
        request.code_repository,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        "--no-ext-diff",
        merge_base.strip(),
        request.code_commit,
        "--",
    )
    if changed is None:
        return _unreadable("C", "the master's net code diff cannot be listed")
    return sorted({one for one in changed.split("\0") if one}), tree.strip()


def _not_current(
    request: LandingGateRequest, path: str, entry_id: str, observed: EntryObservation
) -> GateFinding:
    code = STALE_AT_LANDING if observed.state == "stale" else UNVERIFIABLE_AT_LANDING
    return GateFinding(
        code,
        path,
        f"{entry_id} of {observed.invariant} is {observed.state} at the master's code commit "
        f"{request.code_commit} ({observed.reason}); the master's net diff changed its path and "
        "no row and re-anchor made it current",
    )


def _unreadable(name: str, detail: str) -> GateFinding:
    return GateFinding(RUN_INCOMPLETE, name, f"{detail}; an unreadable tree blocks the landing")


def _commit_blob_ids(repository: Path, commit: str, directory: str) -> dict[str, str] | None:
    """Map every blob path under ``directory`` at ``commit`` to its blob id; ``None`` if unreadable."""

    listed = _git(repository, "ls-tree", "-r", commit, "--", directory)
    if listed is None:
        return None
    ids: dict[str, str] = {}
    for line in listed.splitlines():
        head, tab, path = line.partition("\t")
        parts = head.split()
        if tab and len(parts) == 3 and parts[1] == "blob":
            ids[path] = parts[2]
    return ids


def _listed_history_blobs(
    request: LandingGateRequest,
) -> tuple[dict[str, str], list[dict[str, str]], dict[str, bytes]] | GateFinding:
    """List the landed and base history blobs and read them in one batch.

    Returns ``(landed_ids, bases, blobs)`` or the finding when a side cannot be listed.
    """

    landed_ids = _commit_blob_ids(
        request.memory_repository, request.memory_commit, f"{KNOWLEDGE_ROOT}/history/"
    )
    if landed_ids is None:
        return _unreadable(
            f"{KNOWLEDGE_ROOT}/history/",
            "the landed memory commit's history files cannot be listed",
        )
    bases = [
        _commit_blob_ids(request.memory_repository, base, f"{KNOWLEDGE_ROOT}/history/")
        for base in request.memory_bases
    ]
    if any(ids is None for ids in bases):
        return _unreadable(
            f"{KNOWLEDGE_ROOT}/history/",
            "a comparison base's history files cannot be listed",
        )
    wanted = {path for path in landed_ids if path.endswith(".json")}
    blob_ids = [landed_ids[path] for path in wanted]
    for base in bases:
        assert base is not None
        blob_ids += [blob for path, blob in base.items() if path in wanted]
    try:
        blobs = read_git_blobs_bytes(request.memory_repository, blob_ids)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        return _unreadable(
            f"{KNOWLEDGE_ROOT}/history/",
            f"history blobs cannot be read ({type(error).__name__}: {error})",
        )
    return landed_ids, [base for base in bases if base is not None], blobs


def _history_owner_id(path: str) -> str | None:
    """The history owner a ``knowledge/history/`` file names, or ``None`` outside the shape."""

    match = _HISTORY_OWNER_PATTERN.match(path)
    return match[1] if match else None


def resolve_history_owners(
    request: LandingGateRequest, series: WorktreeContract, paths: list[str]
) -> dict[str, list[_HistoryOwner]] | GateFinding:
    """Resolve, in one function, every owner a master-level landing may hold history files from.

    Owners are a leaf with a landed enclosure under the master (its recorded closeout memory
    commit), a direct landing recorded in the series' operation journal (its recorded memory
    commit), or a crossing sync whose merge the chain proof admits (a recorded sync head). The map
    holds, per newly closed history path, the candidate owners in that precedence order: a leaf
    claims the files naming it, while a direct landing or crossing head claims the landed files
    it holds on the landed line. An unreadable enclosure contract or operation journal refuses as
    the returned finding, naming the file.
    """

    try:
        # Owner resolution needs only landed enclosures; rows with no enclosure published
        # nothing and own nothing (MIK-R52 rule 4).
        enclosures = exact_atomic_leaf_contracts(series, require_all=False)
    except (CloseoutQueueError, ContractError, TaskDocumentRefError, OSError, ValueError) as error:
        return GateFinding(
            RUN_INCOMPLETE,
            series.contract_path.as_posix(),
            f"the master's leaf enclosures cannot be read ({type(error).__name__}: {error}); "
            "the landing is blocked until every enclosure is accounted for",
        )
    leaf_commits: dict[str, str] = {}
    for leaf in enclosures.values():
        if leaf.integration_status != "completed":
            continue
        publication = leaf.memory_content_commit or leaf.integrated_memory_content_commit
        if leaf.leaf_id and publication:
            leaf_commits[leaf.leaf_id] = publication
    direct = _direct_landing_commits(series)
    if isinstance(direct, GateFinding):
        return direct
    heads = _recorded_crossing_heads(series, enclosures)
    return _claim_history_paths(request, paths, leaf_commits, direct, heads)


def _claim_history_paths(
    request: LandingGateRequest,
    paths: list[str],
    leaf_commits: dict[str, str],
    direct: list[str],
    heads: list[str],
) -> dict[str, list[_HistoryOwner]]:
    """Claim every newly closed history path for its leaf, direct landing or crossing head owners."""

    owners: dict[str, list[_HistoryOwner]] = {}
    for path in paths:
        candidates = owners.setdefault(path, [])
        owner_id = _history_owner_id(path)
        if owner_id is not None and owner_id in leaf_commits:
            candidates.append(_HistoryOwner("leaf", f"leaf {owner_id!r}", leaf_commits[owner_id]))
        for commit in direct:
            if _commit_holds_path(request, commit, path):
                candidates.append(
                    _HistoryOwner("direct", f"the direct landing at {commit}", commit)
                )
        for commit in heads:
            if _commit_holds_path(request, commit, path):
                candidates.append(
                    _HistoryOwner("crossing", f"the crossing sync at {commit}", commit)
                )
    return owners


def _commit_holds_path(request: LandingGateRequest, commit: str, path: str) -> bool:
    """Whether ``commit`` sits on the landed line and holds ``path``.

    A publication off the landed line owns nothing here: the same bytes from another line would
    admit a file that never landed on this one. A legitimate non-ancestor therefore answers
    False and keeps that policy. A commit Git cannot read refuses instead: a failed read never
    passes as absence (MIK-R52 F01).
    """

    repository = request.memory_repository
    ancestry = run_git(
        repository,
        ["merge-base", "--is-ancestor", commit, request.memory_commit],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if ancestry.returncode == 1:
        return False
    if ancestry.returncode != 0:
        detail = (ancestry.stderr or "").strip()
        raise RuntimeError(
            f"the history owner check cannot read the ancestry of {commit} to the landed "
            f"memory commit {request.memory_commit} (git merge-base rc {ancestry.returncode}"
            + (f": {detail}" if detail else "")
            + "); the landing is not judged"
        )
    read = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", f"{commit}:{path}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if read.returncode == 1:
        # ``rev-parse --verify --quiet`` answers 1 only for a path the readable commit does
        # not hold: genuinely absent, which owns nothing here.
        return False
    if read.returncode != 0:
        detail = (read.stderr or "").strip()
        raise RuntimeError(
            f"the history owner check cannot read {path} at {commit} "
            f"(git rev-parse rc {read.returncode}"
            + (f": {detail}" if detail else "")
            + "); the landing is not judged"
        )
    return True


def _direct_landing_commits(series: WorktreeContract) -> list[str] | GateFinding:
    """The recorded memory commits of the series' direct landings, oldest first.

    The series' operation journal is the current direct-landing record plus every archived
    generation beside it. A journal that cannot be read refuses as the returned finding, naming
    the file.
    """

    try:
        store = direct_landing_store(series)
        records: list[dict[str, object]] = []
        current = store.read()
        if current is not None:
            records.append(current.model_dump(mode="json"))
        archives = sorted(
            store.path.parent.glob(f"{store.path.stem}.generation-*.json"),
            key=_generation_number,
        )
    except (LifecycleOperationReadError, OSError, RuntimeError, ValueError) as error:
        return GateFinding(
            RUN_INCOMPLETE,
            series.contract_path.as_posix(),
            f"the series' direct-landing journal cannot be read ({type(error).__name__}: "
            f"{error}); the landing is blocked until its direct landings are accounted for",
        )
    commits: list[str] = []
    for archive in archives:
        try:
            payload = json.loads(archive.read_text(encoding="utf-8"))
            record = LifecycleOperationRecord.model_validate(payload)
        except (OSError, ValueError) as error:
            return GateFinding(
                RUN_INCOMPLETE,
                archive.as_posix(),
                f"the series' direct-landing journal entry cannot be read "
                f"({type(error).__name__}: {error}); the landing is blocked until its direct "
                "landings are accounted for",
            )
        records.append(record.model_dump(mode="json"))
    for record in records:
        result = record.get("result")
        commit = result.get("memoryContentCommit") if isinstance(result, dict) else None
        if isinstance(commit, str) and commit and commit not in commits:
            commits.append(commit)
    return commits


def _recorded_crossing_heads(
    series: WorktreeContract, enclosures: dict[str, WorktreeContract]
) -> list[str]:
    """The recorded memory heads of the chain's own syncs (MIK-R52 rule 4).

    A crossing sync whose merge the chain proof admits owns the history files its merge closed;
    the bytes are compared with that merge commit. Entries written before this leaf record no
    head and contribute none.
    """

    heads: list[str] = []
    contracts = [series, *enclosures.values()]
    for contract in contracts:
        for entry in contract.sync_log:
            head = entry.get("memoryHeadTo", "")
            if head and head not in heads:
                heads.append(head)
    return heads


def _owned_history_finding(
    request: LandingGateRequest,
    owners: dict[str, list[_HistoryOwner]],
    path: str,
    data: bytes | None,
) -> list[GateFinding]:
    """The one finding for a newly closed history file: closed, owned and identical, or why not."""

    if not is_closed_history(data):
        state = "is absent" if data is None else "is not closed"
        return [
            GateFinding(
                HISTORY_NOT_CLOSED,
                path,
                f"the history file {state} in the landed memory commit; every history file a "
                "master-level landing newly closes is closed by its owner's publication",
            )
        ]
    candidates = owners.get(path, [])
    if not candidates:
        return [
            GateFinding(
                HISTORY_UNOWNED,
                path,
                "the history file names no owner the master accounts for: a leaf with a landed "
                "enclosure under this master, a direct landing recorded in the series' operation "
                "journal, or a crossing sync whose merge the chain proof admits; a hand-made "
                "memory commit, a fabricated or edited closed history file and a commit outside "
                "a leaf do not pass a landing",
            )
        ]
    unreadable: list[str] = []
    tried: list[str] = []
    for owner in candidates:
        blob = _git(
            request.memory_repository,
            "rev-parse",
            "--verify",
            "--quiet",
            f"{owner.commit}:{path}",
        )
        if blob is None or not blob.strip():
            # The owner's publication holds no such path: tried, not unreadable.
            tried.append(f"{owner.name} at {owner.commit} (no such path)")
            continue
        try:
            published = read_git_blobs_bytes(request.memory_repository, [blob.strip()]).get(
                blob.strip()
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            published = None
        if published is None:
            unreadable.append(f"{owner.name} at {owner.commit}")
        elif published == data:
            return []
        else:
            tried.append(f"{owner.name} at {owner.commit}")
    if unreadable and not tried:
        return [
            GateFinding(
                RUN_INCOMPLETE,
                path,
                f"the owner's recorded publication cannot be read ({'; '.join(unreadable)}); "
                "an unreadable input blocks the landing",
            )
        ]
    return [
        GateFinding(
            HISTORY_NOT_IDENTICAL,
            path,
            "the history file differs from the same path in every recorded publication that "
            f"owns it ({'; '.join(tried + unreadable)}); a fabricated or edited closed history "
            "file does not pass a landing",
        )
    ]


_HISTORY_OWNER_PATTERN = re.compile(r"^knowledge/history/(.+?)(?:-attempt-\d+)?\.json$")
_GENERATION_PATTERN = re.compile(r"\.generation-(\d+)\.json$")


def _generation_number(path: Path) -> tuple[int, str]:
    """Sort a generation archive oldest first (numeric generation, then name)."""

    match = _GENERATION_PATTERN.search(path.name)
    return (int(match[1]), path.name) if match else (0, path.name)


@dataclass(frozen=True)
class _HistoryOwner:
    """One recorded publication a master-level landing may hold a history file from (rule 5)."""

    kind: str  # "leaf" | "direct" | "crossing"
    name: str  # how the refusal names the owner
    commit: str  # the recorded publication commit the bytes are compared with


def _master_history_ownership(request: LandingGateRequest) -> list[GateFinding]:
    """MIK-R52 rule 5: every newly closed history file is closed, owned and identical.

    Every history file that the landed memory commit holds and no base holds closed must be
    closed, name an owner the master accounts for, and be byte-identical to the same path in the
    owner's recorded publication. Each failing file is one finding with its reason.
    """

    assert request.series_contract is not None
    listed = _listed_history_blobs(request)
    if isinstance(listed, GateFinding):
        return [listed]
    landed_ids, bases, blobs = listed
    # Only newly closed files need an owner: a base that already holds a file closed freezes it.
    # Masters with no new history files never read the contract or the journal here.
    newly_closed = [
        path
        for path in sorted(landed_ids)
        if path.endswith(".json")
        and not any(path in base and is_closed_history(blobs.get(base[path])) for base in bases)
    ]
    if not newly_closed:
        return []
    try:
        series = load_contract(Path(request.series_contract))
    except (ContractError, OSError, ValueError) as error:
        return [
            GateFinding(
                RUN_INCOMPLETE,
                request.series_contract,
                f"the series contract cannot be read ({type(error).__name__}: {error}); the "
                "landing is blocked until its enclosures can be accounted for",
            )
        ]
    owners = resolve_history_owners(request, series, newly_closed)
    if isinstance(owners, GateFinding):
        return [owners]
    findings: list[GateFinding] = []
    for path in newly_closed:
        findings += _owned_history_finding(request, owners, path, blobs.get(landed_ids[path]))
    return findings
