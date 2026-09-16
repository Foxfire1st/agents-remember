"""eve active projector: eve's durable session stream -> conversation items.

Schema authority: eve's documented stream envelope (``{"type", "data", "meta"}``) as the pinned
``eve@0.56.0`` release emits it -- diverted verbatim by the session adapter under
``AR_EVIDENCE_KEY`` -- plus the adapter's own event kind. The envelope's ``data`` object is the
payload every handler below parses by its declared keys; a shape that does not match becomes
``unknown-vendor`` evidence with the frame preserved, never a guessed message, tool, or control
meaning.

Four rules decide where a frame lands, and each one is a protocol fact rather than a preference:

1. **Streaming text is one item, revised.** ``message.appended``/``reasoning.appended`` carry
   incremental text; ``message.completed``/``reasoning.completed`` carry the authoritative block.
   An appended frame mints its turn's channel item *empty* and delivers the text as a block delta,
   and the completion revises that same item to the finalized text. One turn therefore shows one
   assistant item and one thinking item, never a delta pile beside a duplicate aggregate.
2. **A turn boundary is not a session boundary.** ``turn.completed``/``turn.failed``/
   ``turn.cancelled`` settle a turn and are the ONLY frame types that mint a turn outcome.
   ``session.waiting`` parks the session so it can accept another message -- it maps to a notice
   and never to ``MappedTurnOutcome``. The adapter reports that frame as kind ``completed`` with a
   ``completed`` terminal result, because AR's terminal vocabulary has no word for "parked"; a
   projector that forwarded that result would settle the turn a second time, and would report a
   cancelled turn as completed, since eve emits ``session.waiting`` after ``turn.cancelled`` on the
   same stream. Only ``session.completed``/``session.failed`` retire the session, and only those
   mint a session-scoped outcome.
3. **Identity is exact, and a frame that names no turn is session-scoped.** A turn-scoped frame
   carries ``data.turnId``; a frame without one is never attributed to a turn by guesswork.
4. **Recognized control state mints nothing; an unrecognized frame is preserved.** Session start
   and turn start are the status projection's business, and a second copy of that state machine here
   could only disagree with it, so they are silently consumed by name. Every event this module does
   not classify -- including one a future eve release adds -- becomes ``unknown-vendor`` evidence
   rather than being dropped, so an unmapped shape stays visible instead of silently absent.

``eve`` multiplexes nothing: one evidence stream is one session, so ``parent_thread_id`` is unused
and a native-history page (which eve does not expose) fails closed rather than being invented.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_remember.models.conversations.content import (
    ChoiceOption,
    ChoicesBlock,
    ConversationCorrelation,
    ConversationItem,
    TextBlock,
    ThinkingBlock,
    ToolInputBlock,
    ToolOutputBlock,
)
from agents_remember.models.conversations.evidence import (
    EvidenceFrame,
    NativeEvidenceFrame,
)
from agents_remember.serving.conversation.projectors.common import (
    MappedBlockDelta,
    MappedItem,
    MappedTurnOutcome,
    MappedUnknownVendor,
    MapperOutput,
    TerminalOutcomeValue,
    UnmappableShape,
    harness_provenance,
    optional_text,
    required_list,
    required_object,
    required_text,
    unknown_input_provenance,
)

HARNESS = "eve"

ENVELOPE_TYPE_KEY = "type"
"""Where inside the diverted payload this projector reads eve's own event name from.

eve's evidence envelope is ``{"type", "data", "meta"}`` and the bridge diverts the whole envelope
verbatim as ``EvidenceFrame.raw``, so the discriminator is the envelope's own ``type`` field -- read
here, in the layer that maps it, exactly as the Claude and Pi projectors read the frame ``type``
embedded in their payloads. ``EvidenceFrame.native_method`` is deliberately NOT used: it exists for
an adapter that carries the discriminator out of band, and eve does not need one. The field survives
clipping by documented contract (``clip_evidence_payload`` re-carries the frame type) and crosses
the daemon wire verbatim (``evidence_frame_json`` copies ``raw``).
"""

_LIVE_ORIGIN = "eve durable session stream"
"""Every frame this projector sees is a record in eve's durable stream, live or replayed."""

_PARKED_ITEM = "eve:session:parked"
"""One item id per session for the parked notice; a second park revises it, never stacks it."""

