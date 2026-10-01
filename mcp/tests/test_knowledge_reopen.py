"""Reopen after a converted closeout (L37 ruling of 2026-10-01T01:57:55, the 16:08:08 carry).

A leaf reopened after its closeout keeps its closed history file frozen and writes a new,
attempt-qualified file for the same leaf. The gate and the validator read all of a leaf's history
files as its history; a row of the closed file still counts while it is current, and a later row
about the same subject supersedes it. The test runs end to end: the writer, the gate (with the
validator inside it), the closeout's closing write, and record landing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.knowledge_gate import landing as landing_gate
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory.knowledge_index import (
    KnowledgeIndex,
    build_index,
    directory_snapshot,
    parse_tree,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import GitKnowledgeValidation
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import (
    history_path,
    owner_history_attempt,
    parse_history_document,
)
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    empty_history,
    merged_leaf_history,
    writable_attempt,
)
from agents_remember.worktrees.knowledge_gate import close_owner_history, latest_owner_history
from agents_remember.worktrees.modules import onboarding_trace, record_landing
from agents_remember.worktrees.services import (
    LandingGateRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from test_knowledge_closeout_gate import (
    CODE_A,
    LEAF,
    TRACES,
    Gated,
    _edit,
    _open,
    build_gated,
    commit,
    git,
)

OWNER = Owner(task="260928-MIK", kind="leaf", id=LEAF)
FIRST = f"knowledge/history/{LEAF}.json"
SECOND = f"knowledge/history/{LEAF}-attempt-2.json"


def _handoff(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"history": list(rows)}


def _rows_for_the_edit(*, traces: bool = True) -> dict[str, Any]:
    """The curator's answer to an edit of ``land``: the re-anchor, the family and the traces."""

    return _handoff(
        {
            "subject": "INV-AAAAAA",
            "disposition": "no_impact",
            "reason": "The body changed; values still land unchanged.",
            "covers": ["RLZ-A00001"],
        },
        {
            "subject": "FAM-F00001",
            "disposition": "no_impact",
            "reason": "Every member still holds.",
            "examined": ["INV-AAAAAA", "INV-BBBBBB"],
        },
        *(
            {"subject": subject, "disposition": "no_impact", "reason": "Only the body changed."}
            for subject in (TRACES if traces else ())
        ),
    )


def _write(world: Gated, document: dict[str, Any]) -> Any:
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="notes/handoff.json",
            document=document,
            commit=True,
        )
    )
    assert report.state == "written", report.render()
    return report


def _close_out_and_integrate(world: Gated) -> tuple[str, str]:
    """Close the leaf's history, commit both sides, land them on ``main``, and reopen the leaf."""

    close_owner_history(world.memory, LEAF)
    code_commit = commit(world.code, {})
    memory_commit = commit(world.memory, {}, trailer=code_commit)
    for repository in (world.code, world.memory):
        git(repository, "checkout", "-q", "main")
        git(repository, "merge", "-q", "--ff-only", "leaf")
        git(repository, "checkout", "-q", "-B", "leaf", "main")  # worktree_start: off the tips
    world.code_base = git(world.code, "rev-parse", "main")
    world.memory_base = git(world.memory, "rev-parse", "main")
    return code_commit, memory_commit


@pytest.fixture
def world(tmp_path: Path) -> Gated:
    return build_gated(tmp_path)


