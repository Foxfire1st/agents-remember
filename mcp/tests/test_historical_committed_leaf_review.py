"""Recorded tree comparisons and legacy source JSON survive cleanup, restart and later landings.

The public route reads actual four-tree records. Legacy JSON knowledge remains unavailable while
its source identities and typed absence metadata stay readable, without reopening a canonical file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import apsw
import pytest
from agents_remember.application.knowledge_review import list_knowledge_review_entries
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    resolve_review_candidate,
)
from agents_remember.application.review_comparison_generation import (
    generation_directory,
)
from agents_remember.application.review_comparison_reopen import reopen_comparison_generation
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.serving.review import register_review_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_knowledge_review_comparison_generation import _assemble_legacy_manifest, _legacy_manifest
from test_knowledge_review_source_endpoints import _public_resolution, _source_params
from test_review_git_trees import CODE_FILE, LEAF, MASTER, REPO, World, build_world, commit, git

pytestmark = pytest.mark.evidence_unit
REVIEW_ROUTE = "/api/review/intent"
LATER_TASK_PATH = "pkg/a_later_task_landed.py"
LATER_TASK_TEXT = "# landed after this comparison was recorded\n"

_CHILD_SERVE = """
import json
import sys
from pathlib import Path

from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.review import register_review_routes
from fastapi import FastAPI
from fastapi.testclient import TestClient

descriptor = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
config = McpRuntimeConfig(
    workspace_root=Path(descriptor["workspace_root"]),
    coordination_root=Path(descriptor["coordination_root"]),
    config_path=Path(descriptor["config_path"]),
    transcript_root=Path(descriptor["transcript_root"]),
)
collaborators = serving_collaborators(config)
app = FastAPI()
register_review_routes(
    app,
    config,
    collaborators.knowledge_review,
    collaborators.knowledge_review_entries,
    collaborators.review_source_content,
)
with TestClient(app) as client:
    response = client.get(
        descriptor["route"], params=descriptor["params"], follow_redirects=True
    )
