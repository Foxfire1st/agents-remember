"""Direct-execution grants: giving and taking back a leaf's grant (MIK-R94).

The store addressing, record shape, and call-time read live in
``kernel.direct_execution_grants`` so every layer reads a grant without
reaching up. This module owns the two product writes -- the architect's
``direct_execution_grant`` function here, and the dashboard's control calling
:func:`give_grant`/:func:`revoke_grant` -- both of which prove the leaf
unstarted before changing anything.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import read_agent_binding
from agents_remember.application.task_docs.task_unstarted_evidence import prove_task_unstarted
from agents_remember.kernel.direct_execution_grants import (
    ARCHITECT_GRANTER,
    DEVELOPER_GRANTER,
    GRANT_GIVERS,
    DirectExecutionGrant,
    DirectGrantError,
    code_checkout_dirty,
    create_grant_file,
    grant_from_document,
    grant_path_for,
    grant_removal_paths,
    grant_store_dir,
    grant_store_locked,
    iter_grant_files,
    iter_grant_leaves,
    memory_checkout_dirty,
    read_grant,
    remove_matching_grant,
    require_grant,
    sibling_occupies_checkout,
    utc_now_iso,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology
from agents_remember.tasks.task_paths import series_contract_path
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    direct_pair_proof,
)
from agents_remember.worktrees.task_leaf_binding import (
    TaskLeafBindingError,
    resolve_leaf_task_binding,
)
from agents_remember.worktrees.worktree_contract import load_contract

__all__ = [
    "ARCHITECT_GRANTER",
    "DEVELOPER_GRANTER",
    "GRANT_GIVERS",
    "DirectExecutionGrant",
    "DirectGrantError",
    "GrantChange",
    "GrantReclaim",
    "direct_execution_grant_tool",
    "give_grant",
    "grant_path_for",
    "grant_store_dir",
    "read_grant",
    "reclaim_direct_execution_grants",
    "require_grant",
    "revoke_grant",
]


def _resolve_grant_leaf(coordination_root: Path, ref: TaskDocumentRef) -> tuple[Path, str]:
    """The leaf's task root and id for a canonical leaf reference, else a refusal."""
    try:
        resolved = TaskDocumentTopology(coordination_root).resolve(ref)
    except Exception as error:
        raise DirectGrantError(
            f"no canonical task document for {ref.repository}:{ref.path}: {error}",
            status="direct-grant-leaf-missing",
        ) from error
    if resolved.document.kind != "subTask":
        raise DirectGrantError(
            "a direct-execution grant is given for a leaf only; "
            f"{ref.repository}:{ref.path} is {resolved.document.kind}",
            status="direct-grant-leaf-only",
        )
    return resolved.path.parent, resolved.document.id


def _refuse_started_leaf(config: Any, coordination_root: Path, ref: TaskDocumentRef) -> None:
    """Refuse a grant change for a started leaf, naming the evidence of the start."""
    task_root, leaf_id = _resolve_grant_leaf(coordination_root, ref)
    try:
        binding = resolve_leaf_task_binding(coordination_root, ref.repository, task_root, leaf_id)
    except TaskLeafBindingError as error:
        raise DirectGrantError(error.detail, status=error.status) from error
    evidence = prove_task_unstarted(config, binding)
    if evidence.unstarted:
        return
    facts = (
        "; ".join(f"{fact.get('kind')}: {fact.get('state')}" for fact in evidence.facts)
        or "the leaf has started"
    )
    raise DirectGrantError(
        f"the grant for {ref.repository}:{ref.path} is refused: the leaf has started "
        f"({facts}). Finish the leaf as it is, or abandon it and plan a new one",
        status="direct-grant-leaf-started",
        facts={"facts": evidence.facts},
    )


@dataclass(frozen=True)
class GrantChange:
    """The outcome of giving or taking back a grant."""

    changed: bool
    grant: DirectExecutionGrant | None
    detail: str


