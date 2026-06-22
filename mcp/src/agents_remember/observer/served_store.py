"""Append-only served-onboarding ledger, co-located with the observer substrate.

A served record is the durable fact "this onboarding piece was already served to
this lifecycle, at this content hash". It lets ``read_ar_files`` dedup the
auto-attached overview/sidecar bodies so a repeated read of the same file/route
does not re-spam onboarding the model already holds. The ledger survives a
context compaction (the same lifecycle continues in place), so the dedup state is
on-disk, not only in process memory.

A compaction normally also resets the served set (so onboarding the model lost
to truncation is re-served), but the SessionStart / PreCompact hook that WRITES
the ``compact-reset.json`` marker is deferred to slice-07 S5 with Probe B and
does NOT exist yet; until then ``refresh=true`` is the working manual reset. The
controller-side consumer of that marker already exists
(``read_files._maybe_reset_served``).

Served records live in ``<observer_root>/lifecycles/<lifecycle-id>/served.jsonl``
beside that lifecycle's ``events.jsonl`` / ``gates.jsonl`` -- the GateStore
pattern. Append-only and history-preserving: :meth:`served_set` folds the log
into the set of ``"<kind>:<path>:<hash>"`` keys served so far. One writer per
file in practice (a lifecycle is owned by one live session), the same
single-writer assumption the event and gate stores make.

This is a *record*, not a public MCP response: it carries no token fields and is
never returned by a tool, so it is not registered in
``PUBLIC_TOOL_RESPONSE_MODELS``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

SERVED_RECORD_SCHEMA = "ar-served-record/v1"


def now_iso() -> str:
    """Served-record timestamp: ISO 8601 with offset (UTC)."""
    return datetime.now(UTC).isoformat()


def served_key(kind: str, path: str, content_hash: str) -> str:
    """The dedup key folded over the ledger: ``<kind>:<path>:<hash>``."""
    return f"{kind}:{path}:{content_hash}"


class ServedRecord(BaseModel):
    """One ``ar-served-record/v1`` snapshot of a served onboarding piece.

    camelCase-free leaf fields keep the wire form small; ``schema_version``
    carries the lone alias because ``schema`` is an awkward attribute name --
    always dump with ``model_dump_json(by_alias=True, exclude_none=True)`` so it
    renders as ``schema``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=SERVED_RECORD_SCHEMA, alias="schema")
    kind: str
    path: str
    hash: str
    ts: str

    def key(self) -> str:
        return served_key(self.kind, self.path, self.hash)


class ServedStore:
    """Resolve per-lifecycle served-ledger paths and append/read records."""

    def __init__(self, observer_root: Path) -> None:
        self._root = observer_root

    @property
    def root(self) -> Path:
        return self._root

    def log_path(self, lifecycle_id: str) -> Path:
        """The served log for a lifecycle (beside its events/gates logs)."""
        return self._root / "lifecycles" / lifecycle_id / "served.jsonl"

    def append(self, lifecycle_id: str, record: ServedRecord) -> None:
        """Append one record, creating parent dirs on first write."""
        path = self.log_path(lifecycle_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = record.model_dump_json(by_alias=True, exclude_none=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def read(self, lifecycle_id: str) -> list[ServedRecord]:
        """Read a served log back as validated records (empty when absent)."""
        path = self.log_path(lifecycle_id)
        if not path.exists():
            return []
        return [
            ServedRecord.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def served_set(self, lifecycle_id: str) -> set[str]:
        """Fold the log into the set of ``"<kind>:<path>:<hash>"`` keys served."""
        return {record.key() for record in self.read(lifecycle_id)}
