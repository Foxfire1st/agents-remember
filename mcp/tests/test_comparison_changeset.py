"""The viewer's comparison selector reads immutable trees through the actual HTTP routes."""

from pathlib import Path

import pytest
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.kernel.git_command import run_git
from agents_remember.models.knowledge.review_trees import (
    ReviewConvertedBase,
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.serving import changeset_comparison as owner
from agents_remember.serving.changeset import register_changeset_routes
from agents_remember.serving.response_contract import ComparisonChangeSet
from agents_remember.worktrees.worktree_contract import WorktreeContract
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_knowledge_review_source_endpoints import build_endpoint_fixture

pytestmark = pytest.mark.evidence_unit


def git(root: Path, *args: str) -> str:
    result = run_git(root, list(args))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _recorded_trees(contract: WorktreeContract) -> tuple[ReviewTreeComparisonRecord, str]:
    """Real rename and unchanged sidecar on four immutable fixture trees."""
    code, memory = contract.code_worktree, contract.memory_worktree
    assert code is not None and memory is not None
    before = git(code, "rev-parse", f"{contract.code_base_commit}^{{tree}}")
    # Capture the complete uncommitted candidate, leaving HEAD at the base.
    old_path = next(
        path
        for path in git(code, "ls-tree", "-r", "--name-only", before).splitlines()
        if (code / path).is_file()
    )
    (code / old_path).rename(code / "renamed.txt")
    (code / "renamed.txt").write_text("new path\n")
    git(code, "add", "-A")
    after = git(code, "write-tree")
    sidecar = memory / "onboarding/renamed.txt.md"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text("An unchanged partner.\n")
    git(memory, "add", "-A")
    base_mem = git(memory, "write-tree")
    for rel in ("knowledge/INV-example.json", "onboarding/example.md", "notes/outside.md"):
        path = memory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("candidate memory\n")
    git(memory, "add", "-A")
    after_mem = git(memory, "write-tree")
    record = ReviewTreeComparisonRecord(
        task_id=contract.task_id,
        leaf_id=contract.leaf_id,
        number=5,
        code_base=ReviewTreeSide(repository=str(code), tree=before),
        code_candidate=ReviewTreeSide(repository=str(code), tree=after),
        memory_base=ReviewTreeSide(repository=str(memory), tree=base_mem),
        memory_candidate=ReviewTreeSide(repository=str(memory), tree=after_mem),
        recorded_at="2026-10-08T00:00:00Z",
    )
    return record, old_path


def test_recorded_comparison_is_exact_including_dirty_and_rename_and_scoped_memory(
    tmp_path, monkeypatch
):
    fixture = build_endpoint_fixture(tmp_path, memory_mode="external")
    contract = fixture.contract
    code = contract.code_worktree
    memory = contract.memory_worktree
    assert code is not None and memory is not None
    record, old_path = _recorded_trees(contract)
    before, after = record.code_base.tree, record.code_candidate.tree
    base_mem, after_mem = record.memory_base.tree, record.memory_candidate.tree
    directory = comparison_directory(contract.task_root, contract.leaf_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "5.json"
    path.write_text(record.model_dump_json(by_alias=True))
    app = FastAPI()
    register_changeset_routes(app, fixture.config)
    client = TestClient(app)
    params = {
        "repo": contract.repo_name,
        "master": fixture.master,
        "leaf": contract.leaf_id,
        "comparison": "5",
    }
    children = []
    original = owner.run_git

    def measured(root, args):
        children.append(args)
        return original(root, args)

    monkeypatch.setattr(owner, "run_git", measured)
    body = client.get("/api/changeset/task", params=params).json()
    ComparisonChangeSet.model_validate(body)
    assert body["mode"] == "comparison" and body["comparison"] == 5
    assert body["trees"] == {
        "codeBase": before,
        "codeTip": after,
        "memoryBase": base_mem,
        "memoryTip": after_mem,
    }
    assert sum(args[0] == "diff" for args in children) == 2
    assert not any(args[0] in ("show", "cat-file") for args in children)
    assert (
        next(file for file in body["code"] if file["path"] == "renamed.txt")["hasSidecar"] is True
    )
    assert "onboarding/renamed.txt.md" not in {file["path"] for file in body["memory"]}
    expected = set(git(code, "diff", "--name-only", "--no-renames", before, after).splitlines())
    assert {file["path"] for file in body["code"]} == expected
    assert next(file for file in body["code"] if file["path"] == old_path)["status"] == "D"
    assert next(file for file in body["code"] if file["path"] == "renamed.txt")["status"] == "A"
    assert {file["path"] for file in body["memory"]} == {
        "knowledge/INV-example.json",
        "onboarding/example.md",
    }
    # A later dirty edit never replaces the captured candidate.
    (code / "renamed.txt").write_text("later disk text\n")
    opened = client.get("/api/changeset/file-diff", params={**params, "path": "renamed.txt"}).json()
    assert opened["before"] is None and opened["after"]["content"] == "new path\n"
    assert (
        client.get("/api/changeset/task", params={**params, "mode": "working"}).status_code == 400
    )
    assert client.get("/api/changeset/task", params={**params, "mode": ""}).status_code == 400
    assert (
        client.get(
            "/api/changeset/file-diff", params={**params, "mode": "", "path": "renamed.txt"}
        ).status_code
        == 400
    )
    missing = client.get("/api/changeset/task", params={**params, "comparison": "999"})
    assert missing.status_code == 404 and "999" in missing.text
    lost = record.model_copy(
        update={"code_candidate": record.code_candidate.model_copy(update={"tree": "f" * 40})}
    )
    path.write_text(lost.model_dump_json(by_alias=True))
    missing = client.get("/api/changeset/task", params=params)
    assert missing.status_code == 404 and "f" * 40 in missing.text
    # A converted base is the review's before memory tree, regardless of the legacy tree.
    converted = record.model_copy(
        update={
            "converted_base": ReviewConvertedBase(
                commit=git(memory, "rev-parse", "HEAD"),
                version="fixture",
                code_commit=contract.code_base_commit,
                tree=base_mem,
            ),
            "memory_base": record.memory_base.model_copy(update={"tree": "f" * 40}),
        }
    )
    path.write_text(converted.model_dump_json(by_alias=True))
    assert client.get("/api/changeset/task", params=params).json() == body


def _comparison_with_symlinks(tmp_path):
    """Create both historical file and symlink blobs on each comparison side."""
    fixture = build_endpoint_fixture(tmp_path, memory_mode="external")
    contract = fixture.contract
    record, _ = _recorded_trees(contract)
    halves = (
        ("code", contract.code_worktree, "historic/subject.txt", "historic/link", "renamed.txt"),
        (
            "memory",
            contract.memory_worktree,
            "knowledge/subject.json",
            "knowledge/link.json",
            "onboarding/renamed.txt.md",
        ),
    )
    for kind, root, requested, historical_link, _ in halves:
        assert root is not None
        file = root / requested
        link = root / historical_link
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(f"before {kind}\n")
        link.symlink_to(f"before-{kind}-target")
        git(root, "add", "-A")
        base = git(root, "write-tree")
        file.write_text(f"after {kind}\n")
        link.unlink()
        link.symlink_to(f"after-{kind}-target")
        git(root, "add", "-A")
        tip = git(root, "write-tree")
        record = record.model_copy(
            update={
                f"{kind}_base": ReviewTreeSide(repository=str(root), tree=base),
                f"{kind}_candidate": ReviewTreeSide(repository=str(root), tree=tip),
            }
        )
    directory = comparison_directory(contract.task_root, contract.leaf_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "5.json").write_text(record.model_dump_json(by_alias=True))
    app = FastAPI()
    register_changeset_routes(app, fixture.config)
    client = TestClient(app)
    params = {
        "repo": contract.repo_name,
        "master": fixture.master,
        "leaf": contract.leaf_id,
        "comparison": "5",
    }
    return client, params, halves


def test_comparison_paths_ignore_current_aliases_on_both_git_halves(tmp_path):
    """F01: immutable file and symlink objects stay addressed by their requested Git path."""
    client, params, halves = _comparison_with_symlinks(tmp_path)
    listing = client.get("/api/changeset/task", params=params)
    assert listing.status_code == 200
    for kind, root, requested, historical_link, in_root_target in halves:
        assert root is not None
        response = client.get(
            "/api/changeset/file-diff", params={**params, "kind": kind, "path": requested}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["path"] == requested
        assert body["before"] == {"content": f"before {kind}\n"}
        assert body["after"] == {"content": f"after {kind}\n"}
        file = root / requested
        file.unlink()
        outside = tmp_path / f"outside-{kind}"
        outside.write_text("outside content\n")
        for target in (root / in_root_target, outside, None):
            if target is not None:
                file.symlink_to(target)
            observed = client.get(
                "/api/changeset/file-diff", params={**params, "kind": kind, "path": requested}
            )
            assert observed.status_code == 200
            assert observed.json() == body
            if target is not None:
                file.unlink()
        # Git's symlink is itself a blob: neither its historical nor its current target is read.
        link = root / historical_link
        link.unlink()
        link.symlink_to(root / in_root_target)
        observed = client.get(
            "/api/changeset/file-diff", params={**params, "kind": kind, "path": historical_link}
        )
        assert observed.status_code == 200
        assert observed.json()["path"] == historical_link
        assert observed.json()["before"] == {"content": f"before-{kind}-target"}
        assert observed.json()["after"] == {"content": f"after-{kind}-target"}
    assert client.get("/api/changeset/task", params=params).content == listing.content
    for kind in ("code", "memory"):
        for requested in (
            str(tmp_path / "absolute"),
            "../outside",
            "folder/../outside",
            "knowledge/../../outside",
        ):
            refused = client.get(
                "/api/changeset/file-diff", params={**params, "kind": kind, "path": requested}
            )
            assert refused.status_code == 400 and refused.json()["status"] == "bad-path"
    for requested in (
        "notes/outside.md",
        "knowledge/../notes/outside.md",
        "onboarding/../notes/outside.md",
    ):
        refused = client.get(
            "/api/changeset/file-diff", params={**params, "kind": "memory", "path": requested}
        )
        assert refused.status_code == 400 and refused.json()["status"] == "bad-path"
