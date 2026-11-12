"""Evidence values shared by native execution, capture and graders."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from ..artifacts import json_digest

Harness = Literal["codex", "hermes", "claude"]


@dataclass(frozen=True)
class SourceFile:
    path: str
    sha256: str


@dataclass(frozen=True)
class NativeEvent:
    kind: str
    session_id: str
    sequence: int
    payload: dict[str, Any]
    source: str
    source_sha256: str
    native_timestamp: str | float | int | None = None


@dataclass(frozen=True)
class NativeCapture:
    harness: Harness
    session_id: str
    events: tuple[NativeEvent, ...]
    source_files: tuple[SourceFile, ...]
    raw_records: tuple[dict[str, Any], ...]
    unknowns: tuple[str, ...]
    capture_version: str = "native-capture/v1"
    raw_export_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        actual = raw_export_digest(self.harness, self.session_id, self.raw_records)
        if self.raw_export_sha256 is not None and self.raw_export_sha256 != actual:
            raise ValueError("native raw records changed after their snapshot export was frozen")
        result["raw_export_sha256"] = actual
        return result

    @property
    def installed_server_calls(self) -> tuple[NativeEvent, ...]:
        return tuple(
            event
            for event in self.events
            if event.kind == "tool_call" and event.payload.get("server") == "agents-remember"
        )

    @property
    def server_call_coverage(self) -> str:
        return "unknown" if self.unknowns else "captured"


def tool_server(name: str, explicit: object = None) -> str | None:
    """Only native tool fields identify a server; argument prose never does."""
    if isinstance(explicit, str):
        return explicit
    for prefix, server in (
        ("mcp__agents_remember_task__", "agents-remember-task"),
        ("mcp__agents_remember__", "agents-remember"),
        ("mcp__agents-remember-task__", "agents-remember-task"),
        ("mcp__agents-remember__", "agents-remember"),
    ):
        if name.startswith(prefix):
            return server
    return None


def raw_export_digest(harness: Harness, session_id: str, records: object) -> str:
    """Identity of selected native rows, independent of an unrelated live DB/WAL."""
    return json_digest(
        {
            "schema": "ar-native-row-export/v1",
            "harness": harness,
            "session_id": session_id,
            "records": records,
        }
    )
