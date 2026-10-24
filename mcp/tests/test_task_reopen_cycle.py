"""MIK-R210: a completed task reopens from its master and can be completed again.

The reopen route (`task_reopen`) and the ordinary start after it, on one disposable world each,
with no branch, ref, archive or document written by hand anywhere in the reopen or the start:
every step calls the entry point a registered tool calls. The module pins the four transitions
the reopen had to stop refusing:

1. a completed, landed, cleaned-up and archived leaf reopens, and its seats start through
   ``role_start_set`` (the terminal-archive contract check no longer reads the reopen's own
   reset as hand tampering);
2. the same through ``worktree_start`` with the arguments ``task_reopen`` itself names;
3. a second ``task_reopen`` on the reopened leaf answers ``reopened`` and changes nothing;
4. three complete-and-reopen cycles of the same leaf in a row, each succeeding;
5. a leaf of a master whose series contract names the master as its own ``parent_task_name``
   starts (the 2026-09-29 artifact, which is not an edge of the master admission);
6. a completed master in an active sprint reopens, its branches are restored from the sprint's
   integration branch, and one of its completed leaves is reopened and started under it;
7. a leaf whose master is gone, and a master whose sprint is closed, each get an answer that
   names the missing precondition.

The completion each cycle is written the way the cleanup's own archive records it (the
``worktree_cleanup`` operation and its external archive), because what these cases pin is the
*reopen* and the *start*: the closeout, integration and cleanup tools have their own modules.
The archives are published and collected through the production seam
(``terminal_archive_required_result``), never by writing archive bytes.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.application import worktree_tools
from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_docs.task_ref import TaskRef
from agents_remember.application.worktree_tool_requests import StartExecution, TaskIdentity
from agents_remember.cli import role_launch_workspace
from agents_remember.models.lifecycles.enclosure import TerminalWorktreeCleanupArguments
from agents_remember.models.role_identity import LEAF_ROLES
from agents_remember.models.role_launcher import LauncherRole
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import read_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.integration.terminal_enclosure_archive import (
    terminal_archive_required_result,
)
from agents_remember.worktrees.modules.git import branch_commit, is_ancestor, require_git
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from agents_remember.worktrees.queue.closeout_queue import CloseoutQueueError
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.reopen_series import _series_reopen_plan
from agents_remember.worktrees.series_closeout import _require_chain_origin
from agents_remember.worktrees.task_resolver import series_contract_path
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    WorktreeContract,
    amend_contract,
    load_contract,
    write_contract,
)
from task_reopen_test_support import (
    _completed_leaf_contract,
    _completed_series_contract,
    _leaf_doc,
    _master_doc,
    _runtime_config,
)

LEAF = "260698-L1"
# The leaf seats as the launcher names them; kept beside ``LEAF_ROLES`` so the loop below hands
# the resolver a launcher role and not a bare string, and the product's own set stays authoritative.
LEAF_SEATS: tuple[LauncherRole, ...] = ("worker", "reviewer", "curator")
MASTER_SERIES = "260698_demo-series"


def _drop_branch(repository: Path, branch: str) -> None:
    """Retire one branch the way a sprint's collection retires the line it owned."""

    require_git(repository, ["branch", "-D", branch])


def _remove_generation(contract: WorktreeContract) -> None:
    """Delete one generation's worktrees and drop their registrations, as the cleanup does."""

    for worktree in (contract.code_worktree, contract.memory_worktree):
        if worktree is not None and worktree.exists():
            shutil.rmtree(worktree)
    if contract.worktree_group.exists():
        shutil.rmtree(contract.worktree_group)
    for repository in (contract.code_repo_path, contract.memory_repo_path):
        if repository is not None:
            require_git(repository, ["worktree", "prune"])


def _reopened(contract_path: Path) -> dict[str, Any]:
    result = reopen_task(contract_path)
    return {**result.payload, "returncode": result.returncode}


