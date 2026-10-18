from __future__ import annotations

import asyncio
import json
import os
import shutil
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
    AttentionObservation,
)
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    ProviderScope,
    RepositoryScope,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection import (
    ActionAvailability,
    Analytics,
    DriftSnapshotNode,
    EnclosureNode,
    ProviderNode,
    SetupProgressNode,
    TaskEntry,
    WorkspaceProjection,
)
from agents_remember.observer.reducer import (
    AnalyticalInputs,
    build_attention_queue,
)
from agents_remember.providers.current_state import current_state_path
from agents_remember.providers.setup_progress import PROGRESS_SCHEMA
from agents_remember.serving import app as serving_app
from agents_remember.serving.actions import ActionEvaluationContext, evaluate_action
from agents_remember.serving.projections import projection_inputs as inputs
from agents_remember.serving.projections import snapshots as provider_snapshots
from agents_remember.serving.projections.paths import observer_root
from agents_remember.worktrees.start_progress import START_PROGRESS_SCHEMA


class AttentionDismissalTests(unittest.TestCase):
    """An acknowledgement survives clocks; different actionable findings recur."""

    DISMISS_TS = "2026-06-13T18:00:10+00:00"  # after T0

    def _dismissal(
        self,
        item_id: str,
        *,
        kind: str = "actionable-drift",
        dismissed_at: str | None = None,
    ) -> dict[str, AttentionDismissalRecord]:
        return {
            item_id: AttentionDismissalRecord(
                itemId=item_id,
                kind=kind,
                dismissedAt=dismissed_at or self.DISMISS_TS,
                actionableFingerprint="first",
            )
        }

    def test_dismiss_suppresses_actionable_drift_until_findings_change(self) -> None:
        dismissed = self._dismissal(
            "actionable-drift:repo-a:main",
            kind="actionable-drift",
            dismissed_at="2026-06-13T18:00:10+00:00",
        )
        old_snapshot = DriftSnapshotNode(
            repository="repo-a",
            branch="main",
            actionableCount=1,
            checkedAt="2026-06-13T18:00:00+00:00",
            actionableFingerprint="first",
        )
        self.assertEqual(
            build_attention_queue(
                [],
                AnalyticalInputs(
                    drift_snapshots=[old_snapshot],
                    setup_progress=[],
                    attention_dismissals=dismissed,
                ),
            ),
            [],
        )

        newer_snapshot = DriftSnapshotNode(
            repository="repo-a",
            branch="main",
            actionableCount=1,
            checkedAt="2026-06-13T18:00:11+00:00",
            actionableFingerprint="first",
        )
        queue = build_attention_queue(
            [],
            AnalyticalInputs(
                drift_snapshots=[newer_snapshot], setup_progress=[], attention_dismissals=dismissed
            ),
        )
        self.assertEqual(queue, [])
        changed_snapshot = newer_snapshot.model_copy(update={"actionableFingerprint": "changed"})
        queue = build_attention_queue(
            [], AnalyticalInputs(drift_snapshots=[changed_snapshot], attention_dismissals=dismissed)
        )
        self.assertEqual([item.kind for item in queue], ["actionable-drift"])


# R166: every remaining item is dismissible, independently of a removed session.


def remaining_attention_sources(kind: str) -> tuple[list[ProviderNode], AnalyticalInputs]:
    providers = (
        [
            ProviderNode(
                id="cgc",
                state="failed",
                ok=False,
                watcherUp=False,
                indexingState="unknown",
                scope="workspace",
            )
        ]
        if kind == "provider-down"
        else []
    )
    setup = [SetupProgressNode(group="group", state="failed")] if kind == "failed-setup" else []
    starts = (
        [{"worktreeGroup": "/worktrees/group", "blockedReason": "needs a base"}]
        if kind == "blocked-start"
        else []
    )
    return providers, AnalyticalInputs(setup_progress=setup, engine_start_progress=starts)


def test_attention_ranking_keeps_alarm_above_warning_and_then_wait_and_id() -> None:
    providers, given = remaining_attention_sources("provider-down")
    providers = [
        providers[0].model_copy(update={"id": name, "snapshotStaleSeconds": age})
        for name, age in (("z", 10), ("b", 20), ("a", 20))
    ]
    given = replace(
        given,
        drift_snapshots=[
            DriftSnapshotNode(
                repository="repo",
                branch="main",
                actionableCount=1,
                checkedAt="2026-06-13T18:00:00+00:00",
                actionableFingerprint="finding",
            )
        ],
    )
    queue = build_attention_queue(providers, given)
    assert [item.id for item in queue] == [
        "provider-down:a",
        "provider-down:b",
        "provider-down:z",
        "actionable-drift:repo:main",
    ]


