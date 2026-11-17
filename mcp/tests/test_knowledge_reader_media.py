"""Knowledge-reader media: SVG classification and option-shaped commit refusals.

The repository fixture comes from ``test_knowledge_reader``; this module adds one code commit with
an SVG asset and a memory commit that names that code commit, so the reader resolves media from a
real tree without growing the original module.
"""

from __future__ import annotations

import pytest
from agents_remember.application.knowledge_reader.media import read_knowledge_media
from agents_remember.serving.knowledge_reader import register_knowledge_bytes_route
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_knowledge_reader import (
    REPO,
    REVIEW_PATH,
    World,
    _client,
    _commit,
    world,  # noqa: F401  (pytest fixture dependency)
)

SVG_PATH = "dashboard/src/views/assets/logo.svg"


@pytest.fixture
def media_world(world: World) -> World:  # noqa: F811  (pytest injects the imported fixture)
    target = world.code / SVG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>\n',
        encoding="utf-8",
    )
    code_commit = _commit(world.code, "media")
    second = _commit(world.memory, f"media\n\nCode-Commit: {code_commit}")
    world.code_commit = code_commit
    world.second = second
    return world


def test_a_utf8_svg_is_a_picture_not_source_text(media_world: World) -> None:
    answer = media_world.read("code", commit=media_world.second, path=SVG_PATH)
    assert answer["state"] == "media"
    assert answer["mediaType"] == "image/svg+xml"
    assert answer["size"] > 0
    assert "text" not in answer


def test_an_option_shaped_knowledge_commit_is_a_400_not_a_git_argument(
    media_world: World,
) -> None:
    client = _client(media_world)
    for commit in ("--no-pager", "--output=/tmp/x", "-c", "--end-of-options"):
        response = client.get(
            "/api/knowledge/reader/path",
            params={"repo": REPO, "commit": commit, "path": REVIEW_PATH},
        )
        assert response.status_code == 400, commit
        assert response.json()["state"] == "invalid-request", commit


def test_the_knowledge_byte_route_refuses_an_option_shaped_commit(media_world: World) -> None:
    app = FastAPI()
    register_knowledge_bytes_route(
        app, lambda query: read_knowledge_media(media_world.config, query)
    )
    client = TestClient(app)
    for commit in ("--no-pager", "--output=/tmp/x", "-c"):
        response = client.get(
            "/api/knowledge/bytes",
            params={"repo": REPO, "commit": commit, "path": SVG_PATH},
        )
        assert response.status_code == 400, commit
        assert response.json()["state"] == "invalid-request", commit
