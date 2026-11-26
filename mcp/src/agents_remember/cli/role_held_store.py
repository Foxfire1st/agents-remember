"""Held-message admission and settlement under the launch receipt's lock."""

from __future__ import annotations

import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agents_remember.controlplane.held_store_layout import (
    MAX_HELD_RECORD_LINES,
    _now_iso,
    _read_store_file,
    _write_store_file,
    record_lines,
    recount_waiting_for,
    store_path_for,
)
from agents_remember.errors import AuthorityError
from agents_remember.kernel.file_lock import exclusive_file_lock, lock_path_for

if TYPE_CHECKING:
    from agents_remember.worktrees.worktree_contract import WorktreeContract

GENERATION = "heldStoreGeneration"
CLOSURES = "heldStoreClosures"
_RESTARTABLE_STATUSES = ("stopped", "rejected")
_REPLAY_STATUSES = ("held", "starting", "unknown")
_ENDED = "not-delivered-archived-or-gone"
_UNCONFIRMED = "closed-with-delivery-unconfirmed"
HostClosure = Literal["archived", "gone"]
_Refusal = Literal[
    "closed", "generation-changed", "receipt-moved", "workspace-missing", "creation-pending"
]


class HeldSeatClosed(AuthorityError):
    """A specific launch generation will not accept a held-message mutation."""

    def __init__(self, agent_id: str, reason: _Refusal) -> None:
        self.agent_id = agent_id
        self.reason = reason
        detail = {
            "closed": "its launch receipt records closure",
            "generation-changed": "its launch has moved to a different generation",
            "receipt-moved": "its launch receipt has moved or now names a different seat",
            "workspace-missing": "its working folder is absent",
            "creation-pending": "its prepared creation or opening is still possible",
        }[reason]
        super().__init__(f"Agent {agent_id} cannot write its held-message store: {detail}.")


@dataclass(frozen=True, slots=True)
class HeldSeatRef:
    receipt_path: Path
    request_id: str
    agent_id: str
    workspace: Path | None
    generation: int

    @property
    def store_path(self) -> Path | None:
        return store_path_for(self.workspace, self.agent_id) if self.workspace is not None else None


@dataclass(frozen=True, slots=True)
class HeldStoreTransaction:
    ref: HeldSeatRef
    receipt: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HeldStoreSettlement:
    agent_id: str
    message_count: int
    store_removed: bool
    lock_removed: bool


def receipt_generation(receipt: Mapping[str, Any]) -> int:
    generation = receipt.get(GENERATION, 0)
    if type(generation) is not int or generation < 0:
        raise AuthorityError("The launch receipt names an invalid held-message generation.")
    return generation


def held_seat_ref(path: Path, receipt: Mapping[str, Any]) -> HeldSeatRef:
    agent_id = receipt.get("agentId")
    request_id = receipt.get("requestId")
    if (
        not isinstance(agent_id, str)
        or not agent_id
        or not isinstance(request_id, str)
        or not request_id
    ):
        raise AuthorityError(f"The launch receipt at {path} does not name its agent and request.")
    workspace = receipt.get("workspace")
    folder = workspace.get("path") if isinstance(workspace, dict) else None
    return HeldSeatRef(
        path,
        request_id,
        agent_id,
        Path(folder) if isinstance(folder, str) and folder else None,
        receipt_generation(receipt),
    )


def receipt_is_closed(receipt: Mapping[str, Any]) -> bool:
    closures = receipt.get(CLOSURES)
    return _receipt_marks_closure(receipt) or (
        isinstance(closures, dict) and str(receipt_generation(receipt)) in closures
    )


def receipt_is_explicitly_retired(receipt: Mapping[str, Any]) -> bool:
    retired = receipt.get("retiredBy")
    return isinstance(retired, dict) and retired.get("caller") != "host"


def _receipt_marks_closure(receipt: Mapping[str, Any]) -> bool:
    archive = receipt.get("leafArchive")
    return (
        isinstance(receipt.get("retiredBy"), dict)
        or receipt.get("status") == "rejected"
        or (isinstance(archive, dict) and archive.get("closing") is True)
    )


def receipt_may_create(receipt: Mapping[str, Any]) -> bool:
    archive = receipt.get("leafArchive")
    return (isinstance(archive, dict) and archive.get("creationPending") is True) or (
        receipt.get("status") in _REPLAY_STATUSES and isinstance(receipt.get("replayRequest"), dict)
    )