SILENT_CONTROL_EVENTS = frozenset(
    {
        # Session and turn framing: the status projection owns liveness and identity, and a turn's
        # start is already visible as the operator message and the first delta.
        "session.started",
        "turn.started",
        # Step framing: one pair per model step inside a turn (modelId, stepIndex, finishReason).
        # It is per-step telemetry, not transcript content.
        "step.started",
        "step.completed",
        # The streamed JSON arguments of a tool call. The call's authoritative input arrives with
        # ``actions.requested`` and is what the tool item carries, so the partial text would only
        # duplicate it -- and the adapter renders no transcript entry for it either.
        "action.input.appended",
        # Compaction and context clearing: the adapter publishes an activity change and renders no
        # transcript entry, and the session-status projection already shows that activity.
        "compaction.requested",
        "compaction.completed",
        "context.cleared",
    }
)
"""Recognized eve frames that mint no conversation item.

Every entry is a frame the pinned release emits routinely and that carries nothing a reader of the
transcript would look for; each is consumed BY NAME so a genuinely unrecognized event stays
distinguishable from a known, deliberately silent one. The alternative -- letting them fall through
-- puts "unknown vendor event" rows on the timeline for the most frequent frames in the stream
(six ``step.started``, five ``step.completed`` and four ``action.input.appended`` of the 52 frames in
the pinned release's recorded run), which is noise that hides the genuinely unknown ones. Their
treatment matches the adapter's own: none of them produces a transcript entry."""

Channel = Literal["message", "reasoning"]


@dataclass(frozen=True)
class _Frame:
    """One parsed eve evidence frame: the native envelope plus the adapter's own event kind."""

    evidence: EvidenceFrame
    event_type: str
    data: Mapping[str, object]
    turn_id: str | None
    kind: str

    @property
    def sequence(self) -> int:
        return self.evidence.sequence

    @property
    def created_at(self) -> str:
        return self.evidence.created_at


def map_evidence_frame(
    frame: EvidenceFrame,
    *,
    evidence_ref: str,  # noqa: ARG001 - refs are minted engine-side from the item ids returned
    parent_thread_id: str | None = None,  # noqa: ARG001 - eve carries no multiplexed sub-agents
) -> list[MapperOutput]:
    """Map one live or replayed eve stream record; unknown types stay visible."""

    parsed = _parse(frame)
    handler = _HANDLERS.get(parsed.event_type)
    if handler is None:
        return [_unknown_event(parsed)]
    return handler(parsed)


def _parse(frame: EvidenceFrame) -> _Frame:
    """Parse the native envelope by its declared keys; a shapeless frame is refused, not guessed."""

    event_type = optional_text(frame.raw.get(ENVELOPE_TYPE_KEY))
    if event_type is None:
        raise UnmappableShape("eve evidence frame carries no event type in its envelope")
    data = required_object(frame.raw.get("data") or {}, f"eve {event_type} data")
    return _Frame(
        evidence=frame,
        event_type=event_type,
        data=data,
        turn_id=optional_text(data.get("turnId")),
        kind=frame.kind,
    )


def _silent(_frame: _Frame) -> list[MapperOutput]:
    """A recognized control frame that mints no conversation item; see the module docstring."""

    return []


def _appended_frame(frame: _Frame) -> list[MapperOutput]:
    return _appended(frame.evidence, frame.data, turn_id=frame.turn_id, event_type=frame.event_type)


def _completed_frame(frame: _Frame) -> list[MapperOutput]:
    return _completed(
        frame.evidence, frame.data, turn_id=frame.turn_id, event_type=frame.event_type
    )


def _received_frame(frame: _Frame) -> list[MapperOutput]:
    return [_operator_item(frame.evidence, frame.data, turn_id=frame.turn_id)]


def _actions_frame(frame: _Frame) -> list[MapperOutput]:
    return _tool_calls(frame.evidence, frame.data, turn_id=frame.turn_id)


def _result_frame(frame: _Frame) -> list[MapperOutput]:
    return [
        _tool_result(
            frame.evidence,
            frame.data,
            turn_id=frame.turn_id,
            partial=frame.event_type == "action.partial",
        )
    ]


def _requests_frame(frame: _Frame) -> list[MapperOutput]:
    return _input_requests(frame.evidence, frame.data, turn_id=frame.turn_id)


