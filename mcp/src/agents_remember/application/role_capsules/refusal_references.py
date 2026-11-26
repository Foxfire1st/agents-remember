"""Re-export facade for MCP refusal guidance pointers.

The canonical registry and helpers live in
:mod:`agents_remember.models.role_capsules.refusal_references` so lower-rank
layers (such as ``worktrees``) can attach pointers without upward imports.
"""

from __future__ import annotations

from agents_remember.models.role_capsules.refusal_references import (
    REFUSAL_REFERENCES,
    SKILL_URI_PREFIX,
    refusal_reference,
    with_refusal_reference,
)

__all__ = [
    "REFUSAL_REFERENCES",
    "SKILL_URI_PREFIX",
    "refusal_reference",
    "with_refusal_reference",
]
