"""A code-only closed task retains its exact committed knowledge and continued family review."""

from __future__ import annotations

import gc
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import list_knowledge_review_entries
from agents_remember.application.review_committed_leaf import HISTORY_RECORDED_ENDPOINTS
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.worktrees.worktree_contract import write_contract
from diff_scope_test_support import _git
from test_historical_committed_leaf_review import _body, _entry_params, _serve, _subject_params
from test_knowledge_review_source_endpoints import LEAF_ID, _commit, build_endpoint_fixture
from test_review_family_context import family_request, review

pytestmark = pytest.mark.evidence_unit


def _closed_endpoints(tmp_path: Path):
    fixture = build_endpoint_fixture(tmp_path, memory_mode="external")
    memory = fixture.contract.memory_worktree
    assert memory is not None
    shutil.copyfile(fixture.diff.before.database_path, memory / "knowledge.sqlite")
    memory_base = _commit(memory, "Publish the task's knowledge foundation")
    (memory / "note.md").write_text("A code-only task; knowledge is unchanged.\n")
    memory_head = _commit(memory, "Record memory without changing knowledge")
    code_head = _commit(fixture.worktree, "Deliver source changes")
    contract = replace(
        fixture.contract,
        code_commit=code_head,
        memory_base_commit=memory_base,
        memory_content_commit=memory_head,
    )
    write_contract(contract.contract_path, contract)
    return replace(fixture, contract=contract)


def test_closed_code_only_leaf_reads_recorded_knowledge_and_continues_family(tmp_path: Path):
    fixture = _closed_endpoints(tmp_path)
    expected = dataset_identity(fixture.diff.before.database_path)
    shutil.rmtree(fixture.contract.worktree_group)
    entries = list_knowledge_review_entries(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert entries.state == "entries", entries.refusal
    assert entries.invariant_total > 0 and entries.family_total > 0
    subject = {**_subject_params(fixture), "selectorId": fixture.diff.sibling_invariant_id}
    status, served = _serve(fixture, subject)
    assert status == 200, served
    payload = _body(served)["payload"]
    assert HISTORY_RECORDED_ENDPOINTS in payload["limitations"]
    assert (
        payload["knowledge"]["before_statement"]["text"]
        == payload["knowledge"]["after_statement"]["text"]
    )
    assert payload["source"]["inventory"]["listed_total"] > 0
    assert payload["family_context"]["entries"]

    # Each request creates new temporary paths, but the logical comparison and cursors stay fixed.
    first = review(fixture, family_request(fixture, page_size=1))
    walks = [
        side.page
        for family in first.family_context.entries
        for side in (family.before, family.after)
        if side.page is not None and side.page.continuation is not None
    ]
    assert walks
    cursor = walks[0].continuation
    second = review(
        fixture, family_request(fixture, page_of="family_members", continuation=cursor, page_size=1)
    )
    assert second.page_refusal is None
    assert second.page is not None and second.page.state == "continued"
    assert first.comparison == second.comparison

    # The current repository file can move; only the task's recorded commits are authority.
    repository = fixture.contract.memory_repo_path
    assert repository is not None
    (repository / "knowledge.sqlite").write_bytes(b"today's unrelated broken database")
    for _ in range(2):
        resolved = fixture.resolve()
        assert dataset_identity(resolved.candidate_database) == expected
        directory = resolved.candidate_database.parent.parent
        del resolved
        gc.collect()
        assert not directory.exists()
    repeated_status, repeated = _serve(fixture, subject)
    assert repeated_status == 200
    assert repeated == served


def test_recorded_commit_without_knowledge_stays_source_only(tmp_path: Path):
    fixture = _closed_endpoints(tmp_path)
    memory = fixture.contract.memory_worktree
    assert memory is not None
    _git(memory, ["rm", "knowledge.sqlite"])
    missing = _commit(memory, "Remove the recorded knowledge file")
    write_contract(
        fixture.contract.contract_path,
        replace(fixture.contract, memory_base_commit=missing, memory_content_commit=missing),
    )
    shutil.rmtree(fixture.contract.worktree_group)
    entries = list_knowledge_review_entries(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert entries.state == "refused" and entries.refusal is not None
    assert "knowledge.sqlite" in entries.refusal.detail
    assert "predates" not in entries.refusal.detail
    status, served = _serve(fixture, _entry_params(fixture))
    assert status == 200 and _body(served)["payload"]["source"]["inventory"]["listed_total"] > 0
