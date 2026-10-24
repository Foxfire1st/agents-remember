"""The moved watcher: the notice, the capture that records nothing, and the offers (MIK-R186 rule 7).

Each case protects one promise of rule 7 rather than the bytes: the capture writes Git objects and
no pin or record, the two notice texts name the comparison (or the new state) with the per-block
count, a planning leaf compares the master tips and captures nothing, a turn end never runs for a
planning leaf, and no path sends or holds a message.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.review_evidence_packet import (
    COMPARISON_OWNER,
    PLANNED_OWNER,
    comparison_reference,
    planned_reference,
)
from agents_remember.application.review_planned_comparison import (
    explainer_application_ports,
    planned_comparison,
)
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.canvas.explainer_paths import leaf_workspace
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    PlannedIdentity,
    agent_id,
    write_binding,
)
from agents_remember.cli import role_held_messages
from agents_remember.controlplane import held_store_layout
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.serving.change_watcher import MoveNoticeWatch, WatchOptions
from agents_remember.serving.explainer_watcher import (
    CapturedComparison,
    ComparisonMeasurement,
    GitComparisonOwner,
    LeafSelection,
    MovedNotice,
    MoveNoticeChannel,
    changes_between,
    master_tips,
    notice_event,
    notice_for,
)
from agents_remember.tasks.planned_comparison import (
    PlannedComparisonTips,
    contract_branch_lines,
    master_is_organizational,
    resolve_line_commit,
    resolve_master_contract,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

REPO = "agents-remember"
TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L183"
PLANNING = "260928-MIK-L189"
NOW = "2026-10-22T08:00:00+00:00"


# --- fixtures ------------------------------------------------------------------------------------


WATCHER_PORTS = explainer_application_ports().watcher


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repository(root: Path, name: str, branch: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", branch)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    return repo


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _config(tmp_path: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        transcript_root=tmp_path / "transcripts",
        repositories={
            REPO: RepositoryScope(
                repo_id=REPO,
                path=tmp_path / "repo",
                memory_root=tmp_path / "memory",
            )
        },
    )


def _task_root(tmp_path: Path) -> Path:
    root = tmp_path / "tasks" / REPO / TASK
    root.mkdir(parents=True, exist_ok=True)
    return root


def _side(tree: str, repository: str = "/repo/code") -> ReviewTreeSide:
    return ReviewTreeSide(repository=repository, tree=tree)


def _record(
    number: int,
    code_candidate: str = "b" * 40,
    memory_candidate: str = "d" * 40,
    code_base: str = "a" * 40,
    memory_base: str = "c" * 40,
) -> ReviewTreeComparisonRecord:
    return ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=number,
        code_base=_side(code_base),
        code_candidate=_side(code_candidate),
        memory_base=_side(memory_base, "/repo/memory"),
        memory_candidate=_side(memory_candidate, "/repo/memory"),
        recorded_at=NOW,
    )


def _binding(
    record: ReviewTreeComparisonRecord, *, planned: PlannedIdentity | None = None
) -> ExplainerBinding:
    owner = PLANNED_OWNER if planned is not None else COMPARISON_OWNER
    reference = (
        planned_reference(TASK, LEAF) if planned is not None else comparison_reference(record)
    )
    return ExplainerBinding(
        binding=FrameBinding.from_record(
            record, comparison_owner=owner, comparison_reference=reference
        ),
        planned=planned,
        snapshot=f"snapshot-{record.number}",
        prepared_at=NOW,
        packet_digest="sha256:" + "0" * 64,
    )


def _environment(config: McpRuntimeConfig, leaf_id: str, binding: ExplainerBinding) -> Path:
    workspace = leaf_workspace(config, REPO, TASK, leaf_id)
    workspace.mkdir(parents=True, exist_ok=True)
    write_binding(workspace, binding)
    return workspace


class FakeOwner:
    """A comparison owner whose capture is a prepared record; it counts its calls."""

    def __init__(self, captured: ReviewTreeComparisonRecord) -> None:
        self.record = captured
        self.calls = 0

    def capture(self, _selection: LeafSelection) -> CapturedComparison:
        self.calls += 1
        return CapturedComparison(self.record)


class FakeCounter:
    def __init__(self, blocks: int | None) -> None:
        self.blocks = blocks
        self.calls = 0

    def __call__(self, _binding: FrameBinding, _captured: ReviewTreeComparisonRecord) -> int | None:
        self.calls += 1
        return self.blocks


def _write_record(config: McpRuntimeConfig, record: ReviewTreeComparisonRecord) -> None:
    directory = comparison_directory(config.coordination_root / "tasks" / REPO / TASK, LEAF)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{record.number}.json").write_text(
        record.model_dump_json(by_alias=True), encoding="utf-8"
    )


def _selection(leaf_id: str = LEAF) -> LeafSelection:
    return LeafSelection(REPO, TASK, leaf_id)


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


# --- the comparison notice -----------------------------------------------------------------------


def test_the_notice_names_the_recorded_comparison_and_its_block_count(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _task_root(tmp_path)
    _environment(config, LEAF, _binding(_record(7, code_candidate="b" * 40)))
    captured = _record(1, code_candidate="e" * 40, memory_candidate="f" * 40)
    _write_record(config, _record(9, code_candidate="e" * 40, memory_candidate="f" * 40))
    counter = FakeCounter(4)

    notice = notice_for(
        config,
        _selection(),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=FakeOwner(captured), counter=counter),
    )

    assert notice is not None
    assert notice.kind == "comparison-moved"
    assert notice.from_number == 7
    assert notice.to_number == 9
    assert notice.blocks_changed == 4
    assert notice.update_offered is True
    assert notice.text == "task moved: #7 -> #9, 4 blocks changed"
    record = notice.as_record()
    assert (record["kind"], record["from"], record["to"]) == ("comparison-moved", 7, 9)
    assert (record["blocksChanged"], record["updateOffered"]) == (4, True)
    assert record["text"] == notice.text


def test_an_unrecorded_capture_reads_new_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _task_root(tmp_path)
    _environment(config, LEAF, _binding(_record(7)))
    captured = _record(1, code_candidate="e" * 40)

    notice = notice_for(
        config,
        _selection(),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=FakeOwner(captured), counter=FakeCounter(2)),
    )

    assert notice is not None
    assert notice.to_number is None
    assert notice.text == "task moved: #7 -> new state, 2 blocks changed"


def test_a_binding_that_has_not_moved_earns_no_notice(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _task_root(tmp_path)
    held = _record(7)
    _environment(config, LEAF, _binding(held))
    counter = FakeCounter(3)

    notice = notice_for(
        config,
        _selection(),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=FakeOwner(held), counter=counter),
    )

    assert notice is None
    assert counter.calls == 0


def test_an_unreadable_count_is_no_notice_rather_than_a_zero(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _task_root(tmp_path)
    _environment(config, LEAF, _binding(_record(7)))
    captured = _record(1, code_candidate="e" * 40)

    notice = notice_for(
        config,
        _selection(),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=FakeOwner(captured), counter=FakeCounter(None)),
    )

    assert notice is None


def test_a_leaf_without_an_environment_has_no_notice(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _task_root(tmp_path)

    notice = notice_for(
        config,
        _selection(),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=FakeOwner(_record(1)), counter=FakeCounter(1)),
    )

    assert notice is None


# --- the planning leaf's master notice -----------------------------------------------------------


def _master_pair(tmp_path: Path) -> tuple[McpRuntimeConfig, str, str]:
    """Two real repositories on one branch, and a contract naming that branch."""

    code = _repository(tmp_path, "repo", "ar/master")
    memory = _repository(tmp_path, "memory", "ar/master")
    code_tip = _commit(code, "code base", {"src/app.py": "one\n"})
    memory_tip = _commit(
        memory,
        f"memory base\n\nCode-Commit: {code_tip}",
        {"knowledge/layout.json": '{"conversion": "1"}', "knowledge/README.md": "base\n"},
    )
    _task_root(tmp_path)
    (tmp_path / "tasks" / REPO / TASK / "series-contract.md").write_text(
        "---\ncode:\n  source_branch: ar/master\nmemory:\n  source_branch: ar/master\n",
        encoding="utf-8",
    )
    return _config(tmp_path), code_tip, memory_tip


def _planned(code_tip: str, memory_tip: str) -> ExplainerBinding:
    record = planned_comparison(
        PlannedComparisonTips(
            task_id=TASK,
            leaf_id=PLANNING,
            code_repository=str(Path("/repo/code")),
            code_commit=code_tip,
            code_tree=_tree_of(code_tip),
            memory_repository="/repo/memory",
            memory_commit=memory_tip,
            memory_tree=_tree_of(memory_tip),
        )
    )
    return _binding(
        record,
        planned=PlannedIdentity(
            code_tip=code_tip,
            memory_tip=memory_tip,
            code_tree=_tree_of(code_tip),
            memory_tree=_tree_of(memory_tip),
        ),
    )


def _tree_of(commit: str) -> str:
    """A well-formed tree id; the notice compares tip commits, never these trees."""

    return hashlib.sha1(commit.encode()).hexdigest()


def test_a_planning_leaf_shows_the_master_tips_and_captures_nothing(tmp_path: Path) -> None:
    config, code_tip, memory_tip = _master_pair(tmp_path)
    _environment(config, PLANNING, _planned(code_tip, memory_tip))
    assert master_tips(config, _selection(PLANNING)) == (code_tip, memory_tip)
    moved = _commit(Path(tmp_path / "repo"), "the master moved", {"src/app.py": "two\n"})
    owner = FakeOwner(_record(1))

    notice = notice_for(
        config,
        _selection(PLANNING),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=owner, counter=FakeCounter(0)),
    )

    assert owner.calls == 0
    assert notice is not None
    assert notice.kind == "master-moved"
    assert (notice.from_number, notice.to_number, notice.blocks_changed) == (None, None, 0)
    assert notice.update_offered is True
    assert (notice.commit_from, notice.commit_to) == (code_tip, moved)
    assert notice.text == f"master moved: {code_tip} -> {moved}"


def test_a_planning_leaf_with_unmoved_tips_has_no_notice(tmp_path: Path) -> None:
    config, code_tip, memory_tip = _master_pair(tmp_path)
    _environment(config, PLANNING, _planned(code_tip, memory_tip))

    notice = notice_for(
        config,
        _selection(PLANNING),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=FakeOwner(_record(1))),
    )

    assert notice is None


def test_a_planned_moved_notice_follows_the_builds_declared_contract_without_fallback(
    tmp_path: Path,
) -> None:
    """S23: the planned watcher resolves the master's declared non-default contract -- the same
    one the build binds -- and a moved tip shows on a page refresh with no capture, no message
    and no record."""

    code = _repository(tmp_path, "repo", "ar/master")
    memory = _repository(tmp_path, "memory", "ar/master")
    code_tip = _commit(code, "code base", {"src/app.py": "one\n"})
    memory_tip = _commit(
        memory,
        f"memory base\n\nCode-Commit: {code_tip}",
        {"knowledge/layout.json": '{"conversion": "1"}', "knowledge/README.md": "base\n"},
    )
    task_root = _task_root(tmp_path)
    # The master declares a non-default contract, and the default file is absent: a reader that
    # guessed the fallback name would find no contract at all and could never notice the move.
    (task_root / "task.json").write_text(
        json.dumps(
            {"seriesContractPath": "my-series.md", "executionTopology": {"masterNature": "atomic"}}
        ),
        encoding="utf-8",
    )
    (task_root / "my-series.md").write_text(
        "---\ncode:\n  source_branch: ar/master\nmemory:\n  source_branch: ar/master\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)

    # The build's own resolution reads the same declared contract and its lines.
    contract = resolve_master_contract(config.coordination_root, REPO, TASK)
    assert contract is not None and contract.name == "my-series.md"
    organizational = master_is_organizational(config.coordination_root, REPO, TASK)
    code_lines, memory_lines = contract_branch_lines(
        contract.read_text(encoding="utf-8"), organizational=organizational
    )
    assert resolve_line_commit(Path(tmp_path / "repo"), code_lines) == code_tip
    assert resolve_line_commit(Path(tmp_path / "memory"), memory_lines) == memory_tip

    _environment(config, PLANNING, _planned(code_tip, memory_tip))
    assert master_tips(config, _selection(PLANNING)) == (code_tip, memory_tip)

    moved = _commit(Path(tmp_path / "repo"), "the master moved", {"src/app.py": "two\n"})
    owner = FakeOwner(_record(1))
    notice = notice_for(
        config,
        _selection(PLANNING),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=owner, counter=FakeCounter(0)),
    )

    assert owner.calls == 0
    assert notice is not None and notice.kind == "master-moved"
    assert (notice.commit_from, notice.commit_to) == (code_tip, moved)
    assert notice.update_offered is True
    # A page refresh that notices the move captures, sends, holds and records nothing.
    directory = comparison_directory(task_root, PLANNING)
    assert not directory.is_dir() or list(directory.glob("*.json")) == []
    assert not (tmp_path / "notes").exists()


# --- the turn-end trigger ------------------------------------------------------------------------


def test_turn_ends_skip_a_planning_leaf_and_check_a_recorded_one(tmp_path: Path) -> None:
    config, code_tip, memory_tip = _master_pair(tmp_path)
    _environment(config, PLANNING, _planned(code_tip, memory_tip))
    _environment(config, LEAF, _binding(_record(7)))
    folder = tmp_path / "explainer" / REPO / TASK
    (folder / f"{LEAF}.generation").write_text("1\n", encoding="utf-8")
    (folder / f"{PLANNING}.generation").write_text("1\n", encoding="utf-8")
    seats = {
        agent_id(leaf_workspace(config, REPO, TASK, LEAF), 1): _selection(),
        agent_id(leaf_workspace(config, REPO, TASK, PLANNING), 1): _selection(PLANNING),
    }
    by_leaf = {selection: agent for agent, selection in seats.items()}
    owner = FakeOwner(_record(1, code_candidate="e" * 40))
    watch = MoveNoticeWatch(
        config,
        WATCHER_PORTS,
        options=WatchOptions(
            measurement=ComparisonMeasurement(owner=owner, counter=FakeCounter(4)),
            channel=MoveNoticeChannel(),
            cooldown=0,
        ),
    )

    assert set(watch.seats()) == set(seats)
    assert watch.after_turn_ends([by_leaf[_selection(PLANNING)]]) == []
    assert owner.calls == 0

    published = watch.after_turn_ends([by_leaf[_selection()]])

    assert owner.calls == 1
    assert [notice.kind for notice in published] == ["comparison-moved"]
    assert watch.channel.latest(_selection()) == published[0]


def test_an_unrelated_turn_end_is_not_this_watchers(tmp_path: Path) -> None:
    config, _code_tip, _memory_tip = _master_pair(tmp_path)
    _environment(config, LEAF, _binding(_record(7)))
    owner = FakeOwner(_record(1, code_candidate="e" * 40))
    watch = MoveNoticeWatch(
        config,
        WATCHER_PORTS,
        options=WatchOptions(
            measurement=ComparisonMeasurement(owner=owner, counter=FakeCounter(1)),
            cooldown=0,
        ),
    )

    assert watch.after_turn_ends(["not-an-explainer-seat"]) == []
    assert owner.calls == 0


# --- nothing is sent, held or written ------------------------------------------------------------


def test_no_path_sends_holds_or_writes_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, code_tip, memory_tip = _master_pair(tmp_path)
    _environment(config, PLANNING, _planned(code_tip, memory_tip))
    _environment(config, LEAF, _binding(_record(7)))
    _commit(Path(tmp_path / "repo"), "the master moved", {"src/app.py": "two\n"})
    calls: list[str] = []

    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        calls.append("sent")
        raise AssertionError("the moved watcher sent or held a message")

    monkeypatch.setattr(role_held_messages, "hold_message", _refuse)
    monkeypatch.setattr(role_held_messages, "deliver_ended_turns", _refuse)
    monkeypatch.setattr(role_held_messages, "drain_held", _refuse)
    workspace = leaf_workspace(config, REPO, TASK, LEAF)
    (tmp_path / "explainer" / REPO / TASK / f"{LEAF}.generation").write_text(
        "1\n", encoding="utf-8"
    )
    store = role_held_messages.store_path_for(workspace, agent_id(workspace, 1))
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text('{"schema": "ar-held-messages/v1", "messages": []}\n', encoding="utf-8")
    # The queue's layout is owned by ``held_store_layout`` since MIK-R200 rule 3; the delivery
    # line no longer re-exports it. The watcher's "nothing was written" check reads the same file.
    queue = held_store_layout.held_queue_path(config)
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text('{"schema": "ar-held-queue/v1", "receipts": []}\n', encoding="utf-8")
    held = {path: path.read_bytes() for path in (store, queue)}
    before = _tree_digest(tmp_path / "tasks")
    watch = MoveNoticeWatch(
        config,
        WATCHER_PORTS,
        options=WatchOptions(
            measurement=ComparisonMeasurement(owner=FakeOwner(_record(1)), counter=FakeCounter(1)),
            channel=MoveNoticeChannel(),
            cooldown=0,
        ),
    )

    watch.page_opened(_selection(PLANNING))
    watch.page_opened(_selection())
    watch.after_turn_ends([agent_id(workspace, 1)])

    assert calls == []
    assert _tree_digest(tmp_path / "tasks") == before
    for path, body in held.items():
        assert path.read_bytes() == body


# --- the event shape -----------------------------------------------------------------------------


def test_the_event_carries_the_leaf_and_the_notice() -> None:
    notice = MovedNotice("comparison-moved", 7, 9, 4, True)

    event = notice_event(_selection(), notice)

    assert event["repo"] == REPO
    assert (event["master"], event["leaf"]) == (TASK, LEAF)
    assert event["kind"] == "comparison-moved"
    assert event["text"] == notice.text


def test_the_channel_replays_the_latest_and_hands_a_publish_to_a_listener() -> None:
    async def run() -> None:
        channel = MoveNoticeChannel()
        first = MovedNotice("comparison-moved", 7, 9, 4, True)
        second = MovedNotice("comparison-moved", 7, None, 2, True)
        channel.publish(_selection(), first)

        async def until(target: MovedNotice) -> MovedNotice:
            async for notice in channel.listen(_selection()):
                if notice == target:
                    return notice
            raise AssertionError("the listener ended")

        replay = await asyncio.wait_for(until(first), HANG_GUARD_SECONDS)
        assert replay == first
        listener = asyncio.create_task(until(second))
        await asyncio.sleep(0)
        assert channel.publish(_selection(), first) is False
        assert channel.publish(_selection(), second) is True
        assert await asyncio.wait_for(listener, HANG_GUARD_SECONDS) == second

    asyncio.run(run())


# --- the capture records nothing -----------------------------------------------------------------


def _contract(tmp_path: Path, code: Path, memory: Path) -> WorktreeContract:
    task_root = _task_root(tmp_path)
    code_base = _git(code, "rev-parse", "HEAD")
    memory_base = _git(memory, "rev-parse", "HEAD")
    return WorktreeContract(
        task_id=TASK,
        task_name=TASK,
        repo_name=REPO,
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=tmp_path,
        task_root=task_root,
        contract_path=task_root / "enclosures" / LEAF / "series-contract.md",
        task_artifact=task_root / "task.md",
        worktree_group=task_root / "enclosures",
        code_repo_path=code,
        code_source_branch="main",
        code_work_branch="ar/work",
        code_base_commit=code_base,
        code_worktree=code,
        memory_repo_path=memory,
        memory_source_branch="ar/memory",
        memory_work_branch="ar/memory-work",
        memory_base_commit=memory_base,
        memory_worktree=memory,
        kind="leaf",
        leaf_id=LEAF,
    )


def test_the_capture_writes_objects_and_no_ref_or_record(tmp_path: Path) -> None:
    code = _repository(tmp_path, "repo", "main")
    base = _commit(code, "code base", {"src/app.py": "one\n"})
    memory = _repository(tmp_path, "memory", "ar/memory")
    _commit(
        memory,
        f"memory base\n\nCode-Commit: {base}",
        {"knowledge/layout.json": '{"conversion": "1"}', "knowledge/README.md": "base\n"},
    )
    (code / "src" / "app.py").write_text("two\n", encoding="utf-8")
    (memory / "knowledge" / "README.md").write_text("changed\n", encoding="utf-8")
    config = _config(tmp_path)
    contract = _contract(tmp_path, code, memory)
    status = (
        _git(code, "status", "--porcelain"),
        _git(memory, "status", "--porcelain"),
    )
    owner = GitComparisonOwner(config, WATCHER_PORTS, resolve_contract=lambda _selection: contract)

    captured = owner.capture(_selection())

    assert isinstance(captured, CapturedComparison)
    assert captured.record.code_candidate.tree != captured.record.code_base.tree
    assert captured.record.memory_candidate.tree != captured.record.memory_base.tree
    for repository, tree in (
        (code, captured.record.code_candidate.tree),
        (memory, captured.record.memory_candidate.tree),
    ):
        assert _git(repository, "cat-file", "-t", tree) == "tree"
        assert _git(repository, "for-each-ref", "refs/ar") == ""
    assert not comparison_directory(tmp_path / "tasks" / REPO / TASK, LEAF).exists()
    assert (code / "src" / "app.py").read_text(encoding="utf-8") == "two\n"
    assert (memory / "knowledge" / "README.md").read_text(encoding="utf-8") == "changed\n"
    assert (_git(code, "status", "--porcelain"), _git(memory, "status", "--porcelain")) == status


def test_the_master_notice_follows_the_line_the_planned_binding_came_from(
    tmp_path: Path,
) -> None:
    """A master's own work branch moves while its parent line does not: the notice names the work."""

    code = _repository(tmp_path, "repo", "ar/parent")
    memory = _repository(tmp_path, "memory", "ar/parent")
    parent_code = _commit(code, "the parent line", {"src/app.py": "base\n"})
    parent_memory = _commit(
        memory,
        f"the parent memory\n\nCode-Commit: {parent_code}",
        {"knowledge/README.md": "base\n"},
    )
    _git(code, "checkout", "-q", "-b", "ar/master")
    _git(memory, "checkout", "-q", "-b", "ar/master")
    code_tip = _commit(code, "the master's first leaf", {"src/app.py": "one\n"})
    memory_tip = _commit(
        memory,
        f"the master's memory\n\nCode-Commit: {code_tip}",
        {"knowledge/README.md": "one\n"},
    )
    _task_root(tmp_path)
    (tmp_path / "tasks" / REPO / TASK / "series-contract.md").write_text(
        "---\ncode:\n  source_branch: ar/parent\n  work_branch: ar/master\n"
        "memory:\n  source_branch: ar/parent\n  work_branch: ar/master\n",
        encoding="utf-8",
    )
    config = _config(tmp_path)
    _environment(config, PLANNING, _planned(code_tip, memory_tip))
    moved = _commit(Path(tmp_path / "repo"), "the master's second leaf", {"src/app.py": "two\n"})

    tips = master_tips(config, _selection(PLANNING), _planned(code_tip, memory_tip).planned)
    notice = notice_for(
        config,
        _selection(PLANNING),
        WATCHER_PORTS,
        measurement=ComparisonMeasurement(owner=FakeOwner(_record(1))),
    )

    assert tips == (moved, memory_tip)
    assert _git(Path(tmp_path / "repo"), "rev-parse", "ar/parent") == parent_code
    assert _git(Path(tmp_path / "memory"), "rev-parse", "ar/parent") == parent_memory
    assert notice is not None
    assert (notice.commit_from, notice.commit_to) == (code_tip, moved)


