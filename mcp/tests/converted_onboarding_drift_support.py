"""Shared trees, anchors and the validation binding for the converted-drift cases.

MIK-R44@v2 asks one behavioural case per rule; the cases are split over two modules so each one
stays readable:

* ``test_converted_onboarding_drift.py`` -- the converted branch, the measured fields, the empty
  and unbuildable bases, the carried-anchor refusal, the three entrances and the baseline
  adoption transaction (rules 1-7);
* ``test_converted_onboarding_drift_leaf_route.py`` -- the description/parity/packaging surfaces
  (rules 8-10 and L44-C1-10), the context leaf route (05), the baseline drift block (06), the
  onboarding-root error (07), the empty and malformed sidecars (08), an unbuildable base that
  withholds its refusals (09), the L44-C1 repair cases (03), the two hooks (04) and the
  unreadable history (11).

Every tree, card and anchor below is built here, once, so the two modules cannot disagree about
what a converted memory or a stale anchor is.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.memory_tools import (
    memory_init_tool,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    run_drift_summary,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.memory_quality.reference_state import git_blob_id
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from test_worktree_support import git, init_repo

REPO_ID = "repo"
LAYOUT_MARKER = '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n'


def _commit(repo: Path, message: str = "init") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def _code_tree(root: Path) -> Path:
    """A git code checkout with two tracked source files."""

    code = root / "code"
    init_repo(code, "main")
    (code / "pkg").mkdir(parents=True, exist_ok=True)
    (code / "pkg" / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    (code / "pkg" / "beta.py").write_text("print('beta')\n", encoding="utf-8")
    _commit(code)
    return code


def _converted_memory(root: Path, name: str = "mem") -> Path:
    """A git memory checkout holding the converted layout marker."""

    memory = root / name
    init_repo(memory, "main")
    (memory / "knowledge").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "knowledge" / "layout.json").write_text(LAYOUT_MARKER, encoding="utf-8")
    return memory


def _anchor(*, blob: str, content: str) -> dict[str, Any]:
    return {
        "kind": "code",
        "anchor": {"blob": blob, "locator": {"kind": "file"}, "content": content},
    }


def _current_anchor(code: Path, rel: str) -> dict[str, Any]:
    data = (code / rel).read_bytes()
    return _anchor(blob=git_blob_id(data), content=content_identity(data))


def _stale_anchor() -> dict[str, Any]:
    return _anchor(blob="0" * 40, content="sha256:" + "0" * 64)


def _write_card(memory: Path, rel: str, targets: list[dict[str, Any]]) -> Path:
    """A canonical file sidecar at `onboarding/<rel>.json` plus its marked card."""

    document = {
        "path": rel,
        "schema": "ar-onboarding-file/v1",
        "realizes": [],
        "references": {"1": {"targets": targets}},
    }
    sidecar = memory / "onboarding" / f"{rel}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(canonical_text(document), encoding="utf-8")
    card = memory / "onboarding" / f"{rel}.md"
    card.write_text(f"# {rel}\n\nSee [1].\n", encoding="utf-8")
    return sidecar


def _context(memory: Path, root: Path) -> SimpleNamespace:
    coordination = root / "coordination"
    temp = root / "temp"
    coordination.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        onboarding_root=memory / "onboarding",
        memory_root=memory,
        coordination_root=coordination,
        temp_root=temp,
    )


def _summarize(*args: Any, **kwargs: Any) -> Any:
    """`run_drift_summary` with its packet as a plain mapping for assertions."""

    return run_drift_summary(*args, **kwargs)


@contextmanager
def bound_validation_services() -> Iterator[None]:
    """Bind real worktree services so the first baseline validates before it commits."""

    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=mock.Mock(),
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
        )
    )
    try:
        yield
    finally:
        reset_worktree_services()


def _world(tmp_path: Path) -> tuple[Path, Path]:
    """A code repository, a coordination root, and a settings file outside both."""

    root = tmp_path / "world"
    code = root / REPO_ID
    init_repo(code, "main")
    (code / "pkg").mkdir(parents=True, exist_ok=True)
    (code / "pkg" / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    _commit(code)
    (root / "coordination").mkdir(parents=True, exist_ok=True)
    path = root / "settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coordination").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO_ID: {}},
            }
        ),
        encoding="utf-8",
    )
    return code, path


def _adoptable_converted_world(
    tmp_path: Path, *, stale: bool = False, violation: bool = False, card: bool = False
) -> tuple[Any, Path, Path]:
    code, path = _world(tmp_path)
    memory_init_tool(load_config(path), repo_id=REPO_ID, initial_branch="main")
    memory = load_config(path).repositories[REPO_ID].memory_root
    assert memory is not None
    if stale or violation or card:
        target = "pkg/gone.py" if violation else "pkg/alpha.py"
        # A stale anchor refuses the pair at every publication route (MIK-R198 rule 5), so a world
        # that must be adopted carries an anchor that holds: ``card`` writes the current one.
        anchor = (
            _current_anchor(code, target)
            if card
            else _anchor(blob="0" * 40, content="sha256:" + "2" * 64)
        )
        document = {
            "path": target,
            "schema": "ar-onboarding-file/v1",
            "realizes": [],
            "references": {"1": {"targets": [anchor]}},
        }
        sidecar = memory / "onboarding" / f"{target}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(canonical_text(document), encoding="utf-8")
        (memory / "onboarding" / f"{target}.md").write_text(
            "# card\n\nSee [1].\n", encoding="utf-8"
        )
    return load_config(path), code, memory


def _fingerprint(repo: Path) -> tuple[str, str, str, str]:
    """The index, refs, status and file bytes a no-write run must preserve."""

    head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    index = subprocess.run(
        ["git", "write-tree"], cwd=repo, text=True, capture_output=True, check=False
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, text=True, capture_output=True, check=False
    )
    digest = hashlib.sha256()
    for path in sorted(repo.rglob("*")):
        if ".git" in path.parts or not path.is_file():
            continue
        digest.update(path.relative_to(repo).as_posix().encode())
        digest.update(path.read_bytes())
    return (
        head.stdout.strip() if head.returncode == 0 else "unborn",
        index.stdout.strip(),
        status.stdout,
        digest.hexdigest(),
    )
