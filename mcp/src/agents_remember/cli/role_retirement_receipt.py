"""Retirement attribution and held-message settlement on an existing launch receipt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agents_remember.cli import role_receipt_files
from agents_remember.cli.paseo_launch import LaunchOutcome
from agents_remember.cli.role_held_store import HostClosure, held_seat_ref, settle_held_store
from agents_remember.cli.role_receipt_files import _now_iso, _read_receipt
from agents_remember.controlplane.held_store_layout import MAX_HELD_RECORD_LINES
from agents_remember.errors import AuthorityError
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.worktrees.role_reports import (
    RoleReportCollectionError,
    close_execution_role_folders,
)


@dataclass(frozen=True, slots=True)
class Retirement:
    caller: str
    caller_agent_id: str | None
    reason: str
    detail: str
    replacement_request_id: str | None = None

    def fields(self) -> dict[str, str | None]:
        fields = {
            "caller": self.caller,
            "callerAgentId": self.caller_agent_id,
            "reason": self.reason,
            "detail": self.detail,
        }
        if self.replacement_request_id is not None:
            fields["replacementRequestId"] = self.replacement_request_id
        return fields


@dataclass(frozen=True, slots=True)
class RetirementUpdate:
    state: Literal[
        "held", "recorded", "cleared", "already-retired", "pending-conflict", "receipt-changed"
    ]
    receipt: dict[str, Any]


def hold_retirement(path: Path, receipt: dict[str, Any], intent: Retirement) -> RetirementUpdate:
    """Record the archive's owner before asking the host to change the agent."""
    with exclusive_file_lock(path, "role execution receipt"):
        current, outcome = _retirement_state(path, receipt, intent)
        if outcome is not None:
            return outcome
        if "retireIntent" not in current:
            current["retireIntent"] = {**intent.fields(), "at": _now_iso()}
            role_receipt_files._replace_receipt_bytes(path, current)
        return RetirementUpdate("held", current)


def clear_retirement(path: Path, receipt: dict[str, Any], intent: Retirement) -> RetirementUpdate:
    """Withdraw a matching intent after the host definitively refused the archive."""
    with exclusive_file_lock(path, "role execution receipt"):
        current, outcome = _retirement_state(path, receipt, intent)
        if outcome is not None:
            return outcome
        if "retireIntent" in current:
            del current["retireIntent"]
            role_receipt_files._replace_receipt_bytes(path, current)
        return RetirementUpdate("cleared", current)


def settle_retirement(
    path: Path,
    receipt: dict[str, Any],
    intent: Retirement,
    *,
    host_state: HostClosure | None = None,
) -> RetirementUpdate:
    """Record the confirmed archive and settle the held messages for that exact seat."""
    with exclusive_file_lock(path, "role execution receipt"):
        current, outcome = _retirement_state(path, receipt, intent)
        if outcome is not None:
            return outcome
        updates = _retirement_updates(current, intent)
        settle_held_store(
            held_seat_ref(path, current),
            host_state=host_state,
            receipt_updates=updates,
            remove_receipt_fields=("retireIntent",),
        )
        settled = _read_receipt(path)
        return RetirementUpdate("recorded", settled if settled is not None else current)


def replacement_request_of(receipt: dict[str, Any]) -> str | None:
    pending = receipt.get("retireIntent")
    request_id = pending.get("replacementRequestId") if isinstance(pending, dict) else None
    return request_id if isinstance(request_id, str) else None


def finish_replacing_predecessor(
    path: Path, receipt: dict[str, Any], launch_call: dict[str, Any], outcome: LaunchOutcome
) -> LaunchOutcome:
    predecessor_id = launch_call.get("archiveAgentId")
    if not outcome.predecessor_settled or not isinstance(predecessor_id, str):
        return outcome
    try:
        settle_confirmed_replacement(path, receipt, predecessor_id)
    except OSError as error:
        return LaunchOutcome(
            kind="no-answer",
            code="predecessor_retirement_write_failed",
            message=f"The predecessor archive was confirmed but could not be recorded: {error}",
            predecessor_settled=False,
        )
    predecessor = receipt.get("predecessorReports")
    if isinstance(predecessor, dict):
        receipt["predecessorSettled"] = True
        try:
            close_execution_role_folders(predecessor)
        except RoleReportCollectionError as error:
            # The host already answered: retain its actual result and the source folder,
            # rather than misreporting a created agent as a pre-creation refusal.
            receipt["predecessorCollectionError"] = str(error)
    return outcome


