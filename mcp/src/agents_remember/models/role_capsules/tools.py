"""Requested tool identities, narrowed against the admitted permission policy.

A capsule *requests* capabilities; it never grants them. This module is the whole
of that boundary: it turns the tool identities the compiled sources declare into
:class:`~agents_remember.models.role_capsules.types.CapsuleToolRequest` values, and
refuses rather than silently widening when a request falls outside the snapshot an
existing AR owner admitted. Nothing here can add a tool the policy did not already
permit, which is why the check is a subset test and not a merge.
"""

from __future__ import annotations

from collections.abc import Sequence

from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.statuses import STATUS_TOOL_REQUEST_NOT_PERMITTED
from agents_remember.models.role_capsules.types import (
    CapsuleBinding,
    CapsuleToolId,
    CapsuleToolRequest,
)


def narrow_tool_requests(
    declared: Sequence[tuple[CapsuleToolId, str]],
    binding: CapsuleBinding,
) -> tuple[CapsuleToolRequest, ...]:
    """Return the deduplicated, policy-permitted requests in stable tool-id order.

    ``declared`` pairs each requested tool id with the identity of the source that
    asked for it, so the request carries its own provenance. An empty declaration
    is not a failure: a role that requests nothing compiles to no requests.
    """

    refused = sorted(
        tool_id for tool_id, _ in declared if not binding.admitted.tool_policy.permits(tool_id)
    )
    if refused:
        raise CapsuleCompilationError(
            status=STATUS_TOOL_REQUEST_NOT_PERMITTED,
            detail=(
                "the compiled sources request tool identities the admitted permission snapshot does "
                f"not permit: {_quoted(refused)}"
            ),
            next_action=(
                "the capsule requests tools, it never grants them; admit the capability in the "
                "existing permission policy before it can be requested"
            ),
        )
    first_authority: dict[CapsuleToolId, str] = {}
    for tool_id, authority in declared:
        first_authority.setdefault(tool_id, authority)
    return tuple(
        CapsuleToolRequest(tool_id=tool_id, authority=first_authority[tool_id])
        for tool_id in sorted(first_authority)
    )


def _quoted(names: Sequence[str]) -> str:
    return ", ".join(repr(name) for name in names) if names else "<none>"


__all__ = ["narrow_tool_requests"]