def _resolved_frame(frame: _Frame) -> list[MapperOutput]:
    return [_input_resolved(frame.evidence, frame.data, turn_id=frame.turn_id)]


def _authorization_required_frame(frame: _Frame) -> list[MapperOutput]:
    return [_authorization_required(frame.evidence, frame.data, turn_id=frame.turn_id)]


def _authorization_completed_frame(frame: _Frame) -> list[MapperOutput]:
    return [_authorization_completed(frame.evidence, frame.data, turn_id=frame.turn_id)]


def _turn_completed_frame(frame: _Frame) -> list[MapperOutput]:
    return _settled(frame, "completed")


def _turn_cancelled_frame(frame: _Frame) -> list[MapperOutput]:
    return _settled(frame, "interrupted", stop_reason="cancelled")


def _turn_failed_frame(frame: _Frame) -> list[MapperOutput]:
    return _settled(
        frame, "failed", stop_reason=optional_text(frame.data.get("message")) or "turn.failed"
    )


def _settled(
    frame: _Frame, outcome: TerminalOutcomeValue, *, stop_reason: str | None = None
) -> list[MapperOutput]:
    """One settled turn: the settlement itself AND the item that makes it visible.

    The outcome is the machine truth the status projection and the catalog consume; the item is what
    a reader sees at the end of the turn. Minting only the first is how a cancelled turn rendered as
    nothing at all while the park notice that follows it rendered as a row -- the interruption the
    requirement names was invisible in the transcript. Every sibling projector mints both, so the
    shared ``turn-result`` rendering (``· interrupted`` / ``· turn failed`` / ``· turn complete``)
    is reachable here too.
    """

    turn_id = frame.turn_id
    return [
        MappedItem(
            item=_item(
                _Placement(
                    item_id=f"eve:turn-result:{turn_id or frame.sequence}",
                    kind="turn-result",
                    phase=outcome,
                    turn_id=turn_id,
                ),
                (),
                frame.created_at,
                voice=_Voice(lane="harness", role="system"),
            )
        ),
        MappedTurnOutcome(outcome=outcome, turn_id=turn_id, stop_reason=stop_reason),
    ]


def _waiting_frame(frame: _Frame) -> list[MapperOutput]:
    return [_session_parked(frame.evidence, frame.data)]


def _step_failed_frame(frame: _Frame) -> list[MapperOutput]:
    """One failed step: the failure the adapter renders, as an error item a reader can see.

    ``step.failed`` is the only failure frame the adapter turns into transcript content, so it
    becomes an ``error`` item carrying the same ``code: message`` text the adapter's own entry
    carries -- a failure state that is visible rather than inferred from the absence of a result.
    """

    code = optional_text(frame.data.get("code")) or frame.event_type
    message = optional_text(frame.data.get("message")) or ""
    return [
        MappedItem(
            item=_item(
                _Placement(
                    item_id=f"eve:step-failed:{frame.sequence}",
                    kind="error",
                    phase="failed",
                    turn_id=frame.turn_id,
                ),
                (TextBlock(block_id="text", text=f"{code}: {message}".strip(": ").strip()),),
                frame.created_at,
                voice=_Voice(lane="system", role="system"),
            )
        )
    ]


def _session_completed_frame(frame: _Frame) -> list[MapperOutput]:
    return [MappedTurnOutcome(outcome="completed", turn_id=frame.turn_id)]


def _session_failed_frame(frame: _Frame) -> list[MapperOutput]:
    return _settled(
        frame, "failed", stop_reason=optional_text(frame.data.get("message")) or "session.failed"
    )


_HANDLERS: dict[str, Callable[[_Frame], list[MapperOutput]]] = {
    # Text and reasoning channels: the appended frame carries the delta, the completed frame the block.
    "message.appended": _appended_frame,
    "reasoning.appended": _appended_frame,
    "message.completed": _completed_frame,
    "reasoning.completed": _completed_frame,
    # Operator input, tools, interactions, failures and authorization challenges.
    "message.received": _received_frame,
    "step.failed": _step_failed_frame,
    "actions.requested": _actions_frame,
    "action.result": _result_frame,
    "action.partial": _result_frame,
    "input.requested": _requests_frame,
    "input.resolved": _resolved_frame,
    "authorization.required": _authorization_required_frame,
    "authorization.completed": _authorization_completed_frame,
    # Turn settlement: the ONLY frame types that mint a turn outcome.
    "turn.completed": _turn_completed_frame,
    "turn.cancelled": _turn_cancelled_frame,
    "turn.failed": _turn_failed_frame,
    # The park, deliberately mapped to a notice rather than an outcome.
    "session.waiting": _waiting_frame,
    "session.completed": _session_completed_frame,
    "session.failed": _session_failed_frame,
}

