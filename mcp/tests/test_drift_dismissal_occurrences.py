"""R166: complete findings, durable acknowledgements and source-read proofs."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.kernel.primitives.drift_snapshot import drift_snapshot_path
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)
from agents_remember.memory_quality.reference_state import git_blob_id
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.models.knowledge_files.canonical import canonical_text
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
            inputs.ProjectionReaders(repo_surfaces=lambda _config, _now: ([], [])),
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
    """M3-L56-L168-CALLER-PINS-01: migrate the legacy pin to real converted production.

    Keep this case identity and every original fingerprint assertion. No retired
    writer/provider is aliased back: run_drift_summary reads actual converted files
    and publishes the snapshot through its retained production path.
    """
    code = tmp_path / "code"
    code.mkdir()
    run_git(code, ["init", "--initial-branch=main"])
    source = code / "one.py"
    source.write_text("print('one')\n")
    assert run_git(code, ["add", "one.py"]).returncode == 0
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
        temp_root=tmp_path / "temp",
    )
    (context.memory_root / "knowledge").mkdir(parents=True)
    context.onboarding_root.mkdir(parents=True)
    (context.memory_root / "knowledge/layout.json").write_text(
        canonical_text({"conversion": "1", "schema": "ar-memory-layout/v2"})
    )
    row = {
        "kind": "code",
        "anchor": {"blob": "0" * 40, "locator": {"kind": "file"}, "content": "sha256:" + "0" * 64},
    }
    document: dict[str, Any] = {
        "schema": "ar-onboarding-file/v1",
        "path": "one.py",
        "realizes": [],
        "references": {"1": {"targets": [row]}, "2": {"targets": [row]}},
    }
    sidecar = context.onboarding_root / "one.py.json"
    card = context.onboarding_root / "one.py.md"
    sidecar.write_text(canonical_text(document))
    card.write_text("# one.py\n\nSee [1] and [2].\n")
    path = drift_snapshot_path(tmp_path, repository="code", branch="main")
    result = run_drift_summary(code_repository_root=code, context=context, detail_limit=1)
    assert result.get("basis") == "converted-references"
    first = json.loads(path.read_text())
    assert first["counts"] == {"current": 0, "stale": 2, "violation": 0}
    # Reverse the map's incidental input order, keeping the same reference slots/targets.
    reordered = json.loads(canonical_text(document))
    reordered["references"] = dict(reversed(list(document["references"].items())))
    sidecar.write_text(json.dumps(reordered, indent=2) + "\n")
    run_drift_summary(code_repository_root=code, context=context, detail_limit=1)
    again = json.loads(path.read_text())
    assert first["actionableFingerprint"] == again["actionableFingerprint"]
    # Change the complete second finding beyond the unchanged first sampled row.
    document["references"]["3"] = document["references"].pop("2")
    sidecar.write_text(canonical_text(document))
    card.write_text("# one.py\n\nSee [1] and [3].\n")
    run_drift_summary(code_repository_root=code, context=context, detail_limit=1)
    changed = json.loads(path.read_text())
    assert first["counts"] == changed["counts"]
    assert first["rows"] == changed["rows"]
    assert first["actionableFingerprint"] != changed["actionableFingerprint"]
    document["references"] = {
        "1": {"targets": [row]},
        "2": {"targets": [row]},
        "4": {
            "targets": [
                {
                    "kind": "code",
                    "anchor": {
                        "blob": git_blob_id(source.read_bytes()),
                        "locator": {"kind": "file"},
                        "content": content_identity(source.read_bytes()),
                    },
                }
            ]
        },
    }
    sidecar.write_text(canonical_text(document))
    card.write_text("# one.py\n\nSee [1], [2] and [4].\n")
    run_drift_summary(code_repository_root=code, context=context, detail_limit=1)
    assert json.loads(path.read_text())["counts"] == {"current": 1, "stale": 2, "violation": 0}
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
