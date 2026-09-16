"""The bounded pending-interaction queue for one eve session.

eve pauses a run on ``input.requested`` and resumes it when the caller posts a structured
``inputResponses`` entry for the exact ``requestId`` it raised. Those requests are the adapter's
only authority for answering a paused run, so they are retained exactly, keyed by that id, and
bounded: a session that raises more concurrent prompts than the bound refuses the excess instead
of growing without limit.

Staleness is decided by eve, not here: a response for a request that is no longer pending is
refused locally, and eve itself treats an answered-or-cancelled request id as stale and never
lets it authorize the earlier tool call.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    InteractionQuestion,
    InteractionQuestionOption,
    PendingInteraction,
)

Clock = Callable[[], str]

InteractionKind = Literal["approval", "question", "authorization", "input"]


@dataclass
class EveInteractionQueue:
    """Bounded, ordered retention of the input requests one eve session is waiting on."""

    limit: int
    clock: Clock
    _pending: OrderedDict[str, PendingInteraction] = field(default_factory=OrderedDict)

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise HarnessControlError("eve interaction limit must be positive")

    def __bool__(self) -> bool:
        return bool(self._pending)

    @property
    def retained(self) -> int:
        return len(self._pending)

    def request_ids(self) -> tuple[str, ...]:
        return tuple(self._pending)

    def head(self) -> PendingInteraction | None:
        """The oldest pending request, which is the one the snapshot exposes singularly."""

        return next(iter(self._pending.values()), None)

    def get(self, interaction_id: str) -> PendingInteraction | None:
        return self._pending.get(interaction_id)

    def add_input_request(self, request: Mapping[str, object]) -> PendingInteraction:
        """Retain one ``input.requested`` entry from eve's strict request schema."""

        request_id = _text(request, "requestId")
        if not request_id:
            raise HarnessControlError("eve input request requires a requestId")
        if request_id in self._pending:
            raise HarnessControlError(f"duplicate eve input request id: {request_id}")
        if len(self._pending) >= self.limit:
            raise HarnessControlError("eve pending input queue reached its bounded limit")
        pending = PendingInteraction(
            interaction_id=request_id,
            kind=_text(request, "kind") or "input",
            prompt=_text(request, "prompt") or request_id,
            created_at=self.clock(),
            choices=_option_ids(request.get("options")),
            raw=dict(request),
            questions=_option_questions(request.get("options")),
        )
        self._pending[request_id] = pending
        return pending

    def add_authorization(self, *, name: str, description: str, raw: Mapping[str, object]) -> str:
        """Retain one ``authorization.required`` challenge under a derived stable request id."""

        request_id = authorization_interaction_id(name)
        if request_id in self._pending:
            raise HarnessControlError(f"duplicate eve authorization id: {request_id}")
        if len(self._pending) >= self.limit:
            raise HarnessControlError("eve pending input queue reached its bounded limit")
        self._pending[request_id] = PendingInteraction(
            interaction_id=request_id,
            kind="authorization",
            prompt=description,
            created_at=self.clock(),
            raw=dict(raw),
        )
        return request_id

    def resolve(self, interaction_id: str) -> bool:
        """Drop one request; ``False`` when it was not pending (already answered or cancelled)."""

        return self._pending.pop(interaction_id, None) is not None

    def clear(self) -> None:
        self._pending.clear()


def authorization_interaction_id(name: str) -> str:
    """The deterministic request id one authorization challenge is addressed by."""

    return f"authorization:{name}"


def _option_ids(options: object) -> tuple[str, ...]:
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        return ()
    return tuple(
        option_id
        for option in options
        if isinstance(option, Mapping) and (option_id := _text(option, "id"))
    )


def _option_questions(options: object) -> tuple[InteractionQuestion, ...]:
    """Project eve's option list into the structured question page AR renders."""

    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        return ()
    questions: list[InteractionQuestion] = []
    for option in options:
        if not isinstance(option, Mapping):
            continue
        option_id = _text(option, "id")
        if not option_id:
            continue
        label = _text(option, "label") or option_id
        questions.append(
            InteractionQuestion(
                text=label,
                header=option_id,
                options=(
                    InteractionQuestionOption(
                        label=label,
                        description=_text(option, "description") or None,
                    ),
                ),
            )
        )
    return tuple(questions)


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""
