"""Memory-review verdict references name existing files and map to notes/reports/<rel>."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.application import worktree_tools
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.cli import role_launch_enclosure, role_launch_workspace
from agents_remember.memory.conversion import state_owners
from agents_remember.memory_quality.style.citations.source_index_cache import (
    TerminalNamespaceGuard,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentRef
from agents_remember.tasks.store import read_task_doc
from agents_remember.worktrees import role_reports
from agents_remember.worktrees.memory_verdict_ref import (
    MEMORY_VERDICT_REF_INVALID,
    collected_verdict_ref,
    unmapped_verdict_ref_note,
)
from agents_remember.worktrees.modules.git import head_commit
from agents_remember.worktrees.review_note import committed_review_note
from agents_remember.worktrees.role_reports import (
    COLLECTED_REPORT_DIRECTORY,
    COLLECTED_REPORT_PARTS,
    LEGACY_ROLE_REPORT_ALIAS,
    ROLE_REPORT_DIRECTORY,
    collect_group_reports,
    collected_report_ref,
    is_collected_report_ref,
)
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_direct_landing import _converted_request, _converted_series_fixture, direct_landing_tool
from test_knowledge_closeout_gate import LEAF
from test_worktree_support import git
from transaction_only_worktree_delivery_test_support import (
    _apply,
    _converted_closeout_fixture,
)


class _NoManagedCitationCache:
    def guard(
        self,
        contract: WorktreeContract,
        *,
        requested_contract_path: Path,
        completed_contract_with_remains: bool = False,
    ):
        del contract, requested_contract_path, completed_contract_with_remains
        return nullcontext(TerminalNamespaceGuard(None, None, None))


@pytest.fixture
def bound_services() -> Iterator[None]:
    bind_worktree_services(
        replace(build_default_worktree_services(), citation_guard=_NoManagedCitationCache())
    )
    try:
        yield
    finally:
        reset_worktree_services()


def _memory_tree(memory: Path) -> str:
    with tempfile.TemporaryDirectory() as scratch:
        env = {**os.environ, "GIT_INDEX_FILE": f"{scratch}/index"}
        for args in (["read-tree", "HEAD"], ["add", "-A"]):
            subprocess.run(["git", *args], cwd=memory, env=env, check=True)
        return subprocess.run(
            ["git", "write-tree"], cwd=memory, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()


def _leaf_target(contract: WorktreeContract) -> TaskDocTarget:
    return TaskDocTarget(
        repo_id=contract.repo_name,
        contract_path=contract.contract_path.as_posix(),
        slug="leaf-1",
    )


def _record_memory_round(
    config: Any,
    contract: WorktreeContract,
    *,
    verdict_ref: str | None,
    begin: bool = True,
    baseline: bool = True,
) -> dict[str, Any]:
    target = _leaf_target(contract)
    if begin:
        task_doc_tool(
            config,
            target,
            operation="begin_review",
            edit=TaskDocEdit(review={"reviewKind": "memory"}),
        )
    assert contract.memory_worktree is not None
    payload: dict[str, Any] = {
        "reviewKind": "memory",
        "verdict": "pass",
        "candidateTree": _memory_tree(contract.memory_worktree),
        **({"findings": []} if baseline else {"remainingFindingIds": []}),
    }
    if verdict_ref is not None:
        payload["verdictRef"] = verdict_ref
    return task_doc_tool(
        config,
        target,
        operation="record_review",
        edit=TaskDocEdit(review=payload),
    )


def test_collected_report_ref_defines_the_collected_path() -> None:
    assert collected_report_ref("reviewer/req-1/verdict.md") == (
        "notes/reports/reviewer/req-1/verdict.md"
    )
    assert collected_report_ref(Path("reviewer/req-1/verdict.md")) == (
        "notes/reports/reviewer/req-1/verdict.md"
    )
    assert is_collected_report_ref("notes/reports/reviewer/req-1/verdict.md") is True
    assert is_collected_report_ref("notes/reports") is False
    assert is_collected_report_ref("notes/reports/../escape.md") is False
    assert is_collected_report_ref("/abs/notes/reports/verdict.md") is False
    with pytest.raises(ValueError, match="relative path"):
        collected_report_ref("")
    with pytest.raises(ValueError, match="relative path"):
        collected_report_ref("../escape.md")


def test_record_review_accepts_notes_reports_before_copy_exists_and_ignores_series_group(
    tmp_path: Path, bound_services: None
) -> None:
    contract, config = _converted_closeout_fixture(tmp_path)
    rel = "reviewer/req-1/memory-verdict.md"
    local_verdict = contract.worktree_group / ROLE_REPORT_DIRECTORY / rel
    local_verdict.parent.mkdir(parents=True, exist_ok=True)
    local_verdict.write_text("# Memory PASS\n", encoding="utf-8")
    canonical_verdict = contract.task_root / "notes" / "reports" / rel
    assert not canonical_verdict.exists()

    recorded = _record_memory_round(config, contract, verdict_ref=f"notes/reports/{rel}")
    assert recorded["ok"] is True
    assert recorded["memoryReviewState"]["recordedRounds"][-1]["verdictRef"] == (
        f"notes/reports/{rel}"
    )

    # A round without verdictRef stays accepted as today and yields no fact at closeout.
    no_ref = _record_memory_round(config, contract, verdict_ref=None, begin=True, baseline=False)
    assert no_ref["ok"] is True
    assert no_ref["memoryReviewState"]["recordedRounds"][-1]["verdictRef"] is None

    # Code rounds keep their present rules even when verdictRef is a bare name.
    code_target = _leaf_target(contract)
    task_doc_tool(config, code_target, operation="begin_review", edit=TaskDocEdit(review={}))
    code_res = task_doc_tool(
        config,
        code_target,
        operation="record_review",
        edit=TaskDocEdit(review={"verdict": "pass", "findings": [], "verdictRef": "code-only.md"}),
    )
    assert code_res["ok"] is True


def test_record_review_uses_bound_leaf_contract_not_series_contract(
    tmp_path: Path, bound_services: None
) -> None:
    fixture = _converted_series_fixture(tmp_path)
    config = fixture["config"]
    series_contract = fixture["contract"]
    rel = "reviewer/req-series/verdict.md"
    # Place a file only in the series worktree_group, with no leaf enclosure contract on disk.
    series_file = series_contract.worktree_group / ROLE_REPORT_DIRECTORY / rel
    series_file.parent.mkdir(parents=True, exist_ok=True)
    series_file.write_text("# Series group file\n", encoding="utf-8")

    target = TaskDocTarget(
        repo_id=series_contract.repo_name,
        task_name=series_contract.task_name,
        slug="leaf",
    )
    task_doc_tool(
        config,
        target,
        operation="begin_review",
        edit=TaskDocEdit(review={"reviewKind": "memory"}),
    )
    with pytest.raises(TaskDocError, match=MEMORY_VERDICT_REF_INVALID) as exc_info:
        task_doc_tool(
            config,
            target,
            operation="record_review",
            edit=TaskDocEdit(
                review={
                    "reviewKind": "memory",
                    "verdict": "pass",
                    "findings": [],
                    "verdictRef": f"role-reports/{rel}",
                }
            ),
        )
    message = str(exc_info.value)
    assert "absolute path" in message
    assert "role-reports/<rel>" in message
    assert "notes/reports/<rel>" in message


@pytest.mark.parametrize(
    "bad_ref",
    [
        "verdict.md",
        "reports/verdict.md",
        "/nonexistent/path/verdict.md",
        "role-reports/reviewer/req-1/missing.md",
        "notes/reports/reviewer/req-1/missing.md",
        "notes/reports/../escape.md",
        "   ",
    ],
)
def test_record_review_refuses_bare_and_missing_verdict_refs_on_ordinary_and_record_only_paths(
    tmp_path: Path, bound_services: None, bad_ref: str
) -> None:
    contract, config = _converted_closeout_fixture(tmp_path)
    rel = "reviewer/req-1/existing.md"
    existing = contract.worktree_group / ROLE_REPORT_DIRECTORY / rel
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("# Existing verdict\n", encoding="utf-8")
    leaf_json = contract.task_root / "leaf-1.json"

    # Ordinary path refusal leaves task document unchanged.
    task_doc_tool(
        config,
        _leaf_target(contract),
        operation="begin_review",
        edit=TaskDocEdit(review={"reviewKind": "memory"}),
    )
    before_ordinary = leaf_json.read_bytes()
    with pytest.raises(TaskDocError, match=MEMORY_VERDICT_REF_INVALID) as ordinary_exc:
        _record_memory_round(config, contract, verdict_ref=bad_ref, begin=False, baseline=True)
    for expected in ("absolute path", "role-reports/<rel>", "notes/reports/<rel>"):
        assert expected in str(ordinary_exc.value)
    assert leaf_json.read_bytes() == before_ordinary

    # Record a valid round first, then refuse bad_ref on the record-only path.
    assert _record_memory_round(
        config, contract, verdict_ref=f"role-reports/{rel}", begin=False, baseline=True
    )["ok"]
    before_record_only = leaf_json.read_bytes()
    with pytest.raises(TaskDocError, match=MEMORY_VERDICT_REF_INVALID) as record_only_exc:
        _record_memory_round(config, contract, verdict_ref=bad_ref, begin=False, baseline=False)
    for expected in ("absolute path", "role-reports/<rel>", "notes/reports/<rel>"):
        assert expected in str(record_only_exc.value)
    assert leaf_json.read_bytes() == before_record_only


@pytest.mark.parametrize(
    "form",
    [
        "absolute-role-reports",
        "relative-role-reports",
        "relative-notes-reports",
        "absolute-notes-reports",
        "legacy-alias-absolute",
        "legacy-alias-relative",
    ],
)
def test_closeout_maps_every_valid_verdict_ref_form_to_collected_path(
    tmp_path: Path, bound_services: None, form: str
) -> None:
    contract, config = _converted_closeout_fixture(tmp_path)
    rel = "reviewer/req-closeout/memory-verdict.md"
    local_file = contract.worktree_group / ROLE_REPORT_DIRECTORY / rel
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("# Memory PASS\n", encoding="utf-8")
    canonical_file = contract.task_root / "notes" / "reports" / rel

    if form == "absolute-role-reports":
        verdict_ref = local_file.as_posix()
    elif form == "relative-role-reports":
        verdict_ref = f"{ROLE_REPORT_DIRECTORY}/{rel}"
    elif form == "relative-notes-reports":
        verdict_ref = f"notes/reports/{rel}"
    elif form == "absolute-notes-reports":
        canonical_file.parent.mkdir(parents=True, exist_ok=True)
        canonical_file.write_text("# Memory PASS\n", encoding="utf-8")
        verdict_ref = canonical_file.as_posix()
    elif form == "legacy-alias-absolute":
        alias = contract.worktree_group / LEGACY_ROLE_REPORT_ALIAS
        alias.symlink_to(contract.worktree_group / ROLE_REPORT_DIRECTORY)
        verdict_ref = (alias / rel).as_posix()
    else:
        verdict_ref = f"{LEGACY_ROLE_REPORT_ALIAS}/{rel}"

    if form == "legacy-alias-relative":
        # Legacy relative alias recorded before this rule: written straight into the task doc.
        assert contract.memory_worktree is not None
        leaf_json = contract.task_root / "leaf-1.json"
        doc = json.loads(leaf_json.read_text(encoding="utf-8"))
        doc["memoryReviewState"] = {
            "round": 1,
            "pending": False,
            "recordedRounds": [
                {
                    "round": 1,
                    "kind": "memory",
                    "findingsRemain": False,
                    "verdict": "pass",
                    "verdictRef": verdict_ref,
                    "memoryTree": _memory_tree(contract.memory_worktree),
                }
            ],
        }
        leaf_json.write_text(json.dumps(doc), encoding="utf-8")
    else:
        assert _record_memory_round(config, contract, verdict_ref=verdict_ref)["ok"]

    result = _apply(config, contract)
    assert result["ok"], result
    assert contract.memory_worktree is not None
    history = json.loads(
        git(contract.memory_worktree, "show", f"HEAD:knowledge/history/{contract.leaf_id}.json")
    )
    assert history["review"]["verdictRef"] == f"notes/reports/{rel}"
    assert "could not be mapped" not in str(result["summary"])


def test_collected_verdict_ref_never_matches_another_worktree_group(tmp_path: Path) -> None:
    own_group = tmp_path / "worktrees" / "repo" / "own-leaf-ar"
    other_group = tmp_path / "worktrees" / "repo" / "other-leaf-ar"
    task_root = tmp_path / "tasks" / "repo" / "master"
    foreign = (other_group / ROLE_REPORT_DIRECTORY / "reviewer/req/v.md").as_posix()
    assert collected_verdict_ref(foreign, worktree_group=own_group, task_root=task_root) == foreign


@pytest.mark.parametrize(
    "form",
    [
        "absolute-role-reports",
        "relative-role-reports",
        "relative-notes-reports",
        "legacy-alias-absolute",
    ],
)
def test_direct_landing_maps_verdict_ref_forms_to_collected_path(
    tmp_path: Path, bound_services: None, form: str
) -> None:
    fixture = _converted_series_fixture(tmp_path)
    world = fixture["world"]
    world.rows()
    contract = fixture["contract"]
    rel = "reviewer/req-direct/memory-verdict.md"
    local_file = contract.worktree_group / ROLE_REPORT_DIRECTORY / rel
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_text("# Direct Memory PASS\n", encoding="utf-8")

    if form == "absolute-role-reports":
        verdict_ref = local_file.as_posix()
    elif form == "relative-role-reports":
        verdict_ref = f"{ROLE_REPORT_DIRECTORY}/{rel}"
    elif form == "relative-notes-reports":
        verdict_ref = f"notes/reports/{rel}"
    else:
        alias = contract.worktree_group / LEGACY_ROLE_REPORT_ALIAS
        alias.symlink_to(contract.worktree_group / ROLE_REPORT_DIRECTORY)
        verdict_ref = (alias / rel).as_posix()

    tree = _memory_tree(world.memory)
    leaf_json = world.task_root / "leaf.json"
    leaf = json.loads(leaf_json.read_text(encoding="utf-8"))
    leaf["memoryReviewState"] = {
        "round": 1,
        "pending": False,
        "recordedRounds": [
            {
                "round": 1,
                "kind": "memory",
                "findingsRemain": False,
                "verdict": "pass",
                "verdictRef": verdict_ref,
                "memoryTree": tree,
            }
        ],
    }
    leaf_json.write_text(json.dumps(leaf), encoding="utf-8")

    git(world.memory, "add", "-A")
    git(
        world.memory,
        "commit",
        "--allow-empty",
        "-m",
        f"open leaf history\n\nCode-Commit: {world.code_base}",
    )
    git(world.code, "commit", "--allow-empty", "-m", "leaf without code changes")
    landed = direct_landing_tool(
        fixture["config"], _converted_request(fixture, head_commit(world.code))
    )
    assert landed["ok"], landed
    history = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))
    assert history["review"]["verdictRef"] == f"notes/reports/{rel}"
    assert "could not be mapped" not in str(landed["summary"])
    assert committed_review_note(world.memory, head_commit(world.memory), LEAF) == ""


def test_collect_group_reports_and_collected_report_ref_share_one_definition(
    tmp_path: Path, bound_services: None
) -> None:
    assert PurePosixPath(COLLECTED_REPORT_DIRECTORY).parts == COLLECTED_REPORT_PARTS
    contract, _config = _converted_closeout_fixture(tmp_path)
    rel = "reviewer/req-single/verdict.md"
    source = contract.worktree_group / ROLE_REPORT_DIRECTORY / rel
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# Shared collected definition\n", encoding="utf-8")

    collected = collect_group_reports(contract, dry_run=False)
    assert collected["state"] == "collected"
    assert collected["canonical"] == (contract.task_root / COLLECTED_REPORT_DIRECTORY).as_posix()
    expected = contract.task_root / collected_report_ref(rel)
    assert expected == Path(collected["canonical"]) / rel
    assert expected.is_file()
    assert expected.read_text(encoding="utf-8") == "# Shared collected definition\n"


def _has_duplicate_collected_literal(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Div)
            and isinstance(node.right, ast.Constant)
            and node.right.value == "reports"
            and isinstance(node.left, ast.BinOp)
            and isinstance(node.left.op, ast.Div)
            and isinstance(node.left.right, ast.Constant)
            and node.left.right.value == "notes"
        ):
            return True
    return False


def test_role_launch_task_report_root_producers_follow_collected_report_directory(
    tmp_path: Path, bound_services: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Static check across all taskReportRoot producers in mcp/src/agents_remember/.
    src_root = Path(__file__).resolve().parents[1] / "src" / "agents_remember"
    producers: list[Path] = []
    for path in sorted(src_root.rglob("*.py")):
        if "package_data" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if 'workspace["taskReportRoot"] =' in source or "taskReportRoot=" in source:
            producers.append(path)
            assert not _has_duplicate_collected_literal(ast.parse(source)), (
                f"{path.relative_to(src_root)} duplicates 'notes' / 'reports' instead of "
                "using COLLECTED_REPORT_DIRECTORY"
            )
    assert {p.name for p in producers} == {
        "role_launch_enclosure.py",
        "role_launch_workspace.py",
    }

    # 2. Enclosed leaf launch workspace follows COLLECTED_REPORT_DIRECTORY (default & overridden).
    contract, config = _converted_closeout_fixture(tmp_path / "enclosed")
    leaf_path = contract.task_root / "leaf-1.json"
    leaf_doc = ResolvedTaskDocument(
        ref=TaskDocumentRef(repository="agents-remember", path="series/leaf-1.json"),
        path=leaf_path,
        document=read_task_doc(leaf_path),
    )
    enclosed_default: dict[str, str] = {
        "path": contract.worktree_group.resolve().as_posix(),
        "contractPath": contract.contract_path.as_posix(),
        "lifecycleId": contract.lifecycle_id,
    }
    role_launch_workspace._prepare_enclosed_workspace(config, leaf_doc, enclosed_default, {})
    assert (
        enclosed_default["taskReportRoot"]
        == (contract.task_root / COLLECTED_REPORT_DIRECTORY).resolve().as_posix()
    )

    custom_dir = "notes/custom-collected"
    monkeypatch.setattr(role_launch_workspace, "COLLECTED_REPORT_DIRECTORY", custom_dir)
    monkeypatch.setattr(role_launch_enclosure, "COLLECTED_REPORT_DIRECTORY", custom_dir)

    enclosed_custom: dict[str, str] = {
        "path": contract.worktree_group.resolve().as_posix(),
        "contractPath": contract.contract_path.as_posix(),
        "lifecycleId": contract.lifecycle_id,
    }
    role_launch_workspace._prepare_enclosed_workspace(config, leaf_doc, enclosed_custom, {})
    assert (
        enclosed_custom["taskReportRoot"] == (contract.task_root / custom_dir).resolve().as_posix()
    )
    assert (contract.task_root / custom_dir).is_dir()

    # 3. Direct leaf launch workspace follows COLLECTED_REPORT_DIRECTORY without creating notes/reports.
    fixture = _converted_series_fixture(tmp_path / "direct")
    world = fixture["world"]
    direct_repo = fixture["contract"].repo_name
    direct_leaf_path = world.task_root / "leaf.json"
    direct_rel = direct_leaf_path.relative_to(
        fixture["config"].coordination_root / "tasks" / direct_repo
    ).as_posix()
    direct_leaf = ResolvedTaskDocument(
        ref=TaskDocumentRef(repository=direct_repo, path=direct_rel),
        path=direct_leaf_path,
        document=read_task_doc(direct_leaf_path),
    )
    direct_context: Any = SimpleNamespace(task=direct_leaf)
    direct_ws = role_launch_workspace._resolve_direct_workspace(fixture["config"], direct_context)
    assert direct_ws["taskReportRoot"] == (world.task_root / custom_dir).resolve().as_posix()
    assert (world.task_root / custom_dir).is_dir()
    assert not (world.task_root / "notes" / "reports").exists()

    # 4. Non-leaf prepare_role_folders follows COLLECTED_REPORT_DIRECTORY.
    non_leaf_root = tmp_path / "non-leaf-workspace"
    non_leaf_root.mkdir()
    req_id = uuid.uuid4()
    non_leaf_ws: dict[str, str] = {"path": non_leaf_root.as_posix()}
    non_leaf_context: Any = SimpleNamespace(role="manager", effective_task=direct_leaf)
    role_launch_enclosure.prepare_role_folders(non_leaf_context, non_leaf_ws, req_id)
    assert (
        non_leaf_ws["taskReportRoot"]
        == (world.task_root / custom_dir / "manager" / str(req_id)).as_posix()
    )


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            ids.add(id(node.body[0].value))
    return ids


def test_state_conversion_uses_shared_collected_report_ref_without_duplicate_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "agents_remember"
    state_owners_path = src_root / "memory" / "conversion" / "state_owners.py"
    tree = ast.parse(state_owners_path.read_text(encoding="utf-8"))
    imported_from_role_reports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "agents_remember.worktrees.role_reports"
        for alias in node.names
    }
    assert {
        "COLLECTED_REPORT_PARTS",
        "ROLE_REPORT_DIRECTORY",
        "collected_report_ref",
    } <= imported_from_role_reports

    docstrings = _docstring_node_ids(tree)
    assert not _has_duplicate_collected_literal(tree)
    for node in ast.walk(tree):
        if id(node) in docstrings:
            continue
        if isinstance(node, ast.Constant) and node.value in {
            COLLECTED_REPORT_DIRECTORY,
            ROLE_REPORT_DIRECTORY,
        }:
            raise AssertionError(
                f"state_owners.py duplicates collected/role report literal {node.value!r}"
            )
        if isinstance(node, ast.Tuple) and tuple(
            el.value for el in node.elts if isinstance(el, ast.Constant)
        ) == ("notes", "reports"):
            raise AssertionError("state_owners.py duplicates ('notes', 'reports') literal")

    rel = "reviewer/r1/verdict.md"
    assert state_owners._durable_verdict_ref(f"/worktrees/leaf/{ROLE_REPORT_DIRECTORY}/{rel}") == (
        collected_report_ref(rel)
    )
    assert state_owners._durable_verdict_ref(f"{ROLE_REPORT_DIRECTORY}/{rel}") == (
        collected_report_ref(rel)
    )
    monkeypatch.setattr(role_reports, "COLLECTED_REPORT_DIRECTORY", "notes/custom-collected")
    assert state_owners._durable_verdict_ref(f"/worktrees/leaf/{ROLE_REPORT_DIRECTORY}/{rel}") == (
        f"notes/custom-collected/{rel}"
    )


def test_unmapped_preexisting_verdict_ref_is_written_as_today_and_named_in_closeout(
    tmp_path: Path, bound_services: None
) -> None:
    contract, config = _converted_closeout_fixture(tmp_path)
    assert contract.memory_worktree is not None
    legacy_ref = "legacy-bare-verdict.md"
    leaf_json = contract.task_root / "leaf-1.json"
    doc = json.loads(leaf_json.read_text(encoding="utf-8"))
    doc["memoryReviewState"] = {
        "round": 1,
        "pending": False,
        "recordedRounds": [
            {
                "round": 1,
                "kind": "memory",
                "findingsRemain": False,
                "verdict": "pass",
                "verdictRef": legacy_ref,
                "memoryTree": _memory_tree(contract.memory_worktree),
            }
        ],
    }
    leaf_json.write_text(json.dumps(doc), encoding="utf-8")

    result = _apply(config, contract)
    assert result["ok"], result
    history = json.loads(
        git(contract.memory_worktree, "show", f"HEAD:knowledge/history/{contract.leaf_id}.json")
    )
    assert history["review"]["verdictRef"] == legacy_ref
    assert legacy_ref in str(result["summary"])
    assert "could not be mapped" in str(result["summary"])
    assert committed_review_note(
        contract.memory_worktree, head_commit(contract.memory_worktree), contract.leaf_id
    ) == unmapped_verdict_ref_note(legacy_ref)


def test_unmapped_preexisting_verdict_ref_is_written_as_today_and_named_in_direct_landing(
    tmp_path: Path, bound_services: None
) -> None:
    fixture = _converted_series_fixture(tmp_path)
    world = fixture["world"]
    world.rows()
    legacy_ref = "legacy-direct-bare.md"
    leaf_json = world.task_root / "leaf.json"
    leaf = json.loads(leaf_json.read_text(encoding="utf-8"))
    leaf["memoryReviewState"] = {
        "round": 1,
        "pending": False,
        "recordedRounds": [
            {
                "round": 1,
                "kind": "memory",
                "findingsRemain": False,
                "verdict": "pass",
                "verdictRef": legacy_ref,
                "memoryTree": _memory_tree(world.memory),
            }
        ],
    }
    leaf_json.write_text(json.dumps(leaf), encoding="utf-8")

    git(world.memory, "add", "-A")
    git(
        world.memory,
        "commit",
        "--allow-empty",
        "-m",
        f"open leaf history\n\nCode-Commit: {world.code_base}",
    )
    git(world.code, "commit", "--allow-empty", "-m", "leaf without code changes")
    landed = direct_landing_tool(
        fixture["config"], _converted_request(fixture, head_commit(world.code))
    )
    assert landed["ok"], landed
    history = json.loads(git(world.memory, "show", f"HEAD:knowledge/history/{LEAF}.json"))
    assert history["review"]["verdictRef"] == legacy_ref
    assert legacy_ref in str(landed["summary"])
    assert "could not be mapped" in str(landed["summary"])
    assert committed_review_note(
        world.memory, head_commit(world.memory), LEAF
    ) == unmapped_verdict_ref_note(legacy_ref)


def test_end_to_end_closeout_and_cleanup_keeps_verdict_ref_readable(
    tmp_path: Path, bound_services: None
) -> None:
    """Evidence 3: record an absolute worktree verdictRef, close out, integrate, clean up;
    the history record names notes/reports/<rel> and that file opens after cleanup."""

    contract, config = _converted_closeout_fixture(tmp_path)
    rel = "reviewer/0142f780-9710-50bc-84ab-b14ac6c70079/role-launch/memory-verdict.md"
    verdict_body = "# Memory Review Verdict: PASS\n"
    worktree_verdict = contract.worktree_group / ROLE_REPORT_DIRECTORY / rel
    worktree_verdict.parent.mkdir(parents=True, exist_ok=True)
    worktree_verdict.write_text(verdict_body, encoding="utf-8")

    recorded = _record_memory_round(config, contract, verdict_ref=worktree_verdict.as_posix())
    assert recorded["ok"] is True

    closed = _apply(config, contract)
    assert closed["ok"], closed
    integrated = worktree_tools.worktree_integrate_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
        strategy="ff-only",
        dry_run=False,
    )
    assert integrated["ok"], integrated
    cleaned = worktree_tools.worktree_cleanup_tool(
        config,
        contract_path=contract.contract_path.as_posix(),
        dry_run=False,
    )
    assert cleaned["ok"], cleaned

    # The worktree folder and original absolute path are gone after cleanup.
    assert not contract.worktree_group.exists()
    assert not worktree_verdict.exists()

    # The committed history record names notes/reports/<rel>, which opens after cleanup.
    assert contract.memory_repo_path is not None
    history = json.loads(
        git(
            contract.memory_repo_path,
            "show",
            f"{contract.memory_source_branch}:knowledge/history/{contract.leaf_id}.json",
        )
    )
    recorded_ref = history["review"]["verdictRef"]
    assert recorded_ref == f"notes/reports/{rel}"
    surviving = contract.task_root / recorded_ref
    assert surviving.is_file()
    assert surviving.read_text(encoding="utf-8") == verdict_body
