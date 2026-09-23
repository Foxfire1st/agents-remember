"""Base-defect witness (no candidate code): drive the REAL production sync tool at the base commit.

Builds the same enclosure the leaf's cases build, freezes a real comparison generation through the
real freeze owner, moves the official source line, runs the real `worktree_sync` production tool, and
reports what the tool says about the leaf's review.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

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
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.memory.knowledge.closed_snapshot import freeze_closed_snapshot
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.publication import publish_prepared_snapshot
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import InvariantRequest, RevisionDraft, RevisionRequest
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.models.knowledge.snapshot import SnapshotDestinationRequest
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.worktree_contract import contract_publication_text
from read_scope_test_support import make_read_authorship
from test_knowledge_review_source_endpoints import build_endpoint_fixture

REVIEW_INVARIANT_ID = "0f4a4d5e-6b7c-4d8e-9f10-1a2b3c4d5e6f"
REVIEW_REVISION_ID = "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e"
CANDIDATE_ONLY_INVARIANT_ID = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def main(resolved: Path) -> None:
    from agents_remember.kernel.primitives import checkout_coordination

    checkout_coordination.declare_test_process()
    root = resolved / "fix"
    endpoint = build_endpoint_fixture(resolved / "endpoint", memory_mode="external")
    contract = endpoint.contract
    repository_id = contract.repo_name
    link = resolved / repository_id
    if not link.exists():
        link.symlink_to(contract.code_repo_path, target_is_directory=True)
    config_path = resolved / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": resolved.as_posix(),
                "retirement": {"autoLandOnIntegration": False},
                "repositories": {repository_id: {}},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    for repository in (contract.code_repo_path, contract.memory_repo_path):
        git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(repository, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    publish_new_lifecycle_operation_location(
        contract, contract_text=contract_publication_text(contract.contract_path, contract)
    )

    # The leaf's two review datasets, created through the store in its own namespace.
    halves = {}
    for half, invariants in (
        ("baseline", (REVIEW_INVARIANT_ID,)),
        ("candidate", (REVIEW_INVARIANT_ID, CANDIDATE_ONLY_INVARIANT_ID)),
    ):
        path = contract.worktree_group / REVIEW_CANDIDATE_RELATIVE_ROOT / half / CANDIDATE_DATABASE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        store = open_knowledge_store(path, repository_id)
        try:
            store.create_repository(
                RepositoryIdentity(repository_id=repository_id, authority_home=repository_id)
            )
            for invariant_id in invariants:
                store.create_invariant(
                    InvariantRequest(
                        repository_id=repository_id,
                        invariant_id=invariant_id,
                        display_label="the invariant the review compares",
                        provenance=make_read_authorship(),
                    )
                )
            store.create_revision(
                RevisionRequest(
                    repository_id=repository_id,
                    revision=RevisionDraft(
                        revision_id=REVIEW_REVISION_ID,
                        invariant_id=REVIEW_INVARIANT_ID,
                        display_version="v1",
                        statement="The reviewed invariant holds for this candidate.",
                        applicability="Every admitted candidate write in this repository namespace.",
                        exclusions=("Historical rows are not rewritten by a candidate write.",),
                        provenance=make_read_authorship(),
                    ),
                )
            )
        finally:
            store.close()
        halves[half] = path
    candidate_database = halves["candidate"]

    # Commit the leaf's candidate, freeze the review, publish the reviewed dataset.
    worktree = contract.code_worktree
    git(worktree, "add", "--all")
    git(worktree, "commit", "-m", "the leaf's committed candidate")
    manifest_outcome = freeze_review_comparison(
        config,
        ReviewSurfaceRequest(
            repository_id=repository_id,
            master=contract.task_root.name,
            leaf_id=contract.leaf_id,
            selector=InvariantIdentitySeed(invariant_id=REVIEW_INVARIANT_ID),
        ),
    )
    assert manifest_outcome.state == "published", manifest_outcome.refusal
    manifest = manifest_outcome.manifest
    location = declared_publication_location(contract)
    identity = dataset_identity(candidate_database)
    store = open_knowledge_store(candidate_database, identity.repository_id)
    stage = location.path.parent / ".review-publication-stage.sqlite"
    try:
        prepared = freeze_closed_snapshot(store, identity, stage)
    finally:
        store.close()
    resolved_intent = resolve_published_intent(location.context)
    admitted = (
        None
        if isinstance(resolved_intent, PublishedIntentUnavailable)
        else SnapshotIdentity(
            repository_id=resolved_intent.repository_id,
            schema_version=resolved_intent.schema_version,
            logical_digest=resolved_intent.logical_digest,
        )
    )
    published = publish_prepared_snapshot(
        prepared,
        SnapshotDestinationRequest(destination_path=location.path, expected_destination=admitted),
    )
    assert published.state in {"published", "no_change"}, published

    # The official source line moves, and the REAL production sync tool carries it in.
    previous = git(contract.code_repo_path, "symbolic-ref", "--short", "HEAD")
    git(contract.code_repo_path, "checkout", contract.code_source_branch)
    try:
        target = contract.code_repo_path / "src" / "landed.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("VALUE = 'landed'\n", encoding="utf-8")
        git(contract.code_repo_path, "add", "src/landed.py")
        git(contract.code_repo_path, "commit", "-m", "the official line moves")
    finally:
        git(contract.code_repo_path, "checkout", previous)

    payload = worktree_tools.worktree_sync_tool(
        config, contract_path=contract.contract_path.as_posix(), memory_sync_choice="skip-memory"
    )
    reloaded = load_config(config_path)
    del reloaded
    from agents_remember.worktrees.worktree_contract import load_contract

    after = capture_future_code_candidate(load_contract(contract.contract_path)).codeCandidateTree
    print("sync state              :", payload.get("state"), "| ok:", payload.get("ok"))
    print("review_rebinding key    :", "review_rebinding" in payload)
    print("review_rebinding block  :", json.dumps(payload.get("review_rebinding"), default=str))
    print("reviewed candidate tree :", manifest.source.candidate_code_tree_id)
    print("post-sync capture tree  :", after)
    print("capture moved           :", after != manifest.source.candidate_code_tree_id)
    print("deleted-worktree-fixed  :", shutil.which("git") is not None)
    print("sys.path[0]             :", Path(sys.path[0]).name)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
