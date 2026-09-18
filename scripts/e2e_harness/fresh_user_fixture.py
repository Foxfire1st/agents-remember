"""Deterministic, self-contained fixture repositories for the fresh-user acceptance.

Two repositories, created from nothing, each with the shape the packet names:

``spear-not-main``
    the spear branch is ``dev``, and the tree carries a source file over the per-file cap plus
    a vendored/generated tree that the exclusion register excludes. This is the fixture that
    would have made the pre-ruling citation index refuse outright.

``plain-main``
    an ordinary ``main``-spear repository for the default path.

WHAT IS DELIBERATELY ABSENT
---------------------------
Nothing here reads the developer's real repositories, this master's coordination tree,
``ar-coordination/memory-repos/**``, or any machine-local state. Every path is under the run
root the caller supplies, and every Git repository is created by this module. That is what
"clean environment" means for this packet; it is also why the module takes a ``root`` and
never a repository path.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[2] / "mcp" / "src"
# The fixture's own oversized source: 4 MiB + 1 byte is the shipped per-file cap plus one.
OVERSIZED_BYTES = 4 * 1024 * 1024 + 1
# The sprint's integration workbench. It is neither ``main`` (the code repository's default) nor
# the spear (the branch the memory repo is founded on), because the integration-branch authority
# refuses a sprint that claims either.
SPRINT_BRANCH = "ar/super"
COORDINATION_DIRECTORIES = ("memory-repos", "tasks", "worktrees", "notes", "temp", "system")


@dataclass(frozen=True)
class FreshUserFixture:
    """One disposable clean-room repository and everything a user's first hour touches."""

    name: str
    root: Path
    repo_id: str
    spear_branch: str
    sprint_branch: str
    coordination_root: Path
    workspace_root: Path
    code_repo: Path
    memory_root: Path
    authority_path: Path
    base_commit: str

    @property
    def master_branch(self) -> str:
        """The atomic master's series branch: what the master's own start creates and the leaf cuts."""
        return f"ar/{self.repo_id}-master"

    @property
    def memory_settings(self) -> Path:
        return self.memory_root / "system" / "settings.json"


def git(root: Path, *args: str) -> str:
    """One fixture Git command; a failure is the fixture's, so it is raised, not swallowed."""
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {result.stderr or result.stdout}")
    return result.stdout.strip()


def _sparse(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size)


