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
attempts and the applied policy, and no code path here starts a model except :func:`generate`, which
demands an explicit activation and sits behind the cross-origin write guard.

A refusal is a typed answer with its reason, never a silent no-op: the panel and a reviewer both need
to know *why* a page did not open, did not publish or was refused.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from agents_remember.application.review_evidence_adapter import prepare_evidence
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
from agents_remember.application.review_tree_comparison import ReviewTrees, TreeKnowledge
from agents_remember.canvas.document import bound_document
from agents_remember.canvas.evidence import resolve_evidence_reference
from agents_remember.canvas.paths import PathRefused, design_text_source, task_root
from agents_remember.canvas.policy import (
    CONTAINER_REQUIRED,
    LIMITS,
    IsolationProof,
    asset_headers,
    document_headers,
    frame_sandbox,
    load_isolation_proof,
    policy_digest,
    scripts_enabled,
)
from agents_remember.canvas.presenter import (
    Presenter,
    PresenterRefused,
    PresenterRunner,
    presenter_launch,
)
from agents_remember.canvas.protocol import Binding, ProtocolRefusal, parse_frame_message
from agents_remember.canvas.sessions import CanvasSession, SessionBinding, SessionRegistry
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_evidence import EvidenceMap
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
MAX_FOCUS_CHARS = 2000
MAX_QUESTION_CHARS = 1024
#: One protocol request is bounded by the protocol's own envelope ceiling; the route reads at most one
#: byte past it so the parser can refuse `oversized` without the server buffering a hostile body
#: (L104-B08).
MAX_REQUEST_BYTES = 4096

_COMPARISON_OWNER = "agents_remember.application.review_tree_comparison"


@dataclass
class CanvasHosts:
    """What the canvas routes were composed with: the config, the sessions, the ports."""

    config: McpRuntimeConfig
    sessions: SessionRegistry
    review_trees: ReviewTreesPort | None = None
    evidence: Callable[[ReviewTreesQuery, str, str], Any] | None = None
    runner: PresenterRunner | None = None
    proof: IsolationProof | None = None
    limits: FrameLimits | None = None


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
    return frame_scope(task_root(hosts.config, repository_id, master), leaf_id, frame_id)


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
    host = request.headers.get("host") or f"{request.url.hostname}:{request.url.port or 80}"
    return f"{request.url.scheme}://{host}"


def _scripts(hosts: CanvasHosts) -> bool:
    return scripts_enabled(hosts.proof) if hosts.proof is not None else False


def _policy_view(hosts: CanvasHosts) -> dict[str, Any]:
    proof = hosts.proof
    return {
        "policyDigest": policy_digest(),
        "originArrangement": "opaque-sandbox-v1",
        "scripts": _scripts(hosts),
        "proof": {
            "state": "absent" if proof is None else proof.state,
            "browser": None if proof is None else proof.browser,
            "browserVersion": None if proof is None else proof.browser_version,
            "observed": [] if proof is None else list(proof.observed),
            "failedChecks": [] if proof is None else list(proof.failed_checks),
            "detail": "" if proof is None else proof.detail,
        },
        "limits": LIMITS,
        "containerRequired": list(CONTAINER_REQUIRED),
    }


