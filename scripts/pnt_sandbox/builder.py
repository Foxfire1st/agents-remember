"""Build the sandbox: repository, memory repository, coordination root, settings, Eve application.

Every step creates what is missing and leaves what exists, so the command is safe to repeat and a
rebuild never rewrites a task document or the repository an agent has worked in. The coordination
root, the memory repository and the task documents are created by the tool server of the PNT
build under test; this module writes only plain files and runs Git.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import fixture
from .layout import (
    INTEGRATION_BRANCH,
    REPOSITORY_ID,
    SANDBOX_SCHEMA,
    SandboxLayout,
    SandboxRefusal,
    location_refusal,
    read_marker,
    settings_document,
)
from .operations import HELPERS, Operations, StepFailed

LAYOUT_VERSION = 1
EVE_LAUNCHER_SOURCE = HELPERS / "eve-acp-launcher.mjs"
# What the sandbox's Eve application takes from the developer's Eve project. Its connections are
# left out on purpose: they point the agent at the developer's installed AR tool server.
EVE_APPLICATION_FILES = ("package.json", "tsconfig.json", "agent/agent.ts", "agent/channels/eve.ts")
EVE_INSTRUCTIONS = "You are a coding agent working in a disposable sandbox. Keep answers short.\n"
_SEED_IDENTITY = ("-c", "user.name=PNT Sandbox", "-c", "user.email=pnt-sandbox@invalid.example")

Out = Callable[[str], None]


def is_built(layout: SandboxLayout) -> bool:
    marker = read_marker(layout)
    return (
        marker is not None
        and marker.get("state") == "built"
        and marker.get("layout") == LAYOUT_VERSION
    )


def require_sandbox_directory(layout: SandboxLayout) -> None:
    """Refuse a directory that cannot or must not become a sandbox."""
    refusal = location_refusal(layout.root)
    if refusal is not None:
        raise SandboxRefusal(refusal)
    unmarked = layout.root.exists() and read_marker(layout) is None
    if unmarked and (not layout.root.is_dir() or any(layout.root.iterdir())):
        raise SandboxRefusal(
            f"{layout.root} exists and is not a sandbox this tool built; refusing to use it"
        )


def _write_marker(layout: SandboxLayout, state: str, eve_project: Path | None) -> None:
    marker = {
        "schema": SANDBOX_SCHEMA,
        "layout": LAYOUT_VERSION,
        "state": state,
        "root": layout.root.as_posix(),
        "repository": REPOSITORY_ID,
        "eveProject": eve_project.as_posix() if eve_project is not None else None,
    }
    layout.marker.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")


def _put(path: Path, content: str) -> bool:
    """Write ``content`` unless the file already holds it; say whether it was written."""
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _git(ops: Operations, cwd: Path, *args: str) -> str:
    return ops.require("git", ["git", *args], cwd=cwd, timeout=120).stdout.strip()


def _seed_repository(layout: SandboxLayout, ops: Operations) -> str:
    repository = layout.repository
    if (repository / ".git").exists():
        return "present"
    for relative, content in fixture.REPOSITORY_FILES.items():
        _put(repository / relative, content)
    _git(ops, repository, "init", "--quiet", "--initial-branch=main")
    _git(ops, repository, "add", "--all")
    _git(ops, repository, *_SEED_IDENTITY, "commit", "--quiet", "-m", "Seed the sandbox app")
    return "created"


def _seed_origin(layout: SandboxLayout, ops: Operations) -> str:
    """Give the repository the default-branch authority the build's worktree owner requires.

    The origin is a bare repository inside the sandbox; the sprint's integration branch exists
    beside ``main`` so that leaf enclosures can be opened from it.
    """
    repository, origin = layout.repository, layout.origin
    state = "present"
    if not origin.exists():
        origin.parent.mkdir(parents=True, exist_ok=True)
        _git(ops, origin.parent, "init", "--quiet", "--bare", "--initial-branch=main", origin.name)
        state = "created"
    remotes = _git(ops, repository, "remote").split()
    if "origin" not in remotes:
        _git(ops, repository, "remote", "add", "origin", origin.as_posix())
    elif Path(_git(ops, repository, "remote", "get-url", "origin")).resolve() != origin.resolve():
        raise StepFailed("repository", "the sandbox repository's origin is not the sandbox's own")
    if not _git(ops, repository, "branch", "--list", INTEGRATION_BRANCH):
        _git(ops, repository, "branch", INTEGRATION_BRANCH, "main")
    if not _git(ops, repository, "branch", "--remotes", "--list", f"origin/{INTEGRATION_BRANCH}"):
        _git(ops, repository, "push", "--quiet", "--set-upstream", "origin", "main")
        _git(ops, repository, "push", "--quiet", "--set-upstream", "origin", INTEGRATION_BRANCH)
        _git(ops, repository, "fetch", "--quiet", "origin")
        _git(ops, repository, "remote", "set-head", "origin", "main")
    return state


def _seed_eve(layout: SandboxLayout, eve_project: Path) -> Path | None:
    """Create the sandbox's Eve application; the env file to load at launch, or ``None``.

    ``None`` means the developer has no usable Eve project. Dependencies are linked, not copied,
    and the env file with the model keys stays where it is.
    """
    sources = [eve_project / name for name in EVE_APPLICATION_FILES]
    dependencies = eve_project / "node_modules"
    if not all(source.is_file() for source in sources) or not dependencies.is_dir():
        return None
    for name, source in zip(EVE_APPLICATION_FILES, sources, strict=True):
        _put(layout.eve_app / name, source.read_text(encoding="utf-8"))
    _put(layout.eve_app / "agent" / "instructions.md", EVE_INSTRUCTIONS)
    link = layout.eve_app / "node_modules"
    if link.is_symlink() and link.resolve() != dependencies.resolve():
        link.unlink()
    if not link.is_symlink():
        link.symlink_to(dependencies, target_is_directory=True)
    _put(layout.eve_launcher, EVE_LAUNCHER_SOURCE.read_text(encoding="utf-8"))
    layout.eve_launcher.chmod(0o755)
    return eve_project / ".env.local"


def _memory_has_baseline(layout: SandboxLayout, ops: Operations) -> bool:
    if not (layout.memory / ".git").exists():
        return False
    head = ops.run(["git", "rev-parse", "--verify", "HEAD"], cwd=layout.memory, timeout=60)
    return head.returncode == 0


def _corpus_request(layout: SandboxLayout, ops: Operations, out: Out) -> dict[str, Any]:
    """The calls still needed; what already exists is reported and left alone."""
    target = {"repo_id": REPOSITORY_ID}
    steps: list[tuple[bool, dict[str, Any]]] = [
        (
            all((layout.coordination / name).is_dir() for name in ("skills", "system", "tasks")),
            {
                "name": "coordination root",
                "tool": "runtime_install",
                "arguments": {"install_provider_deps": False},
            },
        ),
        (
            (layout.memory / ".git").exists(),
            {
                "name": "memory repository",
                "tool": "memory_init",
                "arguments": {**target, "initial_branch": "main", "initialize_git": True},
            },
        ),
        (
            _memory_has_baseline(layout, ops),
            {
                "name": "memory baseline",
                "tool": "memory_baseline_adopt",
                "arguments": {**target, "source_branch": "main"},
            },
        ),
    ]
    calls = [call for present, call in steps if not present]
    for present, call in steps:
        if present:
            out(f"  {call['name']}: present")
    return {
        "config": layout.settings_file.as_posix(),
        "cwd": layout.root.as_posix(),
        "serverLog": (layout.run_dir / "tool-server.log").as_posix(),
        "expect": {
            "coordinationRoot": layout.coordination.as_posix(),
            "workspaceRoot": layout.projects.as_posix(),
            "allowedRepoIds": [REPOSITORY_ID],
        },
        "calls": [*calls, *fixture.task_document_calls()],
    }


def _seed_corpus(layout: SandboxLayout, checkout: Path, ops: Operations, out: Out) -> None:
    """Have the build's own tool server create the coordination root, memory and task documents."""
    master = layout.task_root / fixture.MASTER_TASK
    for relative, content in fixture.packet_files().items():
        if not (master / relative).exists():
            _put(master / relative, content)
    layout.run_dir.mkdir(parents=True, exist_ok=True)
    request = layout.run_dir / "corpus-request.json"
    request.write_text(
        json.dumps(_corpus_request(layout, ops, out), indent=2) + "\n", encoding="utf-8"
    )
    report = ops.tool_calls(checkout, request)
    for result in report.get("results", []):
        action = "created" if result.get("action") == "called" else "present"
        out(f"  {result.get('name')}: {action if result.get('ok') else 'FAILED'}")
    if report.get("ok") is not True:
        failed = [result for result in report.get("results", []) if not result.get("ok")]
        detail = failed[-1].get("detail") if failed else None
        raise StepFailed(
            "sandbox corpus",
            f"{report.get('error')}{': ' + detail if detail else ''}",
            layout.run_dir / "tool-server.log",
        )
    if not _git(ops, layout.memory, "branch", "--list", INTEGRATION_BRANCH):
        _git(ops, layout.memory, "branch", INTEGRATION_BRANCH, "main")


