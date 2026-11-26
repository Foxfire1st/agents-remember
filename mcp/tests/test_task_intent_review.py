"""MIK-R189: an intent review stands between a new leaf and its first start.

The leaf field ``intentReview`` holds the architect's record of the developer's answer. While it
reads ``pending`` or ``review-first`` every start of the leaf is refused with
``intent-review-open`` -- in ``worktree_start``, in ``worktree_attach``, in every leaf role's
``_resolve_workspace`` and at the stage move itself -- and nothing is created before the refusal.
A leaf without the field is an older leaf and is admitted. A leaf role's start that found a live
enclosure makes the planning -> ``inProgress`` move as its last act, so the first seat's copies
already read ``inProgress``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.cli import role_launch_workspace
from agents_remember.cli.role_launch_workspace import _resolve_workspace
from agents_remember.errors import RolePreparationError
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.document import (
    INTENT_REVIEW_REFUSAL,
    IntentReview,
    IntentReviewRevision,
    intent_review_open,
    intent_review_refusal,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.start import attach_result, start_result
from agents_remember.worktrees.task_stage import plan_task_stage
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    contract_publication_text,
    default_contract,
    default_series_contract,
    write_contract,
)
from test_task_document import _config
from test_worktree_support import git, init_repo

REPO = "repo-a"
MASTER_NAME = "intent-master"
MASTER_ID = "TEST-INTENT-MASTER"
LEAF_ID = "TEST-INTENT-LEAF"
LEAF_SLUG = "test-intent-leaf"
OTHER_ID = "TEST-INTENT-OTHER"
OTHER_SLUG = "test-intent-other"
LEAF_TASK_ID = "TESTDOC-LEAF"

GO = {"state": "go", "decisionRef": "D369"}
PENDING = {"state": "pending"}
REVIEW_FIRST = {"state": "review-first"}


def _leaf_doc(
    leaf_id: str = LEAF_ID,
    slug: str = LEAF_SLUG,
    *,
    intent_review: dict[str, Any] | None = None,
    status: str = "planning",
    waits_for: list[str] | None = None,
) -> TaskDocument:
    data: dict[str, Any] = {
        "id": leaf_id,
        "slug": slug,
        "title": f"Leaf {leaf_id}",
        "kind": "subTask",
        "status": status,
        "repo": REPO,
        "createdAt": "2026-10-19T00:00:00+00:00",
        "objective": "Do the work.",
        "master": "task.md",
    }
    if waits_for is not None:
        data["waitsFor"] = waits_for
    if intent_review is not None:
        data["intentReview"] = intent_review
    return TaskDocument.model_validate(data)


def _master_doc(rows: list[SubTaskRef]) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": MASTER_ID,
            "slug": MASTER_NAME,
            "title": "Intent master",
            "kind": "master",
            "status": "planning",
            "repo": REPO,
            "createdAt": "2026-10-19T00:00:00+00:00",
            "executionNature": "atomic",
            "subTasks": [row.model_dump(mode="json") for row in rows],
        }
    )


def _row(number: str, slug: str, status: Any = "planning") -> SubTaskRef:
    return SubTaskRef(number=number, name=f"Leaf {number}", file=f"{slug}.md", status=status)


def _fixture(root: Path) -> SimpleNamespace:
    """One master, two leaves and a code+memory repository pair under a coordination root."""

    coord = root / "coord"
    task_root = coord / "tasks" / REPO / MASTER_NAME
    task_root.mkdir(parents=True)
    write_task_doc(task_root, _master_doc([_row(LEAF_ID, LEAF_SLUG), _row(OTHER_ID, OTHER_SLUG)]))
    write_task_doc(task_root, _leaf_doc())
    write_task_doc(task_root, _leaf_doc(OTHER_ID, OTHER_SLUG))
    code = root / REPO
    init_repo(code, "main")
    (code / "file.txt").write_text("code\n", encoding="utf-8")
    git(code, "add", ".")
    git(code, "commit", "-m", "base")
    memory = coord / "memory-repos" / f"ar-{REPO}"
    init_repo(memory, "main")
    (memory / "memory.md").write_text("# memory\n", encoding="utf-8")
    git(memory, "add", ".")
    git(memory, "commit", "-m", "ledger")
    task = ContractTask(
        name=MASTER_NAME,
        repo_name=REPO,
        coordination_root=coord,
        workflow_kind="light-task",
        memory_mode="external",
        parent_task_name="sprint-parent",
    )
    series = default_series_contract(
        task,
        task_root=task_root,
        code=RepoBranchPlan(
            repo_path=code,
            source_branch="main",
            work_branch=f"ar/{MASTER_NAME}",
            base_commit="0" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=memory,
            source_branch="main",
            work_branch=f"ar/{MASTER_NAME}",
            base_commit="0" * 40,
        ),
    )
    series.worktree_group.mkdir(parents=True, exist_ok=True)
    write_contract(series.contract_path, series)
    publish_new_lifecycle_operation_location(
        series,
        contract_text=contract_publication_text(series.contract_path, series),
    )
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=coord,
        workspace_root=root,
        transcript_root=coord / "logs" / "mcp",
        repositories={REPO: RepositoryScope(repo_id=REPO, path=code, memory_root=memory)},
    )
    settings_path = root / "settings" / "ar.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "coordinationRoot": coord.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO: {}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return SimpleNamespace(
        config=config,
        coord=coord,
        task_root=task_root,
        task=task,
        code=code,
        memory=memory,
        series=series,
        root=root,
        settings_path=settings_path,
    )


def _rewrite_leaf(
    fixture: SimpleNamespace, document: TaskDocument, *, leaf_id: str = LEAF_ID
) -> None:
    write_task_doc(fixture.task_root, document)


def _leaf_contract(fixture: SimpleNamespace) -> Any:
    """A leaf contract at the canonical enclosure path, with its roots as real directories."""

    contract = default_contract(
        fixture.task,
        leaf=LeafIdentity(worktree_name=LEAF_SLUG, leaf_id=LEAF_ID),
        code=RepoBranchPlan(
            repo_path=fixture.code,
            source_branch="main",
            work_branch=f"ar/{LEAF_SLUG}",
            base_commit="0" * 40,
        ),
        memory=RepoBranchPlan(
            repo_path=fixture.memory,
            source_branch="main",
            work_branch=f"ar/{LEAF_SLUG}",
            base_commit="0" * 40,
        ),
    )
    contract.worktree_group.mkdir(parents=True, exist_ok=True)
    contract.code_worktree.mkdir(parents=True, exist_ok=True)
    if contract.memory_worktree is not None:
        contract.memory_worktree.mkdir(parents=True, exist_ok=True)
    write_contract(contract.contract_path, contract)
    publish_new_lifecycle_operation_location(
        contract,
        contract_text=contract_publication_text(contract.contract_path, contract),
    )
    return contract


def _context(
    fixture: SimpleNamespace,
    *,
    role: Any = "worker",
    leaf_id: str = LEAF_ID,
    slug: str = LEAF_SLUG,
) -> RoleLaunchContext:
    master = read_task_doc(fixture.task_root / "task.json")
    leaf = read_task_doc(fixture.task_root / f"{slug}.json")
    ref = TaskDocumentRef(repository=REPO, path=f"{MASTER_NAME}/{slug}.json")
    master_ref = TaskDocumentRef(repository=REPO, path=f"{MASTER_NAME}/task.json")
    resolved_master = ResolvedTaskDocument(
        ref=master_ref, path=fixture.task_root / "task.json", document=master
    )
    resolved_leaf = ResolvedTaskDocument(
        ref=ref, path=fixture.task_root / f"{slug}.json", document=leaf
    )
    return RoleLaunchContext(
        role=role,
        sprint=resolved_master,
        master=resolved_master,
        task=resolved_leaf,
        effective_task=resolved_leaf,
    )


def _start_args(fixture: SimpleNamespace, *, dry_run: bool = False) -> WorktreeArgs:
    return WorktreeArgs(
        coordination_root=fixture.coord,
        workspace_root=fixture.root,
        code_repository_root=fixture.code,
        code_repository_name=REPO,
        task_name=MASTER_NAME,
        worktree_name=LEAF_SLUG,
        leaf_id=LEAF_ID,
        parent_task=MASTER_NAME,
        dry_run=dry_run,
    )


def _attach_args(fixture: SimpleNamespace, contract: Any) -> WorktreeArgs:
    return WorktreeArgs(
        coordination_root=fixture.coord,
        workspace_root=fixture.root,
        code_repository_root=fixture.code,
        code_repository_name=REPO,
        contract_path=contract.contract_path,
    )


class IntentReviewModelTests(unittest.TestCase):
    def test_a_leaf_without_the_field_is_admitted_and_a_recorded_revision_rides_the_go(
        self,
    ) -> None:
        older = _leaf_doc()
        self.assertIsNone(older.intentReview)
        self.assertIsNone(intent_review_open(older))
        self.assertIsNone(intent_review_refusal(older))
        go = _leaf_doc(
            intent_review={
                **GO,
                "agreedRevision": {
                    "revisionId": "rev-7",
                    "sha256": "b" * 64,
                    "packetVersions": ["MIK-R189@v1"],
                },
            }
        )
        self.assertEqual(go.intentReview.state, "go")  # type: ignore[union-attr]
        self.assertIsNone(intent_review_open(go))
        # ``agreed`` is not a state: the revision is a separate field, written with the go.
        with self.assertRaisesRegex(ValueError, "agreedRevision is written with the go"):
            IntentReview.model_validate(
                {
                    "state": "review-first",
                    "agreedRevision": {"revisionId": "rev-7", "sha256": "b" * 64},
                }
            )
        with self.assertRaisesRegex(ValueError, "revision id must not be blank"):
            IntentReviewRevision.model_validate({"revisionId": " ", "sha256": "b" * 64})

    def test_a_master_carries_no_intent_review(self) -> None:
        with self.assertRaisesRegex(ValueError, "a master document has no"):
            TaskDocument.model_validate(
                {
                    "id": MASTER_ID,
                    "slug": MASTER_NAME,
                    "title": "Intent master",
                    "kind": "master",
                    "repo": REPO,
                    "createdAt": "2026-10-19T00:00:00+00:00",
                    "intentReview": PENDING,
                }
            )


class NewLeafDefaultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.coord = self.root / "coord"
        self.coord.mkdir()
        self.config = _config(self.coord)
        self._write_master()

    def _write_master(self) -> None:
        task_doc_tool(
            self.config,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="task"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "series",
                    "slug": "series",
                    "title": "Series",
                    "kind": "master",
                    "repo": "agents-remember",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
                }
            ),
        )

    def _write_row(self) -> None:
        task_doc_tool(
            self.config,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="task"),
            operation="set_subtask",
            edit=TaskDocEdit(
                subtask={
                    "number": LEAF_TASK_ID,
                    "name": "Smoke",
                    "file": "03c_x.md",
                    "status": "planning",
                }
            ),
        )

    def test_a_leaf_created_now_starts_pending_and_an_explicit_answer_is_kept(self) -> None:
        self._write_row()
        target = TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x")
        task_doc_tool(
            self.config,
            target,
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": LEAF_TASK_ID,
                    "slug": "03c_x",
                    "title": "Smoke",
                    "kind": "subTask",
                    "repo": "agents-remember",
                    "createdAt": "2026-01-01T00:00:00Z",
                }
            ),
        )
        stored = read_task_doc(self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json")
        self.assertEqual(stored.intentReview.state, "pending")  # type: ignore[union-attr]
        self.assertIn(
            "**Intent review:** pending",
            (self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.md").read_text(
                encoding="utf-8"
            ),
        )
        # A master never carries the field.
        master = read_task_doc(self.coord / "tasks" / "agents-remember" / "3c-x" / "task.json")
        self.assertIsNone(master.intentReview)


class IntentReviewKeepsTests(unittest.TestCase):
    """The field's one writer and its survival of ``set_field`` and ``replace``."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.coord = Path(self._tmp.name) / "coord"
        self.coord.mkdir()
        self.config = _config(self.coord)
        self.target = TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x")
        self.master_target = TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="task")
        task_doc_tool(
            self.config,
            self.master_target,
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "series",
                    "slug": "series",
                    "title": "Series",
                    "kind": "master",
                    "repo": "agents-remember",
                    "createdAt": "2026-01-01T00:00:00Z",
                    "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
                }
            ),
        )
        task_doc_tool(
            self.config,
            self.master_target,
            operation="set_subtask",
            edit=TaskDocEdit(
                subtask={
                    "number": LEAF_TASK_ID,
                    "name": "Smoke",
                    "file": "03c_x.md",
                    "status": "planning",
                }
            ),
        )
        self.fields: dict[str, Any] = {
            "id": LEAF_TASK_ID,
            "slug": "03c_x",
            "title": "Smoke",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-01-01T00:00:00Z",
        }
        task_doc_tool(
            self.config,
            self.target,
            operation="create",
            edit=TaskDocEdit(fields=dict(self.fields)),
        )

    @property
    def _json(self) -> Path:
        return self.coord / "tasks" / "agents-remember" / "3c-x" / "03c_x.json"

    def _write(self, operation: str, **kwargs: Any) -> dict[str, Any]:
        return task_doc_tool(self.config, self.target, operation=operation, **kwargs)

    def test_replace_keeps_the_field_it_omits_and_refuses_a_call_that_would_drop_it(self) -> None:
        self._write(
            "set_field",
            edit=TaskDocEdit(fields={"intentReview": GO}),
            call=TaskDocCall(),
        )
        self.assertEqual(read_task_doc(self._json).intentReview.state, "go")  # type: ignore[union-attr]
        # A replace that omits the field keeps it.
        self._write("replace", edit=TaskDocEdit(fields={**self.fields, "objective": "Changed."}))
        kept = read_task_doc(self._json)
        self.assertEqual(kept.intentReview.state, "go")  # type: ignore[union-attr]
        self.assertEqual(kept.objective, "Changed.")
        # A replace that would drop it is refused by name, and the document is untouched.
        before = self._json.read_bytes()
        before_md = self._json.with_suffix(".md").read_bytes()
        with self.assertRaisesRegex(TaskDocError, "cannot drop intentReview"):
            self._write("replace", edit=TaskDocEdit(fields={**self.fields, "intentReview": None}))
        self.assertEqual(self._json.read_bytes(), before)
        self.assertEqual(self._json.with_suffix(".md").read_bytes(), before_md)
        # set_field with intentReview=None is also refused for both identity-free and architect callers.
        with self.assertRaisesRegex(TaskDocError, "intent-review-erasure-refused"):
            self._write("set_field", edit=TaskDocEdit(fields={"intentReview": None}))
        self.assertEqual(self._json.read_bytes(), before)
        architect = AgentBinding(
            agent_id="01J" + "9" * 23,
            role="architect",
            request_id="01K" + "9" * 23,
            report_path=(self.coord / "architect-report.md").as_posix(),
        )
        with patch(
            "agents_remember.application.task_docs.task_doc_tools.read_agent_binding",
            return_value=architect,
        ):
            with self.assertRaisesRegex(TaskDocError, "intent-review-erasure-refused"):
                self._write("set_field", edit=TaskDocEdit(fields={"intentReview": None}))
            with self.assertRaisesRegex(TaskDocError, "intent-review-erasure-refused"):
                self._write(
                    "replace", edit=TaskDocEdit(fields={**self.fields, "intentReview": None})
                )
        self.assertEqual(self._json.read_bytes(), before)
        # True preexisting legacy absence (a leaf on disk without intentReview) remains admitted.
        write_task_doc(
            self._json.parent,
            TaskDocument.model_validate({**self.fields, "intentReview": None}),
        )
        self._write("set_field", edit=TaskDocEdit(fields={"objective": "Legacy leaf updated."}))
        legacy = read_task_doc(self._json)
        self.assertIsNone(legacy.intentReview)
        self.assertEqual(legacy.objective, "Legacy leaf updated.")

    def test_a_hosted_seat_other_than_the_architect_cannot_write_the_field(self) -> None:
        worker = AgentBinding(
            agent_id="01J" + "0" * 23,
            role="worker",
            request_id="01K" + "0" * 23,
            report_path=(self.coord / "report.md").as_posix(),
        )
        with patch(
            "agents_remember.application.task_docs.task_doc_tools.read_agent_binding",
            return_value=worker,
        ):
            with self.assertRaisesRegex(TaskDocError, "cannot write intentReview"):
                self._write("set_field", edit=TaskDocEdit(fields={"intentReview": GO}))
            # A write that leaves the field alone is not that seat's write at all.
            self._write("set_field", edit=TaskDocEdit(fields={"objective": "Worker text."}))
        # The developer's own identity-free call is admitted.
        self._write("set_field", edit=TaskDocEdit(fields={"intentReview": GO}))
        self.assertEqual(read_task_doc(self._json).intentReview.state, "go")  # type: ignore[union-attr]


class StageGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fixture = _fixture(self.root)

    def test_a_move_to_in_progress_is_refused_while_the_review_is_open(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=REVIEW_FIRST))
        contract = _leaf_contract(self.fixture)
        for status in ("planning", "abandoned"):
            _rewrite_leaf(self.fixture, _leaf_doc(intent_review=REVIEW_FIRST, status=status))
            with self.assertRaisesRegex(ValueError, INTENT_REVIEW_REFUSAL):
                plan_task_stage(contract, "inProgress")
            self.assertEqual(
                read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json").status, status
            )
            # Generic task_doc routes (set_status, set_field, replace) also refuse moving to
            # inProgress before publication and keep leaf + master byte-identical.
            leaf_path = self.fixture.task_root / f"{LEAF_SLUG}.json"
            master_path = self.fixture.task_root / "task.json"
            leaf_before = leaf_path.read_bytes()
            master_before = master_path.read_bytes()
            target = TaskDocTarget(repo_id=REPO, task_name=MASTER_NAME, slug=LEAF_SLUG)
            for op, fields in (
                ("set_status", {"status": "inProgress"}),
                ("set_field", {"status": "inProgress"}),
                (
                    "replace",
                    {
                        **_leaf_doc(intent_review=PENDING, status=status).model_dump(
                            mode="json", exclude_none=True
                        ),
                        "status": "inProgress",
                    },
                ),
            ):
                with self.assertRaisesRegex(TaskDocError, INTENT_REVIEW_REFUSAL):
                    task_doc_tool(
                        self.fixture.config,
                        target,
                        operation=op,
                        edit=TaskDocEdit(fields=fields),
                    )
                self.assertEqual(leaf_path.read_bytes(), leaf_before)
                self.assertEqual(master_path.read_bytes(), master_before)

    def test_a_replace_that_omits_the_field_still_cannot_move_a_pending_leaf(self) -> None:
        # The replace route rebuilds from the payload alone; while the review is open the
        # omitted field is kept and the move is refused by name, with nothing published.
        leaf_path = self.fixture.task_root / f"{LEAF_SLUG}.json"
        master_path = self.fixture.task_root / "task.json"
        for status in ("planning", "abandoned"):
            _rewrite_leaf(self.fixture, _leaf_doc(intent_review=PENDING, status=status))
            leaf_before = leaf_path.read_bytes()
            master_before = master_path.read_bytes()
            fields = _leaf_doc(status=status).model_dump(mode="json", exclude_none=True)
            fields["status"] = "inProgress"
            with self.assertRaisesRegex(TaskDocError, INTENT_REVIEW_REFUSAL):
                task_doc_tool(
                    self.fixture.config,
                    TaskDocTarget(repo_id=REPO, task_name=MASTER_NAME, slug=LEAF_SLUG),
                    operation="replace",
                    edit=TaskDocEdit(fields=fields),
                )
            self.assertEqual(leaf_path.read_bytes(), leaf_before)
            self.assertEqual(master_path.read_bytes(), master_before)
            self.assertEqual(read_task_doc(leaf_path).intentReview.state, "pending")  # type: ignore[union-attr]

    def test_a_leaf_without_the_field_and_a_leaf_with_the_go_are_admitted(self) -> None:
        contract = _leaf_contract(self.fixture)
        target = TaskDocTarget(repo_id=REPO, task_name=MASTER_NAME, slug=LEAF_SLUG)
        for review in (None, GO):
            _rewrite_leaf(self.fixture, _leaf_doc(intent_review=review))
            change = plan_task_stage(contract, "inProgress")
            self.assertTrue(change.documents)
            self.assertEqual(change.documents[0].status, "inProgress")
            for op in ("set_status", "set_field"):
                _rewrite_leaf(self.fixture, _leaf_doc(intent_review=review, status="planning"))
                result = task_doc_tool(
                    self.fixture.config,
                    target,
                    operation=op,
                    edit=TaskDocEdit(fields={"status": "inProgress"}),
                )
                self.assertTrue(result["ok"])
                self.assertEqual(
                    read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json").status,
                    "inProgress",
                )
            # The replace route admits the same two leaves, and one status-capable call can
            # record the go and the move together.
            _rewrite_leaf(self.fixture, _leaf_doc(intent_review=review, status="planning"))
            fields = _leaf_doc(status="planning").model_dump(mode="json", exclude_none=True)
            if review is not None:
                fields["intentReview"] = review
            fields["status"] = "inProgress"
            result = task_doc_tool(
                self.fixture.config,
                target,
                operation="replace",
                edit=TaskDocEdit(fields=fields),
            )
            self.assertTrue(result["ok"])
            self.assertEqual(
                read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json").status, "inProgress"
            )
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=PENDING, status="planning"))
        result = task_doc_tool(
            self.fixture.config,
            target,
            operation="set_field",
            edit=TaskDocEdit(fields={"status": "inProgress", "intentReview": GO}),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json").status, "inProgress"
        )


class WorktreeStartGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.fixture = _fixture(self.root)

    def test_a_start_is_refused_before_the_enclosure_is_created(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=PENDING))
        result = start_result(_start_args(self.fixture))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], INTENT_REVIEW_REFUSAL)
        self.assertIn(INTENT_REVIEW_REFUSAL, str(result.payload["detail"]))
        # Nothing was created: no leaf contract, no leaf worktree group.
        self.assertFalse(leaf_enclosure_path(self.fixture.task_root, LEAF_ID).exists())
        self.assertFalse(self.fixture.series.worktree_group.joinpath(LEAF_SLUG).exists())
        self.assertEqual(
            read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json").status, "planning"
        )

    def test_the_same_start_is_admitted_after_the_go_and_for_an_older_leaf(self) -> None:
        for review in (GO, None):
            _rewrite_leaf(self.fixture, _leaf_doc(intent_review=review))
            result = start_result(_start_args(self.fixture, dry_run=True))
            # The gate no longer answers: the start proceeds to its own next step.
            self.assertNotEqual(result.payload.get("state"), INTENT_REVIEW_REFUSAL)
            self.assertNotIn(INTENT_REVIEW_REFUSAL, json.dumps(result.payload))


class WorktreeAttachGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))

    def test_attaching_a_planning_leaf_is_refused_and_moves_nothing(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=PENDING))
        contract = _leaf_contract(self.fixture)
        before = (self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes()
        result = attach_result(_attach_args(self.fixture, contract))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.payload["state"], INTENT_REVIEW_REFUSAL)
        self.assertEqual((self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes(), before)


class RoleLaunchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))

    def test_every_leaf_role_is_refused_before_any_enclosure_is_made(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=PENDING))
        for role in ("worker", "reviewer", "curator"):
            with (
                patch.object(
                    role_launch_workspace,
                    "_ensure_leaf_enclosure",
                    side_effect=AssertionError("nothing may be prepared before the refusal"),
                ),
                self.assertRaises(RolePreparationError) as raised,
            ):
                _resolve_workspace(self.fixture.config, _context(self.fixture, role=role))
            self.assertEqual(raised.exception.status, INTENT_REVIEW_REFUSAL)
        self.assertFalse(leaf_enclosure_path(self.fixture.task_root, LEAF_ID).exists())

    def test_a_direct_leaf_is_refused_too(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=REVIEW_FIRST))
        with (
            patch.object(
                role_launch_workspace,
                "_resolve_direct_workspace",
                side_effect=AssertionError("a direct leaf start is refused before its checkouts"),
            ),
            self.assertRaises(RolePreparationError) as raised,
        ):
            _resolve_workspace(self.fixture.config, _context(self.fixture))
        self.assertEqual(raised.exception.status, INTENT_REVIEW_REFUSAL)


if __name__ == "__main__":
    unittest.main()
