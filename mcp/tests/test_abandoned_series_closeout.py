"""An abandoned row is asked for nothing; a recorded landing cannot be hidden by that label."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
)
from agents_remember.mcp.tools.task_doc import task_doc_payload
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocument,
    read_task_doc,
    series_abandoned,
    series_done,
    series_total,
    write_task_doc,
)
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.route_review import RouteReviewError
from agents_remember.worktrees.route_review_scope import require_current_route_review
from agents_remember.worktrees.series_closeout import (
    SeriesCheckpointRefs,
    _require_checkpoint_candidate_unchanged,
    _unordered_leaves_detail,
    capture_series_checkpoint_refs,
    require_closeout_publication_authority,
    require_series_checkpoint_authority,
    series_memory_closeout,
)
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    RepoBranchPlan,
    WorktreeContract,
    default_series_contract,
    load_contract,
    write_contract,
)
from checkpoint_landing_test_support import close_out_leaf, closeout_messages
from test_closeout_queue import MASTER_B, NOW, REPO, QueueFixture
from test_task_execution_topology import REPOSITORY, _config, _master
from test_worktree_support import commit_file, git, init_repo


def series(root: Path, completed: int = 1, abandoned: int = 1) -> WorktreeContract:
    code = root / "code"
    base = init_repo(code)
    git(code, "branch", "master")
    contract = default_series_contract(
        ContractTask(
            name="master",
            repo_name="repo-a",
            coordination_root=root / "coord",
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        code=RepoBranchPlan(
            repo_path=code, source_branch="main", work_branch="master", base_commit=base
        ),
    )
    rows = []
    previous = base
    for index in range(completed + abandoned):
        leaf_id = f"L{index}"
        status = "Completed" if index < completed else "abandoned"
        leaf = TaskDocument(
            id=leaf_id,
            slug=leaf_id.lower(),
            title=leaf_id,
            kind="subTask",
            repo="repo-a",
            createdAt="2026-10-02T00:00:00+00:00",
            status=status,
        )
        write_task_doc(contract.task_root, leaf)
        rows.append(SubTaskRef(number=leaf_id, name=leaf_id, file=f"{leaf.slug}.md", status=status))
        if status == "Completed":
            landed = commit_file(code, f"{leaf_id}.txt", leaf_id, leaf_id)
            git(code, "branch", "-f", "master", landed)
            child = replace(
                contract,
                kind="leaf",
                leaf_id=leaf_id,
                contract_path=leaf_enclosure_path(contract.task_root, leaf_id),
                parent_contract_path=contract.contract_path,
                parent_task_name="master",
                code_source_branch="master",
                code_work_branch=f"leaf-{index}",
                code_base_commit=previous,
                code_commit=landed,
                integration_status="completed",
                integrated_code_commit=landed,
                code_worktree=root / f"leaf-{index}",
            )
            write_contract(child.contract_path, child)
            previous = landed
    master = TaskDocument(
        id="MASTER",
        slug="master",
        title="Master",
        kind="master",
        repo="repo-a",
        createdAt="2026-10-02T00:00:00+00:00",
        status="Completed",
        executionNature="atomic",
        subTasks=rows,
    )
    write_task_doc(contract.task_root, master)
    return contract


@pytest.mark.parametrize("completed,abandoned", [(1, 3), (50, 7)])
def test_abandoned_rows_without_enclosures_and_with_unlanded_enclosures_pass(
    tmp_path, completed, abandoned
):
    contract = series(tmp_path, completed, abandoned)

    def empty_last_file_cell(master):
        master.subTasks[-1].file = ""

    _rewrite_rows(contract, empty_last_file_cell)
    master = TaskDocument.model_validate_json((contract.task_root / "task.json").read_bytes())
    assert (series_done(master), series_total(master)) == (completed, completed)
    assert series_abandoned(master) == abandoned
    before = (contract.task_root / "task.json").read_bytes()
    for index in range(completed, completed + 2):
        child = replace(
            contract,
            kind="leaf",
            leaf_id=f"L{index}",
            contract_path=leaf_enclosure_path(contract.task_root, f"L{index}"),
            code_source_branch="master",
            parent_contract_path=contract.contract_path,
            parent_task_name="master",
            cleanup="abandoned",
        )
        write_contract(child.contract_path, child)
    require_closeout_publication_authority(contract)
    assert (contract.task_root / "task.json").read_bytes() == before


def test_landed_abandoned_row_refuses_with_named_remedy(tmp_path):
    contract = series(tmp_path)
    child = replace(
        contract,
        kind="leaf",
        leaf_id="L1",
        contract_path=leaf_enclosure_path(contract.task_root, "L1"),
        integration_status="completed",
        integrated_code_commit=contract.code_base_commit,
        code_commit=contract.code_base_commit,
    )
    write_contract(child.contract_path, child)
    with pytest.raises(ValueError, match=r"row 'L1'.*abandoned.*completed integration.*reconcile"):
        require_closeout_publication_authority(contract)


def test_unresolved_master_row_still_refuses(tmp_path):
    contract = series(tmp_path)
    path = contract.task_root / "task.json"
    master = TaskDocument.model_validate_json(path.read_bytes())
    master.subTasks[1].status = "planning"
    write_task_doc(contract.task_root, master)
    with pytest.raises(ValueError, match=r"L1.*planning"):
        require_closeout_publication_authority(contract)


def test_an_abandoned_row_is_asked_for_no_file_cell_and_no_document(tmp_path):
    contract = series(tmp_path, completed=2, abandoned=4)

    def abandoned_rows_without_documents(master):
        master.subTasks[2].file = ""
        master.subTasks[5].file = master.subTasks[1].file
        master.subTasks[5].number = master.subTasks[1].number

    _rewrite_rows(contract, abandoned_rows_without_documents)
    (contract.task_root / "l3.json").unlink()
    (contract.task_root / "l3.md").unlink()
    (contract.task_root / "l4.json").write_text("{broken")
    require_closeout_publication_authority(contract)


def test_a_row_that_is_not_abandoned_still_needs_its_readable_document(tmp_path):
    contract = series(tmp_path)
    path = contract.task_root / "l0.json"
    document = path.read_bytes()
    path.write_text("{broken")
    with pytest.raises(TaskDocumentRefError, match=r"(?s)row 'L0'.*l0\.json.*restore that") as bad:
        require_closeout_publication_authority(contract)
    assert bad.value.status == "task-document-invalid"
    path.unlink()
    with pytest.raises(
        TaskDocumentRefError, match=r"master 'MASTER'.*row 'L0'.*does not exist.*restore that"
    ) as gone:
        require_closeout_publication_authority(contract)
    assert gone.value.status == "task-document-not-found"
    path.write_bytes(document)
    require_closeout_publication_authority(contract)


def test_all_abandoned_master_proves_empty_chain_and_refuses_foreign_commit(tmp_path):
    contract = series(tmp_path, completed=0, abandoned=2)
    require_closeout_publication_authority(contract)
    foreign = commit_file(contract.code_repo_path, "foreign.txt", "foreign", "foreign")
    git(contract.code_repo_path, "branch", "-f", "master", foreign)
    with pytest.raises(ValueError, match=r"history beyond.*" + foreign):
        require_closeout_publication_authority(contract)


def test_master_without_abandoned_rows_closes_as_before(tmp_path):
    require_closeout_publication_authority(series(tmp_path, completed=2, abandoned=0))


def _rewrite_rows(contract: WorktreeContract, edit) -> None:
    path = contract.task_root / "task.json"
    master = TaskDocument.model_validate_json(path.read_bytes())
    edit(master)
    write_task_doc(contract.task_root, master)


def test_refusals_name_the_row_or_leaf_they_are_about_and_the_action_that_clears_them(tmp_path):
    contract = series(tmp_path, completed=2, abandoned=1)

    def no_file(master):
        master.subTasks[1].file = ""

    _rewrite_rows(contract, no_file)
    with pytest.raises(
        ValueError, match=r"row 'L1'.*task.json.*no task-document file.*set_subtask"
    ):
        require_closeout_publication_authority(contract)

    def same_file(master):
        master.subTasks[1].file = master.subTasks[0].file

    _rewrite_rows(contract, same_file)
    with pytest.raises(ValueError, match=r"rows 'L0' and 'L1'.*same task document.*set_subtask"):
        require_closeout_publication_authority(contract)

    def wrong_id(master):
        master.subTasks[1].file = "l1.md"
        master.subTasks[1].number = "LX"

    _rewrite_rows(contract, wrong_id)
    with pytest.raises(ValueError, match=r"row 'LX'.*does not bind one exact owned leaf.*l1"):
        require_closeout_publication_authority(contract)

    def repeated_number(master):
        master.subTasks[1].number = "L0"

    _rewrite_rows(contract, repeated_number)
    with pytest.raises(ValueError, match=r"row 'L0'.*repeats the number.*task_doc replace"):
        require_closeout_publication_authority(contract)

    def no_rows(master):
        master.subTasks = []

    _rewrite_rows(contract, no_rows)
    with pytest.raises(ValueError, match=r"no subtask rows.*add its leaf rows"):
        require_closeout_publication_authority(contract)


def test_an_unordered_landing_chain_names_the_leaves_and_the_cells_to_repair(tmp_path):
    contract = series(tmp_path, completed=2, abandoned=0)
    first = load_contract(leaf_enclosure_path(contract.task_root, "L0"))
    twin = replace(load_contract(leaf_enclosure_path(contract.task_root, "L1")))
    twin = replace(twin, integrated_code_commit=first.integrated_code_commit)
    message = _unordered_leaves_detail(contract, {"L0": first, "L1": twin})
    assert "'L0' and 'L1'" in message and contract.task_id in message
    assert "integrated_code_commit" in message and "series-contract.md" in message
    assert message.endswith("retry closeout")


def test_a_leaf_that_did_not_land_is_named_with_the_way_to_land_it(tmp_path):
    contract = series(tmp_path, completed=2, abandoned=0)
    path = leaf_enclosure_path(contract.task_root, "L1")
    write_contract(
        path, replace(load_contract(path), integrated_code_commit=contract.code_base_commit)
    )
    with pytest.raises(
        ValueError, match=r"leaf 'L1' has not landed.*worktree_integrate.*retry closeout"
    ):
        require_closeout_publication_authority(contract)


def test_a_foreign_enclosure_keeps_the_original_status_code_and_names_leaf_and_action(tmp_path):
    contract = series(tmp_path, completed=1, abandoned=0)
    foreign = replace(
        load_contract(leaf_enclosure_path(contract.task_root, "L0")),
        leaf_id="LX",
        contract_path=leaf_enclosure_path(contract.task_root, "LX"),
    )
    write_contract(foreign.contract_path, foreign)
    with pytest.raises(ValueError, match=r"set-incomplete.*leaf 'LX'.*not a row.*before closeout"):
        require_closeout_publication_authority(contract)


def test_checkpoint_refusals_name_the_master_and_the_action(tmp_path):
    contract = series(tmp_path, completed=1, abandoned=0)
    with (
        mock.patch("agents_remember.worktrees.series_closeout.branch_commit", return_value=""),
        pytest.raises(ValueError, match=r"does not resolve.*retry worktree_checkpoint_landing"),
    ):
        capture_series_checkpoint_refs(contract)
    with pytest.raises(
        ValueError, match=r"master .*is already Completed; land it with worktree_integrate"
    ):
        require_series_checkpoint_authority(contract)
    with pytest.raises(ValueError, match=r"master.*moved after this checkpoint.*Re-run"):
        _require_checkpoint_candidate_unchanged(
            contract, SeriesCheckpointRefs(code_commit="0" * 40)
        )


def test_a_contract_without_its_memory_repository_names_the_master_and_the_cell(tmp_path):
    contract = replace(
        series(tmp_path, completed=1, abandoned=0), memory_mode="external", memory_repo_path=None
    )
    with pytest.raises(RuntimeError, match=r"master .*external memory.*no memory repo_path.*retry"):
        series_memory_closeout(contract, "0" * 40)


def _leaf_document(task_root: Path, leaf_id: str, status: str = "abandoned") -> None:
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": leaf_id,
                "slug": leaf_id.lower(),
                "title": leaf_id,
                "kind": "subTask",
                "status": status,
                "repo": REPO,
                "createdAt": NOW,
            }
        ),
    )


def _row(leaf_id: str, status: str = "abandoned", *, file: bool = True) -> SubTaskRef:
    return SubTaskRef.model_validate(
        {
            "number": leaf_id,
            "name": leaf_id,
            "file": f"{leaf_id.lower()}.md" if file else "",
            "status": status,
        }
    )


def _live_atomic_master(root: Path, *, landed: bool) -> tuple[QueueFixture, WorktreeContract]:
    """An atomic master on real refs; ``landed`` lands its one started leaf and completes it."""

    fixture = QueueFixture(root, atomic_b=True, memory_mode="external")
    series_contract = load_contract(fixture.tasks / "master-b" / "series-contract.md")
    for leaf_id, document in (("L2", False), ("L3", True), ("L4", True)):
        if document:
            _leaf_document(series_contract.task_root, leaf_id)
    rows = [_row("L1", file=False), _row("L2"), _row("L3"), _row("L4")]
    master = read_task_doc(series_contract.task_root / "task.json")
    if landed:
        closed = close_out_leaf(fixture.contracts[MASTER_B])
        git(fixture.code, "branch", "-f", series_contract.code_work_branch, closed.code_commit)
        git(
            fixture.memory,
            "branch",
            "-f",
            series_contract.memory_work_branch,
            closed.memory_content_commit,
        )
        write_contract(
            closed.contract_path,
            replace(
                closed,
                integration_status="completed",
                integrated_code_commit=closed.code_commit,
                integrated_memory_content_commit=closed.memory_content_commit,
            ),
        )
        rows.insert(0, master.subTasks[0].model_copy(update={"status": "Completed"}))
        master = master.model_copy(update={"status": "Completed"})
    else:
        rows.insert(0, master.subTasks[0])
    write_task_doc(series_contract.task_root, master.model_copy(update={"subTasks": rows}))
    return fixture, series_contract


def _preview(fixture: QueueFixture, contract: WorktreeContract) -> dict:
    return worktree_tools.worktree_closeout_preview_tool(
        fixture.cfg, contract.contract_path.as_posix(), closeout_messages()
    )


def _master_edit(fixture: QueueFixture, operation: str, **edit) -> dict:
    return task_doc_payload(
        fixture.cfg,
        TaskDocTarget(repo_id=REPO, task_name="master-b"),
        operation=operation,
        edit=TaskDocEdit(**edit),
        call=TaskDocCall(),
    )


@pytest.mark.usefixtures("worktree_services")
def test_the_closeout_preview_would_close_a_master_whose_abandoned_rows_have_nothing(tmp_path):
    # Beside the landed leaf: an abandoned row with an empty file cell (L1), one whose document
    # is gone (L2), one with a document and no enclosure (L3), one with an unlanded enclosure (L4).
    fixture, contract = _live_atomic_master(tmp_path, landed=True)
    leaf = load_contract(fixture.contracts[MASTER_B].contract_path)
    unlanded = replace(
        leaf,
        leaf_id="L4",
        contract_path=leaf_enclosure_path(contract.task_root, "L4"),
        code_work_branch="ar/l4",
        memory_work_branch="ar/l4",
        code_worktree=tmp_path / "wt-l4",
        memory_worktree=tmp_path / "wtm-l4",
        cleanup="abandoned",
        closeout_status="not-started",
        integration_status="not-started",
        code_commit="",
        memory_content_commit="",
        integrated_code_commit="",
        integrated_memory_content_commit="",
    )
    write_contract(unlanded.contract_path, unlanded)
    before = {path: path.read_bytes() for path in fixture.tasks.rglob("*") if path.is_file()}
    refs = git(fixture.code, "show-ref")

    preview = _preview(fixture, contract)

    assert (preview["ok"], preview["state"]) == (True, "would-closeout")
    assert {
        path: path.read_bytes() for path in fixture.tasks.rglob("*") if path.is_file()
    } == before
    assert git(fixture.code, "show-ref") == refs


@pytest.mark.usefixtures("worktree_services")
def test_the_closeout_preview_names_a_live_row_without_its_document_and_the_action(tmp_path):
    fixture, contract = _live_atomic_master(tmp_path, landed=True)
    master = read_task_doc(contract.task_root / "task.json")
    landed_row = master.subTasks[0].model_copy(update={"file": ""})
    write_task_doc(
        contract.task_root,
        master.model_copy(update={"subTasks": [landed_row, *master.subTasks[1:]]}),
    )
    with pytest.raises(
        ValueError, match=r"master 'MASTER-B'.*row 'LEAF-B'.*no task-document file.*set_subtask"
    ):
        _preview(fixture, contract)
    _master_edit(fixture, "set_subtask", subtask={"number": "LEAF-B", "file": "leaf-b.md"})
    assert _preview(fixture, contract)["state"] == "would-closeout"

    document = contract.task_root / "leaf-b.json"
    kept = document.read_bytes()
    document.unlink()
    with pytest.raises(
        TaskDocumentRefError, match=r"master 'MASTER-B'.*row 'LEAF-B'.*leaf-b\.json.*restore that"
    ):
        _preview(fixture, contract)
    document.write_bytes(kept)
    assert _preview(fixture, contract)["state"] == "would-closeout"


@pytest.mark.usefixtures("worktree_services")
def test_the_review_scope_asks_an_abandoned_row_for_nothing(tmp_path):
    fixture, contract = _live_atomic_master(tmp_path, landed=False)
    leaf = fixture.contracts[MASTER_B]

    assert require_current_route_review(contract)["childCount"] == 3
    assert require_current_route_review(leaf)["status"] == "deferred-atomic-child"

    (contract.task_root / "l4.json").write_text("{broken")
    assert require_current_route_review(contract)["childCount"] == 2

    master = read_task_doc(contract.task_root / "task.json")
    write_task_doc(
        contract.task_root,
        master.model_copy(
            update={"subTasks": [*master.subTasks, _row("L5", "planning", file=False)]}
        ),
    )
    with pytest.raises(
        RouteReviewError,
        match=r"master-b/task\.json row 'L5'.*no task-document file.*task_doc create",
    ):
        require_current_route_review(contract)
    task_doc_payload(
        fixture.cfg,
        TaskDocTarget(repo_id=REPO, task_name="master-b"),
        operation="create",
        edit=TaskDocEdit(
            fields={
                "id": "L5",
                "slug": "l5",
                "title": "L5",
                "kind": "subTask",
                "repo": REPO,
                "createdAt": NOW,
            }
        ),
        call=TaskDocCall(),
    )
    assert require_current_route_review(contract)["childCount"] == 3

    # An abandoned row listed first cannot take a document from a row that is not abandoned.
    master = read_task_doc(contract.task_root / "task.json")
    stale = _row("L0").model_copy(update={"file": master.subTasks[0].file})
    write_task_doc(
        contract.task_root, master.model_copy(update={"subTasks": [stale, *master.subTasks]})
    )
    assert require_current_route_review(contract)["childCount"] == 3


@pytest.mark.usefixtures("worktree_services")
def test_a_master_whose_rows_are_all_abandoned_and_without_documents_has_a_review_scope(tmp_path):
    """The closeout preview would close it, so the review scope must exist: one with no child."""
    fixture, contract = _live_atomic_master(tmp_path, landed=False)
    master = read_task_doc(contract.task_root / "task.json")
    rows = [_row("LEAF-B", file=False), _row("L1", file=False)]
    write_task_doc(
        contract.task_root, master.model_copy(update={"status": "Completed", "subTasks": rows})
    )

    assert _preview(fixture, contract)["state"] == "would-closeout"
    assert require_current_route_review(contract)["childCount"] == 0

    # A master without any row is not such a master: it still has no scope.
    master = read_task_doc(contract.task_root / "task.json")
    write_task_doc(contract.task_root, master.model_copy(update={"subTasks": []}))
    with pytest.raises(RouteReviewError, match=r"atomic master has no canonical child documents"):
        require_current_route_review(contract)


class _TaskWorld:
    """A sprint and the masters it commands, driven through task_doc."""

    def __init__(
        self,
        root: Path,
        masters: dict[str, tuple[str, str, list[SubTaskRef]]],
        *,
        graph: bool = True,
    ) -> None:
        self.coord = root / "coord"
        self.tasks = self.coord / "tasks" / REPOSITORY
        self.code = root / "code"
        self.base = init_repo(self.code)
        self.cfg = _config(self.coord, self.code)
        for name, (nature, status, rows) in masters.items():
            write_task_doc(
                self.tasks / name,
                _master(identity=name, execution_nature=nature).model_copy(
                    update={"status": status, "subTasks": rows}
                ),
            )
        nodes = [{"repository": REPOSITORY, "path": f"{name}/task.json"} for name in masters]
        write_task_doc(
            self.tasks / "sprint",
            _master(
                identity="sprint",
                orchestrates=list(masters),
                execution_graph={"nodes": nodes, "edges": []} if graph else None,
            ),
        )

    def task(self, master: str, operation: str, **edit) -> dict:
        return task_doc_payload(
            self.cfg,
            TaskDocTarget(repo_id=REPOSITORY, task_name=master),
            operation=operation,
            edit=TaskDocEdit(**edit),
            call=TaskDocCall(),
        )

    def status(self, master: str) -> str:
        return read_task_doc(self.tasks / master / "task.json").status

    def enclosure(self, master: str, leaf_id: str, *, landed: bool) -> Path:
        contract = default_series_contract(
            ContractTask(
                name=master,
                repo_name=REPOSITORY,
                coordination_root=self.coord,
                workflow_kind="light-task",
                memory_mode="disabled",
            ),
            code=RepoBranchPlan(
                repo_path=self.code,
                source_branch="main",
                work_branch=f"ar/{master}",
                base_commit=self.base,
            ),
        )
        path = leaf_enclosure_path(self.tasks / master, leaf_id)
        write_contract(
            path,
            replace(
                contract,
                kind="leaf",
                leaf_id=leaf_id,
                contract_path=path,
                cleanup="completed" if landed else "abandoned",
                integration_status="completed" if landed else "not-started",
                code_commit=self.base if landed else "",
                integrated_code_commit=self.base if landed else "",
            ),
        )
        return path


def _rows(*statuses: str) -> list[SubTaskRef]:
    return [
        SubTaskRef.model_validate({"number": f"L{index}", "name": f"L{index}", "status": status})
        for index, status in enumerate(statuses)
    ]


@pytest.mark.usefixtures("worktree_services")
def test_an_organizational_master_completes_over_abandoned_rows_that_did_not_land(tmp_path):
    world = _TaskWorld(
        tmp_path,
        {"org": ("organizational", "inProgress", _rows("Completed", "abandoned", "abandoned"))},
    )
    world.enclosure("org", "L2", landed=False)
    # An unreadable document elsewhere in the task tree is no reason to refuse this edit.
    (world.tasks / "unrelated").mkdir()
    (world.tasks / "unrelated" / "task.json").write_text("{broken")
    assert world.task("org", "set_status", fields={"status": "Completed"})["status"] == "Completed"
    assert world.status("org") == "Completed"


@pytest.mark.usefixtures("worktree_services")
def test_an_organizational_master_refuses_to_complete_over_an_abandoned_row_that_landed(tmp_path):
    world = _TaskWorld(
        tmp_path, {"org": ("organizational", "inProgress", _rows("Completed", "abandoned"))}
    )
    enclosure = world.enclosure("org", "L1", landed=True)
    named = rf"master 'org'.*row 'L1' is abandoned.*{enclosure}.*completed integration.*set_subtask"
    before = (world.tasks / "org" / "task.json").read_bytes()
    with pytest.raises(TaskDocError, match=named):
        world.task("org", "set_status", fields={"status": "Completed"})
    document = read_task_doc(world.tasks / "org" / "task.json").model_dump(mode="json")
    with pytest.raises(TaskDocError, match=named):
        world.task("org", "replace", fields={**document, "status": "Completed"})
    assert (world.tasks / "org" / "task.json").read_bytes() == before

    world.task("org", "set_subtask", subtask={"number": "L1", "status": "Completed"})
    world.task("org", "set_status", fields={"status": "Completed"})
    assert world.status("org") == "Completed"


@pytest.mark.usefixtures("worktree_services")
def test_an_organizational_master_refuses_an_unreadable_enclosure_of_an_abandoned_row(tmp_path):
    world = _TaskWorld(
        tmp_path, {"org": ("organizational", "inProgress", _rows("Completed", "abandoned"))}
    )
    enclosure = world.enclosure("org", "L1", landed=False)
    enclosure.write_text("not a contract")
    with pytest.raises(
        TaskDocError, match=rf"(?s)master 'org'.*{enclosure}.*row 'L1'.*repair or remove that file"
    ):
        world.task("org", "set_status", fields={"status": "Completed"})
    assert world.status("org") == "inProgress"

    enclosure.unlink()
    world.task("org", "set_status", fields={"status": "Completed"})
    assert world.status("org") == "Completed"


@pytest.mark.usefixtures("worktree_services")
def test_the_completion_check_leaves_other_rows_masters_and_edits_as_they_were(tmp_path):
    world = _TaskWorld(
        tmp_path,
        {
            "open": ("organizational", "inProgress", _rows("Completed", "planning")),
            "atomic": ("atomic", "inProgress", _rows("Completed", "abandoned")),
            "done": ("organizational", "Completed", _rows("Completed", "abandoned")),
        },
    )
    for master in ("atomic", "done"):
        world.enclosure(master, "L1", landed=True)

    with pytest.raises(TaskDocError, match=r"unresolved work units.*'L1'.*planning"):
        world.task("open", "set_status", fields={"status": "Completed"})
    # An atomic master is decided by its closeout, which refuses this row by name.
    world.task("atomic", "set_status", fields={"status": "Completed"})
    assert world.status("atomic") == "Completed"
    # Only the transition is checked: a completed master stays editable.
    world.task("done", "set_field", fields={"statusNote": "kept"})
    assert read_task_doc(world.tasks / "done" / "task.json").statusNote == "kept"

    # Under a sprint without an execution graph every commanded master executes atomically.
    plain = _TaskWorld(
        tmp_path / "plain",
        {"org": ("organizational", "inProgress", _rows("Completed", "abandoned"))},
        graph=False,
    )
    plain.enclosure("org", "L1", landed=True)
    plain.task("org", "set_status", fields={"status": "Completed"})
    assert plain.status("org") == "Completed"
