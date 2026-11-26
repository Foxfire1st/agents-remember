"""Build the disposable linked Git pair for the native stdio memory test."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.kernel.memory_ledger import create_initial_ledger, write_ledger
from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
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
from mcp_memory_bounded_corpus import write_code, write_memory
from test_worktree_support import TEST_CERTIFICATION_PROFILE_REFERENCE, install_fixture_profile

REPO_ID = "repo-a"
TASK_NAME = "bug-r18-memory-fixture"
LEAF_ID = "BUG-R18-MEMORY-FIXTURE-L1"
LEAF_SLUG = LEAF_ID.lower()
_SPRINT = "bug-r18-fixture-sprint"
_DEFAULT_BRANCH = "fixture-default"
_SERIES_BRANCH = f"ar/{TASK_NAME}"
_LEAF_BRANCH = f"ar/{LEAF_SLUG}"
_CREATED_AT = "2026-01-01T00:00:00Z"

_GIT = GitRunnerOptions(
    identity={
        "GIT_AUTHOR_NAME": "Memory regression fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": _CREATED_AT,
        "GIT_COMMITTER_NAME": "Memory regression fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_DATE": _CREATED_AT,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
)


@dataclass(frozen=True)
class Fixture:
    root: Path
    settings: Path
    code_root: Path
    code_tree: str
    memory_root: Path
    coordination: Path
    contract: Path
    task_ref: dict[str, str]
    logs: Path
    interpreter: Path

    @property
    def task_context(self) -> dict[str, str | dict[str, str]]:
        return {"task_document_ref": self.task_ref, "contract_path": str(self.contract)}


def _git(repo: Path, *args: str) -> str:
    result = run_git(repo, list(args), _GIT)
    assert result.returncode == 0, f"Git {args!r} in {repo}: {result.stderr}"
    return result.stdout.strip()


def _init(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", _DEFAULT_BRANCH)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git_pair(workspace: Path, coordination: Path) -> tuple[RepoBranchPlan, RepoBranchPlan]:
    code = workspace / REPO_ID
    _init(code)
    install_fixture_profile(code, REPO_ID)
    write_code(code)
    code_base = _commit(code, "Generate the code corpus")
    remote_ref = f"refs/remotes/origin/{_DEFAULT_BRANCH}"
    _git(code, "update-ref", remote_ref, code_base)
    _git(code, "symbolic-ref", "refs/remotes/origin/HEAD", remote_ref)

    memory = coordination / "memory-repos" / f"ar-{REPO_ID}"
    _init(memory)
    _git(memory, "config", "--local", "agents-remember.defaultBranch", _DEFAULT_BRANCH)
    _git(memory, "commit", "-q", "--allow-empty", "-m", "Start the generated memory history")
    seed = _git(memory, "rev-parse", "HEAD")
    write_ledger(memory / "memory.md", create_initial_ledger(REPO_ID, code_base, seed))
    _write_json(
        memory / "system" / "settings.json",
        {
            "version": 2,
            "onboarding": {
                "storage": {"mode": "memory-repo"},
                "pathRules": {"include": {"paths": ["*"], "fileTypes": [".md", ".py"]}},
            },
        },
    )
    (memory / ".gitignore").write_text("*.index.json\n", encoding="utf-8")
    write_memory(memory, code)
    memory_base = _commit(
        memory, f"Generate the converted memory corpus\n\nCode-Commit: {code_base}"
    )

    for repo, base in ((code, code_base), (memory, memory_base)):
        _git(repo, "branch", _SERIES_BRANCH, base)
    return (
        RepoBranchPlan(code, _DEFAULT_BRANCH, _SERIES_BRANCH, code_base),
        RepoBranchPlan(memory, _DEFAULT_BRANCH, _SERIES_BRANCH, memory_base),
    )


def _publish(contract: WorktreeContract) -> None:
    write_contract(contract.contract_path, contract)
    publish_new_lifecycle_operation_location(
        contract, contract_text=contract_publication_text(contract.contract_path, contract)
    )


def _enclose(
    coordination: Path, code: RepoBranchPlan, memory: RepoBranchPlan
) -> tuple[WorktreeContract, WorktreeContract]:
    task = ContractTask(TASK_NAME, REPO_ID, coordination, "chat-task", "external")
    series = default_series_contract(task, code=code, memory=memory)
    _publish(series)
    leaf = default_contract(
        replace(task, parent_task_name=TASK_NAME, parent_contract_path=series.contract_path),
        leaf=LeafIdentity(worktree_name=LEAF_SLUG, leaf_id=LEAF_ID),
        code=replace(code, source_branch=_SERIES_BRANCH, work_branch=_LEAF_BRANCH),
        memory=replace(memory, source_branch=_SERIES_BRANCH, work_branch=_LEAF_BRANCH),
    )
    assert leaf.memory_worktree is not None
    for plan, worktree in ((code, leaf.code_worktree), (memory, leaf.memory_worktree)):
        _git(plan.repo_path, "worktree", "add", "-b", _LEAF_BRANCH, str(worktree), _SERIES_BRANCH)
    _publish(leaf)
    return series, leaf


def _task_documents(series: WorktreeContract, leaf: WorktreeContract) -> dict[str, str]:
    common = {"createdAt": _CREATED_AT, "repo": REPO_ID, "status": "inProgress"}
    branch_contract = {"seriesContractPath": str(series.contract_path)}
    write_task_doc(
        series.task_root,
        TaskDocument.model_validate(
            {
                **common,
                **branch_contract,
                "id": series.task_id,
                "slug": "task",
                "title": "Generated memory workload",
                "kind": "master",
                "executionNature": "atomic",
                "subTasks": [
                    {
                        "number": LEAF_ID,
                        "name": "Generated workload leaf",
                        "file": f"{LEAF_SLUG}.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    leaf_path, _ = write_task_doc(
        leaf.task_root,
        TaskDocument.model_validate(
            {
                **common,
                **branch_contract,
                "id": LEAF_ID,
                "slug": LEAF_SLUG,
                "title": "Generated workload leaf",
                "kind": "subTask",
                "master": "task.md",
                "enclosures": [{"leafId": LEAF_ID, "enclosurePath": str(leaf.contract_path)}],
            }
        ),
    )
    repo_tasks = series.task_root.parent
    master_ref = {"repository": REPO_ID, "path": f"{TASK_NAME}/task.json"}
    write_task_doc(
        repo_tasks / _SPRINT,
        TaskDocument.model_validate(
            {
                **common,
                "id": _SPRINT.upper(),
                "slug": "task",
                "title": "Generated workload sprint",
                "kind": "master",
                "orchestrates": [TASK_NAME],
                "integrationBranch": _DEFAULT_BRANCH,
                "executionGraph": {"nodes": [master_ref], "edges": []},
            }
        ),
    )
    return {"repository": REPO_ID, "path": leaf_path.relative_to(repo_tasks).as_posix()}


_WARM_INDEX = """
import json
import sys
from pathlib import Path

