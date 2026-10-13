"""MIK-R82 rule 2: copies under the notes of a finished task are deleted, the open task's are not.

``agents-remember knowledge-copies`` gains one rule: a copy under the notes of a task whose
document (``task.json``) says ``Completed`` or ``abandoned`` is deleted under the rule
``completed-task-notes`` -- dry run, reviewed apply via ``--reviewed``/``reviewDigest``, and the
same report shape the command already uses. A copy that is a comparison generation's retained
snapshot is released through its deletion owner
(``discard_comparison_snapshots``), so reopening that generation says its history was deleted
and why. The task's review refs and retained-code refs stay until its archive: the copies
command never touches refs. A task with no document, or a live status, keeps the old rule:
its copies stay for the archive hook.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import apsw
from agents_remember.application.legacy_dataset_copies import plan_cleanup, run_cleanup
from agents_remember.application.review_comparison_generation import (
    _UNSEALED_FIELDS,
    COMPARISON_GENERATION_VERSION,
    ComparisonGenerationManifest,
    ComparisonKnowledgeBinding,
    ComparisonPublicationLineage,
    ComparisonRecordBinding,
    ComparisonScopeBinding,
    ComparisonSourceBinding,
    generation_directory,
    generation_identity,
    read_history_deletion,
)
from agents_remember.application.review_comparison_reclamation import SNAPSHOT_DELETION_OWNER
from agents_remember.application.review_comparison_reopen import reopen_comparison_generation
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from knowledge_rows_test_support import open_knowledge_store

REPO = "proj"
OPEN_TASK = "260101_live-task"
DONE_TASK = "260102_done-task"
LEFT_TASK = "260103_left-task"
GONE_TASK = "260104_gone-task"
LEAF = "260102-DONE-L1"
OPEN_SNAPSHOT = (
    f"tasks/{REPO}/{OPEN_TASK}/notes/comparison-generations/l1/g1/knowledge/before/snapshot.sqlite"
)
DONE_SCRATCH = f"tasks/{REPO}/{DONE_TASK}/notes/scratch/pub2.sqlite"
GONE_COPY = (
    f"tasks/{REPO}/{GONE_TASK}/notes/comparison-generations/l1/g1/knowledge/before/snapshot.sqlite"
)
LEFT_COPY = (
    f"tasks/{REPO}/{LEFT_TASK}/notes/comparison-generations/l1/g1/knowledge/before/snapshot.sqlite"
)


def _dataset(path: Path) -> bytes:
    """One SQLite file holding the knowledge schema; return the bytes the manifest seals.

    MIK-R26 rule 6: the file is built under a working name and copied into place --
    the derived index never opens or writes a retired dataset name, so a
    retired-named copy cannot be authored through the store. The copy keeps only
    the logical tables: a file carrying the index's own ``ix_*`` tables reads as a
    derived index, not as a leftover copy. The same shape
    ``test_knowledge_copy_cleanup`` builds.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ar-copies-template-") as temporary:
        template = Path(temporary) / "template.sqlite"
        open_knowledge_store(template, "11111111-1111-4111-8111-111111111111").close()
        shutil.copy(template, path)
    connection = apsw.Connection(str(path))
    try:
        for table in (
            "ix_meta",
            "ix_problem",
            "ix_record",
            "ix_entry",
            "ix_member",
            "ix_route",
            "ix_link",
            "ix_history_row",
            "ix_uuid",
        ):
            connection.execute(f"DROP TABLE {table}")
    finally:
        connection.close()
    return path.read_bytes()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _manifest(task_root: Path, snapshots: dict[str, bytes]) -> ComparisonGenerationManifest:
    """Seal one committed-history generation whose retained snapshots are the given bytes."""

    identity = {
        "repository_id": "00000000-0000-4000-8000-000000000001",
        "schema_version": "legacy",
        "logical_digest": "0" * 64,
    }
    knowledge = [
        {
            "side": side,
            "state": "retained",
            "identity": identity,
            "artifact": {
                "relative_path": f"knowledge/{side}/snapshot.sqlite",
                "sha256": hashlib.sha256(snapshots[side]).hexdigest(),
                "byte_count": len(snapshots[side]),
                "deletion_owner": SNAPSHOT_DELETION_OWNER,
                "cleanup_scope": f"knowledge/{side}/snapshot.sqlite",
            },
        }
        for side in ("before", "after")
    ]
    body: dict[str, Any] = {
        "manifest_version": COMPARISON_GENERATION_VERSION,
        "generation_index": 1,
        "recorded_at": "2026-01-01T00:00:00Z",
        "repository_id": REPO,
        "master": DONE_TASK,
        "leaf_id": LEAF,
        "task_root": str(task_root),
        "temporary_storage_scope": "notes/reports/comparison-generations",
        "contract_path": str(task_root / "contract.json"),
        "source": ComparisonSourceBinding.model_validate(
            {
                "code_repository_root": str(task_root.parent / "code"),
                "baseline_code_tree_id": "a" * 40,
                "candidate_code_tree_id": "b" * 40,
                "candidate_capture": {
                    "observedCodeHead": "a" * 40,
                    "codeBaseCommit": "a" * 40,
                    "codeCandidateTree": "b" * 40,
                },
                "custody": "committed-history",
                "custody_refs": [],
                "custody_commits": [],
            }
        ).model_dump(mode="json"),
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


def _generation(task_root: Path, staging: Path) -> tuple[Path, str]:
    """Publish one generation whose before/after snapshots are real knowledge datasets."""

    staged = {side: _dataset(staging / f"{side}.sqlite") for side in ("before", "after")}
    manifest = _manifest(task_root, staged)
    directory = generation_directory(task_root, LEAF, manifest.generation_id)
    for side in ("before", "after"):
        target = directory / f"knowledge/{side}/snapshot.sqlite"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(staged[side])
    (directory / "manifest.json").write_bytes(manifest.manifest_bytes())
    return directory, manifest.generation_id


def _task(root: Path, name: str, status: str | None) -> Path:
    task_root = root / "tasks" / REPO / name
    task_root.mkdir(parents=True, exist_ok=True)
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    if status is not None:
        (task_root / "task.json").write_text(
            json.dumps({"id": name.upper(), "status": status}), encoding="utf-8"
        )
    return task_root


def coordination_root(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "coordination"
    live = _task(root, OPEN_TASK, "inProgress")
    _dataset(live / "notes/comparison-generations/l1/g1/knowledge/before/snapshot.sqlite")
    done = _task(root, DONE_TASK, "Completed")
    _dataset(done / "notes/scratch/pub2.sqlite")
    _, generation_id = _generation(done, tmp_path / "staging")
    _task(root, GONE_TASK, "abandoned")
    gone = root / "tasks" / REPO / GONE_TASK
    _dataset(gone / "notes/comparison-generations/l1/g1/knowledge/before/snapshot.sqlite")
    _task(root, LEFT_TASK, None)
    left = root / "tasks" / REPO / LEFT_TASK
    _dataset(left / "notes/comparison-generations/l1/g1/knowledge/before/snapshot.sqlite")
    for name in ("code", "memory"):
        repository = root / name
        repository.mkdir(parents=True, exist_ok=True)
        _git(repository, "init", "-q", "-b", "main")
        _git(repository, "config", "user.email", "fixture@example.invalid")
        _git(repository, "config", "user.name", "rule two fixture")
        (repository / "file.txt").write_text("x\n", encoding="utf-8")
        _git(repository, "add", "-A")
        _git(repository, "commit", "-q", "-m", "fixture")
    return root, generation_id


def _refs(repository: Path) -> dict[str, str]:
    listed = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname) %(objectname)", "refs/ar/"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return dict(line.split(" ", 1) for line in listed.stdout.splitlines() if line)


def _pin(root: Path) -> dict[str, dict[str, str]]:
    before = {}
    for name in ("code", "memory"):
        repository = root / name
        head = subprocess.run(
            ["git", "rev-parse", "HEAD" if name == "code" else "HEAD^{tree}"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        review = f"refs/ar/review/{DONE_TASK}/{LEAF}/1"
        pin = "refs/ar/retained-code/260102-done-l1/g1"
        subprocess.run(["git", "update-ref", review, head], cwd=repository, check=True)
        if name == "code":
            subprocess.run(["git", "update-ref", pin, head], cwd=repository, check=True)
        before[name] = _refs(repository)
    return before


def test_the_dry_run_deletes_finished_tasks_and_keeps_the_open_one(tmp_path: Path) -> None:
    root, _ = coordination_root(tmp_path)
    pinned = _pin(root)
    before = {path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts}

    document = plan_cleanup(root)

    rules = {copy["path"]: copy["rule"] for copy in document["copies"]}
    snapshots = [path for path in rules if path.endswith("/snapshot.sqlite")]
    assert rules[OPEN_SNAPSHOT] == "unarchived-task-notes"
    assert rules[DONE_SCRATCH] == "completed-task-notes"
    assert "the task document says Completed" in next(
        copy["ruleText"] for copy in document["copies"] if copy["path"] == DONE_SCRATCH
    )
    assert rules[GONE_COPY] == "completed-task-notes"
    assert rules[LEFT_COPY] == "unarchived-task-notes"
    done_snapshots = [path for path in snapshots if DONE_TASK in path]
    assert len(done_snapshots) == 2
    assert all(rules[path] == "completed-task-notes" for path in done_snapshots)
    assert all(
        "discard_comparison_snapshots" in copy["ruleText"]
        for copy in document["copies"]
        if copy["path"] in done_snapshots
    )
    assert set(document["wouldDelete"]) == {
        path for path, rule in rules.items() if rule == "completed-task-notes"
    }
    assert OPEN_SNAPSHOT not in document["wouldDelete"] and LEFT_COPY not in document["wouldDelete"]
    assert document["mode"] == "dry-run" and document["deleted"] == []
    assert {
        path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts
    } == before
    assert _refs(root / "code") == pinned["code"] and _refs(root / "memory") == pinned["memory"]


def test_the_reviewed_apply_releases_snapshots_through_their_owner(tmp_path: Path) -> None:
    root, generation_id = coordination_root(tmp_path)
    pinned = _pin(root)
    done = root / "tasks" / REPO / DONE_TASK

    applied = run_cleanup(root, str(plan_cleanup(root)["reviewDigest"]))

    rules = {copy["path"]: copy["rule"] for copy in applied["copies"]}
    done_snapshots = [path for path in rules if DONE_TASK in path and path.endswith(".sqlite")]
    assert set(applied["deleted"]) == {
        path for path, rule in rules.items() if rule == "completed-task-notes"
    }
    assert applied["failures"] == []
    assert not (done / "notes/scratch/pub2.sqlite").exists()
    assert all(not (root / path).exists() for path in done_snapshots)
    assert (root / OPEN_SNAPSHOT).is_file() and (root / LEFT_COPY).is_file()
    for target in ("knowledge-before", "knowledge-after"):
        record = read_history_deletion(done, LEAF, generation_id, target)
        assert record is not None
        assert record.deletion_owner == SNAPSHOT_DELETION_OWNER
        assert "Completed" in record.reason
    config = McpRuntimeConfig(
        workspace_root=tmp_path,
        coordination_root=root,
        config_path=tmp_path / "config.json",
        transcript_root=tmp_path / "transcripts",
    )
    reopened = reopen_comparison_generation(config, REPO, DONE_TASK, LEAF)
    assert reopened.generation is not None and reopened.generation.generation_id == generation_id
    assert {channel.state for channel in reopened.knowledge} == {"unavailable-history"}
    for channel in reopened.knowledge:
        assert (
            "was deleted explicitly" in channel.detail and SNAPSHOT_DELETION_OWNER in channel.detail
        )
    assert _refs(root / "code") == pinned["code"] and _refs(root / "memory") == pinned["memory"]


def test_a_second_apply_after_the_first_deletes_nothing_new(tmp_path: Path) -> None:
    root, _ = coordination_root(tmp_path)

    first = run_cleanup(root, str(plan_cleanup(root)["reviewDigest"]))

    assert DONE_SCRATCH in first["deleted"] and GONE_COPY in first["deleted"]
    assert OPEN_SNAPSHOT not in first["deleted"] and LEFT_COPY not in first["deleted"]
    again = run_cleanup(root, str(plan_cleanup(root)["reviewDigest"]))
    assert again["deleted"] == [] and again["failures"] == []
    assert again["after"] == first["after"]
    assert (root / OPEN_SNAPSHOT).is_file()
