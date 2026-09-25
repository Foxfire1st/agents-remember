"""Read-only change-set API: a task's (and the master's accumulated) code + memory diff.

L3 of the operations-integration series. Mirrors the L1 files API pattern
(``serving/files.py``): GET-only, read-only, 127.0.0.1-bound, reusing
``serving/scope.py`` for scope resolution + the 404/400 error map and
``kernel/sidecar_pairing`` for sidecar pairing on the changed code set.

The change-set is per **enclosure**: it loads the leaf contract for the base/verified
commits and diffs the task's full ``base -> current`` range -- ``base_commit -> the
worktree`` for an active enclosure, ``base_commit -> code_commit`` for a completed leaf
whose worktree is gone (the commits live on the source repo after integration).
``file-diff`` emits BEFORE + AFTER content (not unified-diff text) so the L4 pane feeds
CodeMirror MergeView ``a``/``b`` directly. ``master`` is the NET ``base -> selected-result``
diff between the master's declared endpoints (one coherent range, inspectable per file),
bound to a generation with a deterministic digest and ``current``/``superseded`` currentness --
so a recorded result keeps resolving after the source branch advances -- with a per-leaf
counter breakdown alongside. Generation pins on the master routes freeze the result to the
listed generation; a missing endpoint is refused by name, never substituted with a later tip.
Mainline has no base, so a mainline scope is a 404.

L4a adds the doc-reader views: a ``leaf`` change-set resolved by leaf-id from the persisted
enclosure contract (so it works with no live worktree, for a completed leaf) in one of two
modes -- ``committed`` (the contract's two **recorded** commits: ``base_commit`` -> the
landed commit its closeout or integration wrote) or ``working`` (``worktree-HEAD ->
worktree``, the UNCOMMITTED delta only, live worktree required). These ride
the same ``/api/changeset/{task,file-diff}`` routes via a ``leaf`` + ``mode`` selector, returning
the ``task_changeset`` shape. Selection precedence is ``leaf > master > scope``. The two modes are
never mixed: a committed view whose recorded endpoint is not written yet is refused by name (404)
rather than answered from the worktree's moveable ``HEAD``, and the working view keeps publishing
exactly the uncommitted delta its name claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse, Response

from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.kernel.sidecar_pairing import confine_rel, route_sidecar_status
from agents_remember.serving.changeset_endpoints import (
    NOT_RECORDED,
    RecordedEndpointAbsent,
    recorded_committed_range,
)
from agents_remember.serving.master_net_generation import (
    MASTER_NET_SCOPE,
    MasterEndpointAbsent,
    MasterNetPins,
    load_master_contract,
    master_task_root,
    select_master_net,
)
from agents_remember.serving.response_contract import (
    SCOPED_READ_RESPONSES,
    FileDiff,
    LeafChangeSet,
    MasterChangeSet,
    TaskChangeSet,
)
from agents_remember.serving.scope import FileScope, language_for, run_scoped
from agents_remember.worktrees.modules.git import (
    changed_files_with_counts,
    commit_text_or_none,
    head_commit,
)
from agents_remember.worktrees.task_resolver import slugify
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)


def _require_contract(scope: FileScope) -> WorktreeContract:
    """Load the leaf contract for an enclosure scope; mainline / unreadable -> 404 not-found."""
    if scope.kind != "worktree" or scope.contract_path is None:
        raise FileNotFoundError(f"no change-set for scope {scope.scope_id!r}")
    try:
        return load_contract(scope.contract_path)
    except (ContractError, OSError) as err:
        raise FileNotFoundError(str(scope.contract_path)) from err


def _sum(files: list[dict[str, Any]]) -> dict[str, int]:
    """Counters for one changed set: file count + summed insertions/deletions (binary -> 0)."""
    return {
        "files": len(files),
        "insertions": sum(int(f["insertions"] or 0) for f in files),
        "deletions": sum(int(f["deletions"] or 0) for f in files),
    }


def task_changeset(scope: FileScope) -> dict[str, Any]:
    """The change-set for one active enclosure: base -> worktree code + memory, with counts."""
    contract = _require_contract(scope)
    code = changed_files_with_counts(scope.code_root, contract.code_base_commit, None)
    for entry in code:
        entry["hasSidecar"] = (
            scope.onboarding_root is not None
            and route_sidecar_status(scope.onboarding_root, str(entry["path"])) == "present"
        )
    memory: list[dict[str, Any]] = []
    if contract.memory_worktree is not None and contract.memory_base_commit:
        memory = changed_files_with_counts(
            contract.memory_worktree, contract.memory_base_commit, None
        )
    return {
        "scope": scope.scope_id,
        "code": code,
        "memory": memory,
        "counters": {"code": _sum(code), "memory": _sum(memory)},
    }


def file_diff(scope: FileScope, kind: str, rel: str) -> dict[str, Any]:
    """BEFORE (base commit) + AFTER (current) content + language for one changed file.

    ``kind`` selects the tree: ``"memory"`` diffs the memory worktree, anything else the
    code worktree. ``before`` is ``None`` for an added file, ``after`` is ``None`` for a
    deleted one -- the L4 MergeView renders those as a pure add/delete.
    """
    contract = _require_contract(scope)
    if kind == "memory":
        root, base = contract.memory_worktree, contract.memory_base_commit
    else:
        root, base = scope.code_root, contract.code_base_commit
    if root is None:
        raise FileNotFoundError(rel)
    relp = confine_rel(root, rel)
    before = commit_text_or_none(root, base, relp) if base else None
    after_path = root / relp
    after = after_path.read_text(errors="replace") if after_path.is_file() else None
    return {
        "scope": scope.scope_id,
        "kind": "memory" if kind == "memory" else "code",
        "path": relp,
        "language": language_for(Path(relp)),
        "before": {"content": before} if before is not None else None,
        "after": {"content": after} if after is not None else None,
    }


def _leaf_counts(contract: WorktreeContract, *, memory: bool) -> list[dict[str, Any]]:
    """One leaf's change-set: base -> worktree when live, else base -> the integrated commit."""
    if memory:
        live, repo = contract.memory_worktree, contract.memory_repo_path
        base, head = contract.memory_base_commit, contract.memory_content_commit
    else:
        live, repo = contract.code_worktree, contract.code_repo_path
        base, head = contract.code_base_commit, contract.code_commit
    if not base:
        return []
    if live is not None and live.exists():
        return changed_files_with_counts(live, base, None)
    if repo is not None and head:
        return changed_files_with_counts(repo, base, head)
    return []


