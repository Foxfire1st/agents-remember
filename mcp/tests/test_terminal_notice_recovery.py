"""An interrupted terminal notice is settled by its own native operation result (F06).

An intent is real only when the route that took it also published: the landing route records a
final landing in the contract's integration cell, the direct-landing route journals a completed
generation, and the abandonment route writes ``cleanup: abandoned``. These cases drive the crash
window itself -- take the intent, publish the record, never confirm -- and check both directions of
the failure: a true publication is still published once, and a publication that did not happen is
not announced from a generic "this cell looks completed" or from an authored task status.

The last group proves the recovery still works after the enclosure root is collected, which is when
only the leaf task document and the terminal archive remain.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.attention_notices import (
    PendingTerminalNotice,
    discard_terminal_notice,
    read_notice_store,
    settle_terminal_notices,
)
from agents_remember.memory_quality.style.citations.source_index_cache import TerminalNamespaceGuard
from agents_remember.models.closeout.input import EffectiveCloseoutInput, EnabledCloseoutLeg
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.mutation_evidence import (
    CloseoutMutationLeg,
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import (
    LifecycleOperationRecord,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import write_task_doc
from agents_remember.worktrees.abandon_task_publication import publish_abandoned_task
from agents_remember.worktrees.attention_terminal_notices import (
    begin_abandoned_notice,
    begin_direct_landed_notice,
    begin_landed_notice,
)
from agents_remember.worktrees.terminal_notice_recovery import (
    DIRECT_LANDING_RECORD,
    NoticeVerdict,
    resolve_pending_notice,
    verify_pending_notice,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    amend_contract,
    contract_publication_text,
    default_contract,
    load_contract,
    write_contract,
)
from lifecycle_enclosure_test_support import publish_test_enclosure, terminalize_test_enclosure
from test_attention_notice_publication import _archived_enclosure
from test_terminal_notice_subject import (
    CODE_COMMIT,
    SIBLING_COMMIT,
    leaf_document,
    series_world,
    two_leaf_world,
)

OPERATION_KEY = "e" * 64
OTHER_OPERATION_KEY = "9" * 64
TREE = "d" * 40
LEAF_REF = TaskDocumentRef(repository="repo", path="master/1_leaf-one.json")
SIBLING_REF = TaskDocumentRef(repository="repo", path="master/2_leaf-two.json")


def landed(contract: WorktreeContract, commit: str = CODE_COMMIT) -> WorktreeContract:
    """The cell the landing route writes for one final landing."""

    return amend_contract(
        replace(
            contract,
            integration_strategy="merge",
            integrated_code_commit=commit,
        ),
        ContractCells(integration_status="completed", cleanup="pending"),
    )


def abandoned(contract: WorktreeContract) -> WorktreeContract:
    """The cell the abandonment publication writes."""

    return amend_contract(contract, ContractCells(cleanup="abandoned"))


def crash_after_landing(contract: WorktreeContract, commit: str = CODE_COMMIT) -> str:
    """Take the landed intent, publish the landing cell, and never confirm it."""

    occurrence = begin_landed_notice(contract, code_commit=commit, strategy="merge")
    write_contract(contract.contract_path, landed(contract, commit))
    return occurrence


def crash_after_abandonment(contract: WorktreeContract) -> str:
    """Take the abandonment intent, publish its cell, and never confirm it."""

    occurrence = begin_abandoned_notice(contract)
    write_contract(contract.contract_path, abandoned(contract))
    return occurrence


def _snapshot(head: str) -> GitMutationSnapshot:
    return GitMutationSnapshot(
        headRef="refs/heads/ar/leaf-l1",
        head=head,
        headTree=TREE,
        refLogFingerprint="1" * 64,
        indexTree=TREE,
        candidateTree=TREE,
        statusFingerprint="2" * 64,
    )


def direct_journal(
    contract: WorktreeContract,
    *,
    operation_key: str = OPERATION_KEY,
    status: str = "completed",
    code_commit: str = CODE_COMMIT,
    generation: int = 1,
) -> Path:
    """Publish one direct-landing generation journal, exactly as that route advances it."""

    completed = status == "completed"
    record = LifecycleOperationRecord(
        taskId=contract.task_id,
        taskName=contract.task_name,
        contractPath=contract.contract_path.as_posix(),
        operationKind="direct-landing",
        candidateState="f" * 64,
        fingerprint="a" * 64,
        operationKey=operation_key,
        generation=generation,
        irreversibleBoundaryEntered=completed,
        recoveryCommits=(
            LifecycleOperationRecoveryCommits(codeCommit=code_commit, memoryContentCommit="b" * 40)
            if completed
            else None
        ),
        input=DirectLandingOperationInput(
            configPath=contract.contract_path.as_posix(),
            contractPath=contract.contract_path.as_posix(),
            leafTaskDocumentRef=TaskDocumentRef(
                repository=contract.repo_name, path="master/1_leaf-one.json"
            ),
            effectiveInput=EffectiveCloseoutInput(
                route="direct-landing",
                contractKind="leaf",
                memoryMode="external",
                code=EnabledCloseoutLeg(reason="approved", message="land"),
                memory=EnabledCloseoutLeg(reason="approved", message="land"),
            ),
            approvalNote="approved",
            codeCommit=code_commit,
            codeTree=TREE,
            candidateTree=TREE,
            memoryRepository="/memory",
            memoryBranch="ar/leaf-l1",
            memoryRef="refs/heads/ar/leaf-l1",
            memoryBefore=_snapshot("b" * 40),
        ),
        status=status,  # type: ignore[arg-type]
        phase="completed" if completed else "direct-preflight",  # type: ignore[arg-type]
        queuedAt="2026-10-11T00:00:00+00:00",
        startedAt="2026-10-11T00:00:00+00:00",
        heartbeatAt="2026-10-11T00:00:00+00:00",
        finishedAt="2026-10-11T00:00:00+00:00" if completed else None,
        currentCommand="direct landing",
        reportPath=contract.worktree_group.joinpath(
            ".lifecycle", "direct-landing-operation.log"
        ).as_posix(),
        result=(
            {"codeCommit": code_commit, "memoryContentCommit": "b" * 40} if completed else None
        ),
        mutationEvidence=_mutation_evidence(contract, code_commit=code_commit, committed=completed),
    )
    path = contract.worktree_group / ".lifecycle" / DIRECT_LANDING_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json(), encoding="utf-8")
    return path


def _mutation_evidence(
    contract: WorktreeContract, *, code_commit: str, committed: bool
) -> dict[CloseoutMutationLeg, GitMutationEvidence]:
    """Both enabled legs' evidence: commit proof, or the pre-mutation state a crash leaves."""

    if not committed:
        return {
            "code": GitMutationEvidence(
                leg="code", repository=contract.code_repo_path.as_posix(), state="pre-mutation"
            ),
            "memory": GitMutationEvidence(leg="memory", repository="/memory", state="pre-mutation"),
        }
    return {
        "code": GitMutationEvidence(
            leg="code",
            repository=contract.code_repo_path.as_posix(),
            state="commit-proven",
            before=_snapshot("b" * 40),
            observed=_snapshot(code_commit),
            expectedOutputTree=TREE,
            commit=code_commit,
        ),
        "memory": GitMutationEvidence(
            leg="memory",
            repository="/memory",
            state="reconciled-unchanged",
            before=_snapshot("b" * 40),
            observed=_snapshot("b" * 40),
        ),
    }