def leaf_workspace_allows_creation(
    receipt: Mapping[str, Any], *, contract: WorktreeContract | None = None
) -> bool:
    from agents_remember.worktrees.integration.terminal_enclosure_archive import (  # noqa: PLC0415
        terminal_contract_authority_if_present,
    )
    from agents_remember.worktrees.worktree_contract import load_contract  # noqa: PLC0415

    workspace = receipt.get("workspace")
    if not isinstance(workspace, dict):
        return True
    address = workspace.get("contractPath")
    if not isinstance(address, str):
        return True
    if contract is None:
        contract = load_contract(Path(address))
    generation = workspace.get("lifecycleId")
    if generation and generation != contract.lifecycle_id:
        return False
    if contract.cleanup in {"completed", "abandoned", "reopened"}:
        return False
    terminal = terminal_contract_authority_if_present(contract)
    return terminal is None or terminal.state == "reopen-reset"


def _current(ref: HeldSeatRef) -> dict[str, Any]:
    # All writers use this receipt reader, including the ordinary launch routes.
    from agents_remember.cli.role_launch_receipts import _read_receipt  # noqa: PLC0415

    receipt = _read_receipt(ref.receipt_path)
    if receipt is None:
        raise HeldSeatClosed(ref.agent_id, "receipt-moved")
    current = held_seat_ref(ref.receipt_path, receipt)
    if (current.request_id, current.agent_id, current.workspace) != (
        ref.request_id,
        ref.agent_id,
        ref.workspace,
    ):
        raise HeldSeatClosed(ref.agent_id, "receipt-moved")
    if current.generation != ref.generation:
        raise HeldSeatClosed(ref.agent_id, "generation-changed")
    return receipt


@contextmanager
def open_held_store(ref: HeldSeatRef) -> Iterator[HeldStoreTransaction]:
    """Admit one local write or link creation after checking the exact live receipt."""
    with exclusive_file_lock(ref.receipt_path, "role execution receipt"):
        receipt = _current(ref)
        if receipt_is_closed(receipt):
            raise HeldSeatClosed(ref.agent_id, "closed")
        if ref.workspace is None or not ref.workspace.is_dir():
            raise HeldSeatClosed(ref.agent_id, "workspace-missing")
        yield HeldStoreTransaction(ref, receipt)


def refresh_waiting_count(ref: HeldSeatRef) -> None:
    """Record the current count, including zero when the workspace is already absent."""
    from agents_remember.cli.role_launch_receipts import _write_receipt  # noqa: PLC0415

    with (
        suppress(OSError, HeldSeatClosed),
        exclusive_file_lock(ref.receipt_path, "role execution receipt"),
    ):
        receipt = _current(ref)
        if not receipt_is_closed(receipt):
            recount_waiting_for(receipt)
            _write_receipt(ref.receipt_path, receipt)


def settle_held_store(
    ref: HeldSeatRef,
    *,
    host_state: HostClosure | None = None,
    receipt_updates: Mapping[str, Any] | None = None,
    remove_receipt_fields: tuple[str, ...] = (),
) -> HeldStoreSettlement:
    """Publish the closed receipt and its exact waiting text before unlinking the store."""
    with exclusive_file_lock(ref.receipt_path, "role execution receipt"):
        receipt = _current(ref)
        updates = receipt_updates or {}
        if not receipt_is_closed(receipt) and not _receipt_marks_closure(updates):
            _require_host_closure(ref, host_state)
        return _settle(ref, receipt, updates, remove_receipt_fields)


def reconcile_held_store(
    ref: HeldSeatRef, *, host_state: HostClosure | None = None
) -> HeldStoreSettlement:
    """Settle one shared store and its obsolete lock after the old writers have stopped."""
    with exclusive_file_lock(ref.receipt_path, "role execution receipt"):
        receipt = _current(ref)
        updates = _reconciliation_updates(ref, receipt, host_state)
        _validate_store_parent(ref)
        lock = _store_lock(ref)
        had_lock = _regular_file(lock)
        result = _settle(ref, receipt, updates)
        if had_lock and lock is not None:
            lock.unlink(missing_ok=True)
        return HeldStoreSettlement(
            result.agent_id, result.message_count, result.store_removed, had_lock
        )


def preview_held_store_reconciliation(
    ref: HeldSeatRef, *, host_state: HostClosure | None = None
) -> HeldStoreSettlement:
    """Read the same shared operands and validate them without acquiring a lock."""
    receipt = _current(ref)
    _reconciliation_updates(ref, receipt, host_state)
    path = ref.store_path
    lock = _store_lock(ref)
    store = _settlement_store(ref, path)
    if store is not None:
        _closed_messages(receipt, store, list(store["messages"]), {})
    return HeldStoreSettlement(
        ref.agent_id,
        len(store["messages"]) if store is not None else 0,
        store is not None,
        _regular_file(lock),
    )


def preview_held_store_settlement(ref: HeldSeatRef) -> None:
    """Verify that a recorded task seat's store can be moved out without changing it."""
    receipt = _current(ref)
    store = _settlement_store(ref, ref.store_path)
    if store is not None:
        _closed_messages(receipt, store, list(store["messages"]), {})


