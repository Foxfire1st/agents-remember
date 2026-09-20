"""Issue #54: worktree_sync pulls the moved official line into a live worktree."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import uuid4

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import pytest

# The two identities the delete/reference scenario authors: an anchor the left side removes and the
# claim the right side's own row cites. Fixed rather than random so a failure names the same rows.
CONFLICT_ANCHOR_ID = "33333333-3333-4333-8333-333333333333"
CONFLICT_CLAIM_ID = "44444444-4444-4444-8444-444444444444"

from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.models.knowledge.merge import AuthoredReconciliation
from agents_remember.worktrees import sync_transaction_git
from agents_remember.worktrees.integration.closeout.door_evidence import memory_candidate_tree
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.cleanup import (
    _terminal_mutation_authority,
    remove_registered_worktree,
)
from agents_remember.worktrees.modules.startup.start_memory import prepare_memory_for_start
from agents_remember.worktrees.modules.sync import sync_result
from agents_remember.worktrees.modules.terminal_validation import terminal_preflight
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationStore,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    load_contract,
    write_contract,
)
from generation_test_support import create_generation_2_store
from merge_case_test_support import (
    BASE_INVARIANT_ID,
    BASE_REVISION_ID,
    add_anchor,
    add_realization_claim,
    delete_anchor,
    file_digest,
    labels_of,
    row_counts,
    set_label,
    statements_of,
)
from merge_case_test_support import build_case as merge_case_build


class SyncFixture:
    """Live code/memory worktrees whose official lines can be moved."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.code_repo = root / "repo-a"
        self.code_base = make_repo(self.code_repo)
        self.memory_repo = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
        memory_seed = make_repo(self.memory_repo)
        write_ledger(
            self.memory_repo / "memory.md",
            create_initial_ledger("repo-a", self.code_base, memory_seed),
        )
        git(self.memory_repo, "add", "memory.md")
        git(self.memory_repo, "commit", "-m", "Add memory ledger")
        self.memory_base = git(self.memory_repo, "rev-parse", "HEAD")
        self.contract = default_contract(
            ContractTask(
                name="Sync Thing",
                repo_name="repo-a",
                coordination_root=root / "ar-coordination",
                workflow_kind="light-task",
                memory_mode="external",
            ),
            leaf=LeafIdentity(worktree_name="sync-thing"),
            code=RepoBranchPlan(
                repo_path=self.code_repo,
                source_branch="main",
                work_branch="ar/sync-thing",
                base_commit=self.code_base,
            ),
            memory=RepoBranchPlan(
                repo_path=self.memory_repo,
                source_branch="main",
                work_branch="ar/sync-thing",
                base_commit=self.memory_base,
            ),
        )
        assert self.contract.memory_worktree is not None
        git(
            self.code_repo,
            "worktree",
            "add",
            "-b",
            self.contract.code_work_branch,
            str(self.contract.code_worktree),
            "main",
        )
        git(
            self.memory_repo,
            "worktree",
            "add",
            "-b",
            self.contract.memory_work_branch,
            str(self.contract.memory_worktree),
            "main",
        )
        write_contract(self.contract.contract_path, self.contract)

    def move_official_code(self) -> str:
        commit_file(self.code_repo, "src/new.py", "VALUE = 'landed'")
        return git(self.code_repo, "rev-parse", "main")

    def map_official_memory(self, code_tip: str) -> str:
        """Land memory content attributed to the admitted code commit."""
        target = self.memory_repo / "onboarding/src/new.py.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# new.py onboarding\n", encoding="utf-8")
        git(self.memory_repo, "add", "onboarding/src/new.py.md")
        git(
            self.memory_repo,
            "commit",
            "-m",
            render_memory_content_message("Update new.py onboarding", code_tip),
        )
        return git(self.memory_repo, "rev-parse", "main")

    def sync(self, **kwargs: Any):
        return sync_result(WorktreeArgs(contract_path=self.contract.contract_path, **kwargs))


