from __future__ import annotations

import asyncio
import json
import os
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
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection import (
    ActionAvailability,
    Analytics,
    DriftSnapshotNode,
    EnclosureNode,
    TaskEntry,
    WorkspaceProjection,
)
from agents_remember.observer.reducer import (
    AnalyticalInputs,
    WorkspaceStructure,
    build_attention_queue,
    project_workspace,
)
from agents_remember.serving import app as serving_app
from agents_remember.serving.actions import ActionEvaluationContext, evaluate_action
from agents_remember.serving.projections import projection_inputs as inputs
from agents_remember.serving.projections.paths import observer_root
from agents_remember.worktrees.start_progress import START_PROGRESS_SCHEMA
from pydantic import ValidationError

# The two provider-only kinds MIK-R199 retires. Neither is a current attention kind any
# more: nothing observes them, no projection carries them, and no dismissal admits them.
RETIRED_KINDS = ("provider-down", "failed-setup")


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
                AnalyticalInputs(
                    drift_snapshots=[old_snapshot],
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
            AnalyticalInputs(drift_snapshots=[newer_snapshot], attention_dismissals=dismissed),
        )
        self.assertEqual(queue, [])
        changed_snapshot = newer_snapshot.model_copy(update={"actionableFingerprint": "changed"})
        queue = build_attention_queue(
            AnalyticalInputs(drift_snapshots=[changed_snapshot], attention_dismissals=dismissed)
        )
        self.assertEqual([item.kind for item in queue], ["actionable-drift"])


# R166: every remaining item is dismissible, independently of a removed session.


def blocked_start_sources() -> AnalyticalInputs:
    return AnalyticalInputs(
        engine_start_progress=[
            {"worktreeGroup": "/worktrees/group", "blockedReason": "needs a base"}
        ]
    )


def test_attention_ranking_lists_the_newest_entry_first_and_breaks_ties_by_id() -> None:
    given = replace(
        blocked_start_sources(),
        drift_snapshots=[
            DriftSnapshotNode(
                repository="repo",
                branch="main",
                actionableCount=1,
                checkedAt="2026-06-13T18:00:00+00:00",
                actionableFingerprint="finding",
                snapshotStaleSeconds=10.0,
            ),
            DriftSnapshotNode(
                repository="older",
                branch="main",
                actionableCount=1,
                checkedAt="2026-06-13T18:00:00+00:00",
                actionableFingerprint="finding",
                snapshotStaleSeconds=20.0,
            ),
            DriftSnapshotNode(
                repository="also-older",
                branch="main",
                actionableCount=1,
                checkedAt="2026-06-13T18:00:00+00:00",
                actionableFingerprint="finding",
                snapshotStaleSeconds=20.0,
            ),
        ],
    )
    queue = build_attention_queue(given)
    # MIK-R200@v2 rule 2a: the queue ranks by age alone, newest first, with the id breaking a
    # tie -- an entry that names no age counts as zero rather than sinking to the bottom. With
    # the provider-only kinds retired (MIK-R199) the tie-break is carried by two drift rows of
    # the same age, so the order is decided by id and nothing else.
    assert [item.id for item in queue] == [
        "blocked-start:/worktrees/group",
        "actionable-drift:repo:main",
        "actionable-drift:also-older:main",
        "actionable-drift:older:main",
    ]


def test_remaining_kind_click_is_accepted_without_a_session() -> None:
    given = blocked_start_sources()
    queue = build_attention_queue(given)
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
            actor="developer",
            now="2026-10-15T01:00:00+00:00",
            item_id=item.id,
            kind=item.kind,
        ),
    )
    assert result.status_code == 202
    assert result.dismissal and result.dismissal.kind == item.kind


def test_remaining_kind_dismissal_survives_unchanged_passes() -> None:
    given = blocked_start_sources()
    item = build_attention_queue(given)[0]
    given = replace(
        given,
        attention_dismissals={
            item.id: AttentionDismissalRecord(
                itemId=item.id, kind=item.kind, dismissedAt="2026-10-15T01:00:00+00:00"
            )
        },
    )
    for _ in range(10):
        assert build_attention_queue(given) == []


