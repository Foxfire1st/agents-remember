"""ICR-R22@v1: a managed sync rebinds the leaf's review to the pair it resolved.

The defect this module protects is the packet's non-conforming example -- the old review stays current
while its own inputs move under it -- and the four cases drive the real managed path to show it: the
production `worktree_sync` tool, the real transaction, the real binary-stage knowledge merge, the real
publication owner and the real comparison-generation owner. Nothing here injects a resolution, a
payload or a prebuilt record, and every assertion compares the record against the store's own reopened
truth rather than against the response that carried it.

It lives beside `test_worktree_sync.py` rather than inside it for the repository's file-size rail: that
module was already in the 900-line soft band, and these four cases plus their fixture would have taken
it over the 1200-line hard rail. The sibling module keeps every pre-existing managed-sync case; this one
owns the rebinding obligation, so each module is one intent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import apsw
import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.knowledge_publication_route import declared_publication_location
from agents_remember.application.published_intent import (
    PublishedIntentUnavailable,
    resolve_published_intent,
)
from agents_remember.application.review_candidate_resolution import (
    CANDIDATE_DATABASE_NAME,
    REVIEW_CANDIDATE_RELATIVE_ROOT,
)
from agents_remember.application.review_comparison_freeze import freeze_review_comparison
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationManifest,
    generation_directory,
    leaf_generation_root,
    read_manifest,
)
from agents_remember.application.review_sync_rebinding import (
    discard_review_sync_rebindings,
    read_review_sync_rebinding,
    rebinding_file_name,
    rebinding_names_the_generation,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.memory.knowledge.closed_snapshot import freeze_closed_snapshot
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.publication import publish_prepared_snapshot
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import InvariantRequest, RevisionDraft, RevisionRequest
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.models.knowledge.review_sync_rebinding import ReviewSyncRebinding
from agents_remember.models.knowledge.snapshot import SnapshotDestinationRequest
from agents_remember.models.worktree import SyncResolutionInput
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.future_code_candidate import (
    capture_future_code_candidate,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    contract_publication_text,
    load_contract,
)
from merge_case_test_support import BASE_REVISION_ID, add_anchor, labels_of, set_label
from pydantic import ValidationError
from read_scope_test_support import make_read_authorship
from test_knowledge_review_source_endpoints import build_endpoint_fixture

REVIEW_INVARIANT_ID = "0f4a4d5e-6b7c-4d8e-9f10-1a2b3c4d5e6f"
REVIEW_REVISION_ID = "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e"
# The identity only the candidate half records, so the two review halves are different datasets
# rather than two spellings of one: a comparison is *between* two datasets, and "the published
# dataset is the reviewed candidate" has to be a measurement rather than a file name.
CANDIDATE_ONLY_INVARIANT_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
# A fixed identity for the anchor the union case adds on one side only, so a failure names the
# same row every run rather than a fresh UUID.
CONFLICT_ANCHOR_ID = "55555555-5555-4555-8555-555555555555"
# The base source anchor the authored knowledge line records, so the arriving side's own anchor is a
# genuinely disjoint addition rather than the first row of its table.
CASE_ANCHOR_ID = "66666666-6666-4666-8666-666666666666"
NEWLINE = "\n"


class ReviewSyncFixture:
    """One live leaf enclosure whose review is real and whose official lines can move.

    The enclosure is the shipped endpoint fixture's own -- two datasets, a real captured candidate in
    a real linked worktree and a real external memory half -- and this class adds only what a managed
    sync needs on top of it: an MCP configuration bound to the enclosure's coordination root, one
    published lifecycle-operation location, and two review datasets created through the store's own
    API inside the leaf's disposable knowledge root. Everything downstream -- the capture, the
    comparison, the freeze, the publication, the transaction and the rebinding -- is the shipped
    operation, and no case injects a resolution, a payload or a prebuilt record.
    """

    def __init__(self, root: Path) -> None:
        self.endpoint = build_endpoint_fixture(root / "endpoint", memory_mode="external")
        self.contract = self.endpoint.contract
        self.repository_id = self.contract.repo_name
        link = root / self.repository_id
        if not link.exists():
            link.symlink_to(self.code_repo, target_is_directory=True)
        self.config = self._bind_authority(root)
        self.baseline_database, self.candidate_database = self._seed_review_datasets()
        for repository in (self.code_repo, self.memory_repo):
            git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
            git(repository, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        publish_new_lifecycle_operation_location(
            self.contract,
            contract_text=contract_publication_text(self.contract.contract_path, self.contract),
        )

    @property
    def memory_worktree(self) -> Path:
        worktree = self.contract.memory_worktree
        assert worktree is not None
        return worktree

    @property
    def memory_repo(self) -> Path:
        repository = self.contract.memory_repo_path
        assert repository is not None
        return repository

    @property
    def code_repo(self) -> Path:
        return self.contract.code_repo_path

    def _bind_authority(self, root: Path) -> McpRuntimeConfig:
        """Write the settings file the tool's own contract admission reloads, and load it."""

        config_path = root / "settings.json"
        config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.contract.coordination_root.as_posix(),
                    "workspaceRoot": root.as_posix(),
                    "retirement": {"autoLandOnIntegration": False},
                    "repositories": {self.repository_id: {}},
                }
            ),
            encoding="utf-8",
        )
        return load_config(config_path)

    def _seed_review_datasets(self) -> tuple[Path, Path]:
        """Create the leaf's two review datasets through the store, bound to its own namespace.

        The endpoint fixture's own datasets are invented namespaces recorded beside a fixed authority
        home, which the publication route rightly refuses -- a dataset bound to another repository's
        authority home is another repository's publication. These two are created through the store's
        API instead, in the leaf's disposable knowledge root, with the candidate half carrying one
        invariant the baseline half does not.
        """

        root = self.contract.worktree_group / REVIEW_CANDIDATE_RELATIVE_ROOT
        halves: dict[str, Path] = {}
        for half, invariants in (
            ("baseline", (REVIEW_INVARIANT_ID,)),
            ("candidate", (REVIEW_INVARIANT_ID, CANDIDATE_ONLY_INVARIANT_ID)),
        ):
            path = root / half / CANDIDATE_DATABASE_NAME
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
            store = open_knowledge_store(path, self.repository_id)
            try:
                created = store.create_repository(
                    RepositoryIdentity(
                        repository_id=self.repository_id, authority_home=self.repository_id
                    )
                )
                assert created.state == "created", created
                for invariant_id in invariants:
                    recorded = store.create_invariant(
                        InvariantRequest(
                            repository_id=self.repository_id,
                            invariant_id=invariant_id,
                            display_label="the invariant the review compares",
                            provenance=make_read_authorship(),
                        )
                    )
                    assert recorded.state == "created", recorded
                revision = store.create_revision(
                    RevisionRequest(
                        repository_id=self.repository_id,
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

    def build_knowledge_base(self) -> Path:
        """Author a base dataset in the leaf's namespace, through the store's own operations.

        A three-way knowledge merge needs a real common ancestor whose namespace is the one the
        enclosure is bound to, so this builds one: the same invariant and revision identities the
        shared merge-case support authors, with one anchor and one realization so the sides have
        something to diverge *from*, and nothing hand-written into the file.
        """

        path = self.contract.worktree_group / "authored" / "base.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        store = open_knowledge_store(path, self.repository_id)
        try:
            created = store.create_repository(
                RepositoryIdentity(
                    repository_id=self.repository_id, authority_home=self.repository_id
                )
            )
            assert created.state == "created", created
            invariant = store.create_invariant(
                InvariantRequest(
                    repository_id=self.repository_id,
                    invariant_id=REVIEW_INVARIANT_ID,
                    display_label="the invariant the merge case compares",
                    provenance=make_read_authorship(),
                )
            )
            assert invariant.state == "created", invariant
            revision = store.create_revision(
                RevisionRequest(
                    repository_id=self.repository_id,
                    revision=RevisionDraft(
                        revision_id=BASE_REVISION_ID,
                        invariant_id=REVIEW_INVARIANT_ID,
                        display_version="v1",
                        statement="The base statement both sides descend from.",
                        applicability="Every dataset in this leaf's namespace.",
                        provenance=make_read_authorship(),
                    ),
                )
            )
            assert revision.state == "created", revision
        finally:
            store.close()
        add_anchor(path, CASE_ANCHOR_ID, "src/base_anchor.py")
        return path

    def adopt_base_dataset(self, base: Path) -> None:
        """Make one authored dataset the review's candidate and the leaf's published line.

        The review's two halves and the memory line's dataset have to be one dataset for a merge to
        be a merge rather than an add/add conflict, so this copies it into all three places and lets
        the caller commit each side from there.
        """

        shutil.copyfile(base, self.candidate_database)
        shutil.copyfile(base, self.baseline_database)

    def commit_leaf_candidate(self) -> str:
        """Commit the leaf's uncommitted candidate, returning the new head.

        A real leaf reaches a sync with its candidate committed (the enclosure fixture leaves one
        uncommitted so the capture cases can bind staged, unstaged and untracked content), and a
        clean worktree is what lets these cases isolate the two things they measure: the moving
        official line and, when a case parks one, its own untracked file.
        """

        worktree = self.contract.code_worktree
        git(worktree, "add", "--all")
        git(worktree, "commit", "-m", "the leaf's committed candidate")
        return git(worktree, "rev-parse", "HEAD")

    def on_official_line(self, repository: Path, action):
        """Run ``action`` with one repository checked out on its recorded source branch.

        Both fixture repositories are checked out on their own default branch, so the official line a
        sync reads is advanced explicitly and the checkout is returned where it was: a case must
        never depend on which branch happened to be checked out.
        """

        branch = (
            self.contract.code_source_branch
            if repository == self.code_repo
            else self.contract.memory_source_branch
        )
        previous = git(repository, "symbolic-ref", "--short", "HEAD")
        git(repository, "checkout", branch)
        try:
            return action()
        finally:
            git(repository, "checkout", previous)

    def review_request(self) -> ReviewSurfaceRequest:
        return ReviewSurfaceRequest(
            repository_id=self.repository_id,
            master=self.contract.task_root.name,
            leaf_id=self.contract.leaf_id,
            selector=InvariantIdentitySeed(invariant_id=REVIEW_INVARIANT_ID),
        )

    def freeze_review(self) -> ComparisonGenerationManifest:
        """Freeze this enclosure's review through the real owner and require a published record."""

        outcome = freeze_review_comparison(self.config, self.review_request())
        assert outcome.state == "published", outcome.refusal
        assert outcome.manifest is not None
        return outcome.manifest

    def publish_reviewed_candidate(self) -> SnapshotIdentity:
        """Install the reviewed candidate at the declared location through the publication owner.

        The destination is the read route's own declaration and the install is the reusable half of
        the publication contract, so what stands at the location afterwards is a real published
        dataset rather than a copied file.
        """

        location = declared_publication_location(self.contract)
        destination = location.path
        identity = dataset_identity(self.candidate_database)
        store = open_knowledge_store(self.candidate_database, identity.repository_id)
        stage = destination.parent / ".review-publication-stage.sqlite"
        try:
            prepared = freeze_closed_snapshot(store, identity, stage)
        finally:
            store.close()
        published = publish_prepared_snapshot(
            prepared,
            SnapshotDestinationRequest(
                destination_path=destination,
                expected_destination=admitted_destination_identity(location),
            ),
        )
        assert published.state in {"published", "no_change"}, published
        return identity

    def sync(self, **kwargs: Any) -> dict[str, Any]:
        """Run the production sync tool for this enclosure's contract, rebinding included."""

        return worktree_tools.worktree_sync_tool(
            self.config, contract_path=self.contract.contract_path.as_posix(), **kwargs
        )

    def reload_contract(self) -> WorktreeContract:
        return load_contract(self.contract.contract_path)

    def capture_tree(self) -> str:
        """The leaf's candidate tree after whatever the case did, through the capture owner."""

        return capture_future_code_candidate(self.reload_contract()).codeCandidateTree

    def generation_directory(self, generation_id: str) -> Path:
        """Where one generation of this leaf was published, from the store's own layout owner."""

        return generation_directory(self.contract.task_root, self.contract.leaf_id, generation_id)


class ManagedSyncReviewRebindingTests(unittest.TestCase):
    """ICR-R22@v1: a managed sync rebinds the review to the pair it resolved.

    The defect these cases protect is the packet's non-conforming example -- the old review stays
    current while its inputs move under it -- so every case drives the real managed path: the
    production sync tool, the real transaction, the real binary-stage knowledge merge and the real
    publication owner. Each assertion compares the record against the store's own reopened truth
    rather than against the response that carried it.
    """

    def test_official_source_movement_is_measured_against_the_reviewed_generation(self) -> None:
        """A sync that carries the leaf onto a moved official line records the comparison as moved.

        The reviewed candidate tree was captured from the leaf's worktree; the official code line then
        advances and the sync merges it in, so the tree the review bound is no longer the tree the
        leaf holds. The record must say so, name both identities, and leave the judged generation
        exactly as it was: recording movement is not the act of publishing a successor.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            manifest = fixture.freeze_review()
            reviewed = manifest.source.candidate_code_tree_id
            directory = fixture.generation_directory(manifest.generation_id)

            # The reviewed dataset is installed where a later reader selects it, so the knowledge
            # channel is measured here and the verdict this case reads is about the moved source.
            published = fixture.publish_reviewed_candidate()

            def land_official_commit() -> str:
                commit_file(fixture.contract.code_repo_path, "src/landed.py", "VALUE = 'landed'")
                return git(
                    fixture.contract.code_repo_path,
                    "rev-parse",
                    fixture.contract.code_source_branch,
                )

            official_tip = fixture.on_official_line(
                fixture.contract.code_repo_path, land_official_commit
            )

            payload = fixture.sync(memory_sync_choice="skip-memory")

            assert payload["ok"] is True, payload
            rebinding = payload["review_rebinding"]
            assert rebinding["state"] == "moved", rebinding
            assert rebinding["covers_resolved_pair"] is False, rebinding
            assert rebinding["code_match"] == "differs-from-reviewed-input", rebinding
            assert rebinding["reviewed_candidate_code_tree_id"] == reviewed, rebinding
            assert rebinding["knowledge_match"] == "matches-reviewed-input", rebinding
            assert rebinding["reviewed_knowledge_logical_digest"] == published.logical_digest
            # The remedy names the one operation that produces a current comparison, and it is the
            # freeze owner's own entry point rather than a second route invented here.
            assert "freeze_review_comparison" in rebinding["successor_action"], rebinding
            assert "parent" in rebinding["successor_action"], rebinding
            assert rebinding["generation_id"] == manifest.generation_id, rebinding
            assert rebinding["binding_digest"] == manifest.binding_digest, rebinding
            assert rebinding["resolved_code_head"] == git(
                fixture.contract.code_worktree, "rev-parse", "HEAD"
            )
            # The resolved source side is the leaf's own capture after the sync, not a claim.
            assert rebinding["resolved_candidate_code_tree_id"] == fixture.capture_tree()
            assert rebinding["read_back"] == "matched", rebinding

            # The resolved pair the record names is the one the store holds: a real merge of the
            # recorded pre-sync head with the moved official tip.
            parents = git(
                fixture.contract.code_worktree, "rev-list", "--parents", "-n", "1", "HEAD"
            ).split()
            assert sorted(parents[1:]) == sorted(
                [manifest.source.candidate_capture.observedCodeHead, official_tip]
            )
            # The judged generation kept every byte and every recorded identity it had.
            kept = read_manifest(directory / COMPARISON_MANIFEST_NAME)
            assert kept.model_dump(mode="json") == manifest.model_dump(mode="json")
            # And the record is durable, with its bytes back-read and its own reader agreeing.
            read = read_review_sync_rebinding(
                fixture.contract.task_root, fixture.contract.leaf_id, manifest.generation_id
            )
            assert read.state == "recorded", read
            assert read.rebinding is not None, read
            assert read.rebinding.state == "moved"
            assert read.rebinding.statement() == rebinding["statement"]

    def test_clean_knowledge_union_is_measured_and_the_parked_wip_returns(self) -> None:
        """The conforming example: a real knowledge union, a measured rebinding, the WIP handed back.

        One authored knowledge line is committed as both sides' ancestor, the two sides then make
        disjoint changes to it, and a curator's own uncommitted file is parked for the duration. The
        binary-stage adapter merges the pair into one dataset, the parked file comes back with no
        stash entry left behind, and the rebinding measures the dataset that now stands at the
        declared publication location against the dataset the review compared -- so the verdict is
        ``moved``, and the judged generation keeps every byte it had.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            member = "knowledge.sqlite"
            worktree = fixture.memory_worktree
            reviewed, published, left_head, right_head = stage_knowledge_divergence(fixture, member)
            # The curator's own uncommitted work, which the transaction must hand back.
            (worktree / "onboarding").mkdir(parents=True, exist_ok=True)
            (worktree / "onboarding" / "wip.md").write_text("# parked" + NEWLINE, encoding="utf-8")

            payload = fixture.sync(memory_sync_choice="merge-memory")

            assert payload["ok"] is True, payload
            # The union is visible in knowledge, and it is a real merge of both sides.
            merged = dataset_identity(worktree / member)
            assert merged != reviewed.knowledge_side("after").identity
            assert sorted(
                git(worktree, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
            ) == sorted([left_head, right_head])
            # Each side's own authored change survived into the union.
            assert "the left side's own label" in labels_of(worktree / member)
            assert anchor_paths_of(worktree / member).get(CONFLICT_ANCHOR_ID) == (
                "src/right_anchor.py"
            )
            # The parked candidate came back and no stash entry was left behind.
            assert (worktree / "onboarding" / "wip.md").read_text(encoding="utf-8") == (
                "# parked" + NEWLINE
            )
            assert git(worktree, "stash", "list") == ""
            assert_rebinding_measures_the_location(
                payload["review_rebinding"], reviewed, published, merged, worktree / member
            )
            # The judged generation is untouched, and no successor was published in its place.
            kept = read_manifest(
                fixture.generation_directory(reviewed.generation_id) / COMPARISON_MANIFEST_NAME
            )
            assert kept.model_dump(mode="json") == reviewed.model_dump(mode="json")

    def test_a_forged_rebinding_verdict_is_refused_by_the_record_itself(self) -> None:
        """The record cannot claim coverage its own measured identities deny.

        The published record is the writer's word; a reader that trusts it is trusting a sentence
        rather than a measurement. Each mutation below rewrites one field of a real published record
        and requires the model to refuse it, so every guard in the validator is load-bearing: remove
        one and the corresponding case reads as a valid record claiming something nobody measured.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            fixture.publish_reviewed_candidate()
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/landed.py", "VALUE = 'landed'"),
            )
            payload = fixture.sync(memory_sync_choice="skip-memory")
            assert payload["ok"] is True, payload
            record = json.loads(
                Path(str(payload["review_rebinding"]["evidence"])).read_text(encoding="utf-8")
            )
            # The record as published is one the model accepts, and its own fields say what it
            # measured -- so each mutation below contradicts a value the record itself carries.
            published = ReviewSyncRebinding.model_validate(record)
            assert published.state == "moved", published.state
            assert published.code_match == "differs-from-reviewed-input"
            assert published.covers_resolved_pair() is False
            checked = []
            for label, mutated in (
                # the verdict contradicting the channels it carries
                ("state", {**record, "state": "current"}),
                # a channel claiming a comparison the identities deny
                ("code_match", {**record, "code_match": "matches-reviewed-input"}),
                ("knowledge_match", {**record, "knowledge_match": "unmeasured"}),
                # both channels *and* the verdict forged together, so nothing but the derivation
                # from the carried identities stands against the record
                (
                    "code_and_verdict",
                    {
                        **record,
                        "code_match": "matches-reviewed-input",
                        "knowledge_match": "matches-reviewed-input",
                        "state": "current",
                    },
                ),
                # a knowledge state the record's own digest contradicts
                (
                    "reviewed_knowledge_state",
                    {**record, "reviewed_knowledge_state": "not-selected"},
                ),
                # both knowledge fields dropped, so the channel names no reviewed operand at all
                (
                    "reviewed_knowledge",
                    {
                        **record,
                        "reviewed_knowledge_state": "not-selected",
                        "reviewed_knowledge_logical_digest": None,
                    },
                ),
                # the knowledge channel's dependency dropped *and* the verdict forged to follow it,
                # so only the retained-state/digest rule stands against the record
                (
                    "dependency_dropped",
                    {
                        **record,
                        "reviewed_knowledge_state": "not-selected",
                        "reviewed_knowledge_logical_digest": None,
                        "knowledge_match": "unmeasured",
                        "state": "moved",
                    },
                ),
                # a dataset identity beside a location reported as holding nothing, with the
                # channel's verdict forged to follow the state
                (
                    "identity_beside_unread",
                    {
                        **record,
                        "knowledge_match": "unmeasured",
                        "state": "unmeasured",
                        "resolved_knowledge": {
                            **record["resolved_knowledge"],
                            "state": "not-recorded",
                        },
                    },
                ),
            ):
                with pytest.raises(ValidationError):
                    ReviewSyncRebinding.model_validate(mutated)
                checked.append(label)
            assert checked == [
                "state",
                "code_match",
                "knowledge_match",
                "code_and_verdict",
                "reviewed_knowledge_state",
                "reviewed_knowledge",
                "dependency_dropped",
                "identity_beside_unread",
            ]

            # A record whose *identity* fields are forged cannot be caught by the record's own
            # validator -- comparing two fabricated identities is still a comparison -- so the
            # measurement is checked against the generation itself, and a forged reviewed identity
            # stops describing the manifest.
            manifest = read_manifest(
                fixture.generation_directory(reviewed.generation_id) / COMPARISON_MANIFEST_NAME
            )
            read = read_review_sync_rebinding(
                fixture.contract.task_root, fixture.contract.leaf_id, reviewed.generation_id
            )
            assert rebinding_names_the_generation(read, manifest) is not None, read
            forged_identity = replace(
                read,
                rebinding=ReviewSyncRebinding.model_validate(
                    {**record, "reviewed_candidate_code_tree_id": "0" * 40}
                ),
            )
            assert rebinding_names_the_generation(forged_identity, manifest) is None

    def test_every_state_that_carried_nothing_says_which_one_it_is(self) -> None:
        """F1: a preview, an already-current pair, a retained conflict and a cancel are four facts.

        None of the four resolved a pair, and none may be reported as one -- but neither may any of
        them be described as something it is not. Each must name the store fact it observed, and the
        durable location the record would occupy must stay empty for all four. A live already-current
        sync is the case that used to render "this result is not a completed sync" beside the payload's
        own summary saying the recorded pair already contained the official line.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            destination = durable_reports_root(fixture.contract.task_root) / rebinding_file_name(
                fixture.contract.leaf_id, reviewed.generation_id
            )

            # (1) A live sync that carried nothing: the recorded pair already held the official line.
            already_current = fixture.sync(memory_sync_choice="skip-memory")
            assert already_current["ok"] is True, already_current
            assert already_current["state"] == "already-current", already_current
            block = already_current["review_rebinding"]
            assert block["state"] == "no-movement", block
            assert "already contained the official line" in block["detail"], block
            assert destination.exists() is False

            # (2) A genuine preview: the official line has moved, and the dry run moves nothing.
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/landed.py", "VALUE = 'landed'"),
            )
            before = git(fixture.contract.code_worktree, "rev-parse", "HEAD")
            preview = fixture.sync(memory_sync_choice="skip-memory", dry_run=True)
            assert preview["ok"] is True, preview
            assert preview["state"] == "would-sync", preview
            assert preview["review_rebinding"]["state"] == "preview", preview
            assert git(fixture.contract.code_worktree, "rev-parse", "HEAD") == before
            assert destination.exists() is False

            # (3) A source conflict the adapter will not settle: the pair is not resolved yet.
            worktree = fixture.contract.code_worktree
            commit_file(worktree, "src/shared.py", "VALUE = 'candidate'")
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/shared.py", "VALUE = 'official'"),
            )
            stopped = fixture.sync(memory_sync_choice="skip-memory")
            assert stopped["ok"] is False, stopped
            assert stopped["state"] == "sync-resolution-required", stopped
            assert stopped["review_rebinding"]["state"] == "not-resolved", stopped
            assert destination.exists() is False

            # (4) The supported cancellation of that same retained transaction.
            cancelled = fixture.sync(resolution=SyncResolutionInput(action="cancel"))
            assert cancelled["ok"] is True, cancelled
            assert cancelled["state"] == "sync-cancelled", cancelled
            assert cancelled["review_rebinding"]["state"] == "cancelled", cancelled
            assert destination.exists() is False

    def test_the_source_clause_names_a_locator_not_a_carrier(self) -> None:
        """F2: with the leaf's WIP restored the head does not carry the add-all capture.

        The captured candidate is the worktree's whole add-all content, so it equals the head's own
        tree only while the worktree is clean -- and the state where it does not is exactly the state
        the packet requires the sync to preserve. The sentence must therefore locate the capture at
        the head rather than claim the head carries it, which is a claim Git denies here.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            fixture.freeze_review()
            fixture.publish_reviewed_candidate()
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/landed.py", "VALUE = 'landed'"),
            )
            # The curator's own uncommitted work, which the sync parks and hands back.
            worktree = fixture.contract.code_worktree
            wip = worktree / "src" / "curator-wip.py"
            wip.parent.mkdir(parents=True, exist_ok=True)
            wip.write_text("# parked through the sync" + NEWLINE, encoding="utf-8")

            payload = fixture.sync(memory_sync_choice="skip-memory")

            assert payload["ok"] is True, payload
            rebinding = payload["review_rebinding"]
            assert wip.read_text(encoding="utf-8") == ("# parked through the sync" + NEWLINE)
            assert git(worktree, "stash", "list") == ""
            head_tree = git(worktree, "rev-parse", f"{rebinding['resolved_code_head']}^{{tree}}")
            capture = rebinding["resolved_candidate_code_tree_id"]
            # The relation the old wording asserted is denied by the store in this very state.
            assert head_tree != capture, (head_tree, capture)
            assert f"carries candidate tree {capture}" not in rebinding["statement"]
            assert (
                f"candidate tree {capture} captured from the leaf's worktree at work branch head "
                f"{rebinding['resolved_code_head']}" in rebinding["statement"]
            ), rebinding["statement"]

    def test_a_carrying_sync_without_a_measurable_generation_says_which(self) -> None:
        """F3: no generation, an ambiguous selection and unreadable manifests are three states.

        Each names a different fact about the generation store, and none of them is the reader's own
        ``unreadable``, which means the *rebinding artifact* could not be read. The block carries its
        own sentences and keeps the selection owner's sentence under its own key.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/landed.py", "VALUE = 'landed'"),
            )

            # (1) No generation at all: the leaf was never reviewed.
            none_yet = fixture.sync(memory_sync_choice="skip-memory")
            assert none_yet["ok"] is True, none_yet
            block = none_yet["review_rebinding"]
            assert block["state"] == "no-generation", block
            assert "no comparison generation is published" in block["detail"], block
            # This block's sentence is R22's; the generation owner's own sentence about a
            # final-output receipt travels under its own key rather than as this block's answer.
            assert "final output" not in block["detail"], block
            assert block["selection_state"] == "no-generation", block
            assert "final output" in block["selection_detail"], block

            # (2) Generation directories exist and none holds a readable manifest. The official line
            # moves again first, so this second sync carries a pair rather than reporting no movement.
            root = leaf_generation_root(fixture.contract.task_root, fixture.contract.leaf_id)
            (root / "stray-unreadable-generation").mkdir(parents=True, exist_ok=True)
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/again.py", "VALUE = 'again'"),
            )
            unreadable = fixture.sync(memory_sync_choice="skip-memory")
            assert unreadable["ok"] is True, unreadable
            block = unreadable["review_rebinding"]
            assert block["state"] == "generation-unreadable", block
            assert "none holds a readable manifest" in block["detail"], block
            assert block["selection_state"] == "unreadable", block
            assert block["selection_detail"] != block["detail"], block

    def test_the_reader_observes_the_location_after_its_own_reclamation(self) -> None:
        """F4: after its own discard, the reader reports an empty location, not a history claim.

        The reclamation owner removes the record, so an absence at the location has two causes -- a
        discard and a sync that never measured -- and the sentence must say which fact it observed
        rather than claim no sync ever measured the generation.
        """

        with tempfile.TemporaryDirectory() as tmp:
            fixture = ReviewSyncFixture(Path(tmp))
            fixture.commit_leaf_candidate()
            reviewed = fixture.freeze_review()
            fixture.publish_reviewed_candidate()
            fixture.on_official_line(
                fixture.code_repo,
                lambda: commit_file(fixture.code_repo, "src/landed.py", "VALUE = 'landed'"),
            )
            payload = fixture.sync(memory_sync_choice="skip-memory")
            assert payload["ok"] is True, payload

            before = read_review_sync_rebinding(
                fixture.contract.task_root, fixture.contract.leaf_id, reviewed.generation_id
            )
            assert before.state == "recorded", before

            removed = discard_review_sync_rebindings(
                fixture.contract.task_root, fixture.contract.leaf_id
            )
            assert [path.name for path in removed] == [
                rebinding_file_name(fixture.contract.leaf_id, reviewed.generation_id)
            ]

            after = read_review_sync_rebinding(
                fixture.contract.task_root, fixture.contract.leaf_id, reviewed.generation_id
            )
            assert after.state == "not-recorded", after
            assert reviewed.generation_id in after.detail, after.detail
            assert after.destination.as_posix() in after.detail, after.detail
            assert "no managed sync has measured" not in after.detail, after.detail
            assert "discarded" in after.detail, after.detail


