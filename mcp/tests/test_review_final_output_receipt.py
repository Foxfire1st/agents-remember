"""The recorded final comparison beside what closeout and integration delivered (ICR-R21@v1).

A comparison generation records what a review *read*; closeout and integration produce what the task
*delivered*, and the two are different facts. These cases drive that whole operation for real -- a live
enclosure with two real datasets and a real captured candidate, the real freeze, the real publication
owner at the repository's declared location, and the real closeout and integration tools -- and then
compare every identity the receipt recorded against the store's own reopened truth: the generation
store, the Git objects and the published dataset. Nothing here is a prebuilt payload, and no case
asserts a value the operation did not produce.

The load-bearing properties, one case each:

* the conforming example -- the recorded comparison opens the pair the task actually landed, and both
  the closeout and the integration results carry it;
* the non-conforming example -- the published dataset is not the one the review compared, and the
  receipt says so instead of claiming coverage;
* the boundary -- a candidate that moves before the commit is recorded as ``moved``, the prior
  generation keeps its bytes, and a successor supersedes it by naming it;
* a leaf with no generation records no receipt and still closes, because the receipt is not a gate;
* the receipts are leaf-scoped, one per phase, and the named reclamation owner removes them.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.knowledge_publication_route import declared_publication_location
from agents_remember.application.review_candidate_resolution import (
    CANDIDATE_DATABASE_NAME,
    REVIEW_CANDIDATE_RELATIVE_ROOT,
)
from agents_remember.application.review_comparison_freeze import (
    ComparisonFreezeOptions,
    freeze_review_comparison,
)
from agents_remember.application.review_comparison_generation import (
    ComparisonGenerationManifest,
    read_generation_refs,
)
from agents_remember.application.review_comparison_reopen import reopen_comparison_generation
from agents_remember.application.review_final_output_receipt import (
    discard_final_output_receipts,
    read_final_output_receipt,
)
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
)
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.memory.knowledge.closed_snapshot import freeze_closed_snapshot
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.publication import publish_prepared_snapshot
from agents_remember.memory.knowledge.store import (
    open_existing_knowledge_store,
    open_knowledge_store,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    InvariantRequest,
    RevisionDraft,
    RevisionRequest,
)
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.models.knowledge.snapshot import SnapshotDestinationRequest
from agents_remember.tasks import SubTaskRef, TaskDocument, TaskEnclosureRef, write_task_doc
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    contract_publication_text,
    default_series_contract,
    load_contract,
    write_contract,
)
from read_scope_test_support import make_read_authorship
from test_knowledge_review_source_endpoints import EndpointFixture, build_endpoint_fixture

pytestmark = pytest.mark.evidence_unit

MESSAGES = CloseoutCommitMessages(
    code="Add the reviewed candidate",
    memory="Document the reviewed candidate",
)


REVIEW_INVARIANT_ID = "0f4a4d5e-6b7c-4d8e-9f10-1a2b3c4d5e6f"
# The identity only the candidate half records, so the two halves are different datasets rather than
# two spellings of the same one: the comparison is between them, and "the published dataset is the
# reviewed candidate" must be a measurement rather than a file name.
CANDIDATE_ONLY_INVARIANT_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
# One revision of the reviewed invariant, recorded in both halves with the same identity: a subject
# with no retained revision is not selectable at all, so the review would have nothing to compare.
REVIEW_REVISION_ID = "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e"


def _seed_review_datasets(contract: WorktreeContract) -> tuple[Path, Path]:
    """Create the leaf's two real review datasets, bound to the enclosure's own repository.

    Fixture provisioning, and it is deliberate about *why* it replaces the shared fixture's pair. That
    fixture invents a random namespace and records a **fixed** authority home beside it; the review
    route reads a dataset under its namespace and never consults that home, so the fixture has always
    been free to leave it standing -- but the publication read-back does consult it, because a dataset
    bound to another repository's authority home is another repository's publication, and the store
    refuses a namespace rebind. A dataset built that way can therefore never carry a readable
    publication for the enclosure it is reviewed in. These two are created through the store's own API
    instead, bound to the repository the contract names, with one invariant recorded in the candidate
    half so the two halves are genuinely different datasets. Everything downstream -- the resolution,
    the comparison, the freeze, the publication, the closeout -- is the shipped operation.
    """

    root = contract.worktree_group / REVIEW_CANDIDATE_RELATIVE_ROOT
    halves: dict[str, Path] = {}
    for half, invariants in (
        ("baseline", (REVIEW_INVARIANT_ID,)),
        ("candidate", (REVIEW_INVARIANT_ID, CANDIDATE_ONLY_INVARIANT_ID)),
    ):
        path = root / half / CANDIDATE_DATABASE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        store = open_knowledge_store(path, contract.repo_name)
        try:
            created = store.create_repository(
                RepositoryIdentity(
                    repository_id=contract.repo_name, authority_home=contract.repo_name
                )
            )
            assert created.state == "created", created
            for invariant_id in invariants:
                recorded = store.create_invariant(
                    InvariantRequest(
                        repository_id=contract.repo_name,
                        invariant_id=invariant_id,
                        display_label="the invariant the review compares",
                        provenance=make_read_authorship(),
                    )
                )
                assert recorded.state == "created", recorded
            if REVIEW_INVARIANT_ID in invariants:
                revision = store.create_revision(
                    RevisionRequest(
                        repository_id=contract.repo_name,
                        revision=RevisionDraft(
                            revision_id=REVIEW_REVISION_ID,
                            invariant_id=REVIEW_INVARIANT_ID,
                            display_version="v1",
                            statement="The reviewed invariant holds for this candidate.",
                            applicability=(
                                "Every admitted candidate write in this repository namespace."
                            ),
                            exclusions=("Historical rows are not rewritten by a candidate write.",),
                            provenance=make_read_authorship(),
                        ),
                    )
                )
                assert revision.state == "created", revision
        finally:
            store.close()
        halves[half] = path
    return halves["baseline"], halves["candidate"]


def _git(repository: Path, *args: str) -> str:
    """Run one Git command whose stdout is the answer, in an isolated environment."""

    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repository),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    return result.stdout.strip()


def _review_config(root: Path, contract: WorktreeContract) -> McpRuntimeConfig:
    """Bind the fixture enclosure to MCP authority without a certification profile."""

    code_link = root / contract.repo_name
    if not code_link.exists():
        code_link.symlink_to(contract.code_repo_path, target_is_directory=True)
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "retirement": {"autoLandOnIntegration": False},
                "repositories": {contract.repo_name: {}},
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


@dataclass(frozen=True)
class _ReviewCloseout:
    """One enclosure carrying both a real review candidate and a closeout-ready task tree."""

    fixture: EndpointFixture
    contract: WorktreeContract
    config: McpRuntimeConfig
    baseline_database: Path
    candidate_database: Path

    @property
    def task_root(self) -> Path:
        return self.contract.task_root


def _review_closeout_fixture(root: Path) -> _ReviewCloseout:
    """Build the review enclosure, then give it the task shape a closeout requires.

    The endpoint fixture builds the live review half: two real datasets, a real captured candidate in
    a real linked worktree, and a real external-memory repository. Closeout additionally needs the
    *task* half -- a parent series contract, the sprint/master/leaf documents and one published
    lifecycle-operation location -- which is the same shape ``test_source_lineage._fixture`` produces
    for the transaction suites, applied to this enclosure rather than a second enclosure built to fit
    it. Every dataset therefore belongs to the same repository namespace the contract names.
    """

    fixture = build_endpoint_fixture(root / "endpoint", memory_mode="external")
    contract = fixture.contract
    baseline_database, candidate_database = _seed_review_datasets(contract)
    memory_repo = contract.memory_repo_path
    assert memory_repo is not None and contract.memory_base_commit
    master_branch = f"ar/{contract.task_name}"
    _git(contract.code_repo_path, "branch", "-f", master_branch, contract.code_base_commit)
    _git(memory_repo, "branch", "-f", master_branch, contract.memory_base_commit)
    master = default_series_contract(
        ContractTask(
            contract.task_name,
            contract.repo_name,
            contract.coordination_root,
            "light-task",
            "external",
        ),
        code=RepoBranchPlan(
            contract.code_repo_path, "super", master_branch, contract.code_base_commit
        ),
        memory=RepoBranchPlan(memory_repo, "super", master_branch, contract.memory_base_commit),
        task_root=contract.task_root,
    )
    write_contract(master.contract_path, master)
    leaf = replace(
        contract,
        parent_task_name=master.task_name,
        parent_contract_path=master.contract_path,
        code_source_branch=master_branch,
        memory_source_branch=master_branch,
        memory_base_commit=contract.memory_base_commit,
    )
    write_contract(leaf.contract_path, leaf)
    for value in (master, leaf):
        publish_new_lifecycle_operation_location(
            value, contract_text=contract_publication_text(value.contract_path, value)
        )
    for repository in (leaf.code_repo_path, memory_repo):
        _git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
        _git(repository, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _write_review_task_documents(leaf)
    memory_worktree = leaf.memory_worktree
    assert memory_worktree is not None
    (memory_worktree / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory_worktree / "onboarding" / "review.md").write_text("# review\n", encoding="utf-8")
    (memory_worktree / "review-card.md").write_text("# review card\n", encoding="utf-8")
    return _ReviewCloseout(
        fixture=fixture,
        contract=load_contract(leaf.contract_path),
        config=_review_config(root, leaf),
        baseline_database=baseline_database,
        candidate_database=candidate_database,
    )


def _write_review_task_documents(contract: WorktreeContract) -> None:
    """Publish the sprint/master/leaf documents the leaf's enclosure binding is resolved through."""

    leaf_slug = contract.leaf_id.lower()
    master_slug = contract.task_root.name
    write_task_doc(
        contract.coordination_root / "tasks" / contract.repo_name / "sprint",
        TaskDocument.model_validate(
            {
                "id": "SPRINT",
                "slug": "sprint",
                "title": "Sprint",
                "kind": "master",
                "repo": contract.repo_name,
                "createdAt": "2026-09-23T00:00:00+00:00",
                "orchestrates": [master_slug],
                "integrationBranch": "super",
                "executionGraph": {
                    "nodes": [
                        {"repository": contract.repo_name, "path": f"{master_slug}/task.json"}
                    ],
                    "edges": [],
                },
            }
        ),
    )
    write_task_doc(
        contract.task_root,
        TaskDocument.model_validate(
            {
                "id": contract.task_id,
                "slug": master_slug,
                "title": contract.task_name,
                "kind": "master",
                "repo": contract.repo_name,
                "createdAt": "2026-09-23T00:00:00+00:00",
                "executionNature": "atomic",
                "subTasks": [
                    SubTaskRef(
                        number=contract.leaf_id,
                        name=contract.leaf_id,
                        file=f"{leaf_slug}.md",
                        status="inProgress",
                    )
                ],
            }
        ),
    )
    write_task_doc(
        contract.task_root,
        TaskDocument.model_validate(
            {
                "id": contract.leaf_id,
                "slug": leaf_slug,
                "title": contract.leaf_id,
                "kind": "subTask",
                "status": "inProgress",
                "repo": contract.repo_name,
                "createdAt": "2026-09-23T00:00:00+00:00",
                "master": "task.md",
                "objective": "Exercise the review-to-closeout identity receipt.",
                "requirements": ["ICR-R21@v1"],
                "seriesContractPath": series_contract_path(contract.task_root).as_posix(),
                "enclosures": [
                    TaskEnclosureRef(
                        leafId=contract.leaf_id,
                        enclosurePath=contract.contract_path.as_posix(),
                    )
                ],
            }
        ),
    )


