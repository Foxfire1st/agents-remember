"""Publishing a launch's two hand-over files: the message and the complete object beside it.

The first message names both, so both exist before the start is sent, and a launch that cannot
publish them sends nothing (MIK-R193 rules 2 and 3). Every target the launch would touch is
checked before the first of them is written: a request that already holds other bytes is refused
with both files as they were, never with one of them created beside a refusal.

The first message names the message's own file through this request's bounded locator
(:mod:`agents_remember.worktrees.role_handover_locator`), so the recovery note that repeats that path
does not grow with the folder the report lives in. The locator is settled before the files are
written and refused when it would lead to another folder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.cli.immutable_artifact import canonical_path, preflight
from agents_remember.cli.role_handover_artifacts import (
    preflight_handover_artifact,
    write_handover_artifact,
)
from agents_remember.cli.role_handover_file import (
    handover_file_reference,
    handover_file_refusal,
    handover_file_target,
    write_handover_file,
)
from agents_remember.worktrees.role_handover_locator import ensure_locator, locator_directory


def publish_handover_files(
    report_path: str,
    prompt: str,
    handover_content: str,
    *,
    expected: dict[str, Any],
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write the message's artifact and the complete hand-over, or write neither.

    ``expected`` is the complete hand-over reference the compiled message already names; a text
    that no longer matches it refuses the launch before any file is created, and so does a target
    that already holds other bytes. ``request_id`` owns the locator the artifact is named
    through: a repeat of the same request reuses it, and one that would lead to another folder
    refuses the launch before anything is written.
    """

    planned = handover_file_reference(report_path, handover_content)
    # The digest is what the compiled message promises about the file it names; the path is the
    # one place that plans both, so only a changed text can disagree here.
    if planned["sha256"] != expected.get("sha256"):
        raise ValueError(
            "The complete hand-over changed between its compilation and its write, so no "
            "start was sent. Start the role again under a new request id."
        )
    target, body = handover_file_target(report_path, handover_content)
    # Both file positions are settled before the first of them is created.
    preflight_handover_artifact(report_path, prompt)
    preflight(target, body, handover_file_refusal(target))
    locator = locator_directory(request_id)
    ensure_locator(locator, canonical_path(Path(report_path)).parent)
    artifact = write_handover_artifact(report_path, prompt, locator=locator)
    handover_file = write_handover_file(report_path, handover_content)
    return artifact, handover_file