def _remove_ids(
    ref: HeldSeatRef,
    message_ids: set[str],
    claim: str | None,
    *,
    clear_claim: bool = True,
) -> list[dict[str, Any]]:
    """Delete exactly the delivered texts; the receipt lines are the caller's to write.

    A multi-part drain keeps its claim across its parts and releases it at the end;
    only the final removal clears it.
    """

    path = _batch_store(ref)
    removed: list[dict[str, Any]] = []
    with open_held_store(ref):
        store = _read_store_file(path, ref.agent_id)
        if claim is not None and store.get("deliveryClaim") != claim:
            return []
        kept = []
        for row in store["messages"]:
            if row.get("id") in message_ids:
                removed.append(row)
            else:
                kept.append(row)
        store["messages"] = kept
        if clear_claim:
            store.pop("deliveryClaim", None)
            store.pop("deliveryClaimAt", None)
            store.pop("deliveryClaimIds", None)
        _write_store_file(path, store)
    return removed


def _release_claim(ref: HeldSeatRef, claim: str) -> None:
    path = _batch_store(ref)
    with open_held_store(ref):
        try:
            store = _read_store_file(path, ref.agent_id)
        except ValueError:
            return
        if store.get("deliveryClaim") == claim:
            store.pop("deliveryClaim", None)
            store.pop("deliveryClaimAt", None)
            store.pop("deliveryClaimIds", None)
            with suppress(OSError):
                _write_store_file(path, store)


def _batch_store(ref: HeldSeatRef) -> Path:
    path = ref.store_path
    if path is None:
        raise HeldSeatClosed(ref.agent_id, "workspace-missing")
    return path


def _reconciliation_updates(
    ref: HeldSeatRef, receipt: Mapping[str, Any], host_state: HostClosure | None
) -> Mapping[str, Any]:
    if receipt_is_closed(receipt):
        return {}
    if host_state == "gone" and receipt_may_create(receipt):
        raise HeldSeatClosed(ref.agent_id, "creation-pending")
    _require_host_closure(ref, host_state)
    return {"status": "stopped", "detail": f"The host confirmed the agent was {host_state}."}


def _require_host_closure(ref: HeldSeatRef, host_state: HostClosure | None) -> None:
    if host_state not in ("archived", "gone"):
        raise AuthorityError(f"The host has not confirmed closure of agent {ref.agent_id}.")


def _store_lock(ref: HeldSeatRef) -> Path | None:
    path = ref.store_path
    return lock_path_for(path) if path is not None else None


