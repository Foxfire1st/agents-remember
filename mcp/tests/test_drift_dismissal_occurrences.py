"""R166: complete findings, durable acknowledgements and source-read proofs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import DriftRow
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    _write_drift_snapshot,
)
from agents_remember.observer.projection import Analytics, WorkspaceProjection
from agents_remember.observer.reducer import AnalyticalInputs, build_attention_queue
from agents_remember.serving import app as serving_app
from agents_remember.serving.actions import ActionEvaluationContext, evaluate_action
from agents_remember.serving.projections import drift_snapshots
from agents_remember.serving.projections import projection_inputs as inputs
from agents_remember.serving.projections.paths import observer_root
from agents_remember.serving.projections.snapshots import read_drift_snapshots
from agents_remember.serving.projections.snapshots_impl import _common

NOW = datetime(2026, 10, 15, 2, tzinfo=UTC)
STAMP = "2026-10-15T01:00:00+00:00"


@pytest.fixture
def drift_source(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    assert run_git(repository, ["init", "--initial-branch=main"]).returncode == 0
    assert (
        run_git(
            repository,
            [
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "base",
            ],
        ).returncode
        == 0
    )
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
        repositories={"repo": RepositoryScope(repo_id="repo", path=repository)},
    )
    path = drift_snapshot_path(tmp_path, repository="repo", branch="main")
    path.parent.mkdir(parents=True)
    store = AttentionDismissalStore(observer_root(config))
    item_id = "actionable-drift:repo:main"

    def write(fingerprint="first", *, count=2, stamp=STAMP):
        payload = {
            "schema": "ar-drift-snapshot/v1",
            "repository": "repo",
            "branch": "main",
            "checkedAt": stamp,
            "actionableCount": count,
            "counts": {"stale": count},
            "rows": [{"sample": "unchanged"}],
        }
        if fingerprint is not None:
            payload["actionableFingerprint"] = fingerprint
        path.write_text(json.dumps(payload))

    def refresh(state, domain=None):
        result = state.read(
            config,
            inputs.ProjectionReaders(repo_surfaces=lambda _config, _now: ([], [], [])),
            observer_root=store.root,
            pass_=inputs.RefreshPass(now=NOW, refresh=domain or inputs.ProjectionRefresh.full()),
        )
        return build_attention_queue(
            [],
            AnalyticalInputs(
                drift_snapshots=result.drift_snapshots,
                attention_dismissals=result.attention_dismissals,
            ),
        )

    return config, repository, path, store, item_id, write, refresh


def test_legacy_snapshot_fingerprint_uses_all_actionable_rows_and_no_clock(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    run_git(code, ["init", "--initial-branch=main"])
    assert (
        run_git(
            code,
            [
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "base",
            ],
        ).returncode
        == 0
    )
    context = SimpleNamespace(
        coordination_root=tmp_path,
        memory_root=tmp_path / "memory",
        onboarding_root=tmp_path / "memory/onboarding",
    )
    row = DriftRow(
        onboarding_file="one.md",
        source_file="one.py",
        repository="code",
        storage_mode="sidecar",
        last_verified_hash="abc",
        last_verified_date="2026-10-14",
        classification="drifted",
        trust="stale",
        affected_sections="all",
        note="changed",
    )
    rows = [row, replace(row, onboarding_file="two.md", source_file="two.py")]
    path = drift_snapshot_path(tmp_path, repository="code", branch="main")
    _write_drift_snapshot(code, context, rows)
    first = json.loads(path.read_text())
    _write_drift_snapshot(code, context, list(reversed(rows)))
    again = json.loads(path.read_text())
    assert first["actionableFingerprint"] == again["actionableFingerprint"]
    _write_drift_snapshot(
        code, context, [row, replace(rows[1], note="different actionable finding")]
    )
    changed = json.loads(path.read_text())
    assert first["counts"] == changed["counts"]
    assert first["actionableFingerprint"] != changed["actionableFingerprint"]
    _write_drift_snapshot(code, context, [*rows, replace(row, classification="up to date")])
    assert json.loads(path.read_text())["actionableFingerprint"] == first["actionableFingerprint"]


def test_drift_click_keeps_published_fingerprint_and_only_changed_findings_return(drift_source):
    _config, _repository, _path, store, item_id, write, refresh = drift_source
    write()
    queue = refresh(inputs.ProjectionInputState())
    outcome = evaluate_action(
        WorkspaceProjection(generatedAt=STAMP, analytics=Analytics(attentionQueue=queue)),
        "dismiss",
        None,
        ActionEvaluationContext(
            actor="developer", now=STAMP, item_id=item_id, kind="actionable-drift"
        ),
    )
    assert outcome.dismissal is not None
    assert outcome.dismissal.actionable_fingerprint == "first"
    store.dismiss(
        AttentionDismissalRecord(
            itemId=item_id,
            dismissedAt=STAMP,
            kind="actionable-drift",
            actionableFingerprint=outcome.dismissal.actionable_fingerprint,
        )
    )
    for number in range(10):
        write(stamp=f"2026-10-15T01:00:{number + 1:02d}+00:00")
        assert refresh(inputs.ProjectionInputState()) == []
    write("second")
    assert [row.id for row in refresh(inputs.ProjectionInputState())] == [item_id]
    assert store.current() == {}


def test_existing_drift_row_adopts_once_without_resurfacing_then_changes(drift_source):
    _config, _repository, _path, store, item_id, write, refresh = drift_source
    store.dismiss(
        AttentionDismissalRecord(itemId=item_id, dismissedAt=STAMP, kind="actionable-drift")
    )
    write(None)
    assert refresh(inputs.ProjectionInputState()) == []
    assert store.current()[item_id].actionableFingerprint is None
    write("first")
    assert refresh(inputs.ProjectionInputState()) == []
    assert store.current()[item_id].actionableFingerprint == "first"
    assert refresh(inputs.ProjectionInputState()) == []
    write(None)
    assert refresh(inputs.ProjectionInputState()) == []
    assert store.current()[item_id].actionableFingerprint == "first"
    write("second")
    assert [row.id for row in refresh(inputs.ProjectionInputState())] == [item_id]
    assert store.current() == {}


@pytest.mark.parametrize("fingerprint", [None, "first"])
@pytest.mark.parametrize(
    "failure",
    ["partial", "malformed", "branch-read", "directory-read", "reported-partial", "invalid-counts"],
)
def test_failed_or_partial_drift_pass_adopts_and_prunes_nothing(
    drift_source, monkeypatch, fingerprint, failure
):
    _config, _repository, path, store, item_id, write, refresh = drift_source
    store.dismiss(
        AttentionDismissalRecord(
            itemId=item_id,
            dismissedAt=STAMP,
            kind="actionable-drift",
            actionableFingerprint=fingerprint,
        )
    )
    state = inputs.ProjectionInputState()
    write(fingerprint)
    assert refresh(state) == []
    orphan_id = "actionable-drift:repo:already-gone"
    store.dismiss(
        AttentionDismissalRecord(itemId=orphan_id, dismissedAt=STAMP, kind="actionable-drift")
    )
    write("changed")
    domain = None
    if failure == "partial":
        domain = inputs.ProjectionRefresh.change(frozenset({inputs.ProjectionDomain.WORKSPACE}))
    elif failure == "malformed":
        path.write_text("{")
    elif failure == "branch-read":
        monkeypatch.setattr(
            drift_snapshots,
            "run_git",
            lambda *_args: (_ for _ in ()).throw(OSError("unreadable refs")),
        )
    elif failure == "directory-read":
        original = _common.os.scandir

        def scan(directory):
            if Path(directory) == path.parent:
                raise PermissionError("unreadable drift directory")
            return original(directory)

        monkeypatch.setattr(_common.os, "scandir", scan)
    else:
        payload = json.loads(path.read_text())
        if failure == "reported-partial":
            payload["actionableFingerprint"] = None
        else:
            payload["actionableCount"] = "unread"
        path.write_text(json.dumps(payload))
    before = store.log_path().read_bytes()
    expected = [item_id] if failure == "branch-read" and fingerprint == "first" else []
    assert [row.id for row in refresh(state, domain)] == expected
    assert store.log_path().read_bytes() == before
    assert store.current()[item_id].actionableFingerprint == fingerprint


@pytest.mark.parametrize("failure", ["malformed", "null-fingerprint", "branch-read"])
def test_incomplete_drift_census_still_publishes_valid_changed_occurrences(
    drift_source, monkeypatch, failure
):
    _config, _repository, path, store, item_id, write, refresh = drift_source
    write("first")
    state = inputs.ProjectionInputState()
    assert [row.id for row in refresh(state)] == [item_id]
    store.dismiss(
        AttentionDismissalRecord(
            itemId=item_id,
            dismissedAt=STAMP,
            kind="actionable-drift",
            actionableFingerprint="first",
        )
    )
    assert refresh(state) == []
    if failure == "branch-read":
        monkeypatch.setattr(
            drift_snapshots,
            "run_git",
            lambda *_args: (_ for _ in ()).throw(OSError("unreadable refs")),
        )
    else:
        other = path.with_name("unrelated.json")
        payload = json.loads(path.read_text())
        payload.update(repository="unrelated", actionableFingerprint=None)
        other.write_text("{" if failure == "malformed" else json.dumps(payload))
    write("changed")
    before = store.log_path().read_bytes()
    visible = {item_id}
    if failure == "null-fingerprint":
        visible.add("actionable-drift:unrelated:main")
    assert {row.id for row in refresh(state)} == visible
    assert store.log_path().read_bytes() == before
    assert store.current()[item_id].actionableFingerprint == "first"
    # A dashboard starting on the same partial inputs also sees the good item.
    assert {row.id for row in refresh(inputs.ProjectionInputState())} == visible


def test_healthy_drift_and_deleted_branch_end_only_their_occurrences(drift_source):
    config, repository, path, store, item_id, write, refresh = drift_source
    write()
    store.dismiss(
        AttentionDismissalRecord(
            itemId=item_id,
            dismissedAt=STAMP,
            kind="actionable-drift",
            actionableFingerprint="first",
        )
    )
    write("empty", count=0)
    assert refresh(inputs.ProjectionInputState()) == []
    assert store.current() == {}
    write("first")
    assert [row.id for row in refresh(inputs.ProjectionInputState())] == [item_id]
    assert run_git(repository, ["branch", "temporary"]).returncode == 0
    branch_id = "actionable-drift:repo:temporary"
    store.dismiss(
        AttentionDismissalRecord(itemId=branch_id, dismissedAt=STAMP, kind="actionable-drift")
    )
    # No snapshot does not mean the branch is gone.
    refresh(inputs.ProjectionInputState())
    assert branch_id in store.current()
    orphan_snapshot = path.with_name("temporary.json")
    payload = json.loads(path.read_text())
    payload["branch"] = "temporary"
    orphan_snapshot.write_text(json.dumps(payload))
    assert run_git(repository, ["branch", "-D", "temporary"]).returncode == 0
    assert [row.id for row in refresh(inputs.ProjectionInputState())] == [item_id]
    assert branch_id not in store.current()
    assert not orphan_snapshot.exists()
    assert path.exists()
    assert read_drift_snapshots(config.coordination_root, now=NOW)


def test_drift_actual_http_click_restart_and_new_occurrence(drift_source, monkeypatch):
    config, _repository, _path, store, item_id, write, _refresh = drift_source
    write()
    runtimes = []
    original = serving_app._build_serving_runtime

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        runtimes.append(result[0])
        return result

    monkeypatch.setattr(serving_app, "_build_serving_runtime", capture)

    def make_app():
        app = serving_app.create_app(
            config,
            live_inputs=serving_app.LiveProjectionInputs(
                provider_state=False, landing_state=False, change_watch=False
            ),
        )
        return app, runtimes[-1]

    async def exercise():
        app, runtime = make_app()
        await runtime.projector.prime()
        body = {"itemId": item_id, "kind": "actionable-drift"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.post("/api/actions/dismiss", json=body)).status_code == 202
            assert store.current()[item_id].actionableFingerprint == "first"
            for number in range(10):
                write(stamp=f"2026-10-15T02:00:{number:02d}+00:00")
                await runtime.projector.prime()
                assert (await client.get("/api/state")).json()["analytics"]["attentionQueue"] == []
        restarted, next_runtime = make_app()
        await next_runtime.projector.prime()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://test"
        ) as client:
            assert (await client.get("/api/state")).json()["analytics"]["attentionQueue"] == []
            write("changed")
            await next_runtime.projector.prime()
            queue = (await client.get("/api/state")).json()["analytics"]["attentionQueue"]
            assert [row["id"] for row in queue] == [item_id]
            assert store.current() == {}

    asyncio.run(exercise())


def test_native_branch_census_bounds_ten_thousand_sessions_and_one_thousand_worktrees(drift_source):
    _config, repository, _path, store, _item_id, _write, refresh = drift_source
    commit = run_git(repository, ["rev-parse", "HEAD"]).stdout.strip()
    references = [f"refs/heads/worktree-{i}" for i in range(1000)]
    created = run_git(
        repository,
        ["update-ref", "--stdin"],
        GitRunnerOptions(input_text="".join(f"create {ref} {commit}\n" for ref in references)),
    )
    assert created.returncode == 0
    store.log_path().parent.mkdir(parents=True, exist_ok=True)
    old = [
        json.dumps(
            {
                "schema": "ar-attention-dismissal/v1",
                "itemId": f"stale-session:{i}",
                "kind": "stale-session",
                "dismissedAt": STAMP,
                "lifecycleId": f"session-{i}",
            }
        )
        for i in range(10000)
    ]
    drift = [
        AttentionDismissalRecord(
            itemId=f"actionable-drift:repo:worktree-{i}", kind="actionable-drift", dismissedAt=STAMP
        ).model_dump_json(by_alias=True)
        for i in range(1000)
    ]
    store.log_path().write_text("\n".join([*old, *drift]) + "\n")
    removed = run_git(
        repository,
        ["update-ref", "--stdin"],
        GitRunnerOptions(input_text="".join(f"delete {ref}\n" for ref in references[:-3])),
    )
    assert removed.returncode == 0
    state = inputs.ProjectionInputState()
    assert refresh(state) == []
    expected = {f"actionable-drift:repo:worktree-{i}" for i in range(997, 1000)}
    assert set(store.current()) == expected
    assert len(store.log_path().read_text().splitlines()) == 3
    after = store.log_path().stat().st_mtime_ns
    assert refresh(state) == []
    assert set(store.current()) == expected
    assert store.log_path().stat().st_mtime_ns == after