def settle_confirmed_replacement(
    path: Path, successor: dict[str, Any], predecessor_id: str
) -> None:
    """Attribute an acknowledged archive to the exact replacement on its predecessor."""
    predecessor = successor.get("predecessorReports")
    if not isinstance(predecessor, dict) or not isinstance(
        request_id := predecessor.get("requestId"), str
    ):
        return
    history = path.parent / "history" / f"{request_id}.json"
    if history.parent != path.parent / "history":
        raise AuthorityError("The predecessor request does not name a receipt in history.")
    receipt = _read_receipt(history)
    if receipt is None or replacement_request_of(receipt) is None:
        return
    successor_id = successor.get("requestId")
    if (
        receipt.get("requestId") != request_id
        or receipt.get("agentId") != predecessor_id
        or replacement_request_of(receipt) != successor_id
    ):
        raise AuthorityError("The archived predecessor does not match the saved replacing start.")
    pending = receipt["retireIntent"]
    intent = Retirement(
        pending["caller"],
        pending["callerAgentId"],
        pending["reason"],
        pending["detail"],
        successor_id,
    )
    result = settle_retirement(history, receipt, intent)
    if result.state not in {"recorded", "already-retired"}:
        raise AuthorityError("The predecessor's retirement intent changed before settlement.")


def _retirement_updates(receipt: dict[str, Any], intent: Retirement) -> dict[str, Any]:
    """Build retirement fields on a fresh snapshot whose receipt lock the caller owns."""
    pending = receipt.get("retireIntent")
    now = _now_iso()
    updates: dict[str, Any] = {
        "retiredBy": {
            "caller": intent.caller,
            "callerAgentId": intent.caller_agent_id,
            "reason": intent.reason,
            "at": pending["at"] if isinstance(pending, dict) else now,
        },
        "retireReason": intent.reason,
        "canRevive": False,
        "updatedAt": now,
    }
    if receipt.get("status") != "rejected" and not _leaf_start_needs_reconciliation(receipt):
        updates.update(status="stopped", detail=intent.detail, terminalObservedAt=now)
    return updates


def _leaf_start_needs_reconciliation(receipt: dict[str, Any]) -> bool:
    mark = receipt.get("leafArchive")
    return isinstance(mark, dict) and (
        mark.get("creationPending") is True or mark.get("startRefused") is True
    )


def preserve_retirement(current: dict[str, Any], receipt: dict[str, Any]) -> None:
    """An ordinary writer cannot reopen a retired seat or resurrect a cleared intent."""
    if isinstance(current.get("retiredBy"), dict):
        receipt.pop("retireIntent", None)
        for key in ("retiredBy", "retireReason", "canRevive", "heldWaiting", "terminalObservedAt"):
            _copy_field(current, receipt, key)
        if not _prepared_start_refused(receipt):
            for key in ("status", "detail"):
                _copy_field(current, receipt, key)
        _merge_held_lines(current, receipt)
        return
    if not isinstance(receipt.get("retiredBy"), dict):
        _copy_field(current, receipt, "retireIntent")


def _prepared_start_refused(receipt: dict[str, Any]) -> bool:
    mark = receipt.get("leafArchive")
    return (
        receipt.get("status") == "rejected"
        and isinstance(mark, dict)
        and mark.get("startRefused") is True
    )


def _merge_held_lines(current: dict[str, Any], receipt: dict[str, Any]) -> None:
    committed = current.get("heldMessageLines")
    if not isinstance(committed, list):
        return
    incoming = receipt.get("heldMessageLines")
    have = {line["messageId"]: line for line in committed if isinstance(line, dict)}
    if isinstance(incoming, list):
        for line in incoming:
            if isinstance(line, dict):
                have.setdefault(line["messageId"], line)
    receipt["heldMessageLines"] = list(have.values())[-MAX_HELD_RECORD_LINES:]


def _copy_field(current: dict[str, Any], receipt: dict[str, Any], key: str) -> None:
    if key in current:
        receipt[key] = current[key]
    else:
        receipt.pop(key, None)


def _retirement_state(
    path: Path, receipt: dict[str, Any], intent: Retirement
) -> tuple[dict[str, Any], RetirementUpdate | None]:
    current = _read_receipt(path)
    if current is None or any(
        current.get(key) != receipt.get(key) for key in ("requestId", "agentId")
    ):
        return receipt, RetirementUpdate("receipt-changed", receipt)
    if isinstance(current.get("retiredBy"), dict):
        return current, RetirementUpdate("already-retired", current)
    pending = current.get("retireIntent")
    if pending is not None and (
        not isinstance(pending, dict)
        or any(pending.get(key) != value for key, value in intent.fields().items())
    ):
        return current, RetirementUpdate("pending-conflict", current)
    return current, None
