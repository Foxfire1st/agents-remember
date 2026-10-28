"""The generated page's host, and the presenter's start/session surface (MIK-R104, MIK-R105).

Two surfaces, deliberately different in authority:

**The frame surface** (``/canvas/page/...``) serves admitted revision bytes and nothing else. It
reads no cookie and sets none, answers only the revision its live session was opened for, and
carries the policy headers of :mod:`agents_remember.canvas.policy`. Both routes are addressed by the
session value alone: the document is ``/canvas/page/{session}/`` and its assets are
``/canvas/page/{session}/<asset>``, so a relative reference in the document resolves inside the one
prefix the policy admits. Neither route names a revision, a frame or a task, and both call the frame
store's own ``open_asset`` with the session's revision, so containment, digest verification and
media type come from the owner that admitted the bytes -- never from the request (L104-B03).

**The trusted API** (``/api/canvas/...``) is the dashboard's own surface. It opens a session, carries
the page's requests to the host protocol, and offers the review panel the saved-first state it
draws. Reads start nothing: :func:`frame_state` reopens the retained revision and reports staleness,
attempts and the applied policy. No code path here starts a model. The explainer workspace
demands an explicit activation and sits behind the cross-origin write guard.

A refusal is a typed answer with its reason, never a silent no-op: the panel and a reviewer both need
to know *why* a page did not open, did not publish or was refused.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from agents_remember.application.review_frame_store import (
    FrameRefused,
    FrameScope,
    frame_scope,
    frame_staleness,
    guard_frame_id,
    open_asset,
    reopen_frame,
    revision_manifest,
)
from agents_remember.canvas.document import bound_document
from agents_remember.canvas.evidence import resolve_evidence_reference
from agents_remember.canvas.paths import PathRefused, task_root
from agents_remember.canvas.policy import (
    CONTAINER_REQUIRED,
    LIMITS,
    ORIGIN_ARRANGEMENT,
    asset_headers,
    document_headers,
    frame_sandbox,
)
from agents_remember.canvas.presenter import (
    Presenter,
    PresenterRefused,
    PresenterRunner,
)
from agents_remember.canvas.protocol import (
    AcceptedMessage,
    Binding,
    ProtocolRefusal,
    parse_frame_message,
)
from agents_remember.canvas.sessions import CanvasSession, SessionBinding, SessionRegistry
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_frame import FrameLimits
from agents_remember.serving.review_trees import ReviewTreesPort, ReviewTreesQuery

__all__ = [
    "CANVAS_API_PREFIX",
    "CANVAS_FRAME_PREFIX",
    "CanvasHosts",
    "canvas_hosts",
    "register_canvas_routes",
]

CANVAS_API_PREFIX = "/api/canvas"
CANVAS_FRAME_PREFIX = "/canvas"
#: Where one live session's document and its admitted assets are served. The policy's subresource
#: allowlist is exactly this prefix, so a relative reference in the document stays inside it.
CANVAS_PAGE_PREFIX = f"{CANVAS_FRAME_PREFIX}/page"

#: How long the panel's focus text may be, and the question a page may ask.
MAX_QUESTION_CHARS = 1024
#: One protocol request is bounded by the protocol's own envelope ceiling; the route reads at most one
#: byte past it so the parser can refuse `oversized` without the server buffering a hostile body
#: (L104-B08).
MAX_REQUEST_BYTES = 4096

_COMPARISON_OWNER = "agents_remember.application.review_tree_comparison"

#: The shape a forwarded host may take before it is used in a document policy: ``host`` or
#: ``host:port``, IPv6 in brackets. A scheme, a path, a comma-separated chain or whitespace means a
#: value this service did not expect, and the request's own host stands instead (F5).
_FORWARDED_HOST = re.compile(
    r"(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?::[0-9]{1,5})?"
)


@dataclass
class CanvasHosts:
    """What the canvas routes were composed with: the config, the sessions, the ports."""

    config: McpRuntimeConfig
    sessions: SessionRegistry
    review_trees: ReviewTreesPort | None = None
    runner: PresenterRunner | None = None
    limits: FrameLimits | None = None
    pinned_sessions: set[str] = field(default_factory=set)
    session_slots: dict[str, str] = field(default_factory=dict)
    selection_picks: OrderedDict[tuple[str, str, str, str, int], dict[str, Any]] = field(
        default_factory=OrderedDict
    )


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _refusal(reason: str, detail: str, *, status: int = 409) -> JSONResponse:
    return JSONResponse(
        {"state": "refused", "reason": reason, "detail": detail}, status_code=status
    )


def _frame_scope(
    hosts: CanvasHosts, repository_id: str, master: str, leaf_id: str, frame_id: str
) -> FrameScope:
    """The admitted frame scope for one review target, or a refusal naming the path fault."""

    require_repo(hosts.config, repository_id)
    guard_frame_id(frame_id)
    root = task_root(hosts.config, repository_id, master)
    scope = frame_scope(root, leaf_id, frame_id)
    if not scope.frame_dir.is_dir():
        raise FrameRefused(
            f"frame {frame_id!r} has no folder at {scope.frame_dir}; a missing frame is refused "
            "by name and is never swapped for another frame's bytes"
        )
    return scope


def _presenter(hosts: CanvasHosts, scope: FrameScope, leaf_id: str) -> Presenter:
    parts = scope.task_root.parts
    repository_id = parts[parts.index("tasks") + 1]
    return Presenter(
        hosts.config,
        repository_id=repository_id,
        task=scope.task_root.name,
        leaf_id=leaf_id,
        frame_id=scope.frame_id,
        runner=hosts.runner,
        limits=hosts.limits,
        clock=_now,
    )


def _origin(request: Request) -> str:
    """The browser-facing origin of a frame document: what the page is loaded from, not the proxy's.

    A dev proxy in front of this service requests the document with its own ``Host`` (that is what
    makes the request admissible at all, ``write_guard.host_refusal``) and tells us the origin the
    browser is really on through ``X-Forwarded-Host``. The document policy is built from that
    origin, so a page loaded from ``localhost:5195`` may load its own ``styles.css`` from
    ``localhost:5195`` instead of being refused as foreign by a policy that names the proxy target.
    The forwarded value is used only in the ``host[:port]`` shape: anything carrying a scheme, a
    path, or whitespace is ignored and the request's own host stands (MIK-R211 v2, F5).
    """

    host = request.headers.get("host") or f"{request.url.hostname}:{request.url.port or 80}"
    scheme = request.url.scheme
    forwarded_host = request.headers.get("x-forwarded-host", "").strip()
    if _FORWARDED_HOST.fullmatch(forwarded_host):
        host = forwarded_host
        forwarded_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
        if forwarded_proto in {"http", "https"}:
            scheme = forwarded_proto
    return f"{scheme}://{host}"


def _policy_view() -> dict[str, Any]:
    """The policy the served pages run under. Scripts are part of it, not a separate decision."""

    return {
        "originArrangement": ORIGIN_ARRANGEMENT,
        "scripts": True,
        "limits": LIMITS,
        "containerRequired": list(CONTAINER_REQUIRED),
    }


async def _bounded_raw(request: Request) -> bytes:
    """Read one frame message within its admitted size, never the whole hostile body."""

    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk[: MAX_REQUEST_BYTES + 1 - len(payload)])
        if len(payload) > MAX_REQUEST_BYTES:
            return bytes(payload[: MAX_REQUEST_BYTES + 1])
    return bytes(payload)


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def frame_state(hosts: CanvasHosts, request: Request) -> JSONResponse:
    """The saved-first view: opening, staleness, attempts, policy and bounded diagnostics.

    No path in this read starts a model, resumes a session or publishes anything.
    """

    params = request.path_params
    try:
        scope = _frame_scope(
            hosts, params["repository_id"], params["master"], params["leaf_id"], params["frame_id"]
        )
        presenter = _presenter(hosts, scope, params["leaf_id"])
        opening = reopen_frame(scope)
    except (PathRefused, FrameRefused, ValueError) as error:
        return _refusal("frame-unavailable", str(error), status=404)
    staleness: dict[str, Any] = {"state": "unavailable", "detail": "no comparison is selected"}
    if hosts.review_trees is not None and opening.manifest is not None:
        try:
            result = hosts.review_trees(
                ReviewTreesQuery(
                    repository_id=params["repository_id"],
                    master=params["master"],
                    leaf_id=params["leaf_id"],
                )
            )
            if result.comparison is not None:
                staleness = frame_staleness(
                    scope, result.comparison, comparison_owner=_COMPARISON_OWNER
                ).model_dump()
        except (ValueError, FrameRefused) as error:
            staleness = {"state": "unavailable", "detail": str(error)}
    return JSONResponse(
        {
            "state": "ok",
            "frame": {
                "repository": params["repository_id"],
                "master": params["master"],
                "leaf": params["leaf_id"],
                "frame": params["frame_id"],
            },
            "opening": opening.model_dump(),
            "staleness": staleness,
            "presenter": presenter.opening(),
            "policy": _policy_view(),
            "sessions": [
                {
                    "revision": session.revision,
                    "comparison": session.comparison,
                    "expiresAt": session.expires_at,
                }
                for session in hosts.sessions.sessions()
            ],
        }
    )


def attempt_operation(hosts: CanvasHosts, request: Request, body: dict[str, Any]) -> JSONResponse:
    """Cancel, resume, say or publish one attempt -- each an explicit, named operation."""

    allowed = {
        "requestId",
        "reason",
        "text",
        "entrypoint",
        "comparison",
        "comparisonNumber",
        "history",
    }
    if set(body) - allowed:
        return _refusal(
            "launch-override-refused",
            "attempt actions cannot override launch choices/permissions",
            status=400,
        )
    params = request.path_params
    try:
        scope = _frame_scope(
            hosts, params["repository_id"], params["master"], params["leaf_id"], params["frame_id"]
        )
    except (PathRefused, FrameRefused, ValueError) as error:
        return _refusal("frame-unavailable", str(error), status=404)
    presenter = _presenter(hosts, scope, params["leaf_id"])
    attempt_id = params["attempt_id"]
    try:
        result = _attempt_act(presenter, params["operation"], attempt_id, body)
    except PresenterRefused as refusal:
        return _refusal(refusal.reason, refusal.detail)
    if isinstance(result, JSONResponse):
        return result
    if isinstance(result, dict) and result.get("state") == "published":
        # Rule 6: the old document's channel is revoked when its successor becomes ready.
        hosts.sessions.revoke_frame(
            repository_id=params["repository_id"],
            leaf_id=params["leaf_id"],
            frame_id=params["frame_id"],
            reason="superseded",
        )
    if isinstance(result, dict) and "state" in result:
        return JSONResponse(result)
    return JSONResponse({"state": "ok", "repository": params["repository_id"], "attempt": result})


def _attempt_act(
    presenter: Presenter, operation: str, attempt_id: str, body: dict[str, Any]
) -> Any:
    if operation in {"cancel", "resume"}:
        attempt = (
            presenter.resume(attempt_id)
            if operation == "resume"
            else presenter.cancel(attempt_id, reason=str(body.get("reason") or "cancelled"))
        )
        return attempt.model_dump()
    if operation == "say":
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return _refusal("empty-message", "a conversation turn carries text", status=400)
        return presenter.say(attempt_id, role="developer", text=text).model_dump()
    if operation == "publish":
        publication = presenter.publish(
            attempt_id, entrypoint=str(body.get("entrypoint") or "index.html")
        )
        return (
            _refusal("publication-refused", publication.detail, status=409)
            if not publication.published
            else {
                "state": "published",
                "detail": publication.detail,
                "revision": None if publication.manifest is None else publication.manifest.revision,
            }
        )
    return _refusal("unknown-operation", f"{operation!r} is not an operation", status=404)


def _resolve_open_revision(
    scope: FrameScope, requested: object
) -> tuple[Any, int, bool] | JSONResponse:
    if requested is not None:
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
            return _refusal("frame-unavailable", "revision is a positive integer", status=400)
        manifest = revision_manifest(scope.frame_dir, requested)
        if manifest is None:
            return _refusal(
                "no-ready-revision",
                f"revision {requested} is not retained in frame {scope.frame_id}",
                status=404,
            )
        return manifest, manifest.revision, True
    opening = reopen_frame(scope)
    if opening.state != "ready" or opening.manifest is None or opening.revision is None:
        return _refusal("no-ready-revision", opening.detail, status=404)
    return opening.manifest, opening.revision, False


def _revoke_matching_sessions(
    hosts: CanvasHosts,
    target: tuple[str, str, str],
    *,
    revision: int,
    slot: str,
    pinned: bool,
) -> None:
    repository_id, leaf_id, frame_id = target
    if not pinned:
        hosts.sessions.revoke_frame(
            repository_id=repository_id, leaf_id=leaf_id, frame_id=frame_id, reason="replaced"
        )
        return
    doomed = [
        item.session
        for item in hosts.sessions.sessions()
        if (item.repository_id, item.leaf_id, item.frame_id, item.revision)
        == (repository_id, leaf_id, frame_id, revision)
        and hosts.session_slots.get(item.session, "") == slot
    ]
    for token in doomed:
        hosts.sessions.revoke(token, "replaced")
        hosts.pinned_sessions.discard(token)
        hosts.session_slots.pop(token, None)


def open_session(
    hosts: CanvasHosts,
    body: dict[str, Any],
    path_params: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """Open one frame for viewing: the saved-first open, with a fresh bound session."""

    params = path_params or {}
    repository_id = body.get("repository") or params.get("repository_id")
    master = body.get("master") or params.get("master")
    leaf_id = body.get("leaf") or params.get("leaf_id")
    frame_id = body.get("frame") or params.get("frame_id")
    if not (
        isinstance(repository_id, str)
        and isinstance(master, str)
        and isinstance(leaf_id, str)
        and isinstance(frame_id, str)
        and repository_id
        and master
        and leaf_id
        and frame_id
    ):
        return _refusal("frame-unavailable", "a session names its exact review target", status=400)
    try:
        scope = _frame_scope(hosts, repository_id, master, leaf_id, frame_id)
        resolved = _resolve_open_revision(scope, body.get("revision"))
    except (PathRefused, FrameRefused, ValueError) as error:
        return _refusal("frame-unavailable", str(error), status=404)
    if isinstance(resolved, JSONResponse):
        return resolved
    manifest, revision_num, pinned = resolved
    slot = str(body.get("slot") or "")
    _revoke_matching_sessions(
        hosts,
        (repository_id, leaf_id, scope.frame_id),
        revision=revision_num,
        slot=slot,
        pinned=pinned,
    )
    session, displaced = hosts.sessions.open(
        SessionBinding(
            repository_id=repository_id,
            master=master,
            leaf_id=leaf_id,
            frame_id=scope.frame_id,
            revision=revision_num,
            comparison=manifest.binding.comparison_reference,
            evidence_digest=manifest.evidence_digest(),
            entrypoint=manifest.entrypoint.path,
        )
    )
    if pinned:
        hosts.pinned_sessions.add(session.session)
        hosts.session_slots[session.session] = slot
    return JSONResponse(
        {
            "state": "ok",
            "session": session.session,
            "revision": session.revision,
            "comparison": session.comparison,
            "entrypoint": session.entrypoint,
            "scripts": True,
            "sandbox": frame_sandbox(),
            "pageUrl": f"{CANVAS_PAGE_PREFIX}/{session.session}/",
            "displaced": [item.session for item in displaced],
            "limits": {
                "messagesPerSecond": LIMITS["messages-per-second"]["value"],
                "requestsPerSession": LIMITS["requests-per-session"]["value"],
                "activeFrames": LIMITS["active-frames"]["value"],
                "containerRequired": list(CONTAINER_REQUIRED),
            },
        }
    )


def page(hosts: CanvasHosts, request: Request) -> Response:
    """The one document one live session may load, with its policy headers."""

    value = request.path_params["session"]
    session = hosts.sessions.get(value)
    if session is None:
        return _not_live(hosts, value)
    try:
        scope = _frame_scope(
            hosts, session.repository_id, session.master, session.leaf_id, session.frame_id
        )
        payload = open_asset(scope, session.revision, session.entrypoint)
    except (PathRefused, FrameRefused, ValueError) as error:
        return _refusal("revision-unavailable", str(error), status=404)
    return Response(
        content=bound_document(payload, session),
        media_type="text/html",
        headers=document_headers(_origin(request), session=session.session),
    )


def _not_live(hosts: CanvasHosts, value: str) -> JSONResponse:
    """One answer for a value that is not a live channel: its revocation reason, or unknown."""

    reason = hosts.sessions.revocation(value) or "unknown-session"
    return JSONResponse(
        {
            "state": "refused",
            "reason": reason,
            "detail": f"this page channel is not live: {reason}",
        },
        status_code=410,
    )


def _asset_media_type(scope: FrameScope, revision: int, path: str) -> str:
    """The media type the revision's own manifest recorded for one admitted asset."""

    manifest = revision_manifest(scope.frame_dir, revision)
    if manifest is not None:
        for item in manifest.assets:
            if item.path == path:
                return item.media_type
    return "application/octet-stream"


