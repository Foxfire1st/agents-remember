"""A converted memory repository and its code repository, for the MIK-R12 curator writer tests.

:func:`build_world` creates two real Git repositories under ``tmp_path``:

* **code** -- a small Python package and its test module, committed;
* **memory** -- a converted tree (the layout marker) whose committed ``HEAD`` is the writer's base:
  one invariant (``INV-BASE01``) realized by ``land_pair`` (``RLZ-BASE01``, anchored at the committed
  code) and one family (``FAM-FAM001``) with that invariant as its member;

plus a task root with ``task.json`` and a leaf contract that names both worktrees, so the command
line runs exactly as a leaf runs it.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.models.knowledge_files import canonical_text

TASK_ID = "260928-MIK"
LEAF_ID = "260928-MIK-L99"
BASE_INVARIANT = "INV-BASE01"
BASE_REALIZATION = "RLZ-BASE01"
BASE_FAMILY = "FAM-FAM001"
CODE_FILE = "pkg/landing.py"
TEST_FILE = "tests/test_landing.py"

CODE = '''"""Landing."""


def land_pair(code, memory):
    """Land both halves."""
    return (code, memory)


class Helper:
    def check(self):
        return True


class Other:
    def check(self):
        return False
'''

TESTS = """from pkg.landing import land_pair


class LandingTests:
    def test_pair_lands_together(self):
        assert land_pair(1, 2) == (1, 2)


def test_plain():
    assert True
"""


@dataclass(frozen=True)
class World:
    root: Path
    code: Path
    memory: Path
    task_root: Path
    contract: Path


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def _init(root: Path) -> None:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "writer fixture")


def write(root: Path, files: dict[str, str | bytes]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")


def canonical(document: dict[str, Any]) -> str:
    return canonical_text(document)


def base_memory(code: Path) -> dict[str, str | bytes]:
    anchor = CodeSnapshot.capture(code).resolve(CODE_FILE, {"kind": "symbol", "name": "land_pair"})
    origin = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}
    return {
        "knowledge/layout.json": canonical({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        f"knowledge/invariants/{BASE_INVARIANT}-landing-pair.json": canonical(
            {
                "schema": "ar-invariant/v1",
                "id": BASE_INVARIANT,
                "revision": 1,
                "status": "accepted",
                "statement": "Code and memory land as one pair.",
                "applicability": "Every landing.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
                "origin": origin,
            }
        ),
        f"knowledge/families/{BASE_FAMILY}-landing.json": canonical(
            {
                "schema": "ar-family/v1",
                "id": BASE_FAMILY,
                "revision": 1,
                "status": "accepted",
                "title": "Landing",
                "guarantee": "A landing is one recorded pair.",
                "members": [BASE_INVARIANT],
                "routes": ["pkg"],
                "admission": "legacy-unassessed",
                "origin": origin,
            }
        ),
        f"onboarding/{CODE_FILE}.md": "# pkg/landing.py\n",
        f"onboarding/{CODE_FILE}.json": canonical(
            {
                "schema": "ar-onboarding-file/v1",
                "path": CODE_FILE,
                "references": {},
                "realizes": [
                    {
                        "id": BASE_REALIZATION,
                        "invariant": BASE_INVARIANT,
                        "anchor": anchor.to_document(),
                        "role": "primary-authority",
                        "rationale": "The one function that lands both halves.",
                    }
                ],
            }
        ),
        f"onboarding/{TEST_FILE}.md": "# tests/test_landing.py\n",
        "onboarding/overview.md": "# root\n",
    }


def contract_text(world_root: Path, code: Path, memory: Path, task_root: Path) -> str:
    return (
        "---\n"
        "schema: ar-series-contract/v1\n"
        "schemaVersion: 1.0\n"
        "kind: leaf\n"
        "task_id: 260928_WRITER-CASE\n"
        "task_name: writer_case\n"
        "repo_name: agents-remember\n"
        "workflow_kind: light-task\n"
        "memory_mode: external\n"
        "\n"
        "coordination:\n"
        f"  root: {world_root}\n"
        f"  task_root: {task_root}\n"
        f"  task_artifact: {task_root / 'task.md'}\n"
        f"  worktree_group: {world_root}\n"
        f"  leaf_id: {LEAF_ID}\n"
        "  parent_task_name: writer_case\n"
        "\n"
        "code:\n"
        f"  repo_path: {code}\n"
        "  source_branch: main\n"
        "  work_branch: main\n"
        f"  base_commit: {git(code, 'rev-parse', 'HEAD')}\n"
        f"  worktree: {code}\n"
        "\n"
        "memory:\n"
        "  mode: external\n"
        f"  repo_path: {memory}\n"
        "  source_branch: main\n"
        "  work_branch: main\n"
        f"  base_commit: {git(memory, 'rev-parse', 'HEAD')}\n"
        f"  worktree: {memory}\n"
        f"  ledger: {memory / 'memory.md'}\n"
        "---\n"
    )


def build_world(tmp_path: Path) -> World:
    code, memory, task_root = tmp_path / "code", tmp_path / "memory", tmp_path / "task"
    _init(code)
    write(code, {CODE_FILE: CODE, TEST_FILE: TESTS})
    commit_all(code, "code")
    _init(memory)
    write(memory, base_memory(code))
    commit_all(memory, "converted memory")
    write(task_root, {"task.json": json.dumps({"id": TASK_ID}), "task.md": "# task\n"})
    contract = task_root / "contract.md"
    contract.write_text(contract_text(tmp_path, code, memory, task_root), encoding="utf-8")
    return World(root=tmp_path, code=code, memory=memory, task_root=task_root, contract=contract)


def entry(entry_id: str, **curator: Any) -> dict[str, Any]:
    """A producer entry with its thirteen fields, plus the curator's keys given."""

    return {
        "id": entry_id,
        "statement": f"Statement of {entry_id}: a landing is one recorded pair.",
        "kind": "clause",
        "target": [],
        "found_at": [],
        "disposition": "satisfied",
        "disposition_source": None,
        "evidence": None,
        "authority": {"task_document": "writer_case"},
        "resolution": None,
        "validated_at": None,
        "record_action": None,
        "supersedes": None,
        **curator,
    }


SCOPE = {"applicability": "Every landing.", "conditions": [], "exclusions": []}
ADMISSION = {"criteria": ["guarded_by_test"], "justification": "A test proves it."}


def target(symbol: str, rationale: str = "It lands both halves.", **extra: Any) -> dict[str, Any]:
    return {
        "path": CODE_FILE,
        "locator": {"kind": "symbol", "value": symbol},
        "rationale": rationale,
        **extra,
    }


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def read_json(root: Path, relative: str) -> dict[str, Any]:
    return json.loads((root / relative).read_text(encoding="utf-8"))