@pytest.fixture
def ports() -> Iterator[None]:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def test_a_reopened_leaf_writes_its_next_attempt_and_its_history_is_every_file(
    world: Gated, ports: None
) -> None:
    # Attempt 1: the edit is answered through the writer, the gate passes, and the leaf closes out.
    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _rows_for_the_edit())
    assert not (world.memory / SECOND).exists()
    assert world.gate().ok, world.gate().refusal()
    _close_out_and_integrate(world)
    closed_bytes = (world.memory / FIRST).read_bytes()
    assert json.loads(closed_bytes)["closed"] is True
    assert world.gate().ok  # reopened, nothing changed yet: nothing to answer

    # Attempt 2: a new edit of `land`. The closed invariant row no longer holds (its entry is stale
    # at C), and neither does the family row; the closed trace rows still count, because they are
    # current, so the traces are not asked again.
    _edit(world, CODE_A.replace("return value", "return +value"))
    refused = world.gate()
    assert set(_open(refused)) == {"touched_invariant INV-AAAAAA", "reached_family FAM-F00001"}
    assert "RLZ-A00001 is stale" in _open(refused)["touched_invariant INV-AAAAAA"]
    assert history_path(LEAF, 2) in str(refused.refusal())  # where the rows now go

    # The writer writes the reopened leaf's rows into its second attempt; the closed file is frozen.
    report = _write(world, _rows_for_the_edit(traces=False))
    assert all(row.history == SECOND for row in report.rows)
    assert (world.memory / FIRST).read_bytes() == closed_bytes
    second = parse_history_document(SECOND, (world.memory / SECOND).read_text())
    assert (second.leaf, second.attempt, second.closed) == (LEAF, 2, False)
    passed = world.gate()  # the validator inside the gate reads both files, and passes
    assert passed.ok, passed.refusal()

    # The history the gate reads: the later rows supersede, the closed trace rows still count.
    snapshot = directory_snapshot(world.memory)
    parsed = parse_tree(snapshot)
    history = parsed.histories[LEAF]
    by_subject = {row.subject: row for row in history.rows}
    assert by_subject["INV-AAAAAA"] in second.rows
    assert all(by_subject[subject] not in second.rows for subject in TRACES)
    assert set(parsed.history_files) == {FIRST, SECOND}
    index_path = world.root / "index.sqlite"
    build_index(snapshot, index_path)
    with KnowledgeIndex(index_path) as index:  # "rows about X" spans every file, each by its path
        about = index.history_rows_about("INV-AAAAAA").value
    assert {row.path for row in about} == {FIRST, SECOND}
    trace_rows_by_subject, problem = onboarding_trace._history_rows(
        cast(Any, SimpleNamespace(owner=LEAF, candidate=snapshot.files))
    )
    assert problem is None and set(trace_rows_by_subject) >= set(TRACES)  # MIK-R30 reads both

    # Editing the frozen file is refused by the validator, never read as the leaf's history.
    frozen = json.loads(closed_bytes)
    frozen["rows"][0]["reason"] = "Rewritten after closeout."
    (world.memory / FIRST).write_text(canonical_text(frozen), encoding="utf-8")
    edited = world.gate()
    assert any(
        "R22.7" in finding.message and FIRST in finding.message for finding in edited.findings
    )
    (world.memory / FIRST).write_bytes(closed_bytes)

    # Record landing reads the latest attempt: an open second attempt is not a closed-out leaf.
    open_commit = commit(world.memory, {}, trailer=commit(world.code, {}))
    open_refusal = record_landing._knowledge_gate_refusal(
        world.contract, git(world.code, "rev-parse", "HEAD"), open_commit
    )
    assert open_refusal is not None and f"- {SECOND}: [knowledge-history-not-closed]" in (
        open_refusal
    )

    # The closeout closes the second attempt (never the frozen file), and record landing checks it.
    code_commit, memory_commit = _close_out_and_integrate(world)
    assert json.loads((world.memory / SECOND).read_text())["closed"] is True
    assert (world.memory / FIRST).read_bytes() == closed_bytes
    contract = world.contract
    assert record_landing._knowledge_gate_refusal(contract, code_commit, memory_commit) is None


