"""MIK-R56: three memory-content routes, one renderer and one historical rewriter.

Closeout, direct landing and baseline adoption create attributed memory content. Carryover
writes into the recovery worktree without committing; the unreachable prepared route is gone.
Backfill is separately bound to its exact path and the original commit's tree: it rewrites
history, not content. An unlisted renderer caller or a returned retired name fails the census.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.memory_tools import (
    CarryoverSelection,
    MemoryBranches,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
    memory_carryover_apply_tool,
)
from agents_remember.kernel import memory_backfill
from agents_remember.kernel.memory_attribution import (
    CODE_COMMIT_TRAILER_KEY,
    parse_code_commit_trailer,
    render_memory_content_message,
)
from agents_remember.kernel.memory_ledger import load_ledger
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.worktrees.worktree_contract import (
    WorktreeContract,
    load_contract,
    write_contract,
)
from test_closeout_queue import MASTER_A, QueueFixture
from test_worktree_support import git, init_repo

CODE_ONE = "1" * 40
CODE_TWO = "2" * 40

# Exact source paths, not package/pattern exemptions. Backfill is not a content route.
_PRODUCERS = {
    "worktrees/modules/closeout_external.py": "memory_content_message",
    "worktrees/integration/direct_landing/direct_landing_execution.py": "memory_content_message",
    "memory/baseline.py": "render_memory_content_message",
}
_SHARED_RENDERER = "models/closeout/input.py"
_HISTORY_REWRITER = "kernel/memory_backfill.py"
_ONE_MODULE = "kernel/memory_attribution.py"
_KEY_NAME = "CODE_COMMIT_TRAILER_KEY"
_REMOVED_NAMES = {
    "execute_selected_closeout",
    "prepare_closeout_certification",
    "select_initial_certification",
    "certification_continuation",
    "prepared_memory_certification",
    "prepared_closeout_refusal",
    "prepared_closeout_lock",
    "PREPARED_CLOSEOUT_UNCLOSABLE",
    "_require_legacy_format_target",
}


def _renderer_binding(
    expression: ast.AST | None, aliases: dict[str, str], modules: set[str], entries: set[str]
) -> str | None:
    if isinstance(expression, ast.Name):
        return aliases.get(expression.id, expression.id if expression.id in entries else None)
    if isinstance(expression, ast.Attribute) and expression.attr in entries:
        return expression.attr
    if (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and expression.func.id == "getattr"
        and len(expression.args) >= 2
    ):
        member = expression.args[1]
        if (
            isinstance(member, ast.Constant)
            and isinstance(member.value, str)
            and member.value in entries
        ):
            return member.value
        owner = expression.args[0]
        if isinstance(owner, ast.Name) and owner.id in modules:
            assert isinstance(member, ast.Constant), "unresolved renderer binding"
    return None


def _assignment_renderer_aliases(
    tree: ast.AST, aliases: dict[str, str], modules: set[str], entries: set[str]
) -> None:
    assignments = [item for item in ast.walk(tree) if isinstance(item, (ast.Assign, ast.AnnAssign))]
    for _ in range(len(assignments) + 1):
        changed = False
        for assignment in assignments:
            entry = _renderer_binding(assignment.value, aliases, modules, entries)
            if entry is None:
                continue
            targets = (
                assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in aliases:
                    aliases[target.id] = entry
                    changed = True
        if not changed:
            break


def _renderer_calls(tree: ast.AST, entries: set[str]) -> set[str]:
    imports = [item for item in ast.walk(tree) if isinstance(item, (ast.Import, ast.ImportFrom))]
    aliases = {
        node.asname or node.name: node.name
        for item in imports
        for node in item.names
        if node.name in entries
    }
    modules = {
        node.asname or node.name.split(".")[-1]
        for item in imports
        for node in item.names
        if node.name.endswith("memory_attribution")
    }
    _assignment_renderer_aliases(tree, aliases, modules, entries)
    return {
        called
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        if (called := _renderer_binding(node.func, aliases, modules, entries)) is not None
    }


def _assert_source_census(contents: dict[str, str]) -> None:
    """Observe actual calls (including imported aliases); reject retired bindings/literals."""

    reached: dict[str, set[str]] = {}
    removed: dict[str, list[str]] = {}
    entries = {"render_memory_content_message", "memory_content_message"}
    pattern = re.compile(r"\b(" + "|".join(sorted(_REMOVED_NAMES)) + r")\b")
    for name, text in contents.items():
        tree = ast.parse(text, filename=name)
        called = _renderer_calls(tree, entries)
        if called:
            reached[name] = called
        for node in ast.walk(tree):
            token = (
                node.id
                if isinstance(node, ast.Name)
                else node.attr
                if isinstance(node, ast.Attribute)
                else node.name
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.alias)
                )
                else node.value
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
                else ""
            )
            if found := pattern.findall(token):
                removed.setdefault(name, []).extend(found)
    expected = {path: {entry} for path, entry in _PRODUCERS.items()}
    expected.update(
        {path: {"render_memory_content_message"} for path in (_SHARED_RENDERER, _HISTORY_REWRITER)}
    )
    assert reached == expected, f"actual producer census: {reached!r}; expected: {expected!r}"
    assert not removed, removed


def _production_source() -> Path:
    return Path(__file__).resolve().parents[2] / "mcp" / "src" / "agents_remember"


def _attribution(repository: Path, commit: str) -> str:
    """The trailer the committed object carries, read the way Git documents it."""

    return git(repository, "log", "-1", "--format=%(trailers:key=Code-Commit)", commit)


def _parsed_trailer(repository: Path, commit: str, scratch: Path) -> str:
    """``git interpret-trailers --parse`` over the committed message, as data."""

    message = scratch / "attributed-message.txt"
    message.write_text(git(repository, "log", "-1", "--format=%B", commit) + "\n", encoding="utf-8")
    return git(repository, "interpret-trailers", "--parse", message.as_posix())


def test_the_attribution_key_is_named_and_rendered_in_exactly_one_module() -> None:
    """One key, one interpolation, one module -- the guard the whole transition rests on.

    ``kernel/memory_attribution.py`` declares the key once, renders it once, and reads it back
    once. A producer that imported the key and hand-built the trailer, or worse wrote its own copy
    of the spelling, would emit trailers this reader silently ignores, and that failure looks like
    "no attribution exists" rather than like a bug. Three separate halves, because a divergent
    writer has three ways to appear: naming the constant (the drift a second copy introduces),
    interpolating it (the act of rendering), and spelling it out as a quoted literal (a copy that
    never mentions the constant at all). Prose that merely mentions the trailer's spelling is
    documentation and is not counted.
    """

    root = _production_source()
    sources = sorted(path for path in root.rglob("*.py"))
    contents = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in sources
    }
    named = [name for name, text in contents.items() if _KEY_NAME in text]
    rendered = [name for name, text in contents.items() if "{" + _KEY_NAME + "}" in text]
    quoted = [
        name
        for name, text in contents.items()
        if f'"{CODE_COMMIT_TRAILER_KEY}:' in text or f"'{CODE_COMMIT_TRAILER_KEY}:" in text
    ]

    assert named == [_ONE_MODULE], named
    assert rendered == [_ONE_MODULE], rendered
    # Nobody starts the trailer as a string literal: the one definition above is the only place
    # the spelling may exist, and a declared trailer always begins with the key it interpolates.
    assert quoted == [], quoted


def test_every_census_producer_reaches_the_shared_renderer() -> None:
    """Three content routes plus their model renderer and exact historical rewriter."""

    root = _production_source()
    contents = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
    }
    _assert_source_census(contents)
    # Actual copied source is rejected outside the named path, without changing production.
    copied = {**contents, "memory/copied_producer.py": contents["memory/baseline.py"]}
    with pytest.raises(AssertionError, match="actual"):
        _assert_source_census(copied)
    # A returned name alone must fail even if it adds no new trailer call.
    returned = {**contents, "memory/returned_route.py": "def execute_selected_closeout(): pass\n"}
    with pytest.raises(AssertionError, match="execute_selected_closeout"):
        _assert_source_census(returned)


@pytest.mark.parametrize(
    "binding",
    [
        "from agents_remember.kernel.memory_attribution import render_memory_content_message\n"
        "alias = render_memory_content_message\nalias('extra', '1' * 40)\n",
        "from agents_remember.kernel import memory_attribution as trailers\n"
        "alias = getattr(trailers, 'render_memory_content_message')\nalias('extra', '1' * 40)\n",
        "from agents_remember.kernel import memory_attribution as trailers\n"
        "getattr(trailers, 'render_memory_content_message')('extra', '1' * 40)\n",
    ],
)
def test_census_rejects_indirect_additional_renderer_callers(binding):
    root = _production_source()
    contents = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
    }
    _assert_source_census(contents)
    with pytest.raises(AssertionError, match="actual producer census"):
        _assert_source_census({**contents, "memory/indirect_producer.py": binding})


def test_the_named_history_rewriter_replays_the_original_tree(tmp_path: Path) -> None:
    """The one exact-path exemption creates its object from the replaced commit's tree."""

    repo = tmp_path / "memory"
    init_repo(repo)
    (repo / "card.md").write_text("original content\n", encoding="utf-8")
    git(repo, "add", "card.md")
    git(repo, "commit", "-q", "-m", "original memory")
    original = git(repo, "rev-parse", "HEAD")
    parents = git(repo, "show", "-s", "--format=%P", original).split()
    # A changed checkout and index cannot supply the rewriter's content.
    (repo / "card.md").write_text("different candidate\n", encoding="utf-8")
    git(repo, "add", "card.md")
    state = git(repo, "status", "--porcelain"), git(repo, "write-tree")
    rebuilt = memory_backfill._rebuild(repo, original, parents, CODE_ONE)
    assert git(repo, "rev-parse", f"{rebuilt}^{{tree}}") == git(
        repo, "rev-parse", f"{original}^{{tree}}"
    )
    assert git(repo, "show", "-s", "--format=%P|%an|%ae|%at|%cn|%ce|%ct", rebuilt) == git(
        repo, "show", "-s", "--format=%P|%an|%ae|%at|%cn|%ce|%ct", original
    )
    assert _attribution(repo, rebuilt) == f"Code-Commit: {CODE_ONE}"
    assert git(repo, "rev-parse", "HEAD") == original
    assert (git(repo, "status", "--porcelain"), git(repo, "write-tree")) == state


