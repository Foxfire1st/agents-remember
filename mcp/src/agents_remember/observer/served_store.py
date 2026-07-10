"""Append-only served-onboarding ledger, co-located with the observer substrate.

A served record is the durable fact "this onboarding piece was already served to
this lifecycle, at this content hash". It lets ``read_ar_files`` dedup the
auto-attached overview/sidecar bodies so a repeated read of the same file/route
does not re-spam onboarding the model already holds. The ledger survives a
context compaction (the same lifecycle continues in place), so the dedup state is
on-disk, not only in process memory.

A compaction would otherwise leave the served set stale (onboarding the model
lost to truncation would not re-serve), but a session-hook producer for the
``compact-reset.json`` marker is **not** planned: compaction-reset is a
fresh-worker / lifecycle concern (small work -> new worker -> new lifecycle ->
fresh ledger) deferred to the post-3.0 agentic-control-plane follow-up, and
``clear`` / a new chat already yields a fresh lifecycle and ledger. Until then
``refresh=true`` is the working manual reset; the controller-side marker consumer
(``read_files._maybe_reset_served``) stays as defensive scaffolding.

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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
        """Read a served log back as validated records (empty when absent).

        260707-HFX2-L12 F12: skip a torn/legacy line rather than raise — a served ledger read that
        feeds ``read_ar_files`` dedup + the dashboard must degrade to "re-serve once" on one bad row,
        not crash the read."""
        path = self.log_path(lifecycle_id)
        if not path.exists():
            return []
        records: list[ServedRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(ServedRecord.model_validate_json(line))
            except ValidationError:
                continue
        return records

    def served_set(self, lifecycle_id: str) -> set[str]:
        """Fold the log into the set of ``"<kind>:<path>:<hash>"`` keys served."""
        return {record.key() for record in self.read(lifecycle_id)}
