"""Which exact Git objects a master NET change-set binds, and what to say when one is absent.

A master review exposes one exact, generation-bound net comparison between its declared
source/knowledge base and its selected result (``ICR-R13@v1``). The net-diff computation itself
stays where it is -- :mod:`agents_remember.serving.changeset` owns the ``base -> tip`` diff -- and
this module owns the *selection* that computation runs over: the declared integrated result for a
live request, or the exact recorded endpoints a pinned request names.

The three states a request can be in:

* **Live (no pins).** The selection is the declared integrated result: the series contract's
  recorded base and the live head of its integration branch, for code and for memory. This is the
  active master net scope, and the response labels it ``integrated`` so an in-flight preview -- a
  leaf worktree delta, a work-branch that is not the integration branch -- can never be mistaken
  for it. Leaf liveness is reported per leaf beside the net, never mixed into it.
* **Pinned.** The caller names the generation a listing published (``ICR-R03@v1``'s listing-pinned
  expansion idiom, applied to the master range): base and tip commits for the side it reads. Both
  must resolve in their repository, or the read is refused by name. A completed master's URL keeps
  working this way after its source branch advances: the recorded endpoints still resolve, the net
  is unchanged, and the response reports ``superseded`` currentness against the moved live tip.
* **Absent.** A missing endpoint is never substituted with a later branch tip: no source-branch
  fallback, no worktree ``HEAD``, no current knowledge. The code side -- the side the view *is* --
  raises :class:`MasterEndpointAbsent`; the memory side degrades to nothing to show, the same
  degradation the leaf views publish for a leg the task does not run.

The digest is deterministic content addressing over the four endpoint commits (no wall clock, no
branch names), so two selections of the same generation share one id and a reseal is comparable.
Currentness compares the selection against the live integrated tips measured in the same call.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.changeset_endpoints import (
    NOT_RECORDED,
    RecordedEndpointAbsenceKind,
    RecordedEndpointAbsent,
)
from agents_remember.worktrees.modules.git import branch_exists, head_commit
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

__all__ = [
    "MASTER_NET_SCOPE",
    "MasterEndpointAbsent",
    "MasterNetEndpoints",
    "MasterNetPins",
    "MasterNetSelection",
    "integrated_tip",
    "load_master_contract",
    "master_net_digest",
    "master_task_root",
    "select_master_net",
]

# The one scope this selection ever serves. The active master net is the declared integrated
# result; any in-flight preview travels under its own label elsewhere, never silently inside it.
MASTER_NET_SCOPE: Literal["integrated"] = "integrated"

# What the selection reports about itself against the live integrated line.
MasterNetCurrentness = Literal["current", "superseded", "unmeasured"]


class MasterEndpointAbsent(RecordedEndpointAbsent):
    """One master net endpoint is missing, so the net range has no second object.

    It subclasses the committed-range refusal because it *is* the same vocabulary -- the same
    404 idiom on the routes, the same three kinds a caller acts on -- applied to the master's
    declared base and selected result rather than to one leaf's landed range. Only
    :data:`~agents_remember.serving.changeset_endpoints.NOT_RECORDED` (nothing has produced an
    integrated result yet) may leave a half with nothing to show, and only on the memory side;
    ``no-repository`` and ``unresolvable`` are broken state no view may report as an empty range.
    """


@dataclass(frozen=True)
class MasterNetPins:
    """An explicitly selected master generation: exact commits, never branch names.

    Empty means unpinned: the declared integrated result (the series contract's recorded base and
    the live integration-branch head) is selected instead. A non-empty pin must resolve in its
    repository, or the read is refused rather than re-resolved to a tip the caller did not name.
    """

    code_base: str = ""
    code_tip: str = ""
    memory_base: str = ""
    memory_tip: str = ""


@dataclass(frozen=True)
class MasterNetEndpoints:
    """The exact ``base -> tip`` commits one master net comparison is computed over."""

    code_base: str
    code_tip: str
    memory_base: str = ""
    memory_tip: str = ""


@dataclass(frozen=True)
class MasterNetSelection:
    """One resolved master net selection: its endpoints, identity, scope and currentness."""

    endpoints: MasterNetEndpoints
    digest: str
    scope: Literal["integrated"] = MASTER_NET_SCOPE
    currentness: MasterNetCurrentness = "current"


def master_task_root(config: McpRuntimeConfig, repo_id: str, master: str) -> Path | None:
    """Return the exact requested master task root when ``master`` is one safe path segment."""

    if not master or "/" in master or "\\" in master or master.startswith("."):
        return None
    return config.coordination_root / "tasks" / repo_id / master


def load_master_contract(
    config: McpRuntimeConfig, repo_id: str, master: str
) -> WorktreeContract | None:
    """The series (root) contract at ``tasks/<repo>/<master>/series-contract.md``, or None."""

    task_root = master_task_root(config, repo_id, master)
    if task_root is None:
        return None
    try:
        return load_contract(task_root / "series-contract.md")
    except (ContractError, OSError):
        return None


def integrated_tip(repository: Path | None, branch: str) -> str | None:
    """The live head of the integration branch, or None when it names no integrated result.

    A configured branch that does not exist is *missing*, not empty: there is deliberately no
    fallback to the source branch, whose later tip is other work and must never be labelled this
    master's net.
    """

    if repository is None or not branch or not branch_exists(repository, branch):
        return None
    try:
        return head_commit(repository, branch)
    except (RuntimeError, OSError):
        return None


def master_net_digest(endpoints: MasterNetEndpoints) -> str:
    """The deterministic identity of one endpoint selection, shared by every identical one."""

    return sha256_digest(
        {
            "code_base": endpoints.code_base,
            "code_tip": endpoints.code_tip,
            "memory_base": endpoints.memory_base,
            "memory_tip": endpoints.memory_tip,
        }
    )


def select_master_net(
    config: McpRuntimeConfig, repo_id: str, master: str, pins: MasterNetPins | None = None
) -> MasterNetSelection | None:
    """Resolve which exact commits one master net comparison is computed over.

    Returns None when no series contract names this master -- the caller keeps degrading an
    unresolvable master to empty lists rather than refusing it. Raises
    :class:`MasterEndpointAbsent` when the contract exists but the code side has no resolvable
    base or result, naming the missing endpoint instead of substituting a later tip. The memory
    side degrades to no endpoints when its leg is absent or unpinned-and-unresolvable; explicit
    memory pins that do not resolve are refused like code pins.
    """

    contract = load_master_contract(config, repo_id, master)
    if contract is None:
        return None
    wanted = pins or MasterNetPins()
    code_base, code_tip = _code_endpoints(contract, master, wanted)
    memory = _memory_endpoints(contract, master, wanted)
    endpoints = MasterNetEndpoints(
        code_base=code_base,
        code_tip=code_tip,
        memory_base=memory[0] if memory is not None else "",
        memory_tip=memory[1] if memory is not None else "",
    )
    return MasterNetSelection(
        endpoints=endpoints,
        digest=master_net_digest(endpoints),
        currentness=_currentness(contract, endpoints),
    )


def _code_endpoints(
    contract: WorktreeContract, master: str, wanted: MasterNetPins
) -> tuple[str, str]:
    """The code ``(base, tip)`` commits: recorded or pinned, both required to resolve."""

    base = wanted.code_base or contract.code_base_commit
    if not base:
        raise MasterEndpointAbsent(
            f"master {master!r} records no code base commit, so its net code range does not "
            "exist yet: the net comparison reads the declared base and selected result and "
            "substitutes no branch tip for either",
            kind=NOT_RECORDED,
        )
    tip = wanted.code_tip or integrated_tip(contract.code_repo_path, contract.code_work_branch)
    if not tip:
        raise MasterEndpointAbsent(
            f"master {master!r} has no integrated code result yet: branch "
            f"{contract.code_work_branch!r} does not resolve to a commit, so the net code "
            "range does not exist yet and the source branch's later tip is not substituted "
            "for it",
            kind=NOT_RECORDED,
        )
    _require_resolves(contract.code_repo_path, base, tip, master, "code")
    return base, tip


def _memory_endpoints(
    contract: WorktreeContract, master: str, wanted: MasterNetPins
) -> tuple[str, str] | None:
    """The memory ``(base, tip)`` commits, or None when this master shows no memory half.

    An absent leg degrades -- the same nothing-to-show the leaf views publish for a task that
    does not run memory -- while an explicit pin that does not resolve is refused, because the
    caller asked for exact history and an empty range would claim a measurement never made.
    """

    if contract.memory_repo_path is None:
        return None
    base = wanted.memory_base or contract.memory_base_commit
    tip = wanted.memory_tip or integrated_tip(
        contract.memory_repo_path, contract.memory_work_branch
    )
    if not base or not tip:
        if wanted.memory_base or wanted.memory_tip:
            raise MasterEndpointAbsent(
                f"master {master!r} names a pinned memory result whose other endpoint "
                "nothing recorded, so the pinned net memory range does not exist yet and "
                "no branch or working tree is substituted for it",
                kind=NOT_RECORDED,
            )
        return None
    _require_resolves(contract.memory_repo_path, base, tip, master, "memory")
    return base, tip


def _require_resolves(repository: Path | None, base: str, tip: str, master: str, side: str) -> None:
    """Refuse an endpoint pair whose recorded commits this repository cannot resolve."""

    for commit in (base, tip):
        if repository is not None and _resolves(repository, commit):
            continue
        kind: RecordedEndpointAbsenceKind = (
            "no-repository" if repository is None else "unresolvable"
        )
        raise MasterEndpointAbsent(
            f"the master {master!r} net {side} range names {commit}, which the {side} "
            "repository does not hold, so the net comparison cannot be read from its "
            "endpoints and no branch or working tree is substituted for them",
            kind=kind,
        )


def _resolves(repository: Path, commit: str) -> bool:
    """Whether ``commit`` resolves to a commit object in ``repository``."""

    return run_git(repository, ["cat-file", "-e", f"{commit}^{{commit}}"]).returncode == 0


def _currentness(contract: WorktreeContract, endpoints: MasterNetEndpoints) -> MasterNetCurrentness:
    """Whether the selection still is the live integrated result, measured in the same call."""

    live_code = integrated_tip(contract.code_repo_path, contract.code_work_branch)
    if live_code is None:
        return "unmeasured"
    if endpoints.code_tip != live_code:
        return "superseded"
    if endpoints.memory_tip:
        live_memory = integrated_tip(contract.memory_repo_path, contract.memory_work_branch)
        if live_memory is None:
            return "unmeasured"
        if endpoints.memory_tip != live_memory:
            return "superseded"
    return "current"
