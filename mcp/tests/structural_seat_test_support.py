"""Shared real-store fixtures for canonical structural-seat forcing tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.terminal import TerminalSessionBinding, TerminalSessionSpec
from agents_remember.tasks import TaskDocument, write_task_doc


def _task_doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": values.pop("id"),
            "slug": values.pop("slug"),
            "title": values.pop("title"),
            "kind": values.pop("kind"),
            "repo": "repo",
            "createdAt": "2026-08-25T00:00",
            **values,
        }
    )


def write_structural_topology(
    root: Path,
) -> tuple[TaskDocumentRef, TaskDocumentRef, TaskDocumentRef]:
    task_root = root / "tasks" / "repo"
    write_task_doc(
        task_root / "sprint",
        _task_doc(
            id="SPRINT",
            slug="sprint",
            title="Sprint",
            kind="master",
            orchestrates=["master"],
            integrationBranch="ar/super",
            executionGraph={
                "nodes": [{"repository": "repo", "path": "master/task.json"}],
                "edges": [],
            },
        ),
    )
    write_task_doc(
        task_root / "master",
        _task_doc(
            id="MASTER",
            slug="master",
            title="Master",
            kind="master",
            executionNature="organizational",
            subTasks=[
                {
                    "number": "leaf-1",
                    "name": "Leaf 1",
                    "file": "leaf-1.md",
                    "status": "inProgress",
                }
            ],
        ),
    )
    write_task_doc(
        task_root / "master",
        _task_doc(
            id="leaf-1",
            slug="leaf-1",
            title="Leaf 1",
            kind="subTask",
            master="task.md",
        ),
    )
    return (
        TaskDocumentRef(repository="repo", path="sprint/task.json"),
        TaskDocumentRef(repository="repo", path="master/task.json"),
        TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
    )


def structural_config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root / "workspace",
        transcript_root=root / "logs" / "mcp",
        repositories={"repo": RepositoryScope("repo", root / "workspace" / "repo")},
    )


def write_structural_settings(root: Path) -> None:
    path = agentic_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    roles = {
        role: {"harness": "claude", "model": "claude-fable-5", "effort": "high"}
        for role in ("architect", "orchestrator", "strategist", "manager", "worker")
    }
    path.write_text(json.dumps({"orchestration": {"roles": roles}}), encoding="utf-8")


def detected_harness(_command: str) -> str | None:
    return "/usr/bin/harness"


class FakeHost:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.known: set[str] = set()
        self.ensured: list[str] = []
        self.terminated: list[str] = []

    def has_session(self, tmux_name: str) -> bool:
        with self._lock:
            return tmux_name in self.known

    def ensure(self, sid: str, spec: TerminalSessionSpec) -> TerminalSessionBinding:
        binding = TerminalSessionBinding(
            sid=sid,
            tmux_name=spec.tmux_name_for(sid),
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            suspend_unsafe=spec.suspend_unsafe,
        )
        with self._lock:
            self.known.add(binding.tmux_name)
            self.ensured.append(sid)
        return binding

    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:
        with self._lock:
            if tmux_name is not None:
                self.known.discard(tmux_name)
            self.terminated.append(sid)

    def shutdown(self) -> None:
        return None