def give_grant(
    config: Any,
    ref: TaskDocumentRef,
    *,
    granted_by: str,
    granter_agent_id: str | None = None,
) -> GrantChange:
    """Give a leaf its direct-execution grant before work starts.

    Giving a grant that exists changes nothing and says so. A grant for a
    started leaf, or for a master or light task, is refused. The read and
    proof hold the store lock; the publication is an atomic exclusive create,
    so concurrent givers across processes and servers produce one changed
    grant and one unchanged response retaining its issuer and time, with no
    lock held across the publication itself (F08). A leaf that started
    between the proof and the publication does not keep the grant: it is
    removed again and the give is refused.
    """
    coordination_root = Path(config.coordination_root)
    address = f"{ref.repository}:{ref.path}"
    for _attempt in (0, 1):
        with grant_store_locked(coordination_root):
            _resolve_grant_leaf(coordination_root, ref)
            existing = read_grant(coordination_root, ref)
            if existing is not None:
                return GrantChange(
                    changed=False,
                    grant=existing,
                    detail=(
                        f"{address} already has its direct-execution grant "
                        f"({existing.describe()}); nothing changed"
                    ),
                )
            _refuse_started_leaf(config, coordination_root, ref)
        grant = DirectExecutionGrant(
            leaf=ref,
            granted_by=granted_by,
            granted_at=utc_now_iso(),
            granter_agent_id=granter_agent_id,
        )
        try:
            create_grant_file(grant_path_for(coordination_root, ref), grant)
        except FileExistsError:
            existing = read_grant(coordination_root, ref)
            if existing is not None:
                return GrantChange(
                    changed=False,
                    grant=existing,
                    detail=(
                        f"{address} already has its direct-execution grant "
                        f"({existing.describe()}); nothing changed"
                    ),
                )
            continue
        except OSError as error:
            raise DirectGrantError(
                f"the direct-execution grant for {address} cannot be written: {error}",
                status="direct-grant-unwritable",
            ) from error
        try:
            _refuse_started_leaf(config, coordination_root, ref)
        except DirectGrantError:
            _remove_own_grant(coordination_root, ref, grant)
            raise
        _refresh_leaf_markdown(coordination_root, ref)
        _migrate_legacy_grant_file(coordination_root, ref)
        return GrantChange(
            changed=True,
            grant=grant,
            detail=f"{address} has its direct-execution grant ({grant.describe()})",
        )
    raise DirectGrantError(
        f"the direct-execution grant for {address} cannot be written: "
        "the grant file changed under this give",
        status="direct-grant-unwritable",
    )


def _remove_own_grant(
    coordination_root: Path, ref: TaskDocumentRef, grant: DirectExecutionGrant
) -> None:
    """Remove exactly the grant this give published, for a post-write refusal."""

    remove_matching_grant(coordination_root, ref, grant)


def _refresh_leaf_markdown(coordination_root: Path, ref: TaskDocumentRef) -> None:
    """Re-render the leaf's stored Markdown with its live grant (F12).

    Give and revoke rewrite the addressed ``.md`` through the ordinary
    publication path, so the ``get renderedPath`` artifact always agrees with
    the store: exactly one current giver/time header when granted, none when
    absent. Ungranted Markdown stays byte-identical because the render adds
    the header only for a live grant.
    """

    task_root, _leaf_id = _resolve_grant_leaf(coordination_root, ref)
    try:
        document = read_task_doc(task_root / Path(ref.path).name)
    except (OSError, ValueError) as error:
        raise DirectGrantError(
            f"the direct-execution grant for {ref.repository}:{ref.path} was stored, "
            f"but its document cannot be re-rendered: {error}",
            status="direct-grant-unwritable",
        ) from error
    write_task_doc(task_root, document)


def _migrate_legacy_grant_file(coordination_root: Path, ref: TaskDocumentRef) -> None:
    """Remove the pre-addressing file once the addressed one is written."""
    for legacy in grant_removal_paths(coordination_root, ref)[1:]:
        with suppress(OSError):
            legacy.unlink()


@dataclass(frozen=True)
class GrantReclaim:
    """The outcome of reclaiming one task's grants that no leaf still requires."""

    reclaimed: tuple[str, ...]
    preserved: tuple[dict[str, str], ...]


def _grant_leaf_ref(leaf: dict[str, Any]) -> TaskDocumentRef | None:
    """The leaf reference of a decoded grant record, or None when it is not one."""
    repository = leaf.get("repository")
    leaf_path = leaf.get("path")
    if not isinstance(repository, str) or not isinstance(leaf_path, str):
        return None
    try:
        return TaskDocumentRef(repository=repository, path=leaf_path)
    except Exception:
        return None