def test_the_one_renderer_keeps_the_callers_body_verbatim_and_its_trailer_final() -> None:
    """A caller's body may itself close with ``Key: value`` lines; the attribution is still ours.

    Carryover and baseline take their commit message as an argument of another tool, so the body
    is not this module's to edit: it is committed byte for byte, and the trailer is a separate
    final paragraph. That is what makes the attribution unambiguous -- Git reads a trailer only
    from the final block, so the caller's own lookalike line cannot be mistaken for it, and a
    producer that prepended or merged instead would be read as the caller's, not the code commit's.
    """

    body = (
        "Carry over landed branch memory\n"
        "\n"
        "The caller's own closing notes.\n"
        "\n"
        "Reviewed-By: someone@example.invalid\n"
        f"{CODE_COMMIT_TRAILER_KEY}: {CODE_ONE}\n"
    )

    rendered = render_memory_content_message(body, CODE_TWO)

    assert rendered == f"{body}\n\n{CODE_COMMIT_TRAILER_KEY}: {CODE_TWO}"
    assert parse_code_commit_trailer(rendered) == CODE_TWO


def _carryover_world(
    root: Path, *, converted: bool = False
) -> tuple[QueueFixture, WorktreeContract, str, str, str]:
    """One open external leaf whose source branch memory describes landed official code.

    Carryover's subject is the retired legacy-format copy, so its world stays unconverted
    unless a caller asks for a converted target.
    """

    fixture = QueueFixture(root, memory_mode="external", converted=converted)
    contract = fixture.contracts[MASTER_A]
    code = fixture.code
    memory = fixture.memory
    memory_worktree = contract.memory_worktree
    assert memory_worktree is not None
    old_base = git(code, "rev-parse", "main")
    feature = "FEATURE = 1\n"
    # The same content lands twice: once on the branch that describes it, once on the official
    # line. That is the ``patch-id-match`` evidence carryover auto-carries without a review.
    git(code, "switch", "-q", "-c", "source", old_base)
    (code / "feature.py").write_text(feature, encoding="utf-8")
    git(code, "add", "feature.py")
    git(code, "commit", "-q", "-m", "Add feature on the source branch")
    git(code, "switch", "-q", "-c", "official", old_base)
    (code / "feature.py").write_text(feature, encoding="utf-8")
    git(code, "add", "feature.py")
    git(code, "commit", "-q", "-m", "Land the feature officially")
    official_head = git(code, "rev-parse", "official")
    git(code, "switch", "-q", "main")
    # Carryover requires the target leaf's code worktree to be unchanged at the official tip.
    git(contract.code_worktree, "reset", "--hard", official_head)
    git(contract.code_worktree, "clean", "-qfd")
    git(memory_worktree, "clean", "-qfd")
    # Carryover writes only with declared target route-index authority, and it requires a clean
    # target, so the authority is committed here as the settings edit it is: no code commit to
    # name, no trailer.
    settings = memory_worktree / "system" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "version": 2,
                "onboarding": {
                    "storage": {"mode": "memory-repo"},
                    "pathRules": {
                        "include": {"paths": ["*"], "fileTypes": [".md", ".py"]},
                        "exclude": {"paths": []},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    git(memory_worktree, "add", "system/settings.json")
    git(memory_worktree, "commit", "-q", "-m", "Declare target-memory route authority")
    write_contract(contract.contract_path, replace(contract, code_base_commit=official_head))
    contract = load_contract(contract.contract_path)
    # The branch's memory is a real second checkout of the memory repository: carryover reads its
    # merge base against the target, so a plain directory would not be the same world.
    source_memory = fixture.coord / "source-memory"
    git(memory, "switch", "-q", "-c", "branch-memory", "main")
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding" / "feature.py.md").write_text(
        "# Feature\n"
        "\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| lastVerifiedCommitHash | `{old_base}` |\n"
        "| lastVerifiedCommitDate | 2026-01-01T00:00:00+00:00 |\n",
        encoding="utf-8",
    )
    git(memory, "add", "onboarding/feature.py.md")
    git(memory, "commit", "-q", "-m", "Describe the feature on the branch memory")
    git(memory, "switch", "-q", "main")
    git(memory, "worktree", "add", "--detach", source_memory.as_posix(), "branch-memory")
    return fixture, contract, old_base, official_head, source_memory.as_posix()


def test_carryover_apply_refuses_unconverted_memory_with_the_conversion_step(tmp_path) -> None:
    """MIK-R46 rule 1: ``memory_carryover_apply`` refuses unconverted memory, writing nothing.

    The legacy-format copy (Markdown with no format check and no validator) is retired: on
    unconverted memory the apply refuses with the one ``legacy-format`` message naming the
    conversion, and the refusal writes nothing. What carryover does on converted memory is
    MIK-R56, which lands after this leaf.
    """

    fixture, contract, old_base, _official_head, source_memory = _carryover_world(tmp_path)
    memory_worktree = contract.memory_worktree
    assert memory_worktree is not None and contract.ledger_path is not None

    selection = CarryoverSelection(
        repo_id=contract.repo_name,
        contract_path=contract.contract_path.as_posix(),
        source_memory=source_memory,
        official_code_ref="official",
        source_code_ref="source",
        old_base=old_base,
    )
    contract.ledger_path.write_text("not a ledger\n", encoding="utf-8")

    def _byte_tree(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and "/.git/objects/" not in path.as_posix()
        }

    before = (
        git(fixture.code, "for-each-ref"),
        git(memory_worktree, "for-each-ref"),
        git(memory_worktree, "rev-parse", "HEAD"),
        git(memory_worktree, "status", "--porcelain"),
        git(memory_worktree, "diff", "--cached", "--name-only"),
        git(memory_worktree, "ls-files", "-s"),
        _byte_tree(memory_worktree),
    )
    result = memory_carryover_apply_tool(
        fixture.cfg,
        selection,
        intent_note="carry the landed branch memory",
    )
    assert result.get("ok") is False, result
    assert result.get("code") == "legacy-format", result
    assert "did not run" in str(result.get("message", "")), result
    assert "agents-remember knowledge-convert" in str(result.get("detail", "")), result
    assert "nextAction" in result, result
    assert (
        git(fixture.code, "for-each-ref"),
        git(memory_worktree, "for-each-ref"),
        git(memory_worktree, "rev-parse", "HEAD"),
        git(memory_worktree, "status", "--porcelain"),
        git(memory_worktree, "diff", "--cached", "--name-only"),
        git(memory_worktree, "ls-files", "-s"),
        _byte_tree(memory_worktree),
    ) == before


def test_carryover_takes_the_unconverted_memory_cutover_lock(tmp_path) -> None:
    """MIK-R46 rules 1 and 2: the retained unconverted side still writes nothing.

    The cutover lock returns the one legacy-format refusal whether or not this memory
    repository holds converted memory elsewhere, naming the crossing sync when it does.
    Only the obsolete converted-target refusal half is retired under MIK-R56.
    """

    fixture, contract, old_base, _official_head, source_memory = _carryover_world(tmp_path)
    memory_worktree = contract.memory_worktree
    assert memory_worktree is not None
    selection = CarryoverSelection(
        repo_id=contract.repo_name,
        contract_path=contract.contract_path.as_posix(),
        source_memory=source_memory,
        official_code_ref="official",
        source_code_ref="source",
        old_base=old_base,
    )

    def refused_legacy() -> dict:
        before = (
            git(memory_worktree, "rev-parse", "HEAD"),
            git(memory_worktree, "status", "--porcelain"),
        )
        result = memory_carryover_apply_tool(
            fixture.cfg,
            selection,
            intent_note="carry the landed branch memory",
        )
        after = (
            git(memory_worktree, "rev-parse", "HEAD"),
            git(memory_worktree, "status", "--porcelain"),
        )
        assert after == before
        assert result.get("ok") is False, result
        assert result.get("code") == "legacy-format", result
        return result

    def convert(checkout: Path) -> None:
        (checkout / "knowledge").mkdir(exist_ok=True)
        (checkout / "knowledge" / "layout.json").write_text("{}\n", encoding="utf-8")
        git(checkout, "add", "knowledge/layout.json")
        git(checkout, "commit", "-q", "-m", "Convert this line")

    memory = fixture.memory
    git(memory, "switch", "-q", "-c", "converted-line", "main")
    convert(memory)
    git(memory, "switch", "-q", "main")
    locked = refused_legacy()
    assert "branch converted-line" in str(locked.get("detail", "")) and "crossing sync" in str(
        locked.get("detail", "")
    ), locked


def test_baseline_attributes_its_memory_content_commit_to_the_code_source_branch(
    tmp_path, worktree_services: None
) -> None:
    """``memory_baseline_adopt`` attributes the first memory commit it creates, once.

    The code commit is resolved once for the content trailer, from which the cache derives its
    pair. The adopted content is the first and only commit in the unborn memory history, and
    cache absence does not make an already adopted baseline eligible for another commit.
    """

    root = tmp_path / "world"
    coordination = root / "coordination"
    code = root / "repo"
    memory = coordination / "memory-repos" / "ar-repo"
    init_repo(code)
    (code / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-q", "-m", "Add module")
    coordination.mkdir(parents=True, exist_ok=True)
    # The MCP settings file must sit outside the coordinator root it points at.
    config_path = root / "settings.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coordination.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo": {}},
            }
        ),
        encoding="utf-8",
    )
    init_repo(memory)
    # Adoption is the bootstrap exception on the exact unborn branch memory_init mints.
    git(memory, "config", "agents-remember.defaultBranch", "main")
    git(memory, "update-ref", "-d", "refs/heads/main")
    # MIK-R46 rule 8: this subject (attribution) runs on converted memory -- the marker is
    # present, and the bare card below carries no old-format sign, so no legacy card blocks it.
    (memory / "knowledge").mkdir(parents=True, exist_ok=True)
    (memory / "knowledge" / "layout.json").write_text(
        '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n', encoding="utf-8"
    )
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding" / "module.md").write_text("# Module\n", encoding="utf-8")
    (memory / "system").mkdir(parents=True, exist_ok=True)
    (memory / "system" / "settings.md").write_text("# Settings\n", encoding="utf-8")
    (memory / "memory.md").write_text("not a ledger\n", encoding="utf-8")

    config = load_config(config_path)
    initial = memory_baseline_status_tool(config, repo_id="repo")
    assert initial["state"] == "ready", initial
    result = memory_baseline_adopt_tool(
        config,
        repo_id="repo",
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )

    assert result["ok"] is True, result
    assert result["state"] == "adopted", result
    content = str(result["bootstrap"]["memoryContentCommit"])
    assert "ledgerCommit" not in result["bootstrap"]
    assert git(memory, "rev-parse", "HEAD") == content
    assert git(memory, "rev-list", "--count", "HEAD") == "1"
    source_commit = git(code, "rev-parse", "main")
    assert _attribution(memory, content) == f"Code-Commit: {source_commit}"
    assert _parsed_trailer(memory, content, tmp_path) == f"Code-Commit: {source_commit}"
    assert git(memory, "ls-files", "memory.md") == ""
    row = load_ledger(memory / "memory.md").rows[0]
    assert (row.code_commit, row.memory_commit) == (source_commit, content)
    (memory / "memory.md").unlink()
    repeated = memory_baseline_adopt_tool(
        config,
        repo_id="repo",
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert repeated["state"] == "already-adopted", repeated
    assert git(memory, "rev-parse", "HEAD") == content

    # A resolvable HEAD with unreadable ancestry is unavailable, never a new baseline.
    _replace_with_missing_parent(memory, content, tmp_path / "missing-parent.commit")
    refs = git(memory, "show-ref")
    unavailable = memory_baseline_status_tool(config, repo_id="repo")
    assert unavailable["ok"] is False and unavailable["state"] == "unavailable", unavailable
    refused = memory_baseline_adopt_tool(
        config,
        repo_id="repo",
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="main"),
    )
    assert refused["ok"] is False and refused["state"] == "unavailable", refused
    assert git(memory, "show-ref") == refs


def _replace_with_missing_parent(memory: Path, content: str, commit_file: Path) -> None:
    header, message = git(memory, "cat-file", "-p", content).split("\n\n", 1)
    tree, remaining = header.split("\n", 1)
    commit_file.write_text(f"{tree}\nparent {'f' * 40}\n{remaining}\n\n{message}\n")
    broken = git(memory, "hash-object", "-t", "commit", "-w", str(commit_file))
    git(memory, "update-ref", "refs/heads/main", broken, content)
