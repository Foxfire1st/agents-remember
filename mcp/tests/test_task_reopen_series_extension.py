"""A completed master is extended and re-landed, not just reset (D-49, T89).

``test_task_reopen.py::SeriesReopenTests`` pins the reopen half: a terminal atomic series is
reset under CAS, its retired integration ref is re-cut, and its successor enclosure generation
cites the archived one. That is the *door*. This module pins what the door is for -- that the
master behind it can do work again -- because the reopen was added to this plane precisely
because a completed master was a dead end: no route back to a new leaf, and no route back to
integration once that leaf existed.

The sequence is the whole claim, in order, on one real temporary world:

1. reopen the terminal series and read what the reset recorded;
2. author a new leaf's owning-master row *with its ``file`` cell*, and its own task document;
3. ``worktree_start`` that leaf off the reopened master;
4. work it and commit in its own code and memory worktrees;
5. close it out and integrate it onto the master's re-cut work branch;
6. carry its row to ``Completed`` through the ordinary finalize edge;
7. close the master out again and integrate it.

Every step goes through the entry point the registered tool calls -- ``reopen_task``, the
``task_doc`` application function, ``worktree_start_tool``, ``worktree_closeout_*``,
``worktree_integrate_tool``, ``lifecycle_finalize_task_tool`` -- and no branch, ref or document
is written by hand anywhere in it. The guard cases below pin what keeps the route honest rather
than merely possible: the reachability precondition that refuses to re-cut a line the series no
longer landed on, the refusal when the contract records no landing at all, and the two
stale-terminal-artifact paths that must refuse instead of replacing an archived contract -- the
one where the contract bytes are present, and the one where they were hand-deleted beside an
archived locator.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import test_suite_budget
from agents_remember.application import worktree_tools
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
    FinalizeTaskDocs,
    StartExecution,
    TaskIdentity,
)
from agents_remember.tasks import TaskDocument, read_task_doc, write_task_doc
from agents_remember.worktrees.modules.git import branch_exists, is_ancestor
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from conftest import REPOSITORY_ROOT
from task_reopen_test_support import _completed_series_contract, _runtime_config
from test_worktree_support import git

SERIES = "260698_demo-series"
LEAF = "260698-L2"
LEAF_SLUG = "l2"
CODE_MESSAGE = "260918-TSIP-L9: the extension leaf's code"
MEMORY_MESSAGE = "260918-TSIP-L9: the extension leaf's memory"

# ---------------------------------------------------------------------------------------
# This module drives a whole master lifecycle in-process, so the unit population is where it
# runs -- and the unit population is over its declared rail. That is not this module's defect and
# it is not this module's edit to make: leaf `260918-TSIP-L7` owns the budget surface, and the
# developer's ruling is 3000 unit / 600 integration. Named, counted, with the owner and the rule
# -- repaired => remove the entry in the same change; never delete the constant, never widen it
# (the `ENVELOPE_LOSING_RAISERS` shape in `test_tool_entry_point_sweep.py`).
#
# It is a *tolerance*, not an acceptance: when the base already carries the ruled rail, the case
# below is a plain assertion that the declaration is at least the ruling. While the raise is
# still in flight it reports that fact instead of failing on someone else's pending edit.
# ---------------------------------------------------------------------------------------
RULED_UNIT_BUDGET = 3000
RULED_INTEGRATION_BUDGET = 600
BUDGET_RAIL_PENDING = (
    "260918-TSIP-L7",
    "the declared pair is below the developer's ruled 3000/600; the ceiling raise has not landed "
    "on this base and `mcp/tests/test_suite_budget.py` is red for it",
)


class _World:
    """One disposable terminal master, with every entry point bound to its own config.

    ``sprint`` commands the master from a sprint whose integration branch is ``super``, which is
    what makes the master *landable*: generic integration refuses a standalone atomic series onto
    a repository-default branch by design, because only the PR landing plane may move that root.
    The guard cases use the standalone shape, because their refusals are decided before any
    integration surface is computed.
    """

    def __init__(self, root: Path, *, sprint: bool = True) -> None:
        self.root = root
        self.terminal = _completed_series_contract(
            root,
            memory_mode="external",
            sprint=sprint,
        )
        self.cfg = _runtime_config(root, self.terminal)

    @property
    def contract_path(self) -> Path:
        return self.terminal.contract_path

    @property
    def master_path(self) -> Path:
        return self.terminal.task_root / "task.json"

    @property
    def leaf_doc_path(self) -> Path:
        return self.terminal.task_root / f"{LEAF_SLUG}.json"

    def lineage_state(self) -> str:
        """What `worktree_status` reports for this master's source lineage, as an operator sees it."""

        payload = worktree_tools.worktree_status_tool(
            self.cfg,
            TaskRef(repo_id=self.terminal.repo_name, contract_path=self.contract_path.as_posix()),
        )
        projection = cast("dict[str, Any]", payload.get("source_lineage") or {})
        return str(projection.get("state", "absent"))

    def reopen(self, *, dry_run: bool = False) -> dict[str, Any]:
        return reopen_task(self.contract_path, dry_run=dry_run).payload

    def master(self) -> WorktreeContract:
        return load_contract(self.contract_path)

    def subtask(self, **row: object) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=self.terminal.repo_name, task_name=SERIES),
            operation="set_subtask",
            edit=TaskDocEdit(subtask={"number": LEAF, "name": "L2", **row}),
        )

    def author_leaf_document(self) -> dict[str, Any]:
        return task_doc_tool(
            self.cfg,
            TaskDocTarget(repo_id=self.terminal.repo_name, task_name=SERIES, slug=LEAF_SLUG),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": LEAF,
                    "slug": LEAF_SLUG,
                    "title": "L2 - extension leaf",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": self.terminal.repo_name,
                    "createdAt": "2026-07-01T11:00",
                    "master": "task.md",
                    "steps": [{"id": "S1", "title": "the extension work", "status": "pending"}],
                }
            ),
        )

    def leaf_target(self) -> TaskDocTarget:
        return TaskDocTarget(repo_id=self.terminal.repo_name, task_name=SERIES, slug=LEAF_SLUG)

    def start_leaf(self, *, dry_run: bool = False) -> dict[str, Any]:
        return worktree_tools.worktree_start_tool(
            self.cfg,
            TaskIdentity(
                repo_id=self.terminal.repo_name,
                task_name=SERIES,
                worktree_name=LEAF_SLUG,
                leaf_id=LEAF,
            ),
            execution=StartExecution(dry_run=dry_run, skip_provider_setup=True),
        )

    def master_rows(self) -> list[tuple[str, str, str]]:
        document = read_task_doc(self.master_path)
        return [(row.number, row.file or "", row.status) for row in document.subTasks]

    def memory_source_rewritten(self) -> None:
        """Replace the *memory* source line with one the memory landing is not on."""

        plan = self.terminal.memory_repo_path
        assert plan is not None
        branch = self.terminal.memory_source_branch
        self._rewrite_line(plan, branch)

    def work_branch_re_established_on_a_rewritten_line(self) -> None:
        """Re-create the retired work branch on a source line that no longer holds the landing.

        This is the shape a hand repair leaves behind, and the one the reachability proof has to
        cover on the ``present`` arm as well as the ``re-cut`` arm: the branch exists, so the
        reopen has no ref to create, and its tip is the rewritten source tip, so the "stands at
        the recorded source tip" test passes while the recorded landing is gone.
        """

        repo = self.terminal.code_repo_path
        work_branch = self.terminal.code_work_branch
        before = self.terminal.code_commit
        self._rewrite_line(repo, self.terminal.code_source_branch)
        rewritten_tip = git(repo, "rev-parse", self.terminal.code_source_branch)
        # An unrelated tip, distinct from both the source tip and the recorded landing: the content
        # is unique so the commit is not the same object the rewrite just produced (Git hashes
        # content, so a second identical orphan commit is the same commit).
        (repo / "unrelated.txt").write_text("unrelated work\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "Unrelated work on the retired branch")
        unrelated = git(repo, "rev-parse", "HEAD")
        git(repo, "branch", "-f", work_branch, unrelated)
        self._unrelated_tip = unrelated
        # `branch -f` while that branch is checked out only moves the ref file; the working tree
        # still holds the unrelated commit, so a later `git checkout` would carry it to the source
        # branch and make the source tip equal the unrelated tip. `checkout -B` is the form that
        # moves the worktree back with the ref, and it is what a hand repair would have run.
        git(repo, "checkout", "-q", "-B", work_branch, unrelated)
        git(repo, "checkout", "-q", "-B", self.terminal.code_source_branch, rewritten_tip)
        assert unrelated != rewritten_tip
        assert not is_ancestor(repo, before, rewritten_tip)

    @staticmethod
    def _rewrite_line(repo: Path, branch: str) -> None:
        git(repo, "checkout", "-q", "--orphan", "rewritten")
        git(repo, "rm", "-rf", "--quiet", ".")
        (repo / "README.md").write_text("# Rewritten\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "Rewrite the line")
        git(repo, "branch", "-f", branch, "HEAD")
        git(repo, "checkout", "-q", branch)
        git(repo, "branch", "-D", "rewritten")

    def rewritten_source_line(self) -> None:
        """Replace the source line with one the series' recorded landing is not on."""

        repo = self.terminal.code_repo_path
        git(repo, "checkout", "-q", "--orphan", "rewritten")
        git(repo, "rm", "-rf", "--quiet", ".")
        (repo / "README.md").write_text("# Rewritten\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "Rewrite the line")
        git(repo, "branch", "-f", self.terminal.code_source_branch, "HEAD")
        git(repo, "checkout", "-q", self.terminal.code_source_branch)
        git(repo, "branch", "-D", "rewritten")


MUTATION_ROUTE_SKIP_ENV = "L9_MUTATION_SKIP_ROUTE"
"""The environment variable the mutation control sets to skip the route case. See below."""

ROUTE_COMPLETIONS: list[int] = []
"""One entry per completed run of the route case.

A case that did not run is not a case that passed, and `unittest` reports a *skipped* test as a
success -- so a `skipTest` injected into the route would turn the leaf's whole end-to-end claim
green while executing nothing. The route case appends here on its last line, and
``RouteExecutionTests`` (which sorts last, after every ``test_a_...`` case) asserts the population
is non-empty. Deleting the append, or skipping the route, reds that case.

**The boundary, measured** (S7, successor round 2): that is a claim about a skipped *behaviour*,
not about skipping in general. A ``@unittest.skip`` decorator on the route case and deleting
``ROUTE_COMPLETIONS.append(1)`` both red ``test_z``; skipping the route case *and* ``test_z``
leaves ``14 passed, 3 skipped`` and nothing red, because the population check is itself an
ordinary case and any in-repo check can be deleted. The residual is a hole in the counting, not in
the guard.
"""


class SeriesExtensionRouteTests(unittest.TestCase):
    """The whole route, once, in order -- the regression this module exists for.

    Each step is one private method so the order is readable and a break names its own phase; the
    case itself is the sequence. The steps assert rather than return, so a failure inside one is
    reported at the assertion that failed, not at the end of a long method.
    """

    def test_a_reopened_master_runs_a_new_leaf_through_closeout_and_lands_again(self) -> None:
        if os.environ.get(MUTATION_ROUTE_SKIP_ENV):
            # The mutation anchor for the population check below. A skip is reported as a success by
            # `unittest`, so this is the one shape `test_z_the_route_case_actually_executed` exists
            # to catch; keeping the anchor in the case (rather than having the instrument rewrite
            # this file) is what makes the control a one-file mutation.
            self.skipTest("mutated: a skip is not a pass")
        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp))
            for run in (
                self._reopen_and_read_what_it_recorded,
                self._the_reopen_restores_the_source_lineage_proof,
                self._author_the_new_leaf_with_its_file_cell,
                self._start_the_leaf_off_the_reopened_master,
                self._the_leaf_precondition_survives_the_series_reopen,
                self._work_the_leaf,
                self._close_out_and_integrate_the_leaf,
                self._carry_the_row_to_completed,
                self._close_the_master_out_again_and_land_it,
            ):
                run(world)
            ROUTE_COMPLETIONS.append(1)

    def test_the_budget_screen_and_the_declaration_agree_in_either_landing_order(self) -> None:
        """F5: the arm that is true and falsifiable on BOTH sides of the sibling's landing order.

        This module cannot make a claim about the *value* of the rail on a base where the ruled
        raise has not landed -- that is L7's edit and either of us may land first. What it can hold
        in both orders is the *relation*, and it is falsifiable: the pair the screen proves
        boundaries for must be the pair a reader of this tree would find. While the rail is below
        the ruling, that pair is the pre-raise one (a sibling's pending edit is not this leaf's
        red); once the rail is raised, that pair is the declared one. Drift between the screen and
        the declaration in either direction fails here.

        Which order each half is true in, stated rather than implied. **Before L7 lands** the
        declaration is ``2300/400``, the screen proves ``1000/250``, and this case asserts exactly
        that pair and that it is *not* the declared one. **After L7 lands** -- whose actual edit
        raises ``pyproject.toml`` to ``3000/600`` and rewrites the screen's row to name its
        ``STUB_UNIT``/``STUB_INTEGRATION`` constants -- the declaration is ``3000/600``, the screen
        proves ``3000/600``, and this case asserts the screen follows the declaration. The screen's
        pair is read from the ``parametrize`` marks the screen runs with, so a literal row and a row
        of named constants resolve alike; a raise landed in ``pyproject.toml`` alone, with the
        screen left proving the pre-raise pair, is an inconsistent tree and stays red **here** --
        and ``test_suite_budget.py``'s own ``test_the_stub_matches_the_declared_pair``, which L7
        lands in the same change, reds it too.
        """

        declared = _declared_case_rails()
        screen = _declared_screen_boundaries()
        declared_pair = (declared["unit_case_budget"], declared["integration_case_budget"])
        stale_pair = (1000, 250)
        if declared_pair >= (RULED_UNIT_BUDGET, RULED_INTEGRATION_BUDGET):
            self.assertEqual(
                screen,
                declared_pair,
                "the rail is raised: the screen must prove the boundaries of the raised pair",
            )
        else:
            self.assertEqual(
                screen,
                stale_pair,
                "the rail is NOT raised on this base: the screen's pair is the pre-raise one, and "
                "this asserts it is exactly that rather than following the declaration it does "
                "not yet read",
            )
            self.assertNotEqual(
                screen,
                declared_pair,
                "the screen's pair and the declaration's pair are equal while the rail is below "
                "the ruling; either the raise landed (and this arm is stale) or one of them moved "
                "without the other",
            )

    def test_the_declared_case_rails_carry_the_developer_ruling(self) -> None:
        """The ruling arm: true in the RAISED landing order only, and it says so when it is not."""

        declared = _declared_case_rails()
        below = {
            name: (declared[name], ruled)
            for name, ruled in (
                ("unit_case_budget", RULED_UNIT_BUDGET),
                ("integration_case_budget", RULED_INTEGRATION_BUDGET),
            )
            if declared[name] < ruled
        }
        if below:
            owner, why = BUDGET_RAIL_PENDING
            self.skipTest(f"{owner}: {why}; declared (observed, ruled) = {below!r}")
        self.assertEqual(
            declared,
            {
                "unit_case_budget": RULED_UNIT_BUDGET,
                "integration_case_budget": RULED_INTEGRATION_BUDGET,
            },
            "a pair above the ruling is a further deliberate raise; move this constant with it",
        )

    def _reopen_and_read_what_it_recorded(self, world: _World) -> None:
        """Step 1: the reopen resets the master AND records what it reset.

        The reset blanks all four landing cells, so the response and the audit decision are the
        only surviving record of what the branch was re-cut from.
        """

        terminal = world.master()
        self._source_tip = git(terminal.code_repo_path, "rev-parse", terminal.code_source_branch)
        self._recorded_landing = terminal.code_commit
        self.assertFalse(
            branch_exists(terminal.code_repo_path, terminal.code_work_branch),
            "cleanup must have retired the integration branch for this route to be real",
        )
        reopened = world.reopen()
        self.assertEqual(reopened["state"], "reopened", reopened)
        contract = world.master()
        self.assertEqual(
            (
                contract.cleanup,
                contract.closeout_status,
                contract.integration_status,
                contract.lifecycle_id,
                contract.code_commit,
                contract.integrated_code_commit,
            ),
            ("pending", "not-started", "not-started", "", "", ""),
        )
        previous = cast("dict[str, str]", reopened["previousLanding"])
        self.assertEqual(previous["code"], self._recorded_landing)
        self.assertTrue(previous["memory"])
        self.assertEqual(reopened["previousStatus"], "Completed")
        self.assertEqual(reopened["frozenLanding"], "absent")
        self.assertEqual(reopened["nextTool"], "worktree_start")
        self.assertIn("reopenOrder", reopened)
        document = read_task_doc(world.master_path)
        self.assertEqual(document.status, "inProgress")
        audit = [entry for entry in document.decisions if "reopened" in entry.decision]
        self.assertTrue(audit, "the reopen must leave an audit decision")
        self.assertIn(self._recorded_landing, audit[-1].rationale)
        sides = cast("list[dict[str, str]]", reopened["seriesRefs"])
        self.assertEqual({side["side"] for side in sides}, {"code", "memory"})
        for side in sides:
            self.assertEqual(side["action"], "re-cut")
            self.assertEqual(side["recordedLanding"], previous[side["side"]])
            self.assertEqual(
                git(Path(side["repository"]), "rev-parse", side["branch"]), side["tip"]
            )
        self.assertEqual(
            git(terminal.code_repo_path, "rev-parse", terminal.code_work_branch), self._source_tip
        )

    def _the_reopen_restores_the_source_lineage_proof(self, world: _World) -> None:
        """A master whose branch cleanup deleted must not read as unprovable after the reopen.

        This is the developer's own symptom -- ``source_lineage.state: unavailable``, *"task-bound
        seats fail closed until contract and branch evidence is restored"* -- and the reopen's
        re-cut is what restores it. Asserted on the same projection an operator reads, not on a
        private fact: unavailable before, current after.
        """

        self.assertEqual(
            world.lineage_state(),
            "current",
            "the re-cut branch must restore this master's source-lineage proof",
        )

    def _author_the_new_leaf_with_its_file_cell(self, world: _World) -> None:
        """Step 2: the row carries its `file` cell -- `set_subtask` does not derive one.

        An empty cell does not fail here; it surfaces later as a topology refusal that blames the
        declaration rather than the missing value, which is why the cell is part of the route.
        """

        self.assertTrue(world.subtask(file=f"{LEAF_SLUG}.md", status="inProgress")["ok"])
        authored = world.author_leaf_document()
        self.assertTrue(authored["ok"], authored)
        self.assertEqual(world.master_rows(), [(LEAF, f"{LEAF_SLUG}.md", "inProgress")])
        self.assertTrue(world.leaf_doc_path.exists())

    def _start_the_leaf_off_the_reopened_master(self, world: _World) -> None:
        """Step 3: the leaf starts, with both of its own worktrees, on the re-established line."""

        preview = world.start_leaf(dry_run=True)
        self.assertEqual(preview["state"], "would-start", preview)
        started = world.start_leaf()
        self.assertTrue(started["ok"], started.get("summary"))
        self.assertEqual(started["state"], "started")
        self._leaf = load_contract(Path(started["contract_path"]))
        self.assertEqual((self._leaf.kind, self._leaf.leaf_id), ("leaf", LEAF))
        self.assertTrue(self._leaf.code_worktree.exists())
        assert self._leaf.memory_worktree is not None
        self.assertTrue(self._leaf.memory_worktree.exists())
        self.assertEqual(
            self._leaf.code_base_commit,
            self._source_tip,
            "the leaf must start on the line the reopen re-established",
        )

    def _the_leaf_precondition_survives_the_series_reopen(self, world: _World) -> None:
        """A live leaf is not reopenable, and the blocker names BOTH of its worktrees.

        This is ``_reopen_blockers``' leaf half -- the guard that stops a mid-flight leaf being
        reset while its worktree still exists, which would put two writers on one contract. The
        series path dispatches *around* it, so it is exactly the guard a series-shaped change is
        most likely to hollow out; here it is pinned while both worktrees really exist.
        """

        self.assertTrue(world.master().cleanup, "the series must still own its lane here")
        live = reopen_task(self._leaf.contract_path)
        self.assertEqual(live.payload["state"], "blocked", live.payload)
        blockers = " ".join(cast("list[str]", live.payload["blockers"]))
        self.assertIn("closeout is 'not-started', not completed.", blockers)
        self.assertIn("cleanup is 'pending', not completed.", blockers)
        self.assertIn("the code worktree still exists at", blockers)
        self.assertIn("the memory worktree still exists at", blockers)
        self.assertIn(self._leaf.code_worktree.as_posix(), blockers)
        assert self._leaf.memory_worktree is not None
        self.assertIn(self._leaf.memory_worktree.as_posix(), blockers)

    def _work_the_leaf(self, world: _World) -> None:
        """Step 4: real work, committed in the leaf's own worktrees."""

        del world
        assert self._leaf.memory_worktree is not None
        (self._leaf.code_worktree / "extension.txt").write_text("extension\n", encoding="utf-8")
        (self._leaf.memory_worktree / f"{LEAF_SLUG}.md").write_text(f"# {LEAF}\n", encoding="utf-8")
        git(self._leaf.code_worktree, "add", "-A")
        git(self._leaf.code_worktree, "commit", "-m", CODE_MESSAGE)

    def _close_out_and_integrate_the_leaf(self, world: _World) -> None:
        """Step 5: closeout records the accepted commit; integration lands it on the re-cut line."""

        messages = CloseoutCommitMessages(code=CODE_MESSAGE, memory=MEMORY_MESSAGE)
        self.assertEqual(
            worktree_tools.worktree_closeout_preview_tool(
                world.cfg, self._leaf.contract_path.as_posix(), messages
            )["state"],
            "would-closeout",
        )
        closed = worktree_tools.worktree_closeout_apply_tool(
            world.cfg,
            self._leaf.contract_path.as_posix(),
            messages,
            CloseoutApproval(intent_note="260918-TSIP-L9 extension closeout"),
        )
        self.assertTrue(closed["ok"], closed.get("summary"))
        landed = worktree_tools.worktree_integrate_tool(
            world.cfg, contract_path=self._leaf.contract_path.as_posix(), strategy="ff-only"
        )
        self.assertTrue(landed["ok"], landed.get("summary"))
        integrated = load_contract(self._leaf.contract_path)
        self.assertEqual(
            (integrated.closeout_status, integrated.integration_status),
            ("completed", "completed"),
        )
        self.assertEqual(
            git(self._leaf.code_repo_path, "rev-parse", self._leaf.code_source_branch),
            integrated.integrated_code_commit,
        )
        self.assertNotEqual(integrated.integrated_code_commit, self._recorded_landing)

    def _carry_the_row_to_completed(self, world: _World) -> None:
        """Step 6: the ordinary finalize edge -- which is also what makes re-closeout possible."""

        for operation, edit in (
            ("set_step", TaskDocEdit(step={"id": "S1", "status": "done"})),
            ("set_status", TaskDocEdit(fields={"status": "Completed"})),
        ):
            edited = task_doc_tool(world.cfg, world.leaf_target(), operation=operation, edit=edit)
            self.assertTrue(edited["ok"], edited)
        finalized = worktree_tools.lifecycle_finalize_task_tool(
            world.cfg,
            self._leaf.contract_path.as_posix(),
            docs=FinalizeTaskDocs(
                task_doc_path=world.leaf_doc_path.as_posix(),
                master_doc_path=world.master_path.as_posix(),
                subtask_number=LEAF,
            ),
            teardown_providers=False,
        )
        self.assertTrue(finalized["ok"], finalized.get("summary"))
        self.assertEqual(world.master_rows(), [(LEAF, f"{LEAF_SLUG}.md", "Completed")])

    def _close_the_master_out_again_and_land_it(self, world: _World) -> None:
        """Step 7: `series_closeout` needs Completed with every row resolved -- so this proves it."""

        reclosed = task_doc_tool(
            world.cfg,
            TaskDocTarget(repo_id=world.terminal.repo_name, task_name=SERIES),
            operation="set_status",
            edit=TaskDocEdit(fields={"status": "Completed"}),
        )
        self.assertTrue(reclosed["ok"], reclosed)
        messages = CloseoutCommitMessages(
            code="260918-TSIP-L9: re-close the master",
            memory="260918-TSIP-L9: re-close the master memory",
        )
        self.assertEqual(
            worktree_tools.worktree_closeout_preview_tool(
                world.cfg, world.contract_path.as_posix(), messages
            )["state"],
            "would-closeout",
        )
        master_closed = worktree_tools.worktree_closeout_apply_tool(
            world.cfg,
            world.contract_path.as_posix(),
            messages,
            CloseoutApproval(intent_note="260918-TSIP-L9 master re-closeout"),
        )
        self.assertTrue(master_closed["ok"], master_closed.get("summary"))
        master_landed = worktree_tools.worktree_integrate_tool(
            world.cfg, contract_path=world.contract_path.as_posix(), strategy="ff-only"
        )
        self.assertTrue(master_landed["ok"], master_landed.get("summary"))
        final_master = world.master()
        self.assertEqual(final_master.integration_status, "completed")
        self.assertEqual(
            git(final_master.code_repo_path, "rev-parse", final_master.code_source_branch),
            git(final_master.code_repo_path, "rev-parse", final_master.code_work_branch),
            "the master's accumulated line must be the source tip after integration",
        )
        self.assertEqual(
            final_master.integrated_code_commit,
            git(final_master.code_repo_path, "rev-parse", final_master.code_work_branch),
        )


class SeriesReopenGuardTests(unittest.TestCase):
    """The refusals that keep the route honest, each with the bytes it must not touch."""

    def _unchanged(self, world: _World, before: tuple[bytes, bytes]) -> None:
        self.assertEqual(
            (world.contract_path.read_bytes(), world.master_path.read_bytes()),
            before,
            "a refused reopen must write nothing",
        )

    def test_a_retired_branch_whose_landing_left_the_source_line_is_reconstructed(self) -> None:
        """The reopened master is rebuilt, not stranded, when the source line moved (ratchet sweep).

        The re-cut target is the source tip because a work branch recreated at the *recorded
        landing* would sit behind the source and the next integration could not fast-forward. When
        the source line no longer contains the landing that reasoning inverts: re-cutting at the
        source tip would publish a branch that does not contain the work the series already landed.
        The branch is therefore rebuilt **at the recorded landing**, the result says
        ``reconstructed``, and the ordinary ``worktree_sync`` remedy applies to the fact that the
        branch is behind its source. Refusing here stranded a master whose only fault was that its
        source line moved.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            recorded = terminal.code_commit
            self.assertFalse(branch_exists(terminal.code_repo_path, terminal.code_work_branch))
            world.rewritten_source_line()
            rewritten_tip = git(terminal.code_repo_path, "rev-parse", terminal.code_source_branch)
            self.assertFalse(is_ancestor(terminal.code_repo_path, recorded, rewritten_tip))

            reopened = world.reopen()

            self.assertEqual(reopened["state"], "reopened", reopened)
            code = cast("list[dict[str, str]]", reopened["seriesRefs"])[0]
            self.assertEqual(code["action"], "reconstructed")
            self.assertEqual(code["recordedLanding"], recorded)
            self.assertEqual(
                git(terminal.code_repo_path, "rev-parse", terminal.code_work_branch),
                recorded,
                "the reconstructed branch must carry the work the series landed",
            )
            self.assertEqual(world.master().cleanup, "pending")
            self.assertEqual(world.master().code_work_branch, terminal.code_work_branch)

    def test_a_cleaned_up_master_reads_as_unprovable_until_it_is_reopened(self) -> None:
        """The developer's symptom, pinned with its remedy: unavailable before, current after."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            self.assertEqual(world.lineage_state(), "unavailable")

            self.assertEqual(world.reopen()["state"], "reopened")

            self.assertEqual(world.lineage_state(), "current")

    def test_a_landing_the_contract_no_longer_records_refuses_the_re_cut(self) -> None:
        """A missing recorded landing is a blocker, never a licence to re-cut from nothing."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            write_contract(
                terminal.contract_path,
                replace(terminal, code_commit="", integrated_code_commit=""),
            )
            before = (world.contract_path.read_bytes(), world.master_path.read_bytes())

            refused = world.reopen()

            self.assertEqual(refused["state"], "blocked", refused)
            self.assertIn(
                "records no landing commit",
                " ".join(cast("list[str]", refused["blockers"])),
            )
            self._unchanged(world, before)
            self.assertFalse(branch_exists(terminal.code_repo_path, terminal.code_work_branch))

    def test_a_start_over_an_archived_terminal_contract_refuses_instead_of_replacing_it(
        self,
    ) -> None:
        """A fresh bootstrap must not mint a replacement over a collected generation.

        ``_existing_master_series_contract`` treats a terminal series artifact as one that no
        longer owns the lane, so a caller's fresh bootstrap would take it. When that artifact's
        enclosure generation was *archived*, this contract is the master's landed history, and
        discarding it lets the bootstrap overwrite the archived record -- the 2026-09-19 hand
        edit is the precedent and the permanent terminal-archive mismatch it left behind is the
        cost. The start is refused and named to ``task_reopen`` instead, which is the route that
        publishes a successor generation citing the archived one.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            # The start resolves the leaf's *document* before it ever looks at the master's
            # contract, so this refusal is only reachable once the leaf is authored -- and this is
            # the dishonest order the guard exists inside: get the leaf addressable, start it, and
            # let the bootstrap decide what the archived contract was worth.
            self.assertTrue(world.author_leaf_document()["ok"])
            before = (world.contract_path.read_bytes(), world.master_path.read_bytes())

            started = world.start_leaf(dry_run=True)

            self.assertFalse(started["ok"], started)
            self.assertEqual(started["state"], "atomic-series-contract-reopen-required", started)
            self.assertEqual(started["nextTool"], "task_reopen")
            self.assertIn("task_reopen", str(started["summary"]))
            self._unchanged(world, before)
            self.assertEqual(world.master().cleanup, "completed")

            # And the named route really is the one that works from here.
            self.assertEqual(world.reopen()["state"], "reopened")
            self.assertEqual(world.master().cleanup, "pending")
            self.assertTrue(branch_exists(terminal.code_repo_path, terminal.code_work_branch))

    def test_a_hand_deleted_contract_beside_an_archived_generation_is_refused(self) -> None:
        """The archived-generation guard must not depend on the contract *file* existing.

        ``_existing_master_series_contract`` used to answer "no existing contract" at its first
        line whenever the path was absent, so a hand-deleted ``series-contract.md`` beside a
        ``terminal-archived`` locator reached the fresh-bootstrap path: the integration branch was
        created and the bootstrap journal was written *before* the plane refused the publication,
        untyped. Nothing was ever minted -- the locator binding is immutable -- but a collected
        generation was mutated and left journal bytes behind, which is the shape this guard exists
        to prevent. The occupancy fact the guard needs is the *plane's*, and it outlives the file,
        so the plane is asked first and the refusal lands before any ref exists.

        The measurement that matters is what the old path did: this case is red when the plane
        query is removed from that branch (``l9-fix2-*``), because the branch is then created and
        the journal written again.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            coordination_root = terminal.coordination_root
            # The start resolves the leaf's *document* before it ever consults the master's
            # contract, so this refusal is only reachable once the leaf is authored -- and this is
            # the order the guard has to survive.
            self.assertTrue(world.author_leaf_document()["ok"])
            world.contract_path.unlink()
            self.assertFalse(world.contract_path.exists())
            before = _coordination_files(coordination_root)

            started = world.start_leaf()

            self.assertFalse(started["ok"], started)
            self.assertEqual(started["state"], "atomic-series-contract-reopen-required", started)
            self.assertEqual(started["nextTool"], "task_reopen")
            self.assertIn("task_reopen", str(started["summary"]))
            self.assertFalse(
                world.contract_path.exists(),
                "a refusal must not mint a replacement over an archived generation",
            )
            self.assertFalse(
                branch_exists(terminal.code_repo_path, terminal.code_work_branch),
                "the refusal must land before the integration branch exists",
            )
            self.assertEqual(
                _coordination_files(coordination_root),
                before,
                "the refusal must write nothing -- the bootstrap journal and its lock are the "
                "bytes the old path left behind on a collected generation",
            )

    def test_a_live_series_is_still_refused_so_a_running_seat_is_never_reset_under(self) -> None:
        """The terminal precondition survives the new guard: reopen is not a general reset."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            self.assertEqual(world.reopen()["state"], "reopened")

            second = world.reopen()

            self.assertEqual(second["state"], "blocked", second)
            self.assertIn(
                "not a terminal series state",
                " ".join(cast("list[str]", second["blockers"])),
            )

    def test_the_memory_side_is_proven_too_and_not_only_the_code_side(self) -> None:
        """F4: a guard proven on one side is proven on no side.

        Dropping only the memory half of the reachability proof left this module green before this
        case existed. The memory repository is a separate line with a separate landing, so a memory
        branch whose landing left the line is reconstructed exactly as the code side is -- and the
        two actions are asserted independently, which is what a one-sided mutation cannot satisfy.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            recorded_memory = terminal.memory_content_commit
            world.memory_source_rewritten()

            reopened = world.reopen()

            self.assertEqual(reopened["state"], "reopened", reopened)
            sides = {
                side["side"]: side for side in cast("list[dict[str, str]]", reopened["seriesRefs"])
            }
            self.assertEqual(sides["code"]["action"], "re-cut")
            self.assertEqual(sides["memory"]["action"], "reconstructed")
            self.assertEqual(sides["memory"]["recordedLanding"], recorded_memory)
            self.assertEqual(
                git(Path(sides["memory"]["repository"]), "rev-parse", sides["memory"]["branch"]),
                recorded_memory,
                "the memory branch must carry the memory the series landed",
            )
            self.assertEqual(world.master().cleanup, "pending")

    def test_the_integration_cell_is_the_documented_code_landing_fallback(self) -> None:
        """F4: `code_commit` falls back to `integrated_code_commit`, and the proof still runs.

        ``_series_recorded_landing`` reads the closeout cell first and the integration cell second.
        Dropping that fallback left the module green before this case existed, because every fixture
        recorded both cells. Here only the integration cell carries the code landing, so a reopen
        that still succeeds succeeded *through* the fallback.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            write_contract(terminal.contract_path, replace(terminal, code_commit=""))
            expected = load_contract(terminal.contract_path)

            reopened = world.reopen()

            self.assertEqual(reopened["state"], "reopened", reopened)
            landing = cast("dict[str, str]", reopened["previousLanding"])
            self.assertEqual(landing["code"], expected.integrated_code_commit)
            self.assertTrue(landing["code"])
            self.assertIn(reopened["seriesRefs"][0]["action"], ("re-cut", "reconstructed"))

    def test_the_integration_cell_is_the_documented_memory_landing_fallback(self) -> None:
        """F4, other side: `memory_content_commit` falls back to the integration memory cell."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            write_contract(terminal.contract_path, replace(terminal, memory_content_commit=""))
            expected = load_contract(terminal.contract_path)

            reopened = world.reopen()

            self.assertEqual(reopened["state"], "reopened", reopened)
            landing = cast("dict[str, str]", reopened["previousLanding"])
            self.assertEqual(landing["memory"], expected.integrated_memory_content_commit)
            self.assertTrue(landing["memory"])

    def test_a_missing_master_document_does_not_block_the_reopen(self) -> None:
        """R3 (ratchet sweep): the document is repaired after the lane is owned, not before.

        Blocking the reopen on `task.json` made a master unrecoverable for a reason that does not
        hold: the reopen's object is the contract, the refs and the successor enclosure generation,
        and those are exactly what a seat needs in hand to repair the document. The reopen proceeds
        and *reports* the missing document rather than refusing.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            world.master_path.unlink()

            reopened = world.reopen()

            self.assertEqual(reopened["state"], "reopened", reopened)
            self.assertIn("is missing", str(reopened["documentNote"]))
            self.assertIsNone(reopened["doc"])
            self.assertEqual(world.master().cleanup, "pending")
            self.assertTrue(
                branch_exists(world.terminal.code_repo_path, world.terminal.code_work_branch)
            )

    def test_a_task_root_holding_a_leaf_document_is_still_refused(self) -> None:
        """The other side of R3: a task root whose document is not a master is a different task."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            document = read_task_doc(world.master_path)
            data = document.model_dump(by_alias=True)
            data["kind"] = "subTask"
            data["steps"] = [{"id": "S1", "title": "not a master", "status": "pending"}]
            data.pop("executionNature", None)
            write_task_doc(world.terminal.task_root, TaskDocument.model_validate(data))
            before = world.contract_path.read_bytes()

            refused = world.reopen()

            self.assertEqual(refused["state"], "blocked", refused)
            self.assertIn(
                "is not a master",
                " ".join(cast("list[str]", refused["blockers"])),
            )
            self.assertEqual(world.contract_path.read_bytes(), before)

    def test_a_contract_that_records_no_landing_at_all_refuses(self) -> None:
        """Both cells empty on both sides: there is nothing to reconstruct or re-cut from."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            write_contract(
                terminal.contract_path,
                replace(
                    terminal,
                    code_commit="",
                    integrated_code_commit="",
                    memory_content_commit="",
                    integrated_memory_content_commit="",
                ),
            )
            before = (world.contract_path.read_bytes(), world.master_path.read_bytes())

            refused = world.reopen()

            self.assertEqual(refused["state"], "blocked", refused)
            self.assertIn(
                "records no landing commit",
                " ".join(cast("list[str]", refused["blockers"])),
            )
            self._unchanged(world, before)

    def test_an_existing_branch_at_an_unrelated_tip_is_refused_not_moved(self) -> None:
        """The guard reconstruction must not swallow: an existing ref is never moved.

        A hand repair can leave the work branch standing somewhere that is neither the source tip
        nor the recorded landing. Rebuilding the landing *there* would rewind or advance a live ref,
        which is the one thing a reset must not do silently, so the reopen refuses on this arm
        rather than reconstructing. This is the arm that keeps reconstruction from becoming a
        licence.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            world.work_branch_re_established_on_a_rewritten_line()
            unrelated = git(terminal.code_repo_path, "rev-parse", terminal.code_work_branch)
            before = (world.contract_path.read_bytes(), world.master_path.read_bytes())

            refused = world.reopen()

            self.assertEqual(refused["state"], "blocked", refused)
            self.assertIn(
                "the reopen never moves an existing ref",
                " ".join(cast("list[str]", refused["blockers"])),
            )
            self._unchanged(world, before)
            self.assertEqual(
                git(terminal.code_repo_path, "rev-parse", terminal.code_work_branch), unrelated
            )
            self.assertEqual(world.master().cleanup, "completed")

    def test_a_reachable_landing_on_an_existing_branch_still_reopens(self) -> None:
        """The other direction of the `present` arm: the proof must not refuse a sound reopen.

        Without this, "refuse harder" would pass every case above. A work branch already standing
        on the source tip whose line still contains the recorded landing is the ordinary reopened
        state and must be adopted, not refused.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _World(Path(tmp), sprint=False)
            terminal = world.master()
            git(
                terminal.code_repo_path,
                "branch",
                terminal.code_work_branch,
                git(terminal.code_repo_path, "rev-parse", terminal.code_source_branch),
            )

            reopened = world.reopen()

            self.assertEqual(reopened["state"], "reopened", reopened)
            sides = {
                side["side"]: side for side in cast("list[dict[str, str]]", reopened["seriesRefs"])
            }
            self.assertEqual(sides["code"]["action"], "present")
            self.assertEqual(world.master().cleanup, "pending")