def page_asset(hosts: CanvasHosts, request: Request) -> Response:
    """One asset of the revision the live session was opened for, and nothing else.

    The route addresses no revision, frame or task: the session is the only address, so a caller
    cannot choose another revision's bytes, another frame's asset or another task's file. The store's
    own ``open_asset`` decides containment, digest and media type from that one revision's manifest
    (L104-B03).
    """

    value = request.path_params["session"]
    session = hosts.sessions.get(value)
    if session is None:
        return _not_live(hosts, value)
    path = request.path_params["asset_path"]
    try:
        scope = _frame_scope(
            hosts, session.repository_id, session.master, session.leaf_id, session.frame_id
        )
        payload = open_asset(scope, session.revision, path)
    except (PathRefused, FrameRefused, ValueError) as error:
        return _refusal("asset-unavailable", str(error), status=404)
    return Response(
        content=payload,
        headers=asset_headers(_asset_media_type(scope, session.revision, path)),
    )


def _superseded(hosts: CanvasHosts, session: CanvasSession) -> str | None:
    """Why this channel's revision is no longer the ready one, or ``None`` while it still is.

    Rule 6: a channel is bound to one immutable revision. When the frame publishes a successor, the
    old channel's display messages change nothing -- and this process revokes a frame's channels on
    its own publication, so the remaining case is a ready pointer that moved elsewhere.
    """

    try:
        scope = _frame_scope(
            hosts, session.repository_id, session.master, session.leaf_id, session.frame_id
        )
        if session.session in hosts.pinned_sessions:
            if revision_manifest(scope.frame_dir, session.revision) is None:
                return (
                    f"this channel serves revision {session.revision}, which is no longer retained"
                )
            return None
        opening = reopen_frame(scope)
    except (PathRefused, FrameRefused, ValueError):
        return None
    if opening.state != "ready" or opening.revision is None:
        return None
    if opening.revision == session.revision:
        return None
    return (
        f"this channel serves revision {session.revision}; the ready revision is now "
        f"{opening.revision}; reopen the frame"
    )


