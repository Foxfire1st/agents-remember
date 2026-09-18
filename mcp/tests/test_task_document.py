"""Tests for the JSON-primary task-document layer (slice 3c, commit 1).

Covers the ``ar-task-document/v1`` schema (round-trip, alias, strictness, progress
helpers), the deterministic markdown renderer (the ``w-02-light-task-workflow``
template shape, checkbox mapping, escaping, empty sections), the JSON+markdown
store, the ``task_doc`` application operations and error paths (including contract
lifecycle-key pickup), and the MCP tool registration.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import agents_remember.tasks.store as task_store
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.tasks import (
    TaskDocument,
    completion_blockers,
    current_step,
    json_path_for,
    markdown_path_for,
    master_is_terminal,
    read_task_doc,
    render_markdown,
    step_done,
    step_total,
    write_task_doc,
    write_task_docs,
)
from agents_remember.tasks.master_sync import derived_master_status, plan_master_sync


def _doc(**over: Any) -> TaskDocument:
    base: dict[str, Any] = {
        "id": "T1",
        "slug": "task",
        "title": "Hello",
        "kind": "light",
        "repo": "r",
        "type": "Docs",
        "createdAt": "2026-01-01T00:00",
    }
    base.update(over)
    return TaskDocument.model_validate(base)


def _master(**over: Any) -> TaskDocument:
    base: dict[str, Any] = {
        "id": "series",
        "slug": "series",
        "title": "Series",
        "kind": "master",
        "repo": "agents-remember",
        "type": "Master (Code)",
        "createdAt": "2026-01-01T00:00",
    }
    base.update(over)
    return TaskDocument.model_validate(base)


def _config(coord: Path) -> McpRuntimeConfig:
    """Build the configured repository authority used by task-doc publication."""
    repo = coord / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "base"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
        ],
        cwd=repo,
        check=True,
    )
    return McpRuntimeConfig(
        config_path=coord / "settings.json",
        coordination_root=coord,
        workspace_root=coord,
        transcript_root=coord / "logs" / "mcp",
        repositories={"agents-remember": RepositoryScope(repo_id="agents-remember", path=repo)},
    )


class SchemaTests(unittest.TestCase):
    def test_progress_counts_every_declared_parent_and_child(self) -> None:
        doc = _doc(
            steps=[
                {
                    "id": "S1",
                    "title": "One",
                    "status": "inProgress",
                    "substeps": [
                        {"id": "S1.a", "title": "a", "status": "done"},
                        {"id": "S1.b", "title": "b", "status": "pending"},
                    ],
                },
                {"id": "S2", "title": "Two", "status": "done"},
            ]
        )
        # Parent S1 remains visible beside its two children, plus S2: 4 units, 2 done.
        self.assertEqual((step_done(doc), step_total(doc)), (2, 4))
        self.assertEqual(
            [(item.id, item.parentId, item.status) for item in completion_blockers(doc)],
            [("S1", None, "inProgress"), ("S1.b", "S1", "pending")],
        )

    def test_current_step_prefers_active_then_first_unfinished_then_none(self) -> None:
        active = _doc(steps=[{"id": "S1", "title": "One", "status": "blocked"}])
        self.assertEqual(current_step(active), "S1 — One")
        pending = _doc(
            steps=[
                {"id": "S1", "title": "One", "status": "done"},
                {"id": "S2", "title": "Two", "status": "pending"},
            ]
        )
        self.assertEqual(current_step(pending), "S2 — Two")
        finished = _doc(steps=[{"id": "S1", "title": "One", "status": "done"}])
        self.assertIsNone(current_step(finished))


class AbandonedRowTests(unittest.TestCase):
    """``abandoned`` is a terminal decision, so it resolves a row without pretending work happened.

    These lock the two directions of that rule. Without the first, a master can never complete
    once any leaf is deliberately not taken; without the second, the rule would silently accept a
    master whose leaves were never looked at.
    """

    def test_abandoned_row_does_not_hold_its_master_open(self) -> None:
        master = _master(
            subTasks=[
                {"number": "1", "name": "Landed", "status": "Completed"},
                {"number": "2", "name": "Not taken", "status": "abandoned"},
            ]
        )
        self.assertEqual(completion_blockers(master), [])

    def test_planning_row_still_holds_its_master_open(self) -> None:
        master = _master(
            subTasks=[
                {"number": "1", "name": "Landed", "status": "Completed"},
                {"number": "2", "name": "Untouched", "status": "planning"},
            ]
        )
        self.assertEqual(
            [(item.id, item.status) for item in completion_blockers(master)],
            [("2", "planning")],
        )

    def test_abandoned_leaf_projects_an_abandoned_row_despite_partial_work(self) -> None:
        # A done step would otherwise collapse the projection to inProgress, and the next master
        # sync would reopen a row that was abandoned on purpose.
        leaf = _doc(status="abandoned", steps=[{"id": "S1", "title": "One", "status": "done"}])
        self.assertEqual(derived_master_status(leaf), "abandoned")

    def test_abandoned_row_renders_its_own_marker(self) -> None:
        # ``_MARKER`` is a direct lookup, so a DocStatus value missing from it raises on render.
        master = _master(
            subTasks=[{"number": "1", "name": "Not taken", "status": "abandoned"}],
            sections=[{"heading": "Sub-Tasks", "kind": "subTasks", "body": ""}],
        )
        self.assertIn("⛔", render_markdown(master))

    def test_an_abandoned_master_is_terminal_even_with_untouched_rows(self) -> None:
        # Case A: abandonment is terminal by declaration, so its rows are deliberately left
        # ``planning``. This is exactly why terminality cannot be spelled
        # "Completed and no blockers" -- that test is false here by construction.
        master = _master(
            status="abandoned",
            subTasks=[{"number": "1", "name": "Never started", "status": "planning"}],
        )
        self.assertTrue(master_is_terminal(master))

    def test_a_completed_master_with_an_open_row_is_not_terminal(self) -> None:
        master = _master(
            status="Completed",
            subTasks=[{"number": "1", "name": "Untouched", "status": "planning"}],
        )
        self.assertFalse(master_is_terminal(master))

    def test_an_in_progress_master_is_not_terminal(self) -> None:
        master = _master(
            status="inProgress",
            subTasks=[{"number": "1", "name": "Landed", "status": "Completed"}],
        )
        self.assertFalse(master_is_terminal(master))


class RowStatusReflectsLandingTests(unittest.TestCase):
    """A master row turns ``Completed`` when the work LANDED, not when its steps were marked.

    Recorded defect D42 of `260915_role-capsules-and-native-eve`: the row flipped the moment a
    leaf's last step was marked done, before any closeout or integration had run, so a master
    claimed that work had landed while the commits were still only on the leaf's task branch.

    The only writer that sets a leaf document's status to ``Completed`` is the task finalizer,
    and it does so only after proving the commit is reachable from the contract's target branch
    and cleanup completed. Step marking writes no such proof. These cases pin both sides of the
    gate: marked-but-unlanded projects ``inProgress``, and the finalizer's own terminal
    generation still projects ``Completed``.

    ``plan_master_sync`` is exercised end to end over real documents on disk, because the gate
    has to hold at the projection that actually writes the row -- not only in the helper.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.task_root = Path(self._tmp.name)

    def _leaf(self, **over: Any) -> TaskDocument:
        base: dict[str, Any] = {
            "id": "1",
            "slug": "leaf",
            "title": "Leaf one",
            "kind": "subTask",
            "repo": "agents-remember",
            "type": "Docs",
            "createdAt": "2026-01-01T00:00",
        }
        base.update(over)
        return TaskDocument.model_validate(base)

    def _write_master(self) -> None:
        write_task_doc(
            self.task_root,
            _master(
                subTasks=[{"number": "1", "name": "Leaf one", "status": "planning"}],
                sections=[{"kind": "subTasks", "heading": "Sub-tasks (execution order)"}],
            ),
        )

    def _row_status(self) -> str:
        master = read_task_doc(self.task_root / "task.json")
        return next(ref.status for ref in master.subTasks if ref.number == "1")

    def test_a_leaf_with_every_step_done_but_no_landing_does_not_complete_its_row(self) -> None:
        self._write_master()
        leaf = self._leaf(
            status="inProgress",
            steps=[
                {"id": "S1", "title": "One", "status": "done"},
                {"id": "S2", "title": "Two", "status": "done"},
            ],
        )
        self.assertEqual(derived_master_status(leaf), "inProgress")

        plan = plan_master_sync(self.task_root, leaf)

        self.assertEqual(plan.status, "updated")
        self.assertIsNotNone(plan.master)
        assert plan.master is not None
        write_task_docs(self.task_root, [plan.master])
        self.assertEqual(self._row_status(), "inProgress")

    def test_the_finalizers_completed_leaf_projects_completed(self) -> None:
        self._write_master()
        landed = self._leaf(
            status="Completed",
            steps=[
                {"id": "S1", "title": "One", "status": "done"},
                {"id": "S2", "title": "Two", "status": "done"},
            ],
        )

        plan = plan_master_sync(self.task_root, landed)

        self.assertEqual(plan.status, "updated")
        self.assertIsNotNone(plan.master)
        assert plan.master is not None
        row = next(ref for ref in plan.master.subTasks if ref.number == "1")
        self.assertEqual(row.status, "Completed")

    def test_the_other_three_inputs_keep_their_own_meaning(self) -> None:
        """Step state still distinguishes started from untouched, and it cannot overrule steps.

        A stale ``Completed`` document with an unresolved step is neither: the document is not
        the landing and the step is not done, so the row stays open. An untouched leaf keeps
        projecting its own ``planning`` state rather than being folded into ``inProgress``.
        """
        unresolved = self._leaf(
            status="Completed",
            steps=[
                {"id": "S1", "title": "One", "status": "done"},
                {"id": "S2", "title": "Two", "status": "pending"},
            ],
        )
        untouched = self._leaf(status="planning", steps=[{"id": "S1", "title": "One"}])

        self.assertEqual(derived_master_status(unresolved), "inProgress")
        self.assertEqual(derived_master_status(untouched), "planning")