def _freeze_review(
    enclosure: _ReviewCloseout, options: ComparisonFreezeOptions | None = None
) -> ComparisonGenerationManifest:
    """Freeze this enclosure's review through the real owner and require a published generation."""

    request = ReviewSurfaceRequest(
        repository_id=enclosure.fixture.repository_id,
        master=enclosure.fixture.master,
        leaf_id=enclosure.contract.leaf_id,
        selector=InvariantIdentitySeed(invariant_id=REVIEW_INVARIANT_ID),
    )
    outcome = freeze_review_comparison(
        enclosure.config, request, options or ComparisonFreezeOptions()
    )
    assert outcome.state == "published", outcome.refusal
    assert outcome.manifest is not None
    return outcome.manifest


def _publish_dataset(contract: WorktreeContract, source: Path) -> SnapshotIdentity:
    """Publish one dataset at the declared location through the publication owner.

    The destination is the read route's own declaration -- ``declared_publication_location`` -- and
    the install is ``publish_prepared_snapshot``, the reusable half of the publication contract that
    a caller holding a validated closed database reaches the destination through. Nothing here is a
    hand-copied file: the bytes are frozen by the storage owner and installed against the identity
    the caller admitted.
    """

    destination = declared_publication_location(contract).path
    identity = dataset_identity(source)
    store = open_existing_knowledge_store(source, identity.repository_id)
    stage = destination.parent / ".review-publication-stage.sqlite"
    try:
        prepared = freeze_closed_snapshot(store, identity, stage)
    finally:
        store.close()
    published = publish_prepared_snapshot(
        prepared, SnapshotDestinationRequest(destination_path=destination)
    )
    assert published.state in {"published", "no_change"}, published
    return identity