def session_message(hosts: CanvasHosts, request: Request, raw: bytes) -> JSONResponse:
    """One host-protocol request: bound, bounded, refused by name, applied only after all three."""

    value = request.path_params["session"]
    session = hosts.sessions.get(value)
    if session is None:
        return _not_live(hosts, value)
    binding = Binding(
        session=session.session, revision=session.revision, comparison=session.comparison
    )
    try:
        # The bucket is charged before the parse: every request that reaches the route is bounded
        # work, so a malformed or replayed flood costs one debit and is then refused rate-limited
        # without a parse (L104-B08).
        session.budget.charge()
        message = parse_frame_message(raw, binding=binding)
        session.budget.admit(message)
    except ProtocolRefusal as refusal:
        session.budget.note_refusal(refusal.reason, refusal.detail)
        return JSONResponse(
            {"state": "refused", "reason": refusal.reason, "detail": refusal.detail}
        )
    if message.operation != "open-evidence":
        stale = _superseded(hosts, session)
        if stale is not None:
            session.budget.note_refusal("stale-revision", stale)
            return JSONResponse({"state": "refused", "reason": "stale-revision", "detail": stale})
    return _dispatch_session_message(hosts, session, message)


def _dispatch_session_message(
    hosts: CanvasHosts, session: CanvasSession, message: AcceptedMessage
) -> JSONResponse:
    if message.operation in {"status", "ready"}:
        if message.operation == "status":
            session.budget.last_status = message.payload
        return JSONResponse({"state": "accepted", "operation": message.operation})
    if message.operation == "offer-selections":
        return _selections_offered(hosts, session, message.payload)
    if message.operation == "request-explanation":
        return _explanation_requested(hosts, session, message.payload)
    return _evidence_requested(hosts, session, message.payload)


