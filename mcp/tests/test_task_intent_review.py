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

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import agents_remember
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
    resolve_agreed_revision,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.master_order import derive_master_order, load_master_children
from agents_remember.tasks.render import render_markdown
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    publish_new_lifecycle_operation_location,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.start import attach_result, start_result
from agents_remember.worktrees.task_leaf_binding import TaskLeafBindingError
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


def _found_status(contract: Any, *, preparation: str = "found") -> dict[str, Any]:
    return {
        "ok": True,
        "worktree_group": Path(contract.worktree_group).as_posix(),
        "code_worktree": Path(contract.code_worktree).as_posix(),
        "memory_worktree": Path(contract.memory_worktree).as_posix(),
        "enclosurePreparation": preparation,
    }


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
        skip_provider_setup=True,
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

    def test_role_capsule_sentences_and_departures_are_present_and_synced(self) -> None:
        checkout_root = Path(agents_remember.__file__).resolve().parents[3]
        skills_root = checkout_root / "skills" / "l-01-agent-lifecycles"
        packaged_root = (
            Path(agents_remember.__file__).resolve().parent
            / "package_data"
            / "runtime"
            / "skills"
            / "l-01-agent-lifecycles"
        )
        planning = (skills_root / "operations" / "planning.md").read_text(encoding="utf-8")
        architect = (skills_root / "roles" / "architect.md").read_text(encoding="utf-8")
        coordination = (skills_root / "operations" / "coordination.md").read_text(encoding="utf-8")
        # Sentence A + both up-front-workspace departures + preserved approval recovery.
        self.assertIn("go, or review first?", planning)
        self.assertIn("planned comparison", planning)
        self.assertIn("no task workspace", planning)
        self.assertIn("explainer workspace on first Explain", planning)
        self.assertIn("intentReview", planning)
        self.assertIn("intent-review-open", planning)
        self.assertIn("without asking again", planning)
        # Sentence B (copied developer SHA-256 match) and Sentence C (claim).
        self.assertIn("From explainer ·", architect)
        self.assertIn("SHA-256", architect)
        self.assertIn("developer-messages/", architect)
        self.assertIn("explainer_control", architect)
        self.assertIn("claim", architect)
        # Sentence D (Manager waits on intent-review-open and never retries).
        self.assertIn("intent-review-open", coordination)
        self.assertIn("never retry", coordination)
        for rel in (
            "operations/planning.md",
            "roles/architect.md",
            "operations/coordination.md",
        ):
            self.assertEqual(
                (skills_root / rel).read_bytes(),
                (packaged_root / rel).read_bytes(),
            )

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


