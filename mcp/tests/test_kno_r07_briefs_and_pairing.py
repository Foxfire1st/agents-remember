"""Focused unit tests for KNO-R07 rules 1-7: briefs refresh, honest read_ar_files, titles, CLI, and code-tree pairing."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.application.file_briefs.folder import BRIEFS, TREE_FILE, brief_location
from agents_remember.application.file_briefs.short_answer import (
    IN_THE_FILE,
    IN_THE_FILE_NOT_CURRENT,
    LEFT_OUT,
    LEFT_OUT_NOT_CURRENT,
    RULE_LEFT_OUT,
    RULE_LEFT_OUT_NOT_CURRENT,
    BriefLocation,
    counts_notice,
    whole,
    without_rule_texts,
)
from agents_remember.application.file_briefs.titles import clause_title, rule_title
from agents_remember.application.landing_answer_shapes import LANDING_KEPT_KEYS
from agents_remember.cli.__main__ import main as cli_main
from agents_remember.kernel.memory_ignore import ignore_briefs
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.knowledge_index.projection import text_uuid
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.worktrees.landing_briefs import (
    refresh_integrated_memory_briefs,
    refresh_recorded_landing_memory_briefs,
)
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from test_file_briefs import _brief, _rule
from test_knowledge_currentness import REVIEW, World, _body_edit
from test_knowledge_paging import _DEEP, _member_path, write_family_tree


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_git(repo: Path, *, branch: str = "main") -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", branch)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _minimal_contract(
    tmp_path: Path,
    *,
    code_repo: Path,
    memory_repo: Path,
    memory_mode: str = "external",
    memory_branch: str = "main",
) -> WorktreeContract:
    coord = tmp_path / "coordination"
    coord.mkdir(exist_ok=True)
    return WorktreeContract(
        task_id="261112-KNO-L7",
        task_name="261112_knowledge-tools-and-retrieval",
        repo_name="agents-remember",
        workflow_kind="chat-task",
        memory_mode=memory_mode,  # type: ignore[arg-type]
        coordination_root=coord,
        task_root=coord / "tasks",
        contract_path=coord / "contract.md",
        task_artifact=coord / "task.md",
        worktree_group=tmp_path / "group",
        code_repo_path=code_repo,
        code_source_branch="main",
        code_work_branch="task/code",
        code_base_commit="0" * 40,
        code_worktree=code_repo,
        memory_repo_path=memory_repo,
        memory_source_branch=memory_branch,
        memory_work_branch="task/mem",
        memory_base_commit="0" * 40,
        memory_worktree=memory_repo,
    )


def test_landing_kept_keys_preserves_briefs_problem_on_all_landing_routes() -> None:
    for tool in ("worktree_integrate", "worktree_checkpoint_landing", "worktree_record_landing"):
        assert "briefsProblem" in LANDING_KEPT_KEYS[tool], tool


def test_integrated_memory_briefs_skips_ledger_only_and_refreshes_content_changes(
    tmp_path: Path,
) -> None:
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    _init_git(code)
    (code / "app.py").write_text("x = 1\n", encoding="utf-8")
    code_commit = _commit_all(code, "init code")

    _init_git(memory)
    (memory / "knowledge").mkdir()
    (memory / LAYOUT_MARKER_PATH).write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}), encoding="utf-8"
    )
    (memory / "memory.md").write_text("base\n", encoding="utf-8")
    c1 = _commit_all(memory, "base memory")
    (memory / "memory.md").write_text("ledger only\n", encoding="utf-8")
    c2 = _commit_all(memory, "ledger only")
    (memory / "onboarding").mkdir()
    (memory / "onboarding" / "note.md").write_text("# Note\n", encoding="utf-8")
    c3 = _commit_all(memory, "content change")

    contract = _minimal_contract(tmp_path, code_repo=code, memory_repo=memory)
    calls: list[tuple[Path, str]] = []

    def _fake_refresh(mem_root: Path, paired: Any, _coord: Path) -> str | None:
        calls.append((mem_root, paired.commit))
        return None

    bind_worktree_services(
        WorktreeServices(
            memory_quality=SimpleNamespace(),  # type: ignore[arg-type]
            citation_guard=SimpleNamespace(),  # type: ignore[arg-type]
            file_briefs=_fake_refresh,
        )
    )
    try:
        # Ledger-only change (memory.md) triggers no brief refresh.
        assert (
            refresh_integrated_memory_briefs(
                contract,
                code_commit=code_commit,
                memory_commit=c2,
                memory_branch="main",
                previous_memory_commit=c1,
            )
            is None
        )
        assert calls == []

        # Non-ledger memory change triggers composition-bound refresh on checked-out owner.
        assert (
            refresh_integrated_memory_briefs(
                contract,
                code_commit=code_commit,
                memory_commit=c3,
                memory_branch="main",
                previous_memory_commit=c2,
            )
            is None
        )
        assert calls == [(memory.resolve(), code_commit)]
    finally:
        reset_worktree_services()


def test_integrated_memory_briefs_returns_advisory_problem_on_failure(tmp_path: Path) -> None:
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    _init_git(code)
    (code / "app.py").write_text("x = 1\n", encoding="utf-8")
    code_commit = _commit_all(code, "init")

    _init_git(memory)
    (memory / "knowledge").mkdir()
    (memory / LAYOUT_MARKER_PATH).write_text("{}", encoding="utf-8")
    c1 = _commit_all(memory, "base")
    (memory / "onboarding").mkdir()
    (memory / "onboarding" / "a.md").write_text("a\n", encoding="utf-8")
    c2 = _commit_all(memory, "update")

    contract = _minimal_contract(tmp_path, code_repo=code, memory_repo=memory)
    bind_worktree_services(
        WorktreeServices(
            memory_quality=SimpleNamespace(),  # type: ignore[arg-type]
            citation_guard=SimpleNamespace(),  # type: ignore[arg-type]
            file_briefs=lambda *_args: "brief write failed on disk",
        )
    )
    try:
        problem = refresh_integrated_memory_briefs(
            contract,
            code_commit=code_commit,
            memory_commit=c2,
            memory_branch="main",
            previous_memory_commit=c1,
        )
        assert problem == "brief write failed on disk"
    finally:
        reset_worktree_services()


def test_recorded_landing_briefs_refreshes_matching_head_and_reports_unpulled(
    tmp_path: Path,
) -> None:
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    _init_git(code)
    (code / "app.py").write_text("x = 1\n", encoding="utf-8")
    code_commit = _commit_all(code, "init")

    _init_git(memory)
    (memory / "knowledge").mkdir()
    (memory / LAYOUT_MARKER_PATH).write_text("{}", encoding="utf-8")
    c1 = _commit_all(memory, "base")
    (memory / "onboarding").mkdir()
    (memory / "onboarding" / "a.md").write_text("a\n", encoding="utf-8")
    c2 = _commit_all(memory, "landed")

    contract = _minimal_contract(tmp_path, code_repo=code, memory_repo=memory)
    calls: list[Path] = []
    bind_worktree_services(
        WorktreeServices(
            memory_quality=SimpleNamespace(),  # type: ignore[arg-type]
            citation_guard=SimpleNamespace(),  # type: ignore[arg-type]
            file_briefs=lambda mem_root, _paired, _coord: calls.append(mem_root) or None,
        )
    )
    try:
        # Checkout HEAD == c2 -> refreshes matching checkout.
        assert (
            refresh_recorded_landing_memory_briefs(
                contract, code_commit=code_commit, memory_commit=c2
            )
            is None
        )
        assert calls == [memory.resolve()]

        # Checkout HEAD != landed commit (operator has not pulled yet) -> reports explicitly.
        calls.clear()
        problem = refresh_recorded_landing_memory_briefs(
            contract, code_commit=code_commit, memory_commit=c1
        )
        assert calls == []
        assert problem is not None
        assert c1[:12] in problem
        assert "agents-remember memory-briefs --repo agents-remember" in problem
    finally:
        reset_worktree_services()


def test_short_answer_gates_read_current_brief_notices_on_location_current() -> None:
    brief = _brief("pkg/a.py", [_rule("INV-000001", 10), _rule("INV-000002", 10)])
    current_whole = whole(brief, BriefLocation("briefs/pkg/a.py.md", current=True))
    stale_whole = whole(
        brief, BriefLocation("briefs/pkg/a.py.md", current=False, freshness="missing")
    )

    assert current_whole["inTheFile"] == IN_THE_FILE
    assert stale_whole["inTheFile"] == IN_THE_FILE_NOT_CURRENT
    assert "Read the current brief" not in stale_whole["inTheFile"]

    current_trimmed = without_rule_texts(current_whole, 1)
    assert current_trimmed["leftOut"] == LEFT_OUT
    assert current_trimmed["rules"][1]["rule"] == RULE_LEFT_OUT

    stale_trimmed = without_rule_texts(stale_whole, 1)
    assert stale_trimmed["leftOut"] == LEFT_OUT_NOT_CURRENT
    assert stale_trimmed["rules"][1]["rule"] == RULE_LEFT_OUT_NOT_CURRENT
    assert "Read the current brief" not in stale_trimmed["leftOut"]

    assert "Read each current brief" in counts_notice(2, all_current=True)
    not_current_notice = counts_notice(2, all_current=False)
    assert "missing or older" in not_current_notice
    assert "Read each current brief" not in not_current_notice


def test_clause_title_handles_short_sentence_clause_and_word_fallbacks() -> None:
    assert clause_title("Short rule.") == "Short rule."
    assert (
        clause_title(
            "Keep the primary boundary explicit. Additional rationale explains the history behind it in detail."
        )
        == "Keep the primary boundary explicit"
    )
    assert rule_title({"title": "Explicit Title", "statement": "Ignored"}).text == "Explicit Title"
    assert rule_title({"statement": "a " * 60}).text.endswith(" …")
    assert len(rule_title({"statement": "a " * 60}).text) <= 84


def test_memory_briefs_cli_refreshes_briefs_and_refuses_legacy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    world = World(tmp_path / "world")
    ignore_briefs(world.memory)
    coord = tmp_path / "coord"
    coord.mkdir()

    rc = cli_main(
        [
            "memory-briefs",
            "--memory-root",
            str(world.memory),
            "--code-root",
            str(world.code),
            "--coordination-root",
            str(coord),
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["briefs"] > 0
    assert (world.memory / BRIEFS / TREE_FILE).is_file()
    assert brief_location(world.memory, REVIEW).is_file()

    # Unconverted directory refuses with legacy-format (exit 2).
    legacy = tmp_path / "legacy-mem"
    legacy.mkdir()
    rc_legacy = cli_main(
        [
            "memory-briefs",
            "--memory-root",
            str(legacy),
            "--code-root",
            str(world.code),
            "--coordination-root",
            str(coord),
            "--json",
        ]
    )
    assert rc_legacy == 2
    legacy_payload = json.loads(capsys.readouterr().out)
    assert legacy_payload["state"] == "legacy-format"


def test_projects_mount_two_page_continuation_pairs_code_tree_with_zero_unverifiable(
    tmp_path: Path,
) -> None:
    """Rule 7: Projects-scoped non-Git workspace auto-pairs code tree on page 1 AND page 2."""

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    code_repo = projects_root / "demo"
    memory_repo = tmp_path / "coord" / "memory-repos" / "ar-demo"
    _init_git(code_repo)
    _init_git(memory_repo)

    # Create a 30-member family so source_context spans >= 2 pages.
    statements = [f"Rule {n} guarantees deterministic pairing across pages." for n in range(30)]
    for n in range(30):
        rel = _member_path(n, _DEEP)
        target = code_repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"def fn_{n}():\n    return {n}\n", encoding="utf-8")
    _commit_all(code_repo, "thirty files")

    write_family_tree(memory_repo, statements, directory=_DEEP)
    _commit_all(memory_repo, "thirty-member family")

    repos = {
        "demo": RepositoryScope(repo_id="demo", path=code_repo, memory_root=memory_repo),
    }
    coord = tmp_path / "coord"

    page1 = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(memory_repo),
            view="source_context",
            source_path=_member_path(0, _DEEP),
        ),
        workspace_root=str(projects_root),  # NOT a Git repository!
        coordination_root=str(coord),
        repositories=repos,
    )
    assert page1["state"] == "page", page1
    assert page1["continuation"] is not None
    assert page1["currentness"]["codeTree"]["repositoryRoot"] == str(code_repo)
    assert page1["currentness"]["counts"]["unverifiable"] == 0

    page2 = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(memory_repo),
            view="source_context",
            continuation=page1["continuation"],
        ),
        workspace_root=str(projects_root),  # NOT a Git repository!
        coordination_root=str(coord),
        repositories=repos,
    )
    assert page2["state"] == "page", page2
    assert page2["currentness"]["codeTree"]["repositoryRoot"] == str(code_repo)
    assert page2["currentness"]["counts"]["unverifiable"] == 0


def test_leaf_worktree_precedence_beats_configured_main_and_observes_dirty_working_tree(
    tmp_path: Path,
) -> None:
    """Rule 6 & 7: Leaf sibling code worktree takes precedence over main, and dirty working-tree
    object_directory is forwarded on fresh reads without marking anchors unverifiable."""

    world = World(tmp_path / "main-world")
    enclosure = tmp_path / "enclosure-ar"
    leaf_code = enclosure / "07_leaf"
    leaf_memory = enclosure / "memory-07_leaf"
    _git(world.code, "worktree", "add", "-b", "leaf/code", str(leaf_code))
    _git(world.memory, "worktree", "add", "-b", "leaf/mem", str(leaf_memory))

    # Modify REVIEW only in the leaf code worktree (uncommitted dirty working tree!).
    (leaf_code / REVIEW).write_text(_body_edit(), encoding="utf-8")

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    repos = {
        "agents-remember": RepositoryScope(
            repo_id="agents-remember", path=world.code, memory_root=world.memory
        )
    }

    response = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(leaf_memory),
            view="invariant",
            invariant_revision_id=text_uuid("revision", "INV-AAAAAA@1"),
        ),
        workspace_root=str(projects_root),
        coordination_root=str(tmp_path / "coord"),
        repositories=repos,
    )
    assert response["state"] == "view", response
    currentness = response["currentness"]
    # Leaf code worktree won over configured main, and dirty uncommitted edit was observed as stale!
    assert currentness["codeTree"]["repositoryRoot"] == str(leaf_code)
    assert currentness["counts"]["unverifiable"] == 0
    assert [inv["state"] for inv in currentness["invariants"]] == ["stale"]

    # Ensure unconfigured standalone memory does not guess unrelated repos.
    unconfigured = tmp_path / "scratch-copy"
    _init_git(unconfigured)
    (unconfigured / "knowledge").mkdir()
    (unconfigured / LAYOUT_MARKER_PATH).write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}), encoding="utf-8"
    )
    _commit_all(unconfigured, "empty converted")
    res = knowledge_read_payload(
        ReadToolRequest(memory_root=str(unconfigured), view="weakness"),
        workspace_root=str(projects_root),
        coordination_root=str(tmp_path / "coord"),
        repositories=repos,
    )
    assert res["state"] == "view"
    assert res["rows"] == []
    assert replace(
        ReadToolRequest(memory_root=str(unconfigured), view="weakness"), compact=True
    ).compact


def test_dirty_working_tree_two_page_resume_succeeds_unchanged_and_refuses_when_mutated(
    tmp_path: Path,
) -> None:
    """Rule 6 & 7: Dirty working tree forwards object_directory across pages and refuses after mutation."""

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    code_repo = projects_root / "demo"
    memory_repo = tmp_path / "coord" / "memory-repos" / "ar-demo"
    _init_git(code_repo)
    _init_git(memory_repo)

    statements = [
        f"Rule {n} verifies dirty working tree resumption across pages." for n in range(30)
    ]
    for n in range(30):
        rel = _member_path(n, _DEEP)
        target = code_repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"def fn_{n}():\n    return {n}\n", encoding="utf-8")
    _commit_all(code_repo, "thirty files")

    write_family_tree(memory_repo, statements, directory=_DEEP)
    _commit_all(memory_repo, "thirty-member family")

    # Dirty the working tree before page 1.
    dirty_target = code_repo / _member_path(0, _DEEP)
    dirty_target.write_text("def fn_0():\n    return 101\n", encoding="utf-8")

    repos = {
        "demo": RepositoryScope(repo_id="demo", path=code_repo, memory_root=memory_repo),
    }
    coord = tmp_path / "coord"

    page1 = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(memory_repo),
            view="source_context",
            source_path=_member_path(0, _DEEP),
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert page1["state"] == "page", page1
    assert page1["continuation"] is not None
    assert page1["currentness"]["codeTree"]["repositoryRoot"] == str(code_repo)
    assert page1["currentness"]["counts"]["unverifiable"] == 0

    # Unchanged dirty working tree resumes cleanly on page 2 with 0 unverifiable.
    page2 = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(memory_repo),
            view="source_context",
            continuation=page1["continuation"],
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert page2["state"] == "page", page2
    assert page2["currentness"]["codeTree"]["repositoryRoot"] == str(code_repo)
    assert page2["currentness"]["counts"]["unverifiable"] == 0

    # Mutating the dirty working tree between pages refuses resumption.
    dirty_target.write_text("def fn_1():\n    return 202\n", encoding="utf-8")
    mutated = knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(memory_repo),
            view="source_context",
            continuation=page1["continuation"],
        ),
        workspace_root=str(projects_root),
        coordination_root=str(coord),
        repositories=repos,
    )
    assert mutated["state"] == "refused", mutated