# The recognized-but-silent frames are registered from their single declared set, so adding one to
# that set cannot leave the dispatch table behind.
_HANDLERS.update(dict.fromkeys(SILENT_CONTROL_EVENTS, _silent))


def map_native_frame(frame: NativeEvidenceFrame, *, evidence_ref: str) -> list[MapperOutput]:
    """eve exposes no native-history page; a native frame reaching here is a contract error."""

    del frame, evidence_ref
    raise NotImplementedError("eve has no native-history page surface (stream/replay only)")


def map_transcript_echo(
    entry: Mapping[str, object],
    *,
    evidence_ref: str,
) -> list[MapperOutput]:
    """eve has no transcript echo channel; its adapter transcript entries are not evidence."""

    del entry, evidence_ref
    raise NotImplementedError("eve has no transcript echo channel")


def _appended(
    frame: EvidenceFrame,
    data: Mapping[str, object],
    *,
    turn_id: str | None,
    event_type: str,
) -> list[MapperOutput]:
    """One appended delta: the channel item's carrier plus the text it actually carried."""

    channel = _channel(event_type)
    delta = required_text(
        data.get(f"{channel}Delta"),
        f"eve {event_type} delta",
    )
    item_id = _channel_item_id(turn_id, channel)
    return [
        MappedItem(
            item=_item(
                _Placement(
                    item_id=item_id,
                    kind=_channel_kind(channel),
                    phase="streaming",
                    turn_id=turn_id,
                ),
                (_empty_block(channel),),
                frame.created_at,
            )
        ),
        MappedBlockDelta(item_id=item_id, block_id=_channel_block_id(channel), delta=delta),
    ]


def _completed(
    frame: EvidenceFrame,
    data: Mapping[str, object],
    *,
    turn_id: str | None,
    event_type: str,
) -> list[MapperOutput]:
    """One finalized block: the channel item the deltas built, revised to its authoritative text.

    ``message.completed`` with a null ``message`` is eve's documented intentional-silence
    delivery -- the block is final and empty -- so the item's phase is revised without inventing
    text that was never produced.
    """

    completed_channel = _completed_channel(event_type)
    finalized = data.get(completed_channel)
    if finalized is not None and not isinstance(finalized, str):
        raise UnmappableShape(f"eve {event_type} payload must be a string or null")
    text = finalized if isinstance(finalized, str) else ""
    return [
        MappedItem(
            item=_item(
                _Placement(
                    item_id=_channel_item_id(turn_id, completed_channel),
                    kind=_channel_kind(completed_channel),
                    phase="completed",
                    turn_id=turn_id,
                ),
                (
                    (
                        TextBlock(block_id="text", text=text)
                        if completed_channel == "message"
                        else ThinkingBlock(block_id="thinking", markdown=text)
                    ),
                ),
                frame.created_at,
            )
        )
    ]


def _operator_item(
    frame: EvidenceFrame, data: Mapping[str, object], *, turn_id: str | None
) -> MappedItem:
    """One delivered operator message, read from the durable record rather than the receipt.

    eve's ``message.received`` records the text the runtime accepted, so the item's source is the
    durable record (``native-history``) rather than the live window. The delivery receipt lives on
    the control authority; this item is the conversation's own copy, so it claims ``unknown-input``
    provenance instead of asserting a producer the stream cannot prove.
    """

    text = optional_text(data.get("message"))
    if text is None:
        raise UnmappableShape("eve message.received carries no message text")
    return MappedItem(
        item=_item(
            _Placement(
                item_id=f"eve:message-received:{frame.sequence}",
                kind="message",
                phase="completed",
                turn_id=turn_id,
            ),
            (TextBlock(block_id="text", text=text),),
            frame.created_at,
            voice=_Voice(lane="unknown-input", role="user", source="native-history"),
        )
    )


