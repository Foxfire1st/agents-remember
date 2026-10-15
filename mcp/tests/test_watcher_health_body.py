"""L81 F10: the actual served body matches the optional watcher-health mirror."""

from pathlib import Path
from unittest import mock

from agents_remember.serving.app import LiveProjectionInputs, create_app
from agents_remember.serving.projector import Projector
from fastapi.testclient import TestClient
from test_serving import _config, _projection


def test_served_body_omits_down_since_only_while_the_watcher_runs(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path), live_inputs=LiveProjectionInputs(change_watch=False))
    with (
        mock.patch(
            "agents_remember.serving.projector.project_and_write", return_value=_projection()
        ),
        TestClient(app) as client,
    ):
        etags = []
        for input_state, expected in (
            ({"running": True, "downSince": None}, {"running": True}),
            ({"running": False, "downSince": 1234.5}, {"running": False, "downSince": 1234.5}),
        ):
            with mock.patch.object(Projector, "watcher_health", return_value=input_state):
                response = client.get("/api/state")
            assert response.status_code == 200
            assert response.json()["watcherHealth"] == expected
            etags.append(response.headers["etag"])
        assert etags[0] == etags[1], "serve-time health does not change the core projection ETag"
