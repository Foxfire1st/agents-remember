"""MIK-R104/105 over HTTP: the frame surface, the protocol route, the panel and the write guard.

One case per boundary the routes own:

1. the panel reads a saved page, its policy and its bounded state, and starts nothing;
2. a session serves exactly its admitted document and assets, with policy headers, and refuses a
   traversal, a foreign revision and an ended channel;
3. the protocol route is bound to the session, refuses a replay and an oversized body, and answers
   an admitted evidence reference exactly;
4. generation refuses an implicit activation, records its attempt, and publishes only through the
   store -- with a retry reconciling the same attempt;
5. a foreign or opaque origin cannot reach a state-changing route at all;
6. every served explainer document references its own ``page.js`` and runs it inside the frame's
   sandbox, under the same CSP and the same opaque origin, with nothing to switch that off;
7. a page that navigates its frame is an accepted channel that gains no write authority, and the
   host installs no navigation guard to refuse it (MIK-R104 v2, D331);
8. every route, reads included, answers only the dashboard's own host and port (rule 15, D332).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from agents_remember.application.review_frame_store import frame_scope
from agents_remember.canvas import presenter as presenter_module
from agents_remember.canvas.policy import FRAME_PERMISSIONS_POLICY
from agents_remember.canvas.prospective import ProspectiveResult
from agents_remember.canvas.sessions import SessionRegistry
from agents_remember.serving import canvas as canvas_module
from agents_remember.serving import write_guard
from agents_remember.serving.canvas import CanvasHosts, register_canvas_routes
from agents_remember.serving.write_guard import (
    RefuseCrossOriginWrites,
    RefuseForeignHosts,
    host_refusal,
)
from canvas_host_test_support import (
    FRAME,
    LEAF,
    REPO,
    TASK,
    FakeRunner,
    PageContent,
    config,
    publish,
    record,
    stored_attempt,
    task,
    write_settings,
)
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

LAUNCH = {"harness": "hermes", "model": "the-presenter-model"}
BASE = f"/api/canvas/frames/{REPO}/{TASK}/{LEAF}/{FRAME}"


class LoadedProspective:
    """A prospective load that observed a working page.

    The route composes its own presenter, so this module reaches the load gate the way the shipped
    default does. These cases assert the route's publication path; the gate itself is
    :mod:`test_canvas_prospective`.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, bytes], str, object]] = []

    def load(self, *, assets, entrypoint, packet):
        self.calls.append((dict(assets), entrypoint, packet))
        return ProspectiveResult(state="loaded", kind="browser")


