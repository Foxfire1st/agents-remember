"""Withdrawal-recovery assembly: content, digests, and recovery refs (260718-CHATS-L3).

The exact body a successful withdrawal retains: the substrate's pre-tombstone
payload first, this authority's submit journal as the source of last resort,
and the authority-parity content digest. The attachment recovery refs mint the
opaque one-use exchange identities with full alt provenance. Everything here is
pure assembly over the bounded retention record; the lifecycle policy lives in
``withdrawals.py``.
"""

from __future__ import annotations

from agents_remember.serving.conversation.control import attachments
from agents_remember.serving.conversation.control.previews import payload_digest
from agents_remember.serving.conversation.control.refs import (
    OperationIdentity,
    RefBinding,
    RefTarget,
    mint_ref,
)
from agents_remember.serving.conversation.control.service import (
    ControlChannel,
    ControlScope,
    JournalEntry,
)
from agents_remember.serving.conversation.models import (
    AttachmentRecoveryRef,
    WithdrawalRecovery,
)
from agents_remember.serving.harness_control_models import (
    AssetReference,
)
from agents_remember.serving.harness_control_models import (
    WithdrawalRecovery as SubstrateWithdrawalRecovery,
)

EMPTY_DIGEST = payload_digest("")


def recovery_text(
    recovery: SubstrateWithdrawalRecovery | None, journal: JournalEntry | None
) -> str | None:
    """Substrate payload first; the submit journal is the source of last resort."""

    if recovery is not None and recovery.text is not None:
        return recovery.text
    return journal.text if journal is not None else None


def recovery_digest(
    text: str | None,
    substrate_assets: tuple[AssetReference, ...],
    journal: JournalEntry | None,
) -> str:
    """The recovery's content digest: substrate parity when text is present."""

    if text is not None:
        return payload_digest(text, substrate_assets)
    if journal is not None:
        return journal.content_digest
    return EMPTY_DIGEST


def recovery_payload(
    recovery_ref: str,
    text: str | None,
    digest: str,
    journal: JournalEntry | None,
    attachment_refs: tuple[AttachmentRecoveryRef, ...],
) -> WithdrawalRecovery:
    return WithdrawalRecovery(
        recovery_ref=recovery_ref,
        text=text if text is not None else "",
        content_digest=digest,
        submitted_draft_revision=journal.draft_revision if journal is not None else None,
        attachments=attachment_refs,
    )


def recover_attachment_refs(
    scope: ControlScope,
    channel: ControlChannel,
    identity: OperationIdentity,
    withdraw_request_id: str,
    expires_at: str,
) -> tuple[AttachmentRecoveryRef, ...]:
    recoverable = attachments.mark_recoverable(
        channel, identity.operation_id, recovery_expires_at=expires_at
    )
    return tuple(
        attachment_recovery_ref(scope, identity, withdraw_request_id, asset, expires_at)
        for asset in recoverable
    )


def attachment_recovery_ref(
    scope: ControlScope,
    identity: OperationIdentity,
    withdraw_request_id: str,
    asset: attachments.AssetRecord,
    expires_at: str,
) -> AttachmentRecoveryRef:
    return AttachmentRecoveryRef(
        recovery_asset_ref=mint_ref(
            scope.service.secret,
            "recovery-asset-ref",
            RefBinding(scope.authorization, scope.ar_session_id, scope.epoch),
            RefTarget(
                identity=identity,
                withdraw_request_id=withdraw_request_id,
                asset_id=asset.asset_id,
            ),
        ),
        revision=1,
        kind=asset.kind,
        name=asset.name,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        alt=asset.alt,
        alt_provenance=asset.alt_provenance,
        expires_at=expires_at,
    )