def _tool_calls(
    frame: EvidenceFrame, data: Mapping[str, object], *, turn_id: str | None
) -> list[MapperOutput]:
    """One requested tool call per action, keyed by the native ``callId`` it will resolve by."""

    actions = required_list(data.get("actions"), "eve actions.requested.actions")
    outputs: list[MapperOutput] = []
    for raw in actions:
        action = required_object(raw, "eve actions.requested action")
        outputs.append(
            MappedItem(
                item=_item(
                    _Placement(
                        item_id=_tool_item_id(
                            required_text(action.get("callId"), "eve action callId")
                        ),
                        kind="tool-call",
                        phase="pending",
                        turn_id=turn_id,
                    ),
                    (
                        ToolInputBlock(
                            block_id="input",
                            summary=required_text(action.get("toolName"), "eve action toolName"),
                            data=action.get("input"),
                        ),
                    ),
                    frame.created_at,
                    voice=_Voice(
                        role="tool",
                        correlation=required_text(action.get("callId"), "eve action callId"),
                    ),
                )
            )
        )
    if not outputs:
        raise UnmappableShape("eve actions.requested carried no actions")
    return outputs


def _tool_result(
    frame: EvidenceFrame,
    data: Mapping[str, object],
    *,
    turn_id: str | None,
    partial: bool,
) -> MappedItem:
    """One tool result, upserting the call item its ``callId`` already opened.

    The item keeps kind ``tool-call`` and gains only the OUTPUT block, exactly as the sibling
    projectors model one invocation plus its result: the store unions blocks by id for that kind, so
    the invocation the call frame recorded survives the result, and its terminal-phase guard
    protects a completed call from a late partial. Publishing a differently-kinded item would
    replace the row wholesale and take the recorded input with it.
    """

    result = required_object(data.get("result"), "eve action result")
    call_id = required_text(result.get("callId"), "eve action result callId")
    status = optional_text(data.get("status")) or ("partial" if partial else "completed")
    return MappedItem(
        item=_item(
            _Placement(
                item_id=_tool_item_id(call_id),
                kind="tool-call",
                phase="streaming" if partial else _tool_phase(status),
                turn_id=turn_id,
            ),
            (ToolOutputBlock(block_id="output", data=result.get("output")),),
            frame.created_at,
            voice=_Voice(role="tool", correlation=call_id),
        )
    )


def _input_requests(
    frame: EvidenceFrame, data: Mapping[str, object], *, turn_id: str | None
) -> list[MapperOutput]:
    """One interaction item per pending input request, keyed by the id a response must name.

    The item carries the question as text and, when the request offers answers, those answers as a
    ``choices`` block: the ids are the exact tokens the adapter's own ``response_payload`` turns into
    eve's ``{requestId, optionId}``, so the shared interaction item renders a question a reader can
    actually answer rather than a label with no options. A request with no options (free text) keeps
    the text alone -- an empty choices block is invalid by the model's own rule.
    """

    requests = required_list(data.get("requests"), "eve input.requested.requests")
    outputs: list[MapperOutput] = []
    for raw in requests:
        request = required_object(raw, "eve input.requested request")
        request_id = required_text(request.get("requestId"), "eve input request requestId")
        kind = optional_text(request.get("kind")) or "input"
        prompt = optional_text(request.get("prompt")) or request_id
        choices = _interaction_choices(request, request_id=request_id)
        outputs.append(
            MappedItem(
                item=_item(
                    _Placement(
                        item_id=_interaction_item_id(request_id),
                        kind="interaction",
                        phase="waiting",
                        turn_id=turn_id,
                    ),
                    (
                        TextBlock(block_id="text", text=f"{kind}: {prompt}"),
                        *((choices,) if choices is not None else ()),
                    ),
                    frame.created_at,
                    voice=_Voice(correlation=request_id),
                )
            )
        )
    if not outputs:
        raise UnmappableShape("eve input.requested carried no requests")
    return outputs


