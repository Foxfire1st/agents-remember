"""Restoring a saved launch's hand-over files before its first message is sent again.

A saved launch is retried, or a held seat opens, from the receipt alone: the message, the file
that holds it and the file that holds the complete hand-over are all written again from what the
receipt stored (MIK-R193 rules 2, 3 and 4). Every reference and every target is checked before the
first byte is written, so a refusal leaves the receipt and all its files exactly as they were, and
says what has to be put right before the same request is retried.

Every refusal here is a :class:`HandoverFileRefusal`, a :class:`ValueError`: the held-seat opener
converts a ``ValueError`` into a plain ``cannot-open`` answer and releases its delivery claim, so a
refusal type it does not know would escape it and keep the claim. The retry route turns this same
type into its 409 answer, while an unrelated refusal (a frozen detail file) stays what it was.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.cli.immutable_artifact import preflight
from agents_remember.cli.role_handover_artifacts import (
    artifact_refusal,
    checked_handover_artifact_target,
    checked_step_up_target,
    restore_handover_artifact,
    restore_step_up_artifact,
    takeover_refusal,
)
from agents_remember.cli.role_handover_file import (
    checked_handover_target,
    handover_file_refusal,
    restore_handover_file,
)
from agents_remember.cli.role_instruction_details import validate_instruction_detail_artifacts


class HandoverFileRefusal(ValueError):
    """A saved launch's hand-over files cannot be restored as the receipt recorded them."""


def restore_launch_files(receipt_path: Path, receipt: dict[str, Any], message: str) -> None:
    """Write a saved launch's files back, or refuse before any of them is written.

    ``message`` is the first message the receipt saved for this seat: the held seat's opening
    handover, or the launch call's own prompt.
    """

    artifact = receipt.get("handoverArtifact")
    handover_file = saved_handover_file(receipt, receipt_path)
    try:
        # The complete hand-over is checked first: its refusal names the file the agent names too.
        file_target = (
            checked_handover_target(handover_file, message, receipt=receipt_path)
            if handover_file is not None
            else None
        )
        artifact_target = checked_handover_artifact_target(
            artifact if isinstance(artifact, dict) else {},
            message,
            receipt=receipt_path,
            handover_file=handover_file,
        )
        step_up_target = checked_step_up_target(receipt, message, receipt=receipt_path)
    except HandoverFileRefusal:
        raise
    except ValueError as error:
        raise HandoverFileRefusal(str(error)) from error
    # Every target is settled before the first write: a refusal must not leave one file created.
    preflight(*artifact_target, artifact_refusal(artifact_target[0]))
    if step_up_target is not None:
        preflight(*step_up_target, takeover_refusal(step_up_target[0]))
    if file_target is not None:
        preflight(*file_target, handover_file_refusal(file_target[0]))
    validate_instruction_detail_artifacts(receipt)
    restore_handover_artifact(
        artifact if isinstance(artifact, dict) else {},
        message,
        receipt=receipt_path,
        handover_file=handover_file,
    )
    restore_step_up_artifact(receipt, message, receipt=receipt_path)
    if handover_file is not None:
        restore_handover_file(handover_file, message, receipt=receipt_path)


def saved_handover_file(receipt: dict[str, Any], path: Path) -> dict[str, Any] | None:
    """The complete hand-over reference of a receipt, refused when it is not one."""

    if "handoverFile" not in receipt:
        return None
    saved = receipt.get("handoverFile")
    if not isinstance(saved, dict):
        raise ValueError(
            f"The receipt {path} records a complete hand-over whose reference is not an object, "
            "so the saved first message cannot be checked against its file and nothing was sent. "
            "Put the reference back as the launch wrote it and retry."
        )
    return saved
