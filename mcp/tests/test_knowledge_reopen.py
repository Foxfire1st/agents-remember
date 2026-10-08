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
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate, memo
from agents_remember.application.knowledge_gate import gate as gate_module
from agents_remember.application.knowledge_gate import landing as landing_gate
from agents_remember.application.knowledge_worklist.route_conditions import family_route_item_open
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory.knowledge_index import (
    KnowledgeIndex,
    build_index,
    directory_snapshot,
    parse_tree,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import GitKnowledgeValidation
from agents_remember.models.closeout.input import EffectiveCloseoutInput
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import (
    history_path,
    owner_history_attempt,
    parse_history_document,
)
from agents_remember.models.knowledge_files.history import (
    HistoryFile,
    InvariantRow,
    empty_history,
    merged_leaf_history,
    writable_attempt,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecoveryCommits
from agents_remember.worktrees.integration.mutation_evidence import ephemeral_git_mutation_snapshot
from agents_remember.worktrees.knowledge_gate import close_owner_history, latest_owner_history
from agents_remember.worktrees.knowledge_validation import PairedCode, memory_commit_refusal
from agents_remember.worktrees.modules import (
    closeout,
    closeout_external,
    onboarding,
    onboarding_trace,
    record_landing,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import VerifiedChange
from agents_remember.worktrees.queue import closeout_recovery
from agents_remember.worktrees.services import (
    LandingGateRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import load_contract
from knowledge_index_test_support import rewrite_in_the_second_of_the_index_write
from test_knowledge_closeout_gate import (
    CODE_A,
    CODE_B,
    LEAF,
    TRACES,
    A,
    B,
    Gated,
    _edit,
    _open,
    build_gated,
    commit,
    family_row,
    git,
    trace_rows,
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


def _write(world: Gated, document: dict[str, Any], worklist: Any = None) -> Any:
    report = write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=OWNER,
            handoff_path="notes/handoff.json",
            document=document,
            commit=True,
            worklist=worklist,
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


@dataclass
class _Continued(Gated):
    """A world whose contract can record the leaf's completed, un-integrated closeout."""

    closed_out: tuple[str, str] | None = None

    def contract_path(self) -> Path:
        path = super().contract_path()
        if self.closed_out is not None:
            code, memory = self.closed_out
            closeout_section = (
                "\ncloseout:\n  status: completed\n"
                f"  code_commit: {code}\n  memory_content_commit: {memory}\n---\n"
            )
            text = path.read_text(encoding="utf-8").removesuffix("---\n")
            path.write_text(text + closeout_section, encoding="utf-8")
        return path


@pytest.fixture
def world(tmp_path: Path) -> _Continued:
    return _Continued(**vars(build_gated(tmp_path)))


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


# --------------------------------------------------------------------------------------------------
# L37 P1c: the worktree closeout's memory commit is gated, and a leaf that continues after a
# closeout that was not integrated writes its next attempt (rulings A and B, 2026-10-01T17:17:07)
# --------------------------------------------------------------------------------------------------

_NEVER = "a refused closeout began a Git mutation"
_INTENTS: list[mock.Mock] = []


@dataclass(frozen=True)
class _Context:
    """The resolved context the closeout's metadata refresh reads."""

    code_repository_root: Path
    onboarding_root: Path
    code_repository_name: str = "agents-remember"
    storage: StorageSettings = field(default_factory=StorageSettings)


def _effective() -> EffectiveCloseoutInput:
    return EffectiveCloseoutInput.model_validate(
        {
            "route": "worktree",
            "contractKind": "leaf",
            "memoryMode": "external",
            "code": {"state": "enabled", "reason": "leaf", "message": "code"},
            "memory": {"state": "enabled", "reason": "leaf", "message": "memory"},
        }
    )


def _closeout(
    world: Gated,
    code_commit: str,
    *,
    refused: bool = False,
    recovered: Any = None,
    own_writes: bool = False,
) -> Any:
    """``external_closeout_commits``, the worktree closeout's memory leg, with only the journal
    hooks stubbed. ``refused`` asserts that no Git mutation begins; ``own_writes`` runs the
    closeout's real metadata refresh instead of an empty one. The stand-in for the journal's
    mutation intent is kept in :data:`_INTENTS`, to read what the commit was bound to."""

    effective = _effective()
    change = VerifiedChange(
        commit=code_commit, commit_date="2026-10-01T00:00:00+00:00", changed_paths=[A]
    )
    begin = mock.Mock(side_effect=AssertionError(_NEVER)) if refused else mock.Mock()
    begin.return_value = None
    _INTENTS.append(begin)
    args = WorktreeArgs(contract_path=world.contract_path(), recovery_commits=recovered)
    refresh = closeout_external._ExternalMemoryRefresh([], [], [], {})
    context = _Context(world.code, world.memory / "onboarding")  # the fixture has no settings
    refreshed = (
        mock.patch.object(closeout_external, "_refresh_external_memory", return_value=refresh)
        if not own_writes
        else mock.patch.object(onboarding, "contract_context", return_value=context)
    )
    with (
        refreshed,
        mock.patch.object(closeout_external, "contract_context", return_value=context),
        mock.patch.object(closeout_external, "report_operation_progress"),
        mock.patch.object(closeout_external, "prove_git_commit"),
        mock.patch.object(closeout_external, "refresh_memory_cache", return_value={}),
        mock.patch.object(closeout_external, "begin_git_mutation", begin),
        mock.patch.object(closeout_recovery, "report_operation_progress"),
        mock.patch.object(closeout_recovery, "refresh_memory_cache", return_value={}),
    ):
        return closeout_external.external_closeout_commits(world.contract, args, effective, change)


def _traces() -> dict[str, Any]:
    return _handoff(
        *(
            {"subject": subject, "disposition": "no_impact", "reason": "Only the body changed."}
            for subject in TRACES
        )
    )


def _refused(world: Gated, code_commit: str, **how: Any) -> str:
    """The closeout's refusal; the memory worktree's ``HEAD`` and files are as they were."""

    before = (git(world.memory, "rev-parse", "HEAD"), git(world.memory, "status", "--porcelain"))
    with pytest.raises(RuntimeError) as refused:
        _closeout(world, code_commit, refused=True, **how)
    assert _NEVER not in str(refused.value)
    after = (git(world.memory, "rev-parse", "HEAD"), git(world.memory, "status", "--porcelain"))
    assert after == before  # nothing committed, and the closing restored
    return str(refused.value)


def _public_closeout(world: Gated) -> Any:
    """``closeout_result``, the function behind ``worktree_closeout_apply``, run whole: the gate
    before the claim, the code commit, the memory leg with the closeout's own writes, and the
    contract's finalization. Only what the fixture has none of is stubbed: the admission of a
    published enclosure, the operation journal and a remote's branch authority."""

    contract, effective = world.contract, _effective()
    args = WorktreeArgs(
        contract_path=contract.contract_path,
        approved=True,
        approval_note="approved",
        closeout_input=effective,
    )
    context = _Context(world.code, world.memory / "onboarding")
    with (
        mock.patch.object(closeout, "_closeout_entry", return_value=(contract, effective, None)),
        mock.patch.object(closeout, "report_operation_progress"),
        mock.patch.object(closeout, "_refuse_unsatisfied_closeout_gate"),
        mock.patch.object(closeout, "_revalidate_candidate", return_value=contract),
        mock.patch.object(closeout, "_claim_closeout_gate", return_value=None),
        mock.patch.object(closeout, "require_ordinary_worktree"),
        mock.patch.object(closeout_recovery, "report_operation_progress"),
        mock.patch.object(closeout_recovery, "begin_git_mutation"),
        mock.patch.object(closeout_recovery, "prove_git_commit"),
        mock.patch.object(closeout_external, "contract_context", return_value=context),
        mock.patch.object(closeout_external, "report_operation_progress"),
        mock.patch.object(closeout_external, "begin_git_mutation"),
        mock.patch.object(closeout_external, "prove_git_commit"),
        mock.patch.object(onboarding, "contract_context", return_value=context),
    ):
        return closeout.closeout_result(args, contract)


def _public_preview(world: Gated) -> Any:
    """``closeout_result`` as a dry run: the function behind ``worktree_closeout_preview``. The
    admission of a published enclosure and the settings are stubbed, as the fixture has none."""

    contract = world.contract
    args = WorktreeArgs(
        contract_path=contract.contract_path, closeout_input=_effective(), dry_run=True
    )
    context = _Context(world.code, world.memory / "onboarding")
    with (
        mock.patch.object(closeout, "leaf_enclosure_binding_refusal", return_value=None),
        mock.patch.object(closeout, "require_effective_closeout_plan"),
        mock.patch.object(closeout, "_validate_closeout_source_state", return_value=contract),
        mock.patch.object(closeout, "require_ordinary_worktree"),
        mock.patch.object(closeout, "contract_context", return_value=context),
        mock.patch.object(onboarding, "contract_context", return_value=context),
    ):
        return closeout.closeout_result(args, contract)


def _sides(world: Gated) -> list[str]:
    """Both worktrees' commits and uncommitted states: what a refused closeout must not change."""

    return [
        git(repository, *arguments)
        for repository in (world.code, world.memory)
        for arguments in (("rev-parse", "HEAD"), ("status", "--porcelain"))
    ]


SIBLING = {
    "subject": "INV-BBBBBB",
    "disposition": "no_impact",
    "reason": "The sibling was examined with the family; it still answers two.",
    "covers": ["RLZ-B00001"],
}


def test_the_public_closeout_refuses_an_open_item_and_commits_once_it_is_answered(
    world: _Continued, ports: None
) -> None:
    """Ruling A, entered through the public closeout itself. The edit touches INV-AAAAAA and the
    leaf wrote only its traces: the closeout is refused before the approval is claimed or either
    side is committed, and the refusal names every open item. Once the rows exist, the same
    closeout commits both sides, closes the history file and records itself in the contract."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _traces())
    open_bytes, before = (world.memory / FIRST).read_bytes(), _sides(world)
    with pytest.raises(RuntimeError) as refused:
        _public_closeout(world)
    refusal = str(refused.value)
    assert "the mandatory invariant gate (MIK-R09) refuses" in refusal
    assert "[knowledge-item-open] touched_invariant INV-AAAAAA" in refusal
    assert "[knowledge-item-open] reached_family FAM-F00001" in refusal
    assert _sides(world) == before  # nothing committed on either side, nothing written
    assert (world.memory / FIRST).read_bytes() == open_bytes

    _write(world, _rows_for_the_edit(traces=False))
    path = world.contract.contract_path
    closed = _public_closeout(world)
    assert (closed.returncode, closed.payload["state"]) == (0, "closed")
    code, memory = closed.payload["code_commit"], closed.payload["memory_content_commit"]
    assert [code, "", memory, ""] == _sides(world) and before[0] != code  # the closeout's commits
    recorded = load_contract(path)  # as the closeout wrote it
    assert recorded.closeout_status == "completed"
    assert (recorded.code_commit, recorded.memory_content_commit) == (code, memory)
    assert json.loads(git(world.memory, "show", f"{memory}:{FIRST}"))["closed"] is True
    assert git(world.memory, "rev-parse", "main") == world.memory_base  # not integrated


def test_the_closeout_preview_answers_what_the_apply_will_do(
    world: _Continued, ports: None
) -> None:
    """L37 ruling of 2026-10-01T22:49:36, Q4. The preview asks the gate the apply's preflight
    asks. A leaf the apply refuses is never previewed as ``would-closeout``: the preview names
    the findings and does not ask for the commit approval. A preview that passes leaves its
    verdict in the memo, so the apply that follows evaluates only the exact tree at the commit."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _traces())
    before = _sides(world)
    refused = _public_preview(world)
    assert (refused.returncode, refused.payload["state"]) == (2, "knowledge-gate-refused")
    verdict = refused.payload["knowledge_gate"]
    assert (verdict["state"], verdict["findingCount"], verdict["truncated"]) == (
        "refused",
        2,
        False,
    )
    assert any("touched_invariant INV-AAAAAA" in one for one in verdict["findings"])
    assert refused.payload["commit_approval_required"] is False
    assert "nothing would be committed" in refused.payload["nextStep"]["summary"]
    assert "nextTool" not in refused.payload and _sides(world) == before  # no apply is offered
    many = "the gate refuses\n" + "\n".join(f"- file: [knowledge-item-open] {n}" for n in range(60))
    capped: Any = closeout._gate_refused_preview(world.contract, many)["knowledge_gate"]
    assert (capped["findingCount"], len(capped["findings"]), capped["truncated"]) == (60, 50, True)

    _write(world, _rows_for_the_edit(traces=False))
    with mock.patch.object(gate_module, "_evaluate", wraps=gate_module._evaluate) as evaluated:
        passing = _public_preview(world)
        assert (passing.returncode, passing.payload["state"]) == (0, "would-closeout")
        assert passing.payload["knowledge_gate"] == {"state": "pass"}
        assert passing.payload["nextTool"] == "worktree_closeout_apply"
        assert evaluated.call_count == 1 and _sides(world) == before  # a preview writes nothing
        assert _public_closeout(world).payload["state"] == "closed"
        assert evaluated.call_count == 2  # the preflight read the memo; the commit's tree is new


def test_an_unconverted_leafs_preview_carries_no_gate_verdict(
    world: _Continued, ports: None
) -> None:
    """L37 review R6-5 (N21). The gate does not govern unconverted memory, and its verdict says so
    instead of reading as a pass: the preview of such a leaf is what it was before the preview
    asked the gate, with no ``knowledge_gate`` block."""

    for branch in ("main", "leaf"):  # neither the parent line nor the leaf holds the marker
        git(world.memory, "checkout", "-q", branch)
        git(world.memory, "rm", "-q", "knowledge/layout.json")
        commit(world.memory, {})
    with mock.patch.object(gate_module, "_evaluate", wraps=gate_module._evaluate) as evaluated:
        preview = _public_preview(world)
    assert (preview.returncode, preview.payload["state"]) == (0, "would-closeout")
    assert "knowledge_gate" not in preview.payload and evaluated.call_count == 0


def test_a_leaf_that_continues_after_its_closeout_writes_its_next_attempt(
    world: _Continued, ports: None
) -> None:
    """Ruling B: the closeout that was not integrated froze the first attempt in the leaf's memory
    ``HEAD``; every later row goes to attempt 2, and the second closeout closes that file."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _handoff(*_rows_for_the_edit()["history"], SIBLING))
    closed = _public_closeout(world)
    first_code, first_memory = (
        closed.payload["code_commit"],
        closed.payload["memory_content_commit"],
    )
    closed_bytes = (world.memory / FIRST).read_bytes()
    world.closed_out = (first_code, first_memory)  # the fixture's contract records it from here

    # Nothing left to commit: the commit the closeout records is still judged, and passes.
    assert _closeout(world, first_code, refused=True).memory_commit == first_memory

    # The committed closed file is frozen though the parent line does not hold it (ruling B).
    edited = json.loads(closed_bytes)
    edited["rows"][0]["reason"] = "Rewritten after the closeout."
    (world.memory / FIRST).write_text(canonical_text(edited), encoding="utf-8")
    assert "R22.7-history-frozen" in _refused(world, first_code)
    (world.memory / FIRST).write_bytes(closed_bytes)

    # The leaf continues. Its memory worktree is clean, and the closeout is refused all the same:
    # the new edit reopened two items, and their rows go to the next attempt file. The edit also
    # adds a comment to the sibling's file, outside its entry.
    _edit(world, CODE_A.replace("return value", "return +value"))
    (world.code / B).write_text(f"# The sibling.\n{CODE_B}", encoding="utf-8")
    second_code = commit(world.code, {})
    refusal = _refused(world, second_code)
    assert "touched_invariant INV-AAAAAA" in refusal and history_path(LEAF, 2) in refusal
    hunk = next(one for one in world.gate().worklist["items"] if one["kind"] == "unexplained_hunk")
    comment = [
        {"subject": f"hunk:{hunk['id']}", "disposition": "no_invariant", "reason": "A comment."},
        {"subject": "onboarding:pkg/b.py", "disposition": "no_impact", "reason": "A comment."},
    ]

    report = _write(world, _handoff(_rows_for_the_edit(traces=False)["history"][1]))
    assert [row.history for row in report.rows] == [SECOND]  # the writer starts attempt 2
    assert (world.memory / FIRST).read_bytes() == closed_bytes
    refusal = _refused(world, second_code)  # a family row alone: the invariant item is still open
    assert "touched_invariant INV-AAAAAA" in refusal
    assert json.loads((world.memory / SECOND).read_text())["closed"] is False  # restored open

    _write(world, _handoff(*_rows_for_the_edit(traces=False)["history"], *comment))
    # The writer carried the sibling's entry: its blob is re-recorded, so the frozen row about
    # INV-BBBBBB names an anchor the entry no longer has. A frozen file is not judged again; an
    # attempt that is not frozen would be refused for it, and no later row supersedes this one.
    (frozen,) = (row for row in json.loads(closed_bytes)["rows"] if row["subject"] == "INV-BBBBBB")
    assert frozen["covers"][0]["after"] != world.anchor("RLZ-B00001")
    second_memory = _closeout(world, second_code).memory_commit
    committed = json.loads(git(world.memory, "show", f"{second_memory}:{SECOND}"))
    assert (committed["attempt"], committed["closed"]) == (2, True)
    assert git(world.memory, "show", f"{second_memory}:{FIRST}").encode() + b"\n" == closed_bytes
    # The frozen attempt's trace rows still count; its invariant row is superseded by attempt 2.
    assert {row["subject"] for row in committed["rows"]} == {
        "INV-AAAAAA",
        "FAM-F00001",
        *(row["subject"] for row in comment),
    }
    # Landing: the contract records this closeout, so what it was made on is frozen as it was at
    # the closeout. The same commit landed without that record freezes nothing (review R5-1), and
    # the first attempt's row about INV-BBBBBB is then judged against the landed tree.
    unrecorded = record_landing._knowledge_gate_refusal(world.contract, second_code, second_memory)
    assert "R09-history-rows" in (unrecorded or "") and "RLZ-B00001" in (unrecorded or "")
    world.closed_out = (second_code, second_memory)
    assert (
        record_landing._knowledge_gate_refusal(world.contract, second_code, second_memory) is None
    )


def test_a_file_rewritten_while_the_gate_runs_is_never_committed_unjudged(
    world: Gated, ports: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L37 P1c, C11. The gate judges a tree read through a fresh index and the commit stages
    through the repository's own, so a file written while the gate ran would be committed
    unjudged. The hardest case is a rewrite in place, same size, in the second the index was
    written: a copy of the index that loses the index file's time does not see it. It is refused
    before any Git mutation, the closing is restored, and a passing commit is bound to the tree
    the gate judged."""

    # Without optional locks ``git status`` does not rewrite the repository's index, which would
    # otherwise end the same-second state before the staging snapshot copies that index.
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")
    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _rows_for_the_edit())
    code = commit(world.code, {})
    card = world.memory / f"onboarding/{A}.md"
    gate = closeout_external.leaf_gate_refusal
    judged: list[str] = []

    def rewriting(contract: Any, *, code_tree: str, memory_tree: str) -> str | None:
        refusal = gate(contract, code_tree=code_tree, memory_tree=memory_tree)
        judged.append(memory_tree)
        rewrite_in_the_second_of_the_index_write(world.memory, card, "# b\n")
        return refusal

    head = git(world.memory, "rev-parse", "HEAD")
    with (
        mock.patch.object(closeout_external, "leaf_gate_refusal", rewriting),
        pytest.raises(RuntimeError, match="changed while the gate ran") as refused,
    ):
        _closeout(world, code, refused=True)
    assert judged[0] in str(refused.value) and git(world.memory, "rev-parse", "HEAD") == head
    assert json.loads((world.memory / FIRST).read_text())["closed"] is False  # restored
    assert not (world.memory / ".gitignore").exists()  # and the cache's ignore rule taken back

    # Both captures read the rewritten card: the exact tree rehashes every file, and the tree the
    # commit would stage is read through a copy of the index that keeps the index file's time.
    rewritten = git(world.memory, "hash-object", str(card))
    exact = world.candidate().memory
    staged = ephemeral_git_mutation_snapshot(world.memory, memory_cache=True).candidateTree
    for tree in (exact, staged):
        assert git(world.memory, "rev-parse", f"{tree}:onboarding/{A}.md") == rewritten
    assert exact == staged != judged[0]

    # Rerun: the gate judges the tree as it is now, and the commit is built from exactly that tree
    # object. Files written after the judged-tree check, directly before the staging, are not in
    # the commit; they stay uncommitted changes (review R5-3).
    checked = closeout_external._require_judged_tree

    def written_after(repository: Path, tree: str) -> None:
        checked(repository, tree)
        judged.append(tree)
        card.write_text("# c\n", "utf-8")
        (world.memory / "onboarding/stray.md").write_text("# Stray\n", "utf-8")

    with mock.patch.object(closeout_external, "_require_judged_tree", written_after):
        committed = _closeout(world, code).memory_commit
    bound = _INTENTS[-1].call_args.kwargs
    assert bound["expected_output_tree"] == judged[-1] != judged[0]
    assert git(world.memory, "rev-parse", f"{committed}^{{tree}}") == judged[-1]
    assert bound["use_current_candidate"] is False
    assert git(world.memory, "diff", "--name-only") == f"onboarding/{A}.md"
    assert git(world.memory, "ls-files", "--others", "--exclude-standard") == "onboarding/stray.md"


def test_the_latest_row_about_a_subject_governs_across_attempts(
    world: _Continued, ports: None
) -> None:
    """L37 P1c, C6: a leaf's attempt files are one history and a subject's latest row governs, for
    the writer, the gate's currentness and the validator's re-anchor rule."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _rows_for_the_edit())
    first_code = commit(world.code, {})
    world.closed_out = (first_code, _closeout(world, first_code).memory_commit)

    # The writer accepts a superseding row at an unchanged revision when its disposition keeps the
    # revision. It lands in attempt 2 under a new row ID, and the leaf's history reads it.
    again = {**_rows_for_the_edit()["history"][0], "reason": "Judged again after the closeout."}
    report = _write(world, _handoff(again))
    assert [row.history for row in report.rows] == [SECOND]
    earlier, later = (
        parse_history_document(path, (world.memory / path).read_text()).row_about("INV-AAAAAA")
        for path in (FIRST, SECOND)
    )
    assert isinstance(earlier, InvariantRow) and isinstance(later, InvariantRow)
    assert (later.revision, later.reason) == (earlier.revision, again["reason"])
    assert later.id != earlier.id
    merged = parse_tree(directory_snapshot(world.memory)).histories[LEAF]
    assert merged.row_about("INV-AAAAAA") == later and world.gate().ok

    # A changed row counts from the writer's base, the leaf's memory HEAD: it is refused unless the
    # record's meaning changed again or it restates the leaf's own changed row of that revision.
    # The frozen row here is a no_impact row, so no change exists whose label could be corrected.
    relabelled = {**again, "disposition": "changed", "effect": "strengthen"}
    refused = write_knowledge(
        WriteRequest(
            world.memory, world.code, OWNER, "notes/handoff.json", _handoff(relabelled), commit=True
        )
    )
    assert refused.state == "refused", refused.render()
    assert "a changed row's revision is the K_B revision 1 plus one" in refused.render()

    # The gate's currentness reads the later row: when it is not current the item is open, though
    # the frozen attempt holds a current row about the same subject.
    governing = (world.memory / SECOND).read_bytes()
    behind = json.loads(governing)
    behind["rows"][0]["revision"] += 1
    (world.memory / SECOND).write_text(canonical_text(behind), encoding="utf-8")
    opened = _open(world.gate())
    assert "records revision 2" in opened["touched_invariant INV-AAAAAA"]
    (world.memory / SECOND).write_bytes(governing)

    # The validator's re-anchor rule reads the later row too. A contract whose closeout cells were
    # reset records no closeout, so the closed first attempt is the leaf's own and is read whatever
    # its flag; its row about a subject the second attempt answers again is superseded.
    world.closed_out = None
    _edit(world, CODE_A.replace("return value", "return +value"))
    _write(world, _rows_for_the_edit(traces=False))  # re-anchors the entry the first row covers
    second_code = commit(world.code, {})
    assert world.gate().ok, world.gate().refusal()
    closed = _closeout(world, second_code).memory_commit
    assert json.loads(git(world.memory, "show", f"{closed}:{SECOND}"))["closed"] is True


def _restating(statement: str, guarantee: str) -> dict[str, Any]:
    """The hand-off sections that restate INV-AAAAAA and its family's guarantee."""

    entry = {
        "id": "A-1",
        "invariant_id": "INV-AAAAAA",
        "statement": statement,
        "kind": "clause",
        "target": [],
        "found_at": [],
        "disposition": "satisfied",
        "authority": {"task_document": "gate_case"},
        **dict.fromkeys(
            ("disposition_source", "evidence", "resolution", "validated_at", "record_action"), None
        ),
        "supersedes": None,
    }
    family = {
        "key": "F-1",
        "kind": "family",
        "id": "FAM-F00001",
        "fields": {"guarantee": guarantee},
    }
    return {"entries": [entry], "records": [family]}


def test_a_changed_record_is_governed_by_its_changed_row_in_every_attempt(
    world: _Continued, ports: None
) -> None:
    """L37 ruling of 2026-10-01T22:49:36, Q2. A leaf that closed out and continues may restate
    the label of a change it already made (a), may not let a later row of another disposition
    govern the changed record (b), and may change the record again: one ``changed`` row per
    revision step, the record two revisions ahead of the parent line (c)."""

    def revisions() -> tuple[int, int]:
        files = {
            name: json.loads(next((world.memory / "knowledge" / name).glob(pattern)).read_text())
            for name, pattern in (("invariants", "INV-AAAAAA-*.json"), ("families", "*.json"))
        }
        return files["invariants"]["revision"], files["families"]["revision"]

    _edit(world, CODE_A.replace("return value", "return -value"))
    unchanged, family, *traces = _rows_for_the_edit()["history"]
    family = {**family, "disposition": "changed"}
    changed = {**unchanged, "disposition": "changed", "effect": "clarify"}
    first = _restating("Values land negated.", "Landing holds, negated.")
    _write(world, {**first, "history": [changed, family, *traces]})
    closed = _public_closeout(world).payload
    world.closed_out = (closed["code_commit"], closed["memory_content_commit"])
    assert revisions() == (2, 2)

    # (a) The same revision step, restated in attempt 2: its effect and reason are corrected.
    restated = {**changed, "effect": "replace", "reason": "The label of that change, corrected."}
    report = _write(world, _handoff(restated))
    assert [row.history for row in report.rows] == [SECOND]
    (row,) = json.loads((world.memory / SECOND).read_text())["rows"]
    assert (row["revision"], row["effect"], row["reason"]) == (2, "replace", restated["reason"])
    assert world.gate().ok and revisions() == (2, 2)

    # (b) A later row of another disposition would hide the change: the writer has no parent line
    # to see it by, so the routes refuse it -- the gate by the record's revision on the parent
    # line, the validator by the changed row in the leaf's earlier attempt -- and name the remedy.
    hiding = _handoff({**unchanged, "reason": "Judged again; nothing changed since."})
    _write(world, hiding)
    hidden = _open(world.gate())["touched_invariant INV-AAAAAA"]
    assert "a no_impact row governs an invariant this leaf changed" in hidden
    assert "revision 1 on the parent line, 2 in K_C" in hidden and "restate that changed" in hidden
    refusal = _refused(world, world.closed_out[0])
    assert "R09-history-rows" in refusal and "replaces this leaf's changed row" in refusal
    _write(world, _handoff(restated))
    assert world.gate().ok

    # (c) The record changes again: the leaf HEAD's revision plus one, with its own changed row.
    second = _restating("Values land negated, always.", "Landing holds, negated, always.")
    again = {**changed, "effect": "strengthen", "reason": "Restated after the closeout."}
    _write(world, {**second, "history": [again, family]})
    assert revisions() == (3, 3)  # two steps ahead of the parent line, which holds revision 1
    assert world.gate().ok, world.gate().refusal()
    memory = _closeout(world, world.closed_out[0]).memory_commit
    steps = [
        row
        for path in (FIRST, SECOND)
        for row in json.loads(git(world.memory, "show", f"{memory}:{path}"))["rows"]
        if row["subject"] == "INV-AAAAAA"
    ]
    assert [(row["disposition"], row["revision"]) for row in steps] == [
        ("changed", 2),
        ("changed", 3),
    ]
    code = world.closed_out[0]
    world.closed_out = (code, memory)
    assert record_landing._knowledge_gate_refusal(world.contract, code, memory) is None

    # Review R5-2, as reproduced: after the second closeout a no_impact row lands in attempt 3 and
    # would govern. The gate refuses it, and so does the validator on its own at a recorded
    # landing of a commit made by hand, a route the worklist does not reach.
    report = _write(world, hiding)
    assert [row.history for row in report.rows] == [history_path(LEAF, 3)]
    assert "a no_impact row governs" in _open(world.gate())["touched_invariant INV-AAAAAA"]
    close_owner_history(world.memory, LEAF)
    by_hand = commit(world.memory, {}, trailer=code)
    refusal = record_landing._knowledge_gate_refusal(world.contract, code, by_hand) or ""
    assert "R09-history-rows" in refusal and "replaces this leaf's changed row" in refusal
    assert f"(revision 3, effect {again['effect']})" in refusal  # the step it would hide


def test_a_change_on_the_parent_line_may_be_answered_by_any_later_row(
    world: _Continued, ports: None
) -> None:
    """Review R5-2, the validator rule's two limits. A change that landed on the leaf's parent
    line is no longer the leaf's to keep visible: reopened, the leaf may answer the invariant with
    a ``no_impact`` row. And the rule judges a leaf's publication only: a master's landing, judged
    against the line as it was before its leaves landed, is not refused for one of them."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    unchanged, family, *traces = _rows_for_the_edit()["history"]
    family = {**family, "disposition": "changed"}
    rows = [{**unchanged, "disposition": "changed", "effect": "clarify"}, family, *traces]
    _write(
        world, {**_restating("Values land negated.", "Landing holds, negated."), "history": rows}
    )
    before_the_leaf = world.memory_base
    _close_out_and_integrate(world)

    _edit(world, CODE_A.replace("return value", "return +value"))
    _write(world, _rows_for_the_edit(traces=False))  # attempt 2: a no_impact row governs
    assert world.gate().ok, world.gate().refusal()
    code = commit(world.code, {})
    memory = _closeout(world, code).memory_commit  # the validator at the leaf's closeout passes
    judged = {
        "memory_repository": world.memory,
        "candidate_tree": memory,
        "bases": (before_the_leaf,),
        "paired_code": PairedCode(repository=world.code, commit=code),
    }
    assert memory_commit_refusal(**judged) is None  # as a master lands it
    as_a_leaf = memory_commit_refusal(
        **judged, leaf_publication=True
    )  # were neither attempt landed
    assert "replaces this leaf's changed row" in (as_a_leaf or "")

    # Review R6-5 (N14). A changed row at an unchanged revision restates the leaf's latest earlier
    # row about the subject. That is attempt 2's no_impact row now, so attempt 3 restates nothing,
    # though attempt 1 holds a changed row at this very revision.
    relabelled = _handoff({**unchanged, "disposition": "changed", "effect": "strengthen"})
    refused = write_knowledge(
        WriteRequest(world.memory, world.code, OWNER, "notes/handoff.json", relabelled, commit=True)
    )
    assert refused.state == "refused", refused.render()
    assert "a changed row's revision is the K_B revision 2 plus one" in refused.render()


def test_a_family_whose_guarantee_the_leaf_changed_is_governed_by_its_changed_row(
    world: _Continued, ports: None
) -> None:
    """L37 ruling of 2026-10-02T01:04:49. A family has one governing row, the leaf's latest about
    it. While the leaf changed the family's guarantee that row is its ``changed`` row: no row of
    another disposition answers the family in the attempt that makes the change, and none replaces
    the ``changed`` row in a later attempt. A record change that leaves the guarantee alone asks
    for no ``changed`` row."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    unchanged, family, *traces = _rows_for_the_edit()["history"]
    renamed = {"key": "F-0", "kind": "family", "id": "FAM-F00001", "fields": {"title": "Landed"}}
    _write(world, {"records": [renamed], "history": [unchanged, family, *traces]})
    reached = next(one for one in world.gate().worklist["items"] if one["kind"] == "reached_family")
    assert "record-changed" in reached["facts"]["reachedBy"] and world.gate().ok

    restated = _restating("Values land negated.", "Landing holds, negated.")
    changed = {**unchanged, "disposition": "changed", "effect": "clarify"}
    _write(world, {**restated, "history": [changed, family]})  # the family row says no_impact
    result = world.gate()
    reached = next(one for one in result.worklist["items"] if one["kind"] == "reached_family")
    assert "guarantee-changed" in reached["facts"]["reachedBy"]
    opened = _open(result)["reached_family FAM-F00001"]
    assert "a no_impact row governs a family whose guarantee this leaf changed" in opened
    _write(world, _handoff({**family, "disposition": "changed"}))
    assert world.gate().ok, world.gate().refusal()
    closed = _public_closeout(world).payload
    world.closed_out = (closed["code_commit"], closed["memory_content_commit"])

    # After the closeout, a route row or a no_impact row would replace the changed row. The gate
    # refuses it by the guarantee on the parent line, the validator by the frozen changed row.
    for disposition in ("rerouted", "no_impact"):
        report = _write(world, _handoff({**family, "disposition": disposition}))
        assert [row.history for row in report.rows] == [SECOND]
        opened = _open(world.gate())["reached_family FAM-F00001"]
        assert f"a {disposition} row governs a family whose guarantee" in opened
        refusal = _refused(world, world.closed_out[0])
        assert "R09-history-rows" in refusal and "(the family's own change)" in refusal
        assert f"the {disposition} row about FAM-F00001 replaces this leaf's changed row" in refusal
    _write(world, _handoff({**family, "disposition": "changed"}))  # named again: it governs
    assert world.gate().ok, world.gate().refusal()
    assert _closeout(world, world.closed_out[0]).memory_commit != world.closed_out[1]


def test_a_route_condition_reads_the_one_governing_family_row_across_attempts() -> None:
    """MIK-R06 rule 3 reads "the latest family row": an answer the frozen attempt gave counts until
    a later attempt's row about the family replaces it. A ``changed`` row that replaces it answers
    the condition too; a ``no_impact`` row does not, and the row is then named again."""

    def governing(*dispositions: str) -> HistoryFile | None:
        files = []
        for attempt, disposition in enumerate(dispositions, start=1):
            row = {**family_row({}), "id": f"ROW-00000{attempt}", "items": []}
            document = {"leaf": LEAF, "closed": attempt < len(dispositions)}
            numbered = {"attempt": attempt} if attempt > 1 else {}
            rows = {"rows": [{**row, "disposition": disposition}]}
            files.append(HistoryFile.model_validate(document | numbered | rows))
        return merged_leaf_history(files)

    item = {"subject": "FAM-F00001#route_unassigned", "facts": {"recordSatisfiesRoutes": True}}
    assert not family_route_item_open(item, governing("assigned"))
    assert not family_route_item_open(item, governing("assigned", "changed"))
    assert family_route_item_open(item, governing("assigned", "no_impact"))
    assert not family_route_item_open(item, governing("assigned", "no_impact", "assigned"))
    assert family_route_item_open(item, governing("no_impact")) and family_route_item_open(
        item, None
    )


def test_the_writer_fills_a_rows_items_from_the_leafs_worklist(world: Gated, ports: None) -> None:
    """L37 P1c, C4 (MIK-R07 rule 1): a row whose hand-off names no item lists the worklist items it
    answers, by each kind's own satisfying-row rule. Named items are kept, and a missing or
    malformed worklist fills nothing and refuses nothing."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    worklist = world.gate().worklist
    ids: dict[str, list[str]] = {}
    for item in worklist["items"]:
        ids.setdefault(f"{item['kind']} {item['subject']}", []).append(item["id"])

    def stored() -> dict[str, list[str]]:
        rows = json.loads((world.memory / FIRST).read_text())["rows"]
        return {row["subject"]: row["items"] for row in rows}

    _write(world, _rows_for_the_edit())  # no worklist: as before, nothing is filled in
    assert set(map(tuple, stored().values())) == {()}
    named = "sha256:" + "0" * 64
    invariant, family, *traces = _rows_for_the_edit()["history"]
    odd = {"kind": "touched_invariant", "subject": "INV-AAAAAA", "id": "not an item id"}
    worklist = {**worklist, "items": [*worklist["items"], odd, {**odd, "id": 7}, "not an item"]}
    _write(world, _handoff({**invariant, "items": [named]}, family, *traces), worklist)
    items = stored()
    assert items["INV-AAAAAA"] == [named]  # what the hand-off names is kept
    assert items["FAM-F00001"] == sorted(ids["reached_family FAM-F00001"])
    assert all(items[subject] == ids[f"onboarding_trace {subject}"] for subject in TRACES)
    _write(world, _handoff(invariant), worklist)
    assert stored()["INV-AAAAAA"] == ids["touched_invariant INV-AAAAAA"]
    assert world.gate().ok  # the field is informational: the gate matches rows by subject


def test_a_history_file_closed_by_a_hand_commit_is_not_frozen(world: Gated, ports: None) -> None:
    """Only a recorded closeout freezes a history file (L09 review R1, finding 1). A file closed
    and committed by hand, whose row contradicts K_C, is still read at the closeout, whatever was
    committed on top of it; and the gate's memo tells two leaf heads apart."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    before = world.reanchor("RLZ-A00001")
    stale = world.invariant_row("INV-AAAAAA", before)
    stale["covers"][0]["after"] = before["RLZ-A00001"]  # the entry's old anchor: contradicts K_C
    examined = {"INV-AAAAAA": 1, "INV-BBBBBB": 1}
    world.rows(stale, family_row(examined), *trace_rows(*TRACES), closed=True)
    code = commit(world.code, {})
    commit(world.memory, {}, trailer=code)  # the closed file, committed by hand
    commit(world.memory, {"notes.md": "one more commit\n"}, trailer=code)
    refusal = _refused(world, code)
    assert "R09-history-rows" in refusal and "RLZ-A00001" in refusal

    tip, candidate = git(world.memory, "rev-parse", "main"), world.candidate()
    keys = {memo.memo_key(world.contract, candidate, tip, head) for head in ("a" * 40, "b" * 40)}
    assert len(keys) == 2 and None not in keys


def test_a_recovered_closeout_is_gated_like_a_fresh_one(world: Gated, ports: None) -> None:
    """A closeout that resumes with a memory commit already made records that commit: the gate
    judges it again. A commit the gate passed passes; one made without the gate is refused."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _rows_for_the_edit())
    code = commit(world.code, {})
    gated = _closeout(world, code).memory_commit
    passed = LifecycleOperationRecoveryCommits(codeCommit=code, memoryContentCommit=gated)
    assert _closeout(world, code, refused=True, recovered=passed).memory_commit == gated

    _edit(world, CODE_A.replace("return value", "return +value"))
    later = commit(world.code, {})
    ungated = commit(world.memory, {"notes.md": "committed by hand\n"}, trailer=later)
    slipped = LifecycleOperationRecoveryCommits(codeCommit=later, memoryContentCommit=ungated)
    refusal = _refused(world, later, recovered=slipped)
    assert "[knowledge-item-open] touched_invariant INV-AAAAAA" in refusal

    # The finalization of a recovered closeout asks the same gate before it writes the contract.
    contract = world.contract
    args = WorktreeArgs(contract_path=world.contract_path(), recovery_commits=slipped)
    proved = mock.Mock(side_effect=AssertionError("finalized an ungated memory commit"))
    with (
        mock.patch.object(closeout, "load_contract", return_value=contract),
        mock.patch.object(closeout, "require_ordinary_worktree"),
        mock.patch.object(closeout, "_closeout_approval_note", return_value="approved"),
        mock.patch.object(closeout, "publish_closeout_under_authority", lambda _c, run: run()),
        mock.patch.object(closeout, "prove_closeout_recovery_commits", proved),
        pytest.raises(RuntimeError, match="knowledge-item-open"),
    ):
        closeout._recover_closeout_finalization(contract, args)
    proved.assert_not_called()


def test_the_closeouts_own_writes_cannot_trip_its_gate(world: _Continued, ports: None) -> None:
    """L37 P1c, C9. The closeout writes memory content itself: the entity fingerprints, the route
    indexes, the ledger cache's ignore rule and the history closing (a converted tree gets no
    onboarding or overview stamps). Each is on disk before the gate reads the tree, the tree the
    gate judges is the tree committed, and the leaf's worklist is the same before and after."""

    _edit(world, CODE_A.replace("return value", "return -value"))
    _write(world, _rows_for_the_edit())
    catalog = world.memory / "onboarding/entities.md"
    catalog.write_text(
        "# Entities\n\n| Entity | Algorithm | Fingerprint | EvidencePaths |\n"
        f"| --- | --- | --- | --- |\n| `landing` | `git-blob-set-v1` | `stale` | `{A}` |\n",
        encoding="utf-8",
    )
    code = commit(world.code, {})
    before, authored = world.gate(), world.candidate().memory
    assert before.ok
    judged: list[str] = []
    gate = closeout_external.leaf_gate_refusal

    def judging(contract: Any, *, code_tree: str, memory_tree: str) -> str | None:
        judged.append(memory_tree)
        return gate(contract, code_tree=code_tree, memory_tree=memory_tree)

    with mock.patch.object(closeout_external, "leaf_gate_refusal", judging):
        outcome = _closeout(world, code, own_writes=True)

    committed = outcome.memory_commit
    assert judged == [git(world.memory, "rev-parse", f"{committed}^{{tree}}")]  # the exact tree
    # What the closeout itself wrote: the committed tree against the tree the curator left.
    wrote = git(world.memory, "diff", "--name-only", authored, committed).splitlines()
    indexes = [  # real memory repositories ignore these caches
        "onboarding/overview.index.json",
        "onboarding/pkg/overview.index.json",
    ]
    assert wrote == [".gitignore", FIRST, "onboarding/entities.md", *indexes]  # no card is stamped
    assert "/memory.md" in git(world.memory, "show", f"{committed}:.gitignore")
    assert "`stale`" not in git(world.memory, "show", f"{committed}:onboarding/entities.md")
    assert [one["entity"] for one in outcome.refreshed_entities] == ["landing"]
    assert outcome.route_index_refresh["written"] >= 1
    assert json.loads(git(world.memory, "show", f"{committed}:{FIRST}"))["closed"] is True
    after = world.gate()  # over the committed pair: no item opened, no row went stale
    assert after.ok and after.worklist["digest"] == before.worklist["digest"]
    assert after.worklist["items"] == before.worklist["items"]
