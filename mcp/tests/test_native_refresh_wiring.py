"""L190-R1 F10/F11: the native attention stores are watched, and gated by their domains.

F10 sealed: the control-plane pause/notice facts and the recipient held-message store were
only *classified* as reader inputs; no watcher root existed for them, so a write there never
woke the reader. These cases drive the real watcher over a real coordination tree and prove
an input event reaches its domain without waiting for a poll.

F11 sealed: every heartbeat re-read native stores. These cases prove a pass hands the native
reader exactly the domains that moved -- nothing on an age-only heartbeat -- and that the
retained facts survive with the stores gone from disk until their own domain changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from agents_remember.controlplane.attention_notices import (
    PendingTerminalNotice,
    begin_terminal_notice,
    confirm_terminal_notice,
)
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer import reducer as reducer_module
from agents_remember.observer.native_attention import NativeAttentionFacts
from agents_remember.observer.reducer import WorkspaceStructure, project_workspace
from agents_remember.observer.reducer_impl._types import AnalyticalInputs
from agents_remember.serving.change_watcher import (
    ChangePacer,
    ProjectionInputWatcher,
    held_receipt_root,
    is_projection_input_event,
    projection_domains_for_paths,
    projection_input_roots,
    shallow_projection_input_roots,
)
from agents_remember.serving.projections import projection_inputs as inputs_module
from agents_remember.serving.projections.projection_inputs import (
    NATIVE_ATTENTION_DOMAINS,
    ProjectionDomain,
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
AGENT_ID = "22222222-2222-4222-8222-222222222222"
TASK_KEY = "repo/master/task.json"


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _coordination_tree(root: Path) -> Path:
    """A coordination tree with every native store *and* the trees we must not recurse into."""

    (root / "tasks").mkdir(parents=True, exist_ok=True)
    activation = root / "controlplane" / "atomic-series-activation"
    activation.mkdir(parents=True, exist_ok=True)
    # The lifecycle archives beside the activation records: large, and never an attention input.
    (root / "controlplane" / "lifecycle-enclosure-archives" / "leaf").mkdir(parents=True)
    (root / "temp").mkdir(exist_ok=True)
    (root / "temp" / "worktree-start").mkdir()
    (root / "temp" / "tool-reports").mkdir()
    # The checkouts the watcher must never watch (provider-runtime churn, 50k dirs).
    (root / "worktrees" / "leaf" / "provider-runtime" / "postgres").mkdir(parents=True)
    return activation


# --- F10: real, bounded roots --------------------------------------------------------------


def _case_test_the_native_stores_have_real_bounded_watch_roots(tmp_path: Path) -> None:
    """The activation records, the notice store and the held queue are actual watched paths."""

    config = _config(tmp_path)
    activation = _coordination_tree(tmp_path)
    controlplane = tmp_path / "controlplane"

    roots = projection_input_roots(config)
    assert activation in roots, f"activation records are watched: {roots}"
    assert controlplane in roots, f"the notice store's parent is watched: {roots}"
    assert tmp_path / "temp" in roots, f"the held queue's parent is watched: {roots}"

    # Bounded (F10): exactly those two parents are watched without recursion, so neither the
    # lifecycle archives nor the product's scratch trees are recursed into, and no root reaches
    # the worktree checkouts.
    assert shallow_projection_input_roots(config) == frozenset({controlplane, tmp_path / "temp"})
    assert controlplane / "lifecycle-enclosure-archives" not in roots
    assert tmp_path not in roots
    worktrees = tmp_path / "worktrees"
    assert not any(root == worktrees or worktrees in root.parents for root in roots), roots


def _case_test_each_native_store_write_maps_to_its_own_domain(tmp_path: Path) -> None:
    """Classification names the two native domains, and the pre-existing roots still do."""

    config = _config(tmp_path)
    activation = _coordination_tree(tmp_path)

    def domains(*paths: str) -> frozenset[ProjectionDomain]:
        return projection_domains_for_paths(config, set(paths))

    assert domains(str(tmp_path / "controlplane" / "attention-notices.json")) == frozenset(
        {ProjectionDomain.ATTENTION_CONTROLPLANE}
    )
    assert domains(str(activation / f"{'a' * 64}.json")) == frozenset(
        {ProjectionDomain.ATTENTION_CONTROLPLANE}
    )
    assert domains(str(tmp_path / "temp" / "held-message-queue.json")) == frozenset(
        {ProjectionDomain.ATTENTION_HELD}
    )
    # Preserved classification: the narrower temp roots still win over the held parent.
    assert domains(str(tmp_path / "temp" / "worktree-start" / "x.json")) == frozenset(
        {ProjectionDomain.START_PROGRESS}
    )
    assert domains(str(tmp_path / "temp" / "tool-reports" / "x.json")) == frozenset(
        {ProjectionDomain.TOOL_REPORTS}
    )
    assert domains(str(tmp_path / "tasks" / "repo" / "master" / "leaf.json")) == frozenset(
        {ProjectionDomain.TASKS}
    )


def _case_test_a_withdrawal_receipt_reaches_the_held_reader_on_both_shapes(tmp_path: Path) -> None:
    """L190-R1-F10: the receipt a withdrawal rewrites is a held event, task-bound or taskless.

    The store itself sits in a workspace product tree the projection does not watch, and every
    store mutation is accompanied by a receipt write. The task-bound receipt lives under the
    recursive ``tasks`` root, so classification stopped at TASKS and the held reader was never
    woken; a taskless receipt was under no watched root at all. Both now name the held domain,
    and the taskless root exists as a real watch root.
    """

    config = _config(tmp_path)
    _coordination_tree(tmp_path)
    task_bound = (
        tmp_path
        / "tasks"
        / "repo"
        / "master"
        / "leaf"
        / "notes"
        / "reports"
        / "paseo-native-executions"
        / f"worker-{AGENT_ID}.json"
    )
    taskless = (
        tmp_path
        / "notes"
        / "reports"
        / "paseo-native-executions"
        / "investigator"
        / "sessions"
        / f"{AGENT_ID}.json"
    )
    for path in (task_bound, taskless):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    assert projection_domains_for_paths(config, {str(task_bound)}) == frozenset(
        {ProjectionDomain.TASKS, ProjectionDomain.ATTENTION_HELD}
    )
    assert projection_domains_for_paths(config, {str(taskless)}) == frozenset(
        {ProjectionDomain.ATTENTION_HELD}
    )
    # The taskless shape is watched at all now; before this repair nothing named it.
    roots = set(projection_input_roots(config))
    assert held_receipt_root(config) in roots
    assert is_projection_input_event(str(task_bound))
    assert is_projection_input_event(str(taskless))
    # A non-receipt document in the same tree keeps its own root's meaning only.
    other = tmp_path / "tasks" / "repo" / "master" / "leaf" / "task.json"
    other.write_text("{}", encoding="utf-8")
    assert projection_domains_for_paths(config, {str(other)}) == frozenset({ProjectionDomain.TASKS})


def activation_dir(root: Path) -> Path:
    """The one directory of native activation records every store write lands in."""

    return root / "controlplane" / "atomic-series-activation"


def _case_test_the_existing_own_write_lock_and_temp_filters_still_drop_non_inputs(
    tmp_path: Path,
) -> None:
    """The store lockfiles and atomic temps these writers produce are never inputs (F10)."""

    _coordination_tree(tmp_path)
    notice = tmp_path / "controlplane" / "attention-notices.json"
    queue = tmp_path / "temp" / "held-message-queue.json"

    assert is_projection_input_event(str(notice))
    assert is_projection_input_event(str(queue))
    # ``kernel.file_lock`` beside the notice store and the held queue.
    assert not is_projection_input_event(f"{notice}.lock")
    assert not is_projection_input_event(f"{queue}.lock")
    # ``mkstemp(prefix=f".{name}.")`` siblings of both atomic writers.
    assert not is_projection_input_event(str(notice.parent / f".{notice.name}.abcd1234"))
    assert not is_projection_input_event(str(queue.parent / f".{queue.name}.abcd1234"))
    assert not is_projection_input_event(str(tmp_path / "controlplane" / "x.json.tmp"))
    # A directory mtime is not content any reader consumes.
    assert not is_projection_input_event(str(activation_dir(tmp_path)))


class NativeWatchRootWakeTests(unittest.IsolatedAsyncioTestCase):
    """F10: one real watcher generation delivers each store write to its domain's reader."""

    async def test_a_notice_and_a_held_queue_write_wake_their_domains_without_a_poll(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _coordination_tree(root)
            config = _config(root)
            # The coordination tree's own roots: the live user's activity directory is a real
            # path on this machine and is not this test's subject (its mapping is above).
            roots = [
                path
                for path in projection_input_roots(config)
                if path == root or root in path.parents
            ]
            self.assertEqual(len(roots), 6, roots)
            # A heartbeat far longer than the test: every wake below must come from the write.
            pacer = ChangePacer(interval=0.05, heartbeat=600.0)
            watcher = ProjectionInputWatcher(config)
            task = asyncio.create_task(watcher._watch_once(roots, pacer, reconcile=False))
            try:
                async with asyncio.timeout(HANG_GUARD_SECONDS):
                    await async_wait_until(
                        lambda: pacer.watcher_healthy, "the native watch establishes"
                    )

                    # The product's own writers: the notice store's atomic replace and the
                    # delivery machinery's atomic queue replace, not a plain write.
                    begin_terminal_notice(
                        root,
                        PendingTerminalNotice(
                            occurrenceId=f"task-abandoned:{TASK_KEY}",
                            kind="task-abandoned",
                            taskKey=TASK_KEY,
                            taskReference=TaskDocumentRef(
                                repository="repo", path="master/task.json"
                            ),
                            taskTitle="Task",
                            at="2026-10-22T08:58:00+00:00",
                        ),
                    )
                    notice = root / "controlplane" / "attention-notices.json"
                    wake = await pacer.wait()
                    pacer.acknowledge(wake)
                    self.assertEqual(wake.reason, "change")
                    self.assertEqual(
                        wake.domains, frozenset({ProjectionDomain.ATTENTION_CONTROLPLANE})
                    )
                    self.assertIn(str(notice), wake.paths)

                    queue = root / "temp" / "held-message-queue.json"
                    atomic_write_text(
                        queue,
                        json.dumps({"schema": "ar-held-message-queue/v1", "entries": {}}) + "\n",
                    )
                    wake = await pacer.wait()
                    pacer.acknowledge(wake)
                    self.assertEqual(wake.reason, "change")
                    self.assertEqual(wake.domains, frozenset({ProjectionDomain.ATTENTION_HELD}))
                    self.assertIn(str(queue), wake.paths)
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def test_the_recursive_lane_never_watches_the_archive_or_worktree_trees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            activation = _coordination_tree(root)
            config = _config(root)
            shallow = shallow_projection_input_roots(config)
            recursive = [path for path in projection_input_roots(config) if path not in shallow]
            self.assertIn(activation, recursive)
            self.assertNotIn(root / "controlplane", recursive)
            self.assertNotIn(root / "temp", recursive)
            self.assertNotIn(root / "worktrees", recursive)
            self.assertNotIn(root, recursive)


# --- F11: the reader is gated on its own domains -------------------------------------------


class _CountingReader:
    """The native reader seam: records the invalidation set every pass hands it."""

    def __init__(self) -> None:
        self.calls: list[frozenset[ProjectionDomain] | None] = []

    def read(self, _config: McpRuntimeConfig, **kwargs: object) -> NativeAttentionFacts:
        # The reader's other inputs are the caller's business; the gating contract is
        # ``refreshed``, which every pass must hand over explicitly.
        assert "refreshed" in kwargs, kwargs
        self.calls.append(kwargs["refreshed"])  # type: ignore[arg-type]
        return NativeAttentionFacts()


def _read_with(state: ProjectionInputState, config: McpRuntimeConfig, refresh: ProjectionRefresh):
    return state.read(
        config,
        ProjectionReaders(repo_surfaces=lambda _config, _now: ([], [])),
        observer_root=config.coordination_root / "logs" / "observer",
        pass_=RefreshPass(now=NOW, refresh=refresh),
    )


def _case_test_a_pass_hands_the_reader_exactly_the_native_domains_that_moved(
    tmp_path: Path,
) -> None:
    """F11: heartbeat and unrelated changes name no native domain; only a real move does."""

    config = _config(tmp_path)
    (tmp_path / "tasks").mkdir()
    state = ProjectionInputState()
    reader = _CountingReader()
    state._native_attention_reader = reader  # type: ignore[assignment]

    _read_with(state, config, ProjectionRefresh.full())
    assert reader.calls[0] is None, "the first pass refreshes everything"

    _read_with(state, config, ProjectionRefresh.heartbeat())
    assert reader.calls[1] == frozenset(), "an age-only heartbeat reaches no store"

    _read_with(state, config, ProjectionRefresh.change(frozenset({ProjectionDomain.TASKS})))
    assert reader.calls[2] == frozenset(), "an unrelated change reaches no native store"

    _read_with(
        state,
        config,
        ProjectionRefresh.change(frozenset({ProjectionDomain.ATTENTION_HELD})),
    )
    assert reader.calls[3] == frozenset({ProjectionDomain.ATTENTION_HELD})

    _read_with(state, config, ProjectionRefresh.full())
    assert reader.calls[4] == NATIVE_ATTENTION_DOMAINS, "a full pass refreshes both stores"


def _case_test_a_pass_builds_the_canonical_task_list_once_and_the_rows_reuse_it(
    tmp_path: Path, monkeypatch
) -> None:
    """L190-R1-F04/F11: one canonical join per pass, read by the stall clock and the rows.

    The clock used to build its own lineage from authored parent text while the row builder
    used the joined parent. The join now happens once in the input reader and the reducer
    reuses it, so the two relations cannot disagree and a pass does not pay for the join twice.
    """

    config = _config(tmp_path)
    (tmp_path / "tasks").mkdir()

    joins = {"inputs": 0, "reducer": 0}
    real_inputs_join = inputs_module.build_task_entries
    real_reducer_join = reducer_module.build_task_entries

    def counting_inputs(*args: object, **kwargs: object):
        joins["inputs"] += 1
        return real_inputs_join(*args, **kwargs)  # type: ignore[arg-type]

    def counting_reducer(*args: object, **kwargs: object):
        joins["reducer"] += 1
        return real_reducer_join(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(inputs_module, "build_task_entries", counting_inputs)
    monkeypatch.setattr(reducer_module, "build_task_entries", counting_reducer)

    state = ProjectionInputState()
    _read_with(state, config, ProjectionRefresh.full())
    assert joins["inputs"] == 1, "the pass builds the canonical list exactly once"
    assert state._task_entries is not None

    # A caller that hands the reducer the pass's own entries: it must not join again.
    given = AnalyticalInputs(
        task_sources=state._task_sources,
        host_activity=state._host_activity,
        task_entries=state._task_entries,
    )
    projection = project_workspace(
        structure=WorkspaceStructure(enclosures=[], providers=[]), given=given, now=NOW
    )
    assert joins["reducer"] == 0, "the reducer reused the canonical entries"
    assert projection.tasks == []


def _held_inputs(root: Path) -> tuple[Path, Path]:
    """One recipient store, its receipt, and the queue naming the receipt (the writer's shape)."""

    folder = root / "worktrees" / "leaf"
    store = folder / ".ar-product" / "held-messages" / f"{AGENT_ID}.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        json.dumps(
            {
                "schema": "ar-held-messages/v1",
                "agentId": AGENT_ID,
                "messages": [
                    {
                        "id": "message-1",
                        "senderAgentId": "33333333-3333-4333-8333-333333333333",
                        "text": "secret body",
                        "takenAt": "2026-10-22T08:55:00+00:00",
                        "reason": "recipient at a permission prompt",
                    }
                ],
                "updatedAt": "2026-10-22T08:55:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    receipt = root / "notes" / "reports" / "receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "ar-role-execution/v1",
                "workspace": {"path": str(folder)},
                "execution": {"agentId": AGENT_ID},
            }
        ),
        encoding="utf-8",
    )
    queue = root / "temp" / "held-message-queue.json"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        json.dumps(
            {
                "schema": "ar-held-message-queue/v1",
                "entries": {str(receipt): NOW.isoformat()},
            }
        ),
        encoding="utf-8",
    )
    return store, queue