def _assert_knowledge_database_conflict_settles(case, fixture, member: str) -> None:
    """CYCLE-02: a divergent knowledge dataset reconciles without the agent calling private code.

    The finding, reproduced the way the external review reproduced it: a real sync over two branches
    making DISJOINT valid database changes performs an ordinary Git merge, stops on the binary file,
    and reports ``sync-resolution-required`` with ``resolutionOwner: agent``. The union was then only
    obtainable by an explicit manual call to ``resolve_knowledge_merge_base`` and
    ``merge_resolved_knowledge_datasets`` -- functions an agent should not have to discover, because
    the substrate's own composition seam is supposed to route them.

    A knowledge database is not text: Git can only call it binary, and no amount of staging resolves
    it. So the transaction itself has to route the three-way merge through the shipped adapter, which
    is what ``application/knowledge_merge.py`` exists for -- its own docstring says it is
    "deliberately callable rather than wired" and that a separately reviewed change turns it into a
    driver. This is that change's proof.

    What is asserted is what the review asked for: the sync COMPLETES rather than stopping for the
    agent, BOTH sides survive in the merged dataset, and the caller never invokes a merge function
    itself. A structurally merged outcome is not a compatibility verdict, and nothing here says it
    is.
    """

    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    left, right = _stage_knowledge_divergence(case, fixture, member)
    inputs = {role: file_digest(case.state_path(role)) for role in ("base", "left", "right")}

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "synced", result.payload
    merged = dataset_identity(worktree / member)
    assert merged != case.identity("left")
    assert merged != case.identity("right")
    assert {role: file_digest(case.state_path(role)) for role in inputs} == inputs
    assert sorted(git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]) == sorted(
        [left, right]
    )


def _stage_knowledge_divergence(case, fixture, member: str) -> tuple[str, str]:
    """Commit the base, take the bootstrap sync, then commit each side's own dataset.

    Returns the two side heads. Both sides are real commits on the memory repository and its work
    branch, so the conflict the transaction meets is a property of the commit graph rather than of
    the fixture's bookkeeping.
    """

    def commit(checkout: Path, role: str) -> str:
        shutil.copyfile(case.state_path(role), checkout / member)
        git(checkout, "add", member)
        git(
            checkout,
            "commit",
            "-m",
            render_memory_content_message(f"{role} knowledge snapshot", fixture.code_base),
        )
        return git(checkout, "rev-parse", "HEAD")

    commit(fixture.memory_repo, "base")
    bootstrap = fixture.sync()
    assert bootstrap.payload["state"] == "synced", bootstrap.payload
    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    assert dataset_identity(worktree / member) == case.identity("base")
    return commit(worktree, "left"), commit(fixture.memory_repo, "right")


def _shape_label_conflict(case, states: dict[str, Path]) -> None:
    set_label(states["left"], "left conflicting label")
    set_label(states["right"], "right conflicting label")


def _shape_delete_reference(case, states: dict[str, Path]) -> None:
    delete_anchor(states["left"], CONFLICT_ANCHOR_ID)
    add_realization_claim(
        states["right"],
        f"{case.repository.repository_id}/{CONFLICT_CLAIM_ID}",
        BASE_REVISION_ID,
        CONFLICT_ANCHOR_ID,
    )


def _shape_delete_reference_reversed(case, states: dict[str, Path]) -> None:
    """The OTHER orientation: the arriving (right) side removes the row the left still cites.

    Same conflict code, same absence of a row in the diagnosis, and no recovery through a decision:
    the only retraction a row-less decision performs removes a row the arriving delta *inserted*, and
    this arriving delta inserted nothing. This is the orientation whose response used to advertise
    that impossible call and repeat it byte-for-byte forever.
    """

    add_realization_claim(
        states["left"],
        f"{case.repository.repository_id}/{CONFLICT_CLAIM_ID}",
        BASE_REVISION_ID,
        CONFLICT_ANCHOR_ID,
    )
    delete_anchor(states["right"], CONFLICT_ANCHOR_ID)


def _base_shape_anchor(case, states: dict[str, Path]) -> None:
    add_anchor(states["base"], CONFLICT_ANCHOR_ID, "src/anchors.py")


def _shape_schema_disagreement(case, states: dict[str, Path]) -> None:
    """Make each side differ from the base, one of them at another recorded schema generation.

    Both sides must differ or the merge is a fast-forward and the transaction never reaches the
    adapter; the right side is replaced by a genuine older-generation dataset so the disagreement the
    preflight reports is a real structural pair rather than a doctored version number.
    """

    set_label(states["left"], "the left side's own reconciled label")
    states["right"].unlink()
    with create_generation_2_store(states["right"], case.repository.repository_id):
        pass