def _master_enclosure_contracts(config: McpRuntimeConfig, repo_id: str, master: str) -> list[Path]:
    """Leaf contracts directly under ``tasks/<repo>/<master>/enclosures`` only."""
    task_root = master_task_root(config, repo_id, master)
    if task_root is None:
        return []
    return sorted((task_root / "enclosures").glob("*/series-contract.md"))


def _leaf_state(contract: WorktreeContract) -> str:
    """Whether a leaf breakdown row shows live uncommitted work or its landed delta.

    A leaf whose code worktree is still live reports ``working`` -- its counters move with the
    worktree -- while a cleaned or never-live leaf reports ``committed``. The label travels on
    the breakdown row so an in-flight preview is never mixed into the net silently.
    """

    if contract.code_worktree is not None and contract.code_worktree.exists():
        return "working"
    return "committed"


def _master_leaf_summaries(
    config: McpRuntimeConfig, repo_id: str, master: str
) -> list[dict[str, Any]]:
    """Per-leaf counter breakdown (each leaf vs its own base) shown alongside the net diff."""
    leaves: list[dict[str, Any]] = []
    for path in _master_enclosure_contracts(config, repo_id, master):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        if contract.repo_name != repo_id or contract.cleanup == "abandoned":
            continue
        if master not in (contract.parent_task_name, contract.task_name):
            continue
        try:
            code = _leaf_counts(contract, memory=False)
            memory = _leaf_counts(contract, memory=True)
        except (RuntimeError, OSError):
            continue  # a leaf whose commits/worktree are unreadable never aborts the breakdown
        leaves.append(
            {
                "leafId": contract.leaf_id,
                "state": _leaf_state(contract),
                "counters": {"code": _sum(code), "memory": _sum(memory)},
            }
        )
    return leaves