class FoundEnclosureMoveTests(unittest.TestCase):
    """MIK-R189 v2: the found live enclosure makes the move as the last act of the resolution."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))
        self.contract = _leaf_contract(self.fixture)

    def _resolve(self, *, preparation: str = "found") -> dict[str, str]:
        status = _found_status(self.contract, preparation=preparation)
        with patch.object(
            role_launch_workspace,
            "_ensure_leaf_enclosure",
            return_value=(
                self.contract.contract_path,
                status,
            ),
        ):
            return _resolve_workspace(self.fixture.config, _context(self.fixture))

    def test_a_found_live_enclosure_moves_the_leaf_and_its_master_row(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=GO))
        workspace = self._resolve()
        self.assertEqual(workspace["enclosurePreparation"], "found")
        leaf = read_task_doc(self.fixture.task_root / f"{LEAF_SLUG}.json")
        self.assertEqual(leaf.status, "inProgress")
        # The found contract's identity is written onto the leaf with the stage.
        self.assertEqual(
            [item.enclosurePath for item in leaf.enclosures],
            [self.contract.contract_path.as_posix()],
        )
        master = read_task_doc(self.fixture.task_root / "task.json")
        self.assertEqual(
            [row.status for row in master.subTasks if row.number == LEAF_ID], ["inProgress"]
        )

    def test_a_refused_start_at_a_found_enclosure_moves_nothing(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=PENDING))
        leaf_before = (self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes()
        master_before = (self.fixture.task_root / "task.json").read_bytes()
        with self.assertRaises(RolePreparationError) as raised:
            self._resolve()
        self.assertEqual(raised.exception.status, INTENT_REVIEW_REFUSAL)
        self.assertEqual((self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes(), leaf_before)
        self.assertEqual((self.fixture.task_root / "task.json").read_bytes(), master_before)

    def test_a_created_enclosure_and_an_already_started_leaf_are_untouched(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=GO))
        leaf_before = (self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes()
        self._resolve(preparation="created")
        self.assertEqual((self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes(), leaf_before)
        # A leaf already inProgress writes nothing on a repeat start.
        plan_task_stage(self.contract, "inProgress").publish()
        started = (self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes()
        master_started = (self.fixture.task_root / "task.json").read_bytes()
        self._resolve()
        self.assertEqual((self.fixture.task_root / f"{LEAF_SLUG}.json").read_bytes(), started)
        self.assertEqual((self.fixture.task_root / "task.json").read_bytes(), master_started)

    def test_a_move_that_cannot_be_written_is_a_named_refusal(self) -> None:
        _rewrite_leaf(self.fixture, _leaf_doc(intent_review=GO))
        failure = TaskLeafBindingError(
            "the leaf enclosure binding is missing", status="task-enclosure-binding-missing"
        )
        with (
            patch.object(role_launch_workspace, "publish_start_task_facts", side_effect=failure),
            self.assertRaises(RolePreparationError) as raised,
        ):
            self._resolve()
        self.assertEqual(raised.exception.status, "task-enclosure-binding-missing")
        self.assertIn("No seat was made", raised.exception.next_action)

    def test_enclosure_copies_reference_folder_and_agreed_revision_excluding_binding_and_warns_on_error(
        self,
    ) -> None:
        ref_dir = self.fixture.task_root / "notes" / "reference" / LEAF_ID
        (ref_dir / "nested").mkdir(parents=True)
        (ref_dir / "mockup.md").write_text("# Mockup\n", encoding="utf-8")
        (ref_dir / "nested" / "spec.txt").write_text("nested spec\n", encoding="utf-8")
        (ref_dir / "binding.json").write_text('{"secret": true}\n', encoding="utf-8")
        rev_dir = (
            self.fixture.task_root
            / "notes"
            / "reports"
            / "review-frames"
            / LEAF_ID.lower()
            / "explainer"
            / "revisions"
            / "7"
        )
        (rev_dir / "assets" / "sub").mkdir(parents=True)
        manifest_bytes = b'{"entrypoint": {"path": "index.html"}, "revision": 7}\n'
        (rev_dir / "manifest.json").write_bytes(manifest_bytes)
        (rev_dir / "binding.json").write_text('{"env": "excluded"}\n', encoding="utf-8")
        (rev_dir / "assets" / "index.html").write_text("<html>agreed</html>\n", encoding="utf-8")
        (rev_dir / "assets" / "sub" / "app.css").write_text("body {}\n", encoding="utf-8")
        _rewrite_leaf(
            self.fixture,
            _leaf_doc(
                intent_review={
                    **GO,
                    "agreedRevision": {
                        "revisionId": "rev-7",
                        "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                        "packetVersions": ["MIK-R189@v2"],
                    },
                }
            ),
        )
        workspace = self._resolve(preparation="created")
        out_ref = Path(workspace["referenceRoot"])
        self.assertNotIn("referenceWarning", workspace)
        self.assertEqual((out_ref / "mockup.md").read_text(encoding="utf-8"), "# Mockup\n")
        self.assertEqual(
            (out_ref / "nested" / "spec.txt").read_text(encoding="utf-8"), "nested spec\n"
        )
        self.assertEqual((out_ref / "manifest.json").read_bytes(), manifest_bytes)
        self.assertEqual(
            (out_ref / "index.html").read_text(encoding="utf-8"), "<html>agreed</html>\n"
        )
        self.assertEqual(
            (out_ref / "assets" / "sub" / "app.css").read_text(encoding="utf-8"), "body {}\n"
        )
        self.assertFalse((out_ref / "binding.json").exists())
        # Retry preserves sealed custody even if source files change afterwards.
        (ref_dir / "mockup.md").write_text("# Mutated after creation\n", encoding="utf-8")
        self._resolve(preparation="found")
        self.assertEqual((out_ref / "mockup.md").read_text(encoding="utf-8"), "# Mockup\n")
        # If custody is unsealed and agreed revision is missing, start continues with referenceWarning.
        (Path(self.contract.worktree_group) / ".ar-product" / "reference-custody.json").unlink()
        _rewrite_leaf(
            self.fixture,
            _leaf_doc(
                intent_review={
                    **GO,
                    "agreedRevision": {
                        "revisionId": "rev-99",
                        "sha256": "c" * 64,
                        "packetVersions": ["MIK-R189@v2"],
                    },
                }
            ),
        )
        warned = self._resolve(preparation="created")
        self.assertIn("referenceWarning", warned)
        self.assertIn("agreed-revision-missing", warned["referenceWarning"])


class AgreedRevisionResolverTests(unittest.TestCase):
    """I13: one canonical resolver, from the strict native fields to the exact retained revision.

    The go is recorded through the real ``task_doc`` route; the retained revisions live in the
    leaf's frame store. The same validated agreement identifies the revision, seals its manifest
    bytes with the recorded digest and supplies that exact manifest -- the yardstick the removal
    pin protects and later receipts cite.
    """

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
        self.fields = {
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
        self.task_root = self.coord / "tasks" / "agents-remember" / "3c-x"

    def _retain(self, number: int, *, content: str = "") -> tuple[Path, bytes]:
        """Retain one revision in the leaf's explainer frame store and return its manifest."""

        rev_dir = (
            self.task_root
            / "notes"
            / "reports"
            / "review-frames"
            / "testdoc-leaf"
            / "explainer"
            / "revisions"
            / str(number)
        )
        rev_dir.mkdir(parents=True, exist_ok=True)
        manifest_bytes = f'{{"revision": {number}, "content": {json.dumps(content)}}}\n'.encode()
        (rev_dir / "manifest.json").write_bytes(manifest_bytes)
        return rev_dir, manifest_bytes

    def _go(self, revision: dict[str, Any]) -> None:
        # The native route: the architect records the developer's answer through task_doc.
        task_doc_tool(
            self.config,
            self.target,
            operation="set_field",
            edit=TaskDocEdit(fields={"intentReview": {"state": "go", **revision}}),
            call=TaskDocCall(),
        )

    def test_the_native_go_resolves_to_the_exact_retained_revision_manifest_and_digest(
        self,
    ) -> None:
        _rev_dir, manifest_bytes = self._retain(7)
        self._retain(8)
        self._go(
            {
                "decisionRef": "D369",
                "agreedRevision": {
                    "revisionId": "rev-7",
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "packetVersions": ["MIK-R189@v2", "MIK-R106@v2"],
                },
            }
        )
        document = read_task_doc(self.task_root / "03c_x.json")
        resolved = resolve_agreed_revision(self.task_root, document)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.revision, 7)
        self.assertEqual(resolved.revision_id, "rev-7")
        self.assertEqual(resolved.packet_versions, ("MIK-R189@v2", "MIK-R106@v2"))
        self.assertEqual(resolved.manifest_path.parent.name, "7")
        self.assertEqual(resolved.manifest_bytes, manifest_bytes)
        self.assertTrue(resolved.digest_verified)
        # The pin names revision 7 alone: the other retained revision is not the yardstick.
        self.assertNotEqual(resolved.manifest_path, self._retain(8)[0] / "manifest.json")

    def test_a_tampered_manifest_resolves_unverified_and_a_missing_one_resolves_nothing(
        self,
    ) -> None:
        _rev_dir, manifest_bytes = self._retain(7)
        self._go(
            {
                "agreedRevision": {
                    "revisionId": "rev-7",
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "packetVersions": ["MIK-R189@v2"],
                }
            }
        )
        document = read_task_doc(self.task_root / "03c_x.json")
        rev_dir = self._retain(7)[0]
        rev_dir.joinpath("manifest.json").write_bytes(b"tampered bytes\n")
        resolved = resolve_agreed_revision(self.task_root, document)
        self.assertIsNotNone(resolved)
        assert resolved is not None
        # The revision is still identified -- removal refuses instead of guessing -- but the
        # recorded digest no longer seals its bytes.
        self.assertEqual(resolved.revision, 7)
        self.assertFalse(resolved.digest_verified)
        # A revision the record does not name resolves to nothing at all.
        task_doc_tool(
            self.config,
            self.target,
            operation="set_field",
            edit=TaskDocEdit(
                fields={
                    "intentReview": {
                        "state": "go",
                        "agreedRevision": {
                            "revisionId": "rev-99",
                            "sha256": "a" * 64,
                            "packetVersions": ["MIK-R189@v2"],
                        },
                    }
                }
            ),
            call=TaskDocCall(),
        )
        self.assertIsNone(
            resolve_agreed_revision(self.task_root, read_task_doc(self.task_root / "03c_x.json"))
        )

    def test_loose_records_do_not_resolve_and_a_symlink_on_the_way_is_refused(self) -> None:
        _rev_dir, manifest_bytes = self._retain(7)
        self._go(
            {
                "agreedRevision": {
                    "revisionId": "rev-7",
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "packetVersions": ["MIK-R189@v2"],
                }
            }
        )
        # A loose record without the strict revisionId is not the native agreement.
        loose = {
            "intentReview": {
                "state": "go",
                "agreedRevision": {
                    "revision": 7,
                    "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                },
            }
        }
        self.assertIsNone(resolve_agreed_revision(self.task_root, loose, leaf_id=LEAF_TASK_ID))
        # A digest that is not a SHA-256 fails the strict schema too.
        self.assertIsNone(
            resolve_agreed_revision(
                self.task_root,
                {
                    "intentReview": {
                        "state": "go",
                        "agreedRevision": {"revisionId": "rev-7", "sha256": "not-a-digest"},
                    }
                },
                leaf_id=LEAF_TASK_ID,
            )
        )
        # A symlink on the way is refused by name, never followed.
        foreign = Path(self._tmp.name) / "foreign"
        foreign.mkdir()
        (foreign / "notes").mkdir()
        (foreign / "notes" / "reports").symlink_to(
            self.task_root / "notes" / "reports", target_is_directory=True
        )
        with self.assertRaisesRegex(ValueError, "agreed-revision-symlink-refused"):
            resolve_agreed_revision(foreign, read_task_doc(self.task_root / "03c_x.json"))


class MasterOrderStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fixture = _fixture(Path(self._tmp.name))
        # A declared waitsFor makes the master carry a structured order.
        write_task_doc(
            self.fixture.task_root,
            _leaf_doc(OTHER_ID, OTHER_SLUG, intent_review=GO, waits_for=[]),
        )
        write_task_doc(
            self.fixture.task_root,
            _leaf_doc(LEAF_ID, LEAF_SLUG, intent_review=PENDING, waits_for=[OTHER_ID]),
        )

    def test_a_pending_leaf_is_left_out_of_ready_and_the_order_shows_it(self) -> None:
        master = read_task_doc(self.fixture.task_root / "task.json")
        children = load_master_children(self.fixture.task_root, master)
        order = derive_master_order(master, children)
        assert order is not None
        self.assertIn(OTHER_ID, order.ready_ids)
        self.assertNotIn(LEAF_ID, order.ready_ids)
        rows = {task.id: task for task in order.tasks}
        self.assertEqual(rows[LEAF_ID].intent_review, "pending")
        self.assertTrue(rows[LEAF_ID].intent_review_open)
        self.assertEqual(rows[OTHER_ID].intent_review, "go")
        rendered = render_markdown(master, master_order=order)
        self.assertIn(
            f"| [{LEAF_ID} — Leaf {LEAF_ID}]({LEAF_SLUG}.md) | planning · intent review open |",
            rendered,
        )
        self.assertIn(f"| [{OTHER_ID} — Leaf {OTHER_ID}]({OTHER_SLUG}.md) | planning |", rendered)


if __name__ == "__main__":
    unittest.main()
