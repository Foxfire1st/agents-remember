"""dispatch_agent ambient caller mode: spawning without plane-injected hosted identity."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.structural.agent_tools import (
    StructuralAgentRuntime,
    dispatch_agent_tool,
)
from agents_remember.application.terminal_tools import SpawnOverrides
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.structural.agent import DispatchAgentRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.ambient_seat import (
    AmbientCaller,
    AmbientSeatError,
    resolve_ambient_caller,
)
from agents_remember.serving.terminal import TerminalSessionBinding, TerminalSessionSpec
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from agents_remember.tasks import TaskDocument, write_task_doc


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
        repositories={"repo": RepositoryScope("repo", root / "workspace" / "repo")},
    )


def _task_doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": values.pop("id"),
            "slug": values.pop("slug"),
            "title": values.pop("title"),
            "kind": values.pop("kind"),
            "repo": "repo",
            "createdAt": "2026-08-11T00:00",
            **values,
        }
    )


def _write_topology(root: Path) -> tuple[TaskDocumentRef, TaskDocumentRef, TaskDocumentRef]:
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
                "nodes": [
                    {"repository": "repo", "path": "master/task.json"},
                ],
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
            executionNature="atomic",
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


def _seat(
    session_id: str,
    document: TaskDocumentRef,
    role: str,
    *,
    status: str = "running",
    spawned_by_kind: str | None = "ambient",
) -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id=session_id,
        label=session_id,
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-08-11T00:00:00+00:00",
        last_attached_at="2026-08-11T00:00:00+00:00",
        status=status,  # type: ignore[arg-type]
        task_document_ref=document,
        seat_role=role,
        spawned_by_kind=spawned_by_kind,
    )


def _detected(_command: str) -> str | None:
    return "/usr/bin/harness"


def _write_architect_settings(root: Path) -> None:
    """A settings-owned architect launch selection (mirrors the spawn wire tests)."""
    path = agentic_settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "orchestration": {
                    "roles": {
                        "architect": {
                            "harness": "claude",
                            "model": "claude-fable-5",
                            "effort": "max",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )


class _FakeHost:
    """A terminal host that records spawns and never owns a real tmux session."""

    def __init__(self) -> None:
        self.ensured: list[dict[str, object]] = []
        self.known: set[str] = set()
        self.terminated: list[tuple[str, str | None]] = []

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.known

    def shutdown(self) -> None:
        return None

    def ensure(self, sid: str, spec: TerminalSessionSpec) -> TerminalSessionBinding:
        tmux_name = spec.tmux_name_for(sid)
        self.ensured.append({"sid": sid, "env": dict(spec.env or {}), "command": spec.command})
        self.known.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            suspend_unsafe=spec.suspend_unsafe,
        )

    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:
        self.terminated.append((sid, tmux_name))


class DispatchAgentAmbientTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.sprint, self.master, self.leaf = _write_topology(self.root)
        self.config = _config(self.root)
        self.catalog = TerminalCatalog(terminal_catalog_path(self.root))

    def test_ambient_dispatch_spawns_on_the_canonical_document_without_hosted_env(self) -> None:
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "ambient-architect"},
            ) as spawn,
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                return_value={
                    "ok": True,
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                },
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "dispatched")
        self.assertEqual(result["taskDocumentRef"], self.sprint.model_dump())
        seat = spawn.call_args.kwargs["seat"]
        self.assertEqual(seat.env, {"AR_SPAWN_ROLE": "architect"})
        self.assertEqual(seat.task_document_ref, self.sprint)
        spawned_by = spawn.call_args.kwargs["spawned_by"]
        self.assertEqual(spawned_by.caller_kind, "ambient")
        self.assertIsNone(spawned_by.session_id)

    def test_ambient_dispatch_refuses_unknown_task_reference_before_spawn(self) -> None:
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=TaskDocumentRef(
                        repository="repo", path="sprint/missing.json"
                    ),
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "task-document-not-found")
        spawn.assert_not_called()

    def test_ambient_dispatch_refuses_role_altitude_mismatch_before_spawn(self) -> None:
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="worker",
                    brief="Work the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "seat-role-altitude-mismatch")
        spawn.assert_not_called()

    def test_plane_dispatch_keeps_structural_caller_provenance(self) -> None:
        self.catalog.upsert(_seat("architect", self.sprint, "architect", spawned_by_kind=None))
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "private-child"},
            ) as spawn,
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                return_value={
                    "ok": True,
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                },
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="orchestrator",
                    brief="Orchestrate the sprint.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "architect",
                        "AR_SPAWN_ROLE": "architect",
                    }
                ),
            )

        self.assertEqual(result["status"], "dispatched")
        spawned_by = spawn.call_args.kwargs["spawned_by"]
        self.assertEqual(spawned_by.caller_kind, "plane")
        self.assertEqual(spawned_by.session_id, "architect")

    def test_ambient_dispatch_persistence_failure_retires_the_unbriefed_child(self) -> None:
        child_id = "ambient-child-id"
        child_entry = _seat(child_id, self.sprint, "architect")
        self.catalog.upsert(child_entry)
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": child_id},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                side_effect=ValueError("store refused"),
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.retire_entry",
                return_value=child_entry,
            ) as retire,
            mock.patch(
                "agents_remember.application.structural.agent_tools.session_retire_tool"
            ) as session_retire,
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "dispatch-persistence-refused")
        self.assertNotIn(child_id, str(result))
        self.assertEqual(retire.call_args.args[2].id, child_id)
        self.assertEqual(retire.call_args.args[3].edge, "ambient-dispatch-rollback")
        session_retire.assert_not_called()

    def test_ambient_dispatch_persists_the_brief_without_a_plane_sender(self) -> None:
        self.catalog.upsert(_seat("ambient-architect", self.sprint, "architect"))
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "ambient-architect"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.post_operator_inbox_entry",
                return_value={
                    "ok": True,
                    "entryId": "initial-brief",
                    "deliveryState": "delivered",
                    "adapterDeliveryState": "accepted",
                },
            ) as post,
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "dispatched")
        poster = post.call_args.kwargs["poster"]
        self.assertIsNone(poster.sender_agent_id)
        self.assertIsNone(poster.sender_role)
        self.assertEqual(post.call_args.kwargs["address"].agent_id, "ambient-architect")
        self.assertEqual(
            self.catalog.get("ambient-architect").dispatch_brief_entry_id,  # type: ignore[union-attr]
            "initial-brief",
        )

    def test_resolve_ambient_caller_returns_none_when_plane_identity_is_present(self) -> None:
        caller = resolve_ambient_caller(environ={"AR_HOSTED_SESSION_ID": "seat-1"})
        self.assertIsNone(caller)

    def test_resolve_ambient_caller_returns_ambient_without_plane_identity(self) -> None:
        caller = resolve_ambient_caller(environ={})
        self.assertIsNotNone(caller)
        assert caller is not None
        self.assertEqual(caller.caller_kind, "ambient")
        self.assertIsInstance(caller, AmbientCaller)

    def test_role_without_hosted_identity_never_falls_back_to_ambient_dispatch(self) -> None:
        with (
            self.assertRaisesRegex(AmbientSeatError, "without the plane-injected"),
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
            ) as spawn,
        ):
            resolve_ambient_caller(environ={"AR_SPAWN_ROLE": "architect"})
        spawn.assert_not_called()

        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="orchestrator",
                    brief="Orchestrate the sprint.",
                ),
                StructuralAgentRuntime(environ={"AR_SPAWN_ROLE": "architect"}),
            )

        self.assertEqual(result["status"], "ambient-seat-incomplete")
        spawn.assert_not_called()

    def test_hosted_identity_without_process_role_refuses_before_spawn(self) -> None:
        self.catalog.upsert(_seat("architect", self.sprint, "architect", spawned_by_kind=None))
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="orchestrator",
                    brief="Orchestrate the sprint.",
                ),
                StructuralAgentRuntime(environ={"AR_HOSTED_SESSION_ID": "architect"}),
            )

        self.assertEqual(result["status"], "ambient-seat-incomplete")
        spawn.assert_not_called()

    def test_ambient_dispatch_runs_the_real_spawn_and_persists_the_brief(self) -> None:
        host = _FakeHost()
        _write_architect_settings(self.root)
        result = dispatch_agent_tool(
            self.config,
            DispatchAgentRequest(
                task_document_ref=self.sprint,
                role="architect",
                brief="Design the sprint.",
            ),
            StructuralAgentRuntime(
                host=host,  # type: ignore[arg-type]
                spawn_overrides=SpawnOverrides(host=host, which=_detected),  # type: ignore[arg-type]
                environ={},
            ),
        )

        self.assertEqual(result["status"], "dispatch-queued")
        self.assertEqual(result["taskDocumentRef"], self.sprint.model_dump())
        rows = self.catalog.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].spawned_by_kind, "ambient")
        self.assertEqual(rows[0].binding_role, "architect")
        inbox = next(iter(OperatorInboxStore(observer_root(self.config)).current().values()))
        self.assertEqual(inbox.messageKind, "dispatch-brief")
        self.assertIsNone(inbox.senderAgentId)

    def test_ambient_dispatch_rolls_back_via_system_closure_when_brief_persistence_fails(
        self,
    ) -> None:
        host = _FakeHost()
        _write_architect_settings(self.root)
        with mock.patch.object(
            OperatorInboxStore,
            "append",
            side_effect=OSError("append refused"),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(
                    host=host,  # type: ignore[arg-type]
                    spawn_overrides=SpawnOverrides(host=host, which=_detected),  # type: ignore[arg-type]
                    environ={},
                ),
            )

        self.assertEqual(result["status"], "dispatch-persistence-refused")
        self.assertIn("child retired", result["detail"])
        rows = self.catalog.list(include_terminated=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "terminated")
        self.assertEqual([entry for entry, _ in host.terminated], [rows[0].id])

    def test_ambient_dispatch_refuses_rollback_when_the_child_row_is_missing(self) -> None:
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "no-such-row"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                side_effect=ValueError("store refused"),
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "dispatch-reconciliation-refused")
        self.assertIn("durable brief state remains unknown", result["detail"])

    def test_ambient_dispatch_refuses_rollback_when_the_child_is_already_terminated(self) -> None:
        self.catalog.upsert(_seat("retired-child", self.sprint, "architect", status="terminated"))
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "retired-child"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                side_effect=ValueError("store refused"),
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "dispatch-reconciliation-refused")
        self.assertIn("durable brief state remains unknown", result["detail"])

    def test_ambient_dispatch_rollback_reports_when_retirement_raises(self) -> None:
        child_entry = _seat("ambient-child", self.sprint, "architect")
        self.catalog.upsert(child_entry)
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "ambient-child"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                side_effect=ValueError("store refused"),
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.retire_entry",
                side_effect=OSError("kill red"),
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "dispatch-persistence-refused")
        self.assertIn("child retirement also failed", result["detail"])

    def test_ambient_dispatch_rollback_reports_when_retirement_races(self) -> None:
        child_entry = _seat("ambient-child", self.sprint, "architect")
        self.catalog.upsert(child_entry)
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "ambient-child"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                side_effect=ValueError("store refused"),
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.retire_entry",
                return_value=None,
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "dispatch-persistence-refused")
        self.assertIn("child retirement also failed", result["detail"])

    def test_ambient_dispatch_rollback_preserves_an_observer_log_failure_as_secondary(self) -> None:
        child_entry = _seat("ambient-child", self.sprint, "architect")
        self.catalog.upsert(child_entry)
        retired = child_entry.with_retirement(
            at="2026-08-25T00:00:00+00:00",
            by_session=None,
            reason="initial dispatch brief persistence failed",
            edge="ambient-dispatch-rollback",
        )
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "ambient-child"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_initial_dispatch_brief",
                side_effect=ValueError("store refused"),
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.retire_entry",
                return_value=retired,
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.log_retire_event",
                side_effect=OSError("observer unavailable"),
            ),
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(environ={}),
            )

        self.assertEqual(result["status"], "dispatch-persistence-refused")
        self.assertIn("child retired; retirement event logging failed", result["detail"])

    def test_plane_dispatch_persistence_failure_retires_the_unbriefed_child_privately(
        self,
    ) -> None:
        self.catalog.upsert(_seat("architect", self.sprint, "architect", spawned_by_kind="plane"))
        self.catalog.upsert(
            _seat(
                "private-child-id",
                self.sprint,
                "orchestrator",
                spawned_by_kind="plane",
            )
        )
        retired = _seat(
            "private-child-id",
            self.sprint,
            "orchestrator",
            status="terminated",
            spawned_by_kind="plane",
        )
        with (
            mock.patch(
                "agents_remember.application.structural.agent_tools.spawn_agent_session_tool",
                return_value={"status": "spawned-unbriefed", "session": "private-child-id"},
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools._post_structural_message",
                side_effect=ValueError("store refused"),
            ),
            mock.patch(
                "agents_remember.application.structural.agent_tools.retire_entry",
                return_value=retired,
            ) as retire,
        ):
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="orchestrator",
                    brief="Coordinate the sprint.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "architect",
                        "AR_SPAWN_ROLE": "architect",
                    }
                ),
            )

        self.assertEqual(result["status"], "dispatch-persistence-refused")
        self.assertNotIn("private-child-id", str(result))
        self.assertEqual(retire.call_args.args[2].id, "private-child-id")
        closure = retire.call_args.args[3]
        self.assertEqual(closure.by_session, "architect")
        self.assertEqual(closure.edge, "dispatch-rollback")

    def test_plane_dispatch_refuses_broken_plane_identity_without_downgrading(self) -> None:
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="architect",
                    brief="Design the sprint.",
                ),
                StructuralAgentRuntime(
                    environ={"AR_HOSTED_SESSION_ID": "ghost", "AR_SPAWN_ROLE": "architect"}
                ),
            )

        self.assertEqual(result["status"], "ambient-seat-stale")
        spawn.assert_not_called()

    def test_plane_dispatch_refuses_an_unauthorized_child_role(self) -> None:
        self.catalog.upsert(_seat("architect", self.sprint, "architect", spawned_by_kind="plane"))
        with mock.patch(
            "agents_remember.application.structural.agent_tools.spawn_agent_session_tool"
        ) as spawn:
            result = dispatch_agent_tool(
                self.config,
                DispatchAgentRequest(
                    task_document_ref=self.sprint,
                    role="system-specialist",
                    brief="Investigate the sprint.",
                ),
                StructuralAgentRuntime(
                    environ={
                        "AR_HOSTED_SESSION_ID": "architect",
                        "AR_SPAWN_ROLE": "architect",
                    }
                ),
            )

        self.assertEqual(result["status"], "structural-child-refused")
        spawn.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
