"""Running-session projector factory (260718-CHATS-L1).

Resolves one exact running session from the terminal catalog, proves its
harness/native identity through the production IPC seam, and constructs the
per-harness active projector. Sessions without a protocol control endpoint,
unknown rows, dead sessions, and harnesses without a projector fail closed
with typed statuses; no session state is ever manufactured.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from agents_remember.errors import AgentsRememberError, HarnessControlError
from agents_remember.serving.conversation.models import ActiveConversationRef
from agents_remember.serving.conversation.projectors import (
    HarnessProjector,
    projector_for,
)
from agents_remember.serving.conversation.runtime import ConversationRuntime
from agents_remember.serving.harness_control_client import (
    read_control_snapshot,
    read_submission_authority,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry


class SessionResolutionError(AgentsRememberError):
    """Base for typed session-resolution refusals."""

    http_status: int = 409
    status_slug: str = "unsupported"


class UnknownSessionError(SessionResolutionError):
    http_status = 404
    status_slug = "unknown-session"


class UnsupportedSessionError(SessionResolutionError):
    http_status = 409
    status_slug = "unsupported"


class ControlUnavailableError(SessionResolutionError):
    http_status = 503
    status_slug = "control-unavailable"


def identity_digest(secret: bytes, identity: ActiveConversationRef) -> str:
    """Recomputable server-issued digest for the native identity tuple."""

    canonical = json.dumps(
        [identity.harness_id, identity.vendor_conversation_id, identity.project_scope],
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hmac.new(secret, canonical, hashlib.sha256).hexdigest()[:32]}"


def resolve_running_entry(
    runtime: ConversationRuntime,
    ar_session_id: str,
) -> TerminalCatalogEntry:
    """Resolve the catalog row; mirrors the serving idiom's failure mapping."""

    entry = runtime.catalog.get(ar_session_id)
    if entry is None or entry.status != "running":
        raise UnknownSessionError("unknown or not-running AR session")
    if entry.kind != "harness" or entry.control_endpoint is None:
        raise UnsupportedSessionError(
            "session has no native protocol control endpoint"
        )
    if not runtime.host.has_session(entry.tmux_name):
        raise UnknownSessionError("AR session is not alive")
    return entry


def build_identity(
    entry: TerminalCatalogEntry,
    *,
    secret: bytes,
    bridge_epoch: str,
    snapshot: AdapterSnapshot,
) -> tuple[ActiveConversationRef, HarnessProjector]:
    """Prove harness + native identity from a live bridge snapshot."""

    mapper = projector_for(entry.harness or "")
    if mapper is None:
        raise UnsupportedSessionError(
            f"harness {entry.harness!r} has no structured conversation projector"
        )
    vendor_id = snapshot.vendor_session_id
    if not vendor_id:
        raise UnsupportedSessionError(
            "session has no proven native conversation identity yet"
        )
    identity = ActiveConversationRef(
        harness_id=mapper.harness_id,
        vendor_conversation_id=vendor_id,
        project_scope=str(entry.cwd),
        identity_digest="pending",
        ar_session_id=entry.id,
        bridge_epoch=bridge_epoch,
    )
    digest = identity_digest(secret, identity)
    return identity.model_copy(update={"identity_digest": digest}), mapper


def current_bridge_epoch(entry: TerminalCatalogEntry) -> str:
    """Read the live bridge epoch from the submission authority."""

    try:
        return read_submission_authority(entry).bridge_epoch
    except HarnessControlError as exc:
        raise ControlUnavailableError(str(exc)) from exc


def live_snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
    """Read the live adapter snapshot; IPC failures map to control-unavailable."""

    try:
        return read_control_snapshot(entry)
    except HarnessControlError as exc:
        raise ControlUnavailableError(str(exc)) from exc


__all__ = [
    "ControlUnavailableError",
    "SessionResolutionError",
    "UnknownSessionError",
    "UnsupportedSessionError",
    "build_identity",
    "current_bridge_epoch",
    "identity_digest",
    "live_snapshot",
    "resolve_running_entry",
]