def _assert_knowledge_conflict_is_diagnosed_and_reconciled(case, fixture, member: str) -> None:
    """CYCLE-02 remainder: the engine's diagnosis reaches the agent, and one decision settles it.

    The measured defect this protects: the engine already refused the merge with the table, the
    operation, the exact row and the reconcile-and-retry action, and the sync response reported only
    ``sync-resolution-required`` and ``files: ["knowledge.sqlite"]``. An agent could not tell what to
    reconcile without implementation knowledge or an outside database tool.

    What is asserted is the whole supported operation: the diagnosis is in the response, the row it
    names is the row the engine refused, a decision for another row is refused with that record
    named, the preview mutates nothing, and the authored decision settles the merge in one call with
    the decision's own effect visible in the committed dataset.
    """

    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    left, right = _stage_knowledge_divergence(case, fixture, member)

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "sync-resolution-required", result.payload
    knowledge = section(section(result.payload, "resolution"), "knowledge")
    conflict = section(knowledge, "conflict")
    assert conflict["code"] == "conflicting_values"
    assert conflict["attribution"] == "engine_attributed"
    assert (conflict["table"], conflict["operation"]) == ("invariant", "UPDATE")
    assert str(conflict["record_id"]).endswith(BASE_INVARIANT_ID), conflict
    assert knowledge["decisions"] == ["keep-left", "keep-right"]
    assert (
        "Reconcile the two authored values explicitly"
        in section(knowledge, "refusal")["next_action"]
    )
    # The advertised next move is the supported operation, and it names the row the engine refused
    # rather than the file that holds it.
    assert result.payload["nextOperation"] == "reconcile_knowledge_resolution"
    advertised = section(section(result.payload, "nextArgs"), "knowledge_resolution")
    assert advertised["table"] == conflict["table"]
    assert advertised["record_id"] == conflict["record_id"]
    # A decision for any other row is refused, and the refusal names the record that was refused.
    wrong_record = fixture.sync(
        resolution_action="reconcile",
        knowledge_resolution=AuthoredReconciliation(
            table="invariant",
            record_id=f"{case.repository.repository_id}/{uuid4()}",
            decision="keep-left",
        ),
    )
    assert wrong_record.payload["state"] == "sync-input-invalid", wrong_record.payload
    assert str(conflict["record_id"]) in str(wrong_record.payload["summary"])
    assert git(worktree, "diff", "--name-only", "--diff-filter=U") == member
    assert git(worktree, "rev-parse", "HEAD") == left
    # The preview is a read: nothing is applied and nothing moves.
    preview = fixture.sync(
        resolution_action="reconcile",
        knowledge_resolution=AuthoredReconciliation(
            table="invariant",
            record_id=str(conflict["record_id"]),
            decision="keep-left",
        ),
        dry_run=True,
    )
    assert preview.payload["state"] == "would-reconcile-knowledge-conflict", preview.payload
    assert git(worktree, "rev-parse", "HEAD") == left
    assert git(worktree, "diff", "--name-only", "--diff-filter=U") == member

    reconciled = fixture.sync(
        resolution_action="reconcile",
        knowledge_resolution=AuthoredReconciliation(
            table="invariant",
            record_id=str(conflict["record_id"]),
            decision="keep-left",
        ),
    )

    assert reconciled.payload["state"] == "synced", reconciled.payload
    # The decision's own effect: the left value stands, and both sides' other rows landed.
    assert labels_of(worktree / member) == [
        "left conflicting label",
        "the left side's own obligation",
        "the right side's own obligation",
    ]
    assert set(statements_of(worktree / member)) == set(
        statements_of(case.state_path("left"))
    ) | set(statements_of(case.state_path("right")))
    assert sorted(git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]) == sorted(
        [left, right]
    )


def _assert_delete_reference_conflict_is_retracted(case, fixture, member: str) -> None:
    """The one conflict SQLite reports without a row is still recoverable through one decision.

    The engine refuses a merge in which one side removed a row the other side's new row still
    references, and it cannot name that row: the conflict callback receives no change at all. The
    recovery is the refusal's own second option -- retract the arriving reference -- expressed as the
    row-less decision the response advertises, and it is bounded to the arriving rows SQLite itself
    reports as breaking a reference.
    """

    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    left, right = _stage_knowledge_divergence(case, fixture, member)

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "sync-resolution-required", result.payload
    knowledge = section(section(result.payload, "resolution"), "knowledge")
    assert section(knowledge, "conflict")["code"] == "delete_reference_conflict"
    assert section(knowledge, "conflict")["attribution"] == "engine_reported_without_row"
    assert knowledge["decisions"] == ["keep-left"]
    advertised = section(section(result.payload, "nextArgs"), "knowledge_resolution")
    assert advertised == {"decision": "<keep-left>"}
    # The overwrite direction is not offered, and the model refuses it rather than the engine
    # discovering it inside the merge.
    with pytest.raises(ValueError, match="can only retract"):
        AuthoredReconciliation(decision="keep-right")

    reconciled = fixture.sync(
        resolution_action="reconcile",
        knowledge_resolution=AuthoredReconciliation(decision="keep-left"),
    )

    assert reconciled.payload["state"] == "synced", reconciled.payload
    rows = row_counts(worktree / member)
    assert rows["source_anchor"] == 0, rows
    assert rows["realization_claim"] == 0, rows
    assert set(statements_of(worktree / member)) == set(
        statements_of(case.state_path("left"))
    ) | set(statements_of(case.state_path("right")))
    assert sorted(git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]) == sorted(
        [left, right]
    )


