"""Task seat archives and settlement on the existing launch receipts.

The pending archive ID remains the debt head. A predecessor is settled first, then the receipt's
own minted ID. Host failure leaves the head owed. A closing seat's receipt also records its
waiting messages before its store is removed.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.cli.paseo_status import AgentReading, _reading
from agents_remember.cli.role_held_store import (
    held_seat_ref,
    preview_held_store_settlement,
    settle_held_store,
)
from agents_remember.cli.role_launch_receipts import (
    _leaf_is_closing,
    _now_iso,
    _read_receipt,
    _same_request_receipt,
    _write_receipt,
    read_enumerated_receipt,
)
from agents_remember.controlplane.held_store_discovery import EXECUTIONS_DIRECTORY
from agents_remember.controlplane.held_store_layout import _read_store_file, product_held_dir
from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import LEAF_ROLES
from agents_remember.tasks.leaf_doc import resolve_terminal_leaf_doc
from agents_remember.worktrees.worktree_contract import WorktreeContract

ARCHIVE_BUDGET_SECONDS = 8.0
ARCHIVE_CALL_SECONDS = 2.0


def receipt_paths(directory: Path) -> Iterator[Path]:
    """Only current and historical launch records, never message-binding projections."""
    yield from directory.glob("*.json")
    yield from (directory / "history").glob("*.json")


def empty_archive_report() -> dict[str, Any]:
    return {"archived": [], "alreadyArchived": [], "gone": [], "owed": [], "leftAlone": []}


def _uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if str(uuid.UUID(value)) == value else None
    except ValueError:
        return None


def leaf_receipt_ref(receipt: dict[str, Any]) -> dict[str, str] | None:
    selection = receipt.get("selection")
    if not isinstance(selection, dict) or receipt.get("role") not in LEAF_ROLES:
        return None
    ref = selection.get("taskDocumentRef")
    if selection.get("role") != receipt.get("role") or not isinstance(ref, dict):
        return None
    return (
        ref if isinstance(ref.get("repository"), str) and isinstance(ref.get("path"), str) else None
    )


def _pending_creation(receipt: dict[str, Any]) -> bool:
    saved = receipt.get("leafArchive")
    if isinstance(saved, dict) and isinstance(saved.get("creationPending"), bool):
        return saved["creationPending"]
    return receipt.get("heldOpenPending") is True or (
        receipt.get("status") in {"starting", "unknown"}
        and receipt.get("leafCreateEntered") is True
    )


def _archive_agent(config: McpRuntimeConfig, agent_id: str, deadline: float) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"state": "owed", "reason": "The bounded archive budget was exhausted."}
    try:
        reply = bridge_call(
            config,
            "agent-archive",
            {"agentId": agent_id},
            timeout_seconds=min(remaining, ARCHIVE_CALL_SECONDS),
        )
    except PaseoBridgeFailure as error:
        return {"state": "owed", "reason": str(error), "code": error.code}
    if reply.get("found") is False:
        return {"state": "gone", "reason": "The host no longer knows this agent."}
    if reply.get("archived") is True:
        return {"state": "archived", "alreadyArchived": reply.get("alreadyArchived") is True}
    return {"state": "owed", "reason": "The host did not confirm the archive."}


def _pending_archive(reason: str) -> dict[str, Any]:
    return {"state": "owed", "reason": reason, "code": "leaf_creation_pending"}


def _confirmed_archive(reading: AgentReading) -> dict[str, Any] | None:
    if reading.reachable and reading.archived:
        return {"state": "archived", "alreadyArchived": True}
    if reading.reachable and not reading.found:
        return {"state": "gone", "reason": "The host no longer knows this agent."}
    return None


def refuse_retired_start(
    config: McpRuntimeConfig,
    path: Path,
    receipt: dict[str, Any],
    *,
    creation_returned: bool,
) -> dict[str, Any]:
    """Refuse the saved start, settling only its own minted target on the same receipt."""
    with exclusive_file_lock(path, "role execution receipt"):
        current = _same_request_receipt(path, receipt)
        if not _leaf_is_closing(current):
            raise AuthorityError("The refused start lost its leaf closing mark.")
        archive = dict(current["leafArchive"])
        pending = (
            archive.get("creationPending") is True
            and (current.get("leafCreateEntered") is True or current.get("heldOpenPending") is True)
            and not creation_returned
        )
        archive.update(startRefused=True, creationPending=pending)
        current["leafArchive"] = archive
        _write_receipt(path, current, archive_update=True)
        prior = archive.get("outcomes", {}).get(current["agentId"], {})
    if prior.get("state") == "archived":
        outcome = prior
    else:
        outcome = _archive_agent(
            config, current["agentId"], time.monotonic() + ARCHIVE_BUDGET_SECONDS
        )
        if pending and outcome["state"] == "gone":
            outcome = _pending_archive("An entered leaf creation is still unconfirmed.")
    with exclusive_file_lock(path, "role execution receipt"):
        current = _same_request_receipt(path, receipt)
        archive = dict(current["leafArchive"])
        outcomes = dict(archive.get("outcomes", {}))
        if outcomes.get(current["agentId"], {}).get("state") != "archived":
            outcomes[current["agentId"]] = {**outcome, "at": _now_iso()}
        archive.update(outcomes=outcomes, startRefused=True)
        current["leafArchive"] = archive
        own_owed = outcomes[current["agentId"]]["state"] == "owed"
        if outcomes[current["agentId"]]["state"] in {"archived", "gone"}:
            current["hostAgentExists"] = outcomes[current["agentId"]]["state"] == "archived"
        # Keep the saved call for a same-ID reconciliation of archive uncertainty, never creation.
        current.update(
            status="unknown" if own_owed else "rejected",
            detail="The leaf is closing; this prepared start is refused. "
            + ("Its agent archive remains owed." if own_owed else "Its agent is archived or gone."),
            canRevive=False,
            updatedAt=_now_iso(),
        )
        if not own_owed:
            current.pop("replayRequest", None)
        head = current.get("pendingArchiveAgentId")
        owed = [target for target, row in outcomes.items() if row.get("state") == "owed"]
        if owed:
            current["pendingArchiveAgentId"] = head if head in owed else owed[0]
        else:
            current.pop("pendingArchiveAgentId", None)
        _write_receipt(path, current, archive_update=True)
        return current


@dataclass
class ArchiveAttempt:
    config: McpRuntimeConfig
    contract: WorktreeContract
    task_ref: dict[str, str]
    deadline: float
    report: dict[str, Any] = field(default_factory=empty_archive_report)

    def record(self, path: Path, receipt: dict[str, Any]) -> None:
        prepared = self.prepare(path, receipt)
        if prepared is None:
            return
        receipt = prepared
        recorded = self._recorded_targets(path, receipt)
        if recorded is None:
            return
        targets, outcomes = recorded
        debt_head: str | None = None
        for agent_id in targets:
            debt_head = self._settle_target(path, receipt, agent_id, outcomes, debt_head)
        self._save(path, receipt, outcomes, pending=debt_head)

    def prepare(self, path: Path, receipt: dict[str, Any]) -> dict[str, Any] | None:
        recorded = self._recorded_targets(path, receipt)
        if recorded is None:
            return None
        targets, outcomes = recorded
        pending = next(
            (
                target
                for target in targets
                if outcomes.get(target, {}).get("state") not in {"archived", "gone"}
            ),
            None,
        )
        self._save(path, receipt, outcomes, pending=pending)
        current = _read_receipt(path)
        if (
            current is None
            or current.get("requestId") != receipt.get("requestId")
            or not _leaf_is_closing(current)
        ):
            self.report["leftAlone"].append(
                {"recordPath": str(path), "reason": "The prepared archive receipt was replaced."}
            )
            return None
        return current

    def preview(self, path: Path, receipt: dict[str, Any]) -> None:
        recorded = self._recorded_targets(path, receipt)
        if recorded is None:
            return
        targets, outcomes = recorded
        for agent_id in targets:
            state = outcomes.get(agent_id, {}).get("state")
            pending_creation = agent_id == receipt["agentId"] and _pending_creation(receipt)
            if state == "archived" or (state == "gone" and not pending_creation):
                continue
            if (
                state == "owed"
                or agent_id == receipt.get("pendingArchiveAgentId")
                or _leaf_is_closing(receipt)
            ):
                self._preview_debt(path, receipt, agent_id, pending_creation=pending_creation)

    def _preview_debt(
        self, path: Path, receipt: dict[str, Any], agent_id: str, *, pending_creation: bool
    ) -> None:
        reading = self._observe_agent(agent_id)
        settled = _confirmed_archive(reading)
        if settled is not None:
            if settled["state"] == "archived" or not pending_creation:
                return
            outcome = _pending_archive("The leaf's prepared creation has not been settled.")
        else:
            reason = (
                "The host still reports the seat live."
                if reading.reachable
                else reading.unreachable_reason or "The host did not confirm the archive."
            )
            outcome = {"state": "owed", "reason": reason}
        self.report["owed"].append({**self._target_row(path, receipt, agent_id), **outcome})

    def _recorded_targets(
        self, path: Path, receipt: dict[str, Any]
    ) -> tuple[list[str], dict[str, Any]] | None:
        context = receipt.get("arMcpContext")
        scope = context.get("taskContext") if isinstance(context, dict) else None
        if isinstance(scope, dict) and scope.get("task_document_ref") != self.task_ref:
            self.report["leftAlone"].append(
                {
                    "recordPath": str(path),
                    "reason": "The launch selection conflicts with its task-scoped reader binding.",
                }
            )
            return
        own = _uuid(receipt.get("agentId"))
        if own is None:
            self.report["leftAlone"].append(
                {"recordPath": str(path), "reason": "The launch record has no minted agent ID."}
            )
            return
        pending_raw = receipt.get("pendingArchiveAgentId")
        pending = _uuid(pending_raw)
        if pending_raw is not None and pending is None:
            self.report["leftAlone"].append(
                {"recordPath": str(path), "reason": "The pending archive ID is not a minted UUID."}
            )
            return
        targets = [pending, own] if pending and pending != own else [own]
        archive = receipt.get("leafArchive")
        saved = archive.get("outcomes", {}) if isinstance(archive, dict) else {}
        if (
            not isinstance(saved, dict)
            or len(saved) > 2
            or any(not isinstance(value, dict) for value in saved.values())
        ):
            self.report["leftAlone"].append(
                {"recordPath": str(path), "reason": "The archive settlement record is malformed."}
            )
            return
        if any(_uuid(agent_id) is None for agent_id in saved) or len(set(targets) | set(saved)) > 2:
            self.report["leftAlone"].append(
                {
                    "recordPath": str(path),
                    "reason": "The receipt has conflicting archive targets; only its own agent and one predecessor may be named.",
                }
            )
            return
        outcomes = dict(saved)
        targets.extend(agent_id for agent_id in saved if agent_id not in targets)
        return targets, outcomes

    @staticmethod
    def _target_row(path: Path, receipt: dict[str, Any], agent_id: str) -> dict[str, Any]:
        return {"agentId": agent_id, "role": receipt["role"], "recordPath": str(path)}

    def _settle_target(
        self,
        path: Path,
        receipt: dict[str, Any],
        agent_id: str,
        outcomes: dict[str, Any],
        debt_head: str | None,
    ) -> str | None:
        row = self._target_row(path, receipt, agent_id)
        prior = outcomes.get(agent_id, {})
        pending_creation = agent_id == receipt["agentId"] and _pending_creation(receipt)
        if prior.get("state") == "archived" or (
            prior.get("state") == "gone" and not pending_creation
        ):
            key = "alreadyArchived" if prior["state"] == "archived" else "gone"
            self.report[key].append(row)
            return debt_head
        self._save(path, receipt, outcomes, pending=debt_head or agent_id)
        outcome = self._archive(agent_id)
        if outcome["state"] == "owed":
            outcome = self._retry_archive(agent_id, outcome)
        if pending_creation and outcome["state"] == "gone":
            outcome = _pending_archive("The leaf's prepared creation has not been settled.")
        if outcome["state"] == "owed":
            debt_head = debt_head or agent_id
        outcomes[agent_id] = {**outcome, "at": _now_iso()}
        self._save(path, receipt, outcomes, pending=debt_head)
        key = "alreadyArchived" if outcome.get("alreadyArchived") else outcome["state"]
        self.report[key].append({**row, **outcome})
        return debt_head

    def _archive(self, agent_id: str) -> dict[str, Any]:
        return _archive_agent(self.config, agent_id, self.deadline)

    def _retry_archive(self, agent_id: str, owed: dict[str, Any]) -> dict[str, Any]:
        if self.deadline <= time.monotonic():
            return owed
        reading = self._observe_agent(agent_id)
        confirmed = _confirmed_archive(reading)
        if confirmed is not None:
            return confirmed
        retry = self._archive(agent_id)
        if retry["state"] == "owed" and reading.reachable:
            return {**retry, "reason": "The host still reports the seat live."}
        return retry

    def _observe_agent(self, agent_id: str) -> AgentReading:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            return AgentReading(
                reachable=False, unreachable_reason="The bounded archive budget was exhausted."
            )
        try:
            reply = bridge_call(
                self.config,
                "agent-state",
                {"agentId": agent_id},
                timeout_seconds=min(remaining, ARCHIVE_CALL_SECONDS),
            )
        except PaseoBridgeFailure as error:
            return AgentReading(reachable=False, unreachable_reason=str(error))
        return _reading(reply, agent_id)

    def _save(
        self, path: Path, receipt: dict[str, Any], outcomes: dict[str, Any], *, pending: str | None
    ) -> None:
        try:
            with exclusive_file_lock(path, "role execution receipt"):
                current = _same_request_receipt(path, receipt)
                saved = current.get("leafArchive", {})
                outcomes = {**saved.get("outcomes", {}), **outcomes}
                for target, row in saved.get("outcomes", {}).items():
                    if row.get("state") == "archived" or (
                        row.get("state") == "gone"
                        and saved.get("creationPending") is False
                        and outcomes.get(target, {}).get("state") != "archived"
                    ):
                        outcomes[target] = row
                archive = {
                    "taskDocumentRef": self.task_ref,
                    "contractPath": str(self.contract.contract_path),
                    **saved,
                    "closing": True,
                    # Native creation may have entered since the receipt was enumerated.
                    "creationPending": _pending_creation(current),
                    "outcomes": outcomes,
                }
                owed = [target for target, row in outcomes.items() if row.get("state") == "owed"]
                head = current.get("pendingArchiveAgentId")
                if head in owed:
                    pending = head
                elif owed:
                    pending = owed[0]
                elif pending in outcomes:
                    pending = None
                unchanged = (
                    current.get("leafArchive") == archive
                    and current.get("pendingArchiveAgentId") == pending
                )
                if not unchanged:
                    current["leafArchive"] = archive
                    if pending is None:
                        current.pop("pendingArchiveAgentId", None)
                    else:
                        current["pendingArchiveAgentId"] = pending
                    _write_receipt(path, current, archive_update=True)
                settle_held_store(held_seat_ref(path, current))
        except (OSError, ValueError, HTTPException) as error:
            reason = str(error.detail) if isinstance(error, HTTPException) else str(error)
            self.report["leftAlone"].append(
                {
                    "recordPath": str(path),
                    "reason": f"Archive debt could not be persisted: {reason}",
                }
            )


@dataclass(frozen=True)
class LeafAgentArchive:
    config: McpRuntimeConfig

    def archive(
        self,
        contract: WorktreeContract,
        *,
        dry_run: bool = False,
        check_census: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        report = empty_archive_report()
        task_ref = _terminal_leaf_ref(contract)
        if contract.kind == "leaf" and task_ref is None:
            _check_unowned_stores(contract.worktree_group, set(), report)
            return {**report, "summary": "No canonical leaf document names a role selection."}

        records = list(self._records(contract, task_ref, report))
        known = {str(receipt.get("agentId")) for _, receipt, _ in records}
        _check_unowned_stores(contract.worktree_group, known, report)
        if check_census is not None:
            check_census()
        if not records and not report["leftAlone"]:
            return {**report, "summary": "No role agents were recorded for this task."}
        deadline = time.monotonic() + ARCHIVE_BUDGET_SECONDS
        prepared = []
        identities: set[str] = set()
        for path, receipt, selection in records:
            try:
                ref = held_seat_ref(path, receipt)
                if ref.agent_id in identities:
                    raise AuthorityError(f"Agent {ref.agent_id} has more than one launch receipt.")
                identities.add(ref.agent_id)
                preview_held_store_settlement(ref)
                attempt = ArchiveAttempt(self.config, contract, selection, deadline, report)
                if dry_run:
                    attempt.preview(path, receipt)
                    continue
                current = attempt.prepare(path, receipt)
                if current is not None:
                    prepared.append((path, current, attempt))
            except (OSError, ValueError, HTTPException) as error:
                _record_blocker(report, path, error, receipt.get("agentId"))

        # All known seats are fenced before the first archive request reaches the host.
        for path, receipt, attempt in prepared:
            attempt.record(path, receipt)
        return {
            **report,
            "summary": (
                "Recorded task seats and their message stores were inspected."
                if dry_run
                else "Recorded task seat archive attempts completed."
            ),
        }

    def _records(
        self,
        contract: WorktreeContract,
        task_ref: dict[str, str] | None,
        report: dict[str, Any],
    ) -> Iterator[tuple[Path, dict[str, Any], dict[str, str]]]:
        directory = contract.task_root / "notes" / "reports" / EXECUTIONS_DIRECTORY
        for path in receipt_paths(directory):
            try:
                require_within_coordination(self.config, str(path), "launch receipt")
                receipt = read_enumerated_receipt(path)
                if receipt is None:
                    continue
                selected = leaf_receipt_ref(receipt)
                if selected is not None and _owns_seat(contract, task_ref, receipt, selected):
                    yield path, receipt, selected
            except (OSError, ValueError, HTTPException) as error:
                _record_blocker(report, path, error)


def _terminal_leaf_ref(contract: WorktreeContract) -> dict[str, str] | None:
    if contract.kind != "leaf":
        return None
    resolved = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
    if resolved is None:
        return None
    task_path, _document = resolved
    return {
        "repository": contract.repo_name,
        "path": task_path.relative_to(
            contract.coordination_root / "tasks" / contract.repo_name
        ).as_posix(),
    }


def _owns_seat(
    contract: WorktreeContract,
    task_ref: dict[str, str] | None,
    receipt: dict[str, Any],
    selected: dict[str, str],
) -> bool:
    workspace = receipt.get("workspace")
    if contract.kind == "leaf":
        lifecycle_id = workspace.get("lifecycleId") if isinstance(workspace, dict) else None
        different_lifecycle = (
            isinstance(lifecycle_id, str)
            and contract.lifecycle_id is not None
            and lifecycle_id != contract.lifecycle_id
        )
        return selected == task_ref and not different_lifecycle
    path = workspace.get("path") if isinstance(workspace, dict) else None
    return isinstance(path, str) and Path(path).is_relative_to(contract.worktree_group)


def _record_blocker(
    report: dict[str, Any], path: Path, error: Exception, agent_id: Any = None
) -> None:
    reason = str(error.detail) if isinstance(error, HTTPException) else str(error)
    report["leftAlone"].append(
        {
            "recordPath": str(path),
            "reason": reason,
            **({"agentId": agent_id} if isinstance(agent_id, str) else {}),
        }
    )


def _check_unowned_stores(group: Path, known: set[str], report: dict[str, Any]) -> None:
    folder = product_held_dir(group)
    if group.is_symlink() or folder.parent.is_symlink() or folder.is_symlink():
        return
    for path in sorted(folder.glob("*.json")):
        agent_id = _uuid(path.stem)
        if agent_id is None or agent_id in known or path.is_symlink():
            continue
        try:
            if not path.is_file():
                raise AuthorityError("The unowned held-message path is not a regular file.")
            store = _read_store_file(path, agent_id)
            if store["messages"] or store.get("pendingLines") or store.get("deliveryClaim"):
                raise AuthorityError(
                    f"The waiting store of agent {agent_id} has no scoped launch receipt."
                )
        except (OSError, ValueError) as error:
            _record_blocker(report, path, error, agent_id)
