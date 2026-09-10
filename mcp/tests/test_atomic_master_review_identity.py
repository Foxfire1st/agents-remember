"""CCR-R26 identity compatibility and atomic-master intent projections."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.tasks import RouteReviewRecord, TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees import route_review_scope
from agents_remember.worktrees.route_review import (
    build_route_review,
    document_ref,
)
from agents_remember.worktrees.route_review_scope import (
    require_current_route_review,
    resolve_atomic_master_scope,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    default_series_contract,
    write_contract,
)

BASELINE_ROUTE_REVIEW_COMMIT = "8133b6a9"
NOW = "2026-09-08T00:00:00+00:00"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "agents-remember-tests@example.invalid")
    _git(repo, "config", "user.name", "Agents Remember Tests")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Initial commit")
    return _git(repo, "rev-parse", "HEAD")


def _leaf_document(contract: WorktreeContract) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": contract.leaf_id,
            "slug": "legacy-leaf",
            "title": "Legacy leaf",
            "kind": "subTask",
            "repo": contract.repo_name,
            "createdAt": NOW,
            "objective": "Preserve the exact leaf review identity.",
            "requirements": ["The unchanged leaf review remains current."],
            "steps": [
                {
                    "id": "S1",
                    "title": "Review the candidate",
                    "outcome": "The candidate is reviewed.",
                }
            ],
        }
    )


def _legacy_leaf_fixture(
    root: Path,
) -> tuple[WorktreeContract, TaskDocument, Path, dict[str, object]]:
    code_repo = root / "code"
    base_commit = _init_repo(code_repo)
    coordination = root / "coordination"
    contract = default_contract(
        ContractTask(
            name="legacy-leaf",
            repo_name="repo",
            coordination_root=coordination,
            workflow_kind="light-task",
            memory_mode="internal",
        ),
        leaf=LeafIdentity(worktree_name="legacy-leaf"),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="main",
            work_branch="ar/legacy-leaf",
            base_commit=base_commit,
        ),
    )
    contract = replace(
        contract,
        code_worktree=code_repo,
        worktree_group=root / "worktrees",
    )
    report = contract.task_root / "notes" / "reports" / "legacy-review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Baseline route review\n\nPass.\n", encoding="utf-8")
    (code_repo / "candidate.py").write_text("VALUE = 1\n", encoding="utf-8")

    document = _leaf_document(contract)
    write_task_doc(contract.task_root, document)
    document_path = contract.task_root / "legacy-leaf.json"
    review = build_route_review(
        contract,
        ResolvedTaskDocument(
            ref=document_ref(contract, document_path),
            path=document_path,
            document=document,
        ),
        {
            "verdict": "pass",
            "verdictRef": "notes/reports/legacy-review.md",
            "routes": [
                {
                    "route": "legacy-leaf",
                    "verdict": "pass",
                    "evidenceRef": "notes/reports/legacy-review.md",
                }
            ],
        },
        now=datetime(2026, 9, 8, tzinfo=UTC),
    )
    # ``scope`` did not exist at the baseline commit.  The exclude-none JSON is
    # therefore the exact persisted shape consumed by the new master-only field.
    legacy_payload = json.loads(review.model_dump_json(by_alias=True, exclude_none=True))
    assert "scope" not in legacy_payload, BASELINE_ROUTE_REVIEW_COMMIT
    return contract, document, document_path, legacy_payload


def test_baseline_leaf_route_review_keeps_digest_and_currentness(tmp_path: Path) -> None:
    """A baseline leaf record remains byte-addressed and current after scope is added."""

    contract, document, document_path, legacy_payload = _legacy_leaf_fixture(tmp_path)
    parsed = RouteReviewRecord.model_validate_json(json.dumps(legacy_payload))

    assert parsed.scope is None
    assert parsed.recordDigest == legacy_payload["recordDigest"]
    write_task_doc(contract.task_root, document.model_copy(update={"routeReview": parsed}))
    persisted = json.loads(document_path.read_text(encoding="utf-8"))
    assert "scope" not in persisted["routeReview"]

    current = require_current_route_review(contract)

    assert current["status"] == "current"
    assert current["required"] is True
    assert current["taskIntent"] == parsed.taskIntent.model_dump(mode="json", by_alias=True)
    assert (
        parsed.recordDigest
        == RouteReviewRecord.model_validate(persisted["routeReview"]).recordDigest
    )


def _master_document(*, child_files: list[str]) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": "ATOMIC-MASTER",
            "slug": "atomic-master",
            "title": "Atomic master",
            "kind": "master",
            "status": "inProgress",
            "repo": "repo",
            "createdAt": NOW,
            "executionNature": "atomic",
            "objective": "Accumulate the exact normative work.",
            "requirements": ["The accumulated master remains reviewable."],
            "subTasks": [
                {
                    "number": f"C{index}",
                    "name": Path(filename).stem,
                    "file": filename,
                    "status": "inProgress",
                }
                for index, filename in enumerate(child_files, start=1)
            ],
        }
    )


def _child_document(slug: str, *, objective: str | None = None) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": slug.upper(),
            "slug": slug,
            "title": slug.replace("-", " ").title(),
            "kind": "subTask",
            "repo": "repo",
            "createdAt": NOW,
            "objective": objective or f"Normative objective for {slug}.",
            "requirements": [f"Requirement for {slug}."],
            "steps": [
                {
                    "id": "S1",
                    "title": "Implement the child",
                    "outcome": "The child implementation is complete.",
                }
            ],
        }
    )


def _atomic_master_fixture(root: Path) -> tuple[WorktreeContract, TaskDocument, TaskDocument]:
    coordination = root / "coordination"
    task = ContractTask(
        name="atomic-master",
        repo_name="repo",
        coordination_root=coordination,
        workflow_kind="light-task",
        memory_mode="internal",
    )
    contract = default_series_contract(
        task,
        code=RepoBranchPlan(
            repo_path=root / "code",
            source_branch="main",
            work_branch="ar/atomic-master",
            base_commit="a" * 40,
        ),
    )
    master = _master_document(child_files=["child-one.md"])
    child = _child_document("child-one")
    write_task_doc(contract.task_root, master)
    write_task_doc(contract.task_root, child)
    return contract, master, child


def _master_review(scope) -> RouteReviewRecord:
    return RouteReviewRecord.model_validate(
        {
            "candidateTree": "b" * 40,
            "verdict": "pass",
            "verdictRef": "notes/reports/master-review.md",
            "reviewedAt": NOW,
            "routes": [
                {
                    "route": "master-aggregate",
                    "verdict": "pass",
                    "evidenceRef": "notes/reports/master-review.md",
                }
            ],
            "taskIntent": scope.aggregate_intent.model_dump(mode="json", by_alias=True),
            "scope": scope.review_scope.model_dump(mode="json", by_alias=True),
        }
    )


@pytest.mark.parametrize("change", ["master-status", "child-step-status", "master-review"])
def test_atomic_master_aggregate_ignores_operational_bookkeeping(
    tmp_path: Path, change: str
) -> None:
    contract, master, child = _atomic_master_fixture(tmp_path)
    original = resolve_atomic_master_scope(contract)
    assert original is not None

    if change == "master-status":
        updated = master.model_copy(update={"status": "Completed", "statusNote": "Done."})
        candidate = ResolvedTaskDocument(
            ref=original.master.ref,
            path=original.master.path,
            document=updated,
        )
        changed_scope = resolve_atomic_master_scope(contract, master_override=candidate)
    elif change == "child-step-status":
        updated = child.model_copy(
            update={"steps": [child.steps[0].model_copy(update={"status": "done"})]}
        )
        write_task_doc(contract.task_root, updated)
        changed_scope = resolve_atomic_master_scope(contract)
    else:
        review = _master_review(original)
        updated = TaskDocument.model_validate(
            {
                **master.model_dump(mode="json", by_alias=True),
                "routeReview": review.model_dump(mode="json", by_alias=True),
            }
        )
        candidate = ResolvedTaskDocument(
            ref=original.master.ref,
            path=original.master.path,
            document=updated,
        )
        changed_scope = resolve_atomic_master_scope(contract, master_override=candidate)

    assert changed_scope is not None
    assert changed_scope.aggregate_intent == original.aggregate_intent


@pytest.mark.parametrize("change", ["master-objective", "child-objective", "membership"])
def test_atomic_master_aggregate_changes_for_normative_or_membership_changes(
    tmp_path: Path, change: str
) -> None:
    contract, master, child = _atomic_master_fixture(tmp_path)
    original = resolve_atomic_master_scope(contract)
    assert original is not None

    if change == "master-objective":
        updated = master.model_copy(update={"objective": "A changed master objective."})
        candidate = ResolvedTaskDocument(
            ref=original.master.ref,
            path=original.master.path,
            document=updated,
        )
        changed_scope = resolve_atomic_master_scope(contract, master_override=candidate)
    elif change == "child-objective":
        write_task_doc(
            contract.task_root,
            child.model_copy(update={"objective": "A changed child objective."}),
        )
        changed_scope = resolve_atomic_master_scope(contract)
    else:
        child_two = _child_document("child-two")
        write_task_doc(contract.task_root, child_two)
        updated = TaskDocument.model_validate(
            {
                **master.model_dump(mode="json", by_alias=True),
                "subTasks": [
                    *master.model_dump(mode="json", by_alias=True)["subTasks"],
                    {
                        "number": "C2",
                        "name": "child-two",
                        "file": "child-two.md",
                        "status": "inProgress",
                    },
                ],
            }
        )
        candidate = ResolvedTaskDocument(
            ref=original.master.ref,
            path=original.master.path,
            document=updated,
        )
        changed_scope = resolve_atomic_master_scope(contract, master_override=candidate)

    assert changed_scope is not None
    assert changed_scope.aggregate_intent != original.aggregate_intent


def _organizational_leaf_contract(root: Path) -> WorktreeContract:
    coordination = root / "coordination"
    code = root / "code"
    base = _init_repo(code)
    parent_task = ContractTask(
        name="organizational-master",
        repo_name="repo",
        coordination_root=coordination,
        workflow_kind="light-task",
        memory_mode="internal",
    )
    parent = default_series_contract(
        parent_task,
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="main",
            work_branch="super",
            base_commit=base,
        ),
    )
    write_contract(parent.contract_path, parent)
    master = TaskDocument.model_validate(
        {
            "id": "ORG-MASTER",
            "slug": "organizational-master",
            "title": "Organizational master",
            "kind": "master",
            "repo": "repo",
            "createdAt": NOW,
            "executionNature": "organizational",
        }
    )
    write_task_doc(parent.task_root, master)
    sprint = TaskDocument.model_validate(
        {
            "id": "SPRINT",
            "slug": "sprint",
            "title": "Sprint",
            "kind": "master",
            "repo": "repo",
            "createdAt": NOW,
            "orchestrates": ["organizational-master"],
            "executionGraph": {
                "nodes": [
                    {
                        "kind": "master",
                        "ref": {"repository": "repo", "path": "organizational-master/task.json"},
                    }
                ],
                "edges": [],
            },
        }
    )
    write_task_doc(coordination / "tasks" / "repo" / "sprint", sprint)
    leaf = default_contract(
        ContractTask(
            name="organizational-master",
            repo_name="repo",
            coordination_root=coordination,
            workflow_kind="light-task",
            memory_mode="internal",
            parent_task_name="organizational-master",
            parent_contract_path=parent.contract_path,
        ),
        leaf=LeafIdentity(worktree_name="organizational-leaf"),
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="main",
            work_branch="ar/organizational-leaf",
            base_commit=base,
        ),
    )
    return replace(leaf, parent_contract_path=parent.contract_path)


@pytest.mark.parametrize("kind", ["standalone", "organizational"])
def test_standalone_and_organizational_leaves_keep_leaf_review_dispatch(
    tmp_path: Path, kind: str
) -> None:
    if kind == "standalone":
        contract = default_contract(
            ContractTask(
                name="standalone-leaf",
                repo_name="repo",
                coordination_root=tmp_path / "coordination",
                workflow_kind="light-task",
                memory_mode="internal",
            ),
            leaf=LeafIdentity(worktree_name="standalone-leaf"),
            code=RepoBranchPlan(
                repo_path=tmp_path / "code",
                source_branch="main",
                work_branch="ar/standalone-leaf",
                base_commit="c" * 40,
            ),
        )
    else:
        contract = _organizational_leaf_contract(tmp_path)

    marker = {"required": True, "status": "leaf-current"}
    with patch.object(
        route_review_scope, "require_leaf_route_review", return_value=marker
    ) as leaf_gate:
        assert resolve_atomic_master_scope(contract) is None
        assert require_current_route_review(contract) is marker
    leaf_gate.assert_called_once_with(contract)