def _selections_offered(
    hosts: CanvasHosts, session: CanvasSession, payload: dict[str, Any]
) -> JSONResponse:
    """Record confirmed choices for the trusted tray, replacing any prior offer of this revision."""

    selections = [
        {"question": str(row["question"]), "choice": str(row["choice"])}
        for row in payload.get("selections") or ()
        if isinstance(row, dict)
    ]
    verbatim = "\n".join(f"{row['question']}: {row['choice']}" for row in selections)
    pick = {
        "id": f"selection:{session.leaf_id}:{session.frame_id}:{session.revision}",
        "kind": "selection",
        "title": f"Selections · revision {session.revision}",
        "revision": session.revision,
        "frame": session.frame_id,
        "comparison": session.comparison,
        "target": "explainer",
        "removable": True,
        "selections": selections,
        "text": verbatim,
    }
    key = (
        session.repository_id,
        session.master,
        session.leaf_id,
        session.frame_id,
        session.revision,
    )
    hosts.selection_picks[key] = pick
    hosts.selection_picks.move_to_end(key)
    while len(hosts.selection_picks) > 64:
        hosts.selection_picks.popitem(last=False)
    return JSONResponse(
        {
            "state": "accepted",
            "operation": "offer-selections",
            "revision": session.revision,
            "comparison": session.comparison,
            "selections": selections,
            "pick": pick,
            "startsModel": False,
            "detail": (
                "the selections are offered to the tray verbatim; nothing was sent or started"
            ),
        }
    )


