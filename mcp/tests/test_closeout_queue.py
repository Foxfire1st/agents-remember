from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.controlplane.closeout_queue_store import (
    CloseoutQueueStore,
    CloseoutQueueStoreError,
    queue_store_paths,
)
from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    load_ledger,
    prepend_mapping,
    write_ledger,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.closeout_queue import (
    CloseoutQueueState,
    SchedulingGradeInput,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import Step, TaskDocument, read_task_doc, write_task_doc
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.worktrees.closeout_queue import (
    CloseoutQueueError,
    CloseoutQueueRequest,
    QueueActor,
    _graph_context,
    _project_candidates,
    closeout_queue_tool,
)
from agents_remember.worktrees.closeout_queue_lifecycle import (
    claim_queue_candidate_for_closeout,
    release_queue_candidate_after_reversible_operation,
    require_queue_candidate_current,
)
from agents_remember.worktrees.route_review import code_candidate_tree
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    default_series_contract,
    load_contract,
    write_contract,
)
from pydantic import ValidationError
from test_worktree_support import git, init_repo

REPO = "repo-a"
SPRINT = TaskDocumentRef(repository=REPO, path="sprint/task.json")
MASTER_A = TaskDocumentRef(repository=REPO, path="master-a/task.json")
MASTER_B = TaskDocumentRef(repository=REPO, path="master-b/task.json")
LEAF_A = TaskDocumentRef(repository=REPO, path="master-a/leaf-a.json")
LEAF_B = TaskDocumentRef(repository=REPO, path="master-b/leaf-b.json")
NOW = "2026-08-15T00:00:00+00:00"
RATIONALE = "Explicit portfolio judgment."
JUDGMENT_HEADING = "Judgment Register (canonical judgment authority)"
PRIORITY_HEADING = "Priority Register (explicit judgment)"
JUDGMENT_HEADER = (
    "| Judgment id | Kind (dependency meaning, execution nature, blast radius, priority, "
    "barrier placement, reprioritization, or leaf move) | Subject | Decision | Rationale | "
    "Evidence/fact refs | Author | Confidence | Supersedes |"
)
JUDGMENT_SEPARATOR = (
    "| ----------- | ---------------------------------------------------------------"
    "----------------------------------------------------------- | ------- | -------- | "
    "--------- | ------------------ | ------ | ---------- | ---------- |"
)
PRIORITY_HEADER = (
    "| Candidate/master | Grade (critical, high, normal, or low) | Affected dependents | "
    "Judgment id |"
)
PRIORITY_SEPARATOR = (
    "| ---------------- | ------------------------------------ | ------------------- | "
    "----------- |"
)


def _config(root: Path, code: Path, memory: Path | None) -> McpRuntimeConfig:
    return cast(
        McpRuntimeConfig,
        SimpleNamespace(
            coordination_root=root,
            repositories={REPO: RepositoryScope(REPO, code, memory)},
        ),
    )


def _master(
    ref: TaskDocumentRef,
    leaf_id: str,
    nature: str,
    *,
    status: str = "inProgress",
) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": ref.path.split("/")[0].upper(),
            "slug": ref.path.split("/")[0],
            "title": ref.path.split("/")[0],
            "kind": "master",
            "status": status,
            "repo": REPO,
            "createdAt": NOW,
            "executionNature": nature,
            "subTasks": [
                {
                    "number": leaf_id,
                    "name": leaf_id,
                    "file": f"{leaf_id.lower()}.md",
                    "status": "inProgress",
                }
            ],
        }
    )


def _leaf(contract: WorktreeContract, slug: str) -> TaskDocument:
    report = contract.task_root / "notes" / "reports" / f"{slug}-review.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Review\n\nPass.\n", encoding="utf-8")
    return TaskDocument.model_validate(
        {
            "id": contract.leaf_id,
            "slug": slug,
            "title": slug,
            "kind": "subTask",
            "status": "inProgress",
            "repo": REPO,
            "createdAt": NOW,
            "steps": [{"id": "S1", "title": "Ready", "status": "done"}],
            "routeReview": {
                "candidateTree": code_candidate_tree(contract),
                "verdict": "pass",
                "verdictRef": f"notes/reports/{slug}-review.md",
                "reviewedAt": NOW,
                "routes": [
                    {
                        "route": slug,
                        "verdict": "pass",
                        "evidenceRef": f"notes/reports/{slug}-review.md",
                    }
                ],
            },
        }
    )


def _curator_report() -> str:
    return """# Curator Memory Quality Checklist

- Status: **ready-for-closeout**

## Summary

| Class | Count | Gate meaning |
| --- | ---: | --- |
| Repairable memory findings | 0 | Must reach zero |
| Missing onboarding | 0 | Must reach zero |
| Stale route indexes | 0 | Must reach zero |
"""


def _judgment_row(ref: TaskDocumentRef, priority: str) -> str:
    return (
        f"| J-{Path(ref.path).stem}-{priority} | priority | {ref.key} | "
        f"priority={priority} | {RATIONALE} | grade.md | "
        "orchestrator | high | |"
    )


def _priority_row(ref: TaskDocumentRef, priority: str) -> str:
    return f"| {ref.key} | {priority} | none | J-{Path(ref.path).stem}-{priority} |"


def _judgment_table(rows: list[str]) -> str:
    return "\n".join([JUDGMENT_HEADER, JUDGMENT_SEPARATOR, *rows])