def _preview(enclosure: _ReviewCloseout) -> dict:
    """Run only the real closeout preview: nothing is written, nothing is committed."""

    contract = load_contract(enclosure.contract.contract_path)
    with mock.patch.object(lifecycle_operations, "require_first_ready_generation"):
        preview = worktree_tools.worktree_closeout_preview_tool(
            enclosure.config, contract.contract_path.as_posix(), MESSAGES
        )
    assert preview["ok"] is True, preview
    return preview


def _closeout(enclosure: _ReviewCloseout) -> tuple[dict, dict]:
    """Run the real closeout preview and apply, returning both results."""

    contract = load_contract(enclosure.contract.contract_path)
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch.object(lifecycle_operations, "require_first_ready_generation")
        )
        preview = worktree_tools.worktree_closeout_preview_tool(
            enclosure.config, contract.contract_path.as_posix(), MESSAGES
        )
        assert preview["ok"] is True, preview
        applied = worktree_tools.worktree_closeout_apply_tool(
            enclosure.config,
            contract.contract_path.as_posix(),
            MESSAGES,
            CloseoutApproval(intent_note="developer approved the fixture closeout"),
        )
    assert applied["ok"] is True, applied
    return preview, applied


def test_review_receipt_binds_the_delivered_pair_to_the_selected_generation(
    tmp_path, worktree_services
):
    """The conforming example: the recorded comparison opens the pair the task actually landed."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    manifest = _freeze_review(enclosure)
    published = _publish_dataset(contract, enclosure.candidate_database)

    preview, applied = _closeout(enclosure)
    assert applied["state"] == "closed", applied

    # The prepared-work result carries the selection, and the candidate it prepared IS the reviewed
    # candidate -- so the tree closeout is about to commit is the tree the generation bound.
    selection = preview["final_comparison_selection"]
    assert selection["state"] == "selected", selection
    assert selection["generation_id"] == manifest.generation_id
    assert selection["generation_index"] == manifest.generation_index
    assert selection["binding_digest"] == manifest.binding_digest
    assert selection["reviewed_candidate_code_tree_id"] == manifest.source.candidate_code_tree_id
    assert selection["prepared_is_reviewed_candidate"] is True

    # The closeout result carries the receipt, and every identity in it is the store's own.
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "recorded", receipt
    assert receipt["receipt_state"] == "bound", receipt
    assert receipt["code_match"] == "matches-reviewed-input", receipt
    assert receipt["knowledge_match"] == "matches-reviewed-input", receipt
    assert receipt["generation_id"] == manifest.generation_id
    assert (
        receipt["manifest_digest"]
        == read_generation_refs(contract.task_root, contract.leaf_id)[-1].manifest_digest
    )
    closed = load_contract(contract.contract_path)
    assert receipt["delivered_code_commit"] == closed.code_commit
    assert receipt["delivered_memory_content_commit"] == closed.memory_content_commit
    assert receipt["delivered_code_tree_id"] == _git(
        contract.code_repo_path, "rev-parse", f"{closed.code_commit}^{{tree}}"
    )
    assert receipt["delivered_code_tree_id"] == manifest.source.candidate_code_tree_id
    memory_repo = contract.memory_repo_path
    assert memory_repo is not None
    assert receipt["delivered_memory_tree_id"] == _git(
        memory_repo, "rev-parse", f"{closed.memory_content_commit}^{{tree}}"
    )
    reviewed_knowledge = manifest.knowledge_side("after").identity
    assert reviewed_knowledge is not None
    assert receipt["published_knowledge_digest"] == published.logical_digest
    assert published.logical_digest == reviewed_knowledge.logical_digest
    assert receipt["read_back"] == "matched"

    # The receipt is a durable artifact whose published bytes are the bytes reported.
    destination = Path(receipt["destination"])
    assert destination.parent == durable_reports_root(contract.task_root)
    assert destination.is_file()
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == receipt["sha256"]


def test_integration_receipt_records_the_refs_it_landed(tmp_path, worktree_services):
    """The integration result carries its own receipt, read against the refs it moved."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    manifest = _freeze_review(enclosure)
    _publish_dataset(contract, enclosure.candidate_database)
    _, applied = _closeout(enclosure)
    assert applied["state"] == "closed", applied

    integrated = worktree_tools.worktree_integrate_tool(
        enclosure.config, contract_path=contract.contract_path.as_posix(), strategy="ff-only"
    )
    assert integrated["ok"] is True, integrated
    assert integrated["state"] == "integrated", integrated
    landed = integrated["final_output_receipt"]
    assert landed["state"] == "recorded" and landed["phase"] == "integration", landed
    assert landed["generation_id"] == manifest.generation_id
    assert landed["delivered_code_commit"] == integrated["integrated_code_commit"]
    assert landed["delivered_code_commit"] == load_contract(contract.contract_path).code_commit
    assert (
        _git(contract.code_repo_path, "rev-parse", contract.code_source_branch)
        == (integrated["integrated_code_commit"])
    )
    read = read_final_output_receipt(
        contract.task_root, contract.leaf_id, manifest.generation_id, "integration"
    )
    assert read.state == "recorded"
    assert read.receipt is not None
    assert read.receipt.phase == "integration"
    assert read.receipt.statement().startswith("integration recorded comparison")
    assert read.destination.name.endswith("-integration.json")