def _pause_record(root: Path) -> Path:
    path = root / "controlplane" / "atomic-series-activation" / f"{'a' * 64}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schemaVersion": "2.0",
                "contractFingerprint": "a" * 64,
                "selectedMaster": {"repository": "repo", "path": "master/task.json"},
                "contractPath": str(root / "tasks" / "repo" / "master" / "series-contract.md"),
                "state": "vacant",
                "revision": 2,
                "selectedAt": "2026-10-22T08:00:00+00:00",
                "pausedAt": "2026-10-22T08:30:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return path


def _notice(root: Path) -> None:
    notice = PendingTerminalNotice(
        occurrenceId=f"task-abandoned:{TASK_KEY}",
        kind="task-abandoned",
        taskKey=TASK_KEY,
        taskReference=TaskDocumentRef(repository="repo", path="master/task.json"),
        taskTitle="Task",
        at="2026-10-22T08:58:00+00:00",
    )
    begin_terminal_notice(root, notice)
    confirm_terminal_notice(root, notice.occurrenceId)


def _case_test_a_heartbeat_retains_every_native_fact_with_the_stores_gone(tmp_path: Path) -> None:
    """F11: no store is reread on an unchanged pass; only a named domain re-derives facts."""

    config = _config(tmp_path)
    (tmp_path / "tasks").mkdir()
    store, queue = _held_inputs(tmp_path)
    pause = _pause_record(tmp_path)
    _notice(tmp_path)
    state = ProjectionInputState()

    first = _read_with(
        state,
        config,
        ProjectionRefresh.change(NATIVE_ATTENTION_DOMAINS | {ProjectionDomain.TASKS}),
    ).native_attention
    assert [row.message_id for row in first.held] == ["message-1"]
    assert first.paused_task_keys == frozenset({TASK_KEY})
    assert [notice.taskKey for notice in first.notices] == [TASK_KEY]
    assert first.held_complete and first.pauses_complete and first.notices_complete

    # Remove every native store from disk. A pass that reaches no store cannot notice.
    store.unlink()
    queue.unlink()
    pause.unlink()
    (tmp_path / "controlplane" / "attention-notices.json").unlink()

    heartbeat = _read_with(state, config, ProjectionRefresh.heartbeat()).native_attention
    assert heartbeat.held == first.held
    assert heartbeat.paused_task_keys == first.paused_task_keys
    assert heartbeat.notices == first.notices
    assert heartbeat.held_complete and heartbeat.pauses_complete and heartbeat.notices_complete

    unrelated = _read_with(
        state, config, ProjectionRefresh.change(frozenset({ProjectionDomain.TASKS}))
    ).native_attention
    assert unrelated.held == first.held
    assert unrelated.paused_task_keys == first.paused_task_keys
    assert unrelated.notices == first.notices

    # The held domain's own invalidation is what re-derives the held rows: the queue is gone,
    # so the complete observation is emptiness -- a real reread, not a retained guess.
    reread = _read_with(
        state,
        config,
        ProjectionRefresh.change(frozenset({ProjectionDomain.ATTENTION_HELD})),
    ).native_attention
    assert reread.held == ()
    assert reread.pauses_complete is True, "the control-plane store was not reread"
    assert reread.paused_task_keys == first.paused_task_keys
    assert reread.notices == first.notices