class RouteExecutionTests(unittest.TestCase):
    """The population check that makes a skip unable to pass. Sorts last, by construction."""

    def test_z_the_route_case_actually_executed(self) -> None:
        self.assertGreaterEqual(
            len(ROUTE_COMPLETIONS),
            1,
            "the route case did not run to completion: a skip is reported as a success by "
            "`unittest`, so this assertion -- which no skip can satisfy -- is what makes the "
            "leaf's end-to-end claim mean it executed",
        )


if __name__ == "__main__":
    unittest.main()


def _declared_screen_boundaries() -> tuple[int, int]:
    """The pair the budget screen's enforcement case proves, read from the marks it runs with.

    ``test_selected_case_budgets`` hands the hook its own rails so it can go over them without
    collecting a population, and those rails are the numbers the screen is *proving boundaries for*.
    This reads them where the screen itself reads them -- the ``parametrize`` argument values, as
    pytest resolved them -- instead of re-parsing the source text.

    That is the whole repair, and it is the `T101` rule applied to the reader rather than to the
    assertion. An AST reader that understood only literal ints returned ``(0, 0)`` the moment the
    sibling that owns this file wrote its row as module constants, so this case failed on the tree
    where the raise *had* landed and passed only on the shape L7 happened not to choose. The value
    the screen runs with is the interface, and it is the same interface in either landing order:
    literal row, named constants, or an expression all resolve here.

    Returns ``(0, 0)`` when no such row exists: the assertion that uses this then fails naming the
    shape it expected, rather than silently succeeding against nothing.
    """

    for member in vars(test_suite_budget).values():
        marks = getattr(member, "pytestmark", None)
        if marks is None:
            continue
        for mark in marks if isinstance(marks, (list, tuple)) else [marks]:
            if mark.name != "parametrize" or len(mark.args) < 2:
                continue
            values = mark.args[1]
            if not isinstance(values, (list, tuple)) or not values:
                continue
            first = values[0]
            if not isinstance(first, (list, tuple)) or len(first) < 2:
                continue
            pair = tuple(first[:2])
            if len(pair) == 2 and all(isinstance(element, int) for element in pair):
                return pair
    return (0, 0)


def _coordination_files(root: Path) -> set[str]:
    """Every file under a coordination root, relative and sorted -- the write surface.

    A refusal is a claim about what was *not* written, and the two bytes the old archived-contract
    path left behind (``logs/worktree-series-bootstrap/<repo>/<task>.json`` and its lock) are
    neither the contract nor the master document, so a bytes comparison of those two files cannot
    see them. This snapshot is the whole coordination root instead, git internals excluded because
    the fixture's memory repository is a real one and its object store is not this claim's subject.
    """

    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _declared_case_rails() -> dict[str, int]:
    """The case rails pytest actually enforces, read from the repository-root declaration.

    ``mcp/tests/conftest.py`` states no ``default=`` for these two options on purpose (D-20), so
    the repository-root ``pyproject.toml`` is the single declaration. Reading it here rather than
    copying the numbers is what keeps this check from rotting into a third copy of the ceiling.
    """

    declaration = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]
    return {
        name: int(declaration[name]) for name in ("unit_case_budget", "integration_case_budget")
    }
