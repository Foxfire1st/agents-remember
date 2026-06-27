"""Durable dashboard terminal-session catalog."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

TerminalSessionKind = Literal["terminal", "harness"]
TerminalSessionStatus = Literal["running", "exited", "terminated"]


@dataclass(frozen=True)
class TerminalCatalogEntry:
    """One dashboard-owned terminal or harness session."""

    id: str
    label: str
    kind: TerminalSessionKind
    harness: str | None
    lifecycle_id: str | None
    cwd: Path
    tmux_name: str
    command: tuple[str, ...]
    created_at: str
    last_attached_at: str
    status: TerminalSessionStatus
    terminated_at: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, object]) -> TerminalCatalogEntry:
        raw_command = data.get("command", [])
        command = raw_command if isinstance(raw_command, list) else []
        return cls(
            id=str(data["id"]),
            label=str(data["label"]),
            kind="harness" if data.get("kind") == "harness" else "terminal",
            harness=str(data["harness"]) if data.get("harness") is not None else None,
            lifecycle_id=(
                str(data["lifecycleId"]) if data.get("lifecycleId") is not None else None
            ),
            cwd=Path(str(data["cwd"])),
            tmux_name=str(data["tmuxName"]),
            command=tuple(str(part) for part in command),
            created_at=str(data["createdAt"]),
            last_attached_at=str(data["lastAttachedAt"]),
            status=_status(data.get("status")),
            terminated_at=(
                str(data["terminatedAt"]) if data.get("terminatedAt") is not None else None
            ),
        )

    def to_json(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "cwd": str(self.cwd),
            "tmuxName": self.tmux_name,
            "command": list(self.command),
            "createdAt": self.created_at,
            "lastAttachedAt": self.last_attached_at,
            "status": self.status,
        }
        if self.harness is not None:
            data["harness"] = self.harness
        if self.lifecycle_id is not None:
            data["lifecycleId"] = self.lifecycle_id
        if self.terminated_at is not None:
            data["terminatedAt"] = self.terminated_at
        return data

    def with_attachment(self, attached_at: str) -> TerminalCatalogEntry:
        return TerminalCatalogEntry(
            id=self.id,
            label=self.label,
            kind=self.kind,
            harness=self.harness,
            lifecycle_id=self.lifecycle_id,
            cwd=self.cwd,
            tmux_name=self.tmux_name,
            command=self.command,
            created_at=self.created_at,
            last_attached_at=attached_at,
            status="running",
            terminated_at=None,
        )

    def with_status(
        self, status: TerminalSessionStatus, *, at: str | None = None
    ) -> TerminalCatalogEntry:
        return TerminalCatalogEntry(
            id=self.id,
            label=self.label,
            kind=self.kind,
            harness=self.harness,
            lifecycle_id=self.lifecycle_id,
            cwd=self.cwd,
            tmux_name=self.tmux_name,
            command=self.command,
            created_at=self.created_at,
            last_attached_at=self.last_attached_at,
            status=status,
            terminated_at=at if status == "terminated" else self.terminated_at,
        )


def terminal_catalog_path(coordination_root: Path) -> Path:
    """Runtime catalog path for dashboard-owned terminal sessions."""

    return coordination_root / "logs" / "dashboard" / "terminal-sessions.json"


class TerminalCatalog:
    """JSON-backed catalog for dashboard-owned terminal sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def list(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]:
        entries = self._read()
        if include_terminated:
            return entries
        return [entry for entry in entries if entry.status != "terminated"]

    def get(self, session_id: str) -> TerminalCatalogEntry | None:
        return next((entry for entry in self._read() if entry.id == session_id), None)

    def upsert(self, entry: TerminalCatalogEntry) -> None:
        entries = [current for current in self._read() if current.id != entry.id]
        entries.append(entry)
        self._write(entries)

    def mark_attached(self, session_id: str, attached_at: str) -> TerminalCatalogEntry | None:
        entry = self.get(session_id)
        if entry is None:
            return None
        updated = entry.with_attachment(attached_at)
        self.upsert(updated)
        return updated

    def mark_exited(self, session_id: str) -> TerminalCatalogEntry | None:
        entry = self.get(session_id)
        if entry is None:
            return None
        if entry.status == "terminated":
            return entry
        updated = entry.with_status("exited")
        self.upsert(updated)
        return updated

    def mark_terminated(
        self, session_id: str, terminated_at: str
    ) -> TerminalCatalogEntry | None:
        entry = self.get(session_id)
        if entry is None:
            return None
        updated = entry.with_status("terminated", at=terminated_at)
        self.upsert(updated)
        return updated

    def _read(self) -> list[TerminalCatalogEntry]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"terminal catalog must be a JSON object: {self.path}")
        sessions = raw.get("sessions", [])
        if not isinstance(sessions, list):
            raise ValueError(f"terminal catalog sessions must be a list: {self.path}")
        return [
            TerminalCatalogEntry.from_json(item)
            for item in sessions
            if isinstance(item, dict)
        ]

    def _write(self, entries: list[TerminalCatalogEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        payload = {
            "schema": "ar-dashboard-terminal-sessions/v1",
            "sessions": [entry.to_json() for entry in sorted(entries, key=lambda e: e.created_at)],
        }
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


def _status(raw: object) -> TerminalSessionStatus:
    if raw == "exited":
        return "exited"
    if raw == "terminated":
        return "terminated"
    return "running"