class RenderTests(unittest.TestCase):
    def test_golden_small_light_doc(self) -> None:
        doc = _doc(
            status="planning",
            objective="Obj.",
            requirements=["one"],
            steps=[{"id": "S1", "title": "Do", "status": "done"}],
            references=["ref"],
        )
        expected = (
            "\n".join(
                [
                    "# Task: Hello",
                    "",
                    "**Status:** planning",
                    "**Repo:** r",
                    "**Type:** Docs",
                    "**Created:** 2026-01-01T00:00",
                    "",
                    "---",
                    "",
                    "## Objective",
                    "",
                    "Obj.",
                    "",
                    "---",
                    "",
                    "## Requirements",
                    "",
                    "- one",
                    "",
                    "---",
                    "",
                    "## Design",
                    "",
                    "No design reasoning needed.",
                    "",
                    "---",
                    "",
                    "## Implementation Steps",
                    "",
                    "### S1 — Do",
                    "",
                    "---",
                    "",
                    "## Route Review",
                    "",
                    "_No candidate-bound route review recorded._",
                    "",
                    "---",
                    "",
                    "## Proposed Code Examples",
                    "",
                    "No code examples are needed for this task.",
                    "",
                    "---",
                    "",
                    "## Decision Log",
                    "",
                    "_None recorded._",
                    "",
                    "---",
                    "",
                    "## Open Questions",
                    "",
                    "- None.",
                    "",
                    "---",
                    "",
                    "## References",
                    "",
                    "- ref",
                ]
            )
            + "\n"
        )
        self.assertEqual(render_markdown(doc), expected)

    def test_decision_cell_escapes_pipe_and_newline(self) -> None:
        md = render_markdown(
            _doc(decisions=[{"at": "t", "decision": "a | b\nc", "rationale": "r"}])
        )
        self.assertIn(r"| t | a \| b c | r |", md)

    def test_code_example_fence_preserves_blank_lines(self) -> None:
        md = render_markdown(
            _doc(
                codeExamples=[
                    {
                        "id": "E1",
                        "title": "Ex",
                        "distinctChange": "c",
                        "why": "w",
                        "language": "python",
                        "snippet": "a = 1\n\nb = 2",
                    }
                ]
            )
        )
        self.assertIn("```python\na = 1\n\nb = 2\n```", md)

    def test_real_subtask_extensions_round_trip_content_complete(self) -> None:
        # Models this 03c sub-task's extensions (R4 acceptance): a descriptive status, extra
        # header lines, and bespoke freeform sections beyond the bare template.
        doc = _doc(
            kind="subTask",
            id="3C",
            slug="03c_x",
            master="task.md",
            status="inProgress",
            statusNote="core JSON format landed",
            headerNotes=[
                {"label": "Verified", "value": "2026-06-18 — 3 commits landed"},
                {"label": "Reopened", "value": "2026-06-19 — pilot surfaced gaps"},
            ],
            objective="Make the task document JSON-primary.",
            sections=[
                {"heading": "Reopened", "body": "gaps the pilot surfaced"},
                {"heading": "Status history", "body": "verbatim, pre-normalization"},
            ],
        )
        md = render_markdown(doc)
        self.assertIn("**Status:** inProgress — core JSON format landed", md)
        self.assertIn("**Verified:** 2026-06-18 — 3 commits landed", md)
        self.assertIn("**Reopened:** 2026-06-19 — pilot surfaced gaps", md)
        self.assertIn("## Reopened", md)
        self.assertIn("## Status history", md)
        # the JSON round-trips losslessly
        self.assertEqual(TaskDocument.model_validate(doc.model_dump(by_alias=True)), doc)


