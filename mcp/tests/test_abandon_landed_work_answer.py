"""An abandon answer says the work already stands on the line it would land on.

Abandoning a master whose work is on its source line stays allowed, and the answer says so.
A completed master's landing cell can be stale while its branch is already merged, so the fact
must be measured from Git -- the work branch's tip reachable from the line -- rather than from
the stale recorded cells.

The measurement and the answer's shape are tested here directly; the end-to-end case proves the
wiring through ``abandon_result`` by way of a real preview that must stay silent.
"""

from __future__ import annotations

import pytest
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.modules import abandon as abandon_module
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from test_master_retirement import World
from test_worktree_support import commit_file, git

pytestmark = pytest.mark.integration


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def _contract(world: World):
    """A master whose document reads abandoned, so an abandon is allowed to run at all."""
    contract = world.series()
    master = read_task_doc(world.tasks / "master-a" / "task.json")
    write_task_doc(world.tasks / "master-a", master.model_copy(update={"status": "abandoned"}))
    sprint = read_task_doc(world.tasks / "sprint" / "task.json")
    write_task_doc(world.tasks / "sprint", sprint.model_copy(update={"integrationBranch": "super"}))
    return contract


def _merge_work_into_the_line(world: World, branch: str, line: str = "super") -> None:
    git(world.code, "checkout", branch)
    commit_file(world.code, "landed.txt", "the master's work\n", "the master's work")
    git(world.code, "checkout", line)
    git(world.code, "merge", "--ff-only", branch)


def test_the_landed_fact_is_measured_by_ancestry(world):
    """Unmerged work is not landed; the same work, once merged, is -- measured from Git."""

    contract = _contract(world)
    args = WorktreeArgs(contract_path=contract.contract_path)
    git(world.code, "checkout", "ar/master-a")
    commit_file(world.code, "work.txt", "the master's work\n", "the master's work")

    assert abandon_module._landed_work_fact(args) is None

    git(world.code, "checkout", "super")
    git(world.code, "merge", "--ff-only", "ar/master-a")

    assert abandon_module._landed_work_fact(args) == {
        "state": "on-source-line",
        "branch": "ar/master-a",
        "line": "super",
    }


def test_the_landed_fact_is_absent_when_the_work_branch_is_gone(world):
    contract = _contract(world)
    git(world.code, "branch", "-D", "ar/master-a")

    assert (
        abandon_module._landed_work_fact(WorktreeArgs(contract_path=contract.contract_path)) is None
    )


def test_the_answer_names_the_line_and_the_branch():
    answer = abandon_module._with_landed_work_answer(
        WorktreeCommandResult(0, {"state": "would-abandon", "summary": "Abandon previewed."}),
        {"state": "on-source-line", "branch": "ar/master-a", "line": "super"},
    )

    assert answer.payload["landedWork"] == {
        "state": "on-source-line",
        "branch": "ar/master-a",
        "line": "super",
    }
    summary = answer.payload["summary"]
    assert isinstance(summary, str)
    assert summary.startswith("Abandon previewed. Its work is already on super:")
    assert "the branch ar/master-a is an ancestor of super" in summary


def test_an_abandon_that_removes_work_does_not_claim_the_work_is_landed(world):
    """The end-to-end wiring: a preview of an abandon that would lose work stays silent."""

    contract = _contract(world)
    git(world.code, "checkout", "ar/master-a")
    commit_file(world.code, "unlanded.txt", "work that would be lost\n", "unlanded work")

    preview = abandon_module.abandon_result(
        WorktreeArgs(contract_path=contract.contract_path, dry_run=True)
    )

    assert preview.returncode != 0, preview.payload
    assert "landedWork" not in preview.payload
    assert "already on" not in str(preview.payload.get("summary") or "")
