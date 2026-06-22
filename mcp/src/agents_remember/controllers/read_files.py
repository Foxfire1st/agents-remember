"""Controller for the ``read_ar_files`` tool (slice 07).

Batch paired source+onboarding reads of repo-relative paths inside an AR-managed
repo. Resolution lives here (not in the payload module) so a later dashboard
``GET /api/files`` route can reuse it. The tool surface is thin: validate the
request, resolve the coordination context once, then per file confine the path,
read source (full or range), and look up onboarding via the storage mode plus the
precomputed route index. On top of the per-file onboarding bodies it auto-attaches
the repo overview and the governing route-overview chain, deduplicated per
lifecycle, and emits a facts-only ``read.packet`` (paths/ranges/statuses/bytes --
never content).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.controllers._guards import require_repo
from agents_remember.errors import AuthorityError
from agents_remember.kernel import filesystem
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.kernel.coordination_context_resolver import (
    resolve_coordination_context,
    resolve_storage_for_source,
)
from agents_remember.kernel.onboarding_doc import meaningful_body
from agents_remember.kernel.route_index import (
    INDEX_FILE_NAME,
    ROUTE_OVERVIEW_NAME,
    sidecar_status,
)
from agents_remember.mcp.config import McpRuntimeConfig, path_is_relative_to
from agents_remember.memory_quality.integrity.onboarding_drift_check.discovery import (
    mirror_onboarding_path,
)
from agents_remember.observer.ambient import ambient
from agents_remember.observer.events import now_iso
from agents_remember.observer.paths import observer_root

MAX_FILES = 5
# Storage modes whose onboarding body lives in a sidecar markdown file. This
# repo's own memory is ``external``, which also writes sidecars, so it is treated
# as sidecar here even though ``is_sidecar_storage`` (used for missing-onboarding
# gating) returns True only for ``repo-sidecar`` / ``memory-repo``.
_SIDECAR_STORAGE_MODES = {"repo-sidecar", "memory-repo", "external"}

# The auto-attached front-door pieces, keyed for the served ledger.
_KIND_REPO_OVERVIEW = "repository_overview"
_KIND_ROUTE_OVERVIEW = "route_overview"

# The workspace marker whose presence clears the served set for the current
# lifecycle on the next read. The producer hook that DROPS it lands in slice-07
# S5 (Probe B); until then ``refresh=true`` is the working manual reset and this
# constant only names the marker this consumer watches for.
_COMPACT_RESET_MARKER = "compact-reset.json"


@dataclass(frozen=True)
class _FileRequest:
    path: str
    full: bool
    start_line: int
    end_line: int
    onboarding_requested: bool


def read_ar_files_tool(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    files: list[dict[str, Any]],
    refresh: bool = False,
    _context: CoordinationContext | None = None,
) -> dict[str, Any]:
    """Resolve a batch of paired source+onboarding reads. Returns a plain dict.

    ``_context`` is a test seam: callers normally let the resolver run, but tests
    inject a pre-built :class:`CoordinationContext` to isolate one storage mode.
    """
    repo = require_repo(config, repo_id)
    if len(files) > MAX_FILES:
        raise AuthorityError(
            f"read_ar_files accepts at most {MAX_FILES} files per call; got {len(files)}"
        )
    requests = [_parse_file_request(entry) for entry in files]

    context = _context or resolve_coordination_context(
        code_repository_name=repo.repo_id,
        workspace_root=config.workspace_root,
        coordination_root=config.coordination_root,
        code_repository_root=repo.path,
        contract_path=repo.contract_path,
    )

    amb = ambient()
    lifecycle_id = amb.current.id if (amb is not None and amb.current is not None) else None
    _maybe_reset_served(config, amb, lifecycle_id, refresh=refresh)

    file_results: list[dict[str, Any]] = []
    event_files: list[dict[str, Any]] = []
    attach_any_onboarding = False
    for request in requests:
        result, facts, attach = _read_one(context, repo.path, request)
        file_results.append(result)
        event_files.append(facts)
        attach_any_onboarding = attach_any_onboarding or attach

    payload: dict[str, Any] = {
        "ok": True,
        "operation": "read_ar_files",
        "repoId": repo.repo_id,
        "files": file_results,
    }
    if attach_any_onboarding:
        served = _attach_front_door(context, repo.path, requests, amb, lifecycle_id)
        if served.get("repository_overview") is not None:
            payload["repository_overview"] = served["repository_overview"]
        if served.get("route_overviews"):
            payload["route_overviews"] = served["route_overviews"]

    if amb is not None:
        amb.emit_read_packet(event_files)
    return payload


# --- request parsing -------------------------------------------------------


def _parse_file_request(entry: dict[str, Any]) -> _FileRequest:
    path = entry.get("path")
    if not isinstance(path, str) or not path:
        raise AuthorityError("each file entry must carry a non-empty 'path'")
    onboarding = entry.get("onboarding", True)
    onboarding_requested = onboarding is not False
    source = entry.get("source", "full")
    if source == "full" or source is None:
        return _FileRequest(path, True, 1, 0, onboarding_requested)
    if isinstance(source, dict):
        start = source.get("startLine")
        end = source.get("endLine")
        if not isinstance(start, int) or not isinstance(end, int):
            raise AuthorityError(
                f"source range for {path!r} needs integer startLine and endLine"
            )
        if start < 1:
            raise AuthorityError(f"startLine for {path!r} must be >= 1")
        if end < 1:
            raise AuthorityError(f"endLine for {path!r} must be >= 1")
        if end < start:
            raise AuthorityError(
                f"endLine for {path!r} must be >= startLine (got {start}..{end})"
            )
        return _FileRequest(path, False, start, end, onboarding_requested)
    raise AuthorityError(f"source for {path!r} must be 'full' or {{startLine, endLine}}")


# --- per-file read ---------------------------------------------------------


def _read_one(
    context: CoordinationContext, code_root: Path, request: _FileRequest
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    rel = _confined_rel(code_root, request.path)
    source, byte_count = _read_source(code_root / rel, request)
    status, onboarding, attach = _resolve_onboarding(context, rel, request)

    result: dict[str, Any] = {"path": request.path, "status": status}
    if source is not None:
        result["source"] = source
    if onboarding is not None:
        result["onboarding"] = onboarding

    facts: dict[str, Any] = {
        "path": request.path,
        "lines": "full" if request.full else [request.start_line, request.end_line],
        "status": status,
        "bytes": byte_count,
    }
    return result, facts, attach


def _confined_rel(code_root: Path, requested: str) -> str:
    """Confine a requested path to ``code_root`` and return its posix-relative form.

    Resolves the real path (following ``..`` and symlinks) before the check, so a
    traversal or symlink escape is rejected, not just a literal ``..`` token.
    """
    candidate = Path(requested)
    if candidate.is_absolute():
        raise AuthorityError("file paths must be repo-relative, not absolute")
    resolved = (code_root / candidate).resolve()
    if not path_is_relative_to(resolved, code_root):
        raise AuthorityError(f"path {requested!r} escapes the repository root")
    return resolved.relative_to(code_root.resolve()).as_posix()


def _read_source(source_path: Path, request: _FileRequest) -> tuple[str | None, int]:
    """Read the requested source slice; ``None`` when absent or non-decodable.

    The byte count is the UTF-8 length of what was returned (0 when omitted) --
    a fact for the event, never the content itself.
    """
    if not filesystem.is_file(source_path):
        return None, 0
    try:
        if request.full:
            text = filesystem.read_text(source_path)
        else:
            text = filesystem.read_text_range(
                source_path, request.start_line, request.end_line
            )
    except (UnicodeDecodeError, ValueError, OSError):
        # Non-decodable, malformed range, or unreadable-but-present file: degrade
        # to source-omitted (matching _sidecar_body / _repo_overview) so one bad
        # file never aborts the whole batch.
        return None, 0
    return text, len(text.encode("utf-8"))


def _resolve_onboarding(
    context: CoordinationContext, rel: str, request: _FileRequest
) -> tuple[str, str | None, bool]:
    """Return ``(status, onboarding_body, attach_front_door)`` for one file.

    ``attach_front_door`` is True when this file participates in onboarding
    (so the auto-attached overviews are served), independent of whether its own
    sidecar body resolved.
    """
    if not request.onboarding_requested:
        return "not_requested", None, False

    storage_mode = resolve_storage_for_source(
        rel, context.storage, context.code_repository_name
    )
    if storage_mode == "disabled":
        return "disabled", None, True
    if storage_mode not in _SIDECAR_STORAGE_MODES:
        # inline (and any non-sidecar mode) cannot yield a sidecar body here.
        return "unsupported", None, True

    index_status = _route_sidecar_status(context.onboarding_root, rel)
    if index_status == "present":
        body = _sidecar_body(context.onboarding_root, rel)
        if body is not None:
            return "found", body, True
        # Route index says covered but the sidecar is unreadable; report missing
        # without probing further.
        return "missing", None, True
    # absent (in-scope, uncovered) and out-of-scope both report missing without
    # probing the filesystem for an unrelated sidecar (pre-resolved decision 4).
    return "missing", None, True


def _route_sidecar_status(onboarding_root: Path, rel: str) -> str:
    """Map the route index's sidecar status, degrading gracefully when absent.

    Walks the governing route-index chain nearest-first; the first index whose
    scope covers the path decides. When no governing ``overview.index.json``
    exists, fall back to a direct sidecar-file probe of the mirror path so a
    repo with sidecars but no built index still resolves (the route index is the
    authority when present, never a crash when absent).
    """
    for index in _governing_indexes(onboarding_root, rel):
        status = sidecar_status(rel, index)
        if status in {"present", "absent"}:
            return status
        # out-of-scope: try the next (more general) governing index.
    # No governing index covered the path. Fall back to the mirror probe.
    return "present" if mirror_onboarding_path(onboarding_root, rel).is_file() else "absent"


def _governing_indexes(onboarding_root: Path, rel: str) -> list[dict[str, Any]]:
    """The route-index chain governing ``rel``, nearest folder first then up to root.

    Small private nearest-route prefix match (the route_index public surface is
    consumed read-only, never extended): for ``a/b/c.py`` we check
    ``a/b/overview.index.json``, ``a/overview.index.json``, then the root
    ``overview.index.json``.
    """
    indexes: list[dict[str, Any]] = []
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    routes = [parent]
    while parent:
        parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
        routes.append(parent)
    for route in routes:
        index = _load_route_index(onboarding_root, route)
        if index is not None:
            indexes.append(index)
    return indexes


def _load_route_index(onboarding_root: Path, route: str) -> dict[str, Any] | None:
    index_rel = INDEX_FILE_NAME if route == "" else f"{route}/{INDEX_FILE_NAME}"
    index_path = onboarding_root / index_rel
    if not index_path.is_file():
        return None
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _sidecar_body(onboarding_root: Path, rel: str) -> str | None:
    sidecar_path = mirror_onboarding_path(onboarding_root, rel)
    if not sidecar_path.is_file():
        return None
    try:
        return meaningful_body(filesystem.read_text(sidecar_path))
    except (UnicodeDecodeError, OSError):
        return None


# --- auto-attached, session-deduped front-door -----------------------------


def _attach_front_door(
    context: CoordinationContext,
    code_root: Path,
    requests: list[_FileRequest],
    amb: Any,
    lifecycle_id: str | None,
) -> dict[str, Any]:
    """Build the deduped repo + route overview attachment for this read.

    Each piece is served once per lifecycle, or again when its content hash
    changed; an already-served-unchanged piece is omitted. With no active
    lifecycle there is no ledger, so everything is served (best effort). Each
    request path is run through the same ``_confined_rel`` confinement here, so
    the front-door's governing-route derivation never depends on ``_read_one``
    having confined the path first (it rejects an escape on its own).
    """
    served: dict[str, Any] = {"repository_overview": None, "route_overviews": {}}

    repo_overview = _repo_overview(context)
    if repo_overview is not None and _should_serve(
        amb, lifecycle_id, _KIND_REPO_OVERVIEW, repo_overview["path"], repo_overview["_hash"]
    ):
        served["repository_overview"] = {
            "path": repo_overview["path"],
            "overview": repo_overview["overview"],
        }

    seen_routes: set[str] = set()
    for request in requests:
        if not request.onboarding_requested:
            continue
        rel = _confined_rel(code_root, request.path)
        for route_overview in _governing_route_overviews(context, rel):
            route = route_overview["route"]
            if route in seen_routes:
                continue
            seen_routes.add(route)
            if _should_serve(
                amb,
                lifecycle_id,
                _KIND_ROUTE_OVERVIEW,
                route_overview["path"],
                route_overview["_hash"],
            ):
                served["route_overviews"][route] = {
                    "path": route_overview["path"],
                    "overview": route_overview["overview"],
                }
    return served


def _should_serve(
    amb: Any, lifecycle_id: str | None, kind: str, path: str, content_hash: str
) -> bool:
    """Serve when there is no ledger, or the piece is new/changed; then record it."""
    if amb is None or lifecycle_id is None:
        return True
    if amb.is_served(lifecycle_id, kind, path, content_hash):
        return False
    amb.record_served(lifecycle_id, kind, path, content_hash, ts=now_iso())
    return True


def _repo_overview(context: CoordinationContext) -> dict[str, Any] | None:
    overview_path = context.onboarding_root / ROUTE_OVERVIEW_NAME
    if not overview_path.is_file():
        return None
    try:
        text = filesystem.read_text(overview_path)
    except (UnicodeDecodeError, OSError):
        return None
    return {"path": ROUTE_OVERVIEW_NAME, "overview": text, "_hash": _content_hash(text)}


def _governing_route_overviews(
    context: CoordinationContext, rel: str
) -> list[dict[str, Any]]:
    """The governing route-overview chain for ``rel`` (nearest folder first).

    Reads each governing route's ``overview.md`` directly; the repo-root
    overview is delivered as ``repository_overview`` and excluded here.
    """
    overviews: list[dict[str, Any]] = []
    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
    routes: list[str] = []
    while parent:
        routes.append(parent)
        parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    for route in routes:
        overview_rel = f"{route}/{ROUTE_OVERVIEW_NAME}"
        overview_path = context.onboarding_root / overview_rel
        if not overview_path.is_file():
            continue
        try:
            text = filesystem.read_text(overview_path)
        except (UnicodeDecodeError, OSError):
            continue
        overviews.append(
            {
                "route": route,
                "path": overview_rel,
                "overview": text,
                "_hash": _content_hash(text),
            }
        )
    return overviews


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- compact-reset marker --------------------------------------------------


def _maybe_reset_served(
    config: McpRuntimeConfig,
    amb: Any,
    lifecycle_id: str | None,
    *,
    refresh: bool,
) -> None:
    """Clear the served set on an explicit ``refresh`` or a compaction marker.

    This is the MCP-side CONSUMER of the marker
    (``<observer_root>/workspace/compact-reset.json``); the SessionStart /
    PreCompact hook that WRITES it is deferred to slice-07 S5 with Probe B and
    does NOT exist yet. Until then, ``refresh=true`` is the working manual
    reset. When the producer lands, this consumer follows the
    ``read_setup_progress``-style "absent == no signal" idiom and deletes the
    marker after consuming it so it fires once.
    """
    if amb is None or lifecycle_id is None:
        return
    marker = _reset_marker_path(config)
    marker_present = marker.is_file()
    if refresh or marker_present:
        amb.reset_served(lifecycle_id)
    if marker_present:
        with contextlib.suppress(OSError):
            marker.unlink()


def _reset_marker_path(config: McpRuntimeConfig) -> Path:
    return observer_root(config) / "workspace" / _COMPACT_RESET_MARKER
