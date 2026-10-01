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

**Record landing (rule 3).** It commits no memory: it checks that the landed memory commit passes
the validator (again through ``memory_commit_refusal``) against the parent line the task synced
from and, for a leaf, that the leaf's history file is ``closed`` in it. A memory commit made before the repository was converted carries no layout marker and is
exempt; the worktree layer decides that with its marker probe before it asks.
"""

from __future__ import annotations

import subprocess
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
from agents_remember.worktrees.services import LandingGateRequest

__all__ = ["STALE_AT_LANDING", "UNVERIFIABLE_AT_LANDING", "landing_refusal", "net_stale_entries"]

STALE_AT_LANDING = "knowledge-stale-at-landing"
UNVERIFIABLE_AT_LANDING = "knowledge-unverifiable-at-landing"
HISTORY_NOT_CLOSED = "knowledge-history-not-closed"


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