@pytest.fixture
def loaded_prospective(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject an explicitly model-free load stand-in; no browser observation is credited."""

    monkeypatch.setattr(
        presenter_module, "BrowserProspectiveLoad", lambda *_args, **_kwargs: LoadedProspective()
    )


def _client(tmp_path: Path) -> tuple[TestClient, CanvasHosts, Path]:
    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    publish(root, record())
    hosts = CanvasHosts(
        config=settings,
        sessions=SessionRegistry(),
        runner=FakeRunner(),
    )
    app = FastAPI()
    register_canvas_routes(app, hosts)
    app.add_middleware(RefuseCrossOriginWrites)
    return TestClient(app), hosts, root


def _open(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/canvas/sessions",
        json={"repository": REPO, "master": TASK, "leaf": LEAF, "frame": FRAME},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _message(session: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "ar-canvas-message/v1",
        "protocolVersion": 1,
        "type": "ready",
        "requestId": "request-0001",
        "session": session,
        "revision": 1,
        "comparison": "review-comparison:260928_maintained-invariant-knowledge/260928-MIK-L104/7",
        "payload": {},
    }
    payload.update(overrides)
    return payload


def test_the_panel_reads_a_saved_page_and_starts_nothing(tmp_path: Path) -> None:
    """Rule 2 of MIK-R105: opening, status and attempts are observational."""

    client, hosts, root = _client(tmp_path)
    response = client.get(BASE)
    assert response.status_code == 200
    body = response.json()
    assert body["opening"]["state"] == "ready"
    assert body["opening"]["revision"] == 1
    assert body["policy"]["scripts"] is True
    assert body["policy"]["originArrangement"] == "opaque-sandbox-v1"
    assert body["presenter"]["attempts"] == []
    assert hosts.sessions.live == 0
    assert not list((root / "notes" / "reports" / "review-presenter").rglob("*.json"))


def test_a_session_serves_its_own_document_and_assets_only(tmp_path: Path) -> None:
    """Rules 2, 3, 4: same revision bytes, policy headers, and no path outside the revision."""

    client, _hosts, _root = _client(tmp_path)
    opened = _open(client)
    session = str(opened["session"])
    assert opened["scripts"] is True
    assert opened["sandbox"] == "allow-scripts"
    document = client.get(str(opened["pageUrl"]))
    assert document.status_code == 200
    csp = document.headers["content-security-policy"]
    assert "sandbox allow-scripts" in csp and "connect-src 'none'" in csp
    assert "allow-same-origin" not in csp
    assert document.headers["cache-control"] == "no-store"
    assert "set-cookie" not in {name.lower() for name in document.headers}

    page_url = str(opened["pageUrl"])
    style = client.get(f"{page_url}app.css")
    assert style.status_code == 200
    assert style.headers["content-type"].startswith("text/css")
    assert style.headers["x-content-type-options"] == "nosniff"
    # An opaque-origin document fetches an admitted asset in CORS mode; without the wildcard the
    # browser refuses the load (L104-A1/B03).
    assert style.headers["access-control-allow-origin"] == "*"
    assert client.get(f"{page_url}app.js").headers["content-type"].startswith("text/javascript")
    traversal = client.get(f"{page_url}../task.json")
    assert traversal.status_code in {404, 410}
    assert traversal.json()["state"] == "refused"
    assert client.get(f"{page_url}missing.css").status_code == 404
    assert session


def test_a_proxied_page_limits_assets_to_the_browser_origin(tmp_path: Path) -> None:
    client, _hosts, _root = _client(tmp_path)
    opened = _open(client)
    document = client.get(
        str(opened["pageUrl"]),
        headers={
            "host": "127.0.0.1:9795",
            "x-forwarded-host": "localhost:5195",
            "x-forwarded-proto": "http",
        },
    )
    assert document.status_code == 200
    csp = document.headers["content-security-policy"]
    directives = {part.split(" ", 1)[0]: part for part in csp.split("; ")}
    assets = f"http://localhost:5195/canvas/page/{opened['session']}/"
    assert directives["style-src"] == f"style-src {assets} 'unsafe-inline'"
    assert directives["img-src"] == f"img-src {assets} data:"
    assert "9795" not in csp
    assert directives["sandbox"] == "sandbox allow-scripts"
    assert directives["script-src"] == f"script-src {assets} 'unsafe-inline'"
    assert "allow-same-origin" not in csp
    assert directives["connect-src"] == "connect-src 'none'"
    assert directives["frame-ancestors"] == "frame-ancestors 'self'"


def test_the_protocol_route_is_bound_bounded_and_replay_safe(tmp_path: Path) -> None:
    """Rule 6: refusals by name; an admitted reference opens exactly; a stranger opens nothing."""

    client, _hosts, _root = _client(tmp_path)
    session = str(_open(client)["session"])
    accepted = client.post(f"/api/canvas/sessions/{session}/messages", json=_message(session))
    assert accepted.json() == {"state": "accepted", "operation": "ready"}
    replay = client.post(f"/api/canvas/sessions/{session}/messages", json=_message(session))
    assert replay.json()["reason"] == "replayed"
    stale = client.post(
        f"/api/canvas/sessions/{session}/messages",
        json=_message(session, requestId="request-0002", revision=2),
    )
    assert stale.json()["reason"] == "stale-revision"
    foreign = client.post(
        f"/api/canvas/sessions/{session}/messages",
        json=_message(session, requestId="request-0003", comparison="review-comparison:x"),
    )
    assert foreign.json()["reason"] == "foreign-comparison"
    oversized = client.post(
        f"/api/canvas/sessions/{session}/messages",
        content=b"x" * 5000,
        headers={"content-type": "application/json"},
    )
    assert oversized.json()["reason"] == "oversized"
    opened = client.post(
        f"/api/canvas/sessions/{session}/messages",
        json=_message(
            session,
            requestId="request-0004",
            type="open-evidence",
            payload={"reference": "code-candidate:src/app.py"},
        ),
    )
    body = opened.json()
    assert body["state"] == "opened" and body["kind"] == "source"
    gone = client.post(
        f"/api/canvas/sessions/{session}/messages",
        json=_message(
            session,
            requestId="request-0005",
            type="open-evidence",
            payload={"reference": "knowledge:old-record"},
        ),
    )
    assert gone.json()["state"] == "unavailable"
    stranger = client.post(
        f"/api/canvas/sessions/{session}/messages",
        json=_message(
            session,
            requestId="request-0006",
            type="open-evidence",
            payload={"reference": "/etc/passwd"},
        ),
    )
    assert stranger.json()["state"] == "refused"


def test_revocation_ends_the_channel_without_an_effect(tmp_path: Path) -> None:
    """Rule 6: a disposed channel answers refused and changes nothing."""

    client, _hosts, _root = _client(tmp_path)
    opened = _open(client)
    session = str(opened["session"])
    revoked = client.post(f"/api/canvas/sessions/{session}/revoke", json={"reason": "navigated"})
    assert revoked.json()["revoked"] is True
    assert client.get(str(opened["pageUrl"])).status_code == 410
    assert (
        client.post(f"/api/canvas/sessions/{session}/messages", json=_message(session)).status_code
        == 410
    )


def test_a_stored_attempt_publishes_through_the_store(
    tmp_path: Path, loaded_prospective: None
) -> None:
    """Rules 2, 5, 7: one explicit start, one attributable attempt, one store publication."""

    client, _hosts, root = _client(tmp_path)
    attempt = stored_attempt(
        canvas_module._presenter(_hosts, frame_scope(root, LEAF, FRAME), LEAF)
    ).model_dump()
    assets = Path(attempt["working_directory"]) / "assets"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<!doctype html><p>explained</p>")
    published = client.post(
        f"/api/canvas/attempts/{REPO}/{TASK}/{LEAF}/{FRAME}/{attempt['attempt_id']}/publish",
        json={"entrypoint": "index.html"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["state"] == "published"
    assert published.json()["revision"] == 2

    state = client.get(BASE).json()
    assert state["opening"]["revision"] == 2
    assert state["presenter"]["attempts"][0]["state"] == "published"
    assert (root / "notes" / "reports" / "review-frames").is_dir()


@pytest.mark.parametrize(
    ("origin", "reason"),
    [("null", "null-origin"), ("http://evil.example", "foreign-origin")],
)
def test_a_foreign_or_opaque_origin_cannot_write(tmp_path: Path, origin: str, reason: str) -> None:
    """Rule 4: an opaque-origin frame reaches no state-changing route at all."""

    client, _hosts, _root = _client(tmp_path)
    response = client.post(
        "/api/canvas/sessions",
        json={"repository": REPO, "master": TASK, "leaf": LEAF, "frame": FRAME},
        headers={"origin": origin},
    )
    assert response.status_code == 403
    assert response.json()["reason"] == reason
    allowed = client.post(
        "/api/canvas/sessions",
        json={"repository": REPO, "master": TASK, "leaf": LEAF, "frame": FRAME},
        headers={"origin": "http://testserver"},
    )
    assert allowed.status_code == 200


#: What a browser sends when it loads a document, either in its tab or in a frame.
NAVIGATION = {
    "sec-fetch-mode": "navigate",
    "sec-fetch-dest": "document",
    "sec-fetch-site": "same-origin",
}


def test_the_served_page_carries_its_isolation_policy(tmp_path: Path) -> None:
    """Rules 2, 4: an opaque origin by the served policy itself, with the page's own script admitted.

    The ``sandbox`` directive is what makes a *direct open* opaque; the COOP/CORP pair and the
    Permissions-Policy are the document's own answers. An asset is ``cross-origin`` CORP because the
    document that loads it is an opaque origin -- with ``same-origin`` there the page cannot load its
    own CSS (L104-B01/B03/B04).
    """

    client, _hosts, _root = _client(tmp_path)
    opened = _open(client)
    assert opened["scripts"] is True
    assert opened["sandbox"] == "allow-scripts"
    document = client.get(str(opened["pageUrl"]))
    csp = document.headers["content-security-policy"]
    directives = {
        part.strip().split(" ")[0]: part.strip() for part in csp.split(";") if part.strip()
    }
    assert directives["sandbox"] == "sandbox allow-scripts"
    assert directives["script-src"] == (
        f"script-src http://testserver/canvas/page/{opened['session']}/ 'unsafe-inline'"
    )
    assert "allow-same-origin" not in csp and "'unsafe-eval'" not in csp
    assert directives["connect-src"] == "connect-src 'none'"
    assert directives["frame-ancestors"] == "frame-ancestors 'self'"
    assert document.headers["cross-origin-opener-policy"] == "same-origin"
    assert document.headers["cross-origin-resource-policy"] == "same-origin"
    assert "set-cookie" not in {name.lower() for name in document.headers}

    policy = document.headers["permissions-policy"]
    assert policy == FRAME_PERMISSIONS_POLICY
    assert ";" not in policy
    entries = [entry.strip() for entry in policy.split(",")]
    assert all(entry.endswith("=()") for entry in entries)
    names = [entry[: -len("=()")] for entry in entries]
    assert len(names) == len(set(names))
    # Chromium ignores an unrecognized feature name and reports it on the console, which reads like
    # a parse failure; the served dictionary carries only names it applies (L104-B04).
    assert "battery" not in names

    asset = client.get(f"{opened['pageUrl']}app.css")
    assert asset.status_code == 200
    assert asset.headers["cross-origin-resource-policy"] == "cross-origin"
    assert asset.headers["access-control-allow-origin"] == "*"


#: A page as the explainer publishes it: its document loads the script shipped beside it.
PAGE_DOCUMENT = (
    b'<!doctype html><script type="module" src="page.js"></script><p>the explained change</p>'
)
PAGE_SCRIPT = b"document.documentElement.dataset.explainer = 'ready'"


def test_the_served_explainer_document_always_references_its_page_js(tmp_path: Path) -> None:
    """Rules 2, 4: the document loads its own page.js, and the isolation that runs it is unchanged.

    Nothing decides this at runtime: no record, no digest and no boolean can serve the page without
    its script or with a wider policy. The document carries ``sandbox="allow-scripts"`` (an opaque
    origin, never ``allow-same-origin``), a ``script-src`` naming this session's own asset prefix
    plus inline script, and nothing else peer-visible. The reference is relative, so it resolves
    inside that one prefix and nowhere else.
    """

    assets = {"index.html": PAGE_DOCUMENT, "page.js": PAGE_SCRIPT}
    client, _hosts, root = _client(tmp_path)
    publish(root, record(), request="req-page-js", page=PageContent(assets=assets))
    opened = _open(client)
    page_url = str(opened["pageUrl"])
    assert opened["scripts"] is True
    assert opened["sandbox"] == "allow-scripts"

    document = client.get(page_url)
    assert document.status_code == 200
    assert b'<script type="module" src="page.js"></script>' in document.content
    csp = document.headers["content-security-policy"]
    directives = {
        part.strip().split(" ")[0]: part.strip() for part in csp.split(";") if part.strip()
    }
    own = f"http://testserver/canvas/page/{opened['session']}/"
    assert directives["sandbox"] == "sandbox allow-scripts"
    assert directives["script-src"] == f"script-src {own} 'unsafe-inline'"
    assert "allow-same-origin" not in csp and "'unsafe-eval'" not in csp
    assert directives["connect-src"] == "connect-src 'none'"
    assert directives["frame-src"] == "frame-src 'none'"
    assert document.headers["cross-origin-opener-policy"] == "same-origin"
    assert document.headers["cross-origin-resource-policy"] == "same-origin"
    assert "set-cookie" not in {name.lower() for name in document.headers}

    # The referenced script is this session's own admitted asset, under the same prefix.
    script = client.get(f"{page_url}page.js")
    assert script.status_code == 200
    assert script.content == PAGE_SCRIPT
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.headers["access-control-allow-origin"] == "*"


def test_a_navigating_page_is_an_accepted_channel_and_gains_no_write(tmp_path: Path) -> None:
    """Rules 4, 10 and D331: the frame navigating itself is observed, not refused, and not a grant.

    MIK-R104 v2 builds no navigation guard: a document navigation request reaches the route exactly
    as any other request does. What keeps the generated page out of the service is the sandbox (an
    opaque origin) and the write guard, so the same navigation that reaches a document is refused
    the moment it is a write.
    """

    client, _hosts, _root = _client(tmp_path)
    page_url = str(_open(client)["pageUrl"])
    # No navigation admission: the page document, its asset and the dashboard API all answer a
    # browser navigation request with their ordinary status.
    assert client.get(page_url, headers=NAVIGATION).status_code == 200
    assert client.get(f"{page_url}app.css", headers=NAVIGATION).status_code == 200
    assert client.get(BASE, headers=NAVIGATION).status_code == 200
    # The write the navigated, opaque-origin page would attempt is refused by Fetch Metadata, not by
    # path: a document that navigated away is a cross-site context (D332's literal precedence, so the
    # reason is the header that decides, not the Origin that follows it).
    refused = client.post(
        "/api/canvas/sessions",
        json={"repository": REPO, "master": TASK, "leaf": LEAF, "frame": FRAME},
        headers={**NAVIGATION, "sec-fetch-site": "cross-site", "origin": "null"},
    )
    assert refused.status_code == 403
    assert refused.json()["reason"] == "cross-site"
    # And the navigated page's own origin, when Fetch Metadata says the page is this one, is not a
    # second refusal: D332 consults Origin only where the header is absent.
    admitted = client.post(
        "/api/canvas/sessions",
        json={"repository": REPO, "master": TASK, "leaf": LEAF, "frame": FRAME},
        headers={**NAVIGATION, "origin": "null"},
    )
    assert admitted.status_code == 200
    # And the module ships no navigation guard to be re-introduced by accident (v2, D331).
    assert not hasattr(write_guard, "RefuseForeignNavigations")
    assert not hasattr(write_guard, "navigation_refusal")
    assert not hasattr(write_guard, "NAVIGATION_REASON")


# --- rule 15 (D332): the dashboard answers only its own host and port --------------------------


def _scope(*, method: str = "GET", headers: tuple[tuple[str, str], ...] = ()) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": "/api/state",
        "scheme": "http",
        "server": ("127.0.0.1", 8765),
        "query_string": b"",
        "headers": [(name.encode(), value.encode()) for name, value in headers],
    }


def test_host_refusal_admits_only_this_dashboards_own_host_and_port() -> None:
    """Rule 15: the loopback names and the bound port are admitted; a rebinding name is not."""

    assert host_refusal(_scope(headers=(("host", "127.0.0.1:8765"),))) is None
    assert host_refusal(_scope(headers=(("host", "localhost:8765"),))) is None
    assert host_refusal(_scope(headers=(("host", "[::1]:8765"),))) is None
    # A bare name means port 80, which is not the port this server bound.
    assert host_refusal(_scope(headers=(("host", "127.0.0.1"),))) is not None
    foreign = host_refusal(_scope(headers=(("host", "evil.example:8765"),)))
    assert foreign is not None and foreign[0] == "foreign-host"
    wrong_port = host_refusal(_scope(headers=(("host", "127.0.0.1:9999"),)))
    assert wrong_port is not None and "port" in wrong_port[1]
    assert host_refusal(_scope()) is None


@pytest.mark.parametrize(("scheme", "default"), [("http", 80), ("https", 443)])
def test_an_asgi_default_port_is_still_checked(scheme: str, default: int) -> None:
    """An omitted ASGI port uses its scheme, without admitting another name or port."""

    def request(host: str, port: int | None = None) -> dict:
        return {**_scope(headers=(("host", host),)), "scheme": scheme, "server": ("test", port)}

    assert host_refusal(request("test")) is None
    assert host_refusal(request(f"test:{default}")) is None
    assert host_refusal(request(f"test:{default + 1}")) is not None
    assert host_refusal(request("evil.example")) is not None
    assert host_refusal(request(f"test:{default + 1}", default + 1)) is None
    assert host_refusal(request("test", default + 1)) is not None


def test_the_host_guard_refuses_a_rebinding_request_before_any_route() -> None:
    """Rule 15: a read and a write are both refused for a foreign ``Host``; the dashboard's own passes."""

    app = FastAPI()

    @app.get("/read")
    def _read() -> object:
        return {"state": "ok"}

    @app.post("/write")
    def _write() -> object:
        return {"state": "ok"}

    app.add_middleware(RefuseForeignHosts)
    client = TestClient(app)
    assert client.get("/read").status_code == 200
    refused_read = client.get("/read", headers={"host": "evil.example"})
    assert refused_read.status_code == 403
    assert refused_read.json()["reason"] == "foreign-host"
    refused_write = client.post("/write", headers={"host": "evil.example"})
    assert refused_write.status_code == 403
    assert refused_write.json()["reason"] == "foreign-host"


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"sec-fetch-site": "cross-site"}, 403),
        ({"sec-fetch-site": "same-site"}, 403),
        ({"origin": "null"}, 403),
        ({"origin": "http://elsewhere.example"}, 403),
        ({"origin": "http://testserver", "sec-fetch-site": "same-origin"}, 200),
        ({}, 200),
        # D332 literal (L104-A5): `none` is not `same-origin`, so a browser that says so is refused.
        ({"sec-fetch-site": "none"}, 403),
        # D332 literal: Origin is a fallback ONLY when Fetch Metadata is absent, so a same-origin
        # request that also carries a null or foreign Origin is judged by Fetch Metadata and passes.
        ({"sec-fetch-site": "same-origin", "origin": "null"}, 200),
        ({"sec-fetch-site": "same-origin", "origin": "http://elsewhere.example"}, 200),
    ],
)
def test_the_write_guard_admits_only_the_dashboards_own_page(
    tmp_path: Path, headers: dict[str, str], status: int
) -> None:
    """Rule 15's write half: cross-site, same-site and opaque origins are refused; CLI calls pass."""

    client, _hosts, _root = _client(tmp_path)
    response = client.post(
        "/api/canvas/sessions",
        json={"repository": REPO, "master": TASK, "leaf": LEAF, "frame": FRAME},
        headers=headers,
    )
    assert response.status_code == status