def _design_view(hosts: CanvasHosts, repository_id: str) -> dict[str, Any]:
    source, text = design_text_source(hosts.config, repository_id)
    if text is None:
        return {"source": source, "available": False, "words": 0, "sha256": None}
    return {
        "source": source,
        "available": True,
        "words": len(text.split()),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
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


def _trees(result: Any) -> ReviewTrees:
    """Build the adapter's own view from the review-trees port's answer, inventing nothing."""

    record = result.comparison
    sides = result.knowledge_sides
    if record is None or len(sides) != 2:
        raise PresenterRefused(
            "evidence-source-unavailable",
            f"the comparison owner answered {result.state!r} without two knowledge sides; no "
            "admitted packet can be prepared from it",
        )
    return ReviewTrees(
        record,
        None,
        Path(record.memory_candidate.repository),
        TreeKnowledge(sides[0], None),
        TreeKnowledge(sides[1], None),
        code_sides=result.code_sides,
    )


def _evidence(
    hosts: CanvasHosts, query: ReviewTreesQuery, *, question: str, focus: str
) -> tuple[Any, Any]:
    """Prepare one admitted R102 packet through the landed owners, or refuse by name."""

    if hosts.evidence is not None:
        return hosts.evidence(query, focus, question), None
    if hosts.review_trees is None:
        raise PresenterRefused(
            "evidence-source-unavailable",
            "the composition root wires no review-trees port, so no comparison can be admitted",
        )
    result = hosts.review_trees(query)
    if result.state != "trees":
        detail = None if result.refusal is None else result.refusal.detail
        raise PresenterRefused(
            "evidence-source-unavailable",
            f"the comparison owner answered {result.state!r}: {detail or 'no tree comparison'}",
        )
    prepared = prepare_evidence(
        _trees(result),
        scope=focus,
        question=question,
        evidence_map=EvidenceMap(coverage=focus),
    )
    return prepared.packet, prepared


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
            "launch": presenter_launch(
                hosts.config, repository_id=params["repository_id"]
            ).model_dump(),
            "design": _design_view(hosts, params["repository_id"]),
            "policy": _policy_view(hosts),
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


@dataclass(frozen=True)
class StartInput:
    """The three facts one explicit start must carry before any evidence is admitted."""

    request_id: str
    focus: str
    question: str


def _start_input(body: dict[str, Any]) -> StartInput | JSONResponse:
    """Validate the start's own fields. An absent activation is a refused start, not a default."""

    allowed = {
        "requestId",
        "focus",
        "activation",
        "question",
        "regenerate",
        "baseRevision",
        "comparison",
        "comparisonNumber",
        "history",
    }
    if set(body) - allowed:
        return _refusal(
            "launch-override-refused",
            "launch choices/permissions come only from L71 user settings; unsupported fields: "
            + ", ".join(sorted(set(body) - allowed)),
            status=400,
        )
    request_id = body.get("requestId")
    focus = body.get("focus")
    if body.get("activation") != "explicit":
        return _refusal(
            "not-activated",
            "authoring starts only from the explicit Generate control; a mount, a reload or a "
            "status read starts no model",
            status=400,
        )
    if not isinstance(request_id, str) or not request_id.strip():
        return _refusal("request-required", "a generation names its request identity", status=400)
    if not isinstance(focus, str) or not 0 < len(focus) <= MAX_FOCUS_CHARS:
        return _refusal("focus-required", "the explanation names its focus", status=400)
    question = body.get("question")
    return StartInput(
        request_id=request_id,
        focus=focus,
        question=question if isinstance(question, str) and question else focus,
    )


def generate(hosts: CanvasHosts, request: Request, body: dict[str, Any]) -> JSONResponse:
    """The one explicit authoring start. Every other path is read-only."""

    params = request.path_params
    try:
        scope = _frame_scope(
            hosts, params["repository_id"], params["master"], params["leaf_id"], params["frame_id"]
        )
    except (PathRefused, FrameRefused, ValueError) as error:
        return _refusal("frame-unavailable", str(error), status=404)
    start = _start_input(body)
    if isinstance(start, JSONResponse):
        return start
    regenerate_from = None
    if body.get("regenerate") is True:
        opening = reopen_frame(scope)
        regenerate_from = opening.revision if opening.state == "ready" else None
        if regenerate_from is None:
            return _refusal(
                "retained-assets-unavailable",
                "no ready revision exists to regenerate from; a page is generated, not "
                "regenerated, when nothing is retained",
            )
    try:
        packet, _prepared = _evidence(
            hosts,
            ReviewTreesQuery(
                repository_id=params["repository_id"],
                master=params["master"],
                leaf_id=params["leaf_id"],
            ),
            question=start.question,
            focus=start.focus,
        )
        attempt = _presenter(hosts, scope, params["leaf_id"]).start(
            caller=str(body.get("caller") or "dashboard"),
            request_id=start.request_id,
            activation="explicit",
            evidence=packet,
            focus=start.focus,
            regenerate_from=regenerate_from,
        )
    except PresenterRefused as refusal:
        return _refusal(refusal.reason, refusal.detail)
    return JSONResponse(
        {"state": "ok", "repository": params["repository_id"], "attempt": attempt.model_dump()}
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
    if operation == "replace":
        return _replace_presenter(presenter, attempt_id, body)
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


def _replace_presenter(presenter: Presenter, attempt_id: str, body: dict[str, Any]) -> Any:
    request_id = body.get("requestId")
    if not isinstance(request_id, str) or not request_id.strip():
        return _refusal("request-required", "replacement names its request identity", status=400)
    return presenter.replace(
        attempt_id, request_id=request_id, reason=str(body.get("reason") or "explicit replacement")
    ).model_dump()


def open_session(hosts: CanvasHosts, body: dict[str, Any]) -> JSONResponse:
    """Open one frame for viewing: the saved-first open, with a fresh bound session."""

    repository_id = body.get("repository")
    master = body.get("master")
    leaf_id = body.get("leaf")
    frame_id = body.get("frame")
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
        opening = reopen_frame(scope)
    except (PathRefused, FrameRefused, ValueError) as error:
        return _refusal("frame-unavailable", str(error), status=404)
    if opening.state != "ready" or opening.manifest is None or opening.revision is None:
        return _refusal("no-ready-revision", opening.detail, status=404)
    manifest = opening.manifest
    # Replacement revokes the old channel before the new one exists: a request on the superseded
    # document changes nothing even if its window is still alive (L104-B02).
    hosts.sessions.revoke_frame(
        repository_id=repository_id, leaf_id=leaf_id, frame_id=frame_id, reason="replaced"
    )
    session, displaced = hosts.sessions.open(
        SessionBinding(
            repository_id=repository_id,
            master=master,
            leaf_id=leaf_id,
            frame_id=frame_id,
            revision=opening.revision,
            comparison=manifest.binding.comparison_reference,
            evidence_digest=manifest.evidence_digest(),
            entrypoint=manifest.entrypoint.path,
            scripts=_scripts(hosts),
        )
    )
    return JSONResponse(
        {
            "state": "ok",
            "session": session.session,
            "revision": session.revision,
            "comparison": session.comparison,
            "entrypoint": session.entrypoint,
            "scripts": session.scripts,
            "sandbox": frame_sandbox(scripts=session.scripts),
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
        headers=document_headers(
            _origin(request), session=session.session, scripts=session.scripts
        ),
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
    if message.operation in {"status", "ready"}:
        if message.operation == "status":
            session.budget.last_status = message.payload
        return JSONResponse({"state": "accepted", "operation": message.operation})
    if message.operation == "request-explanation":
        return _explanation_requested(hosts, session, message.payload)
    return _evidence_requested(hosts, session, message.payload)


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
    session.budget.explanation_requests.append(payload["question"][:200])
    return JSONResponse(
        {
            "state": "recorded",
            "operation": "request-explanation",
            "recorded": recorded,
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
        opening = reopen_frame(scope)
    except (PathRefused, FrameRefused, ValueError) as error:
        return JSONResponse(
            {"state": "unavailable", "operation": "open-evidence", "detail": str(error)}
        )
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
                    f"this channel serves revision {session.revision}; the retained ready revision "
                    f"is {opening.revision}"
                ),
            }
        )
    destination = resolve_evidence_reference(
        opening.manifest, payload["reference"], claim_id=payload.get("claimId")
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
    revoked = hosts.sessions.revoke(request.path_params["session"], reason)
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
    evidence: Callable[[ReviewTreesQuery, str, str], Any] | None = None,
    runner: PresenterRunner | None = None,
    limits: FrameLimits | None = None,
) -> CanvasHosts:
    """Compose the host: one session registry, the shipped proof, and the given ports."""

    bundle = resources.files("agents_remember").joinpath("package_data", "canvas")
    proof_root = Path(str(bundle))
    return CanvasHosts(
        config=config,
        sessions=SessionRegistry(),
        review_trees=review_trees,
        evidence=evidence,
        runner=runner,
        proof=load_isolation_proof(proof_root),
        limits=limits,
    )


def register_canvas_routes(app: FastAPI, hosts: CanvasHosts) -> None:
    """Register both surfaces on the dashboard app, after the API and before the static mount."""

    @app.get(f"{CANVAS_API_PREFIX}/frames/{{repository_id}}/{{master}}/{{leaf_id}}/{{frame_id}}")
    def _frame_state(request: Request) -> JSONResponse:
        return frame_state(hosts, request)

    @app.post(
        f"{CANVAS_API_PREFIX}/frames/{{repository_id}}/{{master}}/{{leaf_id}}/{{frame_id}}/generate"
    )
    async def _generate(request: Request) -> JSONResponse:
        return generate(hosts, request, await _body(request))

    @app.post(
        f"{CANVAS_API_PREFIX}/attempts/{{repository_id}}/{{master}}/{{leaf_id}}/{{frame_id}}"
        "/{attempt_id}/{operation}"
    )
    async def _attempt(request: Request) -> JSONResponse:
        return attempt_operation(hosts, request, await _body(request))

    @app.post(f"{CANVAS_API_PREFIX}/sessions")
    async def _open(request: Request) -> JSONResponse:
        return open_session(hosts, await _body(request))

    @app.post(f"{CANVAS_API_PREFIX}/sessions/{{session}}/messages")
    async def _message(request: Request) -> JSONResponse:
        return session_message(hosts, request, await _bounded_raw(request))

    @app.post(f"{CANVAS_API_PREFIX}/sessions/{{session}}/revoke")
    async def _revoke(request: Request) -> JSONResponse:
        return revoke_session(hosts, request, await _body(request))

    @app.get(f"{CANVAS_PAGE_PREFIX}/{{session}}/")
    def _page(request: Request) -> Response:
        return page(hosts, request)

    @app.get(f"{CANVAS_PAGE_PREFIX}/{{session}}/{{asset_path:path}}")
    def _page_asset(request: Request) -> Response:
        return page_asset(hosts, request)
