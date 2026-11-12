"""The tool surface and truthful reads: the corrections of MIK-R73 and MIK-R83.

Each case here protects a user operation the packets name, and each fails without its change:

* one walker over a repository's task folders, and only the canonical positions of a task folder
  are a task document or a contract (MIK-R83 rules 1 and 2);
* a contract at a canonical place is used only where its own cell names it (rule 3);
* the stray scan of the linkage report lists copies, and only there (rule 4);
* a leaf read names its own source and memory roots, the official line is identified, and a leaf
  whose worktrees are gone is refused by name (rule 6);
* ``knowledge-index`` never answers ``complete`` for an unconverted tree, and a page states its
  index state so a partial index is never presented as complete (MIK-R73 rule 6);
* the writer has a mounted route that refuses what the command refuses (MIK-R73 rule 1).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest
from agents_remember.application.knowledge_paging.pager import (
    PageBinding,
    PageCut,
    page_block,
)
from agents_remember.application.knowledge_reader import read_knowledge_reader
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractAccepted,
)
from agents_remember.cli import knowledge_index
from agents_remember.errors import ContractPlaceError
from agents_remember.kernel.git_command import read_git_blobs_bytes
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
    load_config,
)
from agents_remember.mcp.tools import read_ar_files_payload
from agents_remember.mcp.tools.knowledge import (
    ReadToolRequest,
    WriteToolRequest,
    _observe_source,
    knowledge_write_payload,
)
from agents_remember.models.knowledge_files.records import AssumptionRecord
from agents_remember.models.task_document_ref import TaskDocumentRef, TaskScopedReaderContext
from agents_remember.models.tools.tool_answer import bound_for
from agents_remember.serving.knowledge_reader import register_knowledge_reader_route
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.task_paths import (
    canonical_contract_place,
    canonical_task_document_place,
    iter_stray_task_files,
    iter_task_contracts,
    iter_task_documents,
    iter_task_folders,
    leaf_enclosure_path,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocation,
)
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    _require_named_place,
    load_contract,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_writer_test_support import (
    CODE,
    CODE_FILE,
    base_memory,
    commit_all,
    git,
    write,
)
from tool_entry_point_world import REPO, EntryPointWorld


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


def _tree(root: Path) -> Path:
    """One repository's tasks root: a live task, an archived one, and copies everywhere."""

    live = root / "tasks" / "demo" / "live-task"
    _touch(live / "task.json")
    _touch(live / "series-contract.md")
    _touch(live / "leaf.json")
    _touch(live / "enclosures" / "leaf-l1" / "series-contract.md")
    _touch(live / "notes" / "reports" / "evidence" / "task.json")
    _touch(live / "notes" / "reports" / "evidence" / "series-contract.md")
    _touch(live / "requirements" / "series-contract.md")
    _touch(live / "enclosures" / "leaf-l1" / "deeper" / "series-contract.md")
    _touch(root / "tasks" / "demo" / "0_archive" / "old-task" / "task.json")
    _touch(root / "tasks" / "demo" / "0_archive" / "old-task" / "series-contract.md")
    return root / "tasks" / "demo"


def test_task_folders_are_the_direct_children_except_the_archive(tmp_path: Path) -> None:
    repo_tasks = _tree(tmp_path)

    folders = [path.name for path in iter_task_folders(repo_tasks)]

    assert folders == ["live-task"]


def test_only_canonical_positions_are_a_task_document_or_a_contract(tmp_path: Path) -> None:
    repo_tasks = _tree(tmp_path)

    documents = [
        path.relative_to(repo_tasks).as_posix() for path in iter_task_documents(repo_tasks)
    ]
    contracts = [
        path.relative_to(repo_tasks).as_posix() for path in iter_task_contracts(repo_tasks)
    ]

    assert documents == ["live-task/task.json"]
    assert contracts == [
        "live-task/series-contract.md",
        "live-task/enclosures/leaf-l1/series-contract.md",
    ]
    assert canonical_contract_place(repo_tasks / "live-task" / "series-contract.md")
    assert canonical_contract_place(
        repo_tasks / "live-task" / "enclosures" / "leaf-l1" / "series-contract.md"
    )
    assert not canonical_contract_place(repo_tasks / "live-task" / "notes" / "series-contract.md")
    assert not canonical_task_document_place(
        repo_tasks / "live-task" / "notes" / "reports" / "task.json"
    )