def test_the_write_guard_reads_fetch_metadata_before_the_origin_fallback() -> None:
    """D332 literal, at the predicate (L104-A5): header precedence, not header preference."""

    # `none` is a browser context that is not this page's origin.
    none = write_guard.refusal_reason(_scope(method="POST", headers=(("sec-fetch-site", "none"),)))
    assert none is not None and none[0] == "cross-site"
    # Present and same-origin: the request is this page's, whatever Origin also says.
    assert (
        write_guard.refusal_reason(
            _scope(method="POST", headers=(("sec-fetch-site", "same-origin"), ("origin", "null")))
        )
        is None
    )
    assert (
        write_guard.refusal_reason(
            _scope(
                method="POST",
                headers=(("sec-fetch-site", "same-origin"), ("origin", "http://elsewhere.example")),
            )
        )
        is None
    )
    # Absent: the Origin fallback decides, and `null` is not this dashboard.
    fallback = write_guard.refusal_reason(_scope(method="POST", headers=(("origin", "null"),)))
    assert fallback is not None and fallback[0] == "null-origin"
    # Reads are not this guard's business.
    assert (
        write_guard.refusal_reason(_scope(method="GET", headers=(("sec-fetch-site", "none"),)))
        is None
    )


@pytest.mark.parametrize("size", [1024 * 1024, 2 * 1024 * 1024])
def test_one_large_asgi_chunk_copies_only_the_body_limit(monkeypatch, size):
    copied = []

    class MeasuredBuffer(bytearray):
        def extend(self, chunk):
            copied.append(len(chunk))
            return super().extend(chunk)

    async def receive():
        return {"type": "http.request", "body": b"x" * size, "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/"}, receive)
    monkeypatch.setattr(canvas_module, "bytearray", MeasuredBuffer, raising=False)
    result = asyncio.run(canvas_module._bounded_raw(request))
    assert len(result) == canvas_module.MAX_REQUEST_BYTES + 1
    assert copied == [canvas_module.MAX_REQUEST_BYTES + 1]