def settle(contract: WorktreeContract) -> None:
    """The projection pass: settle every interrupted intent against its native record."""

    settle_terminal_notices(
        contract.coordination_root,
        lambda notice: verify_pending_notice(contract.coordination_root, notice),
    )


def _row(
    contract: WorktreeContract,
    *,
    generation: int = 1,
    leaf_reference: TaskDocumentRef = LEAF_REF,
) -> PendingTerminalNotice:
    """The intent one direct generation writes, exactly as the store holds it.

    Written through the production builder so the row carries the identity the route persists --
    commit, generation-qualified key, generation, repository and root -- rather than a hand-made
    approximation of it.
    """

    begin_direct_landed_notice(
        contract,
        leaf_reference=leaf_reference,
        code_commit=CODE_COMMIT,
        operation_key=OPERATION_KEY,
        generation=generation,
    )
    return pending(contract)[-1]


def pending(contract: WorktreeContract) -> list[PendingTerminalNotice]:
    store = read_notice_store(contract.coordination_root)
    assert store is not None
    return list(store.pending)


def published(contract: WorktreeContract) -> list[str]:
    store = read_notice_store(contract.coordination_root)
    assert store is not None
    return [notice.occurrenceId for notice in store.notices]


class OrdinaryLandingRecoveryTests(unittest.TestCase):
    def _case_test_a_crash_after_the_landing_cell_publishes_one_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            occurrence = crash_after_landing(contract)

            settle(contract)

            self.assertEqual(published(contract), [occurrence])
            self.assertEqual(pending(contract), [])

    def _case_test_an_exact_retry_publishes_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            occurrence = crash_after_landing(contract)

            settle(contract)
            settle(contract)

            self.assertEqual(published(contract), [occurrence])

    def _case_test_a_different_landed_commit_refutes_the_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            crash_after_landing(contract, CODE_COMMIT)

            write_contract(contract.contract_path, landed(contract, SIBLING_COMMIT))
            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(pending(contract), [])

    def _case_test_a_live_enclosure_that_has_not_landed_is_undecided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            begin_landed_notice(contract, code_commit=CODE_COMMIT, strategy="merge")

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(len(pending(contract)), 1)
            verdict = resolve_pending_notice(contract.coordination_root, pending(contract)[0])
            self.assertIs(verdict.verdict, NoticeVerdict.UNDECIDED)

    def _case_test_an_unrelated_coordination_root_does_not_prove_the_landing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = two_leaf_world(Path(tmp) / "one")["L1"]
            second = two_leaf_world(Path(tmp) / "two")["L1"]
            write_contract(second.contract_path, landed(second, CODE_COMMIT))
            begin_landed_notice(first, code_commit=CODE_COMMIT, strategy="merge")

            settle(first)

            self.assertEqual(published(first), [])
            self.assertEqual(len(pending(first)), 1)

    def _case_test_an_enclosure_of_another_repository_refutes_the_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = two_leaf_world(root)["L1"]
            crash_after_landing(contract)
            foreign = default_contract(
                ContractTask("master", "other", root / "coordination", "light-task", "external"),
                leaf=LeafIdentity(worktree_name="leaf-l1", leaf_id="L1"),
                code=RepoBranchPlan(root / "elsewhere", "main", "ar/leaf-l1", "a" * 40),
                memory=RepoBranchPlan(root / "memory", "main", "ar/leaf-l1", "b" * 40),
            )
            write_contract(contract.contract_path, landed(foreign, CODE_COMMIT))

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(pending(contract), [])

    def _case_test_an_unreadable_contract_is_undecided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            begin_landed_notice(contract, code_commit=CODE_COMMIT, strategy="merge")
            contract.contract_path.write_text("---\nnot: a contract\n---\n", encoding="utf-8")

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(len(pending(contract)), 1)

    def test_ordinary_landing_recovery_matrix(self) -> None:
        """F06: the ordinary landing crash matrix -- prove, retry once, refute a moved commit or a
        foreign repository/root, and stay undecided where nothing proves the publication."""

        for message, case in (
            (
                "a crash after the landing cell publishes one notice",
                self._case_test_a_crash_after_the_landing_cell_publishes_one_notice,
            ),
            ("an exact retry publishes once", self._case_test_an_exact_retry_publishes_once),
            (
                "a different landed commit refutes the intent",
                self._case_test_a_different_landed_commit_refutes_the_intent,
            ),
            (
                "a live enclosure that has not landed is undecided",
                self._case_test_a_live_enclosure_that_has_not_landed_is_undecided,
            ),
            (
                "an unrelated coordination root does not prove the landing",
                self._case_test_an_unrelated_coordination_root_does_not_prove_the_landing,
            ),
            (
                "an enclosure of another repository refutes the intent",
                self._case_test_an_enclosure_of_another_repository_refutes_the_intent,
            ),
            (
                "an unreadable contract is undecided",
                self._case_test_an_unreadable_contract_is_undecided,
            ),
        ):
            with self.subTest(msg=message):
                case()