def test_the_whole_watcher_reads_a_live_leaf_and_records_nothing(tmp_path: Path) -> None:
    """The real owner, the real counter and a real pair: one notice, no pin, no record."""

    code = _repository(tmp_path, "repo", "main")
    base = _commit(code, "code base", {"src/app.py": "one\n"})
    memory = _repository(tmp_path, "memory", "ar/memory")
    _commit(
        memory,
        f"memory base\n\nCode-Commit: {base}",
        {"knowledge/layout.json": '{"conversion": "1"}', "knowledge/README.md": "base\n"},
    )
    (code / "src" / "app.py").write_text("two\n", encoding="utf-8")
    config = _config(tmp_path)
    contract = _contract(tmp_path, code, memory)
    record = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=7,
        code_base=ReviewTreeSide(repository=str(code), tree=_git(code, "rev-parse", "HEAD^{tree}")),
        code_candidate=ReviewTreeSide(
            repository=str(code), tree=_git(code, "rev-parse", "HEAD^{tree}")
        ),
        memory_base=ReviewTreeSide(
            repository=str(memory), tree=_git(memory, "rev-parse", "HEAD^{tree}")
        ),
        memory_candidate=ReviewTreeSide(
            repository=str(memory), tree=_git(memory, "rev-parse", "HEAD^{tree}")
        ),
        recorded_at=NOW,
    )
    _environment(config, LEAF, _binding(record))
    owner = GitComparisonOwner(config, WATCHER_PORTS, resolve_contract=lambda _selection: contract)

    notice = notice_for(
        config, _selection(LEAF), WATCHER_PORTS, measurement=ComparisonMeasurement(owner=owner)
    )

    assert notice is not None
    assert (notice.from_number, notice.to_number, notice.blocks_changed) == (7, None, 1)
    assert notice.text == "task moved: #7 -> new state, 1 blocks changed"
    assert _git(code, "for-each-ref", "refs/ar") == ""
    assert _git(memory, "for-each-ref", "refs/ar") == ""
    assert not comparison_directory(tmp_path / "tasks" / REPO / TASK, LEAF).exists()