def reclaim_direct_execution_grants(
    config: Any, ref: TaskDocumentRef, *, dry_run: bool = False
) -> GrantReclaim:
    """Take back one task's grants that no leaf still requires (F09).

    The product-owned reclamation policy, safe under rule 5: a grant is
    reclaimed only for a leaf whose document reads ``abandoned`` *and* that
    never started *and* whose series checkouts are determinably clean. An
    abandoned leaf that started keeps its grant: abandonment is not a packet
    amendment authorizing taking back a started grant. Every other grant is
    preserved with its reason -- in particular a ``Completed`` leaf keeps its
    grant even with clean checkouts, because completion is not landing proof
    (F05). Preserved grants carry the reason: a leaf that still requires its
    grant, a started leaf kept under rule 5, uncommitted work, an
    undeterminable checkout, an unreadable leaf document, or a file that is
    not a valid grant record (never removed: it may belong to another writer
    version). Pre-addressing files for reclaimed leaves are removed with
    them. The whole census holds the store lock with giving.
    """
    coordination_root = Path(config.coordination_root)
    parts = Path(ref.path).parts
    if not parts:
        raise DirectGrantError(
            f"no task address in {ref.repository}:{ref.path}; nothing reclaimed",
            status="direct-grant-reclaim-refused",
        )
    master = parts[0]
    task_root = (coordination_root / "tasks" / ref.repository / master).resolve(strict=False)
    with grant_store_locked(coordination_root):
        checkout_dirty = _sibling_checkout_dirty(task_root)
        reclaimed: list[str] = []
        preserved: list[dict[str, str]] = []
        store = grant_store_dir(coordination_root)
        for path in iter_grant_files(store, repository=ref.repository, master=master):
            try:
                document = json.loads(path.read_bytes().decode("utf-8"))
            except (OSError, ValueError):
                preserved.append({"file": path.name, "reason": "grant file unreadable"})
                continue
            leaf = document.get("leaf") if isinstance(document, dict) else None
            if not isinstance(leaf, dict):
                preserved.append({"file": path.name, "reason": "not a grant record"})
                continue
            leaf_ref = _grant_leaf_ref(leaf)
            if leaf_ref is None:
                preserved.append({"file": path.name, "reason": "not a grant record"})
                continue
            try:
                grant = grant_from_document(leaf_ref, document)
            except DirectGrantError:
                preserved.append({"file": path.name, "reason": "not a valid grant"})
                continue
            _reclaim, reason = _reclaim_decision(config, coordination_root, grant, checkout_dirty)
            label = f"{grant.leaf.repository}:{grant.leaf.path}"
            if _reclaim is None:
                preserved.append({"ref": label, "reason": reason})
                continue
            if not dry_run:
                for candidate in grant_removal_paths(coordination_root, grant.leaf):
                    with suppress(OSError):
                        candidate.unlink()
            reclaimed.append(label)
    return GrantReclaim(reclaimed=tuple(reclaimed), preserved=tuple(preserved))


def _reclaim_task_root(coordination_root: Path, grant: DirectExecutionGrant) -> Path:
    """The task root of a grant's leaf, unresolved when the address is bad."""

    parts = Path(grant.leaf.path).parts
    master = parts[0] if parts else ""
    return (coordination_root / "tasks" / grant.leaf.repository / master).resolve(strict=False)


def _reclaim_leaf_worked(
    config: Any,
    coordination_root: Path,
    grant: DirectExecutionGrant,
    document: Any,
    task_root: Path,
) -> bool:
    """Whether an abandoned leaf shows work evidence (F09).

    Status words alone do not decide: abandonment is a status, and a
    never-worked abandoned leaf is exactly what reclamation collects. Work
    evidence does: execution registrations, a route review, a lifecycle, an
    enclosure, a non-pending step, a landed pair, or any started/ambiguous
    cell of the ordinary unstarted proof (seat, report, locator, enclosure,
    operation, commit) evaluated with status words masked to planning — the
    same sources the ordinary give/revoke refusal reads. A worked abandoned
    leaf keeps its grant under rule 5.
    """

    if any(
        [
            bool(getattr(document, "executionRegistrations", None)),
            getattr(document, "routeReview", None) is not None,
            getattr(document, "lifecycleId", None) is not None,
            bool(getattr(document, "enclosures", None)),
            any(
                (step.status, step.disposition) != ("pending", None)
                for step in (getattr(document, "steps", None) or [])
            ),
        ]
    ):
        return True
    try:
        series_contract = load_contract(series_contract_path(task_root))
        if direct_pair_proof(series_contract, grant.leaf) is True:
            return True
    except Exception:
        pass
    return _reclaim_leaf_started_besides_status(
        config, coordination_root, grant, document, task_root
    )