def _explanation_requested(
    hosts: CanvasHosts, session: CanvasSession, payload: dict[str, Any]
) -> JSONResponse:
    """Record a question for the trusted conversation. It starts no model, by construction."""

    recorded = False
    try:
        scope = _frame_scope(
            hosts, session.repository_id, session.master, session.leaf_id, session.frame_id
        )
        presenter = _presenter(hosts, scope, session.leaf_id)
        attempt = presenter.current()
        if attempt is not None:
            presenter.request_explanation(attempt.attempt_id, payload["question"])
            recorded = True
    except (PresenterRefused, PathRefused, FrameRefused, ValueError):
        recorded = False
    question = str(payload["question"])
    session.budget.explanation_requests.append(question[:200])
    pick = {
        "id": (
            f"explanation-request:{session.leaf_id}:{session.frame_id}:{session.revision}:"
            f"{len(session.budget.explanation_requests)}"
        ),
        "kind": "text",
        "title": f"Question · revision {session.revision}",
        "revision": session.revision,
        "frame": session.frame_id,
        "comparison": session.comparison,
        "target": "explainer",
        "removable": True,
        "question": question,
        "text": question,
    }
    return JSONResponse(
        {
            "state": "recorded",
            "operation": "request-explanation",
            "recorded": recorded,
            "question": question,
            "pick": pick,
            "startsModel": False,
            "detail": "the request is recorded for the trusted conversation; nothing was started",
        }
    )