def _assert_unretractable_delete_reference_advertises_its_real_route(
    case, fixture, member: str
) -> None:
    """The other orientation of the same row-less conflict stops advertising a call that cannot work.

    The defect this protects: the arriving (right) side removed a row the retained side still cites,
    and the response reported the identical diagnosis as the recoverable orientation -- ``["keep-left"]``
    and ``nextOperation=reconcile_knowledge_resolution`` with a summary promising the merge continues.
    Driving exactly that advertised call returned a byte-identical response forever: the retraction
    ``keep-left`` performs removes rows the arriving delta *inserted*, and this arriving delta
    inserted nothing.

    What is asserted is the whole corrected surface and the acceptance the verifier applies to it: the
    diagnosis is still the engine's (same code, same attribution, still no row), no decision and no
    reconcile operation are advertised, the summary says what the caller must do instead, the call the
    response DOES advertise is the manual continuation, driving it moves the state rather than
    repeating the response, and no input or ref moved.
    """

    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    left, _right = _stage_knowledge_divergence(case, fixture, member)
    inputs = {role: file_digest(case.state_path(role)) for role in ("base", "left", "right")}

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "sync-resolution-required", result.payload
    knowledge = section(section(result.payload, "resolution"), "knowledge")
    # The diagnosis is the engine's and is unchanged: the same code, the same attribution, and the
    # same absence of a row -- the fact that narrowed is the *precondition*, not the diagnosis.
    assert section(knowledge, "conflict")["code"] == "delete_reference_conflict"
    assert section(knowledge, "conflict")["attribution"] == "engine_reported_without_row"
    assert section(knowledge, "conflict")["precondition"] == "no_arriving_insertion"
    assert "delete_reference_conflict" in str(result.payload["summary"])
    assert knowledge["decisions"] == []
    assert result.payload["nextOperation"] == "continue_sync_resolution"
    advertised = dict(section(result.payload, "nextArgs"))
    assert "knowledge_resolution" not in advertised
    assert advertised["resolution_action"] == "continue"
    # The response says what the caller must do instead of promising a continuation that cannot
    # happen, and it still names the conflicted path and the cancel route beside it.
    assert "restore the removed row" in str(result.payload["summary"])
    assert section(result.payload, "resolution")["files"] == [member]
    assert section(result.payload, "cancelArgs")["resolution_action"] == "cancel"

    # Driving exactly what the response advertised moves the state. A byte-identical repeat is the
    # failure this leaf exists for, so "the advertised call changed something" is asserted rather
    # than "the second response is one of these states".
    second = fixture.sync(resolution_action=advertised["resolution_action"])
    assert second.payload["state"] != result.payload["state"], second.payload
    assert second.payload["state"] == "sync-resolution-incomplete", second.payload
    assert git(worktree, "diff", "--name-only", "--diff-filter=U") == member
    assert git(worktree, "rev-parse", "HEAD") == left
    assert {role: file_digest(case.state_path(role)) for role in inputs} == inputs


def _assert_schema_disagreement_is_reported_not_reconciled(case, fixture, member: str) -> None:
    """A schema disagreement is refused explicitly, and no authored decision can settle it.

    The invariant this protects: a structural difference is *reported*, never reconciled, so the
    response must carry the refusal and must not offer a decision for it. Without this case a later
    change could quietly make a schema mismatch "reconcilable" and nothing would notice.
    """

    worktree = fixture.contract.memory_worktree
    assert worktree is not None
    left, _right = _stage_knowledge_divergence(case, fixture, member)

    result = fixture.sync(memory_sync_choice="merge-memory")

    assert result.payload["state"] == "sync-resolution-required", result.payload
    knowledge = section(section(result.payload, "resolution"), "knowledge")
    assert section(knowledge, "refusal")["code"] == "schema_mismatch"
    assert "next_action" in section(knowledge, "refusal")
    assert knowledge["decisions"] == []
    assert result.payload["nextOperation"] == "continue_sync_resolution"
    refused = fixture.sync(
        resolution_action="reconcile",
        knowledge_resolution=AuthoredReconciliation(
            table="invariant",
            record_id=f"{case.repository.repository_id}/{BASE_INVARIANT_ID}",
            decision="keep-left",
        ),
    )
    assert refused.payload["state"] == "sync-input-invalid", refused.payload
    assert "admits no authored decision" in str(refused.payload["summary"])
    # Nothing was settled and nothing moved: the conflict is still the agent's.
    assert git(worktree, "diff", "--name-only", "--diff-filter=U") == member
    assert git(worktree, "rev-parse", "HEAD") == left


