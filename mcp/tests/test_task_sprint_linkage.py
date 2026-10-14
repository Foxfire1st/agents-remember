"""L14: sprint↔master typed linkage — attach_master / detach_master / linkage_report.

Covers the atomic four-artifact attach, judgment enforcement with zero writes on
refusal, dry-run previews, detach edge guards, the graph-absent default fact, the
drift report (M16 and ledger-reconciliation shapes), old-shape tolerance, seats
schema rules, and rendering of master links / the generated master index.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tokens import count_response_tokens
from agents_remember.tasks import (
    Decision,
    Section,
    SprintSeat,
    SubTaskRef,
    TaskDocument,
    read_task_doc,
    write_task_doc,
)
from agents_remember.tasks.document_refs import (
    TaskDocumentTopology,
)
from test_task_execution_topology import (
    MASTER_A,
    MASTER_B,
    REPOSITORY,
    _config,
    _master,
)
from test_worktree_support import git, init_repo

JUDGMENT_HEADING = "Judgment Register (canonical judgment authority)"
JUDGMENT_HEADER = (
    "| Judgment id | Kind (dependency meaning, execution nature, blast radius, priority, "
    "blocker placement, reprioritization, or leaf move) | Subject | Decision | Rationale | "
    "Evidence/fact refs | Author | Confidence | Supersedes |"
)
JUDGMENT_SEPARATOR = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def _judgment_register(rows: list[str]) -> str:
    return "\n".join([JUDGMENT_HEADER, JUDGMENT_SEPARATOR, *rows])


def _judgment_row(judgment_id: str, author: str = "strategist") -> str:
    return (
        f"| {judgment_id} | execution nature | graph | nature=ruled | Explicit ruling. | "
        f"notes.md | {author} | high | |"
    )


def _register_section(*judgment_ids: str) -> Section:
    return Section(
        kind="freeform",
        heading=JUDGMENT_HEADING,
        body=_judgment_register([_judgment_row(judgment_id) for judgment_id in judgment_ids]),
    )


class SprintLinkageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.coord = Path(self.temp.name)
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.tasks.mkdir(parents=True)
        self.code = self.coord / "code"
        init_repo(self.code)
        git(self.code, "branch", "super", "main")
        self.cfg = _config(self.coord, self.code)
        self.topology = TaskDocumentTopology(self.coord)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_master(
        self,
        ref: TaskDocumentRef,
        *,
        nature: str | None = None,
        status: str = "planning",
    ) -> None:
        folder = Path(ref.path).parent.name
        write_task_doc(
            self.tasks / folder,
            _master(identity=folder, execution_nature=nature).model_copy(update={"status": status}),
        )

    def _write_sprint(
        self,
        *,
        graph: dict[str, Any] | None = None,
        rows: list[SubTaskRef] | None = None,
        orchestrates: list[str] | None = None,
        decisions: list[dict[str, str]] | None = None,
        seats: list[dict[str, Any]] | None = None,
    ) -> None:
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="SPRINT",
                orchestrates=(
                    orchestrates if orchestrates is not None else ["master-a", "master-b"]
                ),
                execution_graph=graph,
            ).model_copy(
                update={
                    "integrationBranch": "super",
                    "sections": [_register_section("J-1", "J-2")],
                    "subTasks": rows or [],
                    "decisions": [Decision.model_validate(d) for d in decisions or []],
                    "seats": [SprintSeat.model_validate(s) for s in seats or []],
                }
            ),
        )

    def _graph_ful_sprint(self) -> None:
        self._write_master(MASTER_A, nature="organizational")
        self._write_master(MASTER_B, nature="atomic")
        self._write_sprint(
            graph={"nodes": [MASTER_A.model_dump(), MASTER_B.model_dump()], "edges": []}
        )

    def _op(
        self,
        operation: str,
        fields: dict[str, Any],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name="sprint"),
            operation=operation,
            edit=TaskDocEdit(fields=fields),
            call=TaskDocCall(dry_run=dry_run),
        )

    def _attach(self, fields: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        return self._op("attach_master", fields, dry_run=dry_run)

    def _detach(self, fields: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        return self._op("detach_master", fields, dry_run=dry_run)

    def _report(self) -> dict[str, Any]:
        return self._op("linkage_report", {})

    def _snapshot(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self.tasks.rglob("*") if path.is_file()}

    def _sprint(self) -> TaskDocument:
        return read_task_doc(self.tasks / "sprint" / "task.json")

    # --- attach ---------------------------------------------------------------

    # --- detach ---------------------------------------------------------------

    # --- linkage report ---------------------------------------------------------

    def test_linkage_report_answers_with_counts_and_location(self) -> None:
        """MIK-R111 rule 2, evidence item 3: the report is a file, not fields.

        The answer carries the counts and the location; the facts ride the
        report file, whole in the detail file too. A dry run computes the
        same facts and writes no file.
        """

        self._write_master(MASTER_A)
        self._write_sprint()
        target = TaskDocTarget(repo_id=REPOSITORY, task_name="sprint")

        with tool_call_context("task_doc", {"operation": "linkage_report"}, config=self.cfg):
            answer = task_doc_payload(
                self.cfg, target, operation="linkage_report", edit=TaskDocEdit(fields={})
            )
        self.assertTrue(answer["ok"])
        self.assertNotIn("linkageFacts", answer)
        self.assertIn("linkage facts", str(answer["updated"]))
        self.assertTrue(answer["factKinds"])
        self.assertEqual(sum(answer["factKinds"].values()), int(str(answer["updated"]).split()[0]))
        report = json.loads(Path(str(answer["reportPath"])).read_text(encoding="utf-8"))
        self.assertEqual(
            report["facts"],
            json.loads(Path(answer["log"]).read_text(encoding="utf-8"))["linkageFacts"],
        )
        self.assertEqual(report["factKinds"], answer["factKinds"])
        # Evidence item 3's 100 tokens are the shaped row: the framework log
        # location rides beside it per MIK-R108. Random path segments are
        # fixed to equal-length runs and the root stands at 40 characters,
        # so the bound is deterministic.
        coord = str(self.coord)
        content = {
            key: (
                re.sub(
                    r"\d{12}",
                    "0" * 12,
                    re.sub(r"[0-9a-f]{8}", "0" * 8, value.replace(coord, "C" * 40)),
                )
                if isinstance(value, str)
                else value
            )
            for key, value in answer.items()
            if key != "log"
        }
        self.assertLessEqual(count_response_tokens(content), 100)

        with tool_call_context(
            "task_doc", {"operation": "linkage_report", "dry_run": True}, config=self.cfg
        ):
            dry = task_doc_payload(
                self.cfg,
                target,
                operation="linkage_report",
                edit=TaskDocEdit(fields={}),
                call=TaskDocCall(dry_run=True),
            )
        self.assertTrue(dry["ok"])
        self.assertFalse(dry["wouldLose"])
        self.assertNotIn("reportPath", dry)
        self.assertIn("linkage facts", str(dry["updated"]))

    # --- row completion through set_subtask ------------------------------------

    # --- schema -----------------------------------------------------------------

    # --- rendering ----------------------------------------------------------------

    # --- registration --------------------------------------------------------------
