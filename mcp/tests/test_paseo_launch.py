from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.role_launch_context import RoleLaunchContext, selection_binding
from agents_remember.cli import (
    paseo_catalog,
    paseo_launch,
    paseo_status,
    role_launch_preparation,
    role_launch_receipts,
    role_launch_routes,
    role_launch_workspace,
)
from agents_remember.cli.paseo_launch import (
    RECOVERY_NOTE_LIMIT,
    RoleLaunch,
    build_launch_call,
)
from agents_remember.cli.role_launch_preparation import RoleHandoverRequest
from agents_remember.cli.role_launch_receipts import _message_binding_projection_reference, digest
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.role_launcher import (
    RoleDispatchRequest,
    RoleResultRequest,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document import TaskEnclosureRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.role_handover_locator import locator_directory
from fastapi import HTTPException
from paseo_launch_test_support import (
    CATALOG,
    SERVER_ID,
    FakeRuntime,
    GivenToAgentExpectations,
    prepare_host,
)

# Re-exported for the modules built on this fixture: the fake runtime's catalog and the
# server id its answers name.
__all__ = ["CATALOG", "SERVER_ID"]

REPO = "agents-remember"
SPRINT_REF = TaskDocumentRef(repository=REPO, path="sprint/task.json")
MASTER_REF = TaskDocumentRef(repository=REPO, path="master/task.json")
LEAF_REF = TaskDocumentRef(repository=REPO, path="master/01_leaf.json")
ROLE_REFS: dict[str, dict[str, TaskDocumentRef]] = {
    "architect": {},
    "investigator": {},
    "orchestrator": {"sprintDocumentRef": SPRINT_REF},
    "manager": {"sprintDocumentRef": SPRINT_REF, "masterDocumentRef": MASTER_REF},
    **{
        role: {
            "sprintDocumentRef": SPRINT_REF,
            "masterDocumentRef": MASTER_REF,
            "taskDocumentRef": LEAF_REF,
        }
        for role in ("worker", "reviewer", "curator")
    },
}
ROLE_DEFAULTS = ({"agent": "codex", "model": "gpt-a", "effort": "low"}, ("codex", "eve"))


def test_role_tool_definition_carries_product_xdg_homes_without_starting(tmp_path, monkeypatch):
    expected = {
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    binding = AgentBinding("agent", "worker", "request", str(tmp_path / "report.md"))
    before = dict(os.environ)
    parent_node = product_node()
    with patch("subprocess.run") as run:
        definition = paseo_launch.tool_server_definition(tmp_path / "settings.json", binding)
        assert {name: definition["env"][name] for name in expected} == expected
        assert dict(os.environ) == before
        with patch.dict(os.environ, definition["env"], clear=True):
            child_node = product_node()
        assert (child_node.root, child_node.cache) == (parent_node.root, parent_node.cache)
        monkeypatch.delenv("XDG_DATA_HOME")
        monkeypatch.setenv("XDG_STATE_HOME", "")
        definition = paseo_launch.tool_server_definition(tmp_path / "settings.json", binding)
        assert "XDG_DATA_HOME" not in definition["env"]
        assert "XDG_STATE_HOME" not in definition["env"]
        assert definition["env"]["XDG_CACHE_HOME"] == expected["XDG_CACHE_HOME"]
        run.assert_not_called()


def test_role_tool_definition_carries_the_place_of_the_user_settings(tmp_path, monkeypatch):
    """An agent's tool server reads the settings file its launching process read.

    A harness need not pass its environment on (Codex gives a tool server six names, an ACP
    harness what the request holds). Without the name in the definition the tool server falls
    back to the home directory's file: other roles and another host block than the dashboard's.
    """

    binding = AgentBinding("agent", "worker", "request", str(tmp_path / "report.md"))
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    before = dict(os.environ)
    definition = paseo_launch.tool_server_definition(tmp_path / "settings.json", binding)
    assert definition["env"]["XDG_CONFIG_HOME"] == str(config_home)
    assert dict(os.environ) == before

    monkeypatch.delenv("XDG_CONFIG_HOME")
    definition = paseo_launch.tool_server_definition(tmp_path / "settings.json", binding)
    assert "XDG_CONFIG_HOME" not in definition["env"]
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    definition = paseo_launch.tool_server_definition(tmp_path / "settings.json", binding)
    assert "XDG_CONFIG_HOME" not in definition["env"]


# What an agent reports when a refresh closes its execution with the given status (PNT-R07).
CLOSING_STATES: dict[str, dict[str, Any]] = {
    "completed": {"status": "idle", "lastTurn": {"state": "replied", "text": "Done."}},
    "failed": {"status": "error", "lastError": "The model refused the turn."},
    "stopped": {"status": "idle", "lastTurn": {"state": "unreplied"}},
}
NO_ANSWER_CODES = (
    "paseo_bridge_timeout",
    "paseo_daemon_unreachable",
    "paseo_bridge_invalid_reply",
    "paseo_bridge_unavailable",
    "paseo_agent_lookup_failed",
    # The agent exists and did not take its first message; the repeat of the call sends it.
    "paseo_first_message_undelivered",
)


class FakeEnclosures:
    """Stands in for AR's worktree status and start calls of one leaf."""

    def __init__(self, root: Path) -> None:
        self.group = root / "enclosure-group"
        self.started = False
        self.start_calls: list[Any] = []
        self.start_result: dict[str, Any] = {"ok": True}
        # Called when a start succeeds: the real start records the enclosure in the leaf document.
        self.on_start: Any = None

    def status(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if not self.started:
            return {"ok": False, "detail": "no enclosure"}
        return {
            "ok": True,
            "worktree_group": self.group.as_posix(),
            "code_worktree": (self.group / "code").as_posix(),
            "memory_worktree": (self.group / "memory").as_posix(),
        }

    def start(self, _config: Any, identity: Any, **_kwargs: Any) -> dict[str, Any]:
        self.start_calls.append(identity)
        if self.start_result.get("ok") is True:
            for path in (self.group / "code", self.group / "memory"):
                path.mkdir(parents=True)
            self.started = True
            if self.on_start is not None:
                self.on_start()
        return self.start_result


def runtime_config(root: Path, *, configured: bool = True) -> McpRuntimeConfig:
    prepare_host(root, root / "settings" / "mcp.json", configured=configured)
    return McpRuntimeConfig(
        config_path=root / "settings" / "mcp.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
        repositories={
            REPO: RepositoryScope(
                repo_id=REPO, path=root / "projects" / REPO, memory_root=root / "memory" / REPO
            )
        },
    )


def resolved_document(
    config: McpRuntimeConfig, ref: TaskDocumentRef, document_id: str
) -> ResolvedTaskDocument:
    kind = "subTask" if ref == LEAF_REF else "master"
    document = TaskDocument.model_validate(
        {
            "id": document_id,
            "slug": Path(ref.path).stem,
            "title": document_id,
            "kind": kind,
            "repo": REPO,
            "createdAt": "2026-10-02T00:00:00+00:00",
            **({"status": "inProgress", "master": "task.json"} if kind == "subTask" else {}),
        }
    )
    path = config.coordination_root / "tasks" / REPO / ref.path
    write_task_doc(path.parent, document)
    return ResolvedTaskDocument(ref=ref, path=path, document=document)


class PaseoLaunchTestCase(GivenToAgentExpectations):
    """The dispatch route against a fake bridge; capsule compilation is stubbed, the rest is real."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config = runtime_config(self.root)
        self.config.coordination_root.mkdir(exist_ok=True)
        self.runtime = FakeRuntime()
        self.enclosures = FakeEnclosures(self.root)
        self.prompt = "Compiled capsule and handover."
        self.sprint = resolved_document(self.config, SPRINT_REF, "SPRINT")
        self.master = resolved_document(self.config, MASTER_REF, "MASTER")
        self.leaf = resolved_document(self.config, LEAF_REF, "01_LEAF")
        self.enclosures.on_start = self.record_enclosure_in_leaf
        self.resolve_context = self.replace(
            role_launch_routes, "resolve_role_launch_context", side_effect=self.context
        )
        self.replace(
            role_launch_preparation, "resolve_role_launch_context", side_effect=self.context
        )
        self.replace(paseo_catalog, "bridge_call", self.runtime)
        self.replace(paseo_launch, "bridge_call", self.runtime)
        self.replace(paseo_status, "bridge_call", self.runtime)
        self.replace(role_launch_workspace, "worktree_status_tool", self.enclosures.status)
        self.replace(role_launch_workspace, "_start_leaf_enclosure", self.enclosures.start)
        self.replace(role_launch_preparation, "_compile_handover", self.compile_handover)
        self.replace(
            role_launch_preparation, "_ar_mcp_context", lambda *_args: {"scopeKind": "test"}
        )
        self.replace(role_launch_preparation, "_role_defaults", return_value=ROLE_DEFAULTS)
        # The source tree of the launching build, as a checkout outside the interpreter has it.
        self.source = self.root / "build" / "src" / "agents_remember"
        self.replace(paseo_launch, "launching_source_root", return_value=self.source)
        # A launch writes no harness configuration; the user's home is watched for one as well.
        # The launching process has both variables that keep a process from writing into the
        # checkout it runs from. The home is a short one, as a real account's is, because the
        # product keeps each launch's hand-over locator under it (MIK-R208): the report folder
        # may be as deep as the checkout, and the locator must not grow with it.
        self.home = Path(tempfile.mkdtemp(prefix="arh-", dir="/tmp")).resolve()
        self.addCleanup(shutil.rmtree, self.home, True)
        home = patch.dict(
            os.environ,
            {
                "HOME": self.home.as_posix(),
                "GIT_OPTIONAL_LOCKS": "0",
                "PYTHONPYCACHEPREFIX": (self.root / "pycache").as_posix(),
            },
        )
        home.start()
        self.addCleanup(home.stop)
        for absent in ("TMUX_TMPDIR", "AR_DAGGER_AUTHORITY_ROOT"):
            os.environ.pop(absent, None)

    def replace(self, target: Any, name: str, *replacement: Any, **mock: Any) -> Any:
        patcher = patch.object(target, name, *replacement, **mock)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def context(self, _config: McpRuntimeConfig, selection: Any) -> RoleLaunchContext:
        refs = ROLE_REFS[selection.role]
        sprint = self.sprint if "sprintDocumentRef" in refs else None
        master = self.master if "masterDocumentRef" in refs else None
        task = self.leaf if "taskDocumentRef" in refs else None
        return RoleLaunchContext(selection.role, sprint, master, task, task or master or sprint)

    def record_enclosure_in_leaf(self) -> None:
        """What AR's worktree start does to the leaf's task document."""

        contract = leaf_enclosure_path(self.leaf.path.parent, self.leaf.document.id)
        enclosure = TaskEnclosureRef(
            leafId=self.leaf.document.id, enclosurePath=contract.as_posix()
        )
        self.leaf = ResolvedTaskDocument(
            ref=self.leaf.ref,
            path=self.leaf.path,
            document=self.leaf.document.model_copy(update={"enclosures": [enclosure]}),
        )
        write_task_doc(self.leaf.path.parent, self.leaf.document)

    def compile_handover(self, request: RoleHandoverRequest) -> dict[str, Any]:
        assert request.request_id is not None
        report = (self.root / "reports" / f"{request.request_id}.md").as_posix()
        context = request.context
        documents = digest(
            [
                resolved.document.model_dump(mode="json", by_alias=True, exclude_none=True)
                for resolved in (context.sprint, context.master, context.task)
                if resolved is not None
            ]
        )
        # As the preparation writes it: the binding records the selection it was made for.
        binding = {
            "requestId": str(request.request_id),
            "role": context.role,
            "selection": selection_binding(context),
            "taskDocumentDigest": documents,
        }
        return {
            "prompt": self.prompt,
            "projection": {"schema": "ar-role-handover/v1"},
            "handoverFile": self.planned_handover(request)[0],
            "handoverFileContent": self.planned_handover(request)[1],
            "capsuleOperation": "planning",
            "capsuleDigest": "capsule-digest",
            "taskDocumentDigest": documents,
            "taskReportPath": report,
            "canonicalTaskReportPath": report,
            "arMcpContext": request.ar_mcp_context,
            "messageBindingProjection": {
                "requestId": str(request.request_id),
                "binding": binding,
                **_message_binding_projection_reference(self.config, request.request_id, binding),
            },
        }

    def request(self, role: str, request_id: uuid.UUID | None = None, **override: str) -> Any:
        return RoleDispatchRequest.model_validate(
            {
                "role": role,
                "requestId": request_id or uuid.uuid4(),
                **ROLE_REFS[role],
                **({"agentOverride": override} if override else {}),
            }
        )

    def dispatch(
        self, request: RoleDispatchRequest, config: McpRuntimeConfig | None = None
    ) -> tuple[int, dict[str, Any]]:
        response = role_launch_routes._role_launch_dispatch_endpoint(config or self.config, request)
        return response.status_code, json.loads(bytes(response.body))

    def refused(self, request: RoleDispatchRequest, **kwargs: Any) -> HTTPException:
        with self.assertRaises(HTTPException) as raised:
            self.dispatch(request, **kwargs)
        return raised.exception

    def receipt_path(self, request: RoleDispatchRequest) -> Path:
        taskless = not ROLE_REFS[request.role]
        return role_launch_receipts._receipt_path(
            self.config, request, request.request_id if taskless else None
        )

    def receipt(self, request: RoleDispatchRequest) -> dict[str, Any]:
        return json.loads(self.receipt_path(request).read_text(encoding="utf-8"))

    def agent_of(self, request: RoleDispatchRequest) -> dict[str, Any]:
        """The fake runtime's record of the agent this execution launched."""

        return self.runtime.agents[self.receipt(request)["execution"]["agentId"]]

    def refresh(
        self, request: RoleDispatchRequest, config: McpRuntimeConfig | None = None
    ) -> dict[str, Any]:
        """Press Result: the result route for the execution of this request."""

        response = role_launch_routes._role_launch_result_endpoint(
            config or self.config,
            RoleResultRequest.model_validate(
                {
                    "role": request.role,
                    "requestId": request.request_id,
                    **ROLE_REFS[request.role],
                }
            ),
        )
        return json.loads(bytes(response.body))

    def close_execution(self, request: RoleDispatchRequest, status: str) -> dict[str, Any]:
        """Let the agent end its turn so that a refresh closes the execution with this status."""

        self.agent_of(request).update(CLOSING_STATES[status])
        self.assertEqual(self.refresh(request)["status"], status)
        return self.receipt(request)

    def receipt_files(self) -> list[str]:
        return sorted(
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*.json")
            if "-native-executions" in path.as_posix() and "message-bindings" not in path.parts
        )


class LaunchRefusalTests(PaseoLaunchTestCase):
    def test_a_launch_that_cannot_start_refuses_before_any_runtime_call_or_receipt(self) -> None:
        with self.subTest("no Paseo runtime configured"):
            error = self.refused(
                self.request("architect"),
                config=runtime_config(self.root / "unconfigured", configured=False),
            )
            self.assertEqual(error.status_code, 409)
            self.assertTrue(str(error.detail).startswith("no Paseo runtime configured: "))
            self.assertEqual(self.runtime.calls, [])
            self.resolve_context.assert_not_called()
        with self.subTest("the selection fails validation"):
            error = self.refused(self.request("worker", agentId="codex", modelId="gpt-z"))
            self.assertEqual(error.status_code, 409)
            self.assertIn("does not offer model 'gpt-z'", str(error.detail))
            self.assertEqual(self.enclosures.start_calls, [])
        with self.subTest("the leaf enclosure cannot be created"):
            self.enclosures.start_result = {"ok": False, "summary": "base branch is missing"}
            error = self.refused(self.request("worker"))
            self.assertEqual(error.status_code, 409)
            self.assertIn("base branch is missing", str(error.detail))
            self.assertIn("Next:", str(error.detail))
            self.assertEqual(len(self.enclosures.start_calls), 1)
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(list(self.root.rglob("*-native-executions")), [])

    def test_an_unanswered_catalog_passes_configured_choices_to_the_host(self) -> None:
        self.runtime.fail("catalog", "paseo_daemon_unreachable", "The Paseo daemon is down.")
        request = self.request("worker")
        status, body = self.dispatch(request)
        self.assertEqual((status, body["status"]), (200, "running"))
        self.assertEqual(
            [name for name, _ in self.runtime.calls],
            ["catalog", "catalog", "workspace-open", "agent-create"],
        )
        self.assertEqual(self.runtime.calls[0][1]["mode"], "metadata")
        self.assertEqual(self.runtime.calls[1][1]["mode"], "selected")
        self.assertEqual(self.runtime.calls[1][1]["selectedModel"], "gpt-a")
        created = self.runtime.launch_calls()[-1][1]
        self.assertEqual(
            (created["provider"], created["model"], created["thinkingOptionId"]),
            ("codex", "gpt-a", "low"),
        )
        self.assertEqual(len(self.enclosures.start_calls), 1)
        self.assertEqual(self.receipt(request)["status"], "running")


class LaunchFeatureValuesTests(unittest.TestCase):
    """MIK-R112 CUR-C03: accepted featureValues and the legacy tier knob merge, never clobber."""

    def launch(self, session_options: dict[str, Any]) -> RoleLaunch:
        context = RoleLaunchContext("architect", None, None, None, None)
        return RoleLaunch(
            agent_id="agent-1",
            request_id=uuid.uuid4(),
            context=context,
            folder="/tmp",
            provider="codex",
            session_options=session_options,
            prompt="prompt",
            report_path="/tmp/report.md",
            handover_artifact={"path": "/tmp/report.md", "sha256": "0" * 64},
            settings_file=Path("/tmp/settings.json"),
            accepts_tool_servers=False,
        )

    def test_service_tier_merges_into_accepted_feature_values(self) -> None:
        call = build_launch_call(
            self.launch({"featureValues": {"a": "1", "service_tier": "old"}, "serviceTier": "new"})
        )
        self.assertEqual(call["agent"]["featureValues"], {"a": "1", "service_tier": "new"})

    def test_feature_values_pass_through_without_tier(self) -> None:
        call = build_launch_call(self.launch({"featureValues": {"a": "1"}}))
        self.assertEqual(call["agent"]["featureValues"], {"a": "1"})

    def test_tier_alone_sets_only_its_key(self) -> None:
        call = build_launch_call(self.launch({"serviceTier": "new"}))
        self.assertEqual(call["agent"]["featureValues"], {"service_tier": "new"})


class DeepLaunchFolderTests(PaseoLaunchTestCase):
    """MIK-R208: a launch's recovery note fits however deep its report folder is."""

    def compile_at(self, *parts: str) -> None:
        """Compile as the real compilation does, for a report folder of the given parts."""

        def compiled(handover: RoleHandoverRequest) -> dict[str, Any]:
            report = self.root.joinpath(*parts) / f"{handover.request_id}.md"
            return {
                **self.compile_handover(handover),
                "taskReportPath": report.as_posix(),
                "canonicalTaskReportPath": report.as_posix(),
            }

        self.replace(role_launch_preparation, "_compile_handover", compiled)

    def test_the_note_and_the_path_it_names_tell_the_same_at_any_depth(self) -> None:
        # One launch per role: a second start of the same seat is refused while the first is not
        # archived, which this case is not about.
        cases = (
            ("beside the checkout", "reviewer", ("reports",)),
            (
                "deeper than the note's whole budget",
                "worker",
                ("reports", *(("d" * 45 + str(number)) for number in range(6))),
            ),
        )
        lengths: list[int] = []
        named: list[str] = []
        beside: list[int] = []
        for label, role, parts in cases:
            with self.subTest(label):
                self.compile_at(*parts)
                request = self.request(role)
                self.assertEqual(self.dispatch(request)[1]["status"], "running")
                receipt = self.receipt(request)
                artifact = receipt["handoverArtifact"]
                stored = Path(artifact["canonicalPath"])
                report = Path(receipt["report"]["path"])
                # The artifact keeps its own name and bytes beside the report, write-once.
                self.assertEqual(stored.parent, report.parent)
                self.assertEqual(stored.name, f"{report.stem}.handover.txt")
                self.assertEqual(stored.read_text(encoding="utf-8"), self.prompt)
                self.assertEqual(stat.S_IMODE(stored.stat().st_mode), 0o444)
                # The path the agent is given is the request's own locator, whatever the depth.
                self.assertEqual(
                    Path(artifact["path"]).parent, locator_directory(str(request.request_id))
                )
                self.assertEqual(Path(artifact["path"]).read_text(encoding="utf-8"), self.prompt)
                note = self.runtime.launch_calls()[-1][1]["systemPrompt"]
                self.assertLessEqual(len(note), RECOVERY_NOTE_LIMIT, note)
                self.assertIn(
                    f"Assignment file: {artifact['path']} (SHA-256 {artifact['sha256']}).", note
                )
                first_line = self.runtime.launch_calls()[-1][1]["prompt"].splitlines()[0]
                self.assertIn(f"{artifact['path']} (SHA-256 {artifact['sha256']})", first_line)
                self.assertIn(request.role, note)
                # No part of the folder the report lives in is in the note at either depth.
                self.assertNotIn(self.root.joinpath(*parts).as_posix(), note)
                lengths.append(len(note) - len(str(request.role)))
                named.append(artifact["path"])
                beside.append(len(stored.as_posix()))
        # The deep case is the requirement's: the artifact beside its report is longer than 318
        # characters, and the note is not one character longer than at the shallow one.
        self.assertLess(beside[0], 318)
        self.assertGreaterEqual(beside[1], 318)
        self.assertEqual(
            lengths[0], lengths[1], "the note is one length at either depth, the role aside"
        )
        self.assertEqual(len(named[0]), len(named[1]))

    def test_a_locator_that_would_lead_elsewhere_refuses_the_launch_before_it_starts(self) -> None:
        # A name this product did not make for this folder: a collision or a retarget.
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        request = self.request("worker")
        locator = locator_directory(str(request.request_id))
        locator.parent.mkdir(parents=True, exist_ok=True)
        locator.symlink_to(elsewhere, target_is_directory=True)

        error = self.refused(request)

        self.assertEqual(error.status_code, 409)
        self.assertIn("does not lead to this request's report folder", str(error.detail))
        # Nothing started, no receipt, no file written anywhere, and the alias is as it stood.
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertFalse(self.receipt_path(request).exists())
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(locator.resolve(), elsewhere.resolve())


if __name__ == "__main__":
    unittest.main()
