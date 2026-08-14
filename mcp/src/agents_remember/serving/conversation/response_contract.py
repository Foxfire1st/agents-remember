"""The declared response contract for the 25 structured-conversation routes.

Split from ``serving/response_contract.py`` for one hard reason: everything here needs
the conversation models package, and importing that from the app-level module would make
it import ``serving.conversation`` -- whose package ``__init__`` mounts the routers, which
import the contract back. ``serving/app.py`` registers the files/change-set/notes routes
before the conversation ones, so the app-level module must stay importable first. The seam is
therefore the package boundary, not a convenience.

What the routes here already had, and what they did not: all 25 dump a strict ``WireModel``,
so a model existed for nearly every body -- but not one route *declared* it, and three bodies
(the staging answer, the submit answer, the agent-history answer) are assembled at the route
and had no model at all. Those three are declared below; the rest reuse the models the
handlers already dump.
"""

from __future__ import annotations

from typing import Any

from agents_remember.models.conversations.attachments import (
    AttachmentOperationProjection,
    AttachmentReceipt,
)
from agents_remember.models.conversations.interrupts import (
    InterruptOperation,
)
from agents_remember.models.conversations.opening import (
    OpenConversationOperation,
)
from agents_remember.models.conversations.withdrawals import (
    FailedWithdrawalResponse,
    WithdrawnQueueResponse,
)
from agents_remember.serving.response_contract import (
    BridgeEpochMismatchRefusal,
    CapabilityUnavailableRefusal,
    CursorRefusal,
    StatusRefusal,
    WireResponse,
)

__all__ = [
    "CONTROL_RESPONSES",
    "CONVERSATION_RESPONSES",
    "INTERRUPT_OUTCOME_RESPONSES",
    "LIBRARY_RESPONSES",
    "OPEN_OUTCOME_RESPONSES",
    "WITHDRAW_OUTCOME_RESPONSES",
    "AgentHistoryHydrated",
    "ConversationSubmitted",
    "StagedAttachments",
    "WithdrawQueueAnswer",
]


# --- conversation wrappers ------------------------------------------------------------------
#
# The 25 conversation routes dump models that already exist in ``conversation/models.py``;
# only these three shapes are assembled at the route and therefore had nothing to declare.


class StagedAttachments(WireResponse):
    """``/conversation/attachments`` and ``/attachments/rebind``: the operation + its receipts."""

    operation: AttachmentOperationProjection
    receipts: list[AttachmentReceipt]


class ConversationSubmitted(WireResponse):
    """``/conversation/submit``, whose status is 200, 202 or 422 for the SAME body shape.

    The status is chosen from ``acceptance`` (``unknown`` -> 202, ``rejected``/``unsupported``
    -> 422), so all three are the success model and none of them is a refusal. The optional
    keys are written only when the answer has them.
    """

    request_id: str
    acceptance: str
    submitted_at: str
    bridge_epoch: str
    operation_ref: str | None = None
    attachment: AttachmentOperationProjection | None = None
    detail: str | None = None


class AgentHistoryHydrated(WireResponse):
    """``/agents/{agent_id}/history``: a typed child failure is still a 200 local outcome."""

    status: str
    agent_id: str
    detail: str | None = None
    code: str | None = None


WithdrawQueueAnswer = WithdrawnQueueResponse | FailedWithdrawalResponse
"""``/operation-queue/withdraw`` answers with the withdrawal or with why it could not happen;
``withdrawals.withdraw_http_status`` picks the status from which one it built."""


CONTROL_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": StatusRefusal, "description": "A malformed operation/recovery/asset ref"},
    403: {"model": StatusRefusal, "description": "Authorization failed for this caller"},
    404: {"model": StatusRefusal, "description": "The named session or operation is unknown"},
    409: {
        "model": BridgeEpochMismatchRefusal | StatusRefusal,
        "description": "Stale epoch, or a typed conflict on the operation",
    },
    422: {"model": StatusRefusal, "description": "The operation was rejected as posed"},
    503: {
        "model": StatusRefusal,
        "description": "Composition or the control bridge is unavailable",
    },
}
"""Every conversation-control route: ``control/api._map_typed_error`` is the one mapper, so
these six statuses are the complete refusal surface of all 17 of them."""