def test_the_stray_scan_lists_copies_outside_canonical_places(tmp_path: Path) -> None:
    repo_tasks = _tree(tmp_path)

    strays = [path.relative_to(repo_tasks).as_posix() for path in iter_stray_task_files(repo_tasks)]

    assert strays == [
        "live-task/enclosures/leaf-l1/deeper/series-contract.md",
        "live-task/notes/reports/evidence/series-contract.md",
        "live-task/notes/reports/evidence/task.json",
        "live-task/requirements/series-contract.md",
    ]


@pytest.mark.parametrize("named", ["enclosures/x.md", "series-contract.md"])
def test_a_contract_at_a_canonical_place_naming_another_path_is_refused(named: str) -> None:
    found = Path("/coordination/tasks/demo/live-task/enclosures/leaf-l1/series-contract.md")
    copy = SimpleNamespace(contract_path=found.parents[2] / named)

    with pytest.raises(ContractPlaceError) as raised:
        _require_named_place(cast(Any, copy), found)

    message = str(raised.value)
    assert "contract-place-mismatch" in message
    assert found.as_posix() in message
    assert copy.contract_path.as_posix() in message
    assert "Delete the copy, or correct the cell" in message
    _require_named_place(cast(Any, SimpleNamespace(contract_path=found)), found)


def test_a_contract_whose_cell_spells_where_it_was_read_is_taken_without_resolving() -> None:
    found = Path("/coordination/tasks/demo/live-task/enclosures/leaf-l1/series-contract.md")

    with patch.object(Path, "resolve", side_effect=AssertionError("resolved a path")):
        _require_named_place(cast(Any, SimpleNamespace(contract_path=found)), found)


def test_a_contract_whose_cell_names_its_file_through_a_link_is_still_taken(
    tmp_path: Path,
) -> None:
    found = tmp_path / "coordination/tasks/demo/live-task/enclosures/leaf-l1/series-contract.md"
    found.parent.mkdir(parents=True)
    found.write_text("contract")
    (tmp_path / "alias").symlink_to(tmp_path / "coordination")
    linked = tmp_path / "alias/tasks/demo/live-task/enclosures/leaf-l1/series-contract.md"

    _require_named_place(cast(Any, SimpleNamespace(contract_path=linked)), found)


def test_a_contract_outside_the_canonical_places_is_not_checked() -> None:
    found = Path("/coordination/tasks/demo/live-task/notes/evidence/series-contract.md")
    away = SimpleNamespace(contract_path=Path("/somewhere/else/series-contract.md"))

    _require_named_place(cast(Any, away), found)  # no refusal: a copy is simply never met

    named = SimpleNamespace(contract_path=found)
    _require_named_place(cast(Any, named), found)


def test_a_page_states_its_index_state_and_never_calls_a_partial_walk_complete() -> None:
    cut = PageCut(start=0, end=2, total=2)
    partial = page_block(
        cut,
        PageBinding(
            memory_tree_id="tree",
            selection_policy="policy",
            policy_version="v1",
            manifest_digest="digest",
            index_state="partial",
        ),
    )
    complete = page_block(
        cut,
        PageBinding(
            memory_tree_id="tree",
            selection_policy="policy",
            policy_version="v1",
            manifest_digest="digest",
        ),
    )

    assert partial["indexState"] == "partial"
    assert partial["enumerationComplete"] is False
    assert complete["indexState"] == "complete"
    assert complete["enumerationComplete"] is True


