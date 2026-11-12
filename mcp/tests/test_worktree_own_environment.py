"""Candidate environments follow tool notes; each run names its interpreter.

MIK-R136 rule3 duties have one home at the Worker's candidate-environment
 action after D699 retired the old brief. Check the canonical source and every
 declared package copy. Roles, operations and templates name no concrete
 candidate interpreter or PYTHONPATH. The retired-line negative remains.

MIK-R136 rule6 keeps ignored environments out of closeout candidate accounting;
 the existing temporary-repository control is unchanged.
"""

from __future__ import annotations

import venv
from pathlib import Path

from agents_remember.worktrees.modules.git import changed_worktree_paths
from agents_remember_test_support.testing.curation_doctrine import GENERATED_SKILL_COPIES
from test_worktree_support import init_repo

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE = Path("l-01-agent-lifecycles")
ENVIRONMENT_SOURCE = LIFECYCLE / "roles" / "worker.md"

#: Instruction surfaces: a sentence here tells a seat how to run its candidate.
INSTRUCTION_DIRS = ("roles", "operations", "templates")

#: Tokens that name a concrete interpreter or path bootstrap for a candidate.
FORBIDDEN_TOKENS = (
    "<venv-python-path>",
    "venv-python-path",
    "PYTHONPATH=",
    "mcp/.venv/bin/python",
)


def environment_surfaces() -> list[Path]:
    """Every shipped surface of the Worker candidate-environment action, canonical first."""

    return [REPOSITORY_ROOT / "skills" / ENVIRONMENT_SOURCE] + [
        REPOSITORY_ROOT / copy_root / ENVIRONMENT_SOURCE for copy_root in GENERATED_SKILL_COPIES
    ]


def names_candidate_interpreter(text: str) -> bool:
    """Whether a text still points a candidate at a named interpreter."""

    return any(token in text for token in FORBIDDEN_TOKENS)


def test_worker_candidate_environment_names_no_candidate_interpreter() -> None:
    """Every shipped Worker candidate-environment action points at the tool notes, not an interpreter."""

    for surface in environment_surfaces():
        assert surface.is_file(), f"Worker candidate-environment action surface missing: {surface}"
        text = surface.read_text(encoding="utf-8")
        assert not names_candidate_interpreter(text), (
            f"{surface} still names a candidate interpreter"
        )
        assert "system/tools.md" in text, (
            f"{surface} no longer points at the repository's tool notes"
        )
        for clause in (
            "Build each worktree's environment as `system/tools.md` says.",
            "Run every candidate command in that environment.",
            "Report a command the tool notes lack,",
            "record the interpreter each run used.",
        ):
            assert text.count(clause) == 1, (surface, clause)
        assert "record the interpreter each run used" in text, (
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
    shipped = (REPOSITORY_ROOT / "skills" / ENVIRONMENT_SOURCE).read_text(encoding="utf-8")
    assert not names_candidate_interpreter(shipped)


def test_candidate_accounting_excludes_worktree_environments(tmp_path: Path) -> None:
    """The closeout working tier never carries either worktree environment directory.

    The temporary repository holds its own built ``mcp/.venv`` and populated
    ``dashboard/node_modules``; both must be excluded by the product ignore rules.
    """

    root = tmp_path / "repository"
    init_repo(root)
    (root / ".gitignore").write_bytes((REPOSITORY_ROOT / ".gitignore").read_bytes())
    venv.EnvBuilder(with_pip=False).create(root / "mcp" / ".venv")
    modules = root / "dashboard" / "node_modules"
    modules.mkdir(parents=True)
    (modules / "fixture.js").write_text("// installed fixture\n", encoding="utf-8")
    assert (root / "mcp" / ".venv").is_dir(), "no built venv to exclude"
    paths = changed_worktree_paths(root)
    offenders = [
        path for path in paths if ".venv" in Path(path).parts or "node_modules" in Path(path).parts
    ]
    assert offenders == [], "candidate accounting carries environment paths:\n  " + "\n  ".join(
        offenders
    )