@pytest.mark.parametrize("kind", ["provider-down", "failed-setup", "blocked-start"])
def test_remaining_kind_click_is_accepted_without_a_session(kind: str) -> None:
    providers, given = remaining_attention_sources(kind)
    queue = build_attention_queue(providers, given)
    assert len(queue) == 1
    item = queue[0]
    projection = WorkspaceProjection(
        generatedAt="2026-10-15T01:00:00+00:00", analytics=Analytics(attentionQueue=queue)
    )
    result = evaluate_action(
        projection,
        "dismiss",
        None,
        ActionEvaluationContext(
            actor="developer", now="2026-10-15T01:00:00+00:00", item_id=item.id, kind=kind
        ),
    )
    assert result.status_code == 202
    assert result.dismissal and result.dismissal.kind == kind


@pytest.mark.parametrize("kind", ["provider-down", "failed-setup", "blocked-start"])
def test_remaining_kind_dismissal_survives_unchanged_passes(kind: str) -> None:
    providers, given = remaining_attention_sources(kind)
    item = build_attention_queue(providers, given)[0]
    given = replace(
        given,
        attention_dismissals={
            item.id: AttentionDismissalRecord(
                itemId=item.id, kind=kind, dismissedAt="2026-10-15T01:00:00+00:00"
            )
        },
    )
    for _ in range(10):
        assert build_attention_queue(providers, given) == []


def test_precomputed_action_remains_available_on_a_task_bound_worktree() -> None:

    worktree = EnclosureNode(
        enclosure="/contracts/leaf",
        taskId="master",
        taskName="master",
        repoName="repo",
        lifecycleId="binding",
        worktreeGroup="/worktrees/leaf",
        humanReviewStatus="approved",
        closeoutStatus="completed",
        integrationStatus="not-started",
        cleanup="pending",
        codeWorktreeExists=True,
        memoryWorktreeExists=True,
        actions=[
            ActionAvailability(
                action="integrate", enabled=False, disabledReason="independent review required"
            )
        ],
    )
    task = TaskEntry(
        key="repo/master/leaf.json",
        reference=TaskDocumentRef(repository="repo", path="master/leaf.json"),
        id="L1",
        title="Leaf",
        stage="started",
        activity="paused",
        worktrees=[worktree],
    )
    projection = WorkspaceProjection(generatedAt="2026-10-15T01:00:00+00:00", tasks=[task])
    result = evaluate_action(
        projection,
        "integrate",
        worktree.enclosure,
        ActionEvaluationContext(actor="developer", now=projection.generatedAt),
    )
    assert result.status_code == 409
    assert result.body["detail"] == "independent review required"


@pytest.fixture
def dismissal_sources(tmp_path, monkeypatch):

    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
        providers={
            "cgc": ProviderScope(
                provider_id="cgc",
                runtime_root=tmp_path / "runtime",
                log_root=tmp_path / "logs",
                instance_id="test",
                scope="workspace",
            )
        },
    )
    group = tmp_path / "worktrees" / "repo" / "group"
    group.mkdir(parents=True)
    monkeypatch.setattr(inputs, "admitted_worktree_groups", lambda _enclosures: {"group"})
    paths = {
        "provider-down": current_state_path(config),
        "failed-setup": group / "provider-runtime" / "setup-progress.json",
        "blocked-start": tmp_path / "temp" / "worktree-start" / "repo" / "group.json",
    }

    def write(kind, *, healthy=False, stamp="2026-10-15T01:00:00+00:00"):
        payload = {
            "provider-down": {
                "checkedAt": stamp,
                "providers": {
                    "cgc": {"id": "cgc", "state": "running" if healthy else "failed", "ok": healthy}
                },
            },
            "failed-setup": {
                "schema": PROGRESS_SCHEMA,
                "state": "completed" if healthy else "failed",
                "completedPhases": [],
                "updatedAt": stamp,
            },
            "blocked-start": {
                "schema": START_PROGRESS_SCHEMA,
                "worktreeGroup": str(group),
                "blockedReason": "" if healthy else "needs a base",
                "updatedAt": stamp,
            },
        }[kind]
        paths[kind].parent.mkdir(parents=True, exist_ok=True)
        paths[kind].write_text(json.dumps(payload))

    readers = inputs.ProjectionReaders(repo_surfaces=lambda _config, _now: ([], []))
    store = AttentionDismissalStore(observer_root(config))

    def refresh(state, refresh=None):
        result = state.read(
            config,
            readers,
            observer_root=store.root,
            pass_=inputs.RefreshPass(
                now=datetime(2026, 10, 15, 2, tzinfo=UTC),
                refresh=refresh or inputs.ProjectionRefresh.full(),
            ),
        )
        return build_attention_queue(
            result.providers,
            AnalyticalInputs(
                setup_progress=result.setup_progress,
                engine_start_progress=result.engine_start_progress,
                attention_dismissals=result.attention_dismissals,
            ),
        )

    return inputs, paths, group, write, store, refresh, config