from agents_remember.kernel.primitives.checkout_coordination import declare_test_process
from agents_remember.memory.knowledge_index.cache import KnowledgeIndexCache, default_cache_directory
from agents_remember.serving.build_info import process_serving_build

package, coordination, memory = map(Path, sys.argv[1:4])
tree = sys.argv[4]
declare_test_process()
build = process_serving_build()
assert build.package_root == str(package) and build.source_digest is not None, build
cache = KnowledgeIndexCache(default_cache_directory(coordination))
with cache.for_git_tree(memory, tree) as index:
    state = index.state
    assert state.converted and state.state == "complete" and state.key == tree, state.problems[:10]
    print(json.dumps({
        "sourceDigest": build.source_digest, "commit": build.commit, "packageRoot": build.package_root,
        "interpreter": sys.executable, "memoryTree": tree, "indexPath": str(index.path),
        "indexBytes": index.path.stat().st_size,
    }))
"""


def _warm_index(fixture: Fixture, source: Path, memory_tree: str) -> None:
    """Populate the selected serving build's index without warming the measured process."""
    package = source / "mcp" / "src" / "agents_remember"
    temporary = fixture.root / "index-tmp"
    temporary.mkdir()
    command = [
        str(fixture.interpreter),
        "-B",
        "-P",
        "-c",
        _WARM_INDEX,
        str(package),
        str(fixture.coordination),
        str(fixture.memory_root),
        memory_tree,
    ]
    result = subprocess.run(
        command,
        cwd=fixture.root,
        env={
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(temporary),
            "TMPDIR": str(temporary),
            "PYTHONPATH": str(package.parent),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    (fixture.root / "warm-index.json").write_text(result.stdout, encoding="utf-8")


def build_fixture(root: Path, source: Path, interpreter: Path) -> Fixture:
    """Prepare a synthetic leaf and warm its index for the exact selected checkout."""
    declare_test_process()
    root = root.resolve()
    workspace = root / "workspace"
    coordination = root / "ar-coordination"
    code, memory = _git_pair(workspace, coordination)
    series, leaf = _enclose(coordination, code, memory)
    assert leaf.memory_worktree is not None
    task_ref = _task_documents(series, leaf)
    logs = root / "tool-logs"
    settings = root / "settings" / "mcp.json"
    _write_json(
        settings,
        {
            "version": 1,
            "workspaceRoot": str(workspace),
            "coordinationRoot": str(coordination),
            "transcriptRoot": str(logs),
            "repositories": {
                REPO_ID: {"certificationProfile": str(TEST_CERTIFICATION_PROFILE_REFERENCE)}
            },
            "dashboard": {"autoStart": False},
        },
    )
    fixture = Fixture(
        root=root,
        settings=settings,
        code_root=leaf.code_worktree,
        code_tree=_git(leaf.code_worktree, "rev-parse", "HEAD^{tree}"),
        memory_root=leaf.memory_worktree,
        coordination=coordination,
        contract=leaf.contract_path,
        task_ref=task_ref,
        logs=logs,
        interpreter=interpreter,
    )
    _warm_index(fixture, source.resolve(), _git(leaf.memory_worktree, "rev-parse", "HEAD^{tree}"))
    return fixture
