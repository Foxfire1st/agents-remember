"""The served state contract: the projection as the wire actually carries it.

``/api/state`` and the SSE ``snapshot`` event do not serve a :class:`WorkspaceProjection`.
They serve that projection PLUS a two-key serve-time tail -- ``servingBuild`` (which
process is answering) and ``supervisorHeartbeat`` (how long ago the supervisor last
ticked, as of THIS response). Both keys were injected into the dumped projection dict with
nothing declaring them, so the emitted object was outside its own model: feeding a served
body back through ``WorkspaceProjection`` (``extra="forbid"``) raised on the extra keys.

:class:`ServedWorkspaceProjection` is that declaration. The tail lives here rather than on
``WorkspaceProjection`` for five reasons, in ascending order of how expensive getting it
wrong would be:

1. **Layer.** ``Projector`` builds the projection at TICK time; both keys are serving-layer
   facts computed at SERVE time (``_supervisor_heartbeat_payload`` is explicitly "the tick
   age at RESPONSE time"). A tick-time model cannot hold a per-response value.
2. **The dump memo.** ``app._ProjectionBodyCache`` memoizes the ~1.3 MB projection dump per
   published instance *because* the volatile tail is injected onto a shallow copy
   afterwards. Fields on the projection would be inside the memo and would have to be
   either stale or uncached; the memo saves a measured 13.7-16.5 ms per request.
3. **The ETag.** The heartbeat is deliberately outside the projector's content revision, so
   a volatile age never busts the ETag. As a projection field it would change every tick and
   the 304 path would stop firing.
4. **A second consumer.** ``observer.projection_store.write_projection`` persists
   ``WorkspaceProjection`` into ``latest-state.json``. Serving-only fields must not enter
   that artifact's schema.
5. **Shape.** Only the SSE ``snapshot`` carries the tail; ``delta`` events are per-entity
   nodes and carry none of it.

So this one body is assembled rather than dumped from a single model -- deliberately, and
now declared: :func:`served_state_tail` builds the tail from the two payload models, and
``ServedWorkspaceProjection`` is what the assembled result must validate against
(``mcp/tests/test_served_state_conformance.py`` holds that shut against the real route).
Validating per request is not the enforcement: it would re-parse the whole 1.3 MB body and
give back everything the memo exists to save.
"""

from __future__ import annotations

from typing import Any

from agents_remember.observer.projection import WorkspaceProjection
from agents_remember.serving.build_info import ServingBuild, ServingBuildPayload
from agents_remember.serving.supervisor_heartbeat import SupervisorHeartbeatPayload


class ServedWorkspaceProjection(WorkspaceProjection):
    """A ``WorkspaceProjection`` as ``/api/state`` and the SSE ``snapshot`` emit it.

    Both fields are optional: ``stream_events`` serves a snapshot with neither when it is
    driven without a build stamp or a heartbeat reader, and that is a valid served body.
    """

    servingBuild: ServingBuildPayload | None = None
    supervisorHeartbeat: SupervisorHeartbeatPayload | None = None


SERVED_TAIL_FIELDS: tuple[str, ...] = ("servingBuild", "supervisorHeartbeat")
"""The keys :func:`served_state_tail` may add -- exactly this model's extension over the
projection it wraps. Named so the assembly and the contract cannot drift apart silently."""


def served_state_tail(
    *, build: ServingBuild | None, heartbeat: SupervisorHeartbeatPayload | None
) -> dict[str, Any]:
    """The serve-time tail, JSON-ready, to be merged onto a copy of the memoized dump.

    Each half serializes under its own rule, which is why this is two dumps and not one:
    a missing build fact is OMITTED (absence is not a fabricated claim), while a missing
    heartbeat fact is an explicit NULL (a supervisor that never ticked is a reported state,
    not a missing key). ``exclude_none`` is recursive, so one shared dump could not do both.
    """
    tail: dict[str, Any] = {}
    if build is not None:
        tail["servingBuild"] = build.payload().model_dump(mode="json", exclude_none=True)
    if heartbeat is not None:
        tail["supervisorHeartbeat"] = heartbeat.model_dump(mode="json")
    return tail
