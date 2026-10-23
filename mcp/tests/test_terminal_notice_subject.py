"""A terminal notice's subject is the leaf, never the master series it is filed under (F05).

A master task folder holds several sibling leaves, and every one of them publishes its own task
document. The queue names the document the enclosure *published*, so two sibling landings are two
subjects with two occurrences, and an exact retry of one is still only that one -- including after
the enclosure root has been cleaned up, because the leaf document outlives it.

The shared fixture here is the two-leaf world every notice test in this package builds on; the
series case is kept beside it so the one place where the master *is* the task stays proved.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.attention_notices import read_notice_store
from agents_remember.models.closeout.input import EffectiveCloseoutInput, EnabledCloseoutLeg
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.models.lifecycles.mutation_evidence import (
    GitMutationEvidence,
    GitMutationSnapshot,
)
from agents_remember.models.lifecycles.operation import LifecycleOperationRecord
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import SubTaskRef, TaskDocument, write_task_doc
from agents_remember.worktrees.attention_terminal_notices import (
    NO_OCCURRENCE,
    LeafTaskIdentityError,
    abandoned_occurrence,
    begin_abandoned_notice,
    begin_direct_landed_notice,
    begin_landed_notice,
    landed_occurrence,
    leaf_task,
    task_reference,
)
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    DirectLandingRuntime,
    direct_landing_store,
)
from agents_remember.worktrees.modules.landing_record import (
    LandedIntegration,
    record_landed_integration,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_contract,
    default_series_contract,
    write_contract,
)
from lifecycle_enclosure_test_support import publish_test_enclosure

CODE_COMMIT = "c" * 40
SIBLING_COMMIT = "d" * 40
OPERATION_KEY = "e" * 64
TREE = "f" * 40

SIBLING_LEAVES = (("L1", "1_leaf-one"), ("L2", "2_leaf-two"))


def leaf_document(
    leaf_id: str,
    slug: str,
    *,
    contract: WorktreeContract | None = None,
    status: str = "inProgress",
) -> TaskDocument:
    """One authored leaf document, optionally declaring the enclosure that publishes it."""

    return TaskDocument.model_validate(
        {
            "id": leaf_id,
            "slug": slug,
            "title": slug,
            "kind": "subTask",
            "status": status,
            "repo": "repo",
            "createdAt": "2026-08-15T00:00:00+00:00",
            "enclosures": (
                []
                if contract is None
                else [
                    {
                        "leafId": leaf_id,
                        "enclosurePath": contract.contract_path.as_posix(),
                    }
                ]
            ),
        }
    )


def leaf_contract(root: Path, leaf_id: str, slug: str, **_: object) -> WorktreeContract:
    """One leaf enclosure contract under the shared master task root."""

    contract = default_contract(
        ContractTask("master", "repo", root / "coordination", "light-task", "external"),
        leaf=LeafIdentity(worktree_name=f"leaf-{leaf_id.lower()}", leaf_id=leaf_id),
        code=RepoBranchPlan(root / "repo", "main", f"ar/leaf-{leaf_id.lower()}", "a" * 40),
        memory=RepoBranchPlan(root / "memory", "main", f"ar/leaf-{leaf_id.lower()}", "b" * 40),
    )
    write_contract(contract.contract_path, contract)
    return contract


def two_leaf_world(root: Path) -> dict[str, WorktreeContract]:
    """A master with two sibling leaf enclosures, each publishing its own task document."""

    task_root = root / "coordination" / "tasks" / "repo" / "master"
    task_root.mkdir(parents=True)
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "MASTER",
                "slug": "master",
                "title": "Master",
                "kind": "master",
                "status": "inProgress",
                "repo": "repo",
                "createdAt": "2026-08-22T00:00:00+00:00",
                "subTasks": [
                    SubTaskRef(
                        number=leaf_id,
                        name=slug,
                        file=f"{slug}.md",
                        status="inProgress",
                    ).model_dump(mode="json")
                    for leaf_id, slug in (("L1", "1_leaf-one"), ("L2", "2_leaf-two"))
                ],
            }
        ),
    )
    contracts: dict[str, WorktreeContract] = {}
    for leaf_id, slug in (("L1", "1_leaf-one"), ("L2", "2_leaf-two")):
        contract = leaf_contract(root, leaf_id, slug)
        write_task_doc(task_root, leaf_document(leaf_id, slug, contract=contract))
        contracts[leaf_id] = contract
    return contracts


def series_world(root: Path) -> tuple[WorktreeContract, dict[str, TaskDocumentRef]]:
    """The production direct-landing world: one series contract and two named sibling leaves.

    A direct landing requires the series contract and names its leaf through the immutable
    ``DirectLandingOperationInput.leafTaskDocumentRef``, so neither leaf document declares an
    enclosure of its own: the series contract is the enclosure that publishes them. The task
    documents live under the master's task root, exactly as the route's bound-leaf proof requires.
    """

    task_root = root / "coordination" / "tasks" / "repo" / "master"
    task_root.mkdir(parents=True)
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "MASTER",
                "slug": "master",
                "title": "Master",
                "kind": "master",
                "status": "inProgress",
                "repo": "repo",
                "createdAt": "2026-08-22T00:00:00+00:00",
                "subTasks": [
                    SubTaskRef(
                        number=leaf_id,
                        name=slug,
                        file=f"{slug}.md",
                        status="inProgress",
                    ).model_dump(mode="json")
                    for leaf_id, slug in SIBLING_LEAVES
                ],
            }
        ),
    )
    references: dict[str, TaskDocumentRef] = {}
    for leaf_id, slug in SIBLING_LEAVES:
        write_task_doc(
            task_root,
            TaskDocument.model_validate(
                {
                    "id": leaf_id,
                    "slug": slug,
                    "title": f"leaf {leaf_id}",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": "repo",
                    "createdAt": "2026-08-15T00:00:00+00:00",
                }
            ),
        )
        references[leaf_id] = TaskDocumentRef(repository="repo", path=f"master/{slug}.json")
    contract = default_series_contract(
        ContractTask("master", "repo", root / "coordination", "light-task", "external"),
        code=RepoBranchPlan(root / "repo", "main", "ar/master", "a" * 40),
        memory=RepoBranchPlan(root / "memory", "main", "ar/master", "b" * 40),
    )
    contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
    text = contract_publication_text(contract.contract_path, contract)
    write_contract(contract.contract_path, contract)
    publish_test_enclosure(contract, text)
    return contract, references


def running_direct_generation(
    contract: WorktreeContract,
    leaf_reference: TaskDocumentRef,
    *,
    operation_key: str = OPERATION_KEY,
    generation: int = 1,
) -> LifecycleOperationRecord:
    """One admitted, not yet finished direct-landing generation for this leaf."""

    assert contract.memory_repo_path is not None
    snapshot = GitMutationSnapshot(
        headRef="refs/heads/ar/master",
        head="b" * 40,
        headTree=TREE,
        refLogFingerprint="1" * 64,
        indexTree=TREE,
        candidateTree=TREE,
        statusFingerprint="2" * 64,
    )
    return LifecycleOperationRecord(
        taskId=contract.task_id,
        taskName=contract.task_name,
        contractPath=contract.contract_path.as_posix(),
        operationKind="direct-landing",
        candidateState="a" * 64,
        candidateTree=TREE,
        fingerprint="b" * 64,
        operationKey=operation_key,
        generation=generation,
        input=DirectLandingOperationInput(
            configPath=contract.contract_path.as_posix(),
            contractPath=contract.contract_path.as_posix(),
            leafTaskDocumentRef=leaf_reference,
            effectiveInput=EffectiveCloseoutInput(
                route="direct-landing",
                contractKind="series",
                memoryMode="external",
                code=EnabledCloseoutLeg(reason="approved", message="land"),
                memory=EnabledCloseoutLeg(reason="approved", message="land"),
            ),
            approvalNote="approved",
            codeCommit=CODE_COMMIT,
            codeTree=TREE,
            candidateTree=TREE,
            memoryRepository=contract.memory_repo_path.as_posix(),
            memoryBranch=contract.memory_work_branch,
            memoryRef="refs/heads/ar/master",
            memoryBefore=snapshot,
        ),
        status="running",
        phase="direct-preflight",
        queuedAt="2026-10-22T00:00:00+00:00",
        startedAt="2026-10-22T00:00:00+00:00",
        heartbeatAt="2026-10-22T00:00:00+00:00",
        currentCommand="direct landing",
        reportPath=direct_landing_store(contract).path.with_suffix(".log").as_posix(),
        mutationEvidence={
            "code": GitMutationEvidence(
                leg="code", repository=contract.code_repo_path.as_posix(), state="pre-mutation"
            ),
            "memory": GitMutationEvidence(leg="memory", repository="/memory", state="pre-mutation"),
        },
    )


def finish_direct_generation(
    contract: WorktreeContract,
    leaf_reference: TaskDocumentRef,
    *,
    generation: int = 1,
) -> None:
    """Drive the production finish path of one direct-landing generation, as the route does."""

    store = direct_landing_store(contract)
    store.path.unlink(missing_ok=True)  # each sibling lands its own generation
    record, created = store.create(
        running_direct_generation(contract, leaf_reference, generation=generation)
    )
    assert created, "the generation could not be admitted"
    DirectLandingRuntime(contract, record).finish(
        {"codeCommit": CODE_COMMIT, "memoryContentCommit": "b" * 40}
    )


class LeafNoticeSubjectTests(unittest.TestCase):
    def _case_test_two_sibling_leaves_publish_their_own_task_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = two_leaf_world(Path(tmp))

            first = task_reference(contracts["L1"])
            second = task_reference(contracts["L2"])

            self.assertEqual(
                first.model_dump(mode="json"),
                {"repository": "repo", "path": "master/1_leaf-one.json"},
            )
            self.assertEqual(
                second.model_dump(mode="json"),
                {"repository": "repo", "path": "master/2_leaf-two.json"},
            )
            self.assertNotEqual(first, second)
            self.assertNotEqual(first.path, "master/task.json")

    def _case_test_one_leaf_subject_comes_from_its_own_declared_enclosure_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = two_leaf_world(Path(tmp))

            subject = leaf_task(contracts["L1"])

            assert subject is not None
            self.assertEqual(subject.leaf_id, "L1")
            self.assertEqual(subject.enclosure_path, contracts["L1"].contract_path)

    def _case_test_a_series_contract_still_names_its_master_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_root = root / "coordination" / "tasks" / "repo" / "master"
            task_root.mkdir(parents=True)
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "MASTER",
                        "slug": "master",
                        "title": "Master",
                        "kind": "master",
                        "status": "inProgress",
                        "repo": "repo",
                        "createdAt": "2026-08-22T00:00:00+00:00",
                    }
                ),
            )
            contract = default_series_contract(
                ContractTask("master", "repo", root / "coordination", "light-task", "external"),
                code=RepoBranchPlan(root / "repo", "main", "ar/master", "a" * 40),
                memory=RepoBranchPlan(root / "memory", "main", "ar/master", "b" * 40),
            )
            write_contract(contract.contract_path, contract)

            self.assertEqual(
                task_reference(contract).model_dump(mode="json"),
                {"repository": "repo", "path": "master/task.json"},
            )

    def _case_test_an_unresolvable_leaf_never_falls_back_to_the_master(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_root = root / "coordination" / "tasks" / "repo" / "master"
            task_root.mkdir(parents=True)
            write_task_doc(
                task_root,
                TaskDocument.model_validate(
                    {
                        "id": "MASTER",
                        "slug": "master",
                        "title": "Master",
                        "kind": "master",
                        "status": "inProgress",
                        "repo": "repo",
                        "createdAt": "2026-08-22T00:00:00+00:00",
                    }
                ),
            )
            orphan = leaf_contract(root, "L9", "9_no-such-leaf")

            self.assertIsNone(leaf_task(orphan))
            with self.assertRaises(LeafTaskIdentityError):
                task_reference(orphan)
            self.assertEqual(
                begin_landed_notice(orphan, code_commit=CODE_COMMIT, strategy="merge"),
                NO_OCCURRENCE,
            )
            store = read_notice_store(orphan.coordination_root)
            assert store is not None
            self.assertEqual((store.notices, store.pending), ([], []))

    def _case_test_ordinary_landing_publishes_each_sibling_as_its_own_subject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = two_leaf_world(Path(tmp))

            record_landed_integration(
                contracts["L1"],
                landed=LandedIntegration(strategy="merge", code_commit=CODE_COMMIT),
            )
            record_landed_integration(
                contracts["L2"],
                landed=LandedIntegration(strategy="merge", code_commit=SIBLING_COMMIT),
            )

            store = read_notice_store(contracts["L1"].coordination_root)
            assert store is not None
            self.assertEqual(
                [notice.taskKey for notice in store.notices],
                ["repo/master/1_leaf-one.json", "repo/master/2_leaf-two.json"],
            )
            self.assertEqual(
                [notice.taskReference.path for notice in store.notices],
                ["master/1_leaf-one.json", "master/2_leaf-two.json"],
            )
            self.assertEqual(store.pending, [])

    def _case_test_direct_and_abandon_notices_name_their_own_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = two_leaf_world(Path(tmp))

            direct = begin_direct_landed_notice(
                contracts["L1"],
                leaf_reference=TaskDocumentRef(repository="repo", path="master/1_leaf-one.json"),
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )
            abandon = begin_abandoned_notice(contracts["L2"])

            store = read_notice_store(contracts["L1"].coordination_root)
            assert store is not None
            self.assertEqual(
                {row.taskKey: row.kind for row in store.pending},
                {
                    "repo/master/1_leaf-one.json": "task-landed",
                    "repo/master/2_leaf-two.json": "task-abandoned",
                },
            )
            self.assertEqual({row.occurrenceId for row in store.pending}, {direct, abandon})
            self.assertIn("repo/master/1_leaf-one.json", direct)
            self.assertIn(OPERATION_KEY, direct)
            self.assertIn("repo/master/2_leaf-two.json", abandon)

    def _case_test_a_direct_landing_names_the_requested_leaf_never_the_series_master(self) -> None:
        """F05 through production: the series contract's own document is not the subject."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, leaves = series_world(Path(tmp))

            for leaf_id in ("L1", "L2"):
                finish_direct_generation(contract, leaves[leaf_id])

            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual(
                [notice.taskReference.path for notice in store.notices],
                ["master/1_leaf-one.json", "master/2_leaf-two.json"],
            )
            self.assertEqual(
                [notice.taskKey for notice in store.notices],
                ["repo/master/1_leaf-one.json", "repo/master/2_leaf-two.json"],
            )
            self.assertNotIn("repo/master/task.json", [notice.taskKey for notice in store.notices])
            self.assertEqual(
                len({notice.occurrenceId for notice in store.notices}), 2
            )  # two siblings, two occurrences
            self.assertEqual([notice.taskTitle for notice in store.notices], ["leaf L1", "leaf L2"])
            self.assertEqual(store.pending, [])

    def _case_test_a_direct_landing_carries_its_exact_generation_identity(self) -> None:
        """F06: the intent persists generation, repository, root and commit, with the key exact."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, leaves = series_world(Path(tmp))

            occurrence = begin_direct_landed_notice(
                contract,
                leaf_reference=leaves["L1"],
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=3,
            )

            store = read_notice_store(contract.coordination_root)
            assert store is not None
            row = store.pending[0]
            self.assertEqual(row.commit, CODE_COMMIT)
            self.assertEqual(row.operationKey, f"{OPERATION_KEY}:g3")
            self.assertEqual(row.operationGeneration, 3)
            self.assertEqual(row.repository, "repo")
            self.assertEqual(row.root, contract.worktree_group.as_posix())
            self.assertEqual(
                occurrence,
                f"task-landed:repo/master/1_leaf-one.json:{CODE_COMMIT}:{OPERATION_KEY}:g3",
            )

            # A different generation of the same operation is a different native occurrence.
            other = begin_direct_landed_notice(
                contract,
                leaf_reference=leaves["L1"],
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=4,
            )
            self.assertNotEqual(other, occurrence)

    def _case_test_an_unnameable_direct_leaf_writes_no_notice_and_never_the_master(self) -> None:
        """F05: a leaf whose own document is gone is not announced as the master."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, leaves = series_world(Path(tmp))
            (contract.task_root / "1_leaf-one.json").unlink()

            occurrence = begin_direct_landed_notice(
                contract,
                leaf_reference=leaves["L1"],
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )

            store = read_notice_store(contract.coordination_root)
            assert store is not None
            self.assertEqual(occurrence, NO_OCCURRENCE)
            self.assertEqual((store.notices, store.pending), ([], []))

    def _case_test_an_exact_retry_keeps_only_its_own_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = two_leaf_world(Path(tmp))
            first = begin_direct_landed_notice(
                contracts["L1"],
                leaf_reference=TaskDocumentRef(repository="repo", path="master/1_leaf-one.json"),
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )

            again = begin_direct_landed_notice(
                contracts["L1"],
                leaf_reference=TaskDocumentRef(repository="repo", path="master/1_leaf-one.json"),
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )
            other_leaf = begin_direct_landed_notice(
                contracts["L2"],
                leaf_reference=TaskDocumentRef(repository="repo", path="master/2_leaf-two.json"),
                code_commit=CODE_COMMIT,
                operation_key=OPERATION_KEY,
                generation=1,
            )

            store = read_notice_store(contracts["L1"].coordination_root)
            assert store is not None
            self.assertEqual(again, first)
            self.assertNotEqual(other_leaf, first)
            self.assertEqual([row.occurrenceId for row in store.pending], [first, other_leaf])

    def _case_test_occurrences_survive_enclosure_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contracts = two_leaf_world(Path(tmp))
            before = (
                landed_occurrence(contracts["L1"], CODE_COMMIT),
                abandoned_occurrence(contracts["L1"]),
                task_reference(contracts["L1"]),
            )
            enclosure_root = contracts["L1"].contract_path.parent.parent
            shutil.rmtree(enclosure_root)

            after = (
                landed_occurrence(contracts["L1"], CODE_COMMIT),
                abandoned_occurrence(contracts["L1"]),
                task_reference(contracts["L1"]),
            )

            self.assertEqual(after, before)
            self.assertEqual(
                before[2], TaskDocumentRef(repository="repo", path="master/1_leaf-one.json")
            )
            self.assertNotEqual(
                landed_occurrence(contracts["L1"], CODE_COMMIT),
                landed_occurrence(contracts["L2"], CODE_COMMIT),
            )

    def test_ordinary_subjects_are_each_leaf_its_own_document(self) -> None:
        """F05: an ordinary landing or abandonment names the leaf's own task document, resolved through its declared enclosure binding, never a sibling's and never the master."""

        for message, case in (
            (
                "two sibling leaves publish their own task document",
                self._case_test_two_sibling_leaves_publish_their_own_task_document,
            ),
            (
                "one leaf subject comes from its own declared enclosure binding",
                self._case_test_one_leaf_subject_comes_from_its_own_declared_enclosure_binding,
            ),
            (
                "a series contract still names its master document",
                self._case_test_a_series_contract_still_names_its_master_document,
            ),
            (
                "an unresolvable leaf never falls back to the master",
                self._case_test_an_unresolvable_leaf_never_falls_back_to_the_master,
            ),
            (
                "ordinary landing publishes each sibling as its own subject",
                self._case_test_ordinary_landing_publishes_each_sibling_as_its_own_subject,
            ),
        ):
            with self.subTest(msg=message):
                case()

    def test_a_direct_landing_names_the_leaf_it_was_asked_to_land(self) -> None:
        """F05/F06: the direct route runs on the series contract but publishes the requested leaf, carrying its exact generation identity, and writes nothing when it cannot name that leaf."""

        for message, case in (
            (
                "direct and abandon notices name their own leaf",
                self._case_test_direct_and_abandon_notices_name_their_own_leaf,
            ),
            (
                "a direct landing names the requested leaf never the series master",
                self._case_test_a_direct_landing_names_the_requested_leaf_never_the_series_master,
            ),
            (
                "a direct landing carries its exact generation identity",
                self._case_test_a_direct_landing_carries_its_exact_generation_identity,
            ),
            (
                "an unnameable direct leaf writes no notice and never the master",
                self._case_test_an_unnameable_direct_leaf_writes_no_notice_and_never_the_master,
            ),
        ):
            with self.subTest(msg=message):
                case()

    def test_a_retry_or_a_cleanup_keeps_the_subject_its_own(self) -> None:
        """F05: a retry keeps only its own occurrence identity, and a subject still resolves after the enclosure is collected."""

        for message, case in (
            (
                "an exact retry keeps only its own identity",
                self._case_test_an_exact_retry_keeps_only_its_own_identity,
            ),
            (
                "occurrences survive enclosure cleanup",
                self._case_test_occurrences_survive_enclosure_cleanup,
            ),
        ):
            with self.subTest(msg=message):
                case()


if __name__ == "__main__":
    unittest.main()
