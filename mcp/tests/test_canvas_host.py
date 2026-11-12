"""MIK-R104: the isolation policy, the host protocol, the sessions and exact evidence.

One case per rule the host owns, against the real policy, the real protocol validator and a real
published revision in a temporary task:

1. every served document runs its own scripts, and only inside the frame's opaque sandbox;
2. every frame and asset response carries its policy headers, and never ``allow-same-origin``;
3. the envelope is bound to the session, revision and comparison it was served with;
4. malformed, oversized and replayed requests are refused by name without an effect;
5. the rate, request and message ceilings are finite and refuse the excess;
6. a session is host-created, bound and revocable, and displacement names its reason;
7. an admitted basis opens its exact destination, an unread one stays unavailable, and a reference
   outside the packet opens nothing;
8. one live session's page and assets are served under ``/canvas/page/{session}/`` from that
   session's own immutable revision, and every foreign, traversed, symlinked, missing or corrupt
   asset is refused (L104-B03);
9. a replaced or revoked channel answers 410 and changes nothing, and an evidence request on a
   revision that is no longer ready opens nothing (L104-B02);
10. every legal JSON wrong-type value, deep nesting, invalid encoding and oversized body is a named
    refusal with no effect (L104-B07);
11. a malformed flood is rate-charged before it is parsed and its diagnostics stay bounded
    (L104-B08);
12. evidence resolves by typed claim and receipt identity, refusing an unnamed ambiguous reference
    and never opening an unavailable receipt (L104-B09).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.application.review_frame_store import frame_scope, reopen_frame
from agents_remember.canvas.evidence import resolve_evidence_reference
from agents_remember.canvas.policy import (
    ASSET_PATH_PREFIX,
    asset_headers,
    document_headers,
    frame_sandbox,
)
from agents_remember.canvas.protocol import (
    MAX_MESSAGE_BYTES,
    REFUSAL_REASONS,
    AcceptedMessage,
    Binding,
    ProtocolRefusal,
    RequestLedger,
    parse_frame_message,
)
from agents_remember.canvas.sessions import CanvasSession, SessionBinding, SessionRegistry
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidenceCollection,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving.canvas import CanvasHosts, register_canvas_routes
from canvas_host_test_support import (
    ASSETS,
    FRAME,
    LEAF,
    REPO,
    TASK,
    PageContent,
    config,
    packet,
    publish,
    record,
    task,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

ORIGIN = "http://127.0.0.1:8765"


def _envelope(session: str | None = None, **overrides: object) -> dict[str, object]:
    """One request envelope as a page sends it, before it is serialised."""

    payload: dict[str, object] = {
        "schema": "ar-canvas-message/v1",
        "protocolVersion": 1,
        "type": "ready",
        "requestId": "request-0001",
        "session": "s" * 24 if session is None else session,
        "revision": 1,
        "comparison": COMPARISON,
        "payload": {},
    }
    payload.update(overrides)
    return payload


def _message(**overrides: object) -> bytes:
    payload = _envelope()
    payload.update(overrides)
    return json.dumps(payload).encode()


def _sent(opened: dict[str, object], **overrides: object) -> dict[str, object]:
    """One envelope bound to the exact session, revision and comparison the host issued."""

    payload = _envelope(
        str(opened["session"]), revision=opened["revision"], comparison=opened["comparison"]
    )
    payload.update(overrides)
    return payload


def _client(tmp_path: Path) -> tuple[TestClient, CanvasHosts, Path]:
    """The host's own routes over a real task."""

    settings = config(tmp_path)
    root = task(settings)
    hosts = CanvasHosts(config=settings, sessions=SessionRegistry())
    app = FastAPI()
    register_canvas_routes(app, hosts)
    return TestClient(app, raise_server_exceptions=False), hosts, root


