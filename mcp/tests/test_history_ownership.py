"""MIK-R52 F04 rule 5: history-file ownership over a synthetic master.

A newly closed history file passes only with an owner: the landed leaf whose recorded
publication holds byte-identical content, else a direct landing or an admitted crossing
head. A fabricated edit, a leaf with no enclosure, and an unreadable enclosure all refuse
by name. The master task tree is synthetic and tiny; the git reads are real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.knowledge_gate.landing import _master_history_ownership
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.services import (
    LandingGateRequest,
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    leaf_enclosure_path,
    load_contract,
    write_contract,
)

HISTORY = "knowledge/history/L1.json"


@pytest.fixture
def ports() -> Any:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "line")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "t")
    return path


def _history_file(leaf: str, closed: bool, extra: str = "") -> str:
    return canonical_text(
        {"schema": "ar-history/v1", "leaf": leaf, "closed": closed, "rows": [], "note": extra}
    )


class OwnershipMaster:
    """A master with one landed leaf L1, built on real temporary Git lines."""

    def __init__(self, root: Path) -> None:
        coord = root / "coord"
        self.task_root = coord / "tasks" / "agents-remember" / "master"
        self.task_root.mkdir(parents=True)
        code = _repo(root / "code")
        memory = _repo(root / "memory")
        (code / "code.txt").write_text("code\n", encoding="utf-8")
        _git(code, "add", "-A")
        _git(code, "commit", "-q", "-m", "code base")
        self.code_base = _git(code, "rev-parse", "HEAD")
        (memory / "memory.md").write_text("# ledger\n", encoding="utf-8")
        _git(memory, "add", "-A")
        _git(memory, "commit", "-q", "-m", "memory base")
        self.memory_base = _git(memory, "rev-parse", "HEAD")
        self.code = code
        self.memory = memory
        (memory / "knowledge" / "history").mkdir(parents=True)
        (memory / "knowledge" / "history" / "L1.json").write_text(
            _history_file("L1", True), encoding="utf-8"
        )
        _git(memory, "add", "-A")
        _git(memory, "commit", "-q", "-m", "land L1 history closed")
        self.landed = _git(memory, "rev-parse", "HEAD")
        master_doc = TaskDocument.model_validate(
            {
                "id": "OWNERSHIP-MASTER",
                "slug": "task",
                "title": "ownership master",
                "kind": "master",
                "status": "inProgress",
                "repo": "agents-remember",
                "createdAt": "2026-08-13T00:00:00+00:00",
                "subTasks": [
                    {"number": "L1", "name": "leaf one", "file": "L1.md", "status": "inProgress"},
                ],
            }
        )
        write_task_doc(self.task_root, master_doc)
        leaf_doc = TaskDocument.model_validate(
            {
                "id": "L1",
                "slug": "L1",
                "title": "leaf one",
                "kind": "subTask",
                "status": "inProgress",
                "repo": "agents-remember",
                "createdAt": "2026-08-13T00:00:00+00:00",
            }
        )
        (self.task_root / "L1.json").write_text(
            leaf_doc.model_dump_json(by_alias=True), encoding="utf-8"
        )
        enclosure = leaf_enclosure_path(self.task_root, "L1")
        enclosure.parent.mkdir(parents=True, exist_ok=True)
        leaf = WorktreeContract(
            task_id="OWNERSHIP_MASTER",
            task_name="master",
            repo_name="agents-remember",
            workflow_kind="light-task",
            memory_mode="external",
            coordination_root=coord,
            task_root=self.task_root,
            contract_path=enclosure,
            task_artifact=self.task_root / "task.md",
            worktree_group=coord / "worktrees" / "agents-remember" / "leaf-ar",
            code_repo_path=code,
            code_source_branch="line",
            code_work_branch="ar/l1",
            code_base_commit=self.code_base,
            code_worktree=root / "worktrees" / "L1",
            memory_repo_path=memory,
            memory_source_branch="line",
            memory_work_branch="ar/l1",
            memory_base_commit=self.memory_base,
            memory_worktree=root / "worktrees" / "L1-memory",
            ledger_path=memory / "memory.md",
            code_commit=self.code_base,
            memory_content_commit=self.landed,
            integration_status="completed",
            kind="leaf",
            leaf_id="L1",
            parent_task_name="master",
            parent_contract_path=self.task_root / "series-contract.md",
        )
        write_contract(enclosure, leaf)
        (coord / "worktrees" / "agents-remember" / "master-ar" / ".lifecycle").mkdir(parents=True, exist_ok=True)
        self.series = WorktreeContract(
            task_id="OWNERSHIP_MASTER",
            task_name="master",
            repo_name="agents-remember",
            workflow_kind="light-task",
            memory_mode="external",
            coordination_root=coord,
            task_root=self.task_root,
            contract_path=self.task_root / "series-contract.md",
            task_artifact=self.task_root / "task.md",
            worktree_group=coord / "worktrees" / "agents-remember" / "master-ar",
            code_repo_path=code,
            code_source_branch="line",
            code_work_branch="line",
            code_base_commit=self.code_base,
            code_worktree=root / "worktrees" / "master",
            memory_repo_path=memory,
            memory_source_branch="line",
            memory_work_branch="line",
            memory_base_commit=self.memory_base,
            memory_worktree=root / "worktrees" / "master-memory",
            ledger_path=memory / "memory.md",
            kind="series",
        )
        write_contract(self.series.contract_path, self.series)
        text = self.series.contract_path.read_text(encoding="utf-8")
        publish_new_lifecycle_operation_location(load_contract(self.series.contract_path), contract_text=text)

    def request(self) -> LandingGateRequest:
        return LandingGateRequest(
            memory_repository=self.memory,
            memory_commit=self.landed,
            memory_bases=(self.memory_base,),
            code_repository=self.code,
            code_commit=self.code_base,
            code_base=self.code_base,
            series_contract=self.series.contract_path.as_posix(),
        )


@pytest.fixture
def master(tmp_path: Path) -> OwnershipMaster:
    return OwnershipMaster(tmp_path / "owned")


def test_newly_closed_file_with_its_leaf_owner_passes(master: OwnershipMaster, ports: None) -> None:
    """Rule 5 owned: the file closed in the landed commit matches the leaf's recorded
    publication byte for byte, so no finding is produced."""

    assert _master_history_ownership(master.request()) == []


def test_edited_closed_file_refuses_naming_it(master: OwnershipMaster, ports: None) -> None:
    """Rule 5 fabricated: a byte-different closed file is owned by no publication."""

    (master.memory / HISTORY).write_text(_history_file("L1", True, extra="edited"), encoding="utf-8")
    _git(master.memory, "add", "-A")
    _git(master.memory, "commit", "-q", "-m", "edit the closed file")
    edited = _git(master.memory, "rev-parse", "HEAD")
    request = master.request()
    edited_request = LandingGateRequest(
        memory_repository=request.memory_repository,
        memory_commit=edited,
        memory_bases=request.memory_bases,
        code_repository=request.code_repository,
        code_commit=request.code_commit,
        code_base=request.code_base,
        series_contract=request.series_contract,
    )
    findings = _master_history_ownership(edited_request)
    assert findings, "an edited closed file must not pass ownerless"
    assert HISTORY in str(findings[0].path or findings[0].message)


def test_file_of_a_leaf_without_an_enclosure_refuses_as_unowned(
    master: OwnershipMaster, ports: None
) -> None:
    """Rule 5 unowned: a file naming a leaf that published nothing has no owner."""

    other = "knowledge/history/L2.json"
    (master.memory / other).write_text(_history_file("L2", True), encoding="utf-8")
    _git(master.memory, "add", "-A")
    _git(master.memory, "commit", "-q", "-m", "close another leaf's file")
    unowned = _git(master.memory, "rev-parse", "HEAD")
    request = master.request()
    unowned_request = LandingGateRequest(
        memory_repository=request.memory_repository,
        memory_commit=unowned,
        memory_bases=request.memory_bases,
        code_repository=request.code_repository,
        code_commit=request.code_commit,
        code_base=request.code_base,
        series_contract=request.series_contract,
    )
    findings = _master_history_ownership(unowned_request)
    assert findings, "a file no enclosure owns must not pass"
    location = str(findings[0].path or "") + str(findings[0].message)
    assert "L2.json" in location


def test_unreadable_enclosure_refuses_naming_it(master: OwnershipMaster, ports: None) -> None:
    """Rule 5 unreadable: an enclosure contract Git cannot parse refuses, naming it."""

    enclosure = leaf_enclosure_path(master.series.task_root, "L1")
    enclosure.write_text("not a contract\n", encoding="utf-8")
    findings = _master_history_ownership(master.request())
    assert findings, "an unreadable enclosure must not pass silently"
    assert "l1" in str(findings[0].message).lower() or "enclosure" in str(findings[0].message)