def _evidence_requested(
    hosts: CanvasHosts, session: CanvasSession, payload: dict[str, Any]
) -> JSONResponse:
    """Resolve an evidence reference against the session's own admitted revision."""

    try:
        scope = _frame_scope(
            hosts, session.repository_id, session.master, session.leaf_id, session.frame_id
        )
        if session.session in hosts.pinned_sessions:
            manifest = revision_manifest(scope.frame_dir, session.revision)
            if manifest is None:
                return JSONResponse(
                    {
                        "state": "unavailable",
                        "operation": "open-evidence",
                        "detail": f"revision {session.revision} is no longer retained",
                    }
                )
        else:
            opening = reopen_frame(scope)
            if opening.state != "ready" or opening.manifest is None:
                return JSONResponse(
                    {"state": "unavailable", "operation": "open-evidence", "detail": opening.detail}
                )
            if opening.revision != session.revision:
                return JSONResponse(
                    {
                        "state": "unavailable",
                        "operation": "open-evidence",
                        "detail": (
                            f"this channel serves revision {session.revision}; the retained ready "
                            f"revision is {opening.revision}"
                        ),
                    }
                )
            manifest = opening.manifest
    except (PathRefused, FrameRefused, ValueError) as error:
        return JSONResponse(
            {"state": "unavailable", "operation": "open-evidence", "detail": str(error)}
        )
    destination = resolve_evidence_reference(
        manifest, payload["reference"], claim_id=payload.get("claimId")
    )
    return JSONResponse(
        {
            "state": destination.state,
            "operation": "open-evidence",
            "reference": destination.reference,
            "kind": destination.kind,
            "comparison": destination.comparison,
            "target": destination.target,
            "location": destination.location,
            "detail": destination.detail,
        }
    )