def _priority_table(rows: list[str]) -> str:
    return "\n".join([PRIORITY_HEADER, PRIORITY_SEPARATOR, *rows])


def _grade(priority: str, candidate: TaskDocumentRef) -> dict[str, Any]:
    return {
        "priority": priority,
        "judgmentId": f"J-{Path(candidate.path).stem}-{priority}",
    }


def _write_curator_evidence(
    contract: WorktreeContract,
    report_text: str | None = None,
    *,
    source_candidates: list[dict[str, str]] | None = None,
) -> None:
    report = contract.worktree_group / "reports" / "curator-memory-quality.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    text = report_text or _curator_report()
    report.write_text(text, encoding="utf-8")
    candidates = source_candidates or []
    memory_worktree = contract.memory_worktree
    assert memory_worktree is not None
    attestation = {
        "schema": "ar-curator-memory-quality/v1",
        "checklistStatus": "ready-for-closeout",
        "curatorActionableCount": 0,
        "memoryRepairCount": 0,
        "missingOnboardingCount": 0,
        "staleRouteIndexCount": 0,
        "sourceChangeCandidateCount": len(candidates),
        "sourceChangeCandidates": candidates,
        "onboardingRoot": (memory_worktree / "onboarding").resolve().as_posix(),
        "reportPath": report.resolve().as_posix(),
        "reportSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    report.with_suffix(".json").write_text(
        json.dumps(attestation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


class QueueFixture:
    def __init__(
        self,
        root: Path,
        *,
        edge: bool = False,
        atomic_b: bool = False,
        atomic_leaf_id: str = "LEAF-B",
        memory_mode: str = "external",
    ) -> None:
        self.root = root
        self.coord = root / "coordination"
        self.tasks = self.coord / "tasks" / REPO
        self.code = root / "code"
        self.memory = self.coord / "memory-repos" / f"ar-{REPO}"
        self.memory_mode = memory_mode
        self.atomic_b = atomic_b
        code_base = init_repo(self.code, "main")
        memory_content = init_repo(self.memory, "main")
        configured_code = root / REPO
        configured_code.symlink_to(self.code, target_is_directory=True)
        if memory_mode == "internal":
            (self.code / "ar-memory").mkdir()
        self.config_path = root / "settings.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "coordinationRoot": self.coord.as_posix(),
                    "workspaceRoot": root.as_posix(),
                    "repositories": {REPO: {}},
                }
            ),
            encoding="utf-8",
        )
        write_ledger(
            self.memory / "memory.md", create_initial_ledger(REPO, code_base, memory_content)
        )
        git(self.memory, "add", "memory.md")
        git(self.memory, "commit", "-m", "Add ledger")
        memory_base = git(self.memory, "rev-parse", "HEAD")
        git(self.code, "branch", "super", code_base)
        git(self.memory, "branch", "super", memory_base)
        atomic_leaf_ref = TaskDocumentRef(
            repository=REPO,
            path=f"master-b/{atomic_leaf_id.lower()}.json",
        )
        self.leaf_refs = {MASTER_A: LEAF_A, MASTER_B: atomic_leaf_ref}
        self.contracts = {
            MASTER_A: self._contract("master-a", "LEAF-A", code_base, memory_base),
            MASTER_B: self._contract("master-b", atomic_leaf_id, code_base, memory_base),
        }
        self.master_docs = {
            MASTER_A: _master(MASTER_A, "LEAF-A", "organizational"),
            MASTER_B: _master(
                MASTER_B,
                atomic_leaf_id,
                "atomic" if atomic_b else "organizational",
            ),
        }
        for ref, document in self.master_docs.items():
            write_task_doc(self.tasks / Path(ref.path).parent, document)
        graph = {
            "nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()],
            "edges": (
                [
                    {
                        "predecessor": MASTER_A.model_dump(),
                        "successor": MASTER_B.model_dump(),
                        "reason": "A supplies B.",
                    }
                ]
                if edge
                else []
            ),
        }
        rows = [
            _judgment_row(candidate, priority)
            for candidate in self.leaf_refs.values()
            for priority in ("low", "normal", "critical")
        ]
        self.priorities = {candidate: "normal" for candidate in self.leaf_refs.values()}
        priorities = [_priority_row(*item) for item in self.priorities.items()]
        write_task_doc(
            self.tasks / "sprint",
            TaskDocument.model_validate(
                {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Sprint",
                    "kind": "master",
                    "status": "inProgress",
                    "repo": REPO,
                    "createdAt": NOW,
                    "orchestrates": ["master-a", "master-b"],
                    "integrationBranch": "super",
                    "executionGraph": graph,
                    "sections": [
                        {
                            "kind": "freeform",
                            "heading": JUDGMENT_HEADING,
                            "body": _judgment_table(rows),
                        },
                        {
                            "kind": "freeform",
                            "heading": PRIORITY_HEADING,
                            "body": _priority_table(priorities),
                        },
                    ],
                }
            ),
        )
        (self.tasks / "sprint" / "grade.md").write_text("# Grade\n", encoding="utf-8")
        configured_memory = (
            self.memory
            if memory_mode == "external"
            else self.code / "ar-memory"
            if memory_mode == "internal"
            else None
        )
        self.cfg = _config(self.coord, self.code, configured_memory)
        self.request_number = 0
        self.request_revisions: dict[str, int] = {}

    def _contract(
        self, master: str, leaf_id: str, code_base: str, memory_base: str
    ) -> WorktreeContract:
        atomic = self.atomic_b and master == "master-b"
        code_source = f"ar/{master}" if atomic else "super"
        memory_source = f"ar/{master}" if atomic else "super"
        code_work = f"ar/{leaf_id.lower()}"
        memory_work = f"ar/{leaf_id.lower()}"
        if atomic:
            git(self.code, "branch", code_source, "super")
            git(self.memory, "branch", memory_source, "super")
        task = ContractTask(
            name=master,
            repo_name=REPO,
            coordination_root=self.coord,
            workflow_kind="light-task",
            memory_mode=self.memory_mode,
            parent_task_name="sprint" if atomic else "",
        )
        memory_plan = (
            RepoBranchPlan(
                repo_path=self.memory,
                source_branch="super" if atomic else "main",
                work_branch=memory_source,
                base_commit=memory_base,
            )
            if self.memory_mode == "external"
            else None
        )
        parent = default_series_contract(
            task,
            code=RepoBranchPlan(
                repo_path=self.code,
                source_branch="super" if atomic else "main",
                work_branch=code_source,
                base_commit=code_base,
            ),
            memory=memory_plan,
        )
        write_contract(
            parent.contract_path,
            parent if atomic else replace(parent, cleanup="completed"),
        )
        contract = default_contract(
            task,
            leaf=LeafIdentity(worktree_name=leaf_id.lower(), leaf_id=leaf_id),
            code=RepoBranchPlan(
                repo_path=self.code,
                source_branch=code_source,
                work_branch=code_work,
                base_commit=code_base,
            ),
            memory=(
                RepoBranchPlan(
                    repo_path=self.memory,
                    source_branch=memory_source,
                    work_branch=memory_work,
                    base_commit=memory_base,
                )
                if self.memory_mode == "external"
                else None
            ),
        )
        if atomic:
            contract = replace(
                contract,
                parent_task_name=master,
                parent_contract_path=parent.contract_path,
            )
        git(self.code, "worktree", "add", "-b", code_work, str(contract.code_worktree), code_source)
        (contract.code_worktree / "feature.txt").write_text(f"{leaf_id}\n", encoding="utf-8")
        if contract.memory_worktree is not None:
            git(
                self.memory,
                "worktree",
                "add",
                "-b",
                memory_work,
                str(contract.memory_worktree),
                memory_source,
            )
            (contract.memory_worktree / f"{leaf_id.lower()}.md").write_text(
                f"# {leaf_id}\n", encoding="utf-8"
            )
            _write_curator_evidence(contract)
        write_task_doc(contract.task_root, _leaf(contract, leaf_id.lower()))
        write_contract(contract.contract_path, contract)
        return contract

    def next_request_id(self, prefix: str = "request") -> str:
        self.request_number += 1
        return f"{prefix}-{self.request_number}"

    def request_revision(self, request_id: str) -> int:
        if request_id not in self.request_revisions:
            self.request_revisions[request_id] = int(self.status()["revision"])
        return self.request_revisions[request_id]

    def set_priority(self, candidate: TaskDocumentRef, priority: str) -> None:
        self.priorities[candidate] = priority
        path = self.tasks / "sprint" / "task.json"
        sprint = read_task_doc(path)
        sections = [
            section.model_copy(
                update={
                    "body": _priority_table(
                        [_priority_row(ref, value) for ref, value in self.priorities.items()]
                    )
                }
            )
            if section.heading == PRIORITY_HEADING
            else section
            for section in sprint.sections
        ]
        write_task_doc(path.parent, sprint.model_copy(update={"sections": sections}))

    def replace_section_body(self, heading: str, body: str) -> None:
        path = self.tasks / "sprint" / "task.json"
        sprint = read_task_doc(path)
        if heading == JUDGMENT_HEADING and not body.lstrip().startswith(JUDGMENT_HEADER):
            body = _judgment_table(body.splitlines())
        if heading == PRIORITY_HEADING and not body.lstrip().startswith(PRIORITY_HEADER):
            body = _priority_table(body.splitlines())
        sections = [
            section.model_copy(update={"body": body}) if section.heading == heading else section
            for section in sprint.sections
        ]
        write_task_doc(path.parent, sprint.model_copy(update={"sections": sections}))

    def declare(
        self,
        master: TaskDocumentRef,
        *,
        priority: str | None = "normal",
        admission: dict[str, Any] | None = None,
        request_id: str | None = None,
        update_priority: bool = True,
    ) -> dict[str, Any]:
        contract = self.contracts[master]
        leaf = self.leaf_refs[master]
        if priority is not None and update_priority:
            self.set_priority(leaf, priority)
        stable_request_id = request_id or self.next_request_id("declare")
        result = closeout_queue_tool(
            self.cfg,
            CloseoutQueueRequest.model_validate(
                {
                    "action": "declare",
                    "sprint_task_document_ref": SPRINT.model_dump(),
                    "request_id": stable_request_id,
                    "expected_revision": self.request_revision(stable_request_id),
                    "contract_path": contract.contract_path.as_posix(),
                    "admission": admission,
                }
            ),
            actor=QueueActor(role="manager", task_document_ref=master),
            now=NOW,
        )
        self.contracts[master] = load_contract(contract.contract_path)
        if priority is not None:
            return self.mutate(
                "set-grade",
                candidate=leaf,
                grade=_grade(priority, leaf),
                request_id=f"{stable_request_id}-grade",
                update_priority=update_priority,
            )
        return result

    def mutate(self, action: str, **values: Any) -> dict[str, Any]:
        candidate = cast(TaskDocumentRef | None, values.get("candidate"))
        barrier = cast(TaskDocumentRef | None, values.get("barrier"))
        if action == "set-grade" and candidate is not None and values.get("update_priority", True):
            grade = cast(dict[str, Any], values["grade"])
            self.set_priority(candidate, cast(str, grade["priority"]))
        stable_request_id = cast(str | None, values.get("request_id")) or self.next_request_id(
            action
        )
        return closeout_queue_tool(
            self.cfg,
            CloseoutQueueRequest.model_validate(
                {
                    "action": action,
                    "sprint_task_document_ref": SPRINT.model_dump(),
                    "request_id": stable_request_id,
                    "expected_revision": self.request_revision(stable_request_id),
                    "candidate_task_document_ref": (candidate.model_dump() if candidate else None),
                    "barrier_master_ref": barrier.model_dump() if barrier else None,
                    "grade": cast(dict[str, Any] | None, values.get("grade")),
                    "admission": cast(dict[str, Any] | None, values.get("admission")),
                    "barrier_judgment_id": cast(str | None, values.get("barrier_judgment_id")),
                    "rationale": (
                        cast(str, values.get("rationale", "reason"))
                        if action in {"acquire-barrier", "release-barrier"}
                        else ""
                    ),
                }
            ),
            actor=(
                QueueActor(
                    role="manager",
                    task_document_ref=next(
                        master for master, leaf in self.leaf_refs.items() if candidate == leaf
                    ),
                )
                if action == "set-admission" and candidate is not None
                else QueueActor(role="orchestrator", task_document_ref=SPRINT)
            ),
            now=NOW,
        )

    def status(self, actor: QueueActor | None = None) -> dict[str, Any]:
        caller = actor or QueueActor(role="orchestrator", task_document_ref=SPRINT)
        return closeout_queue_tool(
            self.cfg,
            CloseoutQueueRequest(action="status", sprint_task_document_ref=SPRINT),
            actor=caller,
            now=NOW,
        )

    def close_contract(self, master: TaskDocumentRef) -> WorktreeContract:
        contract = self.contracts[master]
        assert contract.memory_worktree is not None
        assert contract.ledger_path is not None
        git(contract.code_worktree, "add", "-A")
        git(contract.code_worktree, "commit", "-m", "close code")
        code_commit = git(contract.code_worktree, "rev-parse", "HEAD")
        git(contract.memory_worktree, "add", "-A")
        git(contract.memory_worktree, "commit", "-m", "close memory")
        memory_commit = git(contract.memory_worktree, "rev-parse", "HEAD")
        write_ledger(
            contract.ledger_path,
            prepend_mapping(load_ledger(contract.ledger_path), code_commit, memory_commit),
        )
        git(contract.memory_worktree, "add", "memory.md")
        git(contract.memory_worktree, "commit", "-m", "close ledger")
        ledger_commit = git(contract.memory_worktree, "rev-parse", "HEAD")
        closed = replace(
            contract,
            human_review_status="approved",
            approved_for_commit=True,
            closeout_status="completed",
            code_commit=code_commit,
            memory_content_commit=memory_commit,
            ledger_commit=ledger_commit,
        )
        write_contract(closed.contract_path, closed)
        self.contracts[master] = closed
        return closed


class CloseoutQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_explicit_grades_order_ready_candidates_by_graph_then_leaf_tie(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        first = fixture.declare(MASTER_A, priority="low")
        self.assertEqual(first["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())
        ranked = fixture.declare(MASTER_B, priority="critical")
        self.assertEqual(
            [item["taskDocumentRef"] for item in ranked["ready"]],
            [LEAF_B.model_dump(), LEAF_A.model_dump()],
        )
        with self.assertRaisesRegex(CloseoutQueueError, "first deterministic ready"):
            fixture.mutate("select", candidate=LEAF_A)
        tied = fixture.mutate("set-grade", candidate=LEAF_A, grade=_grade("critical", LEAF_A))
        self.assertEqual(
            [item["taskDocumentRef"] for item in tied["ready"]],
            [LEAF_A.model_dump(), LEAF_B.model_dump()],
        )
        waiting = fixture.mutate(
            "set-admission",
            candidate=LEAF_A,
            admission={"resourceReady": False, "resourceReason": "runner busy"},
        )
        self.assertEqual(waiting["waiting"][0]["reasons"], ["resource-unavailable: runner busy"])

    def test_ungraded_candidates_are_visible_but_cannot_be_selected(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        declared = fixture.declare(MASTER_A, priority=None)
        self.assertEqual(declared["waiting"][0]["reasons"], ["explicit-grade-required"])
        with self.assertRaisesRegex(CloseoutQueueError, "first deterministic ready"):
            fixture.mutate("select", candidate=LEAF_A)

    def test_internal_and_disabled_memory_modes_use_explicit_not_applicable_readiness(self) -> None:
        for mode in ("internal", "disabled"):
            with self.subTest(mode=mode):
                fixture = QueueFixture(Path(self.temp.name) / mode, memory_mode=mode)
                declared = fixture.declare(MASTER_A)
                self.assertEqual(declared["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())
                state_path, _pending_path = queue_store_paths(fixture.coord, SPRINT)
                state = CloseoutQueueState.model_validate_json(
                    state_path.read_text(encoding="utf-8")
                )
                candidate = state.candidates[LEAF_A.key]
                self.assertEqual(candidate.memoryMode, mode)
                self.assertEqual(candidate.memoryReadiness, "not-applicable")
                self.assertEqual(candidate.memoryEvidence, [])

    def test_request_shape_and_persisted_text_are_bounded(self) -> None:
        with self.assertRaisesRegex(ValidationError, "forbidden"):
            CloseoutQueueRequest(
                action="declare",
                sprint_task_document_ref=SPRINT,
                request_id="declare-grade-bypass",
                expected_revision=0,
                contract_path="/tmp/series-contract.md",
                grade=SchedulingGradeInput.model_validate(_grade("normal", LEAF_A)),
            )
        with self.assertRaisesRegex(ValidationError, "forbidden"):
            CloseoutQueueRequest(
                action="withdraw",
                sprint_task_document_ref=SPRINT,
                request_id="withdraw-1",
                expected_revision=0,
                candidate_task_document_ref=LEAF_A,
                grade=SchedulingGradeInput.model_validate(_grade("normal", LEAF_A)),
            )
        with self.assertRaisesRegex(ValidationError, "256"):
            CloseoutQueueRequest(
                action="withdraw",
                sprint_task_document_ref=SPRINT,
                request_id="x" * 257,
                expected_revision=0,
                candidate_task_document_ref=LEAF_A,
            )
        with self.assertRaisesRegex(ValidationError, "8192"):
            CloseoutQueueRequest(
                action="acquire-barrier",
                sprint_task_document_ref=SPRINT,
                request_id="barrier-1",
                expected_revision=0,
                barrier_master_ref=MASTER_B,
                rationale="x" * 8193,
            )

    def test_only_strategist_or_orchestrator_rows_can_author_queue_grades(self) -> None:
        for author in ("worker", "manager"):
            with self.subTest(author=author):
                fixture = QueueFixture(Path(self.temp.name) / author)
                sprint_path = fixture.tasks / "sprint" / "task.json"
                sprint = read_task_doc(sprint_path)
                judgment = next(
                    section for section in sprint.sections if section.heading == JUDGMENT_HEADING
                )
                original = _judgment_row(LEAF_A, "normal")
                fixture.replace_section_body(
                    JUDGMENT_HEADING,
                    judgment.body.replace(original, original.replace("orchestrator", author)),
                )
                with self.assertRaisesRegex(CloseoutQueueError, "strategist/orchestrator"):
                    fixture.declare(MASTER_A)

    def test_predecessors_and_atomic_barrier_control_logistics_not_judgment(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), edge=True, atomic_b=True)
        fixture.declare(MASTER_A)
        queued = fixture.declare(MASTER_B)
        self.assertEqual(
            queued["waiting"][0]["reasons"],
            [f"predecessor-incomplete: {MASTER_A.key}", "atomic-barrier-required"],
        )
        completed = fixture.master_docs[MASTER_A].model_copy(update={"status": "Completed"})
        write_task_doc(fixture.tasks / "master-a", completed)
        acquired = fixture.mutate(
            "acquire-barrier", barrier=MASTER_B, rationale="Sequential framework block."
        )
        self.assertEqual(acquired["ready"][0]["taskDocumentRef"], LEAF_B.model_dump())
        selected = fixture.mutate("select", candidate=LEAF_B)
        self.assertEqual(selected["inFlight"][0]["candidateState"], "selected")
        with self.assertRaisesRegex(CloseoutQueueError, "candidates"):
            fixture.mutate("release-barrier", barrier=MASTER_B)
        fixture.mutate("release-selection", candidate=LEAF_B)
        with self.assertRaisesRegex(CloseoutQueueError, "candidates"):
            fixture.mutate("release-barrier", barrier=MASTER_B)
        fixture.mutate("withdraw", candidate=LEAF_B)
        with self.assertRaisesRegex(CloseoutQueueError, "completion edge"):
            fixture.mutate("release-barrier", barrier=MASTER_B)
        completed_atomic = fixture.master_docs[MASTER_B].model_copy(
            update={
                "status": "Completed",
                "subTasks": [
                    row.model_copy(update={"status": "Completed"})
                    for row in fixture.master_docs[MASTER_B].subTasks
                ],
            }
        )
        write_task_doc(fixture.tasks / "master-b", completed_atomic)
        with self.assertRaisesRegex(CloseoutQueueError, "does not prove one exact"):
            fixture.mutate("release-barrier", barrier=MASTER_B)
        with mock.patch(
            "agents_remember.worktrees.closeout_queue.require_atomic_master_landed"
        ) as landed:
            released = fixture.mutate("release-barrier", barrier=MASTER_B)
        landed.assert_called_once()
        self.assertEqual(landed.call_args.args[0].ref, MASTER_B)
        self.assertIsNone(released["activeBarrier"])

    def test_atomic_barrier_abort_requires_exact_canonical_judgment(self) -> None:
        fixture = QueueFixture(Path(self.temp.name), atomic_b=True)
        fixture.mutate(
            "acquire-barrier", barrier=MASTER_B, rationale="Isolate the framework block."
        )
        with self.assertRaisesRegex(ValidationError, "barrier_judgment_id"):
            CloseoutQueueRequest(
                action="abort-barrier",
                sprint_task_document_ref=SPRINT,
                request_id="abort-1",
                expected_revision=fixture.status()["revision"],
                barrier_master_ref=MASTER_B,
            )
        sprint_path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        judgment = next(
            section for section in sprint.sections if section.heading == JUDGMENT_HEADING
        )
        graph_revision = fixture.status()["graphRevision"]
        fixture.replace_section_body(
            JUDGMENT_HEADING,
            judgment.body
            + "\n"
            + (
                f"| J-abort-master-b | atomic-barrier-abort | {MASTER_B.key} | "
                f"barrier=abort; graphRevision={graph_revision} | Experiment failed safely. | "
                "grade.md | strategist | high | |"
            ),
        )
        aborted = fixture.mutate(
            "abort-barrier",
            barrier=MASTER_B,
            barrier_judgment_id="J-abort-master-b",
        )
        self.assertIsNone(aborted["activeBarrier"])

    def test_candidate_and_evidence_drift_fail_closed(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_A)
        require_queue_candidate_current(fixture.coord, SPRINT, LEAF_A)
        contract = fixture.contracts[MASTER_A]
        (contract.code_worktree / "feature.txt").write_text("changed\n", encoding="utf-8")
        self.assertIn("candidate-tree-stale", fixture.status()["blocked"][0]["reasons"])
        (contract.code_worktree / "feature.txt").write_text("LEAF-A\n", encoding="utf-8")
        report = contract.worktree_group / "reports" / "curator-memory-quality.md"
        report.write_text(_curator_report() + "\nchanged\n", encoding="utf-8")
        self.assertIn("memory-readiness-evidence-stale", fixture.status()["blocked"][0]["reasons"])

    def test_declared_contract_cannot_bypass_queue_after_topology_or_state_loss(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_A)
        fixture.mutate("select", candidate=LEAF_A)
        contract = load_contract(fixture.contracts[MASTER_A].contract_path)
        sprint_path = fixture.tasks / "sprint" / "task.json"
        sprint = read_task_doc(sprint_path)
        write_task_doc(sprint_path.parent, sprint.model_copy(update={"executionGraph": None}))
        with self.assertRaisesRegex(CloseoutQueueError, "lost its executionGraph"):
            claim_queue_candidate_for_closeout(contract, "a" * 64)

        write_task_doc(sprint_path.parent, sprint)
        state_path, _pending_path = queue_store_paths(fixture.coord, SPRINT)
        state_path.unlink()
        with self.assertRaisesRegex(CloseoutQueueError, "closeout-candidate-not-declared"):
            claim_queue_candidate_for_closeout(contract, "a" * 64)

    def test_grade_judgment_and_evidence_are_revalidated(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_A)
        (fixture.tasks / "sprint" / "grade.md").write_text("changed\n", encoding="utf-8")
        self.assertIn("grade-evidence-stale", fixture.status()["blocked"][0]["reasons"])

    def test_grade_requires_exact_priority_subject_and_optional_signals(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.set_priority(LEAF_A, "normal")
        fixture.replace_section_body(
            PRIORITY_HEADING,
            "\n".join(
                [
                    _priority_row(
                        TaskDocumentRef(repository=REPO, path=f"{LEAF_A.path}.longer.json"),
                        "normal",
                    ),
                    _priority_row(LEAF_B, "normal"),
                ]
            ),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "absent from the canonical"):
            fixture.declare(MASTER_A, update_priority=False)

        fixture = QueueFixture(Path(self.temp.name) / "signals")
        rows = [
            _judgment_row(candidate, priority)
            for candidate in (LEAF_A, LEAF_B)
            for priority in ("low", "normal", "critical")
        ]
        rows = [
            row.replace("priority=normal |", "priority=normal; urgency=high |")
            if "J-leaf-a-normal" in row
            else row
            for row in rows
        ]
        fixture.replace_section_body(JUDGMENT_HEADING, "\n".join(rows))
        with self.assertRaisesRegex(CloseoutQueueError, "urgency"):
            fixture.declare(MASTER_A)

    def test_in_flight_candidate_is_immutable_and_owned_release_is_exact(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_A)
        fixture.mutate("select", candidate=LEAF_A)
        for action, values in (
            ("withdraw", {}),
            ("set-grade", {"grade": _grade("low", LEAF_A)}),
            ("set-admission", {"admission": {}}),
        ):
            with self.subTest(action=action), self.assertRaisesRegex(CloseoutQueueError, "frozen"):
                fixture.mutate(action, candidate=LEAF_A, **values)
        with self.assertRaisesRegex(CloseoutQueueError, "existing candidate"):
            fixture.declare(MASTER_A)
        key = "a" * 64
        claim_queue_candidate_for_closeout(fixture.contracts[MASTER_A], key)
        with self.assertRaisesRegex(CloseoutQueueError, "task-addressed cancellation"):
            fixture.mutate("release-selection", candidate=LEAF_A)
        release_queue_candidate_after_reversible_operation(
            fixture.contracts[MASTER_A],
            operation_key=key,
            operation_kind="closeout",
        )
        self.assertEqual(fixture.status()["ready"][0]["candidateState"], "declared")

    def test_declaration_refuses_incomplete_task_and_non_authoritative_curator_file(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        contract = fixture.contracts[MASTER_A]
        leaf = _leaf(contract, "leaf-a").model_copy(
            update={"steps": [Step(id="S1", title="Ready", status="pending")]}
        )
        write_task_doc(contract.task_root, leaf)
        with self.assertRaisesRegex(CloseoutQueueError, "unresolved work units"):
            fixture.declare(MASTER_A)
        write_task_doc(contract.task_root, _leaf(contract, "leaf-a"))
        _write_curator_evidence(
            contract,
            _curator_report().replace("ready-for-closeout", "action-required"),
        )
        with self.assertRaisesRegex(CloseoutQueueError, "ready-for-closeout contract"):
            fixture.declare(MASTER_A)

    def test_curator_attestation_refuses_ambiguous_status_and_undispositioned_sources(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        contract = fixture.contracts[MASTER_A]
        ambiguous = _curator_report().replace(
            "- Status: **ready-for-closeout**",
            "- Status: **action-required**\n- Status: **ready-for-closeout**",
        )
        _write_curator_evidence(contract, ambiguous)
        with self.assertRaisesRegex(CloseoutQueueError, "ready-for-closeout contract"):
            fixture.declare(MASTER_A)

        candidate = {
            "sourceFile": "mcp/src/changed.py",
            "onboardingFile": "onboarding/mcp/src/changed.py.md",
            "classification": "source_changed",
        }
        _write_curator_evidence(contract, source_candidates=[candidate])
        with self.assertRaisesRegex(CloseoutQueueError, "coherence report"):
            fixture.declare(MASTER_A)
        coherence = (
            contract.task_root / "notes" / "reports" / f"{contract.leaf_id}-curator-report.md"
        )
        coherence.parent.mkdir(parents=True, exist_ok=True)
        coherence.write_text(
            "mcp/src/changed.py and onboarding/mcp/src/changed.py.md are not dispositioned.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CloseoutQueueError, "disposition"):
            fixture.declare(MASTER_A)
        coherence.write_text(
            """# Curator report

## Source-change dispositions

| Source file | Onboarding file | Classification | Disposition | Evidence |
| --- | --- | --- | --- | --- |
| mcp/src/changed.py | onboarding/mcp/src/changed.py.md | source_changed | reconciled | onboarding diff |
""",
            encoding="utf-8",
        )
        declared = fixture.declare(MASTER_A)
        self.assertEqual(declared["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())

    def test_mutations_require_stable_request_id_and_retry_after_wal_publish(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        contract = fixture.contracts[MASTER_A]
        with self.assertRaisesRegex(ValidationError, "request_id"):
            CloseoutQueueRequest.model_validate(
                {
                    "action": "declare",
                    "sprint_task_document_ref": SPRINT.model_dump(),
                    "expected_revision": 0,
                    "contract_path": contract.contract_path.as_posix(),
                    "grade": _grade("normal", LEAF_A),
                }
            )
        original = __import__(
            "agents_remember.controlplane.closeout_queue_store", fromlist=["atomic_write_text"]
        ).atomic_write_text
        state_path, _pending_path = queue_store_paths(fixture.coord, SPRINT)
        failed = False

        def fail_state_once(path: Path, text: str) -> None:
            nonlocal failed
            if path == state_path and not failed:
                failed = True
                raise OSError("state publication interrupted")
            original(path, text)

        with (
            mock.patch(
                "agents_remember.controlplane.closeout_queue_store.atomic_write_text",
                side_effect=fail_state_once,
            ),
            self.assertRaisesRegex(OSError, "publication interrupted"),
        ):
            fixture.declare(MASTER_A, request_id="stable-declare")
        retried = fixture.declare(MASTER_A, request_id="stable-declare")
        self.assertEqual(retried["revision"], 2)
        with self.assertRaisesRegex(CloseoutQueueStoreError, "different payload"):
            fixture.mutate(
                "set-admission",
                candidate=LEAF_A,
                admission={"resourceReady": False, "resourceReason": "busy"},
                request_id="stable-declare",
            )

    def test_successful_noop_request_receipt_prevents_later_replay_mutation(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        first = fixture.mutate("withdraw", candidate=LEAF_A, request_id="noop-withdraw")
        self.assertEqual(first["revision"], 1)
        fixture.declare(MASTER_A)
        replayed = fixture.mutate("withdraw", candidate=LEAF_A, request_id="noop-withdraw")
        self.assertEqual(replayed["revision"], 3)
        self.assertEqual(replayed["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())

    def test_state_and_wal_are_strict_bounded_and_reclaimed_by_completed_sprint(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.mutate("withdraw", candidate=LEAF_A, request_id="ancient-noop")
        fixture.declare(MASTER_A)
        state_path, pending_path = queue_store_paths(fixture.coord, SPRINT)
        pending_path.write_text("{bad", encoding="utf-8")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "invalid pending"):
            fixture.status()
        pending_path.unlink()
        for number in range(260):
            fixture.mutate(
                "set-admission",
                candidate=LEAF_A,
                admission=(
                    {} if number % 2 else {"resourceReady": False, "resourceReason": "bounded"}
                ),
                request_id=f"bounded-{number}",
            )
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertLessEqual(len(persisted["appliedRequests"]), 128)
        self.assertFalse(pending_path.exists())
        with self.assertRaisesRegex(CloseoutQueueError, "revision-stale"):
            fixture.mutate("withdraw", candidate=LEAF_A, request_id="ancient-noop")
        self.assertEqual(fixture.status()["ready"][0]["taskDocumentRef"], LEAF_A.model_dump())
        sprint = fixture.status()["sprintTaskDocumentRef"]
        self.assertEqual(sprint, SPRINT.model_dump())
        fixture.mutate("withdraw", candidate=LEAF_A)
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        sprint_doc = TaskDocument.model_validate_json(
            (fixture.tasks / "sprint" / "task.json").read_text(encoding="utf-8")
        ).model_copy(update={"status": "Completed"})
        CloseoutQueueStore(fixture.coord, SPRINT).publish_sprint_update(
            lambda: write_task_doc(fixture.tasks / "sprint", sprint_doc),
            completed=True,
            recorded_at=NOW,
            validate_completion=lambda: None,
        )
        reclaimed = fixture.status()
        self.assertEqual(reclaimed["revision"], persisted["revision"] + 1)
        reclaimed_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(reclaimed_state["candidates"], {})
        self.assertEqual(reclaimed_state["appliedRequests"], [])
        self.assertTrue(reclaimed_state["closed"])

    def test_persisted_state_size_is_bounded_across_two_event_counts(self) -> None:
        def persisted_size(name: str, event_count: int) -> int:
            fixture = QueueFixture(Path(self.temp.name) / name)
            fixture.declare(MASTER_A)
            for number in range(event_count):
                fixture.mutate(
                    "set-admission",
                    candidate=LEAF_A,
                    admission=(
                        {} if number % 2 else {"resourceReady": False, "resourceReason": "bounded"}
                    ),
                    request_id=f"bounded-{number:04d}",
                )
            state_path, pending_path = queue_store_paths(fixture.coord, SPRINT)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(len(persisted["appliedRequests"]), 128)
            self.assertFalse(pending_path.exists())
            return state_path.stat().st_size

        shorter_history = persisted_size("small", 160)
        longer_history = persisted_size("large", 320)
        self.assertLessEqual(shorter_history, 32_768)
        self.assertLessEqual(longer_history, 32_768)
        self.assertLessEqual(abs(longer_history - shorter_history), 128)

    def test_projection_work_is_linear_across_two_candidate_fleet_sizes(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_A)
        state_path, _pending_path = queue_store_paths(fixture.coord, SPRINT)
        base_state = CloseoutQueueState.model_validate_json(state_path.read_text(encoding="utf-8"))
        base = base_state.candidates[LEAF_A.key]
        graph = _graph_context(TaskDocumentTopology(fixture.coord), SPRINT)
        for size in (16, 64):
            candidates = {}
            for number in range(size):
                ref = TaskDocumentRef(
                    repository=REPO,
                    path=f"master-a/synthetic-{number:03d}.json",
                )
                candidates[ref.key] = base.model_copy(update={"taskDocumentRef": ref})
            state = base_state.model_copy(update={"candidates": candidates})
            with mock.patch(
                "agents_remember.worktrees.closeout_queue._candidate_blockers",
                return_value=[],
            ) as blockers:
                projected = _project_candidates(
                    TaskDocumentTopology(fixture.coord),
                    graph,
                    state,
                    QueueActor(role="orchestrator", task_document_ref=SPRINT),
                )
            self.assertEqual(blockers.call_count, size)
            self.assertEqual(len(projected["ready"]), size)

        too_many = {
            f"{REPO}/master-a/overflow-{number:03d}.json": base.model_copy(
                update={
                    "taskDocumentRef": TaskDocumentRef(
                        repository=REPO,
                        path=f"master-a/overflow-{number:03d}.json",
                    )
                }
            )
            for number in range(257)
        }
        with self.assertRaisesRegex(ValidationError, "256"):
            CloseoutQueueState.model_validate(
                {**base_state.model_dump(mode="json"), "candidates": too_many}
            )

    def test_store_refuses_cross_sprint_initial_and_state(self) -> None:
        root = Path(self.temp.name)
        initial = CloseoutQueueState(
            sprintTaskDocumentRef=SPRINT,
            revision=0,
            graphRevision="0" * 64,
            candidates={},
            updatedAt=NOW,
        )
        store = CloseoutQueueStore(root, SPRINT)
        other = TaskDocumentRef(repository=REPO, path="other/task.json")
        with self.assertRaisesRegex(CloseoutQueueStoreError, "initial"):
            store.read(initial.model_copy(update={"sprintTaskDocumentRef": other}))
        store.state_path.parent.mkdir(parents=True, exist_ok=True)
        store.state_path.write_text(
            initial.model_copy(update={"sprintTaskDocumentRef": other}).model_dump_json(),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(CloseoutQueueStoreError, "different sprint"):
            store.read(initial)

    def test_sprint_completion_publication_is_serialized_with_queue_quiescence(self) -> None:
        fixture = QueueFixture(Path(self.temp.name))
        fixture.declare(MASTER_A)
        store = CloseoutQueueStore(fixture.coord, SPRINT)
        published: list[str] = []
        with self.assertRaisesRegex(CloseoutQueueStoreError, "cannot complete"):
            store.publish_sprint_update(
                lambda: published.append("completed"),
                completed=True,
                recorded_at=NOW,
                validate_completion=lambda: None,
            )
        self.assertEqual(published, [])
        fixture.mutate("withdraw", candidate=LEAF_A)
        sprint_path = fixture.tasks / "sprint" / "task.json"
        completed_sprint = read_task_doc(sprint_path).model_copy(update={"status": "Completed"})
        store.publish_sprint_update(
            lambda: (
                write_task_doc(sprint_path.parent, completed_sprint),
                published.append("completed"),
            ),
            completed=True,
            recorded_at=NOW,
            validate_completion=lambda: None,
        )
        self.assertEqual(published, ["completed"])
        state_path, pending_path = queue_store_paths(fixture.coord, SPRINT)
        state = CloseoutQueueState.model_validate_json(state_path.read_text(encoding="utf-8"))
        self.assertTrue(state.closed)
        self.assertFalse(pending_path.exists())
