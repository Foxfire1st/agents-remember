"""MIK-R170 rule 10: the closeout door accepts a route review after a clean sync.

The door's review check is driven here against a real leaf contract and candidate. The
plane stamps the code base a route review was recorded on; a later sync that changed no
reviewed path keeps the review accepted, and every other tree difference -- an overlapping
path, an extra change, a moved mode, an unrelated base -- is still refused with
``closeout-door-route-review-stale``, as is a record without the stamp.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from agents_remember.models.lifecycles.evidence_dependencies import canonical_sha256
from agents_remember.tasks import RouteReviewRecord, TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.integration.closeout.door_evidence import _review_provenance
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError
from agents_remember.worktrees.route_review import (
    build_route_review,
    code_candidate_tree,
    document_ref,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
)

NOW = datetime(2026, 10, 17, tzinfo=UTC)
STALE = "closeout-door-route-review-stale"
DEFAULT_REVIEWED = {"candidate.py": "VALUE = 1\n", "shared.py": "LEAF = 1\n"}
# Git keeps CR and LF in a pathname as distinct bytes; a text transport rewrites CR to LF and
# makes these two paths one. Both names are real files on a POSIX filesystem.
CR_PATH = "review\rname.py"
LF_PATH = "review\nname.py"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


@dataclass(frozen=True)
class LeafFixture:
    """A leaf contract whose candidate carries one reviewed change over its base."""

    contract: WorktreeContract
    document_path: Path
    review: RouteReviewRecord
    recorded_base: str


def _leaf_review(root: Path, *, reviewed: dict[str, str] | None = None) -> LeafFixture:
    code = root / "code"
    code.mkdir(parents=True)
    _git(code, "init", "-b", "main")
    _git(code, "config", "user.email", "agents-remember-tests@example.invalid")
    _git(code, "config", "user.name", "Agents Remember Tests")
    (code / "README.md").write_text("base\n", encoding="utf-8")
    _git(code, "add", "README.md")
    _git(code, "commit", "-m", "Initial commit")
    base = _git(code, "rev-parse", "HEAD")
    contract = default_contract(
        ContractTask(
            name="clean-sync-leaf",
            repo_name="repo",
            coordination_root=root / "coordination",
            workflow_kind="light-task",
            memory_mode="external",
        ),
        leaf=LeafIdentity(worktree_name="clean-sync-leaf", leaf_id="LEAF"),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="main",
            work_branch="ar/clean-sync-leaf",
            base_commit=base,
        ),
        memory=RepoBranchPlan(
            repo_path=root / "memory",
            source_branch="main",
            work_branch="ar/clean-sync-leaf",
            base_commit="b" * 40,
        ),
    )
    contract = replace(contract, code_worktree=code, worktree_group=root / "worktrees")
    report = contract.task_root / "notes" / "reports" / "review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Route review\n\nPass.\n", encoding="utf-8")
    for name, content in (reviewed or DEFAULT_REVIEWED).items():
        (code / name).write_text(content, encoding="utf-8")
    document = TaskDocument.model_validate(
        {
            "id": contract.leaf_id,
            "slug": "clean-sync-leaf",
            "title": "Clean sync leaf",
            "kind": "subTask",
            "repo": contract.repo_name,
            "createdAt": NOW.isoformat(),
            "steps": [{"id": "S1", "title": "Review the candidate"}],
        }
    )
    write_task_doc(contract.task_root, document)
    path = contract.task_root / "clean-sync-leaf.json"
    review = build_route_review(
        contract,
        ResolvedTaskDocument(ref=document_ref(contract, path), path=path, document=document),
        {
            "verdict": "pass",
            "verdictRef": "notes/reports/review.md",
            "routes": [
                {"route": "candidate", "verdict": "pass", "evidenceRef": "notes/reports/review.md"}
            ],
        },
        now=NOW,
    )
    write_task_doc(contract.task_root, document.model_copy(update={"routeReview": review}))
    return LeafFixture(contract, path, review, base)


def _refresh(fixture: LeafFixture, review: RouteReviewRecord) -> LeafFixture:
    """Record ``review`` as the leaf's current canonical record."""
    document = read_task_doc(fixture.document_path)
    write_task_doc(
        fixture.document_path.parent, document.model_copy(update={"routeReview": review})
    )
    return replace(fixture, review=review)


def _sync(fixture: LeafFixture, *, path: str, content: str) -> LeafFixture:
    """Move the source line one commit, as the supported sync adopts it."""
    repository = fixture.contract.code_repo_path
    (repository / path).write_text(content, encoding="utf-8")
    _git(repository, "add", path)
    _git(repository, "commit", "-m", f"Move the line: {path}")
    moved = replace(
        fixture.contract,
        code_base_commit=_git(repository, "rev-parse", "HEAD"),
    )
    return replace(fixture, contract=moved)


def _provenance(fixture: LeafFixture):
    """The door's own review check against the leaf's current candidate."""
    contract = fixture.contract
    document = read_task_doc(fixture.document_path)
    candidate = ResolvedTaskDocument(
        ref=document_ref(contract, fixture.document_path),
        path=fixture.document_path,
        document=document,
    )
    return _review_provenance(contract, candidate, code_candidate_tree(contract))