def _regular_file(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise AuthorityError(f"The held-message path {path} is not a regular file.")
    return True


def _settlement_store(ref: HeldSeatRef, path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    _validate_store_parent(ref)
    if not _regular_file(path):
        return None
    store = _read_store_file(path, ref.agent_id)
    pending = store.get("pendingLines", [])
    if not isinstance(pending, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("messageId"), str) for row in pending
    ):
        raise AuthorityError(f"The pending message outcomes at {path} are malformed.")
    return store


def _validate_store_parent(ref: HeldSeatRef) -> None:
    path = ref.store_path
    if path is None:
        return
    for directory in (ref.workspace, path.parent.parent, path.parent):
        if directory is not None and directory.is_symlink():
            raise AuthorityError(f"The held-message directory {directory} is a symbolic link.")


def _settle(
    ref: HeldSeatRef,
    receipt: dict[str, Any],
    updates: Mapping[str, Any],
    remove_fields: tuple[str, ...] = (),
) -> HeldStoreSettlement:
    from agents_remember.cli.role_launch_receipts import _write_receipt  # noqa: PLC0415

    path = ref.store_path
    store = _settlement_store(ref, path)
    closures = dict(receipt.get(CLOSURES, {}))
    generation = str(ref.generation)
    if store is None and generation in closures and not updates and not remove_fields:
        return HeldStoreSettlement(ref.agent_id, 0, False, False)

    before = closures.get(generation, {})
    waiting = list(store.get("messages", ())) if store is not None else []
    pending = list(store.get("pendingLines", ())) if store is not None else []
    _merge_pending_lines(receipt, pending)
    waiting = _closed_messages(receipt, store, waiting, before)
    closures[generation] = {
        "closedAt": before.get("closedAt") or _now_iso(),
        "waitingMessages": waiting,
    }
    receipt.update(
        {
            key: value
            for key, value in updates.items()
            if key not in (CLOSURES, GENERATION, "heldWaiting", "heldMessageLines")
        }
    )
    for field in remove_fields:
        receipt.pop(field, None)
    receipt.update({CLOSURES: closures, "heldWaiting": 0})
    _write_receipt(ref.receipt_path, receipt, archive_update="leafArchive" in updates)
    if store is not None and path is not None:
        path.unlink()
    return HeldStoreSettlement(ref.agent_id, len(waiting), store is not None, False)


def _merge_pending_lines(receipt: dict[str, Any], pending: list[Any]) -> None:
    lines = receipt.get("heldMessageLines")
    if not isinstance(lines, list):
        lines = []
        receipt["heldMessageLines"] = lines
    existing = {line.get("messageId") for line in lines if isinstance(line, dict)}
    for row in pending:
        if isinstance(row, dict) and row.get("messageId") not in existing:
            lines.append(row)
            existing.add(row.get("messageId"))
    del lines[: max(0, len(lines) - MAX_HELD_RECORD_LINES)]


def _closed_messages(
    receipt: dict[str, Any],
    store: Mapping[str, Any] | None,
    waiting: list[Any],
    before: Mapping[str, Any],
) -> list[dict[str, Any]]:
    saved = {row["id"]: row for row in before.get("waitingMessages", ())}
    if store is None:
        return list(saved.values())
    claim_ids = store.get("deliveryClaimIds")
    claimed = (
        {item for item in claim_ids if isinstance(item, str)}
        if isinstance(claim_ids, list)
        else set()
    )
    for row in waiting:
        if not isinstance(row.get("id"), str) or not isinstance(row.get("text"), str):
            raise AuthorityError("A waiting message is missing its identity or text.")
        how = _UNCONFIRMED if row["id"] in claimed else _ENDED
        record_lines(receipt, [row], how)
        saved.setdefault(row["id"], {**row, "how": how})
    return list(saved.values())


def preserve_held_closure(
    current: dict[str, Any], receipt: dict[str, Any], prior_generation: int | None
) -> None:
    generation = receipt_generation(receipt)
    expected = generation if prior_generation is None else prior_generation
    agent_id = str(receipt.get("agentId"))
    if (current.get("requestId"), current.get("agentId")) != (
        receipt.get("requestId"),
        receipt.get("agentId"),
    ):
        raise HeldSeatClosed(agent_id, "receipt-moved")
    if receipt_generation(current) != expected:
        raise HeldSeatClosed(agent_id, "generation-changed")
    closures = current.get(CLOSURES)
    if isinstance(closures, dict):
        receipt[CLOSURES] = _merge_closed_generations(closures, receipt.get(CLOSURES, {}))
        if prior_generation is None and str(generation) in closures:
            receipt["heldWaiting"] = 0
            if (
                not _prepared_start_refused(receipt)
                and current.get("status") in _RESTARTABLE_STATUSES
            ):
                receipt["status"] = current["status"]
    if prior_generation is None and isinstance(current.get("retiredBy"), dict):
        keys = (
            ("retiredBy", "retireReason", "canRevive")
            if _prepared_start_refused(receipt)
            else ("status", "retiredBy", "retireReason", "detail", "canRevive")
        )
        for key in keys:
            if key in current:
                receipt[key] = current[key]


def _prepared_start_refused(receipt: Mapping[str, Any]) -> bool:
    mark = receipt.get("leafArchive")
    return (
        receipt.get("status") == "rejected"
        and isinstance(mark, dict)
        and mark.get("startRefused") is True
    )


def _merge_closed_generations(
    stored: Mapping[str, Any], incoming: Mapping[str, Any]
) -> dict[str, Any]:
    merged = dict(incoming)
    for generation, existing in stored.items():
        updated = incoming.get(generation)
        if not isinstance(updated, dict):
            merged[generation] = existing
            continue
        rows = {row["id"]: row for row in existing["waitingMessages"]}
        for row in updated["waitingMessages"]:
            rows.setdefault(row["id"], row)
        merged[generation] = {**existing, "waitingMessages": list(rows.values())}
    return merged


def begin_held_generation(
    ref: HeldSeatRef,
    *,
    receipt_updates: Mapping[str, Any],
    host_state: HostClosure | None = None,
) -> HeldSeatRef:
    """Settle the prior generation and reserve the next generation for an admitted restart."""
    from agents_remember.cli.role_launch_receipts import (  # noqa: PLC0415
        _leaf_is_closing,
        _write_receipt,
    )

    with exclusive_file_lock(ref.receipt_path, "role execution receipt"):
        receipt = _current(ref)
        if (
            receipt.get("status") not in _RESTARTABLE_STATUSES
            or receipt_is_explicitly_retired(receipt)
            or _leaf_is_closing(receipt)
        ):
            raise HeldSeatClosed(ref.agent_id, "closed")
        updates = _reconciliation_updates(ref, receipt, host_state)
        _settle(ref, receipt, updates)
        receipt.update(receipt_updates)
        receipt[GENERATION] = ref.generation + 1
        receipt.pop("leafCreateEntered", None)
        receipt.pop("retiredBy", None)
        receipt.pop("retireReason", None)
        _write_receipt(ref.receipt_path, receipt, prior_generation=ref.generation)
        return held_seat_ref(ref.receipt_path, receipt)
