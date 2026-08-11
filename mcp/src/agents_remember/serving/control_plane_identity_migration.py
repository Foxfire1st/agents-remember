"""One-shot durable-log migration to task-document+role seat identity."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.controlplane.agent_notifier_signals import (
    AGENT_NOTIFIER_SIGNAL_OWNERSHIP,
    AGENT_NOTIFIER_SIGNAL_SCHEMA,
    AgentNotifierSignalCooldownStore,
    AgentNotifierSignalRecord,
)
from agents_remember.controlplane.durable_store import (
    EXPECTATION_ROW_OWNERSHIP,
    OPERATOR_INBOX_OWNERSHIP,
    migrate_jsonl_records,
)
from agents_remember.controlplane.expectation_rows import (
    EXPECTATION_ROW_SCHEMA,
    ExpectationRow,
    ExpectationRowStore,
)
from agents_remember.controlplane.operator_inbox_records import (
    OPERATOR_INBOX_RECORD_SCHEMA,
    OperatorInboxEntry,
)
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.serving.terminal_catalog_migration import (
    legacy_leaf_document_ref,
    task_ref_for_role,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError, TaskDocumentTopology

_INBOX_V1 = "ar-operator-inbox-entry/v1"
_EXPECTATION_V1 = "ar-expectation-row/v1"
_SIGNAL_V1 = "ar-supervisor-signal/v1"


@dataclass(frozen=True)
class IdentityMigrationContext:
    coordination_root: Path
    topology: TaskDocumentTopology
    catalog: TerminalCatalog


def migrate_control_plane_identity_logs(
    coordination_root: Path,
    *,
    include_agent_notifier_signals: bool,
) -> dict[str, int]:
    """Migrate legacy identity logs atomically and return changed-row counts."""

    observer_root = coordination_root / "logs" / "observer"
    topology = TaskDocumentTopology(coordination_root)
    catalog = TerminalCatalog(terminal_catalog_path(coordination_root))
    context = IdentityMigrationContext(coordination_root, topology, catalog)

    def transform(row: dict[str, Any], *, current: str, legacy: str, kind: str) -> dict[str, Any]:
        schema = row.get("schema")
        if schema == current:
            return row
        if schema != legacy:
            raise ValueError(f"{kind}: unsupported durable schema {schema!r}")
        return _migrate_row(context, row, current=current, kind=kind)

    inbox_store = OperatorInboxStore(observer_root)
    expectation_store = ExpectationRowStore(observer_root)
    changed = {
        "operatorInbox": migrate_jsonl_records(
            inbox_store.log_path(),
            OPERATOR_INBOX_OWNERSHIP,
            OperatorInboxEntry,
            lambda row: transform(
                row, current=OPERATOR_INBOX_RECORD_SCHEMA, legacy=_INBOX_V1, kind="inbox"
            ),
        ),
        "expectations": migrate_jsonl_records(
            expectation_store.log_path(),
            EXPECTATION_ROW_OWNERSHIP,
            ExpectationRow,
            lambda row: transform(
                row,
                current=EXPECTATION_ROW_SCHEMA,
                legacy=_EXPECTATION_V1,
                kind="expectation",
            ),
        ),
    }
    if include_agent_notifier_signals:
        signal_store = AgentNotifierSignalCooldownStore(observer_root)
        changed["agentNotifierSignals"] = migrate_jsonl_records(
            signal_store.log_path(),
            AGENT_NOTIFIER_SIGNAL_OWNERSHIP,
            AgentNotifierSignalRecord,
            lambda row: transform(
                row,
                current=AGENT_NOTIFIER_SIGNAL_SCHEMA,
                legacy=_SIGNAL_V1,
                kind="signal",
            ),
        )
    return changed


def _migrate_row(
    context: IdentityMigrationContext,
    row: dict[str, Any],
    *,
    current: str,
    kind: str,
) -> dict[str, Any]:
    migrated = dict(row)
    legacy_leaf = _text(row.get("leafKey"))
    leaf = (
        legacy_leaf_document_ref(
            context.coordination_root,
            context.topology,
            legacy_leaf,
        )
        if legacy_leaf is not None
        else None
    )
    if kind == "inbox":
        _set_ref(
            migrated,
            "taskDocumentRef",
            _address_ref(context, row, leaf, "agentId", "recipientRole"),
        )
        _set_ref(
            migrated,
            "subjectTaskDocumentRef",
            _address_ref(context, row, leaf, "subjectAgentId", "seatRole"),
        )
        _set_ref(
            migrated,
            "ownerTaskDocumentRef",
            _address_ref(context, row, leaf, "ownerAgentId", "ownerRole"),
        )
    elif kind == "expectation":
        _set_ref(
            migrated,
            "taskDocumentRef",
            _address_ref(context, row, leaf, "subjectAgentId", "seatRole"),
        )
    elif kind == "signal":
        _set_ref(
            migrated,
            "taskDocumentRef",
            _address_ref(context, row, leaf, "targetAgentId", "targetRole"),
        )
    migrated.pop("leafKey", None)
    migrated["schema"] = current
    return migrated


def _address_ref(
    context: IdentityMigrationContext,
    row: dict[str, Any],
    leaf: TaskDocumentRef | None,
    exact_field: str,
    role_field: str,
) -> TaskDocumentRef | None:
    exact_id = _text(row.get(exact_field))
    exact = context.catalog.get(exact_id) if exact_id is not None else None
    if exact is not None and exact.binding_task_document_ref is not None:
        return exact.binding_task_document_ref
    role = _text(row.get(role_field))
    if leaf is None or role is None:
        return leaf
    try:
        return task_ref_for_role(context.topology, leaf, role)
    except (TaskDocumentRefError, ValueError):
        # Non-orchestration mailbox roles (system/operator/developer) remain leaf-local. They are
        # not structural seats and no parent altitude is invented for them.
        return leaf


def _set_ref(row: dict[str, Any], field: str, ref: TaskDocumentRef | None) -> None:
    if ref is not None:
        row[field] = ref.model_dump()


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