@pytest.mark.parametrize("repo_id", ["repo", "MyRepo"])
def test_per_repo_provider_dismissal_needs_explicit_recovery_or_configuration_removal(
    dismissal_sources,
    repo_id,
):
    inputs, paths, _group, write, store, refresh, config = dismissal_sources
    config.repositories[repo_id] = RepositoryScope(
        repo_id=repo_id, path=config.coordination_root / "repo"
    )
    provider_id = "codegraphcontext-code"
    config.providers[provider_id] = replace(config.providers.pop("cgc"), provider_id=provider_id)
    path = paths["provider-down"]

    def status(healthy=False, *, per_repo=True):
        write("provider-down", healthy=healthy)
        payload = json.loads(path.read_text())
        provider = payload["providers"].pop("cgc")
        provider["id"] = provider_id
        if per_repo:
            provider["resources"] = {
                "watchers": {
                    repo_id.lower(): {
                        "repoId": repo_id.lower(),
                        "ok": healthy,
                        "watcherUp": healthy,
                    }
                }
            }
        payload["providers"][provider_id] = provider
        path.write_text(json.dumps(payload))

    status()
    state = inputs.ProjectionInputState()
    item = refresh(state)[0]
    assert item.id == f"provider-down:{provider_id}:{repo_id.lower()}"
    record = AttentionDismissalRecord(
        itemId=item.id, kind=item.kind, dismissedAt="2026-10-15T01:10:00Z"
    )
    store.dismiss(record)
    status(healthy=True, per_repo=False)
    assert refresh(state) == []
    assert item.id in store.current()
    status()
    assert refresh(state) == []
    status(healthy=True)
    assert refresh(state) == []
    assert item.id not in store.current()
    status()
    assert [row.id for row in refresh(state)] == [item.id]
    store.dismiss(record)
    config.repositories.clear()
    assert refresh(state) == []
    assert item.id not in store.current()


@pytest.mark.parametrize("kind", ["provider-down", "failed-setup", "blocked-start"])
def test_observed_recovery_ends_dismissal_and_only_a_later_failure_returns(dismissal_sources, kind):
    inputs, _paths, _group, write, store, refresh, _config = dismissal_sources
    write(kind)
    state = inputs.ProjectionInputState()
    item = refresh(state)[0]
    store.dismiss(
        AttentionDismissalRecord(itemId=item.id, kind=kind, dismissedAt="2026-10-15T01:10:00+00:00")
    )
    for index in range(10):
        write(kind, stamp=f"2026-10-15T01:{20 + index}:00+00:00")
        assert refresh(state) == []
    # A new reader instance is the server restart; no occurrence is inferred from it.
    assert refresh(inputs.ProjectionInputState()) == []
    write(kind, healthy=True)
    assert refresh(state) == []
    assert store.current() == {}
    write(kind)
    assert [row.id for row in refresh(state)] == [item.id]