class WorktreeSyncTests(unittest.TestCase):
    def test_pure_fast_forward_sync_advances_both_sides_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            code_tip = fixture.move_official_code()
            memory_tip = fixture.map_official_memory(code_tip)

            result = fixture.sync()

            self.assertEqual(result.payload["state"], "synced")
            self.assertEqual(section(result.payload, "code")["state"], "completed")
            self.assertEqual(section(result.payload, "code")["plan"], "fast-forward")
            self.assertEqual(section(result.payload, "memory")["state"], "completed")
            self.assertEqual(section(result.payload, "memory")["plan"], "fast-forward")
            self.assertEqual(git(fixture.contract.code_worktree, "rev-parse", "HEAD"), code_tip)
            assert fixture.contract.memory_worktree is not None
            self.assertEqual(git(fixture.contract.memory_worktree, "rev-parse", "HEAD"), memory_tip)
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.code_base_commit, code_tip)
            self.assertEqual(reloaded.memory_base_commit, memory_tip)
            self.assertEqual(len(reloaded.sync_log), 1)
            self.assertEqual(reloaded.sync_log[0]["codeBaseTo"], code_tip)

    def test_code_merge_conflict_is_retained_and_can_continue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            commit_file(fixture.contract.code_worktree, "README.md", "work-branch version")
            commit_file(fixture.code_repo, "README.md", "official version")
            code_tip = git(fixture.code_repo, "rev-parse", "main")
            fixture.map_official_memory(code_tip)

            pre_sync = git(fixture.contract.code_worktree, "rev-parse", "HEAD")
            result = fixture.sync()

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "sync-resolution-required")
            self.assertEqual(result.payload["status"], "agent-action-required")
            self.assertIn("README.md", section(result.payload, "resolution")["files"])
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "MERGE_HEAD"), code_tip
            )
            self.assertEqual(
                git(fixture.contract.code_worktree, "rev-parse", "HEAD"),
                pre_sync,
            )
            (fixture.contract.code_worktree / "README.md").write_text(
                "resolved version\n", encoding="utf-8"
            )
            git(fixture.contract.code_worktree, "add", "README.md")

            continued = fixture.sync(resolution_action="continue")

            self.assertEqual(continued.payload["state"], "synced")
            self.assertEqual(
                git(
                    fixture.contract.code_worktree, "rev-list", "--parents", "-n", "1", "HEAD"
                ).split()[1:],
                [pre_sync, code_tip],
            )

    def test_sync_uses_source_refs_when_the_cache_is_stale_missing_or_malformed(self) -> None:
        for cache_state in ("stale", "missing", "malformed"):
            with self.subTest(cache_state=cache_state), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                code_tip = fixture.move_official_code()
                cache = fixture.memory_repo / "memory.md"
                if cache_state == "missing":
                    cache.unlink()
                elif cache_state == "malformed":
                    cache.write_text("<<<<<<< broken cache\n", encoding="utf-8")
                if cache_state != "stale":
                    git(fixture.memory_repo, "add", "-A")
                    git(fixture.memory_repo, "commit", "-m", f"Historical {cache_state} cache")
                memory_tip = git(fixture.memory_repo, "rev-parse", "main")

                result = fixture.sync()

                self.assertEqual(result.returncode, 0, result.payload)
                self.assertEqual(result.payload["state"], "synced")
                self.assertEqual(git(fixture.contract.code_worktree, "rev-parse", "HEAD"), code_tip)
                assert fixture.contract.memory_worktree is not None
                self.assertEqual(
                    git(fixture.contract.memory_worktree, "rev-parse", "HEAD"), memory_tip
                )
                reloaded = load_contract(fixture.contract.contract_path)
                self.assertEqual(reloaded.code_base_commit, code_tip)
                self.assertEqual(reloaded.memory_base_commit, memory_tip)

    def test_start_and_memory_candidate_do_not_take_authority_from_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            contract = replace(fixture.contract, code_base_commit=fixture.move_official_code())
            assert contract.memory_worktree is not None
            source_cache = fixture.memory_repo / "memory.md"
            work_cache = contract.memory_worktree / "memory.md"
            original = source_cache.read_text(encoding="utf-8")
            tree = memory_candidate_tree(contract)
            for contents in (original, "<<<<<<< broken cache\n", None):
                for cache in (source_cache, work_cache):
                    if contents is None:
                        cache.unlink()
                    else:
                        cache.write_text(contents, encoding="utf-8")
                if git(fixture.memory_repo, "status", "--porcelain"):
                    git(fixture.memory_repo, "add", "-A")
                    git(fixture.memory_repo, "commit", "-m", "Historical cache contents")
                contract = replace(
                    contract, memory_base_commit=git(fixture.memory_repo, "rev-parse", "main")
                )

                prepared = prepare_memory_for_start(contract, WorktreeArgs(dry_run=True))

                self.assertEqual(prepared["state"], "compatible")
                self.assertEqual(prepared["lastVerifiedCodeCommit"], "")
                self.assertEqual(prepared["lastMemoryContentCommit"], "")
                self.assertEqual(memory_candidate_tree(contract), tree)
            assert contract.memory_worktree is not None
            (contract.memory_worktree / "real-memory.md").write_text(
                "new memory\n", encoding="utf-8"
            )
            self.assertNotEqual(memory_candidate_tree(contract), tree)

    def test_terminal_removal_discards_only_the_memory_cache(self) -> None:
        for state in ("tracked", "missing", "staged", "untracked"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                contract = fixture.contract
                memory = contract.memory_worktree
                assert memory is not None
                cache = memory / "memory.md"
                if state == "untracked":
                    git(memory, "rm", "--cached", "memory.md")
                    git(memory, "commit", "-m", "Fixture without a tracked cache")
                if state == "missing":
                    cache.unlink()
                else:
                    cache.write_text("<<<<<<< disposable cache\n", encoding="utf-8")
                if state == "staged":
                    git(memory, "add", "-f", "memory.md")
                plan = terminal_preflight(contract, mode="abandon", force=False)
                self.assertFalse([row for row in plan.blockers if "worktree" in row])

                real = memory / "memory.md.other"
                real.write_text("keep real memory\n", encoding="utf-8")
                plan = terminal_preflight(contract, mode="abandon", force=False)
                self.assertIn({"worktree": "memory", "reason": "dirty"}, plan.blockers)
                authority = _terminal_mutation_authority(contract, operation="worktree_abandon")
                refused = remove_registered_worktree(
                    fixture.memory_repo, memory, False, authority=authority
                )
                self.assertFalse(refused["removed"], refused)
                self.assertEqual(real.read_text(encoding="utf-8"), "keep real memory\n")
                real.unlink()

                code_file = contract.code_worktree / "memory.md"
                code_file.write_text("keep code content\n", encoding="utf-8")
                refused = remove_registered_worktree(
                    fixture.code_repo, contract.code_worktree, False, authority=authority
                )
                self.assertFalse(refused["removed"], refused)
                removed = remove_registered_worktree(
                    fixture.memory_repo, memory, False, authority=authority
                )
                self.assertTrue(removed["removed"], removed)
                self.assertEqual(code_file.read_text(encoding="utf-8"), "keep code content\n")

    def test_a_descendant_memory_ledger_that_dropped_a_source_row_is_current(self) -> None:
        """The projection is the ledger's authority, so a dropped row is not a sync refusal.

        Measured on the real master: thirteen rows the rebuild could not resolve (eleven
        stale duplicates whose code commits map to a different memory commit, two naming
        memory commits that exist nowhere) were dropped from the ledger by its own
        closeouts. This transaction required every row its source carried, so the master
        whose closeouts had been correct became permanently unsyncable, and the same rows
        refused the integration gate as well. The exclusion is the projection's to report
        (``read_ledger_source().excluded_rows`` -- pinned in ``test_memory_ledger``), and
        the sync is not a second place to restate that judgement.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            assert fixture.contract.memory_worktree is not None
            worktree = fixture.contract.memory_worktree
            # The work branch descends from its source and carries its own mapping for the
            # same code commit -- exactly the stale-duplicate shape, dropped from the table.
            commit_file(worktree, "onboarding/repo-a/README.md.md", "# README onboarding")
            content_commit = git(worktree, "rev-parse", "HEAD")
            write_ledger(
                worktree / "memory.md",
                create_initial_ledger("repo-a", fixture.code_base, content_commit),
            )
            git(worktree, "add", "memory.md")
            git(worktree, "commit", "-m", "Recompute the ledger from the commits")
            projected_head = git(worktree, "rev-parse", "HEAD")

            result = fixture.sync()

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.payload["state"], "already-current")
            self.assertNotIn("dropped parent mapping", str(result.payload))
            # Nothing moved: the branch already carried the source, so the sync is a read.
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), projected_head)
            reloaded = load_contract(fixture.contract.contract_path)
            self.assertEqual(reloaded.memory_base_commit, fixture.memory_base)

    def test_memory_merge_settles_content_and_knowledge_conflicts_in_the_transaction(
        self,
    ) -> None:
        """Every conflict shape one memory merge can meet, settled inside the transaction.

        The content shape is the shipped one and its body is unchanged; the knowledge shapes are
        CYCLE-02's: the disjoint divergence that settles itself, the same-row conflict whose
        diagnosis has to reach the agent and be settled by one authored decision, the referential
        conflict SQLite reports without a row, and the schema disagreement that is reported and never
        reconciled. They share one case rather than taking one each because the integration lane sits
        at its declared ceiling of 400 -- a further collected case would breach it, and above the
        ceiling conftest raises and the lane then runs ZERO tests (D-46's mechanism), which is worse
        than either outcome it would report.
        """

        with tempfile.TemporaryDirectory() as tmp:
            db_case = merge_case_build(Path(tmp) / "datasets")
            db_fixture = SyncFixture(Path(tmp) / "lifecycle")
            _assert_knowledge_database_conflict_settles(db_case, db_fixture, "knowledge.sqlite")

        self._assert_knowledge_conflict_scenarios()
        self._assert_memory_content_conflict_scenarios()

    def _assert_knowledge_conflict_scenarios(self) -> None:
        """The three retained-conflict shapes, each through the public sync and its own workspace.

        Each scenario builds its own repositories and its own lifecycle so no state can reach the
        next one; the helper functions above hold the assertions and say which operation each
        protects.
        """

        with tempfile.TemporaryDirectory() as tmp:
            same_record = merge_case_build(Path(tmp) / "same-record", shape=_shape_label_conflict)
            _assert_knowledge_conflict_is_diagnosed_and_reconciled(
                same_record, SyncFixture(Path(tmp) / "lifecycle"), "knowledge.sqlite"
            )
        with tempfile.TemporaryDirectory() as tmp:
            referential = merge_case_build(
                Path(tmp) / "referential",
                shape=_shape_delete_reference,
                base_shape=_base_shape_anchor,
            )
            _assert_delete_reference_conflict_is_retracted(
                referential, SyncFixture(Path(tmp) / "lifecycle"), "knowledge.sqlite"
            )
        with tempfile.TemporaryDirectory() as tmp:
            unrecoverable = merge_case_build(
                Path(tmp) / "unrecoverable",
                shape=_shape_delete_reference_reversed,
                base_shape=_base_shape_anchor,
            )
            _assert_unretractable_delete_reference_advertises_its_real_route(
                unrecoverable, SyncFixture(Path(tmp) / "lifecycle"), "knowledge.sqlite"
            )
        with tempfile.TemporaryDirectory() as tmp:
            schema = merge_case_build(
                Path(tmp) / "schema",
                shape=_shape_schema_disagreement,
                diverging_revisions=False,
                diverging_identities=False,
            )
            _assert_schema_disagreement_is_reported_not_reconciled(
                schema, SyncFixture(Path(tmp) / "lifecycle"), "knowledge.sqlite"
            )

    def _assert_memory_content_conflict_scenarios(self) -> None:
        """The shipped memory-merge conflict scenarios, moved intact out of the case above."""

        for real_conflict in (False, True):
            with self.subTest(real_conflict=real_conflict), tempfile.TemporaryDirectory() as tmp:
                fixture = SyncFixture(Path(tmp))
                worktree = fixture.contract.memory_worktree
                assert worktree is not None
                (worktree / "work-content.md").write_text("work content\n", encoding="utf-8")
                (worktree / "memory.md").write_text("work cache\n", encoding="utf-8")
                if real_conflict:
                    (worktree / "README.md").write_text("work version\n", encoding="utf-8")
                git(worktree, "add", "-A")
                git(worktree, "commit", "-m", "Work content with a historical cache")
                before = git(worktree, "rev-parse", "HEAD")
                code_tip = fixture.move_official_code()
                source = fixture.memory_repo
                (source / "source-content.md").write_text("source content\n", encoding="utf-8")
                (source / "memory.md").write_text("source cache\n", encoding="utf-8")
                if real_conflict:
                    (source / "README.md").write_text("source version\n", encoding="utf-8")
                git(source, "add", "-A")
                git(
                    source,
                    "commit",
                    "-m",
                    render_memory_content_message("Source content", code_tip),
                )
                source_tip = git(source, "rev-parse", "HEAD")
                (worktree / "draft.md").write_text("uncommitted candidate\n", encoding="utf-8")
                (worktree / "memory.md").write_text("staged stale cache\n", encoding="utf-8")
                git(worktree, "add", "memory.md")

                result = (
                    fixture.sync(memory_sync_choice="merge-memory")
                    if real_conflict
                    else self._resume_staged_memory_with_unstaged_content(fixture)
                )

                if real_conflict:
                    self.assertEqual(
                        result.payload["state"], "sync-resolution-required", result.payload
                    )
                    self.assertEqual(section(result.payload, "resolution")["files"], ["README.md"])
                    self.assertEqual(git(worktree, "rev-parse", "HEAD"), before)
                    self.assertEqual(git(worktree, "rev-parse", "MERGE_HEAD"), source_tip)
                    self.assertEqual(
                        git(worktree, "diff", "--name-only", "--diff-filter=U"), "README.md"
                    )
                    (worktree / "README.md").write_text("resolved content\n", encoding="utf-8")
                    git(worktree, "add", "README.md")
                    (worktree / "memory.md").write_text("still malformed\n", encoding="utf-8")
                    preview = fixture.sync(resolution_action="continue", dry_run=True)
                    self.assertEqual(preview.returncode, 0, preview.payload)
                    self.assertEqual(git(worktree, "rev-parse", "HEAD"), before)
                    result = fixture.sync(resolution_action="continue")
                self.assertEqual(result.payload["state"], "synced", result.payload)
                merged = git(worktree, "rev-parse", "HEAD")
                self.assertEqual(
                    git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:],
                    [before, source_tip],
                )
                self.assertEqual(
                    git(worktree, "rev-list", merged, "--not", before, source_tip).split(), [merged]
                )
                self.assertEqual(
                    git(worktree, "ls-tree", "--name-only", "HEAD", "--", "memory.md"), ""
                )
                self.assertEqual(git(worktree, "ls-files", "--", "memory.md"), "")
                self.assertTrue((worktree / "memory.md").is_file())
                self.assertEqual(
                    (worktree / "draft.md").read_text(encoding="utf-8"), "uncommitted candidate\n"
                )
                self.assertEqual(
                    (worktree / "work-content.md").read_text(encoding="utf-8"),
                    "work content\n" if real_conflict else "post-admission content\n",
                )
                self.assertEqual(
                    (worktree / "source-content.md").read_text(encoding="utf-8"), "source content\n"
                )
                self.assertEqual(section(result.payload, "memory")["wip"]["paths"], ["draft.md"])
                self.assertEqual(git(worktree, "stash", "list"), "")

    def _resume_staged_memory_with_unstaged_content(self, fixture: SyncFixture):
        worktree = fixture.contract.memory_worktree
        assert worktree is not None
        with mock.patch.object(
            sync_transaction_git,
            "_finish_staged_memory_merge",
            side_effect=sync_transaction_git.SyncGitProofError("interrupted before merge commit"),
        ):
            stopped = fixture.sync(memory_sync_choice="merge-memory")
        self.assertEqual(stopped.payload["state"], "sync-git-proof-failed", stopped.payload)
        before = git(worktree, "rev-parse", "HEAD")
        merge_source = git(worktree, "rev-parse", "MERGE_HEAD")
        refs_before = tuple(
            git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
            for repo in (fixture.code_repo, fixture.memory_repo)
        )
        (worktree / "work-content.md").write_text("post-admission content\n", encoding="utf-8")
        (worktree / "memory.md").write_text("post-admission cache\n", encoding="utf-8")

        refused = fixture.sync()

        self.assertEqual(refused.payload["state"], "sync-git-proof-failed", refused.payload)
        self.assertIn("unstaged changes", str(refused.payload))
        self.assertEqual(git(worktree, "rev-parse", "HEAD"), before)
        self.assertEqual(git(worktree, "rev-parse", "MERGE_HEAD"), merge_source)
        self.assertEqual(
            tuple(
                git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
                for repo in (fixture.code_repo, fixture.memory_repo)
            ),
            refs_before,
        )
        git(worktree, "add", "work-content.md")
        return fixture.sync()

    def test_nonregular_journal_is_renamed_without_following_and_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = SyncFixture(Path(tmp))
            store = SyncOperationStore(fixture.contract.worktree_group)
            store.path.parent.mkdir(parents=True, exist_ok=True)
            target = Path(tmp) / "outside-journal-target"
            target.write_text("do not read or replace\n", encoding="utf-8")
            store.path.symlink_to(target)

            result = fixture.sync(resolution_action="cancel")

            self.assertEqual(result.payload["state"], "sync-cancelled-no-authority")
            metadata = json.loads(
                Path(str(result.payload["evidencePath"])).read_text(encoding="utf-8")
            )
            archived_entry = Path(metadata["rawArchivePath"])
            self.assertEqual(metadata["archiveKind"], "opaque-entry")
            self.assertTrue(archived_entry.is_symlink())
            self.assertEqual(Path(os.readlink(archived_entry)), target)
            self.assertEqual(target.read_text(encoding="utf-8"), "do not read or replace\n")


def section(payload: dict[str, object], key: str) -> dict[str, Any]:
    value = payload[key]
    assert isinstance(value, dict)
    return value


def make_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "agents-remember@example.invalid")
    git(path, "config", "user.name", "Agents Remember")
    commit_file(path, "README.md", "# Fixture")
    commit = git(path, "rev-parse", "HEAD")
    git(path, "update-ref", "refs/remotes/origin/main", commit)
    git(path, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return commit


def commit_file(repo: Path, name: str, content: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content + "\n", encoding="utf-8")
    git(repo, "add", name)
    git(repo, "commit", "-m", f"update {name}")


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