def test_attempt_files_are_named_ordered_and_backward_compatible() -> None:
    """The ``ar-history/v1`` shape gains an optional ``attempt``; a first file keeps its bytes."""

    first = empty_history("leaf", LEAF)
    assert "attempt" not in first.to_document()  # every existing file keeps its exact bytes
    assert history_path(LEAF) == history_path(LEAF, 1) == FIRST
    assert history_path(LEAF, 3) == f"knowledge/history/{LEAF}-attempt-3.json"
    assert owner_history_attempt(SECOND, LEAF) == 2
    assert owner_history_attempt(FIRST, LEAF) == 1
    assert owner_history_attempt(f"knowledge/history/{LEAF}7.json", LEAF) is None  # L97 vs L977
    assert owner_history_attempt(f"knowledge/history/{LEAF}-attempt-1.json", LEAF) is None
    assert writable_attempt({}) == 1
    assert writable_attempt({1: False}) == 1
    assert writable_attempt({1: True}) == 2
    assert writable_attempt({1: True, 2: False}) == 2
    with pytest.raises(ValueError, match="attempt-qualified"):
        HistoryFile.model_validate({"wave": "W-1", "attempt": 2, "closed": False, "rows": []})
    with pytest.raises(ValueError):
        HistoryFile.model_validate({"leaf": LEAF, "attempt": 1, "closed": False, "rows": []})
    second = empty_history("leaf", LEAF, attempt=2)
    with pytest.raises(ValueError, match="lives at"):
        parse_history_document(FIRST, canonical_text(second.to_document()))
    assert merged_leaf_history([]) is None
    assert merged_leaf_history([first]) is first


def test_the_closeout_closes_the_latest_attempt_and_never_reopens_a_closed_one(
    world: Gated,
) -> None:
    """A reopened leaf that wrote no row has its closed file as its history: nothing is written."""

    first = world.memory / FIRST
    assert latest_owner_history(world.memory, LEAF) == first  # no file yet: the plain one
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text(canonical_text(empty_history("leaf", LEAF, closed=True).to_document()))
    closed = first.read_bytes()
    closing = close_owner_history(world.memory, LEAF)
    assert closing.path == first and first.read_bytes() == closed
    second = world.memory / SECOND
    second.write_text(canonical_text(empty_history("leaf", LEAF, attempt=2).to_document()))
    assert latest_owner_history(world.memory, LEAF) == second
    close_owner_history(world.memory, LEAF)
    assert json.loads(second.read_text())["closed"] is True
    assert first.read_bytes() == closed


def test_the_gate_names_the_attempt_the_writer_writes(world: Gated, ports: None) -> None:
    """Review R1, F10: an attempt only the candidate closes (this closeout's own) is still the one
    the rows go to, so the gate names it -- as the writer chooses it -- and never a next attempt."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    close_owner_history(world.memory, LEAF)
    assert json.loads((world.memory / FIRST).read_text())["closed"] is True  # in K_C only
    refused = world.gate()
    assert _open(refused), refused.refusal()
    assert FIRST in str(refused.refusal()) and SECOND not in str(refused.refusal())


def test_record_landing_refuses_when_the_landed_history_cannot_be_listed(world: Gated) -> None:
    """Review R1, F5: a reopened leaf's landed commit holds its closed first attempt and an open
    second one. A listing Git cannot give is an unreadable input, never the first file alone."""

    for attempt, closed in ((1, True), (2, False)):
        path = world.memory / history_path(LEAF, attempt)
        path.parent.mkdir(parents=True, exist_ok=True)
        document = empty_history("leaf", LEAF, attempt=attempt, closed=closed).to_document()
        path.write_text(canonical_text(document), encoding="utf-8")
    landed = commit(world.memory, {}, trailer=world.code_base)
    request = LandingGateRequest(
        world.memory, landed, (world.memory_base,), world.code, world.code_base, leaf_owner=LEAF
    )
    (open_attempt,) = landing_gate._history_closed(request)
    assert (open_attempt.code, open_attempt.path) == (landing_gate.HISTORY_NOT_CLOSED, SECOND)

    real = landing_gate._git

    def unlisted(repository: Path, *args: str) -> str | None:
        return None if args[0] == "ls-tree" else real(repository, *args)

    with mock.patch.object(landing_gate, "_git", side_effect=unlisted):
        (unreadable,) = landing_gate._history_closed(request)
    assert unreadable.code == landing_gate.RUN_INCOMPLETE
    assert "cannot be listed" in unreadable.message
