"""Source-aware operation queue projection (260718-CHATS-L3, R2).

The complete retained live queue over the L2E operation-timeline: every
retained prompt operation across all three sources with exact
source/kind/phase/order truth, and never a body — terminal and durable rows
expose identity fields only. Only queued cockpit rows carry the caller-bound
withdrawalRef, the redacted identification preview, and the content digest
(the privacy rule is enforced by the SC1 contract validator itself).
Previews/digests come from this authority's own submit journal; rows the
journal does not hold (submitted outside the typed L3 submit) honestly report
empty held content — identification copy, never fabricated content. Set-model
and set-effort control operations are authority-minted with no submission
source (the substrate reports ``source=None``) and are not queue rows: the
contract's privacy validator cannot represent them without inventing a source,
so the projection covers the complete prompt queue they interleave with.
"""

from __future__ import annotations

from typing import Literal, cast

from agents_remember.serving.conversation.control.previews import payload_digest, redacted_preview
from agents_remember.serving.conversation.control.refs import OperationIdentity, mint_ref
from agents_remember.serving.conversation.control.service import (
    ControlChannel,
    ConversationControlService,
)
from agents_remember.serving.conversation.models import (
    AuthorizationBinding,
    CockpitQueueIdentity,
    OperationQueueItem,
    OperationQueueProjection,
)
from agents_remember.serving.harness_control_models import OperationTimelineItem

_LIVE_ROW_STATES = {"queued", "dispatching", "unknown"}
_EMPTY_DIGEST = payload_digest("")


async def operation_queue(
    service: ConversationControlService,
    authorization: AuthorizationBinding,
    ar_session_id: str,
    *,
    expected_bridge_epoch: str,
) -> OperationQueueProjection:
    """The complete retained live queue; never a body from another source."""

    entry = service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    channel = service.channel(ar_session_id, epoch)
    items = await service.read_full_timeline(entry, expected_bridge_epoch=epoch)
    rows: list[OperationQueueItem] = []
    key_parts: list[str] = []
    for item in items:
        if item.kind != "prompt" or item.state not in _LIVE_ROW_STATES:
            continue
        row = _queue_row(service, authorization, channel, ar_session_id, epoch, item)
        rows.append(row)
        key_parts.append(
            f"{row.operation_ref}|{row.revision}|{row.phase}|{row.withdrawable}|{row.sequence}"
        )
    items_key = ";".join(key_parts)
    if items_key != channel.queue_items_key:
        channel.queue_items_key = items_key
        channel.queue_revision += 1
    return OperationQueueProjection(
        bridge_epoch=epoch,
        revision=max(1, channel.queue_revision),
        items=tuple(rows),
    )


def _queue_row(
    service: ConversationControlService,
    authorization: AuthorizationBinding,
    channel: ControlChannel,
    ar_session_id: str,
    epoch: str,
    item: OperationTimelineItem,
) -> OperationQueueItem:
    identity = OperationIdentity(kind=item.kind, operation_id=item.operation_id, sequence=item.sequence)
    phase = cast(Literal["queued", "dispatching", "unknown"], item.state)
    source = item.source
    assert source is not None  # prompt rows always carry a submission source
    authorized = source == "cockpit" and phase == "queued"
    content_key = f"{phase}|{source}|{authorized}"
    prior = channel.queue_rows.get((identity.kind, identity.operation_id, identity.sequence))
    if prior is None:
        revision = 1
    elif prior[1] != content_key:
        revision = prior[0] + 1
    else:
        revision = prior[0]
    channel.queue_rows[(identity.kind, identity.operation_id, identity.sequence)] = (
        revision,
        content_key,
    )
    operation_ref = mint_ref(
        service.secret,
        "operation-ref",
        authorization,
        ar_session_id=ar_session_id,
        bridge_epoch=epoch,
        identity=identity,
    )
    cockpit: CockpitQueueIdentity | None = None
    if authorized:
        journal = channel.journal.get(item.operation_id)
        if journal is not None:
            preview, truncated = redacted_preview(journal.text)
            digest = journal.content_digest
        else:
            # The daemon holds no content for this row (it was not submitted
            # through the typed L3 submit): the held-content preview/digest are
            # honestly empty — identification copy, never fabricated content.
            preview, truncated, digest = "", False, _EMPTY_DIGEST
        cockpit = CockpitQueueIdentity(
            withdrawal_ref=mint_ref(
                service.secret,
                "withdrawal-ref",
                authorization,
                ar_session_id=ar_session_id,
                bridge_epoch=epoch,
                identity=identity,
            ),
            redacted_preview=preview,
            preview_truncated=truncated,
            content_digest=digest,
        )
    return OperationQueueItem(
        operation_ref=operation_ref,
        revision=revision,
        sequence=item.sequence,
        kind=item.kind,
        source=source,
        phase=phase,
        withdrawable=authorized,
        safe_label=f"{item.kind} · {source}",
        cockpit=cockpit,
    )


__all__ = ["operation_queue"]
