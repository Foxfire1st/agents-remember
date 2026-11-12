"""The archive hook's two deletion owners, driven over real Git refs and real snapshot files.

``test_review_artifact_cleanup.py`` replaces ``release_comparison_code_object`` and
``discard_comparison_snapshots`` with mocks, so nothing there proves that a retention ref is
deleted only while it still names the commit it recorded, or that a snapshot is removed only while
its bytes are the ones the generation froze. These cases run both owners on a scratch Git
repository and a generation directory under a scratch task root. The retained snapshot files hold
plain bytes: no database file is created or opened anywhere.

Every case asserts what is deleted, what is kept, and what the unavailable-history record says.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.review_comparison_generation import (
    _UNSEALED_FIELDS,
    COMPARISON_GENERATION_VERSION,
    ComparisonGenerationManifest,
    ComparisonKnowledgeBinding,
    ComparisonPublicationLineage,
    ComparisonRecordBinding,
    ComparisonScopeBinding,
    ComparisonSourceBinding,
    deletion_record_path,
    generation_directory,
    generation_identity,
    read_history_deletion,
    read_manifest,
)
from agents_remember.application.review_comparison_reclamation import (
    CODE_OBJECT_DELETION_OWNER,
    SNAPSHOT_DELETION_OWNER,
    discard_comparison_snapshots,
    release_comparison_code_object,
)
from agents_remember.errors import CodeObjectRetentionError, ComparisonReclamationError
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.worktrees.modules.code_object_retention import (
    RETAINED_CODE_REF_NAMESPACE,
    RetainedCodeObject,
)

pytestmark = pytest.mark.evidence_unit

LEAF = "260101-REC-L1"
SIDES = ("before", "after")


def git(root: Path, *args: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def retention_ref(leaf_id: str, generation_id: str) -> str:
    return f"{RETAINED_CODE_REF_NAMESPACE}/{leaf_id}/{generation_id}"


def retain_code_object(
    code: Path, *, ref: str, tree: str, base_commit: str, message: str
) -> RetainedCodeObject:
    """Write a pin the way a freeze once did: one commit of the tree on the base, and one ref.

    Production no longer creates pins; the archive hook only releases the pins that historical
    generation records name, so the fixture writes one directly.
    """
    commit = git(code, "commit-tree", tree, "-p", base_commit, "-m", message)
    git(code, "update-ref", ref, commit)
    return RetainedCodeObject(ref=ref, commit=commit, tree=tree, base_commit=base_commit)


def _ref(repository: Path, ref: str) -> str | None:
    found = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    return found.stdout.strip() if found.returncode == 0 else None


@dataclass
class Scratch:
    """One code repository, one task root, and the pin and snapshots of one generation."""

    code: Path
    task_root: Path
    base: str
    tree: str
    ref: str
    generation_id: str

    def directory(self) -> Path:
        return generation_directory(self.task_root, LEAF, self.generation_id)

    def record(self, target: str):
        return read_history_deletion(self.task_root, LEAF, self.generation_id, target)


def _candidate_tree(code: Path) -> str:
    """A tree that no commit holds, written the way the capture owner writes one."""
    blob = git(code, "hash-object", "-w", "--stdin", stdin="CANDIDATE = 1\n")
    return git(code, "mktree", stdin=f"100644 blob {blob}\tcandidate.py\n")


def _manifest(
    scratch: Scratch,
    *,
    pinned: bool,
    custody_refs: tuple[str, ...],
    snapshots: dict[str, bytes],
) -> ComparisonGenerationManifest:
    """Seal one generation record whose pin and retained snapshots are the given real ones."""
    source: dict[str, Any] = {
        "code_repository_root": str(scratch.code),
        "baseline_code_tree_id": scratch.base,
        "candidate_code_tree_id": scratch.tree,
        "candidate_capture": {
            "observedCodeHead": scratch.base,
            "codeBaseCommit": scratch.base,
            "codeCandidateTree": scratch.tree,
        },
        "custody": "retained" if pinned else "committed-history",
        "custody_refs": list(custody_refs),
        "custody_commits": [],
    }
    if pinned:
        retained = retain_code_object(
            scratch.code,
            ref=scratch.ref,
            tree=scratch.tree,
            base_commit=scratch.base,
            message="pin",
        )
        source["retained"] = retained.model_dump()
        source["deletion_owner"] = CODE_OBJECT_DELETION_OWNER
        source["cleanup_scope"] = scratch.ref
    identity = {
        "repository_id": "00000000-0000-4000-8000-000000000001",
        "schema_version": "legacy",
        "logical_digest": "0" * 64,
    }
    knowledge: list[dict[str, Any]] = []
    for side in SIDES:
        if side in snapshots:
            data = snapshots[side]
            knowledge.append(
                {
                    "side": side,
                    "state": "retained",
                    "identity": identity,
                    "artifact": {
                        "relative_path": f"knowledge/{side}/copy.bin",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "byte_count": len(data),
                        "deletion_owner": SNAPSHOT_DELETION_OWNER,
                        "cleanup_scope": f"knowledge/{side}/copy.bin",
                    },
                }
            )
        else:
            knowledge.append({"side": side, "state": "not-recorded", "reason": "never recorded"})
    body: dict[str, Any] = {
        "manifest_version": COMPARISON_GENERATION_VERSION,
        "generation_index": 1,
        "recorded_at": "2026-01-01T00:00:00Z",
        "repository_id": "agents-remember",
        "master": "260101_reclamation",
        "leaf_id": LEAF,
        "task_root": str(scratch.task_root),
        "temporary_storage_scope": "notes/reports/comparison-generations",
        "contract_path": str(scratch.task_root / "contract.json"),
        "source": ComparisonSourceBinding.model_validate(source).model_dump(mode="json"),
        "knowledge": [
            ComparisonKnowledgeBinding.model_validate(item).model_dump(mode="json")
            for item in knowledge
        ],
        "scope": ComparisonScopeBinding.model_validate(
            {
                "selected": "task-context",
                "inventory_state": "unavailable",
                "inventory_digest": hashlib.sha256(b"unavailable").hexdigest(),
                "changed_path_count": 0,
                "inventory_partial": True,
                "detail": "This fixture records no measured inventory.",
            }
        ).model_dump(mode="json"),
        "records": ComparisonRecordBinding.model_validate(
            {
                "state": "not-supplied",
                "assessments": 0,
                "signals": 0,
                "observations": 0,
                "current_measured": False,
                "collection_digest": hashlib.sha256(b"{}").hexdigest(),
                "detail": "No record collection was captured.",
            }
        ).model_dump(mode="json"),
        "evidence": [],
        "policies": [],
        "lineage": ComparisonPublicationLineage().model_dump(mode="json"),
    }
    body["binding_digest"] = sha256_digest(
        {key: value for key, value in body.items() if key not in _UNSEALED_FIELDS}
    )
    body["generation_id"] = generation_identity(body["binding_digest"])
    return ComparisonGenerationManifest.model_validate(body)


def build(
    tmp_path: Path,
    *,
    pinned: bool = True,
    custody_refs: tuple[str, ...] = (),
    snapshots: dict[str, bytes] | None = None,
) -> Scratch:
    """A real repository with a pin, and a published generation directory that records it."""
    code = tmp_path / "code"
    code.mkdir(parents=True)
    git(code, "init", "-q", "-b", "main")
    git(code, "config", "user.email", "fixture@example.invalid")
    git(code, "config", "user.name", "Fixture")
    (code / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-q", "-m", "base")
    base = git(code, "rev-parse", "HEAD")
    tree = _candidate_tree(code)
    task_root = tmp_path / "coordination" / "tasks" / "agents-remember" / "260101_reclamation"
    task_root.mkdir(parents=True)
    scratch = Scratch(code, task_root, base, tree, retention_ref(LEAF, "g1"), "")
    manifest = _manifest(
        scratch, pinned=pinned, custody_refs=custody_refs, snapshots=snapshots or {}
    )
    scratch.generation_id = manifest.generation_id
    directory = scratch.directory()
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(manifest.manifest_bytes())
    for side, data in (snapshots or {}).items():
        target = directory / f"knowledge/{side}/copy.bin"
        target.parent.mkdir(parents=True)
        target.write_bytes(data)
    return scratch


def test_a_release_deletes_only_its_own_ref_and_records_the_custody_it_measured(
    tmp_path: Path,
) -> None:
    scratch = build(tmp_path)
    sibling = retain_code_object(
        scratch.code,
        ref=retention_ref(LEAF, "g2"),
        tree=scratch.tree,
        base_commit=scratch.base,
        message="a second generation's own pin",
    )
    pinned_commit = _ref(scratch.code, scratch.ref)
    manifest_bytes = (scratch.directory() / "manifest.json").read_bytes()

    deletion = release_comparison_code_object(
        scratch.task_root,
        LEAF,
        scratch.generation_id,
        reason="the task was archived",
        recorded_at="2026-02-02T00:00:00Z",
    )

    assert pinned_commit is not None
    assert _ref(scratch.code, scratch.ref) is None
    assert _ref(scratch.code, sibling.ref) == sibling.commit
    assert _ref(scratch.code, "refs/heads/main") == scratch.base
    assert deletion.target == "code-object"
    assert deletion.deletion_owner == CODE_OBJECT_DELETION_OWNER
    assert deletion.cleanup_scope == scratch.ref
    assert deletion.released_custody == "retained"
    stored = scratch.record("code-object")
    assert stored == deletion
    assert stored is not None
    assert stored.reason == "the task was archived"
    assert (scratch.directory() / "manifest.json").read_bytes() == manifest_bytes
    assert (
        read_manifest(scratch.directory() / "manifest.json").generation_id == scratch.generation_id
    )


def test_a_release_reports_committed_history_once_a_named_branch_holds_the_tree(
    tmp_path: Path,
) -> None:
    scratch = build(tmp_path, custody_refs=("refs/heads/main",))
    # The tree is later landed on the durable branch the manifest named.
    git(scratch.code, "read-tree", scratch.tree)
    git(scratch.code, "commit", "-q", "-m", "land the candidate")
    assert git(scratch.code, "rev-parse", "HEAD^{tree}") == scratch.tree

    deletion = release_comparison_code_object(
        scratch.task_root, LEAF, scratch.generation_id, reason="landed"
    )

    assert deletion.released_custody == "committed-history"
    assert _ref(scratch.code, scratch.ref) is None
    assert _ref(scratch.code, "refs/heads/main") is not None


def test_a_ref_that_moved_is_never_deleted_and_no_record_is_left(tmp_path: Path) -> None:
    scratch = build(tmp_path)
    git(scratch.code, "update-ref", scratch.ref, scratch.base)

    with pytest.raises(CodeObjectRetentionError) as refused:
        release_comparison_code_object(
            scratch.task_root, LEAF, scratch.generation_id, reason="archive"
        )

    assert refused.value.status == "code-object-ref-moved"
    assert _ref(scratch.code, scratch.ref) == scratch.base
    assert not deletion_record_path(
        scratch.task_root, LEAF, scratch.generation_id, "code-object"
    ).exists()


def test_a_release_with_no_pin_refuses_and_an_already_released_pin_converges(
    tmp_path: Path,
) -> None:
    unpinned = build(tmp_path / "unpinned", pinned=False)
    with pytest.raises(CodeObjectRetentionError) as refused:
        release_comparison_code_object(
            unpinned.task_root, LEAF, unpinned.generation_id, reason="archive"
        )
    assert refused.value.status == "code-object-ref-absent"
    assert unpinned.record("code-object") is None

    released = build(tmp_path / "released")
    git(released.code, "update-ref", "-d", released.ref)
    deletion = release_comparison_code_object(
        released.task_root, LEAF, released.generation_id, reason="retry"
    )
    assert deletion.target == "code-object"
    assert released.record("code-object") == deletion
    assert _ref(released.code, released.ref) is None


def test_a_discard_removes_exactly_the_recorded_files_and_records_the_measured_digests(
    tmp_path: Path,
) -> None:
    before, after = b"snapshot before\n", b"snapshot after\n"
    scratch = build(tmp_path, snapshots={"before": before, "after": after})
    neighbour = scratch.directory() / "knowledge" / "before" / "neighbour.txt"
    neighbour.write_text("not recorded by the manifest\n", encoding="utf-8")
    other_generation = generation_directory(scratch.task_root, LEAF, "other") / "copy.bin"
    other_generation.parent.mkdir(parents=True)
    other_generation.write_bytes(before)
    manifest_bytes = (scratch.directory() / "manifest.json").read_bytes()

    deletions = discard_comparison_snapshots(
        scratch.task_root,
        LEAF,
        scratch.generation_id,
        reason="archive",
        recorded_at="2026-02-02T00:00:00Z",
    )

    assert [item.target for item in deletions] == ["knowledge-before", "knowledge-after"]
    assert [item.deleted_digest for item in deletions] == [
        hashlib.sha256(before).hexdigest(),
        hashlib.sha256(after).hexdigest(),
    ]
    assert {item.deletion_owner for item in deletions} == {SNAPSHOT_DELETION_OWNER}
    assert not (scratch.directory() / "knowledge/before/copy.bin").exists()
    assert not (scratch.directory() / "knowledge/after/copy.bin").exists()
    assert neighbour.exists()
    assert other_generation.read_bytes() == before
    assert (scratch.directory() / "manifest.json").read_bytes() == manifest_bytes
    assert scratch.record("knowledge-before") == deletions[0]
    assert scratch.record("knowledge-after") == deletions[1]


def test_a_changed_snapshot_is_kept_and_unrecorded_and_a_retry_converges(tmp_path: Path) -> None:
    scratch = build(tmp_path, snapshots={"before": b"one\n", "after": b"two\n"})
    changed = scratch.directory() / "knowledge/after/copy.bin"
    changed.write_bytes(b"edited behind the generation\n")

    with pytest.raises(ComparisonReclamationError) as refused:
        discard_comparison_snapshots(
            scratch.task_root, LEAF, scratch.generation_id, reason="archive"
        )

    assert refused.value.status == "snapshot-bytes-mismatch"
    assert changed.read_bytes() == b"edited behind the generation\n"
    assert scratch.record("knowledge-after") is None
    # The half that matched was already removed and recorded: the honest partial state.
    assert not (scratch.directory() / "knowledge/before/copy.bin").exists()
    before_record = scratch.record("knowledge-before")
    assert before_record is not None
    assert before_record.deleted_digest == hashlib.sha256(b"one\n").hexdigest()

    changed.write_bytes(b"two\n")
    retried = discard_comparison_snapshots(
        scratch.task_root, LEAF, scratch.generation_id, reason="archive again"
    )

    assert [item.target for item in retried] == ["knowledge-before", "knowledge-after"]
    # The already-removed half is recorded as found: no digest, no failure.
    assert retried[0].deleted_digest is None
    assert retried[1].deleted_digest == hashlib.sha256(b"two\n").hexdigest()
    assert not changed.exists()


def test_a_discard_skips_a_side_the_generation_never_retained(tmp_path: Path) -> None:
    scratch = build(tmp_path, snapshots={"after": b"only after\n"})

    deletions = discard_comparison_snapshots(
        scratch.task_root, LEAF, scratch.generation_id, reason="archive"
    )

    assert [item.target for item in deletions] == ["knowledge-after"]
    assert scratch.record("knowledge-before") is None
    assert (
        json.loads(
            deletion_record_path(
                scratch.task_root, LEAF, scratch.generation_id, "knowledge-after"
            ).read_text(encoding="utf-8")
        )["cleanup_scope"]
        == "knowledge/after/copy.bin"
    )
