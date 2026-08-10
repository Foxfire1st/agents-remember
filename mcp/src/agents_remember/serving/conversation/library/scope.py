"""Canonical allowed project scope authority for the conversation library (260718-CHATS-L2).

``cwd`` is canonicalized against the caller's configured workspace root: a raw query path may
narrow the authorized scope but can never grant a new native-history scope (design section 6.8).
Traversal, symlink escape, cross-repo, and non-directory requests are rejected before any native
store is touched. The query digest binds harness + canonical scope + the normalized sort into
every scope, so cursors minted under one (harness, scope, query) triple can never page another.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
    ConversationLibraryScope,
    HarnessId,
)
from agents_remember.serving.conversation.library.errors import (
    InvalidLibraryCursorError,
    LibraryScopeError,
)

LIBRARY_SORT = "last-activity-desc"
"""The only normalized list ordering this leaf supports (native recency)."""


def canonical_library_scope(
    authorization: AuthorizationBinding,
    harness_id: HarnessId,
    requested_cwd: str | None,
    *,
    workspace_root: Path,
) -> ConversationLibraryScope:
    """Resolve the server-side canonical scope; narrow-only, fail closed on escape.

    The default scope is the resolved workspace root itself. A requested cwd must resolve
    (following symlinks) to an existing directory inside that root; anything else raises
    ``LibraryScopeError`` (an authority violation) rather than being clamped or guessed.
    """

    root = workspace_root.resolve()
    if requested_cwd is None or not requested_cwd.strip():
        canonical = root
    else:
        candidate = Path(requested_cwd).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            # ValueError covers embedded null bytes and other malformed path input; it must
            # surface as the typed scope refusal, never a raw 500 (review F2 / O4).
            raise LibraryScopeError(
                f"requested cwd {requested_cwd!r} does not resolve to a real directory"
            ) from exc
        if not resolved.is_dir():
            raise LibraryScopeError(f"requested cwd {requested_cwd!r} is not a directory")
        if resolved != root and root not in resolved.parents:
            raise LibraryScopeError(
                f"requested cwd {requested_cwd!r} escapes the authorized workspace scope"
            )
        canonical = resolved
    return ConversationLibraryScope(
        authorization=authorization,
        harness_id=harness_id,
        canonical_project_scope=str(canonical),
        query_digest=query_digest(harness_id, str(canonical)),
    )


def query_digest(harness_id: HarnessId, canonical_project_scope: str) -> str:
    """Unkeyed canonical digest of the normalized (harness, scope, sort) query triple."""

    canonical = json.dumps(
        {
            "harnessId": harness_id,
            "canonicalProjectScope": canonical_project_scope,
            "sort": LIBRARY_SORT,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def clamp_limit(value: int | None, *, default: int, maximum: int) -> int:
    """One bounded page-size rule for every library route (resource guard)."""

    if value is None:
        return default
    if isinstance(value, bool) or value < 1:
        raise InvalidLibraryCursorError("limit must be a positive integer")
    return min(value, maximum)


__all__ = ["LIBRARY_SORT", "canonical_library_scope", "clamp_limit", "query_digest"]
