"""Aging an ISO-8601 stamp against a clock -- the primitive the record stores share.

Two substrates measure the same thing. The control plane ages its own durable rows to
decide retention (a gate snapshot past its TTL, an inbox row past the pending window,
a response still waiting for agent pickup), and the observer ages event stamps to
decide staleness in its projection. One definition, so the two can never disagree about
what "old" means for the same stamp.

It lives HERE rather than in ``observer`` because ``controlplane`` is the lower of the
two (``layers.toml``): the observer projects what the control plane records, so the
observer may know this package and this package may not know the observer. The
alternative -- observer owning it and controlplane importing up -- is what made the two
packages mutually dependent, and it made a retention rule impossible to load without
loading the projection it has nothing to do with.
"""

from __future__ import annotations

from datetime import UTC, datetime


def age_seconds(stamp: str, now: datetime) -> float | None:
    """Seconds between an ISO-8601 ``stamp`` and ``now``; ``None`` when unparseable.

    A naive stamp is assumed UTC: both substrates always write offset-aware stamps, but
    a hand-edited or legacy record must degrade to ``None``/UTC rather than crash the
    retention pass or the projection.
    """
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    return (now - then).total_seconds()
