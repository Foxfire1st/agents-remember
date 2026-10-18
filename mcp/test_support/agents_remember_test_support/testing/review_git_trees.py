"""The unchanged native Git-tree review world used by focused tests."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_worklist.code import CodeTrees
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.models.knowledge_files import canonical_text

REPO = "agents-remember"


MASTER = "260101_tree_review"


TASK = "260101-TRV"


LEAF = "260101-TRV-L1"


INVARIANT = "INV-AAAAAA"


FAMILY = "FAM-F00001"


CODE_FILE = "pkg/a.py"


CODE_V1 = "def land(value):\n    return value\n\n\ndef keep():\n    return 1\n"


ORIGIN = {"task": "260101-OLD", "leaf": "260101-OLD-L1"}


INVARIANT_PATH = f"knowledge/invariants/{INVARIANT}-land.json"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_DATE": "2026-10-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-10-01T00:00:00+00:00",
        },
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def commit(root: Path, files: dict[str, str], trailer: str | None = None) -> str:
    _write(root, files)
    git(root, "add", "-A")
    message = "change" if trailer is None else f"memory\n\nCode-Commit: {trailer}"
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def invariant(revision: int = 1, statement: str = "Values land unchanged.") -> str:
    return canonical_text(
        {
            "schema": "ar-invariant/v1",
            "id": INVARIANT,
            "revision": revision,
            "status": "accepted",
            "statement": statement,
            "applicability": "Always.",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": "legacy-unassessed",
            "origin": ORIGIN,
        }
    )


def converted_memory(code: Path, code_commit: str) -> dict[str, str]:
    tree = git(code, "rev-parse", f"{code_commit}^{{tree}}")
    trees = CodeTrees.open(code, tree, tree)
    blob = trees.base()[CODE_FILE]

    def realization(entry_id: str, name: str) -> dict[str, Any]:
        locator = {"kind": "symbol", "name": name}
        resolved = trees.resolve(CODE_FILE, locator, blob, blob)
        assert resolved is not None
        return {
            "id": entry_id,
            "invariant": INVARIANT,
            "anchor": {"locator": locator, "blob": blob, "content": resolved.content},
            "role": "primary-authority",
            "rationale": "It is the rule.",
        }

    return {
        "knowledge/layout.json": canonical_text(
            {"schema": "ar-memory-layout/v2", "conversion": "1"}
        ),
        INVARIANT_PATH: invariant(),
        f"knowledge/families/{FAMILY}-landing.json": canonical_text(
            {
                "schema": "ar-family/v1",
                "id": FAMILY,
                "revision": 1,
                "status": "accepted",
                "title": "Landing",
                "guarantee": "Landing holds.",
                "members": [INVARIANT],
                "routes": ["pkg"],
                "admission": "legacy-unassessed",
                "origin": ORIGIN,
            }
        ),
        f"onboarding/{CODE_FILE}.md": "# a\n",
        f"onboarding/{CODE_FILE}.json": canonical_text(
            {
                "schema": "ar-onboarding-file/v1",
                "path": CODE_FILE,
                "references": {},
                "realizes": [realization("RLZ-A00001", "land"), realization("RLZ-A00002", "keep")],
            }
        ),
        "onboarding/overview.md": "# root\n\n## What This Repo Is\n\nThe repository root route.\n",
    }


def history_file() -> str:
    return canonical_text(
        {
            "schema": "ar-history/v1",
            "leaf": LEAF,
            "closed": False,
            "rows": [
                {
                    "id": "ROW-9Q7K3F",
                    "subject": FAMILY,
                    "disposition": "no_impact",
                    "reason": "every member still holds together",
                    "items": [],
                    "examined": [{"id": INVARIANT, "revision": 2}],
                }
            ],
        }
    )


@dataclass
class World:
    root: Path
    config: McpRuntimeConfig
    code: Path
    memory: Path
    code_worktree: Path
    memory_worktree: Path
    task_root: Path
    code_base: str
    memory_base: str

    def contract(self, *, live: bool = True, **recorded: str) -> Path:
        enclosure = self.task_root / "enclosures" / LEAF.lower()
        enclosure.mkdir(parents=True, exist_ok=True)
        path = enclosure / "series-contract.md"
        code_worktree = self.code_worktree if live else self.root / "removed-code"
        memory_worktree = self.memory_worktree if live else self.root / "removed-memory"
        lines = [
            "---",
            "schema: ar-series-contract/v1",
            "schemaVersion: 1.0",
            "kind: leaf",
            "task_id: 260101_TREE-REVIEW",
            f"task_name: {MASTER}",
            f"repo_name: {REPO}",
            "workflow_kind: light-task",
            "memory_mode: external",
            "",
            "coordination:",
            f"  root: {self.config.coordination_root}",
            f"  task_root: {self.task_root}",
            f"  task_artifact: {self.task_root / 'task.md'}",
            f"  worktree_group: {self.root / 'group'}",
            f"  leaf_id: {LEAF}",
            f"  parent_task_name: {MASTER}",
            "",
            "code:",
            f"  repo_path: {self.code}",
            "  source_branch: main",
            "  work_branch: leaf",
            f"  base_commit: {self.code_base}",
            f"  worktree: {code_worktree}",
            "",
            "memory:",
            "  mode: external",
            f"  repo_path: {self.memory}",
            "  source_branch: main",
            "  work_branch: leaf",
            f"  base_commit: {self.memory_base}",
            f"  worktree: {memory_worktree}",
            f"  ledger: {memory_worktree / 'memory.md'}",
            "",
            "closeout:",
            "  status: not-started",
        ]
        if recorded:
            lines += [f"  {key}: {value}" for key, value in recorded.items()]
        path.write_text("\n".join([*lines, "---", ""]), encoding="utf-8")
        return path

    def edit(self) -> None:
        """The leaf's uncommitted change: a body edit of ``land`` and a revised statement."""

        (self.code_worktree / CODE_FILE).write_text(
            CODE_V1.replace("return value", "return value + 0"), encoding="utf-8"
        )
        _write(
            self.memory_worktree,
            {
                INVARIANT_PATH: invariant(2, "Values land exactly as given."),
                f"knowledge/history/{LEAF}.json": history_file(),
            },
        )

    def review(self, **fields: Any) -> ReviewSurfaceRequest:
        return ReviewSurfaceRequest(repository_id=REPO, master=MASTER, leaf_id=LEAF, **fields)


