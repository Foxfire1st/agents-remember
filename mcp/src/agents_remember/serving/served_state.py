"""The served state contract: the projection as the wire actually carries it.

``/api/state`` and the SSE ``snapshot`` event do not serve a :class:`WorkspaceProjection`.
They serve that projection PLUS a serve-time tail: ``servingBuild``, which process is
answering. The tail key was injected into the dumped projection dict with nothing
declaring it, so the emitted object was outside its own model: feeding a served body back
through ``WorkspaceProjection`` (``extra="forbid"``) raised on the extra key.

:class:`ServedWorkspaceProjection` is that declaration. The tail lives here rather than on
``WorkspaceProjection`` for four reasons:

1. **Layer.** ``Projector`` builds the projection at TICK time; the build stamp is a
   serving-layer fact of the process that answers.
2. **The dump memo.** ``app._ProjectionBodyCache`` memoizes the ~1.3 MB projection dump per
   published instance; the tail is injected onto a shallow copy afterwards.
3. **A second consumer.** ``observer.projection_store.write_projection`` persists
   ``WorkspaceProjection`` into ``latest-state.json``. Serving-only fields must not enter
   that artifact's schema.
4. **Shape.** Only the SSE ``snapshot`` carries the tail; ``delta`` events are per-entity
   nodes and carry none of it.

So this one body is assembled rather than dumped from a single model -- deliberately, and
now declared: :func:`served_state_tail` builds the tail, and ``ServedWorkspaceProjection``
is what the assembled result must validate against
(``mcp/tests/test_served_state_conformance.py`` holds that shut against the real route).
Validating per request is not the enforcement: it would re-parse the whole 1.3 MB body and
give back everything the memo exists to save.
"""

from __future__ import annotations

import contextlib
from typing import Any

from pydantic import BaseModel

from agents_remember.models.core import ServingBuildPayload
from agents_remember.observer.projection import WorkspaceProjection
from agents_remember.serving.build_info import ServingBuild


class WatcherHealthPayload(BaseModel):
    """The change watcher's health as the served state carries it (MIK-R81 rule 5).

    ``running`` says whether the watcher's change detection is trusted right
    now; ``downSince`` is wall-clock seconds of when it last went down, absent
    while it runs. Absence of the whole key (not a fabricated row) is the
    answer where no watcher is configured.
    """

    running: bool
    downSince: float | None = None


class CheckoutBindingPayload(BaseModel):
    """The disposable coordination root a linked-checkout process is bound to (MIK-R83 rule 7)."""

    state: str
    worktree: str
    coordinationRoot: str
    configuredRoot: str | None = None
    remedy: str


class ServedWorkspaceProjection(WorkspaceProjection):
    """A ``WorkspaceProjection`` as ``/api/state`` and the SSE ``snapshot`` emit it.

    The tail field is optional: ``stream_events`` serves a snapshot without it when it is
    driven without a build stamp, and that is a valid served body.
    """

    servingBuild: ServingBuildPayload | None = None
    watcherHealth: WatcherHealthPayload | None = None
    checkoutBinding: CheckoutBindingPayload | None = None


SERVED_TAIL_FIELDS: tuple[str, ...] = ("servingBuild", "watcherHealth", "checkoutBinding")
"""The keys :func:`served_state_tail` may add -- exactly this model's extension over the
projection it wraps. Named so the assembly and the contract cannot drift apart silently."""


def served_state_tail(
    *,
    build: ServingBuild | None,
    watcher_health: dict[str, Any] | None = None,
    checkout_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The serve-time tail, JSON-ready, to be merged onto a copy of the memoized dump.

    A missing build fact is OMITTED: absence is not a fabricated claim. So is a
    checkout binding: a declared process is not bound to a disposable root,
    and its state says nothing about one (MIK-R83 rule 7).
    """
    tail: dict[str, Any] = {}
    if build is not None:
        tail["servingBuild"] = build.payload().model_dump(mode="json", exclude_none=True)
    if checkout_binding is not None:
        with contextlib.suppress(ValueError):
            tail["checkoutBinding"] = CheckoutBindingPayload.model_validate(
                checkout_binding
            ).model_dump(mode="json", exclude_none=True)
    if watcher_health is not None:
        # Validated against the declared tail model so the assembly cannot drift
        # from the contract; a row that fails validation is omitted, never served raw.
        with contextlib.suppress(ValueError):
            tail["watcherHealth"] = WatcherHealthPayload.model_validate(watcher_health).model_dump(
                mode="json", exclude_none=True
            )
    return tail
