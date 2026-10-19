"""The host-created sessions one frame is served under, and their revocation (MIK-R104 rule 6).

An origin string authenticates nothing, so a session is a host-created value that is bound to the
exact frame, revision and comparison the bytes were served from. Opening a frame creates one; every
request names it, and the host compares it to the binding it recorded -- not to a message's claim.

A session ends by *revocation*, and a revoked session is never silently replaced: navigation,
replacement, disposal, a review-scope change and an idle timeout each revoke the channel with their
own reason, and a request on it answers ``refused:revoked`` without an effect. The registry keeps a
bounded set of revocation tombstones, so the answer stays truthful after the session itself is gone
while memory stays flat. Opening past the active-frame ceiling disposes the least recently used
frame first: four live frames, not unbounded ones.
"""

from __future__ import annotations

import secrets
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from agents_remember.canvas.protocol import RequestLedger

__all__ = [
    "MAX_ACTIVE_FRAMES",
    "MAX_TOMBSTONES",
    "SESSION_SECONDS",
    "CanvasSession",
    "SessionBinding",
    "SessionRegistry",
]

MAX_ACTIVE_FRAMES = 4
SESSION_SECONDS = 1800.0
MAX_TOMBSTONES = 64


@dataclass(frozen=True)
class SessionBinding:
    """What one channel is created for: its frame, its immutable revision and its comparison."""

    repository_id: str
    master: str
    leaf_id: str
    frame_id: str
    revision: int
    comparison: str
    evidence_digest: str
    entrypoint: str
    scripts: bool


@dataclass(frozen=True)
class CanvasSession:
    """One admitted channel: what it serves, what it is bound to, and its bounded ledger."""

    session: str
    repository_id: str
    master: str
    leaf_id: str
    frame_id: str
    revision: int
    comparison: str
    evidence_digest: str
    entrypoint: str
    scripts: bool
    created_at: float
    expires_at: float
    budget: RequestLedger = field(default_factory=RequestLedger)

    def expired(self, now: float) -> bool:
        return now >= self.expires_at


class SessionRegistry:
    """The bounded set of live sessions and the tombstones of the revoked ones.

    ``clock`` is injected so the idle bound is testable without sleeping. ``tokens`` is injected for
    the same reason: a test asserts that a value nobody was given authenticates nothing.
    """

    def __init__(
        self,
        *,
        ttl: float = SESSION_SECONDS,
        active: int = MAX_ACTIVE_FRAMES,
        clock: Callable[[], float] = time.monotonic,
        tokens: Callable[[], str] | None = None,
    ) -> None:
        self._ttl = ttl
        self._active = active
        self._clock = clock
        self._tokens = tokens or (lambda: secrets.token_urlsafe(32))
        self._live: OrderedDict[str, CanvasSession] = OrderedDict()
        self._tombstones: OrderedDict[str, str] = OrderedDict()

    def open(self, binding: SessionBinding) -> tuple[CanvasSession, tuple[CanvasSession, ...]]:
        """Create one session and report the frames displaced to keep the ceiling.

        A session value is never reused: the tombstones of revoked values stay, so a page that
        presents an old value after its frame was disposed is refused instead of adopted.
        """

        now = self._clock()
        displaced = self._make_room(now)
        session = CanvasSession(
            session=self._fresh(),
            repository_id=binding.repository_id,
            master=binding.master,
            leaf_id=binding.leaf_id,
            frame_id=binding.frame_id,
            revision=binding.revision,
            comparison=binding.comparison,
            evidence_digest=binding.evidence_digest,
            entrypoint=binding.entrypoint,
            scripts=binding.scripts,
            created_at=now,
            expires_at=now + self._ttl,
        )
        self._live[session.session] = session
        return session, displaced

    def _fresh(self) -> str:
        value = self._tokens()
        while value in self._live or value in self._tombstones:
            value = self._tokens()
        return value

    def _make_room(self, now: float) -> tuple[CanvasSession, ...]:
        displaced: list[CanvasSession] = []
        for value in [key for key, item in self._live.items() if item.expired(now)]:
            expired = self.revoke(value, "idle-timeout")
            if expired is not None:
                displaced.append(expired)
        while len(self._live) >= self._active:
            value, item = next(iter(self._live.items()))
            self.revoke(value, "disposed-lru")
            displaced.append(item)
            if len(displaced) > self._active * 4:  # a pathological clock cannot loop forever
                break
        return tuple(displaced)

    def get(self, session: str) -> CanvasSession | None:
        """The live session, or ``None``. An expired session is revoked on the way out."""

        item = self._live.get(session)
        if item is None:
            return None
        if item.expired(self._clock()):
            self.revoke(session, "idle-timeout")
            return None
        self._live.move_to_end(session)
        return item

    def revocation(self, session: str) -> str | None:
        """Why a value is no longer live: its own revocation reason, or ``None`` if never seen."""

        return self._tombstones.get(session)

    def revoke(self, session: str, reason: str) -> CanvasSession | None:
        """End one channel. The tombstone keeps the reason, not the content."""

        item = self._live.pop(session, None)
        self._tombstones[session] = reason
        self._tombstones.move_to_end(session)
        while len(self._tombstones) > MAX_TOMBSTONES:
            self._tombstones.popitem(last=False)
        return item

    def revoke_frame(self, *, repository_id: str, leaf_id: str, frame_id: str, reason: str) -> int:
        """Revoke every session of one frame: navigation, replacement or a review-scope change."""

        doomed = [
            value
            for value, item in self._live.items()
            if (item.repository_id, item.leaf_id, item.frame_id)
            == (repository_id, leaf_id, frame_id)
        ]
        for value in doomed:
            self.revoke(value, reason)
        return len(doomed)

    def revoke_all(self, reason: str) -> int:
        for value in list(self._live):
            self.revoke(value, reason)
        return 0

    @property
    def live(self) -> int:
        return len(self._live)

    def sessions(self) -> tuple[CanvasSession, ...]:
        """The live sessions, newest first, for the panel's bounded listing."""

        return tuple(reversed(self._live.values()))
