"""Serving-backed implementation of the provider degradation alert port."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.controlplane.operator_inbox_records import AgentRole
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.serving.hosted_session_runtime import HostedSessionRuntime
from agents_remember.serving.inbox_delivery import InboxDeliveryLog, deliver_inbox_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import (
    TerminalCatalog,
    terminal_catalog_path,
)
from agents_remember.serving.terminal_paste import TerminalPaster


class DegradationAlertDelivery:
    """The dashboard-loop implementation of :class:`DegradationAlertPort`."""

    def __init__(self, coordination_root: Path) -> None:
        self._coordination_root = coordination_root

    def role_recipients(self, coordination_root: Path, role: AgentRole) -> list[str | None]:
        catalog = TerminalCatalog(terminal_catalog_path(coordination_root))
        sessions: list[str | None] = [
            entry.id
            for entry in catalog.list()
            if entry.status == "running" and entry.kind == "harness" and entry.binding_role == role
        ]
        if sessions:
            return sessions
        return [None]

    def deliver(self, *, store: OperatorInboxStore, entry: Any) -> None:
        catalog = TerminalCatalog(terminal_catalog_path(self._coordination_root))
        host = TerminalHost()
        paster = TerminalPaster()
        deliver_inbox_entry(
            InboxDeliveryLog(store=store, entry=entry),
            sessions=HostedSessionRuntime(catalog=catalog, host=host),
            paster=paster,
        )


__all__ = ["DegradationAlertDelivery"]