def _reclaim_leaf_started_besides_status(
    config: Any,
    coordination_root: Path,
    grant: DirectExecutionGrant,
    document: Any,
    task_root: Path,
) -> bool:
    """The ordinary unstarted proof with status words masked to planning.

    Abandonment is a status word, and the proof counts any non-planning
    status as execution-progress — so the plain proof would call every
    abandoned leaf started and reclamation could never collect. Masking the
    leaf and row statuses to planning asks what the ordinary give/revoke
    refusal saw before abandonment: seat, report, locator, enclosure,
    operation and commit evidence still count; status words alone do not. An
    unresolvable binding or an unreadable proof preserves: absence of work
    cannot be proved, and an ambiguous locator is not proof of no work.
    """

    try:
        binding = resolve_leaf_task_binding(
            coordination_root, grant.leaf.repository, task_root, document.id
        )
        masked = replace(
            binding,
            row=binding.row.model_copy(update={"status": "planning"}),
            leaf=(
                binding.leaf.model_copy(update={"status": "planning"})
                if binding.leaf is not None
                else None
            ),
        )
        evidence = prove_task_unstarted(config, masked)
    except Exception:
        return True
    return not evidence.unstarted


def _reclaim_decision(
    config: Any,
    coordination_root: Path,
    grant: DirectExecutionGrant,
    checkout_dirty: bool | None,
) -> tuple[bool | None, str]:
    """Whether one grant is reclaimed (True), preserved (None), and why."""
    try:
        TaskDocumentTopology(coordination_root).resolve(grant.leaf)
        document = read_task_doc(
            coordination_root / "tasks" / grant.leaf.repository / grant.leaf.path
        )
    except Exception:
        return None, "leaf document unreadable"
    status = getattr(document, "status", None)
    if status != "abandoned":
        if status == "Completed":
            reason = (
                "completed leaf may still require its grant; "
                "reclaim preserves started leaves (rule 5)"
            )
        else:
            reason = f"leaf still requires its grant (status {status})"
        return None, reason
    if _reclaim_leaf_worked(
        config, coordination_root, grant, document, _reclaim_task_root(coordination_root, grant)
    ):
        return (
            None,
            "started leaf keeps its grant under rule 5; abandonment does not "
            "authorize taking back a started grant",
        )
    if checkout_dirty is not False:
        if checkout_dirty is True:
            reason = "uncommitted work still guards its sibling"
        else:
            reason = "checkout undeterminable"
        return None, reason
    return True, "abandoned leaf with a clean checkout"


def revoke_grant(config: Any, ref: TaskDocumentRef) -> GrantChange:
    """Take back a leaf's grant while the leaf has not started.

    Taking back a grant that does not exist changes nothing and says so; after
    the start the same refusal as giving holds. The read and proof hold the
    store lock and the removal is one atomic unlink, so concurrent give/revoke
    outcomes stay serial-consistent. A leaf that started between the proof
    and the removal keeps its grant: the exact removed record is restored and
    the revoke is refused (F08).
    """
    coordination_root = Path(config.coordination_root)
    address = f"{ref.repository}:{ref.path}"
    with grant_store_locked(coordination_root):
        _resolve_grant_leaf(coordination_root, ref)
        existing = read_grant(coordination_root, ref)
        if existing is None:
            return GrantChange(
                changed=False,
                grant=None,
                detail=f"{address} has no direct-execution grant; nothing changed",
            )
        _refuse_started_leaf(config, coordination_root, ref)
    removed = False
    for path in grant_removal_paths(coordination_root, ref):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            continue
        except OSError as error:
            raise DirectGrantError(
                f"the direct-execution grant for {address} cannot be removed: {error}",
                status="direct-grant-unwritable",
            ) from error
    if not removed:
        return GrantChange(
            changed=False,
            grant=None,
            detail=f"{address} has no direct-execution grant; nothing changed",
        )
    try:
        _refuse_started_leaf(config, coordination_root, ref)
    except DirectGrantError:
        _restore_removed_grant(coordination_root, ref, existing)
        raise
    _refresh_leaf_markdown(coordination_root, ref)
    return GrantChange(
        changed=True,
        grant=None,
        detail=f"the direct-execution grant for {address} was taken back",
    )