def _net_diff(
    repo_path: Path | None, base: str, tip: str, *, side: str, master: str
) -> list[dict[str, Any]]:
    """One side's net change-set between two commits this selection already validated.

    An empty endpoint pair is a degraded leg -- nothing selected, nothing to show -- and keeps
    degrading to ``[]``. A diff that fails *after* validation is refused by name instead: the
    endpoints resolved, so an empty net would publish a measurement never made, and an exact
    zero is exactly what this view must never invent.
    """

    if not base or not tip:
        return []
    if repo_path is None:
        raise MasterEndpointAbsent(
            f"the contract names no {side} repository for master {master!r}, so this side has "
            "no net range to read; the net comparison publishes nothing for a side the task "
            "does not run",
            kind="no-repository",
        )
    try:
        return changed_files_with_counts(repo_path, base, tip)
    except (RuntimeError, OSError) as err:
        raise MasterEndpointAbsent(
            f"the master {master!r} net {side} range {base}..{tip} was validated but cannot "
            f"be read ({err}); the net is refused rather than reported as an empty range",
            kind="unresolvable",
        ) from err


def master_changeset(
    config: McpRuntimeConfig,
    repo_id: str,
    master: str,
    *,
    include_leaves: bool = True,
    pins: MasterNetPins | None = None,
) -> dict[str, Any]:
    """The series NET change-set between the master's declared endpoints, bound to a generation.

    Unlike a sum of the leaf change-sets (which double-counts a file two leaves touched and has
    no single base to diff against), the net range from the master's recorded base to the
    selected result is one coherent diff -- so every changed file is inspectable via
    :func:`master_file_diff`. The selection (see
    :mod:`agents_remember.serving.master_net_generation`) is the declared integrated result for
    a live request, or the exact recorded endpoints a pinned request names; it is published as
    ``generation`` with a deterministic digest and ``current``/``superseded`` currentness, so a
    completed master's recorded result keeps resolving after its source branch advances.
    ``leaves`` keeps the optional per-leaf counter breakdown alongside it, each row labelled
    ``committed`` or ``working``. Callers that render only the net range can skip those extra
    per-leaf git diffs. An in-flight leaf not yet integrated is excluded from the net.
    """
    selection = select_master_net(config, repo_id, master, pins or MasterNetPins())
    if selection is None:
        return {
            "master": master,
            "leaves": [],
            "code": [],
            "memory": [],
            "counters": {"code": _sum([]), "memory": _sum([])},
            "generation": None,
            "currentness": "unmeasured",
            "scope": MASTER_NET_SCOPE,
        }
    contract = load_master_contract(config, repo_id, master)
    onboarding_root: Path | None = None
    if contract is not None and contract.memory_repo_path is not None:
        candidate = contract.memory_repo_path / "onboarding"
        if candidate.is_dir():
            onboarding_root = candidate
    endpoints = selection.endpoints
    code = _net_diff(
        contract.code_repo_path if contract is not None else None,
        endpoints.code_base,
        endpoints.code_tip,
        side="code",
        master=master,
    )
    memory = _net_diff(
        contract.memory_repo_path if contract is not None else None,
        endpoints.memory_base,
        endpoints.memory_tip,
        side="memory",
        master=master,
    )
    for entry in code:
        entry["hasSidecar"] = (
            onboarding_root is not None
            and route_sidecar_status(onboarding_root, str(entry["path"])) == "present"
        )
    return {
        "master": master,
        "leaves": _master_leaf_summaries(config, repo_id, master) if include_leaves else [],
        "code": code,
        "memory": memory,
        "counters": {"code": _sum(code), "memory": _sum(memory)},
        "generation": {
            "codeBase": endpoints.code_base,
            "codeTip": endpoints.code_tip,
            "memoryBase": endpoints.memory_base,
            "memoryTip": endpoints.memory_tip,
            "digest": selection.digest,
        },
        "currentness": selection.currentness,
        "scope": selection.scope,
    }