Path(sys.argv[2]).write_bytes(response.status_code.to_bytes(2, "big") + response.content)
print(f"child status {response.status_code}")
"""


@pytest.fixture
def closed_fixture(tmp_path: Path) -> World:
    world = build_world(tmp_path / "closed")
    world.edit()
    return world


def _serve(world: World, params: dict[str, str]) -> tuple[int, bytes]:
    collaborators = serving_collaborators(world.config)
    app = FastAPI()
    register_review_routes(
        app,
        world.config,
        collaborators.knowledge_review,
        collaborators.knowledge_review_entries,
        collaborators.review_source_content,
    )
    with TestClient(app) as client:
        response = client.get(REVIEW_ROUTE, params=params)
    return response.status_code, response.status_code.to_bytes(2, "big") + response.content


def _subject_params(history: str | None = None) -> dict[str, str]:
    params = _source_params(subject=True)
    if history is not None:
        params["history"] = history
    return params


def _body(payload: bytes) -> dict:
    return json.loads(payload[2:].decode("utf-8"))


def _close(world: World) -> None:
    # Cleanup removes the live group; it does not replace the enclosure's recorded path identities.
    shutil.rmtree(world.root / "group")


def _served_in_a_new_process(world: World, directory: Path, params: dict[str, str]) -> bytes:
    descriptor = directory / "restart.json"
    descriptor.write_text(
        json.dumps(
            {
                "workspace_root": str(world.config.workspace_root),
                "coordination_root": str(world.config.coordination_root),
                "config_path": str(world.config.config_path),
                "transcript_root": str(world.config.transcript_root),
                "route": REVIEW_ROUTE,
                "params": params,
            }
        )
    )
    target = directory / "restarted.bin"
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), environment.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_SERVE, str(descriptor), str(target)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0 and target.is_file(), (
        f"child serve exit {completed.returncode}, stdout {completed.stdout!r}, stderr {completed.stderr!r}"
    )
    return target.read_bytes()


def test_a_closed_leaf_serves_its_recorded_comparison_byte_for_byte(closed_fixture: World) -> None:
    world = closed_fixture
    resolved = _public_resolution(world)
    assert resolved.trees is not None
    record = resolved.trees.record
    live_status, live = _serve(world, _subject_params("recorded"))
    assert live_status == 200, live
    recorded = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF, recorded=True)
    current = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert (
        recorded.entries == current.entries
        and recorded.total_subjects == current.total_subjects > 0
    )
    inventory = _body(live)["payload"]["source"]["inventory"]
    assert inventory["before_code_tree_id"] == record.code_base.commit
    assert inventory["after_code_tree_id"] == record.code_candidate.tree
    _close(world)
    status, closed = _serve(world, _subject_params("recorded"))
    default_status, default = _serve(world, _subject_params())
    assert status == default_status == 200
    assert closed == live and default == live
    limitations = _body(closed)["payload"]["limitations"]
    assert f"review:trees:{record.number}" in limitations
    assert {"history:intent:before:available", "history:intent:after:available"} <= set(limitations)


def test_a_fresh_process_reconstructs_the_same_recorded_comparison(
    closed_fixture: World, tmp_path: Path
) -> None:
    world = closed_fixture
    resolved = _public_resolution(world)
    assert resolved.trees is not None
    live = _serve(world, _subject_params("recorded"))[1]
    _close(world)
    restarted = _served_in_a_new_process(world, tmp_path, _subject_params("recorded"))
    default = _served_in_a_new_process(world, tmp_path, _subject_params())
    assert restarted == live and default == live
    assert (
        f"review:trees:{resolved.trees.record.number}" in _body(restarted)["payload"]["limitations"]
    )


def test_a_later_task_does_not_contaminate_the_recorded_comparison(closed_fixture: World) -> None:
    world = closed_fixture
    _public_resolution(world)
    live = _serve(world, _subject_params("recorded"))[1]
    _close(world)
    assert _serve(world, _subject_params("recorded"))[1] == live
    landed = commit(world.code, {LATER_TASK_PATH: LATER_TASK_TEXT})
    status, after = _serve(world, _subject_params("recorded"))
    assert status == 200 and after == live
    payload = _body(after)["payload"]
    assert landed not in json.dumps(payload)
    assert LATER_TASK_PATH not in json.dumps(payload["source"]["inventory"])
    assert (
        payload["comparison"]["binding_digest"]
        == _body(live)["payload"]["comparison"]["binding_digest"]
    )


def test_a_pre_feature_leaf_exposes_its_recorded_source_range_and_its_absence(
    closed_fixture: World,
) -> None:
    world = closed_fixture
    landed = commit(world.code_worktree, {CODE_FILE: "# the leaf's landing\n"})
    # No comparison has been listed. The legacy memory half has no recorded content endpoint.
    world.contract(live=False, code_commit=landed)
    shutil.rmtree(world.root / "group")
    status, served = _serve(world, _source_params())
    assert status == 200, served
    payload = _body(served)["payload"]
    inventory = payload["source"]["inventory"]
    assert inventory["state"] == "measured" and inventory["listed_total"] > 0
    assert payload.get("comparison") is None and payload["staleness"]["state"] == "not_compared"
    assert payload["knowledge"]["selection_state"] == "task_context"
    assert "legacy-unavailable" in payload["knowledge"]["selection_detail"]
    assert payload["knowledge"]["before_statement"]["state"] == "unresolved"
    assert payload["knowledge"]["after_statement"]["state"] == "unresolved"
    assert payload["knowledge"]["before_statement"].get("text") is None
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.baseline_code_tree_id == world.code_base
    assert resolved.candidate_code_tree_id == git(world.code, "rev-parse", f"{landed}^{{tree}}")
    assert {state for _, state, _ in resolved.knowledge_unavailable} == {"legacy-unavailable"}
    refused = list_knowledge_review_entries(world.config, REPO, MASTER, LEAF)
    assert refused.state == "refused" and refused.refusal is not None
    assert (
        refused.refusal.code == "candidate_dataset_absent"
        and "legacy-unavailable" in refused.refusal.detail
    )


def test_a_generation_that_recorded_no_intent_half_states_that_absence_as_its_own_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = build_world(tmp_path)
    recorded = _legacy_manifest(world)
    payload = recorded.binding_payload()
    payload["knowledge"] = [
        {
            "side": side,
            "state": "not-selected",
            "identity": None,
            "artifact": None,
            "reason": "No intent half was selected for this historical comparison.",
        }
        for side in ("before", "after")
    ]
    manifest = _assemble_legacy_manifest(payload, recorded_at=recorded.recorded_at)
    shutil.rmtree(generation_directory(world.task_root, LEAF, recorded.generation_id))
    directory = generation_directory(world.task_root, LEAF, manifest.generation_id)
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(manifest.manifest_bytes())
    _close(world)

    def forbidden(*args, **kwargs):
        raise AssertionError("historical typed absence opened a canonical dataset")

    monkeypatch.setattr(apsw, "Connection", forbidden)
    reopened = reopen_comparison_generation(world.config, REPO, MASTER, LEAF)
    assert reopened.manifest == manifest and reopened.source is not None
    assert reopened.source.state == "available"
    assert {one.side: one.state for one in reopened.knowledge} == {
        "before": "not-selected",
        "after": "not-selected",
    }
    assert all(one.identity is None and one.path is None for one in reopened.knowledge)
    assert all("No intent half was selected" in one.detail for one in reopened.knowledge)
    assert reopened.state == "available"


def test_a_recorded_range_the_repository_cannot_resolve_is_unresolved_not_not_live(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    landed = commit(world.code_worktree, {CODE_FILE: "# landed then lost\n"})
    world.contract(live=False, code_commit=landed)
    git(world.code, "worktree", "remove", "--force", str(world.code_worktree))
    git(world.code, "update-ref", "-d", "refs/heads/leaf")
    git(world.code, "reflog", "expire", "--expire=now", "--all")
    git(world.code, "gc", "-q", "--prune=now")
    assert not _object_present(world.code, landed)
    status, served = _serve(world, _source_params())
    assert status == 404, served
    refusal = _body(served)["refusal"]
    assert refusal["code"] == "candidate_unresolved"
    assert "cannot resolve" in refusal["detail"] and landed in refusal["detail"]
    assert "restore the recorded commit" in refusal["next_action"]


def _object_present(repository: Path, object_id: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", object_id], cwd=repository, capture_output=True, check=False
        ).returncode
        == 0
    )


def test_expected_content_that_no_longer_resolves_is_unavailable_not_substituted(
    closed_fixture: World,
) -> None:
    world = closed_fixture
    resolved = _public_resolution(world)
    assert resolved.trees is not None
    record = resolved.trees.record
    world.contract(live=False)
    for root, side, worktree in (
        (world.memory, record.memory_candidate, world.memory_worktree),
        (world.code, record.code_candidate, world.code_worktree),
    ):
        assert side.ref is not None
        git(root, "update-ref", "-d", side.ref)
        git(root, "worktree", "remove", "--force", str(worktree))
        git(root, "reflog", "expire", "--expire=now", "--all")
        git(root, "gc", "-q", "--prune=now")
        assert not _object_present(root, side.tree)
    status, served = _serve(world, _source_params())
    assert status == 200, served
    payload = _body(served)["payload"]
    assert payload["source"]["inventory"]["state"] == "unavailable"
    assert "history:intent:after:unavailable-history" in payload["limitations"]
    assert (
        f"history:code:candidate:unavailable-history:{record.code_candidate.tree}"
        in payload["limitations"]
    )
    assert record.memory_candidate.tree in payload["knowledge"]["selection_detail"]
    assert payload["knowledge"]["after_statement"]["state"] == "unresolved"
    assert LATER_TASK_PATH not in json.dumps(payload)
    named_status, named = _serve(world, _subject_params())
    assert named_status == 404 and _body(named)["refusal"]["code"] == "candidate_dataset_absent"
    assert "unavailable-history" in _body(named)["refusal"]["detail"]


def test_a_closed_leaf_with_nothing_recorded_is_refused_by_name(closed_fixture: World) -> None:
    _close(closed_fixture)
    status, served = _serve(closed_fixture, _source_params())
    assert status == 404, served
    refusal = _body(served)["refusal"]
    assert refusal["code"] == "candidate_not_live"
    assert "records no comparison" in refusal["detail"]
    assert "record the leaf's landed commit" in refusal["next_action"]