@pytest.mark.parametrize("kind", ["provider-down", "failed-setup", "blocked-start"])
def test_partial_or_failed_source_keeps_dismissal_but_a_complete_gone_read_reclaims(
    dismissal_sources, kind
):
    inputs, paths, group, write, store, refresh, config = dismissal_sources
    write(kind)
    state = inputs.ProjectionInputState()
    item = refresh(state)[0]
    store.dismiss(
        AttentionDismissalRecord(itemId=item.id, kind=kind, dismissedAt="2026-10-15T01:10:00+00:00")
    )
    paths[kind].write_text("{broken")
    assert refresh(state) == []
    assert item.id in store.current()
    assert (
        refresh(
            state,
            inputs.ProjectionRefresh.change(frozenset({inputs.ProjectionDomain.HOST_ACTIVITY})),
        )
        == []
    )
    assert item.id in store.current()
    paths[kind].unlink()
    if kind == "provider-down":
        config.providers.clear()
    if kind == "failed-setup":
        shutil.rmtree(group)
    assert refresh(state) == []
    assert store.current() == {}


@pytest.mark.parametrize("kind", ["provider-down", "failed-setup"])
def test_unknown_source_state_does_not_reset_an_acknowledgement(dismissal_sources, kind):
    inputs, paths, _group, write, store, refresh, _config = dismissal_sources
    write(kind)
    state = inputs.ProjectionInputState()
    item = refresh(state)[0]
    store.dismiss(
        AttentionDismissalRecord(itemId=item.id, kind=kind, dismissedAt="2026-10-15T01:10:00Z")
    )
    payload = json.loads(paths[kind].read_text())
    if kind == "provider-down":
        payload["providers"]["cgc"].update(state="unknown", ok=None)
    else:
        payload["state"] = "unknown"
    paths[kind].write_text(json.dumps(payload))
    assert refresh(state) == []
    assert item.id in store.current()
    write(kind)
    assert refresh(state) == []


def test_directory_read_failure_keeps_the_kinds_acknowledgements(dismissal_sources, monkeypatch):
    inputs, paths, _group, write, store, refresh, _config = dismissal_sources
    write("blocked-start")
    state = inputs.ProjectionInputState()
    item = refresh(state)[0]
    store.dismiss(
        AttentionDismissalRecord(itemId=item.id, kind=item.kind, dismissedAt="2026-10-15T01:10:00Z")
    )
    root = paths[item.kind].parent.parent
    original = os.scandir

    def unreadable(path):
        if Path(path) == root:
            raise PermissionError("source listing refused")
        return original(path)

    monkeypatch.setattr(os, "scandir", unreadable)
    assert refresh(state) == []
    assert item.id in store.current()


def test_no_change_does_not_write_and_failed_cleanup_retains_the_read_map(tmp_path, monkeypatch):
    store = AttentionDismissalStore(tmp_path)
    record = AttentionDismissalRecord(
        itemId="provider-down:cgc", kind="provider-down", dismissedAt="2026-10-15T01:00:00Z"
    )
    store.dismiss(record)
    observation = AttentionObservation(
        "provider-down", datetime(2026, 10, 15, 2, tzinfo=UTC), existing={record.itemId}
    )
    original_bytes = store.log_path().read_bytes()
    stat_before = store.log_path().stat()

    def unwritable(_records):
        raise PermissionError("read-only acknowledgement store")

    monkeypatch.setattr(store, "_replace", unwritable)
    assert store.reconcile([observation]) == {record.itemId: record}
    assert store.log_path().stat().st_mtime_ns == stat_before.st_mtime_ns
    observation.healthy.add(record.itemId)
    assert store.reconcile([observation]) == {record.itemId: record}
    assert store.log_path().read_bytes() == original_bytes


def test_cleanup_does_not_remove_a_click_newer_than_its_read(tmp_path):
    store = AttentionDismissalStore(tmp_path)
    record = AttentionDismissalRecord(
        itemId="provider-down:cgc", kind="provider-down", dismissedAt="2026-10-15T02:00:01Z"
    )
    store.dismiss(record)
    before_click = AttentionObservation("provider-down", datetime(2026, 10, 15, 2, tzinfo=UTC))
    assert store.reconcile([before_click]) == {record.itemId: record}
    assert (
        store.reconcile(
            [replace(before_click, observed_before=datetime(2026, 10, 15, 2, 0, 2, tzinfo=UTC))]
        )
        == {}
    )


