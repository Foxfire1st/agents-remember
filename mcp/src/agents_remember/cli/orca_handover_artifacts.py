"""Immutable task-local projections of compiled native Orca role handovers."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Any

from agents_remember.cli.orca_task_preparation import PreparedOrcaRoleHandover

MAX_HANDOVER_ARTIFACT_BYTES = 262_144


def build_role_handover_artifact(
    role_handover: PreparedOrcaRoleHandover,
) -> dict[str, Any]:
    """Combine canonical task handover data with exact Orca terminal inputs."""

    prepared = role_handover.handover
    workspace = role_handover.workspace
    agent_id = role_handover.agent_id
    agent_arg_tokens = role_handover.agent_arg_tokens
    return {
        "schema": "ar-orca-prepared-role-handover/v1",
        "requestId": str(role_handover.request_id),
        "prompt": prepared["prompt"],
        "handover": prepared["handover"],
        "nativeLaunch": {
            "agent": agent_id,
            "target": {"kind": "existing", "worktree": workspace["selector"]},
            "workspaceSelector": workspace["selector"],
            "workspacePath": workspace["path"],
            "sessionOptions": role_handover.session_options,
            "agentArgs": shlex.join(agent_arg_tokens) if agent_arg_tokens else None,
        },
    }


def write_role_handover_artifact(report_path: str, artifact: dict[str, Any]) -> dict[str, str]:
    """Write or reuse one bounded, immutable handover beside its canonical task report."""

    path = Path(report_path).with_suffix(".handover.json").resolve(strict=False)
    body = (
        json.dumps(artifact, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(body) > MAX_HANDOVER_ARTIFACT_BYTES:
        raise ValueError("The compiled role handover exceeds the task artifact size limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    reference = {"path": path.as_posix(), "sha256": hashlib.sha256(body).hexdigest()}
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if not _matches(path, body):
                raise ValueError(
                    "This role request already has different handover content."
                ) from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return reference


def read_role_handover_artifact(report_path: str, reference: dict[str, str]) -> dict[str, Any]:
    """Read one task-local handover only after checking its path and raw-byte digest."""

    expected = Path(report_path).with_suffix(".handover.json").resolve(strict=False)
    path = Path(reference["path"]).resolve(strict=False)
    if path != expected:
        raise ValueError("The saved handover projection does not match its canonical task report.")
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ValueError("The saved native handover artifact is unavailable.") from error
    if not stat.S_ISREG(mode):
        raise ValueError("The saved native handover artifact is not a regular file.")
    body = path.read_bytes()
    if len(body) > MAX_HANDOVER_ARTIFACT_BYTES:
        raise ValueError("The saved native handover artifact exceeds the size limit.")
    if hashlib.sha256(body).hexdigest() != reference["sha256"]:
        raise ValueError("The saved native handover artifact failed its SHA-256 check.")
    artifact = json.loads(body)
    if (
        not isinstance(artifact, dict)
        or artifact.get("schema") != "ar-orca-prepared-role-handover/v1"
    ):
        raise ValueError("The saved native handover artifact has an unsupported schema.")
    return artifact


def _matches(path: Path, expected: bytes) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError("The task handover artifact path is not a regular file.")
    return path.read_bytes() == expected