def test_knowledge_index_answers_legacy_format_for_an_unconverted_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "onboarding").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=memory, check=True)

    code = knowledge_index.run(
        argparse.Namespace(
            cache_dir=tmp_path / "cache",
            coordination_root=None,
            memory_root=memory,
            repository=None,
            revision=None,
        )
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 2
    assert printed["state"] == "legacy-format"
    assert printed["converted"] is False
    assert "knowledge/layout.json" in printed["refusal"]


def test_the_mounted_writer_refuses_before_it_writes() -> None:
    config = cast(Any, SimpleNamespace())

    missing = knowledge_write_payload(WriteToolRequest(), config)
    blank = knowledge_write_payload(
        WriteToolRequest(contract_path="/no/contract.md", handoff={"records": []}), config
    )
    both = knowledge_write_payload(
        WriteToolRequest(handoff={}, handoff_path="/tmp/list.json"), config
    )

    assert missing["state"] == "refused" and "handoff" in missing["refusalDetail"]
    assert "authorization" in blank["refusalDetail"]
    assert both["state"] == "refused" and "never both" in both["refusalDetail"]


def test_the_writer_and_the_reads_have_their_answer_rows() -> None:
    assert bound_for("knowledge_write") > 0
    assert bound_for("knowledge_read") > 0


# --- MIK-R83 rule 6 through the registered handler ---------------------------------


@pytest.fixture
def converted_reader_world() -> Iterator[EntryPointWorld]:
    world = EntryPointWorld()
    try:
        write(world.code, {CODE_FILE: CODE})
        commit_all(world.code, "anchored code base")
        write(world.memory, base_memory(world.code))
        commit_all(world.memory, "converted memory base")
        yield world
    finally:
        world.close()


def _linked_assumption(world: EntryPointWorld, targets: list[Any]) -> None:
    record = AssumptionRecord.model_validate(
        {
            "schema": "ar-assumption/v1",
            "id": "ASM-ABC123",
            "revision": 1,
            "status": "accepted",
            "origin": {"task": "fixture", "leaf": "fixture-L1"},
            "proposition": "The linked implementation preserves the landing order.",
            "basis": "The recorded targets are measured at the selected tree.",
            "links": [{"relation": "underlies", "target": target} for target in targets],
        }
    )
    write(
        world.memory,
        {"knowledge/assumptions/ASM-ABC123-targets.json": json.dumps(record.to_document())},
    )
    commit_all(world.memory, "record explicit targets")


def _registered_target_states(world: EntryPointWorld, tree: str) -> list[dict[str, Any]]:
    outcome, answer = world.call(
        "knowledge_read",
        {
            "memoryRoot": str(world.memory),
            "repositoryRoot": str(world.code),
            "codeTreeId": tree,
            "view": "record",
            "recordId": "ASM-ABC123",
        },
    )
    assert outcome == "RETURNED" and answer is not None, answer
    assert answer["state"] == "view", answer
    return [row["targetState"] for row in answer["outgoing"]]


def _dashboard_target_states(world: EntryPointWorld) -> list[dict[str, Any]]:
    app = FastAPI()
    register_knowledge_reader_route(
        app, lambda query: read_knowledge_reader(load_config(world.settings), query)
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/knowledge/reader/record", params={"repo": REPO, "id": "ASM-ABC123"}
        )
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["state"] == "view", answer
    return [row["targetState"] for row in answer["outgoing"]]


@pytest.mark.parametrize("locator", ["symbol", "line_range", "file"])
def test_registered_record_targets_observe_full_anchors_and_selected_routes(
    converted_reader_world: EntryPointWorld, locator: str
) -> None:
    world = converted_reader_world
    sidecar = json.loads((world.memory / f"onboarding/{CODE_FILE}.json").read_text())
    anchor = {"path": CODE_FILE, **sidecar["realizes"][0]["anchor"]}
    if locator != "symbol":
        requested = (
            {"kind": "file"} if locator == "file" else {"kind": "line_range", "start": 4, "end": 6}
        )
        anchor = {
            "path": CODE_FILE,
            **CodeSnapshot.capture(world.code).resolve(CODE_FILE, requested).to_document(),
        }
    _linked_assumption(world, [anchor, "route:pkg", "route:new-route"])
    baseline = git(world.code, "rev-parse", "HEAD^{tree}")

    initial = _registered_target_states(world, baseline)
    assert [row["state"] for row in initial] == ["current", "current", "stale"]
    (world.code / CODE_FILE).write_text(
        CODE.replace("return (code, memory)", "return (memory, code)")
    )
    write(world.code, {"new-route/file.py": "added = True\n"})
    edited = _registered_target_states(world, "working-tree")
    assert [row["state"] for row in edited] == ["stale", "current", "current"], edited
    assert edited[0]["recordedBlob"] == anchor["blob"]
    assert edited[0]["observedBlob"] != anchor["blob"]
    assert _dashboard_target_states(world) == edited
    # A committed selection ignores later files and edits in the checkout.
    assert _registered_target_states(world, baseline) == initial
    (world.code / CODE_FILE).unlink()
    missing = _registered_target_states(world, "working-tree")
    assert [row["state"] for row in missing] == ["stale", "stale", "current"]
    assert "absent" in missing[0]["reason"]
    unavailable = _registered_target_states(world, "0" * 40)
    assert {row["state"] for row in unavailable} == {"unverifiable"}
    assert all("cannot be read" in row["reason"] for row in unavailable)


@pytest.mark.parametrize("archived", [False, True])
def test_registered_record_requirement_targets_preserve_owner_answers_and_latest_version(
    converted_reader_world: EntryPointWorld, archived: bool
) -> None:
    world = converted_reader_world
    path = "requirements/REQ-R01-v1.md"
    task = {"repository": REPO, "path": "requirement-owner"}
    ref = {"task": task, "packet": path, "id": "REQ-R01", "version": "v1"}
    task_root = world.coord / "tasks" / REPO
    task_root /= "0_archive/requirement-owner" if archived else "requirement-owner"
    write(
        task_root,
        {
            path: "# Requirement\n\n| Field | Value |\n| --- | --- |\n| Stable ID | REQ-R01 |\n| Version | v1 |\n",
            "requirements/manifest.json": json.dumps(
                {
                    "format": "approved-requirement-corpus",
                    "packets": [
                        {"id": "REQ-R01", "version": "v1", "state": "approved"},
                        {"id": "REQ-R01", "version": "v3", "state": "approved"},
                        {"id": "REQ-R01", "version": "v4", "state": "draft"},
                    ],
                }
            ),
        },
    )
    _linked_assumption(
        world, [ref, {**ref, "version": "v2"}, {**ref, "packet": "requirements/missing.md"}]
    )
    states = _registered_target_states(world, "working-tree")
    assert [row["state"] for row in states] == ["resolved", "unresolved", "unresolved"]
    assert [row["code"] for row in states] == [
        "",
        "task-intent-requirement-packet-version-mismatch",
        "task-intent-requirement-packet-missing",
    ]
    assert all(row["archived"] is archived and row["latestApproved"] == "v3" for row in states)
    assert "v2" in states[1]["detail"]
    assert _dashboard_target_states(world) == states


def test_a_card_omitted_by_a_stale_route_index_is_served_registered(
    converted_reader_world: EntryPointWorld,
) -> None:
    world = converted_reader_world
    card = world.memory / "onboarding" / f"{CODE_FILE}.md"
    card.unlink()
    index = world.memory / "onboarding" / "overview.index.json"
    index.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "agents-remember-route-index",
                "route": "",
                "sourceScope": ["**"],
                "coveredFiles": [],
            }
        ),
        encoding="utf-8",
    )
    # The card is added after the index; the stale index cannot name it.
    card.write_text("# Card added after the index\n", encoding="utf-8")

    outcome, answer = world.call("read_ar_files", {"repo_id": REPO, "files": [CODE_FILE]})

    assert outcome == "RETURNED" and answer is not None, answer
    row = answer["files"][0]
    assert row["cardStatus"] == "found", answer
    assert (world.code / row["card"]).resolve() == card, answer
    assert row["routeIndexStale"] is True, answer
    assert answer["routeIndexStale"] is True, answer
    assert answer["routeIndexRefresh"] == "route_index_refresh", answer