# Consolidated cases: one group test per requirement. Every assertion and every
# failure message survives; each case runs as a subtest in its own root.


def test_the_native_stores_have_bounded_watch_roots_and_domains(
    subtests, tmp_path, monkeypatch
) -> None:
    """F10: each native store has a real root and its own domain, receipts are held events on both shapes, and the own-write/lock/temp filters still drop non-inputs."""

    for message, run in (
        (
            "the native stores have real bounded watch roots",
            lambda **fixtures: _case_test_the_native_stores_have_real_bounded_watch_roots(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "each native store write maps to its own domain",
            lambda **fixtures: _case_test_each_native_store_write_maps_to_its_own_domain(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a withdrawal receipt reaches the held reader on both shapes",
            lambda **fixtures: (
                _case_test_a_withdrawal_receipt_reaches_the_held_reader_on_both_shapes(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "the existing own write lock and temp filters still drop non inputs",
            lambda **fixtures: (
                _case_test_the_existing_own_write_lock_and_temp_filters_still_drop_non_inputs(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_a_pass_hands_the_reader_only_the_domains_that_moved(
    subtests, tmp_path, monkeypatch
) -> None:
    """F11: a pass names exactly the native domains that moved, a heartbeat retains every fact, and the canonical task list is built once."""

    for message, run in (
        (
            "a pass hands the reader exactly the native domains that moved",
            lambda **fixtures: (
                _case_test_a_pass_hands_the_reader_exactly_the_native_domains_that_moved(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a pass builds the canonical task list once and the rows reuse it",
            lambda **fixtures: (
                _case_test_a_pass_builds_the_canonical_task_list_once_and_the_rows_reuse_it(
                    **_take(fixtures, "tmp_path", "monkeypatch")
                )
            ),
        ),
        (
            "a heartbeat retains every native fact with the stores gone",
            lambda **fixtures: (
                _case_test_a_heartbeat_retains_every_native_fact_with_the_stores_gone(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def _slug(message: str) -> str:
    """A directory name for one case, so no two subtests share a coordination root."""

    return "".join(char if char.isalnum() else "-" for char in message)[:48]


def _take(fixtures: dict, *names: str) -> dict:
    """The fixtures one case declares, so a group can supply its whole set."""

    return {name: fixtures[name] for name in names}