class _LeafWorld:
    """One completed, landed, cleaned-up and archived leaf under its folder master."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.contract = _completed_leaf_contract(root)
        self.leaf_path = _leaf_doc(self.contract.task_root)
        self.master_path = _master_doc(self.contract.task_root)
        self.config = _runtime_config(root, self.contract)

    @property
    def contract_path(self) -> Path:
        return self.contract.contract_path

    def status(self) -> dict[str, Any]:
        return worktree_tools.worktree_status_tool(
            self.config,
            TaskRef(
                repo_id=self.contract.repo_name,
                contract_path=self.contract_path.as_posix(),
            ),
        )

    def launch_context(self, role: LauncherRole) -> RoleLaunchContext:
        """The context ``role_start_set`` hands the workspace resolution for one seat."""

        master_path = self.contract.task_root / "task.json"
        sprint_path = self.contract.task_root.parent / "260698_demo-sprint" / "task.json"
        return RoleLaunchContext(
            role=role,
            sprint=ResolvedTaskDocument(
                ref=TaskDocumentRef(
                    repository=self.contract.repo_name,
                    path=sprint_path.relative_to(
                        self.contract.coordination_root / "tasks" / self.contract.repo_name
                    ).as_posix(),
                ),
                path=sprint_path,
                document=read_task_doc(sprint_path),
            ),
            master=ResolvedTaskDocument(
                ref=TaskDocumentRef(
                    repository=self.contract.repo_name,
                    path=f"{self.contract.task_root.name}/task.json",
                ),
                path=master_path,
                document=read_task_doc(master_path),
            ),
            task=self.resolved_leaf(),
            effective_task=None,
        )

    def resolved_leaf(self) -> ResolvedTaskDocument:
        reference = TaskDocumentRef(
            repository=self.contract.repo_name,
            path=f"{self.contract.task_root.name}/{self.leaf_path.name}",
        )
        return ResolvedTaskDocument(
            ref=reference,
            path=self.leaf_path,
            document=read_task_doc(self.leaf_path),
        )

    def start_from_named_args(self, *, dry_run: bool) -> dict[str, Any]:
        """Start this leaf exactly as ``task_reopen`` named the next call."""

        payload = _reopened(self.contract_path)
        args = payload["nextArgs"]
        assert isinstance(args, dict)
        return worktree_tools.worktree_start_tool(
            self.config,
            TaskIdentity(
                repo_id=str(args["repo_id"]),
                task_name=str(args["task_name"]),
                worktree_name=str(args["worktree_name"]),
                leaf_id=str(args["leaf_id"]),
            ),
            execution=StartExecution(dry_run=dry_run, skip_provider_setup=True),
        )

    def complete_and_clean_up(self) -> None:
        """Record the completion and collect the generation through its own archive.

        The cells are the ones closeout and integration write; the archive is published by
        ``terminal_archive_required_result`` for the ``worktree_cleanup`` operation, and the
        worktree group is removed as the cleanup's deletion step does. Nothing here invents a
        contract state the product cannot produce.
        """

        contract = load_contract(self.contract_path)
        landed_code = contract.code_base_commit or contract.code_commit
        landed_memory = contract.memory_base_commit or contract.memory_content_commit
        completed = replace(
            contract,
            human_review_status="approved",
            approved_for_commit=True,
            closeout_status="completed",
            code_commit=landed_code,
            memory_content_commit=landed_memory,
            integration_status="completed",
            integrated_code_commit=landed_code,
            integrated_memory_content_commit=landed_memory,
            cleanup="pending",
        )
        write_contract(self.contract_path, completed)
        archived = terminal_archive_required_result(
            load_contract(self.contract_path),
            operation="worktree_cleanup",
            arguments=TerminalWorktreeCleanupArguments(teardown_providers=False),
            dry_run=False,
        )
        assert archived.returncode == 0, archived.payload
        write_contract(
            self.contract_path,
            amend_contract(completed, ContractCells(cleanup="completed")),
        )
        _remove_generation(completed)


class ArchivedLeafReopenTests(unittest.TestCase):
    """The three refusals the developer hit on 2026-10-23, and the cycle behind them."""

    def test_a_completed_landed_cleaned_up_archived_leaf_reopens_and_its_seats_start(
        self,
    ) -> None:
        """Rule 1 and 4: every leaf seat resolves its workspace after the reopen.

        ``role_start_set`` resolves each seat through ``_resolve_workspace`` ->
        ``_ensure_leaf_enclosure``, which read the reopened generation as terminal and refused
        the whole set with ``reopen-required``. The real resolution runs here -- the status
        tool, the archive authority, the enclosure resolution and the parent-folder read are the
        product's own. The worktrees the cleanup removed come back through the reopen's own next
        call first, and every seat then lands in that one group.
        """

        with tempfile.TemporaryDirectory() as tmp:
            world = _LeafWorld(Path(tmp))
            self.assertEqual(_reopened(world.contract_path)["state"], "reopened")

            # A caller follows the reopen's own next step before seating anyone: worktree_start
            # with the arguments the reopen names recreates the worktrees the cleanup removed.
            preceded = world.start_from_named_args(dry_run=False)
            self.assertIs(preceded["ok"], True, preceded)

            workspaces = {}
            self.assertEqual(set(LEAF_SEATS), set(LEAF_ROLES))
            for role in LEAF_SEATS:
                with self.subTest(seat=role):
                    workspaces[role] = role_launch_workspace._resolve_workspace(
                        world.config, world.launch_context(role)
                    )

            first = workspaces["curator"]
            self.assertEqual(first["contractPath"], world.contract_path.as_posix())
            # The reopened enclosure is found, not created: the start above recreated its
            # worktrees, and the resolution that refused the whole set now accepts it.
            self.assertEqual(first["enclosurePreparation"], "found")
            started = load_contract(world.contract_path)
            self.assertEqual(started.cleanup, "pending")
            self.assertEqual(started.code_worktree.name, world.contract.code_worktree.name)
            for worktree in (started.code_worktree, started.memory_worktree):
                assert worktree is not None, "the reopen must recreate both worktrees"
                self.assertTrue(worktree.is_dir(), worktree)
            for role, workspace in workspaces.items():
                with self.subTest(seat=role):
                    self.assertEqual(workspace["contractPath"], first["contractPath"])
                    self.assertTrue(Path(workspace["path"]).is_dir(), workspace)
            # The recreated worktree stands on the master's tip, which holds the landing.
            tip = branch_commit(started.code_repo_path, started.code_source_branch)
            self.assertEqual(branch_commit(started.code_repo_path, started.code_work_branch), tip)
            self.assertTrue(
                is_ancestor(started.code_repo_path, world.contract.integrated_code_commit, tip)
            )

    def test_worktree_start_with_the_arguments_task_reopen_names_succeeds(self) -> None:
        """Rule 4: the next call ``task_reopen`` names is a complete call."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _LeafWorld(Path(tmp))

            preview = world.start_from_named_args(dry_run=True)
            self.assertIs(preview["ok"], True, preview)

            started = worktree_tools.worktree_start_tool(
                world.config,
                TaskIdentity(
                    repo_id=world.contract.repo_name,
                    task_name=world.contract.task_root.name,
                    worktree_name=world.contract.code_worktree.name,
                    leaf_id=world.contract.leaf_id,
                ),
                execution=StartExecution(dry_run=False, skip_provider_setup=True),
            )
            self.assertIs(started["ok"], True, started)
            self.assertEqual(started["state"], "started")

    def test_a_second_task_reopen_on_a_reopened_leaf_answers_reopened(self) -> None:
        """Rule 3: the repeat answers ``reopened`` with the same next step and writes nothing."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _LeafWorld(Path(tmp))
            first = _reopened(world.contract_path)
            before = {
                path: path.read_bytes()
                for path in (
                    world.contract_path,
                    world.leaf_path,
                    world.master_path,
                )
            }

            second = _reopened(world.contract_path)

            self.assertEqual((second["returncode"], second["state"]), (0, "reopened"), second)
            self.assertEqual(second["nextTool"], "worktree_start")
            self.assertEqual(second["nextArgs"], first["nextArgs"])
            self.assertEqual(
                {path: path.read_bytes() for path in before},
                before,
                "a repeat reopen must change no contract, leaf doc or master row",
            )

    def test_three_complete_and_reopen_cycles_of_the_same_leaf_each_succeed(self) -> None:
        """Rule 3: there is no limit on the number of cycles."""

        with tempfile.TemporaryDirectory() as tmp:
            world = _LeafWorld(Path(tmp))
            seen: list[str] = []
            for cycle in (1, 2, 3):
                with self.subTest(cycle=cycle):
                    reopened = _reopened(world.contract_path)
                    self.assertEqual((reopened["returncode"], reopened["state"]), (0, "reopened"))
                    contract = load_contract(world.contract_path)
                    self.assertEqual(
                        (
                            contract.cleanup,
                            contract.closeout_status,
                            contract.integration_status,
                        ),
                        ("reopened", "not-started", "not-started"),
                    )
                    self.assertEqual(read_task_doc(world.leaf_path).status, "planning")
                    started = world.start_from_named_args(dry_run=False)
                    self.assertIs(started["ok"], True, started)
                    world.complete_and_clean_up()
                    seen.append(load_contract(world.contract_path).cleanup)
            self.assertEqual(seen, ["completed", "completed", "completed"])


class MasterReopenTests(unittest.TestCase):
    """Rule 2: a completed master returns from its sprint's integration branch."""

    def test_a_leaf_of_a_master_that_names_itself_as_parent_starts(self) -> None:
        """Rule 4: the stored self-name is not an edge of the master admission.

        The series contract written before 2026-09-29 records the master's own name in
        ``parent_task_name``. The suggested start carries no ``parent_task``, so the master
        admission compared ``""`` to the master's name and refused the leaf start.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = _completed_series_contract(root, memory_mode="external", sprint=True)
            self_named = replace(master, parent_task_name=master.task_name)
            write_contract(master.contract_path, self_named)
            config = _runtime_config(root, master)

            self.assertEqual(_reopened(master.contract_path)["state"], "reopened")
            self.assertEqual(load_contract(master.contract_path).parent_task_name, master.task_name)
            self._author_leaf(config, master)
            started = worktree_tools.worktree_start_tool(
                config,
                TaskIdentity(
                    repo_id=master.repo_name,
                    task_name=master.task_root.name,
                    worktree_name="l2",
                    leaf_id="260698-L2",
                ),
                execution=StartExecution(dry_run=False, skip_provider_setup=True),
            )

            self.assertIs(started["ok"], True, started)
            self.assertEqual(started["state"], "started")

    def test_a_completed_master_reopens_from_the_sprint_line_and_its_leaf_follows(self) -> None:
        """Rule 2: the branches come back from the sprint's integration branch, and lead on."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = _completed_series_contract(root, memory_mode="external", sprint=True)
            config = _runtime_config(root, master)
            memory_repo_path = master.memory_repo_path
            assert memory_repo_path is not None, "an external series records its memory repository"
            sprint_tips = {
                "code": branch_commit(master.code_repo_path, "super"),
                "memory": branch_commit(memory_repo_path, "super"),
            }
            sprint_tip = sprint_tips["code"]

            reopened = _reopened(master.contract_path)

            self.assertEqual((reopened["returncode"], reopened["state"]), (0, "reopened"))
            for recut in reopened["seriesRefs"]:
                with self.subTest(side=recut["side"]):
                    self.assertEqual(recut["sourceBranch"], "super")
                    self.assertEqual(recut["tip"], sprint_tips[recut["side"]])
                    self.assertTrue(
                        is_ancestor(
                            Path(recut["repository"]),
                            recut["recordedLanding"],
                            sprint_tips[recut["side"]],
                        )
                    )
            live = load_contract(master.contract_path)
            self.assertEqual(live.cleanup, "pending")
            self.assertEqual(branch_commit(live.code_repo_path, live.code_work_branch), sprint_tip)

            # One of its leaves: started, completed, cleaned up and archived, then reopened
            # under the reopened master -- exactly the cycle the leaf-level cases pin.
            self._author_leaf(config, master)
            self._complete_and_clean_leaf(config, master)
            leaf_contract_path = self._leaf_contract_path(master)
            leaf = load_contract(leaf_contract_path)
            self.assertEqual(leaf.parent_contract_path, master.contract_path)

            leaf_reopened = _reopened(leaf_contract_path)
            self.assertEqual((leaf_reopened["returncode"], leaf_reopened["state"]), (0, "reopened"))
            started = worktree_tools.worktree_start_tool(
                config,
                TaskIdentity(
                    repo_id=master.repo_name,
                    task_name=master.task_root.name,
                    worktree_name="l2",
                    leaf_id="260698-L2",
                ),
                execution=StartExecution(dry_run=False, skip_provider_setup=True),
            )
            self.assertIs(started["ok"], True, started)
            restarted = load_contract(leaf_contract_path)
            master_tip = branch_commit(live.code_repo_path, live.code_work_branch)
            # The restarted leaf stands on the master's line, which holds its first landing.
            self.assertEqual(
                branch_commit(restarted.code_repo_path, restarted.code_work_branch),
                master_tip,
            )
            self.assertTrue(
                is_ancestor(restarted.code_repo_path, leaf.integrated_code_commit, master_tip)
            )

    def test_a_leaf_without_its_master_and_a_master_without_its_sprint_name_it(self) -> None:
        """Rule 1 and 2: the answer says which precondition is missing."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = _completed_series_contract(root, memory_mode="external", sprint=True)
            config = _runtime_config(root, master)
            self._reopen_master(master)
            self._author_leaf(config, master)
            self._complete_and_clean_leaf(config, master)
            leaf_contract_path = self._leaf_contract_path(master)
            missing = series_contract_path(master.task_root)
            self.assertTrue(missing.is_file(), missing)

            missing.unlink()
            answer = _reopened(leaf_contract_path)

            self.assertEqual(answer["returncode"], 2, answer)
            self.assertIn(missing.as_posix(), answer["summary"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = _completed_series_contract(root, memory_mode="external", sprint=True)
            sprint_path = master.task_root.parent / "260698_demo-sprint" / "task.json"
            sprint = read_task_doc(sprint_path)
            sprint_path.write_text(
                sprint.model_copy(update={"status": "Completed"}).model_dump_json(by_alias=True),
                encoding="utf-8",
            )
            # A closed sprint's line is collected with it: the integration branch its masters
            # land on is gone, which is the precondition the reopen has to name.
            for repository in (master.code_repo_path, master.memory_repo_path):
                assert repository is not None
                _drop_branch(repository, "super")

            closed = _reopened(master.contract_path)

            self.assertEqual(closed["returncode"], 2, closed)
            self.assertIn("super", closed["summary"])
            self.assertIn("260698_demo-sprint", closed["summary"])

    # -- helpers ---------------------------------------------------------------------------

    def _reopen_master(self, master: WorktreeContract) -> None:
        reopened = _reopened(master.contract_path)
        self.assertEqual((reopened["returncode"], reopened["state"]), (0, "reopened"))

    def _author_leaf(self, config: Any, master: WorktreeContract) -> None:
        series = master.task_root.name
        row = task_doc_tool(
            config,
            TaskDocTarget(repo_id=master.repo_name, task_name=series),
            operation="set_subtask",
            edit=TaskDocEdit(subtask={"number": "260698-L2", "name": "L2", "file": "l2.md"}),
        )
        self.assertIs(row["ok"], True, row)
        document = task_doc_tool(
            config,
            TaskDocTarget(repo_id=master.repo_name, task_name=series, slug="l2"),
            operation="create",
            edit=TaskDocEdit(
                fields={
                    "id": "260698-L2",
                    "slug": "l2",
                    "title": "L2 - the master's next leaf",
                    "kind": "subTask",
                    "status": "inProgress",
                    "repo": master.repo_name,
                    "createdAt": "2026-07-01T11:00",
                    "master": "task.md",
                    "steps": [{"id": "S1", "title": "the work", "status": "pending"}],
                    # MIK-R189 rule 2: a leaf created after the rule lands starts `pending`, so
                    # every start waits for the architect's go. This fixture starts the leaf, so
                    # it carries the answer the architect would record through task_doc.
                    "intentReview": {"state": "go"},
                }
            ),
        )
        self.assertIs(document["ok"], True, document)

    def _leaf_contract_path(self, master: WorktreeContract) -> Path:
        return leaf_enclosure_path(master.task_root, "260698-L2")

    def _complete_and_clean_leaf(self, config: Any, master: WorktreeContract) -> None:
        started = worktree_tools.worktree_start_tool(
            config,
            TaskIdentity(
                repo_id=master.repo_name,
                task_name=master.task_root.name,
                worktree_name="l2",
                leaf_id="260698-L2",
            ),
            execution=StartExecution(dry_run=False, skip_provider_setup=True),
        )
        self.assertIs(started["ok"], True, started)
        contract_path = self._leaf_contract_path(master)
        contract = load_contract(contract_path)
        landed_code = contract.code_base_commit or contract.code_commit
        landed_memory = contract.memory_base_commit or contract.memory_content_commit
        completed = replace(
            contract,
            human_review_status="approved",
            approved_for_commit=True,
            closeout_status="completed",
            code_commit=landed_code,
            memory_content_commit=landed_memory,
            integration_status="completed",
            integrated_code_commit=landed_code,
            integrated_memory_content_commit=landed_memory,
            cleanup="pending",
        )
        write_contract(contract_path, completed)
        archived = terminal_archive_required_result(
            load_contract(contract_path),
            operation="worktree_cleanup",
            arguments=TerminalWorktreeCleanupArguments(teardown_providers=False),
            dry_run=False,
        )
        self.assertEqual(archived.returncode, 0, archived.payload)
        write_contract(contract_path, amend_contract(completed, ContractCells(cleanup="completed")))
        _remove_generation(completed)
        leaf_doc = master.task_root / "l2.json"
        document = read_task_doc(leaf_doc)
        leaf_doc.write_text(
            document.model_copy(update={"status": "Completed"}).model_dump_json(by_alias=True),
            encoding="utf-8",
        )


class ReopenPathRefusalTests(unittest.TestCase):
    """The two reopen-path refusals ruling 7dc3b6d2 found in the D515 sandbox.

    Each case is written to fail on the candidate as it stood before the repair: the master's
    chain check demanded one exact commit, so a leaf that had landed, been reopened and landed
    again only passed after a hand-edit of its recorded base, and a completed master whose
    cleanup step was never run refused to reopen until someone ran ``worktree_cleanup`` by hand.
    """

    def test_a_master_whose_leaf_landed_again_proves_its_chain_without_a_task_file_edit(
        self,
    ) -> None:
        """Defect (a): the chain origin is the master's line, not one exact commit."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            series = _completed_series_contract(root, memory_mode="external", sprint=True)
            repository = series.code_repo_path
            base = series.code_base_commit
            earlier = _commit_file(repository, "first.txt", "first")
            again = _commit_file(repository, "again.txt", "again")
            self.assertTrue(is_ancestor(repository, base, again))
            landing = _commit_file(repository, "landing.txt", "landing")
            leaf = replace(
                series,
                leaf_id="260698-L1",
                code_base_commit=again,
                integrated_code_commit=landing,
            )
            self.assertTrue(is_ancestor(repository, earlier, again))

            # The reopened leaf restarted at a descendant of the master's base: the chain stands
            # on the master's line, and nothing in any task file had to be corrected for it.
            _require_chain_origin(series, [leaf])
            _require_chain_origin(series, [replace(leaf, code_base_commit=base)])

            # A base off that line still refuses, so the check keeps its teeth: a line with no
            # shared history is not "a descendant of the master's base" by any reading.
            require_git(repository, ["checkout", "-q", "--orphan", "unrelated"])
            require_git(repository, ["rm", "-rq", "--cached", "."])
            divergent = _commit_file(repository, "unrelated.txt", "unrelated")
            with self.assertRaises(CloseoutQueueError) as refused:
                _require_chain_origin(series, [replace(leaf, code_base_commit=divergent)])
            self.assertEqual(refused.exception.status, "atomic-series-leaf-chain-invalid")
            self.assertIn("not on the master's own line", refused.exception.detail)
            self.assertNotIn("Correct the code_base_commit", refused.exception.detail)
            self.assertFalse(is_ancestor(repository, base, divergent))
            self.assertFalse(is_ancestor(repository, divergent, base))

    def test_a_completed_master_with_cleanup_pending_passes_the_cleanup_gate(self) -> None:
        """Defect (b): a finished closeout does not wait for a manual cleanup to reopen.

        The gate is the defect: ``task_reopen`` refused every series whose cleanup cell was not
        terminal, so a completed master that had simply never been cleaned up had to be cleaned
        by hand first. The reopen that follows is proved against a live enclosure in the sandbox
        leg, because a fixture cannot leave an enclosure half-collected without also forging the
        archive receipt the cleanup authority checks.
        """

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = _completed_series_contract(root, memory_mode="external", sprint=True)
            stalled = replace(master, cleanup="pending")
            write_contract(master.contract_path, stalled)

            plan = _series_reopen_plan(stalled)
            if isinstance(plan, WorktreeCommandResult):
                self.fail(
                    f"the cleanup gate refused a completed master: {plan.payload.get('blockers')}"
                )

            # A series that is genuinely mid-flight still refuses, so the gate keeps its teeth.
            unfinished = replace(stalled, closeout_status="not-started")
            refusal = _series_reopen_plan(unfinished)
            self.assertIsInstance(refusal, WorktreeCommandResult)
            assert isinstance(refusal, WorktreeCommandResult)
            blockers = refusal.payload.get("blockers")
            assert isinstance(blockers, list), blockers
            self.assertIn("not a terminal series state", " ".join(str(b) for b in blockers))


def _commit_file(repository: Path, name: str, content: str) -> str:
    """Write one file into the repository and commit it, returning the commit."""

    (repository / name).write_text(content, encoding="utf-8")
    require_git(repository, ["add", "--", name])
    require_git(
        repository, ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", name]
    )
    return require_git(repository, ["rev-parse", "--verify", "HEAD^{commit}"])


if __name__ == "__main__":
    unittest.main()