def test_a_registered_leaf_read_observes_both_edited_worktrees(
    converted_reader_world: EntryPointWorld,
) -> None:
    world = converted_reader_world
    world.build()
    contract = load_contract(world.contract)
    code, memory = contract.code_worktree, contract.memory_worktree
    assert memory is not None
    assert (code / ".git").is_file() and (memory / ".git").is_file()
    assert git(code, "rev-parse", "HEAD") == git(world.code, "rev-parse", "HEAD")
    assert git(memory, "rev-parse", "HEAD") == git(world.memory, "rev-parse", "HEAD")
    (code / CODE_FILE).write_text(
        CODE.replace("return (code, memory)", "return (memory, code)"), encoding="utf-8"
    )
    card = memory / "onboarding" / f"{CODE_FILE}.md"
    card.write_text("# Leaf-only landing\n\nCode and memory land as one pair.\n", encoding="utf-8")

    outcome, leaf = world.call(
        "read_ar_files",
        {
            "repo_id": REPO,
            "files": [CODE_FILE],
            "task_context": {
                "task_document_ref": world.leaf_ref,
                "contract_path": str(world.contract),
            },
        },
    )
    assert outcome == "RETURNED" and leaf is not None, leaf
    assert leaf["worktreeScope"] == "leaf", leaf
    assert leaf["sourceRoot"] == code.as_posix(), leaf
    assert leaf["memoryRoot"] == memory.as_posix(), leaf
    assert leaf["codeState"] == "working-tree", leaf
    row = leaf["files"][0]
    assert (code.parent / row["card"]).resolve() == card, leaf
    assert row["cardState"] == "stale" and row["recordCounts"]["stale"] == 1, leaf
    assert row["actionNeeded"][0]["heading"] == "Leaf-only landing", leaf

    official_outcome, official = world.call(
        "read_ar_files", {"repo_id": REPO, "files": [CODE_FILE]}
    )
    assert official_outcome == "RETURNED" and official is not None, official
    assert official["worktreeScope"] == "official-line", official
    assert official["files"][0]["recordCounts"]["stale"] == 0, official
    assert (world.code / CODE_FILE).read_text(encoding="utf-8") == CODE
    assert "Leaf-only" not in (world.memory / "onboarding" / f"{CODE_FILE}.md").read_text(
        encoding="utf-8"
    )
    assert git(code, "diff", "--name-only") == CODE_FILE
    assert git(memory, "diff", "--name-only") == f"onboarding/{CODE_FILE}.md"


