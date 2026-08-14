"""The ``turn/start`` request shape and the receipts one Codex submission produces.

Everything here is derived from a reserved submission and the settings it was reserved
under: no adapter state is read or written, so the wire shape and the receipt wording can be
checked without a connection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.errors import CodexAppServerError
from agents_remember.models.conversations.control_wire import (
    AssetReference,
    ControlOperationRef,
    SubmissionReceipt,
    read_asset_bytes,
)
from agents_remember.serving.codex_app_server_protocol import JsonObject
from agents_remember.serving.codex_app_server_session import CodexAppServerSettings
from agents_remember.serving.codex_app_server_state import SubmissionEvidence
from agents_remember.serving.harness_control_models import (
    PromptRequest,
)


@dataclass(frozen=True)
class StartedTurn:
    """What ``turn/start`` answered: which turn began, in what state, for which operation.

    The buffered first frame belongs with them -- it is the notification that arrived before the
    response and is only interpretable against this turn id -- so the four settle one submission
    together.
    """

    turn_id: str
    status: str
    operation: ControlOperationRef
    buffered: JsonObject | None


def verified_asset_path(asset: AssetReference) -> str:
    """Re-verify the staged file at construction before the native process sees its path."""

    if asset.spool_path is None:
        raise CodexAppServerError("Codex asset submission requires a verified spool path")
    digest, size, _data = read_asset_bytes(asset.spool_path)
    if size != asset.byte_size or digest != asset.sha256:
        raise CodexAppServerError(
            f"Codex asset {asset.asset_id!r} failed verification at construction"
        )
    return str(asset.spool_path)


def turn_input(request: PromptRequest) -> list[JsonObject]:
    """Build the turn input blocks; verified local images ride as native paths."""

    blocks: list[JsonObject] = [{"type": "text", "text": request.text}]
    for asset in request.assets:
        blocks.append({"type": "localImage", "path": verified_asset_path(asset)})
    return blocks


def turn_start_params(
    evidence: SubmissionEvidence,
    *,
    thread_id: str,
    cwd: Path,
    settings: CodexAppServerSettings,
) -> JsonObject:
    """The ``turn/start`` params for one submission, carrying only the policies that are set.

    An unset approval/sandbox policy is omitted rather than sent as null, so the server keeps
    whatever the thread was configured with instead of being told to clear it.
    """

    params: JsonObject = {
        "threadId": thread_id,
        "input": turn_input(evidence.request),
        "clientUserMessageId": evidence.request.request_id,
        "model": evidence.model.model,
        "cwd": str(cwd),
        "effort": evidence.effort,
    }
    for key, value in (
        ("approvalPolicy", settings.approval_policy),
        ("approvalsReviewer", settings.approvals_reviewer),
        ("sandboxPolicy", settings.turn_sandbox_policy),
    ):
        if value is not None:
            params[key] = dict(value) if isinstance(value, Mapping) else value
    return params


def rejected_turn_receipt(
    evidence: SubmissionEvidence, *, turn_id: str, status: str
) -> SubmissionReceipt:
    """The receipt for a turn ``turn/start`` itself reported terminal."""

    return SubmissionReceipt(
        request_id=evidence.request.request_id,
        acceptance="rejected",
        submitted_at=evidence.request.submitted_at,
        vendor_correlation_id=turn_id,
        detail=f"Codex turn/start returned terminal status {status!r}",
        raw={
            "method": "turn/start",
            "clientUserMessageId": evidence.request.request_id,
            "turnStatus": status,
        },
    )


def accepted_turn_receipt(
    evidence: SubmissionEvidence,
    *,
    turn_id: str,
    accepted_at: str,
    terminal_completion: bool,
) -> SubmissionReceipt:
    """The receipt for a turn that started, recording whether it also finished inside the call."""

    raw: JsonObject = {
        "method": "turn/start",
        "clientUserMessageId": evidence.request.request_id,
        "terminalCompletion": terminal_completion,
    }
    if evidence.request.assets:
        raw["assetIds"] = [asset.asset_id for asset in evidence.request.assets]
    return SubmissionReceipt(
        request_id=evidence.request.request_id,
        acceptance="immediate",
        submitted_at=evidence.request.submitted_at,
        vendor_correlation_id=turn_id,
        accepted_at=accepted_at,
        raw=raw,
    )
