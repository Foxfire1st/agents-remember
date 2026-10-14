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

from typing import Any

from agents_remember.models.core import ServingBuildPayload
from agents_remember.observer.projection import WorkspaceProjection
from agents_remember.serving.build_info import ServingBuild


class ServedWorkspaceProjection(WorkspaceProjection):
    """A ``WorkspaceProjection`` as ``/api/state`` and the SSE ``snapshot`` emit it.

    The tail field is optional: ``stream_events`` serves a snapshot without it when it is
    driven without a build stamp, and that is a valid served body.
    """

    servingBuild: ServingBuildPayload | None = None


SERVED_TAIL_FIELDS: tuple[str, ...] = ("servingBuild",)
"""The keys :func:`served_state_tail` may add -- exactly this model's extension over the
projection it wraps. Named so the assembly and the contract cannot drift apart silently."""


def served_state_tail(*, build: ServingBuild | None) -> dict[str, Any]:
    """The serve-time tail, JSON-ready, to be merged onto a copy of the memoized dump.

    A missing build fact is OMITTED: absence is not a fabricated claim.
    """
    tail: dict[str, Any] = {}
    if build is not None:
        tail["servingBuild"] = build.payload().model_dump(mode="json", exclude_none=True)
    return tail