def test_review_receipt_reports_a_published_dataset_the_review_never_compared(
    tmp_path, worktree_services
):
    """The non-conforming example: the review's candidate is not the dataset standing published."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    manifest = _freeze_review(enclosure)
    reviewed = manifest.knowledge_side("after").identity
    assert reviewed is not None
    # The baseline dataset is published instead of the reviewed candidate: a real publication, of
    # the wrong dataset -- exactly the shape the packet refuses to call a covered review.
    published = _publish_dataset(contract, enclosure.baseline_database)
    assert published.logical_digest != reviewed.logical_digest

    _, applied = _closeout(enclosure)
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "recorded", receipt
    assert receipt["receipt_state"] == "moved", receipt
    assert receipt["code_match"] == "matches-reviewed-input", receipt
    assert receipt["knowledge_match"] == "differs-from-reviewed-input", receipt
    assert receipt["published_knowledge_digest"] == published.logical_digest
    statement = receipt["statement"]
    assert "does not cover the delivered output" in statement
    assert published.logical_digest in statement and reviewed.logical_digest in statement
    assert "is the reviewed candidate dataset" not in statement


def test_a_moved_candidate_is_recorded_as_moved_and_superseded_not_relabelled(
    tmp_path, worktree_services
):
    """The boundary: a source move supersedes the prior generation without deleting its history."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    manifest = _freeze_review(enclosure)
    # The candidate moves after the freeze and before the commit, so the tree closeout delivers is
    # not the tree this generation reviewed.
    (contract.code_worktree / "moved-after-review.py").write_text("MOVED = 1\n", encoding="utf-8")

    preview, applied = _closeout(enclosure)
    assert preview["final_comparison_selection"]["prepared_is_reviewed_candidate"] is False
    receipt = applied["final_output_receipt"]
    assert receipt["receipt_state"] == "moved", receipt
    assert receipt["code_match"] == "differs-from-reviewed-input", receipt
    assert receipt["generation_id"] == manifest.generation_id
    assert receipt["delivered_code_tree_id"] != manifest.source.candidate_code_tree_id
    first = read_final_output_receipt(
        contract.task_root, contract.leaf_id, manifest.generation_id, "closeout"
    )
    assert first.state == "recorded" and first.sha256 is not None
    assert first.superseded_by == ()

    # The remedy the receipt names is a successor that supersedes this generation; the successor
    # names its predecessor in its own lineage, and the earlier receipt is not rewritten.
    successor = _freeze_review(
        enclosure,
        ComparisonFreezeOptions(
            parent=read_generation_refs(contract.task_root, contract.leaf_id)[-1]
        ),
    )
    assert successor.generation_index == manifest.generation_index + 1
    assert successor.lineage.parent_generation_id == manifest.generation_id
    after = read_final_output_receipt(
        contract.task_root, contract.leaf_id, manifest.generation_id, "closeout"
    )
    assert after.state == "recorded"
    assert after.sha256 == first.sha256
    assert after.receipt is not None and after.receipt.state == "moved"
    assert [ref.generation_id for ref in after.superseded_by] == [successor.generation_id]
    assert "supersede" in after.detail