def _leaf_reader_world(tmp_path: Path) -> tuple[Any, Any, Any, Any, Any]:
    """A leaf enclosure the task-scoped reader admits: its config, context, roots and admission."""

    root = tmp_path
    coordinator = root / "ar-coordination"
    task_root = coordinator / "tasks" / "repo" / "master"
    workspace = coordinator / "worktrees" / "repo" / "leaf-group"
    code = workspace / "leaf-code"
    memory = workspace / "leaf-memory"
    contract_path = leaf_enclosure_path(task_root, "L7")
    for path in (code, memory, task_root, contract_path.parent):
        path.mkdir(parents=True, exist_ok=True)
    (code / "pkg").mkdir()
    (code / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (coordinator / "memory-repos" / "ar-repo").mkdir(parents=True, exist_ok=True)
    config_path = root / "settings" / "mcp.json"
    config_path.parent.mkdir()
    config_path.write_text("{}", encoding="utf-8")
    document = TaskDocument.model_validate(
        {
            "id": "L7",
            "slug": "l7",
            "title": "Leaf",
            "kind": "subTask",
            "status": "inProgress",
            "repo": "repo",
            "createdAt": "2026-09-24T10:00",
            "master": "task.md",
            "enclosures": [{"leafId": "L7", "enclosurePath": contract_path.as_posix()}],
        }
    )
    write_task_doc(task_root, document)
    ref = TaskDocumentRef(repository="repo", path="master/l7.json")
    base = McpRuntimeConfig(
        config_path=config_path,
        coordination_root=coordinator,
        workspace_root=root,
        transcript_root=coordinator / "logs" / "mcp",
        repositories={
            "repo": RepositoryScope(
                repo_id="repo",
                path=root / "official-repo",
                memory_root=root / "official-memory",
            )
        },
    )
    contract = SimpleNamespace(
        kind="leaf", leaf_id="L7", code_worktree=code, memory_worktree=memory
    )
    admission = ConfiguredContractAccepted(
        contract_path=contract_path,
        contract=cast(WorktreeContract, contract),
        location=cast(LifecycleOperationLocation, SimpleNamespace(worktree_group=workspace)),
    )
    context = TaskScopedReaderContext(task_document_ref=ref, contract_path=contract_path.as_posix())
    return base, context, code, memory, admission


def test_a_leaf_read_names_its_own_source_and_memory_roots(tmp_path: Path) -> None:
    base, context, code, _leaf_memory, admission = _leaf_reader_world(tmp_path)

    with patch(
        "agents_remember.application.task_scoped_mcp.admit_configured_contract",
        return_value=admission,
    ):
        answer = read_ar_files_payload(base, "repo", ["pkg/mod.py"], task_context=context)

    assert answer["worktreeScope"] == "leaf"
    assert answer["sourceRoot"] == code.as_posix()
    # The card root is the memory tree the scope resolves; this world models the topology,
    # not a converted leaf memory tree, so the answer must name a root and no other.
    assert isinstance(answer["memoryRoot"], str) and answer["memoryRoot"]
    assert answer["files"][0]["exists"] is True


def test_an_unscoped_read_is_identified_as_the_official_line(tmp_path: Path) -> None:
    base, _context, _code, _memory, _admission = _leaf_reader_world(tmp_path)

    answer = read_ar_files_payload(base, "repo", ["pkg/mod.py"], task_context=None)

    assert answer["worktreeScope"] == "official-line"
    assert answer["sourceRoot"] == (tmp_path / "official-repo").as_posix()
    assert (
        answer["memoryRoot"]
        == (tmp_path / "ar-coordination" / "memory-repos" / "ar-repo").as_posix()
    )


def test_a_leaf_whose_memory_worktree_is_gone_is_refused_by_name(tmp_path: Path) -> None:
    base, context, _code, memory, admission = _leaf_reader_world(tmp_path)
    shutil.rmtree(memory)

    with (
        patch(
            "agents_remember.application.task_scoped_mcp.admit_configured_contract",
            return_value=admission,
        ),
        pytest.raises(ValueError) as raised,
    ):
        read_ar_files_payload(base, "repo", ["pkg/mod.py"], task_context=context)

    assert memory.as_posix() in str(raised.value)


# --- MIK-R73 rule 7: the working-tree selector and the held capture -----------------


def _repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "test@example.invalid"],
        ["config", "user.name", "Agents Remember Tests"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True, capture_output=True)
    return root


