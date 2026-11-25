"""Conversion recovers the author of an old revision from archived review trees."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.cli.__main__ import main
from agents_remember.memory.conversion.crossing import merge_trees
from agents_remember.memory.reviewed_content import reviewed_content
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.converted_reviews import CONVERTED_REVIEWS_PATH
from agents_remember.models.knowledge_files.history_product import record_content_digest
from agents_remember_test_support.testing.knowledge_crossing_standing import (
    changed_row,
    encoded,
    matrix_credits,
    matrix_standing,
)
from knowledge_conversion_test_support import commit_files, git, init_repository
from knowledge_review_fact_test_support import history_document
from knowledge_validator_test_support import invariant_document, invariant_path
from test_knowledge_state_conversion import _task_document, _write

TASK = "261112-FIX"
OWNER = "261112-FIX-L2"
PEER = "261112-FIX-L3"
ARCHIVE = "0_archive/261112_legacy"
RECORD = "INV-A00001"
REVISION = 2
RECORD_PATH = invariant_path(RECORD, "retained")
RENAMED_PATH = invariant_path(RECORD, "renamed")
UNKNOWN = ("unknown", "unknown", "unknown")


@dataclass(frozen=True)
class ConversionCase:
    exact_review: bool
    competing_edit: bool
    expected_owner: str | None


@dataclass(frozen=True)
class LegacyBranch:
    owner: str
    files: dict[str, bytes]
    reviewed_tree: str


def _close_legacy_branch(
    memory: Path, base: str, owner: str, record: dict[str, Any]
) -> LegacyBranch:
    git(memory, "switch", "-q", "-c", owner, base)
    row = {
        **changed_row(RECORD, REVISION),
        "id": f"ROW-A0000{owner[-1]}",
        "reason": "The writer changed the meaning of the rule.",
    }
    path, history = history_document(owner, rows=(row,))
    record_text = canonical_text(record)
    reviewed = commit_files(
        memory,
        {RECORD_PATH: record_text, path: canonical_text(history)},
        "Write the revision and its pre-stamp history row",
    )
    closed = canonical_text({**history, "closed": True})
    commit_files(memory, {path: closed}, "Close the reviewed history")
    reviewed_tree = git(memory, "rev-parse", f"{reviewed}^{{tree}}")
    return LegacyBranch(
        owner, {RECORD_PATH: record_text.encode(), path: closed.encode()}, reviewed_tree
    )


def _archive_pass(tasks: Path, owner: str, tree: str | None) -> None:
    leaf = _task_document(owner, "subTask", [(1, True)])
    state = leaf["memoryReviewState"]
    if tree is None:
        del state["candidateTree"]
    else:
        state["candidateTree"] = tree
    _write(tasks, f"{ARCHIVE}/0{owner[-1]}_leaf.json", leaf)


def _legacy_base(memory: Path, tasks: Path) -> tuple[dict[str, Any], str]:
    init_repository(memory)
    _write(tasks, f"{ARCHIVE}/task.json", _task_document(TASK, "master", []))
    before = invariant_document(
        RECORD, status="proposed", revision=1, origin={"task": TASK, "leaf": f"{TASK}-L1"}
    )
    base_commit = commit_files(
        memory,
        {
            RECORD_PATH: canonical_text(before),
            "knowledge/layout.json": canonical_text(
                {"schema": "ar-memory-layout/v2", "conversion": "1"}
            ),
        },
        "Store the common legacy base",
    )
    return before, base_commit


def _integrate_legacy_branches(
    memory: Path, before: dict[str, Any], own: LegacyBranch, peer: LegacyBranch
) -> None:
    base = {RECORD_PATH: encoded(before)}
    merged = merge_trees(base, own.files, peer.files, repository=memory)
    assert not merged.conflicts
    assert merged.files[RECORD_PATH] == own.files[RECORD_PATH]
    git(memory, "switch", "-q", own.owner)
    git(memory, "merge", "--no-commit", "--no-ff", "-s", "ours", peer.owner)
    commit_files(
        memory,
        {path: data.decode() for path, data in merged.files.items() if data is not None},
        "Integrate the legacy edits and their closed histories",
    )
    assert len(git(memory, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3


def _merge_legacy_edits(memory: Path, tasks: Path, case: ConversionCase) -> None:
    before, base_commit = _legacy_base(memory, tasks)
    peer = {**before, "revision": REVISION, "statement": "The record states the revised rule."}
    retained = {**peer, "applicability": "The rule applies to the narrowed case."}
    own = _close_legacy_branch(memory, base_commit, OWNER, retained)
    _archive_pass(tasks, OWNER, own.reviewed_tree if case.exact_review else None)
    if case.competing_edit:
        incoming = _close_legacy_branch(memory, base_commit, PEER, peer)
        _archive_pass(tasks, PEER, incoming.reviewed_tree)
        _integrate_legacy_branches(memory, before, own, incoming)


def _apply_conversion(tmp_path: Path) -> dict[str, Any]:
    report = tmp_path / "report.json"
    assert (
        main(
            [
                "knowledge-convert-states",
                "--memory-root",
                str(tmp_path / "memory"),
                "--task-root",
                str(tmp_path / "tasks"),
                "--apply",
                "--report",
                str(report),
            ]
        )
        == 0
    )
    return json.loads(report.read_bytes())


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(ConversionCase(True, True, OWNER), id="exact-legacy-pass"),
        pytest.param(ConversionCase(False, False, OWNER), id="unambiguous-unbound-pass"),
        pytest.param(ConversionCase(False, True, None), id="competing-unbound-pass"),
    ],
)
def test_conversion_attributes_the_retained_meaning(tmp_path: Path, case: ConversionCase) -> None:
    memory = tmp_path / "memory"
    tasks = tmp_path / "tasks"
    _merge_legacy_edits(memory, tasks, case)
    histories = {path: path.read_bytes() for path in (memory / "knowledge/history").glob("*.json")}
    result = _apply_conversion(tmp_path)
    reviewed = case.expected_owner is not None
    state = "reviewed" if reviewed else "proposed"
    converted = json.loads((memory / RECORD_PATH).read_bytes())
    facts = json.loads((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    assert converted["writer"].get("leaf") == case.expected_owner
    assert converted["writer"]["by"] == "conversion"
    assert tuple(converted["writer"][field] for field in ("role", "harness", "model")) == UNKNOWN
    assert result["counts"]["reviewed"] == int(reviewed)
    assert result["counts"]["reviewFactsAdded"] == len(facts["reviews"]) == 1 + case.competing_edit
    assert all(path.read_bytes() == before for path, before in histories.items())
    assert matrix_standing(memory, tmp_path, RECORD) == (state, UNKNOWN)
    assert matrix_credits(memory, tmp_path)[f"{RECORD}@{REVISION}"] == (UNKNOWN, reviewed)


@pytest.mark.parametrize("both_pass", [True, False], ids=["both-pass", "one-pass-control"])
@pytest.mark.parametrize(
    "owners", [(OWNER, PEER), (PEER, OWNER)], ids=["owner-first", "owner-last"]
)
def test_identical_legacy_edits_stay_reviewed(
    tmp_path: Path, owners: tuple[str, str], both_pass: bool
) -> None:
    memory = tmp_path / "memory"
    tasks = tmp_path / "tasks"
    before, base = _legacy_base(memory, tasks)
    authored = {**before, "revision": REVISION, "statement": "Both leaves corrected the sentence."}
    own = _close_legacy_branch(memory, base, owners[0], authored)
    peer = _close_legacy_branch(memory, base, owners[1], authored)
    assert own.files[RECORD_PATH] == peer.files[RECORD_PATH]
    assert own.reviewed_tree != peer.reviewed_tree
    _archive_pass(tasks, own.owner, own.reviewed_tree)
    expected_facts = {own.owner: own.reviewed_tree}
    if both_pass:
        _archive_pass(tasks, peer.owner, peer.reviewed_tree)
        expected_facts[peer.owner] = peer.reviewed_tree
    else:
        _write(
            tasks,
            f"{ARCHIVE}/0{peer.owner[-1]}_leaf.json",
            _task_document(peer.owner, "subTask", []),
        )
    _integrate_legacy_branches(memory, before, own, peer)
    git(memory, "mv", RECORD_PATH, RENAMED_PATH)
    commit_files(memory, {}, "Rename the record without changing its meaning")

    frozen = {path: data for path, data in (own.files | peer.files).items() if path != RECORD_PATH}
    digest = record_content_digest(authored)
    matches = reviewed_content(memory)
    for branch in (own, peer):
        assert matches(branch.reviewed_tree, RECORD, REVISION, digest) is True
    result = _apply_conversion(tmp_path)
    assert {path: (memory / path).read_bytes() for path in frozen} == frozen
    facts = json.loads((memory / CONVERTED_REVIEWS_PATH).read_bytes())
    assert {entry["owner"]: entry["fact"]["tree"] for entry in facts["reviews"]} == expected_facts
    converted = json.loads((memory / RENAMED_PATH).read_bytes())
    assert record_content_digest(converted) == digest
    assert "status" not in converted
    assert converted["writer"].get("leaf") == (None if both_pass else own.owner)
    assert result["counts"]["reviewed"] == 1
    assert matrix_standing(memory, tmp_path, RECORD) == ("reviewed", UNKNOWN)
    assert matrix_credits(memory, tmp_path)[f"{RECORD}@{REVISION}"] == (UNKNOWN, True)