class DirectLandingRecoveryTests(unittest.TestCase):
    def _case_test_a_completed_generation_publishes_its_own_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            occurrence = begin_direct_landed_notice(
                contract,
                leaf_reference=LEAF_REF,
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )
            direct_journal(contract)

            settle(contract)

            self.assertEqual(published(contract), [occurrence])
            self.assertEqual(pending(contract), [])

    def _case_test_an_in_flight_generation_is_undecided_not_refuted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            begin_direct_landed_notice(
                contract,
                leaf_reference=LEAF_REF,
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )
            direct_journal(contract, status="running")

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(len(pending(contract)), 1)

    def _case_test_a_cancelled_generation_refutes_its_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            begin_direct_landed_notice(
                contract,
                leaf_reference=LEAF_REF,
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )
            direct_journal(contract, status="cancelled")

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(pending(contract), [])

    def _case_test_another_generation_refutes_this_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            begin_direct_landed_notice(
                contract,
                leaf_reference=LEAF_REF,
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )
            direct_journal(contract, operation_key=OTHER_OPERATION_KEY)

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(pending(contract), [])

    def _case_test_a_generation_completed_at_another_commit_refutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            begin_direct_landed_notice(
                contract,
                leaf_reference=LEAF_REF,
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )
            direct_journal(contract, code_commit=SIBLING_COMMIT)

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(pending(contract), [])

    def _case_test_the_exact_generation_publishes_and_a_wrong_one_is_refuted(self) -> None:
        """F06: the numeric generation is part of the stored key, so it separates two runs."""

        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            direct_journal(contract, generation=3)

            exact = _row(contract, generation=3)
            wrong = _row(contract, generation=2)

            self.assertIs(
                resolve_pending_notice(contract.coordination_root, exact).verdict,
                NoticeVerdict.PUBLISHED,
            )
            self.assertIs(
                resolve_pending_notice(contract.coordination_root, wrong).verdict,
                NoticeVerdict.REFUTED,
            )

    def _case_test_a_generic_completed_cell_does_not_override_the_cancelled_journal(self) -> None:
        """F06: the native journal's verdict wins over a cell that happens to look finished."""

        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            direct_journal(contract, status="cancelled")
            write_contract(contract.contract_path, landed(contract))

            verdict = resolve_pending_notice(contract.coordination_root, _row(contract))

            self.assertIs(verdict.verdict, NoticeVerdict.REFUTED)
            self.assertEqual(verdict.record, "direct-landing journal")

    def _case_test_a_generic_completed_cell_does_not_override_an_undecided_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            direct_journal(contract, status="running")
            write_contract(contract.contract_path, landed(contract))

            verdict = resolve_pending_notice(contract.coordination_root, _row(contract))

            self.assertIs(verdict.verdict, NoticeVerdict.UNDECIDED)

    def _case_test_a_generic_completed_cell_alone_is_undecided_for_a_direct_generation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            write_contract(contract.contract_path, landed(contract))

            verdict = resolve_pending_notice(contract.coordination_root, _row(contract))

            self.assertIs(verdict.verdict, NoticeVerdict.UNDECIDED)
            self.assertEqual(verdict.record, "direct-landing journal")

    def _case_test_a_direct_occurrence_of_the_series_route_is_settled_by_its_journal(self) -> None:
        """The series route's leaf documents declare no enclosure; the journal still settles it."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, leaves = series_world(Path(tmp))
            occurrence = begin_direct_landed_notice(
                contract,
                leaf_reference=leaves["L1"],
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )
            direct_journal(contract, generation=1, code_commit=CODE_COMMIT)

            settle(contract)

            self.assertEqual(published(contract), [occurrence])
            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual(store.notices[0].taskReference.path, "master/1_leaf-one.json")

    def _case_test_a_row_written_before_generations_still_settles_by_its_operation(self) -> None:
        """Compatibility: a bare-key intent names an operation, and its journal settles it."""

        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            occurrence = begin_direct_landed_notice(
                contract,
                leaf_reference=LEAF_REF,
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=0,
            )
            row = pending(contract)[0]
            self.assertEqual(row.operationKey, OPERATION_KEY)
            self.assertIsNone(row.operationGeneration)
            direct_journal(contract, generation=1)

            settle(contract)

            self.assertEqual(published(contract), [occurrence])

    def _case_test_an_intent_naming_another_root_is_refuted_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = two_leaf_world(root / "one")["L1"]
            other = two_leaf_world(root / "two")["L1"]
            direct_journal(contract)
            notice = _row(contract).model_copy(update={"root": other.worktree_group.as_posix()})

            verdict = resolve_pending_notice(contract.coordination_root, notice)

            self.assertIs(verdict.verdict, NoticeVerdict.REFUTED)
            self.assertEqual(verdict.record, "notice intent")

    def _case_test_an_intent_naming_another_repository_is_refuted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            direct_journal(contract)
            notice = _row(contract).model_copy(update={"repository": "other"})

            verdict = resolve_pending_notice(contract.coordination_root, notice)

            self.assertIs(verdict.verdict, NoticeVerdict.REFUTED)

    def test_direct_landing_recovery_matrix(self) -> None:
        """F06: a direct occurrence is settled by its exact generation journal and by nothing else --
        completed, in flight, cancelled, another generation, another commit, and the generic cell
        that may never override either verdict."""

        for message, case in (
            (
                "a completed generation publishes its own occurrence",
                self._case_test_a_completed_generation_publishes_its_own_occurrence,
            ),
            (
                "an in flight generation is undecided not refuted",
                self._case_test_an_in_flight_generation_is_undecided_not_refuted,
            ),
            (
                "a cancelled generation refutes its occurrence",
                self._case_test_a_cancelled_generation_refutes_its_occurrence,
            ),
            (
                "another generation refutes this occurrence",
                self._case_test_another_generation_refutes_this_occurrence,
            ),
            (
                "a generation completed at another commit refutes",
                self._case_test_a_generation_completed_at_another_commit_refutes,
            ),
            (
                "the exact generation publishes and a wrong one is refuted",
                self._case_test_the_exact_generation_publishes_and_a_wrong_one_is_refuted,
            ),
            (
                "a generic completed cell does not override the cancelled journal",
                self._case_test_a_generic_completed_cell_does_not_override_the_cancelled_journal,
            ),
            (
                "a generic completed cell does not override an undecided journal",
                self._case_test_a_generic_completed_cell_does_not_override_an_undecided_journal,
            ),
            (
                "a generic completed cell alone is undecided for a direct generation",
                self._case_test_a_generic_completed_cell_alone_is_undecided_for_a_direct_generation,
            ),
            (
                "a direct occurrence of the series route is settled by its journal",
                self._case_test_a_direct_occurrence_of_the_series_route_is_settled_by_its_journal,
            ),
            (
                "a row written before generations still settles by its operation",
                self._case_test_a_row_written_before_generations_still_settles_by_its_operation,
            ),
            (
                "an intent naming another root is refuted not published",
                self._case_test_an_intent_naming_another_root_is_refuted_not_published,
            ),
            (
                "an intent naming another repository is refuted",
                self._case_test_an_intent_naming_another_repository_is_refuted,
            ),
        ):
            with self.subTest(msg=message):
                case()


class AbandonmentRecoveryTests(unittest.TestCase):
    def _case_test_a_rollbackable_abandon_cell_is_undecided_and_keeps_its_intent(self) -> None:
        """F06: ``cleanup: abandoned`` is a publishable cell the terminal guard may still roll back.

        The cell is authored before the guard commits, so reading it as proof would announce an
        abandonment that can still be undone -- and the retraction would have to unwind a notice
        the queue already showed. Until the durable terminal record exists the intent stays open.
        """

        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            occurrence = crash_after_abandonment(contract)

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual([row.occurrenceId for row in pending(contract)], [occurrence])
            verdict = resolve_pending_notice(contract.coordination_root, pending(contract)[0])
            self.assertIs(verdict.verdict, NoticeVerdict.UNDECIDED)
            self.assertEqual(verdict.record, "enclosure contract")

    def _case_test_a_rollback_that_discards_the_publication_leaves_no_notice_standing(self) -> None:
        """The route that published retracts it; nothing survives the rollback in the queue."""

        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            occurrence = crash_after_abandonment(contract)
            location = publish_test_enclosure(
                load(contract), contract_publication_text(contract.contract_path, load(contract))
            )
            terminalize_test_enclosure(location)

            settle(contract)
            self.assertEqual(published(contract), [])
            self.assertEqual([row.occurrenceId for row in pending(contract)], [occurrence])

            discard_terminal_notice(contract.coordination_root, occurrence)
            settle(contract)

            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual((store.notices, store.pending), ([], []))

    def _case_test_an_authored_abandoned_task_is_not_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = two_leaf_world(root)["L2"]
            begin_abandoned_notice(contract)
            write_task_doc(
                contract.task_root,
                leaf_document("L2", "2_leaf-two", contract=contract, status="abandoned"),
            )

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(len(pending(contract)), 1)
            verdict = resolve_pending_notice(contract.coordination_root, pending(contract)[0])
            self.assertIs(verdict.verdict, NoticeVerdict.UNDECIDED)

    def test_abandonment_recovery_matrix(self) -> None:
        """An archive's rollbackable cell and an authored status prove nothing; a rolled-back
        publication leaves no notice standing."""

        for message, case in (
            (
                "a rollbackable abandon cell is undecided and keeps its intent",
                self._case_test_a_rollbackable_abandon_cell_is_undecided_and_keeps_its_intent,
            ),
            (
                "a rollback that discards the publication leaves no notice standing",
                self._case_test_a_rollback_that_discards_the_publication_leaves_no_notice_standing,
            ),
            (
                "an authored abandoned task is not proof",
                self._case_test_an_authored_abandoned_task_is_not_proof,
            ),
        ):
            with self.subTest(msg=message):
                case()


class RecoveryAfterTheEnclosureIsCollectedTests(unittest.TestCase):
    def _collected(self, contract: WorktreeContract) -> None:
        """Collect the enclosure exactly as cleanup does: archive first, then delete the root."""

        location = publish_test_enclosure(
            contract, contract_publication_text(contract.contract_path, contract)
        )
        terminalize_test_enclosure(location)
        contract.contract_path.unlink()

    def _case_test_a_recorded_landing_is_published_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            occurrence = crash_after_landing(contract)
            self._collected(load(contract))

            settle(contract)

            self.assertEqual(published(contract), [occurrence])
            self.assertEqual(pending(contract), [])

    def _case_test_a_recorded_abandonment_is_published_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L2"]
            _archived_enclosure(contract)
            with (
                patch(
                    "agents_remember.worktrees.abandon_task_publication.confirm_notice",
                    side_effect=KeyboardInterrupt,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                publish_abandoned_task(
                    contract, abandoned(contract), TerminalNamespaceGuard(None, None, None)
                )
            occurrence = pending(contract)[0].occurrenceId
            contract.contract_path.unlink()

            settle(contract)

            self.assertEqual(published(contract), [occurrence])

    def _case_test_a_landing_that_was_never_recorded_is_refuted_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            begin_landed_notice(contract, code_commit=CODE_COMMIT, strategy="merge")
            self._collected(load(contract))

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(pending(contract), [])

    def _case_test_an_intent_whose_subject_disappears_is_kept_undecided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = two_leaf_world(Path(tmp))["L1"]
            begin_landed_notice(contract, code_commit=CODE_COMMIT, strategy="merge")
            (contract.task_root / "1_leaf-one.json").unlink()

            settle(contract)

            self.assertEqual(published(contract), [])
            self.assertEqual(len(pending(contract)), 1)

    def test_recovery_after_cleanup_matrix(self) -> None:
        """F06: recovery survives the enclosure's collection -- recorded landings and abandonments still
        publish, an unrecorded one is refuted, and a vanished subject stays undecided."""

        for message, case in (
            (
                "a recorded landing is published after cleanup",
                self._case_test_a_recorded_landing_is_published_after_cleanup,
            ),
            (
                "a recorded abandonment is published after cleanup",
                self._case_test_a_recorded_abandonment_is_published_after_cleanup,
            ),
            (
                "a landing that was never recorded is refuted after cleanup",
                self._case_test_a_landing_that_was_never_recorded_is_refuted_after_cleanup,
            ),
            (
                "an intent whose subject disappears is kept undecided",
                self._case_test_an_intent_whose_subject_disappears_is_kept_undecided,
            ),
        ):
            with self.subTest(msg=message):
                case()


def load(contract: WorktreeContract) -> WorktreeContract:
    """The contract as the enclosure now holds it (the crash wrote the landing cell)."""

    return load_contract(contract.contract_path)


if __name__ == "__main__":
    unittest.main()
