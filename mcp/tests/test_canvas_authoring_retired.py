"""MIK-R106 v2 rule 1: the retired authoring POSTs refuse by name, and the saved page keeps opening.

The dashboard app mounts the explainer (the leaf's page and conversation) and, for history, the old
canvas frame and page *reads*. The old authoring POSTs are retired: a Generate that still ran would be
a second authoring route, so each answers a typed refusal, starts nothing, opens no session and writes
nothing — and the refusal is registered before the canvas routes so it is what matches.

The read contract is judged by what its consumer does, not by the HTTP verb alone: a developer who
saved a page must still be able to reopen it, read its assets, and read its frame state, so those three
GETs are pinned here as surviving the refusal.
"""

from __future__ import annotations

from pathlib import Path

import canvas_host_test_support as host_support
from agents_remember.canvas import kit
from agents_remember.serving.app import (
    REPLACED_AUTHORING_PATHS,
    create_app,
    refuse_replaced_canvas_authoring,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

FRAME = "/api/canvas/frames/r/t/l/f"
PAGE = "/api/canvas/frames/page"
ORIGIN = "http://127.0.0.1:8765"


def _app_with_a_placeholder_canvas() -> FastAPI:
    """The refusals first, then stand-ins for the canvas routes registered behind them."""

    app = FastAPI()
    refuse_replaced_canvas_authoring(app)

    @app.get(FRAME + "/generate")
    def _a_read_that_shares_a_name() -> dict[str, str]:
        return {"state": "the read answered"}

    @app.get("/api/canvas/frames/{repository_id}/{master}/{leaf_id}/{frame_id}")
    def _frame_state() -> dict[str, str]:
        return {"state": "frame state served"}

    @app.get(PAGE + "/{session}/")
    def _page() -> Response:
        return Response("<html>the saved page</html>", media_type="text/html")

    @app.get(PAGE + "/{session}/{asset_path:path}")
    def _page_asset(asset_path: str) -> Response:
        return Response(f"asset:{asset_path}", media_type="text/plain")

    @app.post("/api/canvas/frames/{repository_id}/{master}/{leaf_id}/{frame_id}/generate")
    def _old_generate() -> dict[str, str]:  # pragma: no cover - it must never be reached
        return {"state": "the old route ran"}

    return app


def _concrete(path: str) -> str:
    return (
        path.replace("{repository_id}", "r")
        .replace("{master}", "t")
        .replace("{leaf_id}", "l")
        .replace("{frame_id}", "f")
        .replace("{attempt_id}", "a1")
        .replace("{operation}", "publish")
        .replace("{session}", "s1")
    )


def test_every_retired_authoring_post_refuses_by_name() -> None:
    client = TestClient(_app_with_a_placeholder_canvas())
    for path in REPLACED_AUTHORING_PATHS:
        answer = client.post(_concrete(path), json={})
        assert answer.status_code == 409, path
        payload = answer.json()
        assert payload["reason"] == "canvas-authoring-replaced", path
        assert "explainer" in payload["detail"], path
        assert payload["state"] == "refused", path


def test_the_saved_page_and_its_reads_still_serve() -> None:
    """Rule 1's other half: refusing authoring is not losing a page already saved."""

    client = TestClient(_app_with_a_placeholder_canvas())
    assert client.get(FRAME).json() == {"state": "frame state served"}
    assert client.get(FRAME + "/generate").json() == {"state": "the read answered"}
    page = client.get(PAGE + "/s1/")
    assert page.status_code == 200 and "the saved page" in page.text
    # A single asset segment, which is the shape the frame-state route cannot capture. The nested
    # shape is a pre-existing shadowing in serving/canvas.py (frame state at :824 is registered before
    # the page asset route at :857), recorded as an observation rather than changed under this finding.
    asset = client.get(PAGE + "/s1/index.html")
    assert asset.status_code == 200 and asset.text == "asset:index.html"


def test_a_saved_pages_assets_keep_their_path_conversion() -> None:
    """Without the frame-state route in the way, the refusal must not shadow an asset conversion."""

    app = FastAPI()
    refuse_replaced_canvas_authoring(app)
    seen: list[str] = []

    @app.get(PAGE + "/{session}/{asset_path:path}")
    def _asset(request: Request, session: str, asset_path: str) -> JSONResponse:
        seen.append(f"{session}:{asset_path}")
        return JSONResponse({"asset": asset_path})

    client = TestClient(app)
    assert client.get(PAGE + "/s9/deep/nested/thing.css").json() == {
        "asset": "deep/nested/thing.css"
    }
    assert seen == ["s9:deep/nested/thing.css"]


def _seed_two_revisions(tmp_path: Path, *, frame: str = "explainer"):
    settings = host_support.config(tmp_path)
    task_root = host_support.task(settings)
    comparison = host_support.record(7)
    r1_assets = dict(host_support.ASSETS)
    r1_assets["index.html"] = b"<!doctype html><title>rev-1</title><p>rev-1</p>"
    r1_assets["deep/nested/thing.css"] = b"body { color: #111; }"
    r2_assets = dict(host_support.ASSETS)
    r2_assets["index.html"] = b"<!doctype html><title>rev-2</title><p>rev-2</p>"
    rev1 = host_support.publish(
        task_root,
        comparison,
        request="req-1",
        page=host_support.PageContent(assets=r1_assets),
        frame=frame,
    )
    rev2 = host_support.publish(
        task_root,
        comparison,
        request="req-2",
        page=host_support.PageContent(assets=r2_assets),
        frame=frame,
    )
    assert (rev1, rev2) == (1, 2)
    return settings


def test_real_create_app_serves_saved_frame_revisions_nested_assets_and_bridge_messages(
    tmp_path: Path,
) -> None:
    settings = _seed_two_revisions(tmp_path, frame="review-page")
    client = TestClient(create_app(settings), base_url=ORIGIN)
    headers = {"Origin": ORIGIN}

    target = f"/api/canvas/frames/{host_support.REPO}/{host_support.TASK}/{host_support.LEAF}/review-page"
    state_before = client.get(target)
    body = state_before.json()
    assert body["state"] == "ok"
    assert body["opening"]["state"] == "ready"
    assert body["opening"]["revision"] == 2

    for path in REPLACED_AUTHORING_PATHS:
        refused = client.post(_concrete(path), json={}, headers=headers)
        assert refused.status_code == 409, path
        assert refused.json()["reason"] == "canvas-authoring-replaced", path

    # Zero starts and zero effects: the refusal wrote nothing, opened no session, moved nothing.
    state_after = client.get(target)
    assert state_after.status_code == state_before.status_code
    assert state_after.json() == state_before.json()

    r1_session = client.post(f"{target}/sessions", json={"revision": 1}, headers=headers).json()
    r2_session = client.post(
        f"{target}/sessions", json={"revision": 2, "slot": "compare"}, headers=headers
    ).json()
    assert (r1_session["revision"], r2_session["revision"], r2_session["displaced"]) == (1, 2, [])
    assert "rev-1" in client.get(r1_session["pageUrl"]).text
    assert "rev-2" in client.get(r2_session["pageUrl"]).text
    assert "color: #111" in client.get(r1_session["pageUrl"] + "deep/nested/thing.css").text

    offered = client.post(
        f"/api/canvas/sessions/{r1_session['session']}/messages",
        json={
            "schema": "ar-canvas-message/v1",
            "protocolVersion": 1,
            "type": "offer-selections",
            "requestId": "req-sel-1",
            "session": r1_session["session"],
            "revision": 1,
            "comparison": r1_session["comparison"],
            "payload": {"selections": [{"question": "Approach", "choice": "Option A"}]},
        },
        headers=headers,
    ).json()
    assert (offered["state"], offered["operation"]) == ("accepted", "offer-selections")

    explained = client.post(
        f"/api/canvas/sessions/{r1_session['session']}/messages",
        json={
            "schema": "ar-canvas-message/v1",
            "protocolVersion": 1,
            "type": "request-explanation",
            "requestId": "req-exp-1",
            "session": r1_session["session"],
            "revision": 1,
            "comparison": r1_session["comparison"],
            "payload": {"question": "Why does this hold?"},
        },
        headers=headers,
    ).json()
    assert (explained["state"], explained["operation"]) == ("recorded", "request-explanation")
    helper_js = kit.kit_protocol_helper_js()
    assert "offerSelections" in helper_js and "requestExplanation" in helper_js


def test_a_missing_frame_is_refused_by_name_and_never_swapped(tmp_path: Path) -> None:
    """B7: a frame folder the request named is not answered with another frame's bytes."""

    settings = _seed_two_revisions(tmp_path, frame="explainer")
    client = TestClient(create_app(settings), base_url=ORIGIN)
    target = f"/api/canvas/frames/{host_support.REPO}/{host_support.TASK}/{host_support.LEAF}/review-page"
    answer = client.get(target)
    assert answer.status_code == 404
    assert answer.json()["reason"] == "frame-unavailable"
    assert "review-page" in answer.json()["detail"]