def _repository(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "tree review fixture")
    return root


def build_world(tmp_path: Path) -> World:
    code = _repository(tmp_path / "repos" / "code")
    code_base = commit(code, {CODE_FILE: CODE_V1})
    memory = _repository(tmp_path / "repos" / "memory")
    memory_base = commit(memory, converted_memory(code, code_base), trailer=code_base)
    code_worktree, memory_worktree = tmp_path / "group" / "code", tmp_path / "group" / "memory"
    git(code, "worktree", "add", "-q", "-b", "leaf", str(code_worktree))
    git(memory, "worktree", "add", "-q", "-b", "leaf", str(memory_worktree))
    coordination = tmp_path / "coordination"
    task_root = coordination / "tasks" / REPO / MASTER
    task_root.mkdir(parents=True)
    (task_root / "task.md").write_text("# task\n", encoding="utf-8")
    (task_root / "task.json").write_text(json.dumps({"id": TASK}), encoding="utf-8")
    config = McpRuntimeConfig(
        workspace_root=tmp_path,
        coordination_root=coordination,
        config_path=tmp_path / "config.json",
        transcript_root=tmp_path / "transcripts",
    )
    made = World(
        tmp_path,
        config,
        code,
        memory,
        code_worktree,
        memory_worktree,
        task_root,
        code_base,
        memory_base,
    )
    made.contract()
    return made