@pytest.mark.parametrize("kind", RETIRED_KINDS)
def test_retired_kind_cannot_be_recorded_as_a_current_dismissal(kind: str) -> None:
    with pytest.raises(ValidationError):
        AttentionDismissalRecord(
            itemId=f"{kind}:legacy", kind=kind, dismissedAt="2026-10-15T01:00:00+00:00"
        )


@pytest.mark.parametrize("kind", RETIRED_KINDS)
def test_legacy_retired_dismissal_row_is_dropped_and_suppresses_nothing(tmp_path, kind) -> None:
    store = AttentionDismissalStore(tmp_path)
    store.log_path().parent.mkdir(parents=True)
    store.log_path().write_text(
        json.dumps(
            {
                "schema": "ar-attention-dismissal/v1",
                "itemId": f"{kind}:legacy",
                "kind": kind,
                "dismissedAt": "2026-10-15T01:00:00+00:00",
            }
        )
        + "\n"
    )
    assert store.read() == []
    assert store.current() == {}
    # A persisted retired acknowledgement cannot suppress a live surviving item either.
    live = build_attention_queue(blocked_start_sources())
    assert [row.id for row in live] == ["blocked-start:/worktrees/group"]


@pytest.mark.parametrize("kind", RETIRED_KINDS)
def test_retired_kind_cannot_be_dismissed_through_the_published_queue(kind: str) -> None:
    given = blocked_start_sources()
    queue = build_attention_queue(given)
    item = queue[0]
    projection = WorkspaceProjection(
        generatedAt="2026-10-15T01:00:00+00:00", analytics=Analytics(attentionQueue=queue)
    )
    # No item of the retired kind is published, so a dismissal naming one is unknown.
    unknown = evaluate_action(
        projection,
        "dismiss",
        None,
        ActionEvaluationContext(
            actor="developer",
            now="2026-10-15T01:00:00+00:00",
            item_id=f"{kind}:legacy",
            kind=kind,
        ),
    )
    assert unknown.status_code == 404
    assert unknown.body["status"] == "unknown-item"
    # And a live item cannot be acknowledged under a retired kind.
    mismatch = evaluate_action(
        projection,
        "dismiss",
        None,
        ActionEvaluationContext(
            actor="developer",
            now="2026-10-15T01:00:00+00:00",
            item_id=item.id,
            kind=kind,
        ),
    )
    assert mismatch.status_code == 400
    assert mismatch.body["status"] == "item-kind-mismatch"