def _interaction_choices(request: Mapping[str, object], *, request_id: str) -> ChoicesBlock | None:
    """The request's own answer options as a choices block, or ``None`` when it offers none.

    eve's option list is ``{id, label, description?}``; an entry missing an id or a label is dropped
    rather than given a fabricated one, and a request left with no usable option stays text-only.
    """

    options = request.get("options")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        return None
    parsed = tuple(
        ChoiceOption(
            option_id=option_id,
            label=optional_text(option.get("label")) or option_id,
            description=optional_text(option.get("description")),
        )
        for option in options
        if isinstance(option, Mapping)
        and (option_id := optional_text(option.get("id"))) is not None
    )
    if not parsed:
        return None
    return ChoicesBlock(block_id="choices", interaction_id=request_id, options=parsed)


def _input_resolved(
    frame: EvidenceFrame, data: Mapping[str, object], *, turn_id: str | None
) -> MappedItem:
    """The resolutions eve recorded, so a resumed turn is legible without the pending queue."""

    resolved = [
        required_text(
            required_object(raw, "eve input.resolved resolution").get("requestId"),
            "eve input resolution requestId",
        )
        for raw in required_list(data.get("resolutions"), "eve input.resolved.resolutions")
    ]
    return MappedItem(
        item=_item(
            _Placement(
                item_id=f"eve:input-resolved:{frame.sequence}",
                kind="notice",
                phase="completed",
                turn_id=turn_id,
            ),
            (
                TextBlock(
                    block_id="text",
                    text="input resolved: " + (", ".join(resolved) or "none recorded"),
                ),
            ),
            frame.created_at,
            voice=_Voice(lane="system", role="system"),
        )
    )


def _authorization_required(
    frame: EvidenceFrame, data: Mapping[str, object], *, turn_id: str | None
) -> MappedItem:
    """One authorization challenge; the adapter keys it by the same derived interaction id."""

    name = optional_text(data.get("name")) or "authorization"
    return MappedItem(
        item=_item(
            _Placement(
                item_id=_interaction_item_id(f"authorization:{name}"),
                kind="interaction",
                phase="waiting",
                turn_id=turn_id,
            ),
            (
                TextBlock(
                    block_id="text",
                    text=optional_text(data.get("description"))
                    or f"authorization required: {name}",
                ),
            ),
            frame.created_at,
            voice=_Voice(correlation=f"authorization:{name}"),
        )
    )


def _authorization_completed(
    frame: EvidenceFrame, data: Mapping[str, object], *, turn_id: str | None
) -> MappedItem:
    """The challenge's answer, so the transcript shows what unblocked the turn."""

    name = optional_text(data.get("name")) or "authorization"
    return MappedItem(
        item=_item(
            _Placement(
                item_id=f"eve:authorization-completed:{frame.sequence}",
                kind="notice",
                phase="completed",
                turn_id=turn_id,
            ),
            (TextBlock(block_id="text", text=f"authorization completed: {name}"),),
            frame.created_at,
            voice=_Voice(lane="system", role="system"),
        )
    )


def _session_parked(frame: EvidenceFrame, data: Mapping[str, object]) -> MappedItem:
    """The parked notice -- deliberately NOT a turn outcome.

    ``session.waiting`` leaves the session usable and arrives after BOTH an ordinary
    ``turn.completed`` and a ``turn.cancelled``. Minting a terminal outcome from it would settle
    one turn twice and would report a cancelled turn as completed; the adapter's own ``completed``
    kind and ``completed`` terminal result for this frame are exactly that error's source. The item
    is session-scoped and stable, so a second park revises one row instead of stacking notices.
    """

    wait = optional_text(data.get("wait")) or "the next message"
    return MappedItem(
        item=_item(
            _Placement(item_id=_PARKED_ITEM, kind="notice", phase="waiting"),
            (TextBlock(block_id="text", text=f"eve session parked and ready for {wait}"),),
            frame.created_at,
            voice=_Voice(lane="system", role="system"),
        )
    )


def _unknown_event(frame: _Frame) -> MappedUnknownVendor:
    """A stream record this projector does not map, preserved with its native identity visible."""

    return MappedUnknownVendor(
        item_id=f"eve-event-{frame.sequence}",
        vendor_type=f"eve:{frame.event_type}",
        safe_summary=(f"eve stream event of type {frame.event_type} (adapter kind {frame.kind})"),
        created_at=frame.created_at,
    )


def _channel(event_type: str) -> Channel:
    """The channel an ``*.appended`` frame belongs to, whose delta field is ``<channel>Delta``."""

    return "reasoning" if event_type.startswith("reasoning") else "message"