def _restore_removed_grant(
    coordination_root: Path, ref: TaskDocumentRef, grant: DirectExecutionGrant
) -> None:
    """Restore exactly the grant a revoke removed, for a post-removal refusal.

    The exclusive create never clobbers a concurrent writer's grant: when
    another giver won the race, theirs stands and the revoke still refuses.
    """

    with suppress(FileExistsError, OSError):
        create_grant_file(grant_path_for(coordination_root, ref), grant)


def direct_execution_grant_tool(
    config: Any,
    ref: TaskDocumentRef,
    action: str,
    *,
    binding: Any | None = None,
) -> dict[str, Any]:
    """Give, take back, or reclaim one task's direct-execution grants as the architect's tool-server function.

    Only a caller whose launch is bound to the architect role is admitted: every
    other role, and a caller without a role binding, is refused. The developer
    gives a grant with the dashboard's control instead.
    """
    caller = binding if binding is not None else read_agent_binding()
    if caller is None:
        return _grant_refusal(
            "direct-grant-architect-only",
            f"direct_execution_grant without a role binding is refused; {GRANT_GIVERS}",
            next_action="ask-architect",
        )
    role = getattr(caller, "role", "")
    if role != ARCHITECT_GRANTER:
        return _grant_refusal(
            "direct-grant-architect-only",
            f"direct_execution_grant by role {role!r} is refused; only an architect "
            "gives or takes back a grant, and the developer uses the dashboard control",
            next_action="ask-architect",
        )
    if action not in ("give", "revoke", "reclaim"):
        return _grant_refusal(
            "direct-grant-action-unknown",
            f"direct_execution_grant action must be 'give', 'revoke' or 'reclaim', not {action!r}",
            next_action="correct-request",
        )
    try:
        if action == "give":
            change = give_grant(
                config,
                ref,
                granted_by=ARCHITECT_GRANTER,
                granter_agent_id=getattr(caller, "agent_id", None),
            )
        elif action == "revoke":
            change = revoke_grant(config, ref)
        else:
            reclaimed = reclaim_direct_execution_grants(config, ref)
            return {
                "ok": True,
                "operation": "direct_execution_grant",
                "state": "reclaimed",
                "summary": (
                    f"reclaimed {len(reclaimed.reclaimed)} grant(s), "
                    f"preserved {len(reclaimed.preserved)}"
                ),
                "changed": bool(reclaimed.reclaimed),
                "grant": None,
                "reclaimed": list(reclaimed.reclaimed),
                "preserved": [dict(entry) for entry in reclaimed.preserved],
            }
    except DirectGrantError as error:
        return _grant_refusal(error.status, error.detail, next_action="correct-request")
    if action == "give":
        state = "granted" if change.changed else "unchanged"
    else:
        state = "revoked" if change.changed else "unchanged"
    return {
        "ok": True,
        "operation": "direct_execution_grant",
        "state": state,
        "summary": change.detail,
        "changed": change.changed,
        "grant": change.grant.model_dump() if change.grant is not None else None,
    }


def _grant_refusal(status: str, detail: str, *, next_action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": "direct_execution_grant",
        "state": "refused",
        "status": status,
        "detail": detail,
        "nextAction": next_action,
        "changed": False,
        "grant": None,
    }


def _stored_grant_refs(
    coordination_root: Path, *, repository: str | None = None, master: str | None = None
) -> list[TaskDocumentRef]:
    """Granted leaves, in stable order; unreadable files are skipped.

    Bounded to one task's addressed files when repository and master are
    given; the sibling check passes its own task.
    """
    store = grant_store_dir(coordination_root)
    refs: list[TaskDocumentRef] = []
    for leaf_repo, leaf_path in iter_grant_leaves(store, repository=repository, master=master):
        try:
            refs.append(TaskDocumentRef(repository=leaf_repo, path=leaf_path))
        except ValueError:
            # A structurally invalid canonical reference is skipped here, as in
            # the task census (F13); gate reads still refuse the selected grant.
            continue
    return refs


def _sibling_task_root(coordination_root: Path, ref: TaskDocumentRef) -> Path | None:
    """The sibling's task root when its file lies under the tasks tree."""
    parts = Path(ref.path).parts
    if len(parts) < 2:
        return None
    return (coordination_root / "tasks" / ref.repository / parts[0]).resolve(strict=False)