def _unrelated_base(fixture: LeafFixture) -> LeafFixture:
    """Rewrite the recorded base, so the recorded base is no commit of this line."""
    repository = fixture.contract.code_repo_path
    (repository / "README.md").write_text("rewritten\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "--amend", "-m", "Rewritten base")
    return replace(
        fixture,
        contract=replace(
            fixture.contract,
            code_base_commit=_git(repository, "rev-parse", "HEAD"),
        ),
    )


def _without_stamp(review: RouteReviewRecord) -> RouteReviewRecord:
    """The record as it was persisted before the base stamp existed, digest included."""
    payload = review.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload.pop("baseCommit", None)
    payload.pop("recordDigest", None)
    payload["recordDigest"] = canonical_sha256(payload)
    return RouteReviewRecord.model_validate(payload)


def test_the_plane_stamps_the_base_a_leaf_review_was_recorded_on(tmp_path: Path) -> None:
    fixture = _leaf_review(tmp_path)
    assert fixture.review.baseCommit == fixture.recorded_base


def test_the_door_accepts_a_route_review_after_a_clean_sync(tmp_path: Path) -> None:
    fixture = _sync(_leaf_review(tmp_path), path="incoming.py", content="INCOMING = 1\n")
    provenance = _provenance(fixture)
    assert provenance.state == "proven"
    assert provenance.fingerprint == fixture.review.recordDigest


def test_the_door_refuses_a_sync_that_changed_a_reviewed_path(tmp_path: Path) -> None:
    """A path the leaf changed and the line changed is repaired, never waved through."""

    fixture = _sync(_leaf_review(tmp_path), path="shared.py", content="LINE = 1\n")
    # The sync's merge resolution keeps both sides on the shared path: the candidate tree
    # differs from the reviewed one, and the reviewed path is one the line also changed.
    (fixture.contract.code_worktree / "shared.py").write_text(
        "LINE = 1\nLEAF = 1\n", encoding="utf-8"
    )
    with pytest.raises(CloseoutQueueError) as refused:
        _provenance(fixture)
    assert refused.value.status == STALE


def test_the_door_refuses_any_other_tree_difference(tmp_path: Path) -> None:
    extra = _sync(_leaf_review(tmp_path), path="incoming.py", content="INCOMING = 1\n")
    (extra.contract.code_worktree / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
    with pytest.raises(CloseoutQueueError) as refused:
        _provenance(extra)
    assert refused.value.status == STALE

    # The same path and bytes are not enough: the reviewed mode must still be the mode.
    mode_root = tmp_path / "mode"
    mode_root.mkdir()
    moved = _sync(_leaf_review(mode_root), path="incoming.py", content="INCOMING = 1\n")
    (moved.contract.code_worktree / "candidate.py").chmod(0o755)
    with pytest.raises(CloseoutQueueError) as refused_mode:
        _provenance(moved)
    assert refused_mode.value.status == STALE


def test_the_door_refuses_a_review_recorded_on_an_unrelated_base(tmp_path: Path) -> None:
    fixture = _unrelated_base(_leaf_review(tmp_path))
    with pytest.raises(CloseoutQueueError) as refused:
        _provenance(fixture)
    assert refused.value.status == STALE


def test_a_route_review_without_a_stamped_base_is_refused_as_today(tmp_path: Path) -> None:
    fixture = _leaf_review(tmp_path)
    legacy = _refresh(fixture, _without_stamp(fixture.review))
    assert legacy.review.baseCommit is None
    synced = _sync(legacy, path="incoming.py", content="INCOMING = 1\n")
    with pytest.raises(CloseoutQueueError) as refused:
        _provenance(synced)
    assert refused.value.status == STALE


def test_a_cr_path_replaced_by_an_lf_path_is_not_the_reviewed_change(tmp_path: Path) -> None:
    """L170-R3-F01: the reviewed pathname's exact bytes are part of the reviewed change."""

    fixture = _sync(
        _leaf_review(tmp_path, reviewed={CR_PATH: "X = 1\n"}),
        path="incoming.py",
        content="INCOMING = 1\n",
    )
    worktree = fixture.contract.code_worktree
    (worktree / CR_PATH).unlink()
    (worktree / LF_PATH).write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(CloseoutQueueError) as refused:
        _provenance(fixture)
    assert refused.value.status == STALE


def test_a_disjoint_cr_versus_lf_incoming_path_does_not_falsely_overlap(tmp_path: Path) -> None:
    """A CR path the review changed and an LF path the line added are two paths, not one."""

    fixture = _sync(
        _leaf_review(tmp_path, reviewed={CR_PATH: "X = 1\n"}),
        path=LF_PATH,
        content="Y = 2\n",
    )
    provenance = _provenance(fixture)
    assert provenance.state == "proven"
    assert provenance.fingerprint == fixture.review.recordDigest


def test_two_paths_in_one_diff_keep_their_own_entries(tmp_path: Path) -> None:
    """A same-diff CR/LF pair must not collapse so that one key overwrites the other."""

    fixture = _sync(
        _leaf_review(tmp_path, reviewed={CR_PATH: "X = 1\n", LF_PATH: "X = 1\n"}),
        path="incoming.py",
        content="INCOMING = 1\n",
    )
    # Only one of the two reviewed pathnames survives in the candidate: the exact comparison
    # must see a missing path, while a collapsing text observation would see the same one map.
    (fixture.contract.code_worktree / CR_PATH).unlink()
    with pytest.raises(CloseoutQueueError) as refused:
        _provenance(fixture)
    assert refused.value.status == STALE