def test_closeout_records_no_receipt_without_a_generation_and_still_closes(
    tmp_path, worktree_services
):
    """No generation is a reported state, and it is never a gate on the Git transaction."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    assert read_generation_refs(contract.task_root, contract.leaf_id) == ()

    preview, applied = _closeout(enclosure)
    assert applied["state"] == "closed", applied
    assert applied["code_commit"], applied
    selection = preview["final_comparison_selection"]
    assert selection["state"] == "no-generation", selection
    assert selection["generation_id"] is None
    assert selection["prepared_is_reviewed_candidate"] is None
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "not-recorded", receipt
    assert receipt["phase"] == "closeout"
    assert "no comparison generation is published" in receipt["detail"]
    assert not durable_reports_root(contract.task_root).joinpath("final-output-x.json").exists()


def test_final_output_receipts_are_leaf_scoped_and_reclaimed(tmp_path, worktree_services):
    """Two phases publish two files, another leaf's receipts are untouched, and reclamation removes them."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    manifest = _freeze_review(enclosure)
    _closeout(enclosure)
    integrated = worktree_tools.worktree_integrate_tool(
        enclosure.config, contract_path=contract.contract_path.as_posix(), strategy="ff-only"
    )
    assert integrated["ok"] is True, integrated

    reports = durable_reports_root(contract.task_root)
    other_leaf = reports / "final-output-other-leaf-closeout.json"
    other_leaf.write_text("{}\n", encoding="utf-8")
    published = sorted(
        path.name
        for path in reports.glob("final-output-*.json")
        if path.name.startswith(f"final-output-{contract.leaf_id}-")
    )
    assert published == [
        f"final-output-{contract.leaf_id}-{manifest.generation_id}-closeout.json",
        f"final-output-{contract.leaf_id}-{manifest.generation_id}-integration.json",
    ]

    removed = discard_final_output_receipts(contract.task_root, contract.leaf_id)
    assert sorted(path.name for path in removed) == published
    assert other_leaf.is_file()
    gone = read_final_output_receipt(
        contract.task_root, contract.leaf_id, manifest.generation_id, "closeout"
    )
    assert gone.state == "not-recorded"
    assert gone.receipt is None
    assert reports.as_posix() in gone.detail


