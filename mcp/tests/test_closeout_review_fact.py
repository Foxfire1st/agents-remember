"""The memory review's fact is recorded in the memory at closeout.

A recorded memory round stores the tree the Reviewer named, the stamp of the agent recording it and
the stamp of the agent that handed the memory change over, read when the round is recorded from the
hand-over filed in the leaf's messages and its sender's launch receipt. The closeout writes the fact
of the last recorded round into the leaf's history file, in the same write that closes it and before
the derived files are renewed, only when that round passed, names its verdict and names the very
memory tree being closed. Otherwise it writes no fact and says so; a closed file is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.knowledge_gate.direct import _closing_of
from agents_remember.application.task_docs.review_round_stamp import memory_round_stamp
from agents_remember.application.task_docs.task_doc_route_review import (
    _record_task_review_bound,
)
from agents_remember.cli.role_message_files import (
    MessageContent,
    leaf_messages_folder,
    render_message_file,
    store_message_file,
    utc_stem,
)
from agents_remember.memory.conversion.task_review_facts import last_passed_memory_round
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import parse_history_document
from agents_remember.models.knowledge_files.history import ReviewFact
from agents_remember.models.knowledge_files.record_state import RevisionStates
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.store import read_task_doc
from agents_remember.worktrees.knowledge_gate import close_owner_history
from agents_remember.worktrees.modules import closeout as closeout_module
from agents_remember.worktrees.modules import closeout_external
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import VerifiedChange
from agents_remember.worktrees.queue.closeout_recovery import MemoryCloseoutOutcome
from agents_remember.worktrees.review_fact_source import (
    NO_PASS_SENTENCE,
    UNRECORDED_HANDOVER,
    UNSTAMPED_ROUND,
    leaf_review_fact,
)
from agents_remember.worktrees.review_handover import NO_REGISTERED_HANDOVER, several_registered
from agents_remember.worktrees.review_history import (
    RoundStamps,
    begin_task_review,
    record_task_review,
)
from agents_remember.worktrees.review_note import committed_review_note
from agents_remember.worktrees.worktree_contract import WorktreeContract
from knowledge_review_fact_test_support import written
from test_review_state import _document
from test_task_doc_review_public import _call, _create, _leaf_doc
from test_task_document import _config

MASTER = "261112-KNO"
LEAF = "261112-KNO-L3"
FOLDER = "261112_knowledge"
STATED_TREE = "a" * 40
HISTORY = f"knowledge/history/{LEAF}.json"
ATTEMPT_2 = f"knowledge/history/{LEAF}-attempt-2.json"
RECORD = "INV-K8ZY4F"
NOW = "2026-11-19T20:00:00+01:00"


def _stamp(role: str, model: str, seat: str) -> dict[str, Any]:
    return {
        "by": "agent",
        "role": role,
        "harness": "claude",
        "model": model,
        "effort": "high",
        "seat": seat,
        "task": MASTER,
        "leaf": LEAF,
        "at": "2026-11-19T19:00:00+01:00",
    }


REVIEWER = _stamp("reviewer", "gpt-6.1", "01KREVIEWER")
CURATOR = _stamp("curator", "gpt-5.5", "01KCURATOR")


def _round(number: int, *, passed: bool, **facts: Any) -> dict[str, Any]:
    return {
        "round": number,
        "kind": "memory",
        "findingsRemain": not passed,
        "verdictRef": f"reports/memory-{number}.md",
        **facts,
    }


def _git(repository: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


class _World:
    """A coordination root with a master and its leaf, and the leaf's memory checkout (a Git
    worktree, so the tree a closeout closes can be captured)."""

    def __init__(self, root: Path) -> None:
        self.coordination = root / "coordination"
        self.folder = self.coordination / "tasks" / "agents-remember" / FOLDER
        self.folder.mkdir(parents=True)
        self.memory = root / "memory"
        (self.memory / "knowledge" / "history").mkdir(parents=True)
        for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "t"]):
            _git(self.memory, *args)
        (self.memory / "README.md").write_text("memory\n", encoding="utf-8")
        _git(self.memory, "add", "-A")
        _git(self.memory, "commit", "-qm", "memory")
        self._document("task.json", {"id": MASTER, "slug": "task", "kind": "master"})
        self.leaf(rounds=[])

    def _document(self, name: str, fields: dict[str, Any]) -> None:
        document = {
            "schema": "ar-task-document/v1",
            "title": "Fixture",
            "repo": "agents-remember",
            "createdAt": "2026-11-19T00:00+01:00",
            **fields,
        }
        (self.folder / name).write_text(json.dumps(document), encoding="utf-8")

    def leaf(self, *, rounds: list[dict[str, Any]]) -> None:
        state = {"round": len(rounds), "pending": False, "recordedRounds": rounds}
        self._document(
            "leaf.json",
            {
                "id": LEAF,
                "slug": "leaf",
                "kind": "subTask",
                "master": "task.md",
                **({"memoryReviewState": state} if rounds else {}),
            },
        )

    def tree(self) -> str:
        """The memory checkout's working tree as a commit of every change would record it."""

        with tempfile.TemporaryDirectory() as scratch:
            env = {**os.environ, "GIT_INDEX_FILE": f"{scratch}/index"}
            _git(self.memory, "read-tree", "HEAD", env=env)
            _git(self.memory, "add", "-A", env=env)
            return _git(self.memory, "write-tree", env=env)

    @property
    def holder(self) -> Path:
        return self.folder / "notes" / "reports" / "paseo-native-executions"

    def receipt(
        self, role: str, request_id: str, model: str, agent_id: str = "", **facts: str
    ) -> Path:
        """Register a seat on a leaf of the master: its launch receipt in the task folder.

        ``facts`` may name the receipt's ``status`` (running) and the ``leaf`` document it names.
        """

        self.holder.mkdir(parents=True, exist_ok=True)
        receipt = {
            "schema": "ar-role-execution/v1",
            "requestId": request_id,
            "role": role,
            "agentId": agent_id or f"agent-{request_id}",
            "status": facts.get("status", "running"),
            "selection": {
                "role": role,
                "taskDocumentRef": {
                    "repository": "agents-remember",
                    "path": f"{FOLDER}/{facts.get('leaf', 'leaf.json')}",
                },
            },
            "agent": {"id": "codex", "model": model, "effort": "xhigh"},
        }
        path = self.holder / f"{role}-{request_id}.json"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        return path

    def supersede(self, receipt: Path) -> None:
        """Replace a seat: its receipt moves to the holder's history, as a new start moves it."""

        (self.holder / "history").mkdir(exist_ok=True)
        shutil.move(receipt, self.holder / "history" / receipt.name)

    def message(
        self, sender_role: str, sender: str, recipient_role: str, recipient: str, when: datetime
    ) -> Path:
        """A message filed in the leaf's message folder as the product files one."""

        content = MessageContent(
            when=when,
            sender_line=f"From {sender_role} · {LEAF} · agent {sender}",
            sender_task=LEAF,
            recipient_role=recipient_role,
            recipient_id=recipient,
            recipient_task=LEAF,
            message_id=f"message-{sender}-{when.timestamp()}",
            text=f"Memory candidate tree {STATED_TREE}.",
        )
        return store_message_file(
            leaf_messages_folder(self.folder, f"{FOLDER}/leaf.json"),
            utc_stem(sender_role, sender, recipient_role, recipient, when=when),
            render_message_file(content),
        )

    @property
    def contract(self) -> WorktreeContract:
        return cast(
            WorktreeContract,
            SimpleNamespace(
                kind="leaf",
                coordination_root=self.coordination,
                repo_name="agents-remember",
                # A leaf's enclosure contract names the master's task.md and the master's
                # folder, never the leaf's own document: the fact is found by the leaf's id.
                task_root=self.folder,
                task_artifact=self.folder / "task.md",
                memory_worktree=self.memory,
                memory_repo_path=None,
                leaf_id=LEAF,
                ledger_path=self.memory / "memory.md",
            ),
        )

    def history(self, document: dict[str, Any], name: str = HISTORY) -> bytes:
        text = canonical_text(document)
        (self.memory / name).write_text(text, encoding="utf-8")
        return text.encode("utf-8")

    def read(self, name: str = HISTORY) -> dict[str, Any]:
        return json.loads((self.memory / name).read_text(encoding="utf-8"))

    def states(self) -> RevisionStates:
        files = sorted((self.memory / "knowledge" / "history").glob("*.json"))
        return RevisionStates.of(
            parse_history_document(
                f"knowledge/history/{path.name}", path.read_text(encoding="utf-8")
            )
            for path in files
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def _open_history(**fields: Any) -> dict[str, Any]:
    return {"schema": "ar-history/v1", "leaf": LEAF, "closed": False, "rows": [], **fields}


def _closeout(world: _World, refresh: Any = None) -> MemoryCloseoutOutcome:
    """The external leaf closeout's memory phase, with only its Git and refresh steps stubbed."""

    renewed = closeout_external._ExternalMemoryRefresh([], [], [], {})
    change = VerifiedChange(
        commit="c" * 40, commit_date="2026-11-19T00:00:00+00:00", changed_paths=[]
    )
    args = WorktreeArgs(contract_path=world.folder / "contract.md")
    with (
        mock.patch.object(closeout_external, "leaf_memory_converted", return_value=True),
        mock.patch.object(
            closeout_external,
            "_refresh_external_memory",
            side_effect=refresh,
            return_value=renewed,
        ),
        mock.patch.object(
            closeout_external, "_commit_memory_content", return_value=("d" * 40, False)
        ),
        mock.patch.object(closeout_external, "_report_memory_commit"),
        mock.patch.object(closeout_external, "refresh_memory_cache", return_value={}),
        mock.patch.object(closeout_external, "leaf_review_fact", wraps=_fixed_clock_fact),
    ):
        return closeout_external.external_closeout_commits(
            world.contract, args, cast(Any, None), change
        )


def _fixed_clock_fact(contract: WorktreeContract) -> ReviewFact | None:
    return leaf_review_fact(contract, now=lambda: NOW)


def _passed(world: _World, number: int = 1, **facts: Any) -> dict[str, Any]:
    """A genuine passed round: it names the memory tree the checkout holds now."""

    return _round(
        number,
        passed=True,
        verdict="pass",
        memoryTree=world.tree(),
        recordedBy=REVIEWER,
        handedOverBy=CURATOR,
        **facts,
    )


# --------------------------------------------------------------------------------------------------
# The recorded round
# --------------------------------------------------------------------------------------------------


def test_a_memory_round_stores_the_passed_tree_and_its_recorder() -> None:
    memory = {"reviewKind": "memory"}
    begun = begin_task_review(_document(), memory)
    recorded = record_task_review(
        begun,
        {**memory, "verdict": "pass", "findings": [], "candidateTree": STATED_TREE},
        stamps=RoundStamps(REVIEWER, CURATOR),
    )
    assert recorded.memoryReviewState is not None
    (row,) = recorded.memoryReviewState.recordedRounds
    assert (row.memoryTree, row.verdict) == (STATED_TREE, "pass")
    assert row.recordedBy == REVIEWER
    assert row.handedOverBy == CURATOR
    assert row.model_dump(mode="json")["recordedBy"] == REVIEWER

    code = record_task_review(
        begin_task_review(_document()),
        {"verdict": "pass", "findings": [], "candidateTree": STATED_TREE},
    )
    assert code.reviewState is not None
    (code_row,) = code.reviewState.recordedRounds
    assert code_row.memoryTree is None
    assert code_row.model_dump(mode="json") == {
        "round": 1,
        "kind": "code",
        "findingsRemain": False,
        "verdictRef": None,
    }


def test_a_record_only_check_renews_the_recorder_and_keeps_the_tree() -> None:
    memory = {"reviewKind": "memory"}
    recorded = record_task_review(
        begin_task_review(_document(), memory),
        {**memory, "verdict": "pass", "findings": [], "candidateTree": STATED_TREE},
        actual_candidate="identity",
        stamps=RoundStamps(REVIEWER),
    )
    again = {**REVIEWER, "at": "2026-11-19T19:30:00+01:00"}
    checked = record_task_review(
        recorded,
        {**memory, "verdict": "pass", "remainingFindingIds": [], "candidateTree": STATED_TREE},
        actual_candidate="identity",
        stamps=RoundStamps(again),
    )
    assert checked.memoryReviewState is not None
    (row,) = checked.memoryReviewState.recordedRounds
    assert (row.memoryTree, row.recordedBy) == (STATED_TREE, again)


def test_record_review_stamps_the_recording_reviewer_from_its_binding(tmp_path: Path) -> None:
    """The public route stores the recorder's stamp from its binding, the verdict reference and
    the tree the Reviewer named. The named tree is a statement only: the closeout binds it to the
    memory it closes (``test_a_missing_or_different_tree_or_verdict_gives_no_fact``)."""

    config = _config(tmp_path)
    _create(config)
    folder = config.coordination_root / "tasks" / "agents-remember" / "review-api"
    holder = folder / "notes" / "reports" / "paseo-native-executions"
    holder.mkdir(parents=True)
    request_id = "01K" + "7" * 23
    (holder / "reviewer.json").write_text(
        json.dumps(
            {
                "schema": "ar-role-execution/v1",
                "requestId": request_id,
                "role": "reviewer",
                "agent": {"id": "codex", "model": "gpt-6.1", "effort": "xhigh"},
            }
        ),
        encoding="utf-8",
    )
    binding = AgentBinding(
        agent_id="01J" + "7" * 23,
        role="reviewer",
        request_id=request_id,
        report_path=(tmp_path / "report.md").as_posix(),
        task_ref=TaskDocumentRef(repository="agents-remember", path="review-api/review_api.json"),
    )
    _call(config, "begin_review", review={"reviewKind": "memory"})
    with mock.patch(
        "agents_remember.application.writer_stamp_source.read_agent_binding",
        return_value=binding,
    ):
        recorded = _call(
            config,
            "record_review",
            review={
                "reviewKind": "memory",
                "verdict": "pass",
                "findings": [],
                "candidateTree": STATED_TREE,
                "verdictRef": "notes/reports/review-api-memory-verdict.md",
            },
        )
    (answered,) = recorded["memoryReviewState"]["recordedRounds"]
    state = _leaf_doc(config).memoryReviewState
    assert state is not None
    (row,) = state.recordedRounds
    assert row.model_dump(mode="json") == answered
    assert (row.memoryTree, row.verdictRef) == (
        STATED_TREE,
        "notes/reports/review-api-memory-verdict.md",
    )
    assert row.recordedBy is not None
    assert {key: row.recordedBy[key] for key in ("role", "harness", "model", "effort", "seat")} == {
        "role": "reviewer",
        "harness": "codex",
        "model": "gpt-6.1",
        "effort": "xhigh",
        "seat": request_id,
    }
    assert (row.recordedBy["task"], row.recordedBy["leaf"]) == ("review-series", "review-api")
    # No Curator or Consolidator is registered on this leaf: the round says so for good.
    assert row.handedOverBy is not None
    assert set(row.handedOverBy["unknown"].values()) == {NO_REGISTERED_HANDOVER}


def test_no_review_input_names_the_recorder(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _create(config)
    _call(config, "begin_review", review={"reviewKind": "memory"})
    for named in ("recordedBy", "handedOverBy"):
        with pytest.raises(ValueError, match="unsupported fields"):
            _call(
                config,
                "record_review",
                review={"reviewKind": "memory", "verdict": "pass", "findings": [], named: {}},
            )


# --------------------------------------------------------------------------------------------------
# The fact written at closeout
# --------------------------------------------------------------------------------------------------


def test_the_closeout_writes_the_fact_before_the_derived_files_are_renewed(
    world: _World,
) -> None:
    world.history(_open_history())
    world.leaf(rounds=[_round(1, passed=False), _passed(world, 2)])
    seen: list[dict[str, Any]] = []
    outcome = _closeout(world, refresh=lambda *_: seen.append(world.read()) or mock.DEFAULT)

    closed = world.read()
    assert seen == [closed]  # the renewal ran on the closed file that holds the fact
    assert closed["closed"] is True
    review = closed["review"]
    assert (review["round"], review["verdictRef"]) == (2, "reports/memory-2.md")
    assert review["reviewer"] == REVIEWER
    assert review["handedOverBy"] == CURATOR
    assert outcome.review_note == ""


def test_the_fact_binds_the_real_reviewed_tree_and_its_verdict(world: _World) -> None:
    world.history(_open_history())
    passed_tree = world.tree()
    world.leaf(rounds=[_passed(world)])
    outcome = _closeout(world)
    review = world.read()["review"]
    assert (review["tree"], review["verdictRef"]) == (passed_tree, "reports/memory-1.md")
    assert outcome.review_note == ""


@pytest.mark.parametrize(
    "flaw", ["missing tree", "different tree", "missing verdict", " ", "\u200b", "v1\x00.md"]
)
def test_a_missing_or_different_tree_or_unusable_verdict_gives_no_fact(
    world: _World, flaw: str
) -> None:
    """A verdict reference counts only as ``usable_verdict_ref`` reads it, as in the conversion."""

    world.history(_open_history())
    round_ = _passed(world)
    if flaw == "missing tree":
        del round_["memoryTree"]
    elif flaw == "different tree":
        round_["memoryTree"] = STATED_TREE
    elif flaw == "missing verdict":
        del round_["verdictRef"]
    else:
        round_["verdictRef"] = flaw
    world.leaf(rounds=[round_])
    outcome = _closeout(world)
    closed = world.read()
    assert closed["closed"] is True  # the closeout itself goes on
    assert "review" not in closed
    assert outcome.review_note == NO_PASS_SENTENCE


def test_a_legacy_round_and_an_unrecorded_handover_read_unknown(world: _World) -> None:
    world.leaf(rounds=[_round(1, passed=True, memoryTree=world.tree())])
    fact = _fixed_clock_fact(world.contract)
    assert fact is not None
    assert fact.reviewer.role == "unknown"
    assert fact.reviewer.unknown is not None
    assert set(fact.reviewer.unknown.values()) == {UNSTAMPED_ROUND}
    assert fact.handed_over_by.role == "unknown"
    assert fact.handed_over_by.unknown is not None
    assert set(fact.handed_over_by.unknown.values()) == {UNRECORDED_HANDOVER}
    assert fact.handed_over_by.at == "unknown"  # no closeout time poses as the hand-over's
    assert (fact.reviewer.task, fact.reviewer.leaf) == (MASTER, LEAF)


def test_a_block_after_a_pass_leaves_no_fact(world: _World) -> None:
    world.leaf(rounds=[_passed(world), _round(2, passed=True, verdict="block")])
    assert _fixed_clock_fact(world.contract) is None
    world.leaf(rounds=[_round(1, passed=True, verdict="block", memoryTree=world.tree())])
    assert _fixed_clock_fact(world.contract) is None


def test_a_resumed_closeout_reads_the_sentence_from_the_committed_history(
    world: _World,
) -> None:
    repository = world.memory

    def committed(document: dict[str, Any]) -> str:
        world.history(document)
        _git(repository, "add", "-A")
        _git(repository, "commit", "-qm", "history")
        return _git(repository, "rev-parse", "HEAD")

    fact = {"round": 1, "reviewer": REVIEWER, "handedOverBy": REVIEWER}
    without = committed({**_open_history(), "closed": True})
    with_fact = committed({**_open_history(), "closed": True, "review": fact})
    still_open = committed(_open_history())
    assert committed_review_note(repository, without, LEAF) == NO_PASS_SENTENCE
    assert committed_review_note(repository, with_fact, LEAF) == ""
    assert committed_review_note(repository, still_open, LEAF) == ""
    assert committed_review_note(repository, without, "261112-KNO-L9") == ""


def test_without_a_passed_round_no_fact_is_written_and_the_closeout_says_so(
    world: _World,
) -> None:
    world.leaf(rounds=[_round(1, passed=False, recordedBy=REVIEWER)])
    world.history(_open_history())
    outcome = _closeout(world)

    closed = world.read()
    assert closed["closed"] is True
    assert "review" not in closed
    assert outcome.review_note == NO_PASS_SENTENCE

    class _Contract:
        contract_path = world.folder / "contract.md"

    with (
        mock.patch.object(closeout_module, "status_payload", return_value={}),
        mock.patch.object(closeout_module, "_memory_ledger_repair", return_value=None),
    ):
        payload = closeout_module._closed_result_payload(
            _Contract(),
            closeout_module._CloseoutResultFacts(
                code_commit="c" * 40, memory=outcome, integration_reopen={}, gate_guard=None
            ),
        )
    assert payload["summary"].endswith(NO_PASS_SENTENCE)
    assert payload["summary"].startswith("Closeout completed;")


def test_a_missing_history_file_is_created_closed_with_the_fact(world: _World) -> None:
    passed_tree = world.tree()
    world.leaf(rounds=[_passed(world)])
    outcome = _closeout(world)
    created = world.read()
    assert (created["closed"], created["rows"], created["review"]["tree"]) == (
        True,
        [],
        passed_tree,
    )
    assert outcome.review_note == ""


def test_an_open_file_keeps_no_review_it_was_not_closed_with(world: _World) -> None:
    world.leaf(rounds=[_round(1, passed=False)])
    planted = {"round": 1, "reviewer": REVIEWER, "handedOverBy": REVIEWER}
    world.history(_open_history(review=planted))
    _closeout(world)
    closed = world.read()
    assert closed["closed"] is True
    assert "review" not in closed


def test_an_already_closed_file_is_left_unchanged(world: _World) -> None:
    frozen = world.history({**_open_history(), "closed": True})
    world.leaf(rounds=[_passed(world)])
    outcome = _closeout(world)
    assert (world.memory / HISTORY).read_bytes() == frozen
    assert outcome.review_note == ""


def test_a_reopened_leafs_attempt_file_gets_its_own_fact(world: _World) -> None:
    world.history(_open_history(written=[written(RECORD, 1, LEAF)]))
    first_round = _passed(world)
    world.leaf(rounds=[first_round])
    _closeout(world)
    first = (world.memory / HISTORY).read_bytes()
    assert world.read()["review"]["tree"] == first_round["memoryTree"]

    world.history(_open_history(attempt=2, written=[written(RECORD, 2, LEAF)]), ATTEMPT_2)
    world.leaf(rounds=[first_round, _passed(world, 2)])
    _closeout(world)
    assert (world.memory / HISTORY).read_bytes() == first
    attempt = world.read(ATTEMPT_2)
    assert (attempt["closed"], attempt["attempt"], attempt["review"]["round"]) == (True, 2, 2)
    assert attempt["review"]["tree"] != first_round["memoryTree"]
    assert world.states().state_of(RECORD, 2, retired=False, writer=None) == "reviewed"


@pytest.mark.parametrize("later", [None, "block"])
def test_a_reopened_attempt_closed_without_its_own_pass_gets_no_fact(
    world: _World, later: str | None
) -> None:
    """The pass of the first attempt never speaks for the second, with or without a later block."""

    world.history(_open_history(written=[written(RECORD, 1, LEAF)]))
    first_round = _passed(world)
    world.leaf(rounds=[first_round])
    _closeout(world)
    first = (world.memory / HISTORY).read_bytes()

    world.history(_open_history(attempt=2, written=[written(RECORD, 2, LEAF)]), ATTEMPT_2)
    rounds = [first_round]
    if later == "block":
        rounds.append(_round(2, passed=False, verdict="block", memoryTree=world.tree()))
    world.leaf(rounds=rounds)
    outcome = _closeout(world)

    assert (world.memory / HISTORY).read_bytes() == first
    attempt = world.read(ATTEMPT_2)
    assert attempt["closed"] is True
    assert "review" not in attempt
    assert outcome.review_note == NO_PASS_SENTENCE
    states = world.states()
    assert states.state_of(RECORD, 2, retired=False, writer=None) == "proposed"
    assert states.state_of(RECORD, 1, retired=False, writer=None) == "reviewed"


def test_a_failed_renewal_takes_the_closing_back(world: _World) -> None:
    opened = world.history(_open_history())
    world.leaf(rounds=[_passed(world)])
    with pytest.raises(RuntimeError, match="renewal failed"):
        _closeout(world, refresh=RuntimeError("renewal failed"))
    assert (world.memory / HISTORY).read_bytes() == opened


# --------------------------------------------------------------------------------------------------
# The agent that handed the memory change over
# --------------------------------------------------------------------------------------------------

HANDED_AT = datetime(2026, 11, 19, 22, 26, 31, tzinfo=UTC)


def _record_pass_through_the_task_layer(world: _World) -> dict[str, Any]:
    """Record a passed memory round as ``record_review`` does, with the stamps it stores; return
    the stored round."""

    document = read_task_doc(world.folder / "leaf.json")
    begun = begin_task_review(document, {"reviewKind": "memory"})
    payload = {
        "reviewKind": "memory",
        "verdict": "pass",
        "findings": [],
        "candidateTree": world.tree(),
        "verdictRef": "reports/memory-1.md",
    }
    stamps = memory_round_stamp(
        world.coordination, world.folder, begun, payload, repository="agents-remember"
    )
    recorded = _record_task_review_bound(begun, payload, recorded_by=stamps)
    (world.folder / "leaf.json").write_text(
        recorded.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    state = recorded.memoryReviewState
    assert state is not None
    return state.recordedRounds[-1].model_dump(mode="json")


def test_an_open_memory_amendment_withholds_the_prior_stamped_pass(world: _World) -> None:
    world.receipt("curator", "01KCURATOR", "gpt-5.5")
    _record_pass_through_the_task_layer(world)
    leaf = world.folder / "leaf.json"
    passed = read_task_doc(leaf)
    assert passed.memoryReviewState is not None
    original_fact = leaf_review_fact(world.contract)
    assert original_fact is not None and original_fact.tree == world.tree()
    assert original_fact.handed_over_by.seat == "01KCURATOR"
    assert last_passed_memory_round(passed.memoryReviewState, leaf) is not None

    amended = _record_task_review_bound(
        passed,
        {
            "reviewKind": "memory",
            "verdict": "block",
            "verdictRef": "reports/amendment.md",
            "addedFindings": [
                {"findingId": "M2", "description": "Open evidence finding", "route": "curator"}
            ],
        },
    )
    lane = amended.memoryReviewState
    assert lane is not None and lane.round == passed.memoryReviewState.round
    assert lane.recordedRounds == passed.memoryReviewState.recordedRounds
    assert lane.remainingFindingIds == ["M2"]
    leaf.write_text(amended.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8")
    assert (leaf_review_fact(world.contract), last_passed_memory_round(lane, leaf)) == (None, None)


def _seat(stamp: Mapping[str, Any]) -> tuple[str, str, str]:
    return (stamp["role"], stamp["seat"], stamp["model"])


def test_the_curator_registered_when_the_pass_is_recorded_is_stamped_at_that_time(
    world: _World,
) -> None:
    """One Curator registered on the leaf: it is the hand-over, at the time the round is recorded;
    the recorder's stamp has the same time, and the closeout writes both into the fact."""

    world.history(_open_history())
    world.receipt("curator", "01KCURATOR", "gpt-5.5")

    stored = _record_pass_through_the_task_layer(world)
    outcome = _closeout(world)

    handed = world.read()["review"]["handedOverBy"]
    assert handed == stored["handedOverBy"]
    assert _seat(handed) == ("curator", "01KCURATOR", "gpt-5.5")
    assert (handed["harness"], handed["effort"]) == ("codex", "xhigh")
    assert handed["at"] == stored["recordedBy"]["at"] != "unknown"
    assert (handed["task"], handed["leaf"]) == (MASTER, LEAF)
    assert "unknown" not in handed
    assert outcome.review_note == ""


def test_a_curator_replaced_before_the_pass_is_recorded_gives_the_replacement(
    world: _World,
) -> None:
    world.history(_open_history())
    world.supersede(world.receipt("curator", "01KFIRST", "first-model"))
    world.receipt("curator", "01KNEW", "new-model")

    _record_pass_through_the_task_layer(world)
    _closeout(world)

    assert _seat(world.read()["review"]["handedOverBy"]) == ("curator", "01KNEW", "new-model")


def test_a_curator_replaced_after_the_pass_is_recorded_keeps_the_earlier_one(
    world: _World,
) -> None:
    """The stamp is taken when the round is recorded; a replacement before the closeout, or the
    earlier seat's retirement, changes nothing the fact says."""

    world.history(_open_history())
    first = world.receipt("curator", "01KEARLIER", "earlier-model")
    stored = _record_pass_through_the_task_layer(world)
    world.supersede(first)
    world.receipt("curator", "01KLATER", "later-model")

    _closeout(world)

    handed = world.read()["review"]["handedOverBy"]
    assert _seat(handed) == ("curator", "01KEARLIER", "earlier-model")
    assert handed["at"] == stored["recordedBy"]["at"]


@pytest.mark.parametrize(
    ("seats", "reason"),
    [
        ([], NO_REGISTERED_HANDOVER),
        ([("curator", "01KRETIRED", "stopped")], NO_REGISTERED_HANDOVER),
        ([("curator", "01KREJECTED", "rejected")], NO_REGISTERED_HANDOVER),
        (
            [("curator", "01KONE", "running"), ("consolidator", "01KTWO", "running")],
            several_registered(["consolidator 01KTWO", "curator 01KONE"]),
        ),
        (
            [("curator", "01KONE", "running"), ("curator", "01KTWO", "running")],
            several_registered(["curator 01KONE", "curator 01KTWO"]),
        ),
    ],
    ids=["none-registered", "retired", "rejected", "curator-and-consolidator", "two-curators"],
)
def test_no_single_active_curator_or_consolidator_reads_unknown_with_its_reason(
    world: _World, seats: list[tuple[str, str, str]], reason: str
) -> None:
    """Retired, rejected or no seat, or two active ones: no agent is named, and the reason says
    which; the time is still the recording's. Seats of other roles and of other leaves never count."""

    for role, request_id, status in seats:
        world.receipt(role, request_id, f"model-{request_id}", status=status)
    world.receipt("worker", "01KWORKER", "worker-model")
    world.receipt("reviewer", "01KREVIEWER", "reviewer-model")
    world.receipt("curator", "01KOTHERLEAF", "other-model", leaf="other_leaf.json")

    stored = _record_pass_through_the_task_layer(world)
    handed = stored["handedOverBy"]

    assert handed["role"] == handed["seat"] == handed["model"] == "unknown"
    assert set(handed["unknown"].values()) == {reason}
    assert handed["at"] == stored["recordedBy"]["at"]


def test_a_consolidator_the_registry_names_is_the_hand_over(world: _World) -> None:
    world.history(_open_history())
    world.receipt("consolidator", "01KCONSOLIDATOR", "gpt-6.1")

    _record_pass_through_the_task_layer(world)
    _closeout(world)

    assert _seat(world.read()["review"]["handedOverBy"]) == (
        "consolidator",
        "01KCONSOLIDATOR",
        "gpt-6.1",
    )


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [("curator", "agent-question", HANDED_AT)],
        [
            ("curator", "agent-question", HANDED_AT),
            ("consolidator", "agent-other", HANDED_AT.replace(minute=27)),
            ("curator", "agent-curator", HANDED_AT.replace(minute=28)),
        ],
        [("curator", "agent-later", datetime(2026, 11, 19, 23, 59, tzinfo=UTC))],
    ],
    ids=["no-message", "a-question-before", "several-in-any-order", "mail-after-the-round"],
)
def test_messages_of_any_number_and_order_change_nothing(
    world: _World, messages: list[tuple[str, str, datetime]]
) -> None:
    """Catches a hand-over read from the filed messages instead of the leaf's registered seats."""

    world.receipt("curator", "01KCURATOR", "gpt-5.5")
    for role, sender, when in messages:
        world.message(role, sender, "reviewer", "agent-reviewer", when)

    stored = _record_pass_through_the_task_layer(world)
    handed = stored["handedOverBy"]

    assert _seat(handed) == ("curator", "01KCURATOR", "gpt-5.5")
    assert handed["at"] == stored["recordedBy"]["at"]


@pytest.mark.parametrize("payload", [{}, {"reviewKind": "code"}])
def test_a_code_result_stores_no_stamp(world: _World, payload: dict[str, str]) -> None:
    document = read_task_doc(world.folder / "leaf.json")
    assert (
        memory_round_stamp(
            world.coordination, world.folder, document, payload, repository="agents-remember"
        )
        is None
    )


def test_the_document_reads_back_the_stored_round() -> None:
    base = _document().model_dump(mode="json", exclude_none=True)

    def with_round(**fields: Any) -> TaskDocument:
        state = {"round": 1, "recordedRounds": [_round(1, passed=True, **fields)]}
        return TaskDocument.model_validate({**base, "memoryReviewState": state})

    assert with_round(memoryTree=STATED_TREE, handedOverBy=CURATOR).memoryReviewState is not None
    for named in ("recordedBy", "handedOverBy"):
        with pytest.raises(ValueError, match=named):
            with_round(**{named: {"role": "x"}})


def test_direct_landing_admits_a_review_fact_only_in_the_closing_write() -> None:
    """A closed file is frozen: only the write that closes a file adds its fact.

    An open source may come back closed with a review fact; a source that was already closed must
    come back byte-identical, so a later write neither adds nor changes a closed file's fact.
    """

    fact = {"round": 1, "reviewer": REVIEWER, "handedOverBy": REVIEWER}
    open_source = {"schema": "ar-history/v1", "leaf": "260101-MAS-L1", "closed": False, "rows": []}
    closed_source = {**open_source, "closed": True}

    def text(document: dict[str, Any]) -> bytes:
        return canonical_text(document).encode("utf-8")

    assert _closing_of(open_source, text({**closed_source, "review": fact}))
    assert _closing_of(open_source, text(closed_source))
    assert not _closing_of(open_source, text({**closed_source, "rows": [], "leaf": "other-L2"}))
    assert _closing_of(closed_source, text(closed_source))
    assert not _closing_of(closed_source, text({**closed_source, "review": fact}))
    reviewed = {**closed_source, "review": fact}
    assert _closing_of(reviewed, text(reviewed))
    assert not _closing_of(reviewed, text({**reviewed, "review": {**fact, "round": 2}}))


def test_close_owner_history_writes_the_given_fact_once() -> None:
    """The closing writes whatever fact the closeout decided; a closed file stays as it is."""

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        (root / "knowledge" / "history").mkdir(parents=True)
        fact = ReviewFact.model_validate(
            {"round": 1, "tree": STATED_TREE, "reviewer": REVIEWER, "handedOverBy": CURATOR}
        )
        close_owner_history(root, LEAF, fact)
        closed = (root / HISTORY).read_bytes()
        close_owner_history(root, LEAF, None)
        assert (root / HISTORY).read_bytes() == closed