@pytest.mark.parametrize("history_size", [100, 1000])
def test_long_history_reclaims_to_the_current_things_and_compacts_bad_rows(tmp_path, history_size):
    store = AttentionDismissalStore(tmp_path)
    store.log_path().parent.mkdir(parents=True)
    # Removed session items cannot be published or parsed as current kinds.
    retired = [
        json.dumps(
            {
                "schema": "ar-attention-dismissal/v1",
                "itemId": f"stale-session:{i}",
                "kind": "stale-session",
                "lifecycleId": f"old-{i}",
                "dismissedAt": "2026-10-15T01:00:00Z",
            }
        )
        for i in range(history_size * 10)
    ]
    records = [
        AttentionDismissalRecord(
            itemId=f"failed-setup:group-{i}",
            kind="failed-setup",
            dismissedAt="2026-10-15T01:00:00Z",
        )
        for i in range(history_size)
    ]
    store.log_path().write_text(
        "\n".join(retired + [row.model_dump_json(by_alias=True) for row in records] + ["{torn"])
        + "\n"
    )
    live = {row.itemId for row in records[-3:]}
    observation = AttentionObservation(
        "failed-setup", datetime(2026, 10, 15, 2, tzinfo=UTC), existing=live
    )
    assert set(store.reconcile([observation])) == live
    assert len(store.log_path().read_text().splitlines()) == 3
    after = store.log_path().stat().st_mtime_ns
    assert set(store.reconcile([observation])) == live
    assert store.log_path().stat().st_mtime_ns == after


@pytest.mark.parametrize("kind", ["provider-down", "failed-setup", "blocked-start"])
def test_actual_asgi_click_refresh_restart_and_write_failure(dismissal_sources, monkeypatch, kind):
    _inputs, _paths, _group, write, store, _refresh, config = dismissal_sources
    write(kind)
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
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            initial = (await client.get("/api/state")).json()["analytics"]["attentionQueue"]
            assert len(initial) == 1
            body = {"itemId": initial[0]["id"], "kind": kind}

            def cannot_write(_store, _record):
                raise PermissionError("read-only acknowledgement store")

            with monkeypatch.context() as failing:
                failing.setattr(AttentionDismissalStore, "dismiss", cannot_write)
                refused = await client.post("/api/actions/dismiss", json=body)
                assert refused.status_code == 503
                assert refused.json()["status"] == "dismissal-not-held"
            await runtime.projector.prime()
            assert (await client.get("/api/state")).json()["analytics"]["attentionQueue"]
            assert (await client.post("/api/actions/dismiss", json=body)).status_code == 202
            for _ in range(10):
                await runtime.projector.prime()
                assert (await client.get("/api/state")).json()["analytics"]["attentionQueue"] == []
        restarted, next_runtime = make_app()
        await next_runtime.projector.prime()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted), base_url="http://test"
        ) as client:
            assert (await client.get("/api/state")).json()["analytics"]["attentionQueue"] == []
            assert len(store.current()) == 1
            write(kind, healthy=True)
            await next_runtime.projector.prime()
            assert store.current() == {}
            write(kind)
            await next_runtime.projector.prime()
            assert len((await client.get("/api/state")).json()["analytics"]["attentionQueue"]) == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["settings", "inspection"])
def test_failed_provider_read_cannot_end_even_another_providers_dismissal(
    dismissal_sources, monkeypatch, failure
):
    inputs, _paths, group, write, store, refresh, config = dismissal_sources
    write("provider-down")
    state = inputs.ProjectionInputState()
    item = refresh(state)[0]
    store.dismiss(
        AttentionDismissalRecord(itemId=item.id, kind=item.kind, dismissedAt="2026-10-15T01:10:00Z")
    )
    write("provider-down", healthy=True)
    provider_file = group / "provider-runtime" / "provider-state.json"
    provider_file.parent.mkdir(parents=True, exist_ok=True)
    settings = config.coordination_root / "isolated-settings.json"
    if failure == "inspection":
        settings.write_text(json.dumps({"providers": {}}))
        monkeypatch.setattr(provider_snapshots, "_inspect_containers", lambda _names, **_kw: None)
    provider_file.write_text(
        json.dumps(
            {
                "schema": provider_snapshots.WORKTREE_PROVIDER_STATE_SCHEMA,
                "isolatedProviderSettings": {"providers": ["cgc"], "path": str(settings)},
            }
        )
    )
    assert refresh(state) == []
    assert item.id in store.current()
    provider_file.unlink()
    monkeypatch.undo()
    assert refresh(state) == []
    assert store.current() == {}