class MasterRenderTests(unittest.TestCase):
    def test_golden_master(self) -> None:
        doc = _master(
            title="Series X",
            type="Master (Code / Docs)",
            status="inProgress",
            createdAt="2026-06-12T15:58",
            subTasks=[
                {
                    "number": "1",
                    "name": "Design",
                    "file": "01_d.md",
                    "status": "Completed",
                    "scope": "keystone",
                },
                {"number": "3c", "name": "Persist", "file": "03c_p.md", "status": "inProgress"},
                {"number": "4", "name": "Serve", "status": "planning"},
            ],
            decisions=[{"at": "2026-06-12T15:58", "decision": "8 slices", "rationale": "fits"}],
            sections=[
                {"kind": "freeform", "heading": "Objective", "body": "Ship 3.0.0."},
                {"kind": "subTasks", "heading": "Sub-tasks (execution order)", "body": "> note"},
                {"kind": "sharedDecisions", "heading": "Shared Decisions"},
                {"kind": "freeform", "heading": "Invariants", "body": "- never weaker"},
            ],
        )
        expected = (
            "\n".join(
                [
                    "# Task: Series X",
                    "",
                    "**Status:** inProgress",
                    "**Repo:** agents-remember",
                    "**Type:** Master (Code / Docs)",
                    "**Created:** 2026-06-12T15:58",
                    "",
                    "---",
                    "",
                    "## Objective",
                    "",
                    "Ship 3.0.0.",
                    "",
                    "---",
                    "",
                    "## Sub-tasks (execution order)",
                    "",
                    "> note",
                    "",
                    "1. ✅ **Design** · `01_d.md` — keystone",
                    "3c. 🔨 **Persist** · `03c_p.md`",
                    "4. ⬜ **Serve**",
                    "",
                    "---",
                    "",
                    "## Shared Decisions",
                    "",
                    "| Date-Time | Decision | Rationale |",
                    "| --- | --- | --- |",
                    "| 2026-06-12T15:58 | 8 slices | fits |",
                    "",
                    "---",
                    "",
                    "## Invariants",
                    "",
                    "- never weaker",
                ]
            )
            + "\n"
        )
        self.assertEqual(render_markdown(doc), expected)


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_write_then_read_roundtrips_and_leaves_no_tmp(self) -> None:
        doc = _doc(objective="o", steps=[{"id": "S1", "title": "a", "status": "done"}])
        json_path, md_path = write_task_doc(self.root, doc)
        self.assertEqual(json_path, json_path_for(self.root, doc))
        self.assertEqual(md_path, markdown_path_for(self.root, doc))
        self.assertTrue(json_path.exists() and md_path.exists())
        self.assertEqual(read_task_doc(json_path), doc)
        self.assertEqual(md_path.read_text(encoding="utf-8"), render_markdown(doc))
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_batch_failure_removes_new_files_published_before_later_document(self) -> None:
        docs = [
            _doc(id="L1", slug="01_first", kind="subTask"),
            _doc(id="L2", slug="02_second", kind="subTask"),
        ]
        real_atomic_write = task_store.atomic_write_text
        call_count = 0

        def fail_on_second_document(path: Path, text: str) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise OSError("injected second-document failure")
            real_atomic_write(path, text)

        with (
            patch.object(task_store, "atomic_write_text", side_effect=fail_on_second_document),
            self.assertRaisesRegex(OSError, "injected second-document failure"),
        ):
            write_task_docs(self.root, docs)

        self.assertEqual(list(self.root.iterdir()), [])


class ApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.coord = Path(tempfile.mkdtemp())
        self.cfg = _config(self.coord)

    def _create(self, **fields: Any) -> dict[str, Any]:
        # These leaf operations are authored under a master, which is the flow the task_doc
        # authoring plane allows: a leaf in a task root with no master document at all is
        # refused (nothing would ever bind its derived seriesContractPath/enclosures).
        self._ensure_parent_master()
        payload: dict[str, Any] = {
            "id": "3C",
            "slug": "03c_x",
            "title": "Smoke",
            "kind": "subTask",
            "repo": "agents-remember",
            "type": "Code",
            "createdAt": "2026-01-01T00:00",
        }
        payload.update(fields)
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x"),
            operation="create",
            edit=TaskDocEdit(fields=payload),
        )

    def _ensure_parent_master(self) -> None:
        master_path = self.coord / "tasks" / "agents-remember" / "3c-x" / "task.json"
        if not master_path.exists():
            self._create_parent_master()

    def _create_parent_master(self, **fields: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": "series",
            "slug": "series",
            "title": "Series",
            "kind": "master",
            "repo": "agents-remember",
            "type": "Master (Code)",
            "createdAt": "2026-01-01T00:00",
            "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
        }
        payload.update(fields)
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x"),
            operation="create",
            edit=TaskDocEdit(fields=payload),
        )

    def _call(
        self,
        operation: str,
        *,
        fields: dict[str, Any] | None = None,
        step: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id="agents-remember", task_name="3c-x", slug="03c_x"),
            operation=operation,
            edit=TaskDocEdit(fields=fields, step=step, decision=decision),
            call=TaskDocCall(dry_run=dry_run),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