def master_file_diff(config: McpRuntimeConfig, ref: MasterFileRef) -> dict[str, Any]:
    """BEFORE (master base) + AFTER (selected result) content for one file in the net diff.

    Without pins the AFTER side is the live integrated result; with pins it is the exact
    recorded tip the listing published (``ICR-R03@v1``'s listing-pinned expansion idiom), so an
    opened entry stays bound to its generation after the branch advances. A pinned endpoint the
    repository does not hold is refused by name, never re-resolved to the current tip.
    """
    selection = select_master_net(config, ref.repo, ref.master, ref.pins)
    contract = load_master_contract(config, ref.repo, ref.master)
    if selection is None or contract is None:
        raise FileNotFoundError(f"no series contract for {ref.master!r}")
    endpoints = selection.endpoints
    if ref.kind == "memory":
        repo_path = contract.memory_repo_path
        base, tip = endpoints.memory_base, endpoints.memory_tip
    else:
        repo_path = contract.code_repo_path
        base, tip = endpoints.code_base, endpoints.code_tip
    if repo_path is None or not base or not tip:
        raise FileNotFoundError(ref.path)
    relp = confine_rel(repo_path, ref.path)
    before = commit_text_or_none(repo_path, base, relp)
    after = commit_text_or_none(repo_path, tip, relp)
    return {
        "scope": ref.master,
        "kind": "memory" if ref.kind == "memory" else "code",
        "path": relp,
        "language": language_for(Path(relp)),
        "before": {"content": before} if before is not None else None,
        "after": {"content": after} if after is not None else None,
    }


def _load_leaf_contract(
    config: McpRuntimeConfig, repo_id: str, master: str, leaf: str
) -> WorktreeContract | None:
    """The leaf enclosure contract for ``leaf`` under ``master``, resolved by leaf-id, or None.

    Both requested and persisted leaf ids use ``slugify`` so authored mixed-case ids match the
    dashboard's normalized selector. Discovery is confined to the requested repository/master's
    direct enclosure directory, so a leaf-id that recurs across series cannot collide. ``leaf`` is
    confined to a single path segment.
    """
    if not leaf or "/" in leaf or "\\" in leaf or leaf.startswith("."):
        return None
    want = slugify(leaf)
    for path in _master_enclosure_contracts(config, repo_id, master):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        if contract.repo_name != repo_id or contract.cleanup == "abandoned":
            continue
        if master not in (contract.parent_task_name, contract.task_name):
            continue
        if slugify(contract.leaf_id) == want:
            return contract
    return None


def _leaf_range(
    contract: WorktreeContract, *, memory: bool, mode: str
) -> tuple[list[dict[str, Any]], str]:
    """One side's (code or memory) change-set for a leaf view ``mode``, plus its named absence.

    ``committed`` = the contract's **recorded** range (``base_commit`` -> the landed commit), which
    is the leaf's LANDED delta and is what ``mode=committed`` publishes. A still-live leaf whose
    landed commit is not recorded yet has no committed delta, and says so by name
    (:class:`~agents_remember.serving.changeset_endpoints.RecordedEndpointAbsent`) instead of binding
    the worktree's moveable ``HEAD`` and labelling it the landed delta. ``working`` = ``worktree-HEAD
    -> worktree`` (the UNCOMMITTED delta only), which is exactly what its own ``mode`` names and
    requires a live worktree. Two-commit diffs run against the repository (durable, and it shares the
    worktree's object store) so ``committed`` keeps working after the worktree is cleaned up.

    The two sides resolve **independently**, so one side's unrecorded endpoint never discards the
    other side's answer: a memory half nothing has recorded yet degrades to nothing to show -- the
    same degradation this side has always published for a leaf that does not run memory at all --
    while the code half, resolved from its own recorded commit, is still published.

    **The second return value is the named absence, and the code half carries it rather than
    refusing.** The code endpoint *is* the view, so answering it with an empty list alone would claim
    the leaf landed nothing -- but an unrecorded endpoint is a state of the task's progress, not a
    missing resource, and a ``404`` for a state every live leaf passes through is a browser console
    error on the page whose accepted criterion is zero console errors (register B6).
    :func:`leaf_changeset` therefore publishes it as the body's own explicit ``state``/
    ``stateDetail``: a measurement is never claimed, and a resource that exists is never refused.
    Every other absence (a recorded commit this repository does not hold) stays a refusal on both
    sides, since reporting it as an empty range would publish a measurement the caller never made.

    Only the code side carries a detail: a memory side's unrecorded endpoint is the
    nothing-to-show this view has always published for a leaf that does not run memory at all.
    """
    if memory:
        worktree, repository = contract.memory_worktree, contract.memory_repo_path
    else:
        worktree, repository = contract.code_worktree, contract.code_repo_path
    if mode == "working":
        # No worktree on this side (e.g. memory disabled) -> nothing to show, like task_changeset's
        # memory degradation. The code-side liveness that makes ``working`` meaningful is enforced once
        # in leaf_changeset, so a missing memory worktree never fails the whole view.
        if worktree is None or not worktree.exists():
            return [], ""
        return changed_files_with_counts(worktree, head_commit(worktree, "HEAD"), None), ""
    if repository is None:
        return [], ""
    try:
        recorded = recorded_committed_range(contract, memory=memory)
    except RecordedEndpointAbsent as absent:
        if absent.kind != NOT_RECORDED:
            raise
        return [], ("" if memory else str(absent))
    return (
        changed_files_with_counts(recorded.repository, recorded.base_commit, recorded.head_commit),
        "",
    )