def admitted_destination_identity(location) -> SnapshotIdentity | None:
    """What is admitted at the declared location before a publication: its dataset, or nothing yet.

    Read through the ordinary read route's own owner, so "the location holds no publication yet" is
    that route's answer rather than this helper's guess. A location holding something the route
    cannot read as a dataset of this repository admits nothing, and the publication owner then
    refuses the write -- which is the correct outcome for a destination nobody admitted.
    """

    resolved = resolve_published_intent(location.context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return None
    return SnapshotIdentity(
        repository_id=resolved.repository_id,
        schema_version=resolved.schema_version,
        logical_digest=resolved.logical_digest,
    )


def anchor_paths_of(database: Path) -> dict[str, str]:
    """Every recorded source anchor's path, keyed by its identity, read from the dataset itself."""

    connection = apsw.Connection(str(database))
    try:
        return {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT anchor_id, path FROM source_anchor")
        }
    finally:
        connection.close()


def assert_rebinding_measures_the_location(
    rebinding, reviewed, published, merged, database
) -> None:
    """The shared measurement assertions: the record names the resolved dataset and the judged one."""

    assert rebinding["state"] == "moved", rebinding
    assert rebinding["knowledge_match"] == "differs-from-reviewed-input", rebinding
    assert rebinding["resolved_knowledge"]["state"] == "published", rebinding
    assert rebinding["resolved_knowledge"]["dataset"]["logical_digest"] == (
        merged.logical_digest
    ), rebinding
    assert rebinding["reviewed_knowledge_logical_digest"] == published.logical_digest
    assert rebinding["read_back"] == "matched", rebinding
    assert dataset_identity(database).logical_digest == merged.logical_digest
    assert rebinding["generation_id"] == reviewed.generation_id


def stage_knowledge_divergence(fixture: ReviewSyncFixture, member: str):
    """Commit one authored knowledge line as both sides' ancestor, then diverge from it.

    The review is frozen while the line is the base and its candidate is published at the declared
    location, so the generation compares exactly the dataset both sides later move away from.
    Returns that generation, the published identity, and the two side heads the union joins.
    """

    worktree = fixture.memory_worktree
    base = fixture.build_knowledge_base()
    fixture.adopt_base_dataset(base)
    left = worktree.parent / "left.sqlite"
    right = worktree.parent / "right.sqlite"
    shutil.copyfile(base, left)
    shutil.copyfile(base, right)
    set_label(left, "the left side's own label", invariant_id=REVIEW_INVARIANT_ID)
    add_anchor(right, CONFLICT_ANCHOR_ID, "src/right_anchor.py")

    def commit_side(checkout: Path, state: Path, message: str) -> str:
        shutil.copyfile(state, checkout / member)
        git(checkout, "add", member)
        git(checkout, "commit", "-m", message)
        return git(checkout, "rev-parse", "HEAD")

    # The authored base lands on the official line and the ordinary sync carries it into the leaf's
    # own memory line: every later divergence descends from these bytes.
    base_head = fixture.on_official_line(
        fixture.memory_repo,
        lambda: commit_side(fixture.memory_repo, base, "the authored knowledge base"),
    )
    bootstrap = fixture.sync(memory_sync_choice="merge-memory")
    assert bootstrap["ok"] is True, bootstrap
    assert git(worktree, "rev-parse", "HEAD") == base_head
    assert dataset_identity(worktree / member) == dataset_identity(base)
    # The store's own resource lock is a derived file beside the dataset; recording the ignore rule
    # in the fixture keeps it out of the park/restore path, which would otherwise refuse to reapply
    # a file it must not overwrite.
    (worktree / ".gitignore").write_text(".knowledge.sqlite.lock" + NEWLINE, encoding="utf-8")
    git(worktree, "add", ".gitignore")
    git(worktree, "commit", "-m", "ignore the candidate store's own lock")

    reviewed = fixture.freeze_review()
    published = fixture.publish_reviewed_candidate()
    after = reviewed.knowledge_side("after")
    assert after.identity is not None
    assert after.identity.logical_digest == published.logical_digest
    left_head = commit_side(worktree, left, "the left side's own knowledge change")
    right_head = fixture.on_official_line(
        fixture.memory_repo,
        lambda: commit_side(fixture.memory_repo, right, "the right side's own knowledge change"),
    )
    return reviewed, published, left_head, right_head


def commit_file(repo: Path, name: str, content: str) -> None:
    """Write one committed file into a fixture repository."""

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