def test_legacy_provider_surfaces_publish_no_attention_or_projection_state(tmp_path) -> None:
    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
    )
    # Every legacy provider surface a live install could still hold on disk.
    current_state = (
        tmp_path / "logs" / "providers" / "status" / "workspace" / "projects" / "current.json"
    )
    current_state.parent.mkdir(parents=True, exist_ok=True)
    current_state.write_text(
        json.dumps(
            {
                "checkedAt": "2026-10-15T01:00:00+00:00",
                "providers": {
                    "codegraphcontext-code": {"id": "codegraphcontext-code", "state": "failed"}
                },
            }
        )
    )
    setup = tmp_path / "logs" / "providers" / "setup"
    setup.mkdir(parents=True)
    (setup / "last-setup.json").write_text(
        json.dumps({"action": "setup", "ok": False, "state": "failed"})
    )
    group = tmp_path / "worktrees" / "repo" / "group"
    runtime = group / "provider-runtime"
    runtime.mkdir(parents=True)
    (runtime / "setup-progress.json").write_text(
        json.dumps(
            {
                "schema": "ar-provider-setup-progress/v1",
                "state": "failed",
                "failedPhases": ["cgc-seed"],
                "updatedAt": "2026-10-15T01:00:00+00:00",
            }
        )
    )
    (runtime / "provider-state.json").write_text(
        json.dumps(
            {
                "schema": "ar-worktree-provider-state/v1",
                "isolatedProviderSettings": {"providers": ["codegraphcontext-code"]},
            }
        )
    )

    state = inputs.ProjectionInputState()
    result = state.read(
        config,
        inputs.ProjectionReaders(repo_surfaces=lambda _config, _now: ([], [])),
        observer_root=observer_root(config),
        pass_=inputs.RefreshPass(
            now=datetime(2026, 10, 15, 2, tzinfo=UTC),
            refresh=inputs.ProjectionRefresh.full(),
        ),
    )
    given = AnalyticalInputs(
        drift_snapshots=result.drift_snapshots,
        engine_start_progress=result.engine_start_progress,
        attention_dismissals=result.attention_dismissals,
    )
    assert build_attention_queue(given) == []
    assert result.attention_dismissals == {}
    projection = project_workspace(
        structure=WorkspaceStructure(
            enclosures=result.enclosures,
            active_worktree_groups=result.active_worktree_groups,
        ),
        now=datetime(2026, 10, 15, 2, tzinfo=UTC),
        given=given,
    )
    served = projection.model_dump(by_alias=True)
    assert "providers" not in served
    assert "setupProgress" not in served["analytics"]
    assert "setupSummaries" not in served["analytics"]
    assert served["analytics"]["attentionQueue"] == []


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
def dismissal_sources(tmp_path):

    config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path,
        transcript_root=tmp_path / "logs",
    )
    group = tmp_path / "worktrees" / "repo" / "group"
    group.mkdir(parents=True)
    paths = {
        "blocked-start": tmp_path / "temp" / "worktree-start" / "repo" / "group.json",
    }

    def write(kind, *, healthy=False, stamp="2026-10-15T01:00:00+00:00"):
        payload = {
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
            AnalyticalInputs(
                engine_start_progress=result.engine_start_progress,
                attention_dismissals=result.attention_dismissals,
            ),
        )

    return inputs, paths, group, write, store, refresh, config


def test_observed_recovery_ends_dismissal_and_only_a_later_failure_returns(dismissal_sources):
    inputs, _paths, _group, write, store, refresh, _config = dismissal_sources
    kind = "blocked-start"
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


def test_partial_or_failed_source_keeps_dismissal_but_a_complete_gone_read_reclaims(
    dismissal_sources,
):
    inputs, paths, _group, write, store, refresh, _config = dismissal_sources
    kind = "blocked-start"
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
    assert refresh(state) == []
    assert store.current() == {}


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
        itemId="blocked-start:/worktrees/group",
        kind="blocked-start",
        dismissedAt="2026-10-15T01:00:00Z",
    )
    store.dismiss(record)
    observation = AttentionObservation(
        "blocked-start", datetime(2026, 10, 15, 2, tzinfo=UTC), existing={record.itemId}
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
        itemId="blocked-start:/worktrees/group",
        kind="blocked-start",
        dismissedAt="2026-10-15T02:00:01Z",
    )
    store.dismiss(record)
    before_click = AttentionObservation("blocked-start", datetime(2026, 10, 15, 2, tzinfo=UTC))
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
    # Removed session items and the retired provider kinds cannot be published or parsed
    # as current kinds.
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
    retired += [
        json.dumps(
            {
                "schema": "ar-attention-dismissal/v1",
                "itemId": f"{kind}:legacy-{i}",
                "kind": kind,
                "dismissedAt": "2026-10-15T01:00:00Z",
            }
        )
        for kind in RETIRED_KINDS
        for i in range(history_size)
    ]
    records = [
        AttentionDismissalRecord(
            itemId=f"blocked-start:group-{i}",
            kind="blocked-start",
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
        "blocked-start", datetime(2026, 10, 15, 2, tzinfo=UTC), existing=live
    )
    assert set(store.reconcile([observation])) == live
    assert len(store.log_path().read_text().splitlines()) == 3
    after = store.log_path().stat().st_mtime_ns
    assert set(store.reconcile([observation])) == live
    assert store.log_path().stat().st_mtime_ns == after


def test_actual_asgi_click_refresh_restart_and_write_failure(dismissal_sources, monkeypatch):
    _inputs, _paths, _group, write, store, _refresh, config = dismissal_sources
    kind = "blocked-start"
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
            live_inputs=serving_app.LiveProjectionInputs(landing_state=False, change_watch=False),
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