def _leaf_onboarding_root(contract: WorktreeContract, mode: str) -> Path | None:
    """The onboarding root for sidecar pairing: the live worktree's for ``working``, else the repo's."""
    if mode == "working":
        candidates = [contract.memory_worktree, contract.memory_repo_path]
    else:
        candidates = [contract.memory_repo_path, contract.memory_worktree]
    for cand in candidates:
        if cand is not None and (cand / "onboarding").is_dir():
            return cand / "onboarding"
    return None


def leaf_changeset(
    config: McpRuntimeConfig, repo_id: str, master: str, leaf: str, mode: str
) -> dict[str, Any]:
    """A leaf's change-set in one ``mode`` (``committed`` or ``working``), resolved by leaf-id.

    Returns the same shape as :func:`task_changeset` (so the L4 viewer renders it unchanged): the
    code + memory changed-file lists with counts, code files tagged ``hasSidecar``. ``committed``
    reads the contract's recorded commits and works with no live worktree (a completed leaf). A leaf
    whose landed commit is not recorded yet is **answered, not refused**: the body carries
    ``state="unrecorded"`` and ``stateDetail`` naming the missing endpoint and the action that
    produces it, because that is a state of the task's progress rather than a missing resource, and
    the change-set bar probes this view on every live leaf. Its counters stay a measured zero of the
    range that *is* known, and the explicit state is what keeps that zero from being read as "the
    leaf landed nothing". ``working`` requires a live worktree (404 otherwise), and an unknown leaf
    is still a 404 by name.
    """
    contract = _load_leaf_contract(config, repo_id, master, leaf)
    if contract is None:
        raise FileNotFoundError(f"no leaf contract for {leaf!r}")
    if mode == "working" and not (
        contract.code_worktree is not None and contract.code_worktree.exists()
    ):
        raise FileNotFoundError("no live worktree for the working change-set")
    code, code_absent = _leaf_range(contract, memory=False, mode=mode)
    memory, _ = _leaf_range(contract, memory=True, mode=mode)
    onboarding_root = _leaf_onboarding_root(contract, mode)
    for entry in code:
        entry["hasSidecar"] = (
            onboarding_root is not None
            and route_sidecar_status(onboarding_root, str(entry["path"])) == "present"
        )
    return {
        "scope": contract.leaf_id,
        "mode": mode,
        "state": "unrecorded" if code_absent else "recorded",
        "stateDetail": code_absent,
        "code": code,
        "memory": memory,
        "counters": {"code": _sum(code), "memory": _sum(memory)},
    }


@dataclass(frozen=True)
class ChangesetFileRef:
    """Which file, in which change-set, seen through which lens.

    A file diff is only answerable once all of it is known: the repo and the leaf (or master)
    locate the change-set, ``kind`` picks the code or memory half of it, ``mode`` picks committed
    or working, and ``path`` names the file inside it. Any one of them alone selects nothing, so
    the selector travels as one value from the query string down to the diff.

    A master file diff additionally carries the generation pins the listing published
    (``codeBase``/``codeTip``/``memoryBase``/``memoryTip``): the pair matching ``kind`` freezes
    the AFTER side to the listed generation instead of the live tip. Empty means unpinned.
    """

    repo: str
    path: str
    kind: str = "code"
    scope: str = "mainline"
    master: str = ""
    leaf: str = ""
    mode: str = ""
    code_base: Annotated[str, Query(alias="codeBase")] = ""
    code_tip: Annotated[str, Query(alias="codeTip")] = ""
    memory_base: Annotated[str, Query(alias="memoryBase")] = ""
    memory_tip: Annotated[str, Query(alias="memoryTip")] = ""


