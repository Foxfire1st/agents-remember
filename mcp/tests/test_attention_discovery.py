"""Per-refresh traversal bounds and warm continuation of native attention discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agents_remember.controlplane import held_store_layout as layout
from agents_remember.serving.projections import native_attention
from agents_remember.serving.projections.native_attention import CONTROLPLANE_DOMAIN
from test_attention_native_inputs import (
    NOW,
    _activation,
    _entries,
    _known_host,
    _publication,
    _slug,
    _state,
    _take,
    _task,
)
from test_held_store_authority import (
    AGENT,
    _agent_id,
    _folder,
    _hold,
    _many_holders,
    _queue,
    _receipt,
)
from test_paseo_role_agents import runtime_config


def _enumeration_spy(monkeypatch, directory: Path) -> list[str]:
    """Count actual scandir pulls, including entries no reader elects to keep or parse."""

    examined: list[str] = []
    original = os.scandir

    class Scan:
        def __init__(self, path):
            self._path = Path(path)
            self._scan = original(path)

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self._scan)
            if self._path.is_relative_to(directory):
                examined.append(entry.path)
            return entry

        def close(self):
            self._scan.close()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(os, "scandir", Scan)
    return examined


def _assert_idle_read(reader, config, monkeypatch) -> None:
    def unexpected_io(*_args, **_kwargs):
        raise AssertionError("a complete age-only view must perform no file I/O")

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "stat", unexpected_io)
        scoped.setattr(Path, "read_text", unexpected_io)
        scoped.setattr(os, "scandir", unexpected_io)
        reader.read(
            config,
            host=_known_host(),
            task_entries=_entries([]),
            now=NOW,
            refreshed=frozenset(),
        )


def test_heartbeats_finish_pause_discovery_at_two_sizes(subtests, tmp_path, monkeypatch) -> None:
    """A partial cold or changed listing continues without another control-plane event."""

    for size in (40, 90):
        with subtests.test(size=size):
            root = tmp_path / str(size)
            config = runtime_config(root, root / "host")
            directory = config.coordination_root / "controlplane" / "atomic-series-activation"
            _activation_records(directory, range(size), paused={0: NOW.isoformat()})
            for index in range(size):
                (directory / f"noise-{index}").touch()
            reader = _state()._native_attention_reader
            monkeypatch.setattr(native_attention, "ENUMERATION_BUDGET", 7, raising=False)
            with monkeypatch.context() as scoped:
                examined = _enumeration_spy(scoped, directory)
                for _call in range(size * 2 // 7 + 2):
                    before = len(examined)
                    facts = reader.read(
                        config,
                        host=_known_host(),
                        task_entries=_entries([]),
                        now=NOW,
                        refreshed=frozenset(),
                    )
                    assert len(examined) - before <= 7
                    if facts.pauses_complete:
                        break
                assert facts.pauses_complete, "ordinary heartbeats must finish the parked walk"
                assert len(examined) == 2 * size, "non-JSON entries consume the same allowance"
                assert facts.paused_task_keys == frozenset({"repo/master/task.json"})
                before = len(examined)
                _assert_idle_read(reader, config, scoped)
                assert len(examined) == before, "a completed age-only view does no directory work"


def test_held_discovery_shares_one_entry_allowance_at_two_sizes(
    subtests, tmp_path, monkeypatch
) -> None:
    """Root discovery, receipt traversal and all held folders share the per-refresh bound."""

    for size in (40, 80):
        with subtests.test(size=size):
            root = tmp_path / str(size)
            coordination, holders = _many_holders(root, size)
            receipts = coordination / "tasks/repo/master/notes/reports/paseo-native-executions"
            for index in range(size * 4):
                (receipts / f"noise-{index}").touch()
                branch = coordination / "tasks/repo" / f"other-{index}"
                branch.mkdir()
                (branch / "noise").touch()
            reader = layout.HeldAuthorityReader()
            with monkeypatch.context() as scoped:
                examined = _enumeration_spy(scoped, root)
                for _call in range(size // 2 + 10):
                    before = len(examined)
                    view = reader.read(coordination)
                    assert len(examined) - before <= 256
                    if not reader.pending:
                        break
                assert view.complete, "the bounded root and receipt cursors must reach their ends"
                assert {row.message_id for row in view.rows} == {row[2] for row in holders}


def test_heartbeats_finish_unchanged_held_folders_at_two_sizes(
    subtests, tmp_path, monkeypatch
) -> None:
    """An unchanged receipt neither ends nor strands a partly enumerated sibling-store view."""

    for size in (40, 61):
        with subtests.test(size=size):
            root = tmp_path / str(size)
            config = runtime_config(root, root / "host")
            folder = _folder(root)
            messages = {_hold(folder, AGENT, "named")["heldMessageId"]}
            for index in range(size - 1):
                messages.add(_hold(folder, _agent_id(index), "beside")["heldMessageId"])
            _receipt(config.coordination_root, folder, AGENT)
            _queue(config.coordination_root, {})
            for index in range(400):
                (layout.product_held_dir(folder) / f"noise-{index}").touch()
            reader = _state()._native_attention_reader
            for _call in range(12):
                facts = reader.read(
                    config,
                    host=_known_host(),
                    task_entries=_entries([]),
                    now=NOW,
                    refreshed=frozenset(),
                )
                if facts.held_complete:
                    break
            assert facts.held_complete, (
                "heartbeat must resume the folder despite its unchanged receipt"
            )
            assert {row.message_id for row in facts.held} == messages
            with monkeypatch.context() as scoped:
                examined = _enumeration_spy(scoped, root)
                _assert_idle_read(reader, config, scoped)
                assert not examined, "a completed age-only view does no directory work"


def test_deep_receipt_branches_are_shed_instead_of_retained_without_limit(
    subtests, tmp_path
) -> None:
    """At both excess depths the traversal terminates and refuses a whole-census claim."""

    for depth in (20, 40):
        with subtests.test(depth=depth):
            root = tmp_path / str(depth)
            branch = root / "notes/reports/paseo-native-executions"
            for _ in range(depth):
                branch = branch / "nested"
            branch.mkdir(parents=True)
            (branch / "too-deep.json").write_text("{}")
            _queue(root, {})
            reader = layout.HeldAuthorityReader()
            for _ in range(10):
                view = reader.read(root)
                if not reader.pending:
                    break
            assert not reader.pending
            assert not view.complete
            reader.reset()


def test_an_unreadable_receipt_keeps_later_chunks_incomplete(
    subtests, tmp_path, monkeypatch
) -> None:
    """Resuming past an unreadable receipt cannot turn that partial census into a whole one."""

    for size in (6, 12):
        with subtests.test(size=size):
            root = tmp_path / str(size)
            coordination, _holders = _many_holders(root, size)
            receipts = coordination / "tasks/repo/master/notes/reports/paseo-native-executions"
            next(receipts.iterdir()).write_text("{broken")
            monkeypatch.setattr(layout, "MAX_HELD_AUTHORITY_RECEIPTS", 2)
            reader = layout.HeldAuthorityReader()
            for _call in range(size + 5):
                view = reader.read(coordination)
                assert not view.complete
                if not reader.pending:
                    break
            assert not reader.pending, "the failed record must not force an endless walk"
            assert len(view.rows) == size - 1


def test_receipt_root_cursors_are_capped_and_shed_at_two_sizes(
    subtests, tmp_path, monkeypatch
) -> None:
    """Root-path retention has the same hard cap as the stores; shedding never claims a census."""

    for size in (6, 12):
        with subtests.test(size=size):
            coordination = tmp_path / str(size)
            for index in range(size):
                directory = (
                    coordination / f"tasks/repo/task-{index}/notes/reports/paseo-native-executions"
                )
                directory.mkdir(parents=True)
            _queue(coordination, {})
            monkeypatch.setattr(layout, "MAX_HELD_AUTHORITY_RETAINED", 4)
            reader = layout.HeldAuthorityReader()
            for _call in range(size + 5):
                view = reader.read(coordination)
                assert len(reader._walks) <= 4
                if not reader.pending:
                    break
            assert not reader.pending
            assert not view.complete
            reader.reset()
            assert not reader._walks


def _activation_records(
    directory: Path,
    indexes,
    *,
    master: str = "master/task.json",
    paused: dict[int, str] | None = None,
) -> list[str]:
    """One activation record per index, its mtime stamped by the index so the newest are known."""

    directory.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    paused = paused or {}
    for index in indexes:
        name = f"{index:064x}.json"
        names.append(name)
        path = directory / name
        record: dict[str, object] = {
            "schemaVersion": "2.0",
            "contractFingerprint": f"{index:064x}",
            "selectedMaster": {"repository": "repo", "path": master},
            "contractPath": "/coord/tasks/repo/master/series-contract.md",
            "state": "vacant",
            "revision": 2,
            "selectedAt": "2026-10-22T08:00:00+00:00",
        }
        if index in paused:
            record["pausedAt"] = paused[index]
        path.write_text(json.dumps(record), encoding="utf-8")
        stamp = 1_700_000_000 + index
        os.utime(path, (stamp, stamp))
    return names


def _count_examined(monkeypatch) -> list[str]:
    """Every directory entry a refresh examined, in visit order: the work one call actually did."""

    examined: list[str] = []
    real_keep = native_attention._PauseWalk._keep

    def spy(self, entry, limit):
        examined.append(entry.name)
        return real_keep(self, entry, limit)

    monkeypatch.setattr(native_attention._PauseWalk, "_keep", spy)
    return examined


def _case_test_a_crowded_population_is_walked_within_one_budget_per_call(
    tmp_path, monkeypatch
) -> None:
    """L190-R1-F11: one refresh examines a budget of entries and resumes; it never drains.

    The reviewer's residual: the heap capped what was *kept* while the walk still visited and
    stat'd every entry, so one refresh cost time proportional to the whole population. With a
    budget below the population each call examines at most the budget, reports itself incomplete,
    and carries the cursor forward, so successive refreshes meet every record exactly once and the
    completing one publishes the listing a whole walk would have produced.
    """

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    directory = config.coordination_root / "controlplane" / "atomic-series-activation"
    names = _activation_records(directory, range(40), paused={0: "2026-10-22T08:30:00+00:00"})

    monkeypatch.setattr(native_attention, "ENUMERATION_BUDGET", 7)
    reader = _state()._native_attention_reader
    source = _task()
    examined = _count_examined(monkeypatch)

    facts = reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)
    assert len(examined) == 7, "one refresh examines the budget, not the population"
    assert facts.pauses_complete is False, "a walk short of its end is not a whole observation"
    assert facts.paused_task_keys == frozenset(), "a partial walk publishes no pause it has not met"

    calls = 1
    while not facts.pauses_complete:
        before = len(examined)
        facts = reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)
        assert len(examined) - before <= 7, "every refresh stays within the budget"
        calls += 1
        assert calls <= 8, "the cursor must resume, not restart, or this never completes"

    assert sorted(examined) == sorted(names), "successive refreshes resume where the last stopped"
    assert len(examined) == len(names), "no record is visited twice and none is skipped"
    assert facts.paused_task_keys == frozenset({"repo/master/task.json"})


def _case_test_an_incomplete_walk_keeps_the_listing_it_last_published(
    tmp_path, monkeypatch
) -> None:
    """L190-R1-F11: a partial walk never replaces a published listing with a subset of itself."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    directory = config.coordination_root / "controlplane" / "atomic-series-activation"
    _activation(config, paused_at="2026-10-22T08:30:00+00:00")

    reader = _state()._native_attention_reader
    source = _task()
    first = reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)
    assert first.pauses_complete
    assert first.paused_task_keys == frozenset({"repo/master/task.json"})

    # The directory now holds a second paused master, and a pass may not read it all at once.
    _activation_records(directory, range(1, 12), master="master/other.json")
    _activation_records(
        directory, [12], master="master/other.json", paused={12: "2026-10-22T08:40:00+00:00"}
    )
    monkeypatch.setattr(native_attention, "ENUMERATION_BUDGET", 2)

    stale = reader.read(
        config,
        host=_known_host(),
        task_entries=_entries([source]),
        now=NOW,
        refreshed=frozenset({CONTROLPLANE_DOMAIN}),
    )
    assert stale.pauses_complete is False
    assert stale.paused_task_keys == frozenset({"repo/master/task.json"}), (
        "the last complete listing stays visible while the walk is incomplete"
    )

    facts = stale
    for _ in range(12):
        if facts.pauses_complete:
            break
        facts = reader.read(
            config,
            host=_known_host(),
            task_entries=_entries([source]),
            now=NOW,
            refreshed=frozenset({CONTROLPLANE_DOMAIN}),
        )
    assert facts.pauses_complete, "the resumed walk must reach the end of the population"
    assert facts.paused_task_keys == frozenset(
        {"repo/master/task.json", "repo/master/other.json"}
    ), "the completing pass publishes every pause the whole population holds"


def test_a_crowded_directory_is_enumerated_within_a_per_call_budget(
    subtests, tmp_path, monkeypatch
) -> None:
    """F11: one refresh visits a bounded number of entries, resumes, and publishes only wholes."""

    for message, run in (
        (
            "a crowded population is walked within one budget per call",
            lambda **fixtures: _case_test_a_crowded_population_is_walked_within_one_budget_per_call(
                **_take(fixtures, "tmp_path", "monkeypatch")
            ),
        ),
        (
            "an incomplete walk keeps the listing it last published",
            lambda **fixtures: _case_test_an_incomplete_walk_keeps_the_listing_it_last_published(
                **_take(fixtures, "tmp_path", "monkeypatch")
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)