def revoke_session(hosts: CanvasHosts, request: Request, body: dict[str, Any]) -> JSONResponse:
    """Dispose one channel: navigation, replacement or a scope change each revoke the old one."""

    reason = str(body.get("reason") or "navigated")
    token = request.path_params["session"]
    revoked = hosts.sessions.revoke(token, reason)
    hosts.pinned_sessions.discard(token)
    hosts.session_slots.pop(token, None)
    return JSONResponse(
        {
            "state": "ok",
            "revoked": revoked is not None,
            "detail": "the channel is revoked; requests on it change nothing"
            if revoked
            else "the channel was already not live",
        }
    )


def canvas_hosts(
    config: McpRuntimeConfig,
    *,
    review_trees: ReviewTreesPort | None = None,
    runner: PresenterRunner | None = None,
    limits: FrameLimits | None = None,
) -> CanvasHosts:
    """Compose the host: one session registry and the given ports."""

    return CanvasHosts(
        config=config,
        sessions=SessionRegistry(),
        review_trees=review_trees,
        runner=runner,
        limits=limits,
    )


def register_canvas_routes(app: FastAPI, hosts: CanvasHosts) -> None:
    """Register both surfaces on the dashboard app, after the API and before the static mount."""

    @app.get(f"{CANVAS_API_PREFIX}/frames/{{repository_id}}/{{master}}/{{leaf_id}}/{{frame_id}}")
    def _frame_state(request: Request) -> JSONResponse:
        return frame_state(hosts, request)

    @app.post(
        f"{CANVAS_API_PREFIX}/attempts/{{repository_id}}/{{master}}/{{leaf_id}}/{{frame_id}}"
        "/{attempt_id}/{operation}"
    )
    async def _attempt(request: Request) -> JSONResponse:
        return attempt_operation(hosts, request, await _body(request))

    @app.post(f"{CANVAS_API_PREFIX}/sessions")
    @app.post(
        f"{CANVAS_API_PREFIX}/frames/{{repository_id}}/{{master}}/{{leaf_id}}/{{frame_id}}/sessions"
    )
    async def _open(request: Request) -> JSONResponse:
        return open_session(hosts, await _body(request), request.path_params)

    @app.post(f"{CANVAS_API_PREFIX}/sessions/{{session}}/messages")
    @app.post(
        f"{CANVAS_API_PREFIX}/frames/{{repository_id}}/{{master}}/{{leaf_id}}/{{frame_id}}"
        "/sessions/{session}/messages"
    )
    async def _message(request: Request) -> JSONResponse:
        return session_message(hosts, request, await _bounded_raw(request))

    @app.post(f"{CANVAS_API_PREFIX}/sessions/{{session}}/revoke")
    @app.post(
        f"{CANVAS_API_PREFIX}/frames/{{repository_id}}/{{master}}/{{leaf_id}}/{{frame_id}}"
        "/sessions/{session}/revoke"
    )
    async def _revoke(request: Request) -> JSONResponse:
        return revoke_session(hosts, request, await _body(request))

    @app.get(f"{CANVAS_PAGE_PREFIX}/{{session}}/")
    def _page(request: Request) -> Response:
        return page(hosts, request)

    @app.get(f"{CANVAS_PAGE_PREFIX}/{{session}}/{{asset_path:path}}")
    def _page_asset(request: Request) -> Response:
        return page_asset(hosts, request)