def _pins_from_ref(ref: ChangesetFileRef) -> MasterNetPins:
    """The master generation pins a file-diff selector carries, if any."""

    return MasterNetPins(
        code_base=ref.code_base,
        code_tip=ref.code_tip,
        memory_base=ref.memory_base,
        memory_tip=ref.memory_tip,
    )


@dataclass(frozen=True)
class MasterFileRef:
    """Which file of a master net, at which generation.

    The repo and master locate the series, ``kind`` picks the code or memory half, ``path``
    names the file inside it, and ``pins`` freezes the range to the listed generation instead
    of the live integrated result. Any one of them alone selects nothing, so the selector
    travels as one value from the file ref down to the diff.
    """

    repo: str
    master: str
    kind: str = "code"
    path: str = ""
    pins: MasterNetPins = field(default_factory=MasterNetPins)


@dataclass(frozen=True)
class MasterChangesetRef:
    """Which master net, at which generation, with how much leaf detail.

    ``pins`` names the exact recorded endpoints a listing published; empty means the declared
    integrated result. ``include_leaves`` keeps the per-leaf counter breakdown alongside the
    net for callers that render it.
    """

    repo: str
    master: str
    include_leaves: Annotated[bool, Query(alias="includeLeaves")] = True
    code_base: Annotated[str, Query(alias="codeBase")] = ""
    code_tip: Annotated[str, Query(alias="codeTip")] = ""
    memory_base: Annotated[str, Query(alias="memoryBase")] = ""
    memory_tip: Annotated[str, Query(alias="memoryTip")] = ""


def _pins_from_master_ref(ref: MasterChangesetRef) -> MasterNetPins:
    """The master generation pins a change-set selector carries, if any."""

    return MasterNetPins(
        code_base=ref.code_base,
        code_tip=ref.code_tip,
        memory_base=ref.memory_base,
        memory_tip=ref.memory_tip,
    )


def _master_json(produce: Any) -> Response:
    """Run ``produce`` with the change-set 400/404 status idiom for a master range.

    The one implementation of this mapping for the two master routes, so a missing endpoint's
    named refusal cannot come to differ between the list and the file view.
    """

    try:
        return JSONResponse(produce(), status_code=200)
    except AuthorityError as err:
        return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
    except FileNotFoundError as err:
        return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)


def leaf_file_diff(config: McpRuntimeConfig, ref: ChangesetFileRef) -> dict[str, Any]:
    """BEFORE + AFTER content for one file in a leaf's ``committed`` or ``working`` change-set.

    ``committed`` = the two **recorded** commits (``base`` vs the landed commit), read from the
    repository that holds them. A recorded endpoint that is absent is refused by name rather than
    replaced with the worktree's ``HEAD``. ``working`` = the worktree HEAD vs the (dirty) worktree
    file, which is the one view whose after-side is a filesystem location, and its own ``mode`` says
    so. Mirrors :func:`file_diff`'s response so the L4 MergeView feeds it directly.
    """
    kind, rel, mode = ref.kind, ref.path, ref.mode
    contract = _load_leaf_contract(config, ref.repo, ref.master, ref.leaf)
    if contract is None:
        raise FileNotFoundError(f"no leaf contract for {ref.leaf!r}")
    memory = kind == "memory"
    worktree = contract.memory_worktree if memory else contract.code_worktree
    if mode == "working":
        if worktree is None or not worktree.exists():
            raise FileNotFoundError("no live worktree for the working change-set")
        relp = confine_rel(worktree, rel)
        before = commit_text_or_none(worktree, head_commit(worktree, "HEAD"), relp)
        after_path = worktree / relp
        after = after_path.read_text(errors="replace") if after_path.is_file() else None
    else:
        recorded = recorded_committed_range(contract, memory=memory)
        relp = confine_rel(recorded.repository, rel)
        before = commit_text_or_none(recorded.repository, recorded.base_commit, relp)
        after = commit_text_or_none(recorded.repository, recorded.head_commit, relp)
    return {
        "scope": contract.leaf_id,
        "kind": "memory" if memory else "code",
        "path": relp,
        "language": language_for(Path(relp)),
        "before": {"content": before} if before is not None else None,
        "after": {"content": after} if after is not None else None,
    }