def _completed_channel(event_type: str) -> Channel:
    """The channel an ``*.completed`` frame belongs to, whose block field is the channel name.

    eve names the finalized field after its own event prefix -- ``message.completed`` carries
    ``data.message`` and ``reasoning.completed`` carries ``data.reasoning`` -- so the split is the
    documented key, never a guess about which of two shapes arrived.
    """

    return "reasoning" if event_type.startswith("reasoning") else "message"


def _channel_kind(channel: Channel) -> Literal["message", "thinking"]:
    return "thinking" if channel == "reasoning" else "message"


def _channel_item_id(turn_id: str | None, channel: Channel) -> str:
    """One item per (turn, channel); a turn-less frame still gets one stable identity."""

    return f"eve:{turn_id or 'session'}:{channel}"


def _channel_block_id(channel: Channel) -> str:
    return "thinking" if channel == "reasoning" else "text"


def _empty_block(channel: Channel) -> TextBlock | ThinkingBlock:
    """The delta carrier's initial block: the frame's text arrives as the delta, not twice."""

    return (
        ThinkingBlock(block_id="thinking", markdown="")
        if channel == "reasoning"
        else TextBlock(block_id="text", text="")
    )


def _tool_item_id(call_id: str) -> str:
    return f"eve:tool:{call_id}"


def _interaction_item_id(interaction_id: str) -> str:
    return f"eve:interaction:{interaction_id}"


def _tool_phase(status: str) -> Literal["completed", "pending", "failed", "interrupted", "unknown"]:
    if status == "completed":
        return "completed"
    if status == "pending":
        return "pending"
    if status == "failed":
        return "failed"
    if status == "interrupted":
        return "interrupted"
    return "unknown"


ItemKind = Literal[
    "message",
    "thinking",
    "tool-call",
    "tool-result",
    "interaction",
    "turn-result",
    "notice",
    "error",
    "unknown-vendor",
]
ItemPhase = Literal[
    "pending", "streaming", "waiting", "completed", "failed", "interrupted", "unknown"
]
ItemLane = Literal["harness", "system", "unknown-input"]
ItemRole = Literal["user", "assistant", "system", "tool"]
ItemSource = Literal["harness-live", "harness-replay", "native-history"]


@dataclass(frozen=True)
class _Placement:
    """Where one mapped item belongs: its stable identity, its turn, and its lifecycle phase."""

    item_id: str
    kind: ItemKind
    phase: ItemPhase
    turn_id: str | None = None


@dataclass(frozen=True)
class _Voice:
    """Who the item belongs to and how it is attributed; defaults are the harness's own."""

    lane: ItemLane = "harness"
    role: ItemRole = "assistant"
    correlation: str | None = None
    source: ItemSource = "harness-live"


HARNESS_VOICE = _Voice()
"""The default attribution: a harness-produced assistant item from the live stream."""


def _item(
    placement: _Placement,
    blocks: tuple[object, ...],
    created_at: str | None,
    *,
    voice: _Voice = HARNESS_VOICE,
) -> ConversationItem:
    """One item with honest provenance; the engine owns ordinal, revision, and envelopes.

    An ``unknown-input`` item is a durable-record copy whose producer the stream cannot prove, so it
    carries producer-less provenance -- the same rule every other projector follows.
    """

    return ConversationItem(
        item_id=placement.item_id,
        revision=1,
        global_ordinal=1,
        turn_id=placement.turn_id,
        lane=voice.lane,
        source=voice.source,
        provenance=(
            unknown_input_provenance(_LIVE_ORIGIN, observed_at=created_at)
            if voice.lane == "unknown-input"
            else harness_provenance(_LIVE_ORIGIN, observed_at=created_at)
        ),
        role=voice.role,
        kind=placement.kind,
        phase=placement.phase,
        blocks=blocks,  # type: ignore[arg-type]
        correlation=(
            ConversationCorrelation(vendor_correlation_id=voice.correlation)
            if voice.correlation
            else None
        ),
        created_at=created_at,
    )


__all__ = [
    "ENVELOPE_TYPE_KEY",
    "HARNESS",
    "SILENT_CONTROL_EVENTS",
    "map_evidence_frame",
    "map_native_frame",
]