def _open(client: TestClient, *, frame: str = FRAME) -> dict[str, object]:
    response = client.post(
        "/api/canvas/sessions",
        json={"repository": REPO, "master": TASK, "leaf": LEAF, "frame": frame},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _post_message(client: TestClient, session: str, body: object) -> dict[str, object]:
    response = client.post(f"/api/canvas/sessions/{session}/messages", json=body)
    assert response.status_code == 200, response.text
    return response.json()


COMPARISON = "review-comparison:task/leaf/7"


def _binding(*, frame_id: str = FRAME, revision: int = 1) -> SessionBinding:
    """One session's own binding, as the open route composes it."""

    return SessionBinding(
        repository_id=REPO,
        master=TASK,
        leaf_id=LEAF,
        frame_id=frame_id,
        revision=revision,
        comparison=COMPARISON,
        evidence_digest="e" * 64,
        entrypoint="index.html",
    )


def test_every_response_carries_its_policy_headers() -> None:
    """Rules 2-4: one origin allowlist, no network for data, no credential of the dashboard.

    Every directive is asserted whole. Since D568 the suite, not a shipped browser record, is the
    check of the isolation that stays, so the loads the browser matrix used to try (a foreign style,
    image, font or media, a plugin, a nested frame, a form post, a base rewrite) are held here by the
    exact directive that refuses them.
    """

    headers = document_headers(ORIGIN, session="test-session")
    csp = headers["Content-Security-Policy"]
    directives: dict[str, str] = {}
    for part in csp.split(";"):
        name, _, value = part.strip().partition(" ")
        if name:
            directives[name] = value
    own = f"{ORIGIN}{ASSET_PATH_PREFIX}test-session/"
    assert directives == {
        "sandbox": "allow-scripts",
        "default-src": "'none'",
        "script-src": f"{own} 'unsafe-inline'",
        "style-src": f"{own} 'unsafe-inline'",
        "img-src": f"{own} data:",
        "font-src": f"{own} data:",
        "media-src": own,
        "manifest-src": own,
        "connect-src": "'none'",
        "frame-src": "'none'",
        "child-src": "'none'",
        "worker-src": "'none'",
        "object-src": "'none'",
        "frame-ancestors": "'self'",
        "base-uri": "'none'",
        "form-action": "'none'",
    }
    assert "default-src 'none'" in csp
    assert f"{ORIGIN}{ASSET_PATH_PREFIX}" in csp
    assert "connect-src 'none'" in csp
    assert "frame-src 'none'" in csp
    assert "worker-src 'none'" in csp
    assert "frame-ancestors 'self'" in csp
    # The document's own scripts are part of the served policy, inside the opaque sandbox.
    assert csp.startswith("sandbox allow-scripts")
    assert f"script-src {ORIGIN}{ASSET_PATH_PREFIX}test-session/ 'unsafe-inline'" in csp
    assert "allow-same-origin" not in csp
    assert frame_sandbox() == "allow-scripts"
    assert headers["Cache-Control"] == "no-store"
    assert headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "clipboard-read=()" in headers["Permissions-Policy"]
    asset = asset_headers("image/svg+xml")
    assert asset["X-Content-Type-Options"] == "nosniff"
    assert asset["Content-Security-Policy"] == "default-src 'none'; sandbox"
    assert "Set-Cookie" not in asset


def test_the_envelope_is_bound_to_its_session_revision_and_comparison() -> None:
    """Rule 6: a value nobody was given authenticates nothing, and neither does a claim."""

    binding = Binding(session="s" * 24, revision=1, comparison="review-comparison:task/leaf/7")
    accepted = parse_frame_message(_message(), binding=binding)
    assert accepted.operation == "ready"
    for override, reason in (
        ({"session": "t" * 24}, "foreign-session"),
        ({"revision": 2}, "stale-revision"),
        ({"comparison": "review-comparison:task/leaf/8"}, "foreign-comparison"),
        ({"protocolVersion": 2}, "wrong-version"),
        ({"type": "start-role"}, "unknown-op"),
    ):
        with pytest.raises(ProtocolRefusal) as refusal:
            parse_frame_message(_message(**override), binding=binding)
        assert refusal.value.reason == reason


def test_malformed_oversized_and_unknown_payloads_are_refused() -> None:
    """Rule 6: a malformed or oversized request never reaches an effect."""

    binding = Binding(session="s" * 24, revision=1, comparison="review-comparison:task/leaf/7")
    with pytest.raises(ProtocolRefusal) as oversized:
        parse_frame_message(b"x" * (MAX_MESSAGE_BYTES + 1), binding=binding)
    assert oversized.value.reason == "oversized"
    with pytest.raises(ProtocolRefusal) as malformed:
        parse_frame_message(b"{not json", binding=binding)
    assert malformed.value.reason == "malformed"
    with pytest.raises(ProtocolRefusal) as unknown:
        parse_frame_message(
            _message(type="status", payload={"kind": "ready", "command": "rm -rf /"}),
            binding=binding,
        )
    assert unknown.value.reason == "malformed"
    with pytest.raises(ProtocolRefusal) as bad_kind:
        parse_frame_message(_message(type="status", payload={"kind": "explode"}), binding=binding)
    assert bad_kind.value.reason == "malformed"


def test_the_rate_request_and_replay_ceilings_are_finite() -> None:
    """Rules 5, 6, 8: a channel has a burst, a rate, a total and one memory per request identity."""

    now = [0.0]
    ledger = RequestLedger(rate=2, burst=3, capacity=3, clock=lambda: now[0])
    first = parse_frame_message(
        _message(requestId="request-0001"),
        binding=Binding("s" * 24, 1, "review-comparison:task/leaf/7"),
    )
    _through_the_route(ledger, first)
    with pytest.raises(ProtocolRefusal) as replay:
        _through_the_route(ledger, first)
    assert replay.value.reason == "replayed"
    _through_the_route(
        ledger,
        parse_frame_message(
            _message(requestId="request-0002"),
            binding=Binding("s" * 24, 1, "review-comparison:task/leaf/7"),
        ),
    )
    with pytest.raises(ProtocolRefusal) as rate:
        _through_the_route(
            ledger,
            parse_frame_message(
                _message(requestId="request-0003"),
                binding=Binding("s" * 24, 1, "review-comparison:task/leaf/7"),
            ),
        )
    assert rate.value.reason == "rate-limited"
    now[0] = 5.0
    _through_the_route(
        ledger,
        parse_frame_message(
            _message(requestId="request-0004"),
            binding=Binding("s" * 24, 1, "review-comparison:task/leaf/7"),
        ),
    )
    with pytest.raises(ProtocolRefusal) as exhausted:
        _through_the_route(
            ledger,
            parse_frame_message(
                _message(requestId="request-0005"),
                binding=Binding("s" * 24, 1, "review-comparison:task/leaf/7"),
            ),
        )
    assert exhausted.value.reason == "exhausted"
    assert ledger.diagnostics()["accepted"] == 3


def _through_the_route(ledger: RequestLedger, message: AcceptedMessage) -> None:
    """One request as the route runs it: charged before it is parsed, then admitted."""

    ledger.charge()
    ledger.admit(message)


def test_a_session_is_created_bounded_and_revoked_by_name() -> None:
    """Rule 6: four live frames at most, an idle expiry, and a tombstone that keeps the reason."""

    now = [0.0]
    registry = SessionRegistry(ttl=60.0, active=2, clock=lambda: now[0])
    session, displaced = registry.open(_binding())
    assert displaced == ()
    assert registry.get(session.session) == session
    assert registry.get("nobody-was-given-this") is None
    now[0] = 61.0
    assert registry.get(session.session) is None
    assert registry.revocation(session.session) == "idle-timeout"
    second, _ = registry.open(_binding())
    third, _ = registry.open(_binding(frame_id="another-page"))
    _fourth, replaced = registry.open(_binding(frame_id="a-third-page"))
    assert [item.session for item in replaced] == [second.session]
    assert registry.revocation(second.session) == "disposed-lru"
    assert registry.revoke(third.session, "navigated") is not None
    assert registry.revocation(third.session) == "navigated"


def test_revoking_a_frame_ends_every_channel_of_it(tmp_path: Path) -> None:
    """Rule 6: navigation, replacement or a scope change revoke the old channel."""

    registry = SessionRegistry()
    opened = [registry.open(_binding())[0] for _ in range(2)]
    ended = registry.revoke_frame(
        repository_id=REPO, leaf_id=LEAF, frame_id=FRAME, reason="scope-changed"
    )
    assert ended == len(opened)
    assert registry.live == 0
    assert all(registry.revocation(item.session) == "scope-changed" for item in opened)


def test_evidence_opens_its_exact_destination_or_stays_unavailable(tmp_path: Path) -> None:
    """Rule 7: an admitted basis opens exactly; a gone object stays gone; a stranger opens nothing."""

    settings = config(tmp_path)
    root = task(settings)
    comparison = record()
    publish(root, comparison)
    opening = reopen_frame(frame_scope(root, LEAF, FRAME))
    assert opening.manifest is not None
    manifest = opening.manifest

    present = resolve_evidence_reference(manifest, "code-candidate:src/app.py")
    assert present.state == "opened"
    assert present.kind == "source"
    assert present.location["tree"] == comparison.code_candidate.tree
    assert present.target["comparison"] == manifest.binding.comparison_reference

    gone = resolve_evidence_reference(manifest, "knowledge:old-record")
    assert gone.state == "unavailable"
    assert "pruned" in gone.detail

    stranger = resolve_evidence_reference(manifest, "/etc/passwd")
    assert stranger.state == "refused"
    assert not stranger.target


def test_a_packet_from_another_comparison_opens_nothing(tmp_path: Path) -> None:
    """Rule 7: a page cannot widen its own comparison by carrying another packet's basis."""

    settings = config(tmp_path)
    root = task(settings)
    comparison = record()
    publish(root, comparison)
    opening = reopen_frame(frame_scope(root, LEAF, FRAME))
    assert opening.manifest is not None
    foreign = packet(record(number=8))
    tampered = opening.manifest.model_copy(update={"evidence": foreign})
    destination = resolve_evidence_reference(tampered, "code-candidate:src/app.py")
    assert destination.state == "refused"
    assert "different comparisons" in destination.detail


def test_the_session_record_carries_its_own_binding() -> None:
    """The value a page is given names its master, leaf, frame, revision and comparison."""

    session = CanvasSession(
        session="s" * 24,
        repository_id=REPO,
        master=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        revision=3,
        comparison="review-comparison:task/leaf/7",
        evidence_digest="e" * 64,
        entrypoint="index.html",
        created_at=0.0,
        expires_at=1.0,
    )
    assert session.expired(2.0) is True
    assert session.budget.diagnostics()["accepted"] == 0


# --- the session-bound page and asset base (L104-B03) -------------------------------------------

SHARED = "claim-basis:shared"


def test_one_session_serves_its_page_and_assets_under_the_page_prefix(tmp_path: Path) -> None:
    """Rules 3, 4: one prefix for the document and its relative assets, and no route naming a
    revision, frame or task anywhere in the frame surface."""

    client, _sessions, root = _client(tmp_path)
    publish(root, record())
    opened = _open(client)
    page = str(opened["pageUrl"])
    assert page == f"/canvas/page/{opened['session']}/"
    document = client.get(page)
    assert document.status_code == 200
    # The admitted bytes are served as they are, with the host's binding in front of them.
    assert document.content.startswith(b"<!doctype html>")
    assert b"<title>comparison</title><p>the explained change</p>" in document.content
    assert b"ar-canvas-binding/v1" in document.content
    assert document.headers["content-type"].startswith("text/html")
    assert "set-cookie" not in {name.lower() for name in document.headers}
    for path, media_type in (
        ("app.css", "text/css"),
        ("app.js", "text/javascript"),
        ("img/mark.svg", "image/svg+xml"),
    ):
        asset = client.get(f"{page}{path}")
        assert asset.status_code == 200, path
        assert asset.content == ASSETS[path]
        assert asset.headers["content-type"].startswith(media_type)
        assert asset.headers["x-content-type-options"] == "nosniff"
        assert "set-cookie" not in {name.lower() for name in asset.headers}
    assert client.get(f"{page}img/mark.svg").headers["content-security-policy"] == (
        "default-src 'none'; sandbox"
    )
    # The unbound route that named a repository, task, frame and revision is gone.
    assert client.get(f"/canvas/asset/{REPO}/{TASK}/{LEAF}/{FRAME}/1/app.css").status_code == 404


def test_the_asset_route_serves_only_the_sessions_own_revision(tmp_path: Path) -> None:
    """Rule 3: two existing revisions and a sibling frame, all refused through this session."""

    client, _sessions, root = _client(tmp_path)
    first = {"index.html": ASSETS["index.html"], "app.css": b"body{color:rgb(1,2,3)}"}
    second = {**ASSETS, "app.css": b"body{color:rgb(4,5,6)}"}
    publish(root, record(), request="req-1", page=PageContent(assets=first))
    opened = _open(client)
    assert opened["revision"] == 1
    page = str(opened["pageUrl"])
    publish(root, record(), request="req-2", page=PageContent(assets=second))
    # The session keeps serving the immutable revision it was admitted for.
    assert client.get(f"{page}app.css").content == b"body{color:rgb(1,2,3)}"
    # A revision number is not part of the address, so no caller can choose one.
    assert client.get(f"{page}1/app.css").status_code == 404
    assert client.get(f"{page}2/app.css").status_code == 404
    # Another revision's asset is not this revision's asset.
    assert client.get(f"{page}img/mark.svg").status_code == 404
    publish(root, record(), request="req-3", frame="sibling-page")
    sibling = _open(client, frame="sibling-page")
    assert client.get(f"{sibling['pageUrl']}app.css").content == ASSETS["app.css"]
    assert client.get(f"{page}app.css").content == b"body{color:rgb(1,2,3)}"
    reopened = _open(client)
    assert reopened["revision"] == 2
    assert client.get(f"{reopened['pageUrl']}app.css").content == b"body{color:rgb(4,5,6)}"


def test_a_traversed_symlinked_missing_or_corrupt_asset_is_refused(tmp_path: Path) -> None:
    """Rules 3, 4: containment after decoding, no symlink followed, no corrupt bytes served."""

    client, _sessions, root = _client(tmp_path)
    publish(root, record())
    page = str(_open(client)["pageUrl"])
    for path in (
        "..%2Ftask.json",
        "%2e%2e%2f%2e%2e%2ftask.json",
        "..%5Ctask.json",
        "%2Fetc%2Fpasswd",
        "missing.css",
        "%00app.css",
    ):
        refusal = client.get(f"{page}{path}")
        assert refusal.status_code == 404, path
        assert refusal.json()["reason"] == "asset-unavailable", path

    assets = frame_scope(root, LEAF, FRAME).frame_dir / "revisions" / "1" / "assets"
    stylesheet = assets / "app.css"
    outside = tmp_path / "outside.css"
    outside.write_bytes(b"body{color:rgb(9,9,9)}")
    stylesheet.unlink()
    stylesheet.symlink_to(outside)
    followed = client.get(f"{page}app.css")
    assert followed.status_code == 404
    assert b"rgb(9,9,9)" not in followed.content
    stylesheet.unlink()
    stylesheet.write_bytes(ASSETS["app.css"])
    assert client.get(f"{page}app.css").status_code == 200

    stylesheet.write_bytes(b"body{color:rgb(7,7,7)}")
    corrupt = client.get(f"{page}app.css")
    assert corrupt.status_code == 404
    assert "corrupt" in str(corrupt.json()["detail"])
    stylesheet.write_bytes(ASSETS["app.css"])
    (assets / "app.js").unlink()
    missing = client.get(f"{page}app.js")
    assert missing.status_code == 404
    assert "missing" in str(missing.json()["detail"])


# --- revocation and the stale channel (L104-B02) -----------------------------------------------


def test_a_replaced_channel_answers_410_and_changes_nothing(tmp_path: Path) -> None:
    """Rule 6: replacement revokes first, and a late message on the old value has no effect."""

    client, hosts, root = _client(tmp_path)
    publish(root, record())
    first = _open(client)
    old = str(first["session"])
    accepted = _post_message(client, old, _sent(first, type="status", payload={"kind": "ready"}))
    assert accepted == {"state": "accepted", "operation": "status"}
    second = _open(client)
    live = hosts.sessions.get(str(second["session"]))
    assert live is not None
    before = live.budget.diagnostics()
    assert hosts.sessions.revocation(old) == "replaced"
    late = client.post(
        f"/api/canvas/sessions/{old}/messages",
        json=_sent(
            first,
            requestId="request-late-1",
            type="open-evidence",
            payload={"reference": "code-candidate:src/app.py"},
        ),
    )
    assert late.status_code == 410
    assert late.json()["state"] == "refused"
    assert late.json()["reason"] == "replaced"
    assert "target" not in late.json() and "location" not in late.json()
    # The live channel's own ledger is untouched by the request on the dead one.
    assert live.budget.diagnostics() == before
    assert client.get(str(first["pageUrl"])).status_code == 410
    assert client.get(f"{first['pageUrl']}app.css").status_code == 410
    assert client.get(f"{first['pageUrl']}app.css").json()["reason"] == "replaced"


def test_an_evidence_request_on_a_stale_revision_opens_nothing(tmp_path: Path) -> None:
    """Rule 6: a channel whose revision is no longer ready opens nothing at all."""

    client, _sessions, root = _client(tmp_path)
    publish(root, record())
    opened = _open(client)
    publish(root, record(), request="req-2")
    session = str(opened["session"])
    body = _post_message(
        client,
        session,
        _sent(opened, type="open-evidence", payload={"reference": "code-candidate:src/app.py"}),
    )
    assert body["state"] == "unavailable"
    assert "target" not in body and "location" not in body
    assert "retained ready revision is 2" in str(body["detail"])

    # A display message on that channel is refused too (L104-B02): the channel is bound to a
    # revision that is no longer the ready one, so nothing it says may be applied.
    stale_status = _post_message(
        client,
        session,
        _sent(
            opened,
            type="status",
            requestId="request-stale-status-1",
            payload={"kind": "ready"},
        ),
    )
    assert stale_status["state"] == "refused"
    assert stale_status["reason"] == "stale-revision"
    assert "reopen the frame" in str(stale_status["detail"])


# --- the protocol's typed refusals (L104-B07) --------------------------------------------------

_WRONG_TYPES: dict[str, dict[str, object]] = {
    "status kind list": {"type": "status", "payload": {"kind": []}},
    "status kind object": {"type": "status", "payload": {"kind": {}}},
    "status status kind number": {"type": "status", "payload": {"kind": 1}},
    "status detail number": {"type": "status", "payload": {"kind": "ready", "detail": 123}},
    "status detail list": {"type": "status", "payload": {"kind": "ready", "detail": ["x"]}},
    "status detail blank": {"type": "status", "payload": {"kind": "ready", "detail": "   "}},
    "status bytes bool": {"type": "status", "payload": {"kind": "size", "bytes": True}},
    "status bytes float": {"type": "status", "payload": {"kind": "size", "bytes": 1.5}},
    "status bytes string": {"type": "status", "payload": {"kind": "size", "bytes": "10"}},
    "status bytes negative": {"type": "status", "payload": {"kind": "size", "bytes": -1}},
    "reference null": {"type": "open-evidence", "payload": {"reference": None}},
    "reference list": {
        "type": "open-evidence",
        "payload": {"reference": ["code-candidate:src/app.py"]},
    },
    "reference blank": {"type": "open-evidence", "payload": {"reference": "  "}},
    "claim id number": {
        "type": "open-evidence",
        "payload": {"reference": "code-candidate:src/app.py", "claimId": 12},
    },
    "claim id list": {
        "type": "open-evidence",
        "payload": {"reference": "code-candidate:src/app.py", "claimId": []},
    },
    "question object": {"type": "request-explanation", "payload": {"question": {"text": "why"}}},
    "question list": {"type": "request-explanation", "payload": {"question": ["why"]}},
    "payload string": {"payload": "not-an-object"},
    "payload list": {"payload": []},
    "payload unknown field": {"payload": {"command": "rm -rf /"}},
    "envelope unknown field": {"operation": "start-role"},
    "version string": {"protocolVersion": "1"},
    "version object": {"protocolVersion": {}},
    "version list": {"protocolVersion": []},
    "session number": {"session": 12},
    "session list": {"session": []},
    "revision string": {"revision": "1"},
    "revision float": {"revision": 1.5},
    "revision bool": {"revision": True},
    "comparison null": {"comparison": None},
    "comparison object": {"comparison": {"reference": "x"}},
    "type list": {"type": []},
    "type object": {"type": {}},
    "request id number": {"requestId": 12345678},
    "schema other": {"schema": "ar-canvas-message/v2"},
}


def test_every_wrong_type_envelope_and_payload_is_refused_by_name(tmp_path: Path) -> None:
    """Rules 5, 6: no legal JSON value escapes as a 500 or reaches an effect."""

    client, hosts, root = _client(tmp_path)
    publish(root, record())
    for name, override in _WRONG_TYPES.items():
        opened = _open(client)
        body = _post_message(client, str(opened["session"]), {**_sent(opened), **override})
        assert body["state"] == "refused", name
        assert body["reason"] in REFUSAL_REASONS, (name, body)

    opened = _open(client)
    session = str(opened["session"])
    url = f"/api/canvas/sessions/{session}/messages"
    headers = {"content-type": "application/json"}
    nested = "[" * 40 + "]" * 40
    deep = (
        '{"schema":"ar-canvas-message/v1","protocolVersion":1,"type":"status",'
        '"requestId":"request-deep-1","session":"' + session + '","revision":1,'
        '"comparison":"'
        + str(opened["comparison"])
        + '","payload":{"kind":"ready","detail":'
        + nested
        + "}}"
    )
    deep_refusal = _post_raw(client, url, deep.encode(), headers)
    assert deep_refusal["reason"] == "malformed"
    # The depth guard answers, not some later field rule: the document is refused before a
    # validator walks it (L104-B07).
    assert "nests deeper" in str(deep_refusal["detail"])
    invalid = b'{"schema":"ar-canvas-message/v1","payload":{"note":"\xff"}}'
    assert _post_raw(client, url, invalid, headers)["reason"] == "malformed"
    recursed = '{"payload":{"note":' + "[" * 1500 + "]" * 1500 + "}}"
    assert _post_raw(client, url, recursed.encode(), headers)["reason"] in {
        "malformed",
        "oversized",
    }
    oversized = b"x" * (MAX_MESSAGE_BYTES + 1)
    assert _post_raw(client, url, oversized, headers)["reason"] == "oversized"
    live = hosts.sessions.get(session)
    assert live is not None
    assert live.budget.diagnostics()["accepted"] == 0
    assert live.budget.last_status is None


def _post_raw(
    client: TestClient, url: str, payload: bytes, headers: dict[str, str]
) -> dict[str, object]:
    response = client.post(url, content=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- bound work before validation (L104-B08) ---------------------------------------------------


def test_a_malformed_flood_is_rate_charged_and_diagnostics_stay_bounded(tmp_path: Path) -> None:
    """Rules 5, 8: refused traffic is charged, so a flood stops at the bucket, not at a parser."""

    client, hosts, root = _client(tmp_path)
    publish(root, record())
    oversized = str(_open(client)["session"])
    response = client.post(
        f"/api/canvas/sessions/{oversized}/messages",
        content=b"y" * (MAX_MESSAGE_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert response.json()["reason"] == "oversized"

    session = str(_open(client)["session"])
    url = f"/api/canvas/sessions/{session}/messages"
    reasons = [
        client.post(url, content=b"{not json", headers={"content-type": "application/json"}).json()[
            "reason"
        ]
        for _ in range(60)
    ]
    assert reasons[0] == "malformed"
    assert reasons.count("rate-limited") >= 10
    live = hosts.sessions.get(session)
    assert live is not None
    diagnostics = live.budget.diagnostics()
    assert diagnostics["accepted"] == 0
    assert live.budget.refused == len(reasons)
    assert len(diagnostics["refusals"]) == 20
    assert all(len(item["detail"]) <= 200 for item in diagnostics["refusals"])


# --- typed evidence identity (L104-B09) ---------------------------------------------------------


def _colliding_claims(
    comparison: ReviewTreeComparisonRecord,
) -> tuple[EvidenceClaim, EvidenceClaim]:
    """Two claims of different kinds that carry the same reference string."""

    tree = comparison.code_candidate.tree
    opened = EvidenceClaim(
        claim_id="c-opened",
        authoring_revision="rev-1",
        classification="recorded-fact",
        subject="the first claim",
        applicability="this comparison",
        statement="the candidate tree carries the file",
        bases=(
            ClaimBasis(
                kind="tree-blob",
                reference=SHARED,
                state="present",
                side="code-candidate",
                tree=tree,
                path="src/first.py",
            ),
        ),
    )
    unread = EvidenceClaim(
        claim_id="c-unread",
        authoring_revision="rev-1",
        classification="unavailable-evidence",
        subject="the second claim",
        applicability="this comparison",
        statement="the earlier record cannot be read",
        bases=(
            ClaimBasis(
                kind="knowledge-record",
                reference=SHARED,
                state="unavailable",
                limitation="the record was pruned before this comparison was recorded",
                record_id="record-shared",
                record_revision=1,
                path="onboarding/shared.md",
            ),
        ),
        limit="the object is gone; today's record is not it",
    )
    return opened, unread


def test_evidence_resolves_by_typed_claim_identity(tmp_path: Path) -> None:
    """Rule 7: a shared reference without a claim identity refuses; a named claim answers itself."""

    settings = config(tmp_path)
    root = task(settings)
    comparison = record()
    opened, unread = _colliding_claims(comparison)
    publish(root, comparison, request="req-first", page=PageContent(claims=(opened, unread)))
    manifest = reopen_frame(frame_scope(root, LEAF, FRAME)).manifest
    assert manifest is not None

    ambiguous = resolve_evidence_reference(manifest, SHARED)
    assert ambiguous.state == "refused"
    assert "claim identity" in ambiguous.detail
    assert not ambiguous.target

    first = resolve_evidence_reference(manifest, SHARED, claim_id="c-opened")
    assert first.state == "opened"
    assert first.kind == "source"
    assert first.location["path"] == "src/first.py"
    second = resolve_evidence_reference(manifest, SHARED, claim_id="c-unread")
    assert second.state == "unavailable"
    assert second.location["record_id"] == "record-shared"
    assert "pruned" in second.detail
    unknown = resolve_evidence_reference(manifest, SHARED, claim_id="c-nowhere")
    assert unknown.state == "refused"
    assert not unknown.target

    publish(root, comparison, request="req-second", page=PageContent(claims=(unread, opened)))
    reversed_manifest = reopen_frame(frame_scope(root, LEAF, FRAME)).manifest
    assert reversed_manifest is not None
    assert resolve_evidence_reference(reversed_manifest, SHARED).state == "refused"
    reversed_first = resolve_evidence_reference(reversed_manifest, SHARED, claim_id="c-opened")
    assert reversed_first.location["path"] == "src/first.py"
    assert resolve_evidence_reference(reversed_manifest, SHARED, claim_id="c-unread").state == (
        "unavailable"
    )

    publish(root, comparison, request="req-third")
    default_manifest = reopen_frame(frame_scope(root, LEAF, FRAME)).manifest
    assert default_manifest is not None
    borrowed = resolve_evidence_reference(
        default_manifest, "code-candidate:src/app.py", claim_id="c-gone"
    )
    assert borrowed.state == "refused"
    assert "c-gone" in borrowed.detail


def test_a_retained_receipt_answers_its_own_read_state(tmp_path: Path) -> None:
    """Rule 7: an unread or vanished capture keeps its limitation and never opens."""

    settings = config(tmp_path)
    root = task(settings)
    comparison = record()
    present = SupplementalReceipt(
        receipt_id="receipt-present",
        canonical_source="notes/reports/measurement.md",
        read_state="present",
        capture_identity="capture-1",
        digest="a" * 64,
        byte_size=120,
        establishes="the measured latency",
    )
    gone = SupplementalReceipt(
        receipt_id="receipt-gone",
        canonical_source="notes/reports/older.md",
        read_state="unavailable",
        capture_identity="capture-0",
        establishes="the earlier measurement",
        limitation="the retained capture was removed before this comparison",
    )
    changed = SupplementalReceipt(
        receipt_id="receipt-changed",
        canonical_source="notes/reports/changed.md",
        read_state="changed",
        capture_identity="capture-2",
        digest="b" * 64,
        byte_size=64,
        establishes="the edited document",
        limitation="the source changed after the capture; these are the capture's bytes",
    )
    publish(
        root,
        comparison,
        page=PageContent(supplemental=(present, gone, changed)),
    )
    manifest = reopen_frame(frame_scope(root, LEAF, FRAME)).manifest
    assert manifest is not None

    for receipt in (gone, changed):
        destination = resolve_evidence_reference(manifest, receipt.receipt_id)
        assert destination.state == "unavailable", receipt.receipt_id
        assert destination.detail == receipt.limitation
        assert destination.location["receipt_id"] == receipt.receipt_id
        assert destination.location["canonical_source"] == receipt.canonical_source
        assert "digest" not in destination.location
    opened = resolve_evidence_reference(manifest, "receipt-present")
    assert opened.state == "opened"
    assert opened.kind == "supplemental"
    assert opened.location["receipt_id"] == "receipt-present"
    assert opened.location["digest"] == "a" * 64


def test_a_collection_item_is_admitted_only_by_its_address_fields(tmp_path: Path) -> None:
    """Rule 7: prose that merely equals a reference string is not an admission."""

    settings = config(tmp_path)
    root = task(settings)
    publish(root, record())
    manifest = reopen_frame(frame_scope(root, LEAF, FRAME)).manifest
    assert manifest is not None
    collection = EvidenceCollection(
        name="ad-hoc",
        producer="the test",
        population="two supplied rows",
        state="present",
        count=2,
        items=(
            {"label": "prose:the-claim", "title": "title-only:ref", "path": "docs/thing.md"},
            {"label": "another row"},
        ),
    )
    evidence = manifest.evidence.model_copy(
        update={"collections": (*manifest.evidence.collections, collection)}
    )
    widened = manifest.model_copy(update={"evidence": evidence})

    for prose in ("prose:the-claim", "title-only:ref"):
        assert resolve_evidence_reference(widened, prose).state == "refused", prose
    item = resolve_evidence_reference(widened, "docs/thing.md")
    assert item.state == "opened"
    assert item.kind == "collection-item"
    assert item.location["path"] == "docs/thing.md"
    assert item.location["collection"] == "ad-hoc"
