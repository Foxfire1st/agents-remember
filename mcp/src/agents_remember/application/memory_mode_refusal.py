"""How a removed-memory-mode refusal is published to the operator at a tool boundary.

The refusal itself is raised by the contract reader, which is the right place to decide that a
document recording ``internal`` must not be read. It is the *wrong* place to stop: the reader is
five frames below the tool that answers the developer, and several of those boundaries read a
contract inside ``except (ContractError, OSError, UnicodeError, ValueError)`` because a document
that is not a contract at all is one of their ordinary outcomes. ``MemoryModeUnsupportedError``
subclasses ``ValueError``, so without this module a contract recording the removed mode reaches
the operator as "unreadable or invalid" -- true, useless, and missing the one thing the developer
needs: which value is recorded, what is supported, and how to get out.

One reporter, used by every such boundary, so the removal reads identically wherever it surfaces
and a new boundary cannot invent a sixth spelling of it.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.errors import MemoryModeUnsupportedError
from agents_remember.kernel.memory_mode import memory_mode_refusal_message

REMOVED_MODE_EVIDENCE_STATE = "removed-mode"
"""The ``observed.state`` that marks a failure evidence block as a removal refusal."""


def memory_mode_refusal_evidence(error: MemoryModeUnsupportedError) -> dict[str, object]:
    """The removal facts in the public failure-evidence ``observed`` shape.

    ``public_failure_evidence`` already accepts an ``observed`` mapping, so the facts travel in
    the established evidence envelope rather than as a parallel payload shape.
    """
    observed: dict[str, object] = {
        "state": REMOVED_MODE_EVIDENCE_STATE,
        "requested": error.requested,
        "supported": list(error.supported),
        "remedies": list(error.remedies),
    }
    if error.artifact is not None:
        observed["artifact"] = error.artifact
    return observed


def memory_mode_refusal_payload(error: MemoryModeUnsupportedError) -> dict[str, object]:
    """The same facts as top-level keys, for a boundary that owns the whole payload.

    ``detail`` is the refusal's own operator-legible text, which names the removal, the supported
    set and the route out; ``decisionSurface`` repeats it where a boundary's shape requires one.
    """
    fields: dict[str, object] = {
        "status": error.status,
        "summary": error.detail,
        "detail": error.detail,
        "requested": error.requested,
        "supported": list(error.supported),
        "remedies": list(error.remedies),
        "nextAction": "developer-decision",
        "developerDecisionRequired": True,
        "decisionSurface": error.detail,
    }
    if error.artifact is not None:
        fields["artifact"] = error.artifact
    return fields


def removed_memory_mode_fields_from_evidence(
    read_failure: Mapping[str, object],
) -> dict[str, object] | None:
    """Rebuild the operator fields from a failure-evidence block, or ``None`` if it is not one.

    One boundary assembles the status payload and another projects it, so the projection
    sometimes holds the *evidence* rather than the exception. Rebuilding the fields from the
    evidence keeps the reader's verdict restorable in both -- and the evidence state is written
    and read through the one constant, so the two directions cannot drift apart.
    """
    observed = read_failure.get("observed")
    if not isinstance(observed, Mapping) or observed.get("state") != REMOVED_MODE_EVIDENCE_STATE:
        return None
    remedies = observed.get("remedies")
    supported = observed.get("supported")
    requested = observed.get("requested")
    if not isinstance(requested, str) or not isinstance(supported, Mapping | list | tuple):
        return None
    # The detail is rebuilt from the vocabulary rather than carried in the evidence, so the
    # refusal text has one source and a hand-built evidence block cannot invent a different one.
    artifact = observed.get("artifact")
    detail = memory_mode_refusal_message(
        requested, artifact=artifact if isinstance(artifact, str) else None
    )
    fields: dict[str, object] = {
        "state": MemoryModeUnsupportedError.status,
        "status": MemoryModeUnsupportedError.status,
        "summary": detail,
        "detail": detail,
        "decisionSurface": detail,
        "requested": requested,
        "supported": list(supported),
        "remedies": list(remedies) if isinstance(remedies, list | tuple) else [],
        "nextAction": "developer-decision",
        "developerDecisionRequired": True,
    }
    if isinstance(artifact, str):
        fields["artifact"] = artifact
    return fields


__all__ = [
    "REMOVED_MODE_EVIDENCE_STATE",
    "memory_mode_refusal_evidence",
    "memory_mode_refusal_payload",
    "removed_memory_mode_fields_from_evidence",
]