def build(
    layout: SandboxLayout, checkout: Path, ops: Operations, out: Out, eve_project: Path
) -> None:
    """Bring the sandbox directory to its built state; every step is safe to repeat."""
    require_sandbox_directory(layout)
    layout.root.mkdir(parents=True, exist_ok=True)
    recorded_eve = eve_project if eve_project.is_dir() else None
    if not is_built(layout):
        _write_marker(layout, "building", recorded_eve)
    for folder in (layout.projects, layout.harness_skills, layout.run_dir, layout.tmux_dir):
        folder.mkdir(parents=True, exist_ok=True)
    layout.tmux_dir.chmod(0o700)
    out(f"repository {layout.repository}: {_seed_repository(layout, ops)}")
    out(f"origin {layout.origin}: {_seed_origin(layout, ops)}")
    eve_env_file = _seed_eve(layout, eve_project)
    if eve_env_file is None:
        shutil.rmtree(layout.eve_app.parent, ignore_errors=True)
        out(f"Eve: no usable Eve project at {eve_project}; the Eve provider entry is omitted")
    else:
        out(f"Eve application {layout.eve_app}: from {eve_project}")
    settings = json.dumps(settings_document(layout, eve_env_file), indent=2) + "\n"
    out(
        f"settings {layout.settings_file}: "
        f"{'written' if _put(layout.settings_file, settings) else 'unchanged'}"
    )
    for line in ops.prepare_python(checkout):
        out(line)
    out(
        "coordination root, memory repository and task documents (through the build's tool server):"
    )
    _seed_corpus(layout, checkout, ops, out)
    _write_marker(layout, "built", recorded_eve)
    out(f"sandbox built at {layout.root}")