def _leaf_json(produce: Any, master: str, mode: str) -> Response:
    """Validate the leaf selector, then run ``produce`` with the change-set 400/404 status idiom.

    A leaf change-set is qualified by ``master`` (which scopes the contract search) and needs an
    explicit ``mode`` -- so the selector is explicit and validated, not inferred from which optional
    param happens to be present. A leaf without ``master`` or with an unknown ``mode`` is a 400.
    """
    if not master:
        return JSONResponse(
            {"status": "bad-request", "detail": "leaf change-set needs master"}, status_code=400
        )
    if mode not in ("committed", "working"):
        return JSONResponse(
            {"status": "bad-request", "detail": "leaf change-set needs mode=committed|working"},
            status_code=400,
        )
    try:
        return JSONResponse(produce(), status_code=200)
    except AuthorityError as err:
        return JSONResponse({"status": "bad-path", "detail": str(err)}, status_code=400)
    except FileNotFoundError as err:
        return JSONResponse({"status": "not-found", "path": str(err)}, status_code=404)


def register_changeset_routes(app: FastAPI, config: McpRuntimeConfig) -> None:
    """Register the read-only change-set routes. Must be called BEFORE the greedy static mount.

    The change-set is selected by precedence ``leaf > master > scope`` (a ``leaf`` view is the per-leaf
    committed/working delta, ``master`` the series net, ``scope`` one active enclosure). ``leaf`` is
    qualified by ``master`` and requires a ``mode``; both ``leaf`` and ``master`` go through the
    JSONResponse 400/404 idiom rather than ``run_scoped``.
    """

    # Two success shapes: the leaf view adds ``mode`` to the enclosure view's body, and the
    # ``leaf`` selector is what picks between them.
    @app.get(
        "/api/changeset/task",
        response_model=LeafChangeSet | TaskChangeSet,
        responses=SCOPED_READ_RESPONSES,
    )
    def api_changeset_task(
        repo: str, scope: str = "mainline", master: str = "", leaf: str = "", mode: str = ""
    ) -> Response:
        if leaf:
            return _leaf_json(
                lambda: leaf_changeset(config, repo, master, leaf, mode), master, mode
            )
        return run_scoped(task_changeset, config, repo, scope)

    @app.get("/api/changeset/file-diff", response_model=FileDiff, responses=SCOPED_READ_RESPONSES)
    def api_changeset_file_diff(ref: Annotated[ChangesetFileRef, Depends()]) -> Response:
        if ref.leaf:
            return _leaf_json(lambda: leaf_file_diff(config, ref), ref.master, ref.mode)
        if ref.master:
            # Series net diff (master base -> selected result); no enclosure scope, so it does
            # not go through run_scoped -- the shared master mapping below carries the same
            # status idiom. Generation pins freeze the AFTER side to the listed generation.
            return _master_json(
                lambda: master_file_diff(
                    config,
                    MasterFileRef(
                        repo=ref.repo,
                        master=ref.master,
                        kind=ref.kind,
                        path=ref.path,
                        pins=_pins_from_ref(ref),
                    ),
                )
            )
        return run_scoped(lambda fs: file_diff(fs, ref.kind, ref.path), config, ref.repo, ref.scope)

    # An unknown master (no series contract) degrades to empty lists rather than refusing. A
    # master the contract names but whose code endpoints are missing is refused by name instead
    # of being answered from a later branch tip.
    @app.get(
        "/api/changeset/master", response_model=MasterChangeSet, responses=SCOPED_READ_RESPONSES
    )
    def api_changeset_master(ref: Annotated[MasterChangesetRef, Depends()]) -> Response:
        return _master_json(
            lambda: master_changeset(
                config,
                ref.repo,
                ref.master,
                include_leaves=ref.include_leaves,
                pins=_pins_from_master_ref(ref),
            )
        )