def _read_request(repository: Path, *, tree: str | None = None) -> Any:
    return ReadToolRequest(
        memory_root="/memory",
        view="invariant",
        repository_root=repository.as_posix(),
        code_tree_id=tree,
    )


def test_the_working_tree_selector_captures_and_keeps_the_store_readable(tmp_path: Path) -> None:
    """``codeTreeId: "working-tree"`` reads the bytes on disk, held until the read ends."""

    repository = _repository(tmp_path / "code")
    (repository / "pkg" / "mod.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    edited = b"def f():\n    return 2\n"
    blob = subprocess.run(
        ["git", "-C", str(repository), "hash-object", "pkg/mod.py"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    source = _observe_source(_read_request(repository, tree="working-tree"), None)
    try:
        assert source.code_state == "working-tree"
        assert source.capture is not None
        store = source.capture.object_directory
        # The observer reads the captured blob through the isolated store it holds...
        assert read_git_blobs_bytes(repository, [blob], object_inputs=store)[blob] == edited
        # ...the repository itself still does not hold it...
        absent = subprocess.run(
            ["git", "-C", str(repository), "cat-file", "-e", blob],
            capture_output=True,
            check=False,
        )
        assert absent.returncode != 0
    finally:
        source.close()

    assert not store.exists()


def test_a_clean_checkout_reads_its_commit_and_captures_nothing(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "clean")

    source = _observe_source(_read_request(repository), None)
    try:
        assert source.code_state == "commit"
        assert source.capture is None
        assert source.code_tree_id
    finally:
        source.close()