def _sibling_checkout_dirty(task_root: Path) -> bool | None:
    """Whether either checkout of the task root's series branch has uncommitted work.

    ``False`` when no series contract exists (no branch checkout can hold
    work) or both checkouts read clean. ``None`` when the contract exists but
    its checkouts cannot be determined -- then the caller falls back to the
    document-status guard.
    """

    try:
        contract_path = series_contract_path(task_root)
    except Exception:
        return None
    if not contract_path.is_file():
        return False
    try:
        contract = load_contract(contract_path)
        code = code_checkout_dirty(Path(contract.code_repo_path), contract.code_work_branch)
        memory = memory_checkout_dirty(
            Path(contract.memory_repo_path) if contract.memory_repo_path is not None else None,
            contract.memory_work_branch,
        )
    except Exception:
        return None
    if code is True or memory is True:
        return True
    if code is None or memory is None:
        return None
    return False


def _sibling_series_contract(task_root: Path) -> Any | None:
    """The task root's series contract, or ``None`` when it cannot be read."""

    try:
        return load_contract(series_contract_path(task_root))
    except Exception:
        return None


def open_direct_sibling(config: Any, ref: TaskDocumentRef) -> tuple[TaskDocumentRef, str] | None:
    """The first granted sibling leaf that is started and neither landed nor abandoned.

    ``None`` when no other direct leaf of the same master is in work. The start
    is decided by the same unstarted evidence as giving a grant. A ``Completed``
    leaf with started markers occupies the checkout while either side is dirty
    or no landing proof exists; a completed generation with its published pair
    releases it, as does an explicitly abandoned leaf (F05).
    """
    coordination_root = Path(config.coordination_root)
    task_root, _leaf_id = _resolve_grant_leaf(coordination_root, ref)
    mine = task_root.resolve(strict=False)
    caller_parts = Path(ref.path).parts
    caller_master = caller_parts[0] if caller_parts else None
    siblings = _stored_grant_refs(
        coordination_root, repository=ref.repository, master=caller_master
    )
    checkout_dirty = _sibling_checkout_dirty(mine)
    series_contract = _sibling_series_contract(mine)
    scan = _SiblingScan(
        coordination_root=coordination_root,
        mine=mine,
        caller=(ref.repository, ref.path),
        checkout_dirty=checkout_dirty,
        series_contract=series_contract,
    )
    for sibling in siblings:
        found = _open_sibling_candidate(config, scan, sibling)
        if found is not None:
            return found
    return None


@dataclass(frozen=True)
class _SiblingScan:
    """One sibling-guard sweep: the caller's line plus its shared evidence."""

    coordination_root: Path
    mine: Path
    caller: tuple[str, str]
    checkout_dirty: bool | None
    series_contract: Any | None


def _open_sibling_candidate(
    config: Any, scan: _SiblingScan, sibling: TaskDocumentRef
) -> tuple[TaskDocumentRef, str] | None:
    """The sibling when it is a started, unlanded direct leaf in work."""

    if (sibling.repository, sibling.path) == scan.caller:
        return None
    sibling_root = _sibling_task_root(scan.coordination_root, sibling)
    if sibling_root is None or sibling_root != scan.mine:
        return None
    try:
        _sibling_id = TaskDocumentTopology(scan.coordination_root).resolve(sibling)
        binding = resolve_leaf_task_binding(
            scan.coordination_root,
            sibling.repository,
            sibling_root,
            _sibling_id.document.id,
        )
    except (TaskDocumentRefError, TaskLeafBindingError):
        return None
    if _sibling_id.document.kind != "subTask" or prove_task_unstarted(config, binding).unstarted:
        return None
    landed = (
        direct_pair_proof(scan.series_contract, sibling)
        if scan.series_contract is not None
        else None
    )
    if not sibling_occupies_checkout(
        _sibling_id.document, checkout_dirty=scan.checkout_dirty, landed=landed
    ):
        return None
    status = binding.leaf.status if binding.leaf is not None else ""
    return sibling, status or "started"


def refuse_open_direct_sibling(config: Any, ref: TaskDocumentRef, operation: str) -> None:
    """Refuse a start or landing for a second direct leaf naming the first."""
    found = open_direct_sibling(config, ref)
    if found is None:
        return
    sibling, _status = found
    raise DirectGrantError(
        f"{operation} for {ref.repository}:{ref.path} is refused: the direct leaf "
        f"{sibling.repository}:{sibling.path} of the same master is already in work; "
        "one direct leaf at a time works in a checkout",
        status="direct-grant-sibling-open",
    )
