"""A task worktree has its own environment: the product names no interpreter.

MIK-R136 rule 3: the worker brief template and every role or criteria text of
the product that names an interpreter or a ``PYTHONPATH`` for a candidate say
instead to build the worktree's environment as the repository's tool notes say
and to run every command on the candidate in it. A record of a run names the
interpreter it used.

The census this protects is the canonical ``skills/l-01-agent-lifecycles/``
tree plus the nine generated skill copies ``scripts/sync-skills.py`` owns: the
only surface that ever named a candidate interpreter is the worker brief
template, so every surface of that template carries the rule and the
instruction surfaces (roles, operations, templates) name none.

MIK-R136 rule 6: the product's own worktree-change enumeration — the working
tier of the closeout worklist (`closeout_changed_paths`) — never carries the
ignored environment directories, so they stay out of closeout/landing
candidate accounting; sync merges and landing commits are tracked-content
operations over the same inventory.
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.worktrees.modules.git import changed_worktree_paths

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = Path("l-01-agent-lifecycles")
TEMPLATE = LIFECYCLE / "templates" / "worker-brief.md"

GENERATED_SKILL_COPIES = (
    "mcp/src/agents_remember/package_data/runtime/skills",
    ".claude/skills",
    ".codex/skills",
    ".cursor/skills",
    ".github-vscode/skills",
    ".hermes/skills",
    ".openclaw/workspace/skills",
    ".pi/skills",
    ".agents/skills",
)

#: Instruction surfaces: a sentence here tells a seat how to run its candidate.
INSTRUCTION_DIRS = ("roles", "operations", "templates")

#: Tokens that name a concrete interpreter or path bootstrap for a candidate.
FORBIDDEN_TOKENS = (
    "<venv-python-path>",
    "venv-python-path",
    "PYTHONPATH=",
    "mcp/.venv/bin/python",
)


def template_surfaces() -> list[Path]:
    """Every shipped surface of the worker brief template, canonical first."""

    return [REPOSITORY_ROOT / "skills" / TEMPLATE] + [
        REPOSITORY_ROOT / copy_root / TEMPLATE for copy_root in GENERATED_SKILL_COPIES
    ]


def names_candidate_interpreter(text: str) -> bool:
    """Whether a text still points a candidate at a named interpreter."""

    return any(token in text for token in FORBIDDEN_TOKENS)


def test_worker_brief_template_names_no_candidate_interpreter() -> None:
    """Every shipped worker brief template points at the tool notes, not an interpreter."""

    for surface in template_surfaces():
        assert surface.is_file(), f"worker brief template surface missing: {surface}"
        text = surface.read_text(encoding="utf-8")
        assert not names_candidate_interpreter(text), (
            f"{surface} still names a candidate interpreter"
        )
        assert "system/tools.md" in text, (
            f"{surface} no longer points at the repository's tool notes"
        )
        assert "names the interpreter it used" in text, (
            f"{surface} no longer requires a run record to name its interpreter"
        )


def test_no_role_operation_or_template_names_a_candidate_interpreter() -> None:
    """No other instruction surface of the lifecycle names a candidate interpreter."""

    root = REPOSITORY_ROOT / "skills" / LIFECYCLE
    offenders = [
        str(path.relative_to(REPOSITORY_ROOT))
        for directory in INSTRUCTION_DIRS
        for path in sorted((root / directory).rglob("*.md"))
        if names_candidate_interpreter(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "instruction surfaces naming a candidate interpreter:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_reports_the_retired_interpreter_line() -> None:
    """The predicate above fails on the retired wording, so it guards something."""

    retired = "- Interpreter: `<venv-python-path>` with `PYTHONPATH=<code-worktree>/mcp/src`"
    assert names_candidate_interpreter(retired)
    shipped = (REPOSITORY_ROOT / "skills" / TEMPLATE).read_text(encoding="utf-8")
    assert not names_candidate_interpreter(shipped)


def test_candidate_accounting_excludes_worktree_environments() -> None:
    """The closeout working tier never carries either worktree environment directory.

    The guard is meaningful only with a real environment present: this
    repository root holds the built ``mcp/.venv``. (``dashboard/node_modules``
    has no live instance in any checkout that never installed it; its half is
    structural, backed by the ignore-rule proof.)
    """

    assert (REPOSITORY_ROOT / "mcp" / ".venv").is_dir(), "no built venv to exclude"
    paths = changed_worktree_paths(REPOSITORY_ROOT)
    offenders = [
        path for path in paths if ".venv" in Path(path).parts or "node_modules" in Path(path).parts
    ]
    assert offenders == [], "candidate accounting carries environment paths:\n  " + "\n  ".join(
        offenders
    )
