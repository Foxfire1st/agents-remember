"""Leaf archive attempts and settlement on the existing launch receipts.

The pending archive ID remains the debt head. A predecessor is settled first, then the receipt's
own minted ID. The bounded settlement map is evidence, not another queue; host failure leaves the
head owed. A prepared start on a closing leaf records its refusal on that same receipt; no
message, report, artifact or task/review/Git state is rewritten here.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from agents_remember.application.role_launch_context import LEAF_ROLES
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure, bridge_call
from agents_remember.cli.role_launch_receipts import (
    EXECUTIONS_DIRECTORY,
    _leaf_is_closing,
    _now_iso,
    _read_receipt,
    _same_request_receipt,
    _write_receipt,
)
from agents_remember.errors import AuthorityError
from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
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
    return receipt.get("status") in {"starting", "unknown"} and isinstance(
        receipt.get("replayRequest"), dict
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
            and current.get("leafCreateEntered") is True
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
        self._save(path, receipt, self._debt(receipt, outcomes), pending=debt_head)

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
        self._save(path, receipt, self._debt(receipt, outcomes), pending=pending)
        current = _read_receipt(path)
        if current is None or current.get("requestId") != receipt.get("requestId"):
            self.report["leftAlone"].append(
                {"recordPath": str(path), "reason": "The prepared archive receipt was replaced."}
            )
            return None
        return current

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

    def _debt(self, receipt: dict[str, Any], outcomes: dict[str, Any]) -> dict[str, Any]:
        return {
            "closing": True,
            "creationPending": _pending_creation(receipt),
            "taskDocumentRef": self.task_ref,
            "contractPath": str(self.contract.contract_path),
            "outcomes": outcomes,
        }

    def _settle_target(
        self,
        path: Path,
        receipt: dict[str, Any],
        agent_id: str,
        outcomes: dict[str, Any],
        debt_head: str | None,
    ) -> str | None:
        row = {"agentId": agent_id, "role": receipt["role"], "recordPath": str(path)}
        prior = outcomes.get(agent_id, {})
        pending_creation = agent_id == receipt["agentId"] and _pending_creation(receipt)
        if prior.get("state") == "archived" or (
            prior.get("state") == "gone" and not pending_creation
        ):
            key = "alreadyArchived" if prior["state"] == "archived" else "gone"
            self.report[key].append(row)
            return debt_head
        self._save(path, receipt, self._debt(receipt, outcomes), pending=debt_head or agent_id)
        outcome = self._archive(agent_id)
        if pending_creation and outcome["state"] == "gone":
            outcome = _pending_archive("The leaf's prepared creation has not been settled.")
        if outcome["state"] == "owed":
            debt_head = debt_head or agent_id
        outcomes[agent_id] = {**outcome, "at": _now_iso()}
        self._save(path, receipt, self._debt(receipt, outcomes), pending=debt_head)
        key = "alreadyArchived" if outcome.get("alreadyArchived") else outcome["state"]
        self.report[key].append({**row, **outcome})
        return debt_head

    def _archive(self, agent_id: str) -> dict[str, Any]:
        return _archive_agent(self.config, agent_id, self.deadline)

    def _save(
        self, path: Path, receipt: dict[str, Any], archive: dict[str, Any], *, pending: str | None
    ) -> None:
        try:
            with exclusive_file_lock(path, "role execution receipt"):
                current = _same_request_receipt(path, receipt)
                saved = current.get("leafArchive", {})
                outcomes = {**saved.get("outcomes", {}), **archive["outcomes"]}
                for target, row in saved.get("outcomes", {}).items():
                    if row.get("state") == "archived" or (
                        row.get("state") == "gone"
                        and saved.get("creationPending") is False
                        and outcomes.get(target, {}).get("state") != "archived"
                    ):
                        outcomes[target] = row
                archive = {**archive, **saved, "outcomes": outcomes, "closing": True}
                owed = [target for target, row in outcomes.items() if row.get("state") == "owed"]
                head = current.get("pendingArchiveAgentId")
                if head in owed:
                    pending = head
                elif owed:
                    pending = owed[0]
                elif pending in outcomes:
                    pending = None
                if (
                    current.get("leafArchive") == archive
                    and current.get("pendingArchiveAgentId") == pending
                ):
                    return
                current["leafArchive"] = archive
                if pending is None:
                    current.pop("pendingArchiveAgentId", None)
                else:
                    current["pendingArchiveAgentId"] = pending
                _write_receipt(path, current, archive_update=True)
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

    def archive(self, contract: WorktreeContract) -> dict[str, Any]:
        report = empty_archive_report()
        resolved = resolve_terminal_leaf_doc(contract.task_root, contract.leaf_id)
        if resolved is None:
            return {**report, "summary": "No canonical leaf document names a role selection."}
        task_path, _document = resolved
        task_ref = {
            "repository": contract.repo_name,
            "path": task_path.relative_to(
                contract.coordination_root / "tasks" / contract.repo_name
            ).as_posix(),
        }
        directory = contract.task_root / "notes" / "reports" / EXECUTIONS_DIRECTORY
        attempt = ArchiveAttempt(
            self.config, contract, task_ref, time.monotonic() + ARCHIVE_BUDGET_SECONDS, report
        )
        prepared = []
        for path in receipt_paths(directory):
            try:
                require_within_coordination(self.config, str(path), "launch receipt")
                receipt = _read_receipt(path)
            except (OSError, ValueError, HTTPException) as error:
                reason = str(error.detail) if isinstance(error, HTTPException) else str(error)
                report["leftAlone"].append({"recordPath": str(path), "reason": reason})
                continue
            if receipt is not None and leaf_receipt_ref(receipt) == task_ref:
                current = attempt.prepare(path, receipt)
                if current is not None:
                    prepared.append((path, current))
        # Mark every recorded start before spending any of the bounded host budget.
        for path, receipt in prepared:
            attempt.record(path, receipt)
        summary = (
            "No role agents were recorded for this leaf."
            if not any(report.values())
            else "Recorded leaf agent archive attempts completed; owed targets remain in their launch receipts."
        )
        return {**report, "summary": summary}
