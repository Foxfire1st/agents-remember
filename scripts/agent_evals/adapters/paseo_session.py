"""Native actor/provider identity from the exact fixture's Paseo stored agent.

Paseo 0.12 AgentStorage.buildRecordPath/projectDirNameFromCwd and
STORED_AGENT_SCHEMA.persistence.sessionId are the source contract. Public
PaseoAgentHandle.runtimeInfo carries this same ID; AR's agent-get projection
omits it. Read only this created actor's record, never an agent inventory.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError
from agents_remember.kernel.sidecar_pairing import confine_non_symlink_rel

from ..artifacts import read_object, sha256_file
from .models import Harness, SourceFile


@dataclass(frozen=True)
class PaseoSession:
    session_id: str
    source: SourceFile
    projection: dict[str, Any]


def paseo_session(home: Path, project: Path, owner: str, harness: Harness) -> PaseoSession:
    """Validate native persisted identity, including agreement with runtime info."""
    if str(uuid.UUID(owner)) != owner:
        raise AuthorityError("Paseo actor identity is not a canonical UUID")
    # This tooling runs on the admitted POSIX native host. Exact native storage
    # encoding for an absolute POSIX cwd (not a guessed provider session ID).
    project_name = re.sub(r"[\\/]+", "-", str(project.resolve()).strip("/\\")) or "root"
    relative = f"agents/{project_name}/{owner}.json"
    record_path = home / confine_non_symlink_rel(home, relative)
    digest = sha256_file(record_path)
    value = read_object(record_path)
    if sha256_file(record_path) != digest:
        raise AuthorityError("native actor metadata changed during identity capture")
    if (value.get("id"), value.get("provider"), value.get("cwd")) != (
        owner,
        harness,
        str(project.resolve()),
    ):
        raise AuthorityError("native actor metadata differs from bound actor/provider/project")
    persisted = value.get("persistence")
    if not isinstance(persisted, dict) or persisted.get("provider") != harness:
        raise AuthorityError("native actor has no matching provider persistence identity")
    identity = persisted.get("sessionId")
    if not isinstance(identity, str) or not identity:
        raise AuthorityError("native actor provider session identity is absent")
    runtime = value.get("runtimeInfo")
    if isinstance(runtime, dict) and (runtime.get("provider"), runtime.get("sessionId")) != (
        harness,
        identity,
    ):
        raise AuthorityError("native actor runtime and persisted provider identities disagree")
    projection = {
        "id": value["id"],
        "provider": value["provider"],
        "cwd": value["cwd"],
        "persistence": {"provider": harness, "sessionId": identity},
        "runtimeInfo": {
            key: runtime.get(key)
            for key in ("provider", "sessionId", "model", "thinkingOptionId", "modeId")
        }
        if isinstance(runtime, dict)
        else None,
    }
    return PaseoSession(identity, SourceFile(str(record_path), digest), projection)
