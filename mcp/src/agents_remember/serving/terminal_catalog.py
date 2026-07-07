"""Durable dashboard terminal-session catalog."""

from __future__ import annotations

import contextlib
import json
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

TerminalSessionKind = Literal["terminal", "harness"]
TerminalSessionStatus = Literal["running", "exited", "terminated"]
# The leaf-uniqueness role: a plain shell (``kind == "terminal"``) is a TERMINAL; any agent harness
# is a CHAT. Uniqueness is per (leaf, role) -- at most one running chat AND one running terminal per
# leaf -- so an agent chat and a scratch terminal can share a leaf without colliding (L5 fix 2).
TerminalSessionRole = Literal["chat", "terminal"]


def role_for_kind(kind: TerminalSessionKind) -> TerminalSessionRole:
    """The leaf-uniqueness role for a launch ``kind``: a shell is a terminal, a harness is a chat."""
    return "terminal" if kind == "terminal" else "chat"


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
    # The durable leaf-identity key (qualified leaf id ``repo/master/leaf-id``), opaque to the
    # backend: the catalog is the leaf->chat registry. Written only when set (like ``harness`` /
    # ``lifecycleId`` / ``terminatedAt``) so legacy rows with no ``leafKey`` read back as ``None``
    # -- no schema bump, migration-safe. A chat claims a leaf at open/attach, enclosure-independent.
    leaf_key: str | None = None
    # Spawned-by provenance (L2 agent dispatch): the spawning session id + lifecycle id when this row
    # was created by the ``spawn_agent_session`` tool (an orchestrator spawning a manager, a manager
    # spawning a worker). Same migration-safe pattern as ``leaf_key`` -- written only when set, so a
    # hand-opened or dashboard-opened row reads both back as ``None``. The dashboard reads these to
    # render the orchestration tree (spawner -> spawned edges) once that surface lands.
    spawned_by_session: str | None = None
    spawned_by_lifecycle: str | None = None
    # The l-01 role this session was spawned AS (``AR_SPAWN_ROLE`` seeded into the spawn env by the
    # dispatching seat -- orchestrator/strategist/manager/worker/reviewer/designer), recorded at first
    # spawn so the Chats command tree (L14) can group command chats without re-reading tmux env.
    # Same migration-safe written-only-when-set pattern as the provenance fields above.
    spawn_role: str | None = None
    # Free-form spawn provenance (260703-L16): the escape-hatch role knobs, recorded VERBATIM and
    # never validated -- launch_args rode the harness argv, session_commands were pasted post-launch
    # before the brief, prompt_keywords were prepended to the brief paste. Same migration-safe
    # written-only-when-set pattern as the fields above.
    launch_args: tuple[str, ...] | None = None
    prompt_keywords: tuple[str, ...] | None = None
    session_commands: tuple[str, ...] | None = None
    # The RESOLVED dispatch level (leaf|master|portfolio) this seat was spawned AT, plus whether the
    # dispatcher supplied it ("explicit") or it defaulted ("default") -- the rolesPerLevel knob
    # resolution input (260703-L16, ruling 2026-07-07T08:15). Written-only-when-set.
    spawn_level: str | None = None
    spawn_level_source: str | None = None

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
            leaf_key=str(data["leafKey"]) if data.get("leafKey") is not None else None,
            spawned_by_session=(
                str(data["spawnedBySession"]) if data.get("spawnedBySession") is not None else None
            ),
            spawned_by_lifecycle=(
                str(data["spawnedByLifecycle"])
                if data.get("spawnedByLifecycle") is not None
                else None
            ),
            spawn_role=str(data["spawnRole"]) if data.get("spawnRole") is not None else None,
            launch_args=_string_tuple(data.get("launchArgs")),
            prompt_keywords=_string_tuple(data.get("promptKeywords")),
            session_commands=_string_tuple(data.get("sessionCommands")),
            spawn_level=str(data["spawnLevel"]) if data.get("spawnLevel") is not None else None,
            spawn_level_source=(
                str(data["spawnLevelSource"]) if data.get("spawnLevelSource") is not None else None
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
        if self.leaf_key is not None:
            data["leafKey"] = self.leaf_key
        if self.spawned_by_session is not None:
            data["spawnedBySession"] = self.spawned_by_session
        if self.spawned_by_lifecycle is not None:
            data["spawnedByLifecycle"] = self.spawned_by_lifecycle
        if self.spawn_role is not None:
            data["spawnRole"] = self.spawn_role
        if self.launch_args is not None:
            data["launchArgs"] = list(self.launch_args)
        if self.prompt_keywords is not None:
            data["promptKeywords"] = list(self.prompt_keywords)
        if self.session_commands is not None:
            data["sessionCommands"] = list(self.session_commands)
        if self.spawn_level is not None:
            data["spawnLevel"] = self.spawn_level
        if self.spawn_level_source is not None:
            data["spawnLevelSource"] = self.spawn_level_source
        return data

    def with_attachment(self, attached_at: str) -> TerminalCatalogEntry:
        # ``replace`` preserves every other field (incl. leaf_key + spawned-by provenance) so a new
        # column never silently drops on a re-attach.
        return replace(
            self,
            last_attached_at=attached_at,
            status="running",
            terminated_at=None,
        )

    def with_status(
        self, status: TerminalSessionStatus, *, at: str | None = None
    ) -> TerminalCatalogEntry:
        return replace(
            self,
            status=status,
            terminated_at=at if status == "terminated" else self.terminated_at,
        )

    def with_leaf_key(self, leaf_key: str | None) -> TerminalCatalogEntry:
        """A copy bound to ``leaf_key`` (or unbound when ``None``); the leaf-attach write point."""
        return replace(self, leaf_key=leaf_key)

    @property
    def role(self) -> TerminalSessionRole:
        """This session's leaf-uniqueness role, derived from its kind (chat vs. terminal)."""
        return role_for_kind(self.kind)


def terminal_catalog_path(coordination_root: Path) -> Path:
    """Runtime catalog path for dashboard-owned terminal sessions."""

    return coordination_root / "logs" / "dashboard" / "terminal-sessions.json"


class TerminalCatalog:
    """JSON-backed catalog for dashboard-owned terminal sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # Serialize the read-modify-write within the process. FastAPI runs sync handlers in a threadpool,
        # so concurrent open/attach/terminate/refresh requests would otherwise each read a stale snapshot
        # and clobber one another (lost updates) — and, with the old shared temp file, interleave their
        # bytes into a torn file that then 500s every reader. The unique-temp + atomic replace in `_write`
        # keeps any single write valid even across processes; this lock makes concurrent mutations in THIS
        # process compose instead of racing. (RLock so a mutator may call another lock-taking helper.)
        self._lock = threading.RLock()

    def list(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]:
        entries = self._read()
        if include_terminated:
            return entries
        return [entry for entry in entries if entry.status != "terminated"]

    def get(self, session_id: str) -> TerminalCatalogEntry | None:
        return next((entry for entry in self._read() if entry.id == session_id), None)

    def active_for_leaf(
        self, leaf_key: str, *, role: TerminalSessionRole = "chat"
    ) -> TerminalCatalogEntry | None:
        """The single RUNNING session of ``role`` that owns ``leaf_key``, or ``None``.

        Uniqueness is per (leaf, role): a leaf may hold at most one running chat AND one running
        terminal, so the probe is role-scoped (the default ``"chat"`` is the agent slot). ``list()``
        already excludes terminated rows; gating on ``status == "running"`` means an exited/terminated
        session frees its leaf (a stale ``running`` row is downgraded by ``_refresh_catalog_entries``
        when the tmux session is gone). This is the server-authoritative uniqueness probe the opener +
        attach-leaf routes call immediately before an upsert.
        """
        return next(
            (
                entry
                for entry in self.list()
                if entry.leaf_key == leaf_key and entry.status == "running" and entry.role == role
            ),
            None,
        )

    def upsert(self, entry: TerminalCatalogEntry) -> None:
        with self._lock:
            entries = [current for current in self._read() if current.id != entry.id]
            entries.append(entry)
            self._write(entries)

    def mark_attached(self, session_id: str, attached_at: str) -> TerminalCatalogEntry | None:
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_attachment(attached_at)
            entries[index] = updated
            self._write(entries)
            return updated

    def mark_exited(self, session_id: str) -> TerminalCatalogEntry | None:
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            if entry.status == "terminated":
                return entry
            updated = entry.with_status("exited")
            entries[index] = updated
            self._write(entries)
            return updated

    def mark_terminated(self, session_id: str, terminated_at: str) -> TerminalCatalogEntry | None:
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_status("terminated", at=terminated_at)
            entries[index] = updated
            self._write(entries)
            return updated

    def _read(self) -> list[TerminalCatalogEntry]:
        if not self.path.exists():
            return []
        raw = _load_catalog_json(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        sessions = raw.get("sessions", [])
        if not isinstance(sessions, list):
            return []
        return [TerminalCatalogEntry.from_json(item) for item in sessions if isinstance(item, dict)]

    def _write(self, entries: list[TerminalCatalogEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A UNIQUE temp per write (pid + uuid): a shared fixed-name temp let concurrent writers — even two
        # request threads in one process — interleave their bytes into a torn file. With a private temp +
        # atomic os.replace, every reader sees a complete file; the worst case is a lost update, never
        # corruption. The temp is removed if the write fails so a crash never leaves a partial sibling.
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        payload = {
            "schema": "ar-dashboard-terminal-sessions/v1",
            "sessions": [entry.to_json() for entry in sorted(entries, key=lambda e: e.created_at)],
        }
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


def _string_tuple(raw: object) -> tuple[str, ...] | None:
    """A free-form string list read back from JSON (``None`` for absent/legacy rows)."""
    if not isinstance(raw, list):
        return None
    return tuple(str(item) for item in raw)


def _index_of(entries: list[TerminalCatalogEntry], session_id: str) -> int | None:
    """The position of ``session_id`` in ``entries`` (for an in-place status update), or ``None``."""
    return next((i for i, entry in enumerate(entries) if entry.id == session_id), None)


def _load_catalog_json(text: str) -> object:
    """Parse the catalog JSON, self-healing from a torn write instead of 500-ing the whole dashboard.

    A legacy fixed-temp write (or two dashboards on one coordination root) could leave a valid object
    followed by ``Extra data`` -- a partial duplicate fragment from an interleaved write. Recover the first
    complete object (the real catalog; the trailing fragment is the torn tail). Unparseable content degrades
    to an empty catalog so a corrupt file is treated as "no sessions" and the next write overwrites it clean.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        obj, _ = json.JSONDecoder().raw_decode(text.lstrip())
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj


def _status(raw: object) -> TerminalSessionStatus:
    if raw == "exited":
        return "exited"
    if raw == "terminated":
        return "terminated"
    return "running"