def test_role_session_turn_end_bypasses_cooldown_and_caught_up_check_clears_channel(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    task_root = _task_root(tmp_path)
    held = _record(7)
    _environment(config, LEAF, _binding(held))

    exec_receipt = task_root / "notes" / "reports" / "paseo-native-executions" / f"{LEAF}.json"
    exec_receipt.parent.mkdir(parents=True, exist_ok=True)
    worker_agent = "worker-seat-183"
    exec_receipt.write_text(
        json.dumps(
            {
                "agentId": worker_agent,
                "role": "worker",
                "selection": {
                    "role": "worker",
                    "taskDocumentRef": {"repository": REPO, "path": f"{TASK}/{LEAF}.md"},
                },
            }
        ),
        encoding="utf-8",
    )

    moved_record = _record(1, code_candidate="e" * 40)
    owner = FakeOwner(held)
    channel = MoveNoticeChannel()
    watch = MoveNoticeWatch(
        config,
        WATCHER_PORTS,
        options=WatchOptions(
            measurement=ComparisonMeasurement(owner=owner, counter=FakeCounter(3)),
            channel=channel,
            cooldown=60.0,
        ),
    )

    assert watch.seats().get(worker_agent) == _selection()
    assert watch.page_opened(_selection()) is None
    assert owner.calls == 1

    owner.record = moved_record
    published = watch.after_turn_ends([worker_agent])
    assert owner.calls == 2
    assert len(published) == 1
    assert channel.latest(_selection()) == published[0]

    owner.record = held
    assert watch.after_turn_ends([worker_agent]) == []
    assert channel.latest(_selection()) is None

    enclosure_contract = tmp_path / "enclosures" / TASK / "series-contract.md"
    enclosure_contract.parent.mkdir(parents=True, exist_ok=True)
    code = _repository(tmp_path, "repo-ext", "ar/master")
    memory = _repository(tmp_path, "mem-ext", "ar/master")
    c_tip = _commit(code, "c1", {"a.py": "1\n"})
    m_tip = _commit(memory, f"m1\n\nCode-Commit: {c_tip}", {"README.md": "1\n"})
    enclosure_contract.write_text(
        "---\ncode:\n  work_branch: ar/master\nmemory:\n  work_branch: ar/master\n---\n",
        encoding="utf-8",
    )
    (task_root / "task.json").write_text(
        json.dumps({"id": TASK, "seriesContractPath": str(enclosure_contract)}),
        encoding="utf-8",
    )
    ext_config = McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        transcript_root=tmp_path / "transcripts",
        repositories={
            REPO: RepositoryScope(repo_id=REPO, path=code, memory_root=memory),
        },
    )
    assert master_tips(ext_config, _selection(PLANNING)) == (c_tip, m_tip)


def test_a_bound_view_is_read_through_its_record_not_as_a_bare_record() -> None:
    """The explainer's Update hands ``changes_between`` the bound ``ReviewTrees``, not a record.

    Reading ``code_candidate`` straight off the view raised ``AttributeError`` and answered every
    Update of a page whose comparison had moved with a 500; the record is taken from either shape.
    """

    binding = _binding(_record(1)).binding
    record = _record(2)
    view = changes_between(binding, record)
    assert view.record.code_candidate.tree == record.code_candidate.tree

    again = changes_between(binding, view)
    assert again.record.code_candidate.tree == record.code_candidate.tree
    assert again.record.memory_candidate.tree == record.memory_candidate.tree