def create_fresh_user_fixture(
    root: Path,
    *,
    name: str,
    spear_branch: str,
    over_cap_source: bool,
    vendored_tree: bool,
) -> FreshUserFixture:
    """Create one fixture repository, its memory root, and its authority file.

    ``root`` is the run root; everything the fixture owns is beneath it and nothing outside it
    is read. The authority file is deliberately beside the coordination root rather than inside
    it, because the product refuses settings that live inside the coordinator root.
    """
    repo_id = name
    # The sprint integrates on its own workbench, which must differ from BOTH repositories'
    # default branches (the integration-branch authority refuses a sprint that claims one) and
    # must exist in the memory repo too (the memory series source branch). `main` stays the code
    # repository's default from ``origin/HEAD``; ``memory_init`` founds the memory repo on the
    # spear, so the series branch is a third name.
    sprint_branch = SPRINT_BRANCH
    coordination = root / "coordination"
    workspace = root / "workspace"
    code = workspace / repo_id
    for directory in (coordination, workspace, code):
        directory.mkdir(parents=True, exist_ok=True)
    for directory in COORDINATION_DIRECTORIES:
        (coordination / directory).mkdir(parents=True, exist_ok=True)

    memory_root = coordination / "memory-repos" / f"ar-{repo_id}"
    memory_root.mkdir(parents=True, exist_ok=True)

    # The repository is founded on ``main`` and the SPEAR is a separate branch: the packet asks
    # for a fixture whose spear is not ``main``, and the sprint declares the spear as its
    # integration branch, so a repository whose own default branch were the spear would have two
    # surfaces claiming one branch.
    git(code, "init", "--quiet", "-b", "main")
    git(code, "config", "user.email", "fresh-user@example.invalid")
    git(code, "config", "user.name", "Fresh User")
    (code / "README.md").write_text(f"# {repo_id}\n", encoding="utf-8")
    (code / ".gitignore").write_text("/scratch/\n", encoding="utf-8")
    (code / "src").mkdir(exist_ok=True)
    (code / "src" / "app.py").write_text("FIRST = 1\nSECOND = 2\n", encoding="utf-8")
    if vendored_tree:
        (code / "vendor").mkdir(exist_ok=True)
        (code / "vendor" / "lib.py").write_text("VENDORED = 1\n", encoding="utf-8")
        (code / "vendor" / "bundle.min.js").write_text("var v=1;\n", encoding="utf-8")
    if over_cap_source:
        _sparse(code / "src" / "generated-blob.bin", OVERSIZED_BYTES)
    git(code, "add", "--all")
    git(code, "commit", "--quiet", "-m", "fixture base")
    base_commit = git(code, "rev-parse", "HEAD")
    if spear_branch != "main":
        git(code, "checkout", "--quiet", "-b", spear_branch)
    if spear_branch != "main":
        git(code, "checkout", "--quiet", spear_branch)
    git(code, "branch", "--quiet", sprint_branch, spear_branch)
    git(code, "update-ref", f"refs/remotes/origin/{spear_branch}", base_commit)
    git(code, "update-ref", f"refs/remotes/origin/{sprint_branch}", base_commit)
    # ``origin/HEAD`` is what the product reads as the repository's DEFAULT branch, and a sprint
    # may not claim that branch as its own integration workbench. It therefore stays on ``main``
    # while the spear (``dev``/``main``) and the sprint workbench (``ar/super``) are separate.
    git(code, "update-ref", "refs/remotes/origin/main", base_commit)
    git(code, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if over_cap_source:
        # Untracked-but-unignored, so the population the index reads really contains it.
        (_sparse(code / "scratch" / "dev-host" / "index.mjs", OVERSIZED_BYTES))

    authority = root / "mcp-authority.json"
    authority.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coordination.as_posix(),
                "workspaceRoot": workspace.as_posix(),
                "transcriptRoot": (coordination / "logs" / "mcp").as_posix(),
                "repositories": {repo_id: {}},
                "providers": {},
                "dashboard": {"autoStart": False},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return FreshUserFixture(
        name=name,
        root=root,
        repo_id=repo_id,
        spear_branch=spear_branch,
        sprint_branch=sprint_branch,
        coordination_root=coordination,
        workspace_root=workspace,
        code_repo=code,
        memory_root=memory_root,
        authority_path=authority,
        base_commit=base_commit,
    )


def write_exclusion_register(
    fixture: FreshUserFixture,
    *,
    excludes: tuple[str, ...],
    citation_index: dict[str, int] | None = None,
) -> Path:
    """Persist the register the exclusion review agreed, the way the ruling requires it.

    Judgement first, no new MCP tool (developer ruling 2026-08-21): the review is skill-based
    guidance, and its durable output is this file. Writing it here is the *persistence* step of
    that flow, not a replacement for the review.
    """
    settings: dict[str, object] = {
        "storage": {"mode": "external"},
        "pathRules": {
            "path": "",
            "include": {"paths": ["*"]},
            "exclude": {"paths": list(excludes)},
        },
    }
    if citation_index:
        settings["citationIndex"] = citation_index
    path = fixture.memory_settings
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"onboarding": settings}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def write_thin_bootstrap(fixture: FreshUserFixture) -> Path:
    """The durable outcome a thin ``c-03`` bootstrap produces, written by the scenario.

    ``c-03-repo-bootstrap`` is an LLM-driven procedure with **no runtime entry point** — no MCP
    tool, no CLI, no operation in the frozen capsule vocabulary. A script therefore cannot *run*
    it; it can only produce the same durable artifact, which the skill states is the minimum
    successful bootstrap: one root ``overview.md`` under the resolved ``onboarding_root``. This
    function is that artifact, and the scenario records the distinction rather than presenting
    the file as an executed procedure.
    """
    onboarding = fixture.memory_root / "onboarding"
    onboarding.mkdir(parents=True, exist_ok=True)
    overview = onboarding / "overview.md"
    overview.write_text(
        "# Repository Overview\n\n"
        "| Field | Value |\n"
        "| --- | --- |\n"
        f"| repository | {fixture.repo_id} |\n"
        f"| spearBranch | `{fixture.spear_branch}` |\n\n"
        "## Purpose\n\n"
        f"{fixture.repo_id} is a fresh-user acceptance fixture created by the e2e harness.\n\n"
        "## Repo-Internal References\n\n"
        "| Finding | Anchor | Source |\n"
        "| --- | --- | --- |\n"
        "| the first constant | `FIRST` | `src/app.py:1-1` |\n",
        encoding="utf-8",
    )
    return overview
