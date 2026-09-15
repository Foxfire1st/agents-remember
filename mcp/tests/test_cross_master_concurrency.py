"""Cross-master concurrency on one protected source pair (LOCR-L36).

One sprint commands every atomic master from its own integration branch, so two
atomic masters commanded by the same sprint derive the SAME protected source pair.
The activation record used to be keyed by that pair, so the second master's
selection replaced the first's, and the closeout projection reported the displaced
master as ``atomic-series-paused-by:`` and held it waiting. These cases drive real
temporary Git repositories and the public operations to prove the record is now per
contract: both masters progress, each master's work stays private until it lands,
landing stays protected against a conflicting or stale publication, and a genuine
wave dependency from the sprint execution graph still gates, while a graph-less
sprint serializes nothing between its atomic masters.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.application import worktree_tools
from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_cache import (
    derive_memory_ledger,
    prepare_memory_cache,
    refresh_memory_cache,
)
from agents_remember.kernel.memory_ledger import find_mapping
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.models.lifecycles.operation import IntegrateOperationInput
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SprintExecutionGraph, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.activation.atomic_series_activation import (
    activation_path,
    observe_atomic_series,
    publish_atomic_series_selection,
)
from agents_remember.worktrees.activation.atomic_series_activation_release import (
    release_atomic_series_selection,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operations import (
    start_or_observe_operation,
)
from agents_remember.worktrees.modules import integrate as integrate_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.queue.closeout_projection_activation import (
    project_series_activation,
)
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.queue.closeout_queue_graph import graph_context
from agents_remember.worktrees.scheduling_mode import resolve_scheduling_mode
from agents_remember.worktrees.series_closeout import (
    SeriesCheckpointRefs,
    publish_series_checkpoint_under_authority,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from checkpoint_landing_test_support import (
    accumulate_master_line,
    checkpoint,
    memory_repository,
    rev,
)
from closeout_input_test_support import (
    closeout_operation_input,
    finish_operation_record,
    publish_closeout_finalization,
    start_closeout_operation,
    start_operation_record,
)
from test_closeout_queue import MASTER_A, MASTER_B, NOW, REPO, QueueFixture
from test_worktree_support import git

LATER = "2026-08-15T01:00:00+00:00"
SERIES = {MASTER_A: "master-a", MASTER_B: "master-b"}


def _series(fixture: QueueFixture, master: TaskDocumentRef) -> WorktreeContract:
    contract = load_contract(fixture.tasks / SERIES[master] / "series-contract.md")
    assert contract.kind == "series"
    return contract


def _member(status: dict[str, Any], master: TaskDocumentRef) -> dict[str, Any]:
    return next(
        member for member in status["members"] if member["owningMaster"]["path"] == master.path
    )


def _tree(repository: Path, branch: str) -> set[str]:
    listing = git(repository, "ls-tree", "-r", "--name-only", branch)
    return {line for line in listing.splitlines() if line}


class CrossMasterConcurrencyTests(unittest.TestCase):
    """One real temporary Git world shared by the behaviours this leaf removes or keeps."""

    def setUp(self) -> None:
        declare_test_process()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(
            self.root,
            atomic_a=True,
            atomic_b=True,
            memory_mode="external",
        )
        self.series = {master: _series(self.fixture, master) for master in SERIES}
        self.scratch = self.root / "scratch"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _select_both(self, *, a_last: bool = True) -> None:
        order = (MASTER_B, MASTER_A) if a_last else (MASTER_A, MASTER_B)
        for master in order:
            publish_atomic_series_selection(self.series[master], "active", timestamp=NOW)

    # -- 1: two independent unfinished masters both progress -------------------

    def test_two_unfinished_masters_share_one_source_pair_and_both_stay_ready(self) -> None:
        series_a, series_b = self.series[MASTER_A], self.series[MASTER_B]
        # The premise, asserted rather than assumed: one sprint command is one source pair.
        self.assertEqual(series_a.code_source_branch, "super")
        self.assertEqual(series_a.code_source_branch, series_b.code_source_branch)
        self.assertEqual(series_a.memory_source_branch, series_b.memory_source_branch)
        self.assertNotEqual(series_a.code_work_branch, series_b.code_work_branch)
        self.assertNotEqual(
            activation_path(self.fixture.coord, series_a),
            activation_path(self.fixture.coord, series_b),
        )

        self._select_both()

        # B was selected first, so under the removed rule A's selection replaced it.
        self.assertEqual(observe_atomic_series(series_a).state, "active")
        self.assertEqual(observe_atomic_series(series_a).selected_master, MASTER_A)
        self.assertEqual(observe_atomic_series(series_b).state, "active")
        self.assertEqual(observe_atomic_series(series_b).selected_master, MASTER_B)
        self.assertEqual(project_series_activation(series_a).waiting, ())
        self.assertEqual(project_series_activation(series_b).waiting, ())

        for master in (MASTER_A, MASTER_B):
            self.assertEqual(self.fixture.declare(master)["state"], "valid-built")

        status = self.fixture.status()

        self.assertEqual(status["state"], "valid-built")
        for master in (MASTER_A, MASTER_B):
            member = _member(status, master)
            self.assertEqual(member["classification"], "ready", member["reasons"])
            self.assertEqual(member["reasons"], [])

    # -- 2: an unfinished master's work stays private --------------------------

    def test_master_a_experimental_code_and_memory_stay_private_until_it_lands(self) -> None:
        series_a = self.series[MASTER_A]
        leaf_a = self.fixture.contracts[MASTER_A]
        assert leaf_a.memory_worktree is not None
        self._select_both()
        memory = memory_repository(series_a)

        (leaf_a.code_worktree / "a-experiment.txt").write_text("A only\n", encoding="utf-8")
        git(leaf_a.code_worktree, "add", "a-experiment.txt")
        git(leaf_a.code_worktree, "commit", "-m", "A experimental code")
        (leaf_a.memory_worktree / "a-experiment.md").write_text("# A only\n", encoding="utf-8")
        git(leaf_a.memory_worktree, "add", "a-experiment.md")
        git(leaf_a.memory_worktree, "commit", "-m", "A experimental memory")

        # Private: neither the sprint's source pair nor the sibling master carries A's line.
        self.assertNotIn("a-experiment.txt", _tree(series_a.code_repo_path, "super"))
        self.assertNotIn("a-experiment.md", _tree(memory, "super"))
        self.assertFalse(
            (self.fixture.contracts[MASTER_B].code_worktree / "a-experiment.txt").exists()
        )
        self.assertNotIn("a-experiment.txt", _tree(series_a.code_repo_path, "ar/master-b"))
        # A's own line does carry it, and A is still the selected owner of its own record.
        self.assertIn("a-experiment.txt", _tree(series_a.code_repo_path, "ar/leaf-a"))
        self.assertEqual(observe_atomic_series(series_a).state, "active")

    # -- 3: master B integrates while master A is unfinished -------------------

    def test_master_b_lands_its_leaf_while_master_a_is_unfinished(self) -> None:
        series_b = self.series[MASTER_B]
        leaf_b = self.fixture.contracts[MASTER_B]
        self._select_both(a_last=True)
        candidate = self._close_out_and_land_leaf(MASTER_B)

        # B's line moved onto its own master branch while A stayed exactly where it was.
        self.assertEqual(rev(series_b.code_repo_path, series_b.code_work_branch), candidate)
        self.assertEqual(observe_atomic_series(self.series[MASTER_A]).state, "active")
        self.assertEqual(observe_atomic_series(self.series[MASTER_A]).selected_master, MASTER_A)
        self.assertEqual(
            rev(self.series[MASTER_A].code_repo_path, self.series[MASTER_A].code_work_branch),
            rev(series_b.code_repo_path, "ar/master-a"),
        )
        # B's leaf landed, and B's master itself is still open.
        self.assertEqual(
            load_contract(leaf_b.contract_path).integration_status,
            "completed",
        )
        self.assertEqual(load_contract(series_b.contract_path).integration_status, "not-started")

    def _close_out_and_land_leaf(self, master: TaskDocumentRef, *, declare: bool = True) -> str:
        """Close one atomic leaf out and land it through the public integrate route."""

        if declare:
            self.fixture.declare(master)
        contract = load_contract(self.fixture.contracts[master].contract_path)
        candidate = self._land_leaf_contract(contract)
        self.fixture.contracts[master] = load_contract(contract.contract_path)
        return candidate

    def _land_leaf_contract(self, contract: WorktreeContract) -> str:
        """Close one atomic leaf out and land it on its master's branches, as the fixture does."""

        assert contract.memory_worktree is not None
        git(contract.code_worktree, "add", "-A")
        git(contract.code_worktree, "commit", "-m", "atomic child candidate")
        candidate_commit = git(contract.code_worktree, "rev-parse", "HEAD")
        prepare_memory_cache(contract.memory_worktree)
        git(contract.memory_worktree, "add", "-A")
        git(
            contract.memory_worktree,
            "commit",
            "-m",
            render_memory_content_message("atomic child memory content", candidate_commit),
        )
        memory_content = git(contract.memory_worktree, "rev-parse", "HEAD")
        refresh_memory_cache(contract.memory_worktree, repo_name=contract.repo_name)
        start_closeout_operation(
            closeout_operation_input(
                contract,
                config_path=self.fixture.config_path,
                approval_note="approved atomic child candidate",
            ),
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "closeout"))
        assert start_operation_record(store).status == "running"
        finalized = replace(
            contract,
            human_review_status="approved",
            approved_for_commit=True,
            closeout_status="completed",
            code_commit=candidate_commit,
            memory_content_commit=memory_content,
        )
        write_contract(finalized.contract_path, finalized)
        publish_closeout_finalization(store, finalized)
        finish_operation_record(store, {"state": "closed"}, ok=True)
        closed = load_contract(finalized.contract_path)

        operation_input = IntegrateOperationInput(
            configPath=self.fixture.config_path.as_posix(),
            contractPath=closed.contract_path.as_posix(),
        )
        start_or_observe_operation(operation_input, closed, launcher=lambda *_: None)
        integrate_store = LifecycleOperationStore(
            operation_record_path(closed.worktree_group, "integrate")
        )
        running = integrate_store.read()
        assert running is not None
        args = WorktreeArgs(
            contract_path=closed.contract_path,
            certification_profile=Path("mcp/certification-profile-v1.json"),
            strategy="ff-only",
            approved=True,
            operation_key=running.operationKey,
            operation_generation=running.generation,
        )
        result = integrate_module.integrate_result(args, load_contract(closed.contract_path))
        self.assertEqual(
            (result.returncode, result.payload["state"]),
            (0, "integrated"),
            result.payload,
        )
        return candidate_commit

    def _complete_master_documents(self, master: TaskDocumentRef) -> None:
        """Mark a master and every canonical leaf row Completed, as the workflow's plan requires."""

        series = self.series[master]
        master_path = series.task_root / "task.json"
        document = read_task_doc(master_path)
        for row in document.subTasks:
            leaf_path = (series.task_root / row.file).with_suffix(".json")
            leaf = read_task_doc(leaf_path)
            write_task_doc(leaf_path.parent, leaf.model_copy(update={"status": "Completed"}))
        write_task_doc(
            master_path.parent,
            document.model_copy(
                update={
                    "status": "Completed",
                    "subTasks": [
                        row.model_copy(update={"status": "Completed"}) for row in document.subTasks
                    ],
                }
            ),
        )

    def _closeout_and_land_master(self, master: TaskDocumentRef) -> dict[str, Any]:
        """Complete one atomic master through the PUBLIC closeout and integrate operations."""

        series = self.series[master]
        self._complete_master_documents(master)
        messages = worktree_tools.CloseoutCommitMessages(
            code="atomic master code",
            memory="atomic master memory",
        )
        closed = worktree_tools.worktree_closeout_apply_tool(
            self.fixture.cfg,
            series.contract_path.as_posix(),
            messages,
            worktree_tools.CloseoutApproval(intent_note="developer approved"),
        )
        self.assertTrue(closed.get("ok"), closed)
        self.assertEqual(closed["state"], "closed", closed)
        landed = worktree_tools.worktree_integrate_tool(
            self.fixture.cfg,
            contract_path=series.contract_path.as_posix(),
        )
        self.assertTrue(landed.get("ok"), landed)
        self.assertEqual(landed["state"], "integrated", landed)
        self.series[master] = load_contract(series.contract_path)
        return landed

    def _public_sync(self, contract: WorktreeContract) -> dict[str, Any]:
        """Reconcile independent code and memory content through the public sync operation."""

        result = worktree_tools.worktree_sync_tool(
            self.fixture.cfg,
            contract_path=contract.contract_path.as_posix(),
            memory_sync_choice="merge-memory",
        )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["state"], "synced", result)
        return result

    def _rename_leaf_features(self, *contracts: WorktreeContract) -> None:
        """Give each leaf its own feature file so independent content merges cleanly."""

        for contract in contracts:
            feature = contract.code_worktree / "feature.txt"
            if feature.exists():
                feature.rename(contract.code_worktree / f"{contract.leaf_id.lower()}-feature.txt")

    def _private_master_a_facts(self) -> tuple[Any, ...]:
        """Everything a pause must leave exactly as it was for an unfinished private master."""

        series_a = self.series[MASTER_A]
        memory = memory_repository(series_a)
        series = load_contract(series_a.contract_path)
        leaf_id = self.fixture.unstarted_leaf_a
        assert leaf_id is not None
        document = read_task_doc(series_a.task_root / "task.json")
        row = next(item for item in document.subTasks if item.number == leaf_id)
        leaf_document = read_task_doc(series_a.task_root / f"{leaf_id.lower()}.json")
        enclosure = series_a.task_root / "enclosures" / leaf_id.lower()
        worktree = (
            series_a.coordination_root
            / "worktrees"
            / series_a.repo_name
            / (f"{leaf_id.lower()}-ar")
        )
        return (
            rev(series_a.code_repo_path, "super"),
            rev(memory, "super"),
            rev(series_a.code_repo_path, series_a.code_work_branch),
            rev(memory, series_a.memory_work_branch),
            series.closeout_status,
            series.integration_status,
            (
                row.status,
                leaf_document.status,
                tuple((step.id, step.status) for step in leaf_document.steps),
            ),
            (enclosure.exists(), worktree.exists()),
        )

    def _paused_fixture(self) -> None:
        """Rebuild the shared world with a second, still unstarted canonical leaf under master A."""

        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(
            self.root,
            atomic_a=True,
            atomic_b=True,
            memory_mode="external",
        )
        self.fixture.author_unstarted_leaf("master-a", "LEAF-A2")
        self.series = {master: _series(self.fixture, master) for master in SERIES}
        self.scratch = self.root / "scratch"

    # -- 4: releasing a master's activation publishes nothing and blocks nobody ----

    def test_releasing_master_a_activation_publishes_nothing_and_leaves_master_b_eligible(
        self,
    ) -> None:
        """The activation release a cancelled sync or a terminal contract owns.

        This is deliberately NOT the pause. Releasing an atomic-series selection is the state
        transition the sync-cancel and terminal-cleanup routes need; it moves no ref and preserves
        the master's work, but calling it is not what pausing a master means and must not be read as
        the pause's next move. The pause itself is an ordinary stop -- no tool call publishes or
        advances anything -- and is covered by
        ``test_master_a_resumes_reconciles_and_completes_after_master_b_landed``.
        """

        series_a = self.series[MASTER_A]
        leaf_a = self.fixture.contracts[MASTER_A]
        assert leaf_a.memory_worktree is not None
        self._select_both()
        memory = memory_repository(series_a)
        (leaf_a.code_worktree / "a-paused.txt").write_text("A only\n", encoding="utf-8")
        git(leaf_a.code_worktree, "add", "a-paused.txt")
        git(leaf_a.code_worktree, "commit", "-m", "A work in progress")
        code_source_before = rev(series_a.code_repo_path, "super")
        memory_source_before = rev(memory, "super")
        a_line = rev(series_a.code_repo_path, series_a.code_work_branch)
        # Both masters are eligible BEFORE the release, so the release is the only change below.
        self.assertEqual(self.fixture.declare(MASTER_A)["state"], "valid-built")
        self.assertEqual(self.fixture.declare(MASTER_B)["state"], "valid-built")

        released = release_atomic_series_selection(series_a, timestamp=LATER)

        self.assertEqual(released.state, "vacant")
        self.assertEqual(observe_atomic_series(series_a).state, "vacant")
        # The release publishes nothing and preserves the released master's own work.
        self.assertEqual(rev(series_a.code_repo_path, "super"), code_source_before)
        self.assertEqual(rev(memory, "super"), memory_source_before)
        self.assertEqual(rev(series_a.code_repo_path, series_a.code_work_branch), a_line)
        self.assertIn("a-paused.txt", _tree(series_a.code_repo_path, "ar/leaf-a"))
        # The sibling master is untouched, and its own closeout member stays ready.
        self.assertEqual(observe_atomic_series(self.series[MASTER_B]).state, "active")
        self.assertEqual(observe_atomic_series(self.series[MASTER_B]).selected_master, MASTER_B)

        status = self.fixture.rebuild()

        # A released master is not a waiting master: no activation reason is reintroduced for it.
        self.assertEqual(_member(status, MASTER_A)["reasons"], [])
        self.assertEqual(_member(status, MASTER_A)["classification"], "ready")
        self.assertEqual(_member(status, MASTER_B)["reasons"], [])
        self.assertEqual(_member(status, MASTER_B)["classification"], "ready")

    # -- 5 + 8: a conflicting publication is refused; resume reconciles ---------

    def _land_master_b_line(self) -> dict[str, Any]:
        row = accumulate_master_line(
            self.fixture, self.series[MASTER_B], self.scratch, label="b-one"
        )
        landed = checkpoint(self.fixture, self.series[MASTER_B], dry_run=False)
        self.assertTrue(landed["ok"], landed)
        self.assertEqual(landed["state"], "checkpointed")
        self.assertEqual(
            rev(memory_repository(self.series[MASTER_B]), "super"),
            landed["integrated_memory_content_commit"],
        )
        return {"row": row, "landed": landed}

    def test_a_conflicting_publication_cannot_overwrite_master_b(self) -> None:
        series_a = self.series[MASTER_A]
        self._select_both()
        accumulate_master_line(self.fixture, series_a, self.scratch, label="a-one")
        landed = self._land_master_b_line()
        memory = memory_repository(series_a)
        code_after_b = rev(series_a.code_repo_path, "super")
        memory_after_b = rev(memory, "super")
        self.assertEqual(code_after_b, landed["landed"]["integrated_code_commit"])
        self.assertEqual(memory_after_b, landed["landed"]["integrated_memory_content_commit"])

        # A's line does not contain B's landed source, so it is not a fast-forwardable
        # candidate and it must not overwrite the pair B already landed.
        conflicted = checkpoint(self.fixture, series_a, dry_run=False)

        self.assertFalse(conflicted.get("ok"))
        self.assertEqual(conflicted["state"], "blocked-non-ff", conflicted)
        self.assertEqual(rev(series_a.code_repo_path, "super"), code_after_b)
        self.assertEqual(rev(memory, "super"), memory_after_b)
        # B's committed attribution survives: the landed source still carries its memory commit.
        self.assertIsNotNone(
            find_mapping(
                derive_memory_ledger(memory, memory_after_b),
                landed["landed"]["integrated_code_commit"],
            )
        )
        # A's stale capture cannot be published either, and it lands nothing.
        stale = _checkpoint_with_candidate(
            load_contract(series_a.contract_path),
            SeriesCheckpointRefs(code_commit="a" * 40),
        )
        self.assertFalse(stale.get("ok"))
        self.assertEqual(stale["state"], "atomic-series-checkpoint-candidate-moved", stale)
        self.assertEqual(rev(series_a.code_repo_path, "super"), code_after_b)
        self.assertEqual(rev(memory, "super"), memory_after_b)

    def test_master_a_resumes_reconciles_and_completes_after_master_b_landed(self) -> None:
        """A paused master finishes through ORDINARY closeout and final integration (LOCR-L36).

        Two independent atomic masters share one sprint. B completes and lands normally while A stays
        unfinished and private; A is then paused, resumed, reconciled with B through the PUBLIC sync
        path, finishes its remaining leaf and completes through the public closeout and integrate
        routes. A checkpoint is never used, and is never a substitute for either master's completion.
        """

        self._paused_fixture()
        series_a = self.series[MASTER_A]
        self._rename_leaf_features(*self.fixture.contracts.values())

        # 1 -- two independent atomic masters, both active inside one sprint.
        self._select_both()
        for master in (MASTER_A, MASTER_B):
            self.assertEqual(observe_atomic_series(self.series[master]).state, "active")
            self.assertEqual(self.fixture.declare(master)["state"], "valid-built")
        ready = self.fixture.rebuild()
        for master in (MASTER_A, MASTER_B):
            member = _member(ready, master)
            self.assertEqual(member["classification"], "ready", member["reasons"])
            self.assertEqual(member["reasons"], [])

        # 2 -- A lands its first leaf and stays unfinished and private while B completes and lands.
        first_a_candidate = self._close_out_and_land_leaf(MASTER_A, declare=False)
        self._close_out_and_land_leaf(MASTER_B, declare=False)
        b_landed = self._closeout_and_land_master(MASTER_B)
        self.assertEqual(rev(series_a.code_repo_path, "super"), b_landed["integrated_code_commit"])
        unfinished_a = load_contract(series_a.contract_path)
        self.assertEqual(unfinished_a.closeout_status, "not-started")
        self.assertEqual(unfinished_a.integration_status, "not-started")
        self.assertEqual(rev(series_a.code_repo_path, series_a.code_work_branch), first_a_candidate)
        # A's own work is private: it is on A's line and not on the sprint's.
        self.assertIn(
            "leaf-a-feature.txt", _tree(series_a.code_repo_path, series_a.code_work_branch)
        )
        self.assertNotIn("leaf-a-feature.txt", _tree(series_a.code_repo_path, "super"))

        # 3 -- pausing A. A pause is an ordinary stop: it publishes nothing, moves no ref and
        # advances no unstarted leaf. No worktree tool performs it, so this is exactly the state
        # the stop must leave untouched, taken before and after the pause.
        self._require_pause_left_a_private(series_a, b_landed)

        # 4 -- resume A and reconcile B's landing through the PUBLIC sync path, validating the
        # resulting code/memory pair before any further work lands on A's line.
        reconciled_code = self._reconcile_master_a(series_a, b_landed, first_a_candidate)

        # 5 -- A's remaining, unstarted leaf is started on the reconciled line and lands there.
        second_a_candidate = self._land_remaining_master_a_leaf(series_a)
        self.assertEqual(
            git(series_a.code_repo_path, "merge-base", reconciled_code, second_a_candidate),
            reconciled_code,
        )

        # 6 -- A completes and integrates normally.
        a_landed = self._closeout_and_land_master(MASTER_A)
        self._require_both_committed_histories(
            series_a, a_landed, first_a_candidate, second_a_candidate, b_landed
        )

    def _require_pause_left_a_private(
        self, series_a: WorktreeContract, b_landed: dict[str, Any]
    ) -> None:
        """A pause publishes nothing, moves no ref and advances no unstarted leaf."""

        before_pause = self._private_master_a_facts()
        after_pause = self._private_master_a_facts()

        self.assertEqual(after_pause, before_pause)
        self.assertEqual(after_pause[4], "not-started")
        self.assertEqual(after_pause[5], "not-started")
        # The commanded leaf A2 is still only a plan: its row and document are untouched, no
        # enclosure exists, no worktree exists and none of its steps has advanced.
        self.assertEqual(after_pause[6], ("inProgress", "inProgress", (("S1", "pending"),)))
        self.assertEqual(after_pause[7], (False, False))
        # The sibling master is untouched by A's stop, and its landing stands.
        self.assertEqual(rev(series_a.code_repo_path, "super"), b_landed["integrated_code_commit"])

    def _reconcile_master_a(
        self,
        series_a: WorktreeContract,
        b_landed: dict[str, Any],
        first_a_candidate: str,
    ) -> str:
        """Resume A and reconcile B through the public sync without manufacturing attribution."""

        synced = self._public_sync(load_contract(series_a.contract_path))
        self.assertEqual(synced["state"], "synced", synced)
        resumed_a = load_contract(series_a.contract_path)
        self.assertEqual(resumed_a.code_base_commit, b_landed["integrated_code_commit"])
        self.assertEqual(resumed_a.sync_log[-1]["codeBaseTo"], b_landed["integrated_code_commit"])
        reconciled_code = rev(series_a.code_repo_path, series_a.code_work_branch)
        self.assertNotEqual(reconciled_code, first_a_candidate)
        self._require_committed_attribution(
            series_a, first_a_candidate, b_landed["integrated_code_commit"]
        )
        self.assertEqual(
            git(series_a.code_repo_path, "merge-base", first_a_candidate, reconciled_code),
            first_a_candidate,
        )
        return reconciled_code

    def _land_remaining_master_a_leaf(self, series_a: WorktreeContract) -> str:
        """Start A's commanded, never-started leaf on the reconciled line and land it."""

        leaf_id = self.fixture.unstarted_leaf_a
        assert leaf_id is not None
        leaf_a2 = self.fixture.start_leaf(leaf_id)
        self._rename_leaf_features(leaf_a2)
        leaf_ref = TaskDocumentRef(repository=REPO, path=f"master-a/{leaf_id.lower()}.json")
        self.assertEqual(
            self.fixture.declare_leaf(leaf_a2, leaf_ref, MASTER_A)["state"], "valid-built"
        )
        candidate = self._land_leaf_contract(load_contract(leaf_a2.contract_path))
        self.assertEqual(rev(series_a.code_repo_path, series_a.code_work_branch), candidate)
        self.assertNotEqual(load_contract(series_a.contract_path).closeout_status, "completed")
        return candidate

    def _require_committed_attribution(self, series: WorktreeContract, *code_commits: str) -> None:
        """Each already-attributed memory commit remains reachable on the series work branch."""

        assert series.memory_repo_path is not None
        derived = derive_memory_ledger(
            series.memory_repo_path, rev(series.memory_repo_path, series.memory_work_branch)
        )
        for code_commit in code_commits:
            self.assertIsNotNone(find_mapping(derived, code_commit))

    def _require_both_committed_histories(
        self,
        series_a: WorktreeContract,
        a_landed: dict[str, Any],
        first_a_candidate: str,
        second_a_candidate: str,
        b_landed: dict[str, Any],
    ) -> None:
        """The actual landed refs retain the code and memory histories of both masters."""

        self.assertEqual(rev(series_a.code_repo_path, "super"), a_landed["integrated_code_commit"])
        memory = memory_repository(series_a)
        final_memory = a_landed["integrated_memory_content_commit"]
        self.assertEqual(rev(memory, "super"), final_memory)
        first_a = load_contract(self.fixture.contracts[MASTER_A].contract_path)
        for memory_commit in (
            b_landed["integrated_memory_content_commit"],
            first_a.integrated_memory_content_commit,
        ):
            self.assertEqual(git(memory, "merge-base", memory_commit, final_memory), memory_commit)
        for code_commit in (
            b_landed["integrated_code_commit"],
            first_a_candidate,
            second_a_candidate,
        ):
            self.assertEqual(
                git(
                    series_a.code_repo_path,
                    "merge-base",
                    code_commit,
                    a_landed["integrated_code_commit"],
                ),
                code_commit,
            )
        ledger = derive_memory_ledger(memory, rev(memory, "super"))
        self.assertIsNotNone(find_mapping(ledger, b_landed["integrated_code_commit"]))
        self.assertIsNotNone(find_mapping(ledger, first_a_candidate))
        self.assertIsNotNone(find_mapping(ledger, second_a_candidate))
        final = find_mapping(ledger, a_landed["integrated_code_commit"])
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final.memory_commit, a_landed["integrated_memory_content_commit"])
        self.assertEqual(load_contract(series_a.contract_path).integration_status, "completed")
        self.assertEqual(load_contract(series_a.contract_path).cleanup, "pending")

    # -- 6: the explicit checkpoint landing route is unchanged ------------------

    def test_explicit_checkpoint_landing_remains_available_when_requested(self) -> None:
        series_b = self.series[MASTER_B]
        self._select_both()
        accumulate_master_line(self.fixture, series_b, self.scratch, label="b-two")
        memory = memory_repository(series_b)
        code_before = rev(series_b.code_repo_path, "super")
        memory_before = rev(memory, "super")

        preview = checkpoint(self.fixture, series_b, dry_run=True)

        self.assertTrue(preview["ok"], preview)
        self.assertEqual(preview["state"], "would-checkpoint")
        # A preview moves nothing and the route is explicitly reachable for an open master.
        self.assertEqual(rev(series_b.code_repo_path, "super"), code_before)
        self.assertEqual(rev(memory, "super"), memory_before)
        self.assertFalse(preview["eligibility"]["closeoutRequired"])
        self.assertTrue(preview["eligibility"]["approvalRequired"])
        self.assertEqual(
            preview["eligibility"]["memoryContentCandidate"],
            rev(memory, series_b.memory_work_branch),
        )

        applied = checkpoint(self.fixture, series_b, dry_run=False)

        self.assertTrue(applied["ok"], applied)
        self.assertEqual(applied["state"], "checkpointed")
        self.assertEqual(rev(series_b.code_repo_path, "super"), applied["integrated_code_commit"])
        self.assertEqual(rev(memory, "super"), applied["integrated_memory_content_commit"])
        stored = load_contract(series_b.contract_path)
        self.assertEqual(stored.integration_status, "checkpointed")
        self.assertEqual(stored.closeout_status, "not-started")
        self.assertTrue(series_b.code_worktree.exists())
        # The sibling unfinished master is untouched by B's landing.
        self.assertEqual(observe_atomic_series(self.series[MASTER_A]).state, "active")

    # -- 7: a real wave dependency still gates --------------------------------

    def test_a_dependent_master_still_waits_for_its_unfinished_predecessor(self) -> None:
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = QueueFixture(
            self.root,
            atomic_a=True,
            atomic_b=True,
            memory_mode="external",
            edge=True,
        )
        self.series = {master: _series(self.fixture, master) for master in SERIES}
        self._select_both(a_last=True)

        for master in (MASTER_A, MASTER_B):
            self.assertEqual(self.fixture.declare(master)["state"], "valid-built")

        status = self.fixture.status()
        predecessor = _member(status, MASTER_A)
        dependent = _member(status, MASTER_B)

        # Activation exclusivity is gone, and the sprint's own wave gate is what still holds B.
        self.assertEqual(observe_atomic_series(self.series[MASTER_B]).state, "active")
        self.assertEqual(predecessor["classification"], "ready", predecessor["reasons"])
        self.assertEqual(dependent["classification"], "waiting")
        self.assertEqual(
            dependent["reasons"],
            [f"predecessor-incomplete: {MASTER_A.key}"],
        )

    # -- 9: a graph-less sprint serializes nothing ----------------------------

    def test_a_graph_less_sprint_serializes_nothing_between_its_atomic_masters(self) -> None:
        """The ruling: nothing serializes a graph-less sprint.

        A graph-less sprint declares no dependencies, so there is nothing to honour:
        ``atomic-sequential`` describes the sprint's SHAPE, not a serialization
        mechanism, and no master is held because another is selected. The
        sprint-with-a-graph half of the same ruling is already covered by
        ``test_a_dependent_master_still_waits_for_its_unfinished_predecessor`` above,
        which is why this case asserts only the graph-less half.
        """

        sprint_path = self.fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        write_task_doc(sprint_path.parent, sprint.model_copy(update={"executionGraph": None}))

        topology = TaskDocumentTopology(self.fixture.coord)
        mode = resolve_scheduling_mode(topology, topology.canonical_ref(REPO, sprint_path))

        self.assertEqual(mode.mode, "atomic-sequential")
        self.assertEqual(
            {master.ref.key for master in mode.masters},
            {MASTER_A.key, MASTER_B.key},
        )
        self.assertEqual(
            mode.facts,
            (
                "executionGraph absent: atomic-sequential default — every commanded master "
                "executes atomically and no dependency is declared, so nothing serializes the "
                "masters",
            ),
        )

        self._select_both(a_last=True)

        # Every commanded master holds its own activation at the same time; none waits.
        for master in (MASTER_A, MASTER_B):
            observation = observe_atomic_series(self.series[master])
            self.assertEqual(observation.state, "active")
            self.assertEqual(observation.selected_master, master)
            self.assertEqual(project_series_activation(self.series[master]).waiting, ())

        # The graph-less projection admits both masters concurrently and holds nobody.
        for master in (MASTER_A, MASTER_B):
            self.assertEqual(self.fixture.declare(master)["state"], "valid-built")

        status = self.fixture.status()

        self.assertEqual(status["state"], "valid-built")
        for master in (MASTER_A, MASTER_B):
            member = _member(status, master)
            self.assertEqual(member["classification"], "ready", member["reasons"])
            self.assertEqual(member["reasons"], [])

        # The graph-less seam still refuses a caller whose already-resolved graph the
        # canonical document no longer carries; that refusal states the ruling.
        authored = SprintExecutionGraph.model_validate(
            {"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []}
        )
        with self.assertRaises(CloseoutQueueError) as raised:
            graph_context(
                topology,
                topology.canonical_ref(REPO, sprint_path),
                authored_graph=authored,
            )

        self.assertEqual(raised.exception.status, "task-execution-topology-migration-required")
        detail = str(raised.exception)
        self.assertIn("nothing serializes the masters", detail)
        self.assertNotIn("source-pair", detail)


def _checkpoint_with_candidate(
    series: WorktreeContract, candidate: SeriesCheckpointRefs
) -> dict[str, Any]:
    """Publish one checkpoint through the route's own candidate-validation boundary."""

    published: list[str] = []
    try:
        publish_series_checkpoint_under_authority(
            series,
            lambda: published.append("published") or "published",
            candidate,
        )
    except (CloseoutQueueError, RuntimeError) as error:
        return {
            "ok": False,
            "state": getattr(error, "status", type(error).__name__),
            "detail": str(error),
        }
    return {"ok": True, "state": "checkpointed", "published": published}


if __name__ == "__main__":
    unittest.main()