def test_review_receipt_reports_an_unreadable_published_location(tmp_path, worktree_services):
    """A location holding something that is not a dataset is reported unusable, not as a mismatch."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    _freeze_review(enclosure)
    destination = declared_publication_location(contract).path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("this is not a knowledge dataset\n", encoding="utf-8")

    _, applied = _closeout(enclosure)
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "recorded", receipt
    assert receipt["code_match"] == "matches-reviewed-input", receipt
    assert receipt["knowledge_match"] == "not-comparable", receipt
    assert receipt["published_knowledge_state"] == "unusable", receipt
    assert receipt["published_knowledge_digest"] is None
    assert receipt["receipt_state"] == "unmeasured", receipt
    statement = receipt["statement"]
    assert "is unusable, so the published dataset was not compared" in statement
    assert "is the reviewed candidate dataset" not in statement
    assert "is the delivered output" not in statement
    assert destination.read_text(encoding="utf-8") == "this is not a knowledge dataset\n"


def test_preview_selection_sentence_never_claims_a_recording_the_store_lacks(
    tmp_path, worktree_services
):
    """The prepared-work sentence is prospective, and the store agrees with it in both preview states.

    Two states of the real preview, each with the store's own reader beside it: nothing recorded yet, and
    a recorded closeout for a generation the review has since superseded. A present-tense claim ("…is
    recorded against") would be false in both -- the first has no receipt at all and the second's receipt
    names the predecessor -- so the sentence may only state the relation the preview actually has.
    """

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    first = _freeze_review(enclosure)

    # State (a): a generation exists and nothing has been recorded for it.
    before = _preview(enclosure)["final_comparison_selection"]
    assert before["state"] == "selected", before
    assert before["generation_id"] == first.generation_id
    assert "is to be recorded against" in before["detail"], before["detail"]
    assert "is recorded against" not in before["detail"], before["detail"]
    nothing = read_final_output_receipt(
        contract.task_root, contract.leaf_id, first.generation_id, "closeout"
    )
    assert nothing.state == "not-recorded", nothing.detail

    # The store records a closeout against it, then the review moves and a successor is frozen.
    _publish_dataset(contract, enclosure.candidate_database)
    _, applied = _closeout(enclosure)
    assert applied["final_output_receipt"]["generation_id"] == first.generation_id
    (contract.code_worktree / "moved-after-review.py").write_text("MOVED = 1\n", encoding="utf-8")
    predecessor = read_generation_refs(contract.task_root, contract.leaf_id)[-1]
    successor = _freeze_review(enclosure, ComparisonFreezeOptions(parent=predecessor))
    assert successor.generation_index == first.generation_index + 1

    # State (b): the preview names the successor, and the store's receipt still names the first
    # generation -- which is exactly why the sentence must not say the output *is* recorded against it.
    after = _preview(enclosure)["final_comparison_selection"]
    assert after["state"] == "selected", after
    assert after["generation_id"] == successor.generation_id
    assert "is to be recorded against" in after["detail"], after["detail"]
    assert "is recorded against" not in after["detail"], after["detail"]
    stored = read_final_output_receipt(
        contract.task_root, contract.leaf_id, first.generation_id, "closeout"
    )
    assert stored.state == "recorded", stored.detail
    assert stored.receipt is not None and stored.receipt.generation_id == first.generation_id
    assert [ref.generation_id for ref in stored.superseded_by] == [successor.generation_id]
    unrecorded = read_final_output_receipt(
        contract.task_root, contract.leaf_id, successor.generation_id, "closeout"
    )
    assert unrecorded.state == "not-recorded", unrecorded.detail


def test_a_selected_knowledge_operand_with_nothing_published_is_never_bound(
    tmp_path, worktree_services
):
    """A selected knowledge operand that was never compared yields the unmeasured verdict, not coverage."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    manifest = _freeze_review(enclosure)
    assert manifest.knowledge_side("after").state == "retained"

    _, applied = _closeout(enclosure)
    receipt = applied["final_output_receipt"]
    assert receipt["state"] == "recorded", receipt
    assert receipt["receipt_state"] == "unmeasured", receipt
    assert receipt["code_match"] == "matches-reviewed-input", receipt
    assert receipt["knowledge_match"] == "not-comparable", receipt
    assert receipt["published_knowledge_state"] == "not-recorded", receipt
    statement = receipt["statement"]
    assert "not compared against any delivered dataset" in statement
    assert "leaves the delivered knowledge unmeasured" in statement
    assert "is the delivered output" not in statement
    stored = read_final_output_receipt(
        contract.task_root, contract.leaf_id, manifest.generation_id, "closeout"
    )
    assert stored.state == "recorded"
    assert stored.receipt is not None and stored.receipt.state == "unmeasured"

    # The integration result measures the same delivery and cannot promote it to coverage either.
    integrated = worktree_tools.worktree_integrate_tool(
        enclosure.config, contract_path=contract.contract_path.as_posix(), strategy="ff-only"
    )
    assert integrated["ok"] is True, integrated
    assert integrated["final_output_receipt"]["receipt_state"] == "unmeasured", integrated


def test_a_forged_coverage_verdict_is_refused_when_read_back(tmp_path, worktree_services):
    """The verdict rule is enforced by the record, so `bound` cannot be edited onto an unmeasured delivery."""

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    manifest = _freeze_review(enclosure)
    _, applied = _closeout(enclosure)
    receipt = applied["final_output_receipt"]
    assert receipt["receipt_state"] == "unmeasured", receipt

    destination = Path(receipt["destination"])
    genuine_bytes = destination.read_bytes()
    stored = json.loads(genuine_bytes.decode("utf-8"))
    assert stored["state"] == "unmeasured"
    assert stored["knowledge_match"] == "not-comparable"
    assert stored["reviewed_knowledge_state"] == "retained"

    # Canonical bytes, so the only thing that can refuse this record is the rule it breaks.
    forged = canonical_json_bytes({**stored, "state": "bound"})
    destination.write_bytes(forged)
    refused = read_final_output_receipt(
        contract.task_root, contract.leaf_id, manifest.generation_id, "closeout"
    )
    assert refused.state == "unreadable", refused.detail
    assert "verdict" in refused.detail
    assert refused.receipt is None

    destination.write_bytes(genuine_bytes)
    restored = read_final_output_receipt(
        contract.task_root, contract.leaf_id, manifest.generation_id, "closeout"
    )
    assert restored.state == "recorded"
    assert restored.receipt is not None and restored.receipt.state == "unmeasured"


def test_the_reopened_generation_reports_what_the_task_delivered(tmp_path, worktree_services):
    """Reopening the recorded comparison reports the delivered phase, not just the inputs it bound.

    The reopen owner is the production consumer of the receipt reader: the closed-leaf review route
    (:mod:`agents_remember.application.review_committed_leaf`) reopens a generation, so what a phase
    recorded travels with the record a reader opens rather than living in a file name a caller has to
    know. The integration phase has not been measured at this point, and that is an entry too.
    """

    enclosure = _review_closeout_fixture(tmp_path)
    contract = enclosure.contract
    manifest = _freeze_review(enclosure)
    _publish_dataset(contract, enclosure.candidate_database)
    _, applied = _closeout(enclosure)
    receipt = applied["final_output_receipt"]

    read = read_final_output_receipt(
        contract.task_root, contract.leaf_id, manifest.generation_id, "closeout"
    )
    assert read.state == "recorded", read.detail
    assert read.sha256 == receipt["sha256"]
    assert read.superseded_by == ()
    assert read.receipt is not None and read.receipt.state == "bound"
    assert "is the reviewed candidate dataset" in read.receipt.statement()
    reopened = reopen_comparison_generation(
        enclosure.config,
        enclosure.fixture.repository_id,
        enclosure.fixture.master,
        contract.leaf_id,
    )
    assert reopened.state == "available", reopened.refusal
    assert reopened.manifest is not None
    assert reopened.manifest.generation_id == manifest.generation_id
    assert reopened.manifest.source.candidate_code_tree_id == receipt["delivered_code_tree_id"]
    assert receipt["statement"].startswith("closeout recorded comparison generation")

    # The reopen owner is the production consumer of the receipt reader: reopening this generation
    # reports what the task delivered for it, in phase order, with the closeout entry recorded and
    # the integration phase not yet measured.
    assert [entry.phase for entry in reopened.final_output] == ["closeout", "integration"]
    closeout_entry = reopened.final_output[0]
    assert closeout_entry.state == "recorded", closeout_entry.detail
    assert closeout_entry.sha256 == receipt["sha256"]
    assert closeout_entry.receipt is not None
    assert closeout_entry.receipt.delivered_code_commit == receipt["delivered_code_commit"]
    assert closeout_entry.receipt.state == "bound"
    assert reopened.final_output[1].state == "not-recorded"