CONVERSATION_RESPONSES: dict[int | str, dict[str, Any]] = {
    **CONTROL_RESPONSES,
    400: {"model": CursorRefusal, "description": "The supplied cursor is invalid or conflicting"},
    409: {
        "model": BridgeEpochMismatchRefusal | CursorRefusal,
        "description": "Stale epoch, or a cursor the stream can no longer resume from",
    },
}
"""The active routes add the cursor refusals -- the only refusals on this surface that carry a
machine-readable ``reason``."""


LIBRARY_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": StatusRefusal, "description": "Malformed cursor"},
    403: {"model": StatusRefusal, "description": "Authorization or library scope refused"},
    404: {"model": StatusRefusal, "description": "Unknown harness, conversation, or request"},
    409: {
        "model": StatusRefusal,
        "description": "Cursor reset required, stale identity, or a request-id conflict",
    },
    422: {"model": CapabilityUnavailableRefusal, "description": "The capability is unavailable"},
    503: {"model": StatusRefusal, "description": "Library store or composition unavailable"},
}
"""``library/api._ERROR_STATUS_TABLE`` and ``_error_response``, transcribed: every entry in
that table lands on one of these six statuses."""


INTERRUPT_OUTCOME_RESPONSES: dict[int | str, dict[str, Any]] = {
    202: {
        "model": InterruptOperation,
        "description": "Acknowledged, settlement still pending -- the operation, not a refusal",
    },
    422: {
        "model": InterruptOperation | StatusRefusal,
        "description": "The harness rejected the interrupt, or the request was ill-posed",
    },
    503: {
        "model": InterruptOperation | StatusRefusal,
        "description": "Settlement failed, or the control bridge is unavailable",
    },
}
"""``operations.interrupt_http_status`` picks 200/202/422/503 off the operation's OWN
``acknowledgement``/``settlement``, so three of those statuses carry an ``InterruptOperation``
and not a refusal body. Declaring them as refusals -- which the shared table alone would have
done -- was wrong, and the conformance suite caught it on the real 422."""


WITHDRAW_OUTCOME_RESPONSES: dict[int | str, dict[str, Any]] = {
    202: {
        "model": WithdrawQueueAnswer,
        "description": "Delivery unknown -- the withdrawal answer, not a refusal",
    },
    404: {
        "model": WithdrawQueueAnswer | StatusRefusal,
        "description": "No such operation to withdraw, or an unknown session",
    },
    409: {
        "model": WithdrawQueueAnswer | BridgeEpochMismatchRefusal | StatusRefusal,
        "description": "Already dispatching, an epoch mismatch, or a request-id conflict",
    },
}
"""``withdrawals.withdraw_http_status``: a failed withdrawal is still this route's own answer
with its own body, on 202/404/409, so those statuses carry the answer union too."""


OPEN_OUTCOME_RESPONSES: dict[int | str, dict[str, Any]] = {
    202: {
        "model": OpenConversationOperation,
        "description": "Pending or timeout-unknown -- the open operation's own outcome",
    },
    409: {
        "model": OpenConversationOperation | StatusRefusal,
        "description": (
            "A stale identity, an identity mismatch or a request-id conflict as the "
            "operation's OWN outcome, or the same conflicts as a typed library refusal"
        ),
    },
    422: {
        "model": OpenConversationOperation | CapabilityUnavailableRefusal,
        "description": "The harness cannot reopen this conversation, or the capability gate refused",
    },
    503: {
        "model": OpenConversationOperation | StatusRefusal,
        "description": "The launch failed, or the library store / composition is unavailable",
    },
}
"""``library/api._OPEN_STATUS_BY_OUTCOME``: the open trio pick their status from the
operation's ``outcome``, and every one of those statuses carries the same operation body. They
are declared as success shapes because that is what they are -- a ``pending`` open is an
answer, and mapping it onto ``LIBRARY_RESPONSES``' refusal models alone would have been a lie.

**Each entry unions in the refusal model the shared table declares for that status**, because
the open trio spread this table over ``LIBRARY_RESPONSES`` and ``{**a, **b}`` is a dict merge,
not a union: a bare ``{409: OpenConversationOperation}`` *deletes* ``LIBRARY_RESPONSES[409]``
rather than joining it. ``_error_response`` still answers 409/422/503 with those refusals on
all three routes, so an overwriting entry would name a model the route cannot produce.
``INTERRUPT_OUTCOME_RESPONSES`` and ``WITHDRAW_OUTCOME_RESPONSES`` above carry their refusal
members for the same reason; this is the shape every outcome table has to have."""
