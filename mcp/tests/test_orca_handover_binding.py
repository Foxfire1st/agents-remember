from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from agents_remember.application.orca_task_context import OrcaRoleContext, resolve_orca_role_context
from agents_remember.application.skill_resources import CapsuleCompileRequest, compile_task_capsule
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocCall,
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_scoped_mcp import task_scoped_mcp_config_for_task
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.cli import (
    orca_task_preparation,
    orca_task_receipts,
    orca_task_routes,
    paseo_catalog,
    paseo_launch,
)
from agents_remember.cli.orca_runtime import digest
from agents_remember.cli.orca_task_preparation import (
    ROLE_START_OPERATIONS,
    OrcaHandoverRequest,
    _bind_task_report_access,
    _compile_handover,
    _ensure_leaf_enclosure,
    _resolve_workspace,
    _role_report_path,
    prepare_orca_role_handover,
    role_start_operation,
)
from agents_remember.cli.orca_task_receipts import _message_binding_projection_reference
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import forget_launcher_catalogs
from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.primitives import checkout_coordination
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.orca_launcher import OrcaDispatchRequest, OrcaSelection
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document import TaskEnclosureRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.task_paths import leaf_enclosure_path
from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
from test_worktree_support import open_external_contract_fixture


class OrcaScopedCapsuleBindingTests(unittest.TestCase):
    def test_leaf_roles_compile_contract_repo_identity_with_scoped_context_and_reports(  # noqa: PLR0915
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(checkout_coordination._declared, {"mode": "test"}):
                contract = open_external_contract_fixture(root)
            assert contract.memory_worktree is not None
            repo_id = contract.repo_name
            leaf_slug = contract.leaf_id.lower().replace("_", "-")
            sprint_root = contract.coordination_root / "tasks" / repo_id / "sprint"
            write_task_doc(
                sprint_root,
                TaskDocument.model_validate(
                    {
                        "id": "SPRINT",
                        "slug": "sprint",
                        "title": "Binding proof sprint",
                        "kind": "master",
                        "repo": repo_id,
                        "createdAt": "2026-09-24T10:00:00+00:00",
                        "orchestrates": [contract.task_root.name],
                    }
                ),
            )
            write_task_doc(
                contract.task_root,
                TaskDocument.model_validate(
                    {
                        "id": contract.task_id,
                        "slug": "task",
                        "title": contract.task_name,
                        "kind": "master",
                        "repo": repo_id,
                        "createdAt": "2026-09-24T10:01:00+00:00",
                        "subTasks": [
                            {
                                "number": contract.leaf_id,
                                "name": "Scoped capsule binding",
                                "file": f"{leaf_slug}.md",
                                "status": "inProgress",
                            }
                        ],
                    }
                ),
            )
            write_task_doc(
                contract.task_root,
                TaskDocument.model_validate(
                    {
                        "id": contract.leaf_id,
                        "slug": leaf_slug,
                        "title": "Scoped capsule binding",
                        "kind": "subTask",
                        "status": "inProgress",
                        "repo": repo_id,
                        "createdAt": "2026-09-24T10:02:00+00:00",
                        "master": "task.md",
                        "enclosures": [
                            {
                                "leafId": contract.leaf_id,
                                "enclosurePath": contract.contract_path.as_posix(),
                            }
                        ],
                    }
                ),
            )

            settings_path = root / "settings" / "ar.json"
            settings_path.parent.mkdir(parents=True)
            settings_path.write_text(
                json.dumps(
                    {
                        "coordinationRoot": contract.coordination_root.as_posix(),
                        "workspaceRoot": root.as_posix(),
                        "repositories": {repo_id: {}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            task_ref = TaskDocumentRef(
                repository=repo_id,
                path=f"{contract.task_root.name}/{leaf_slug}.json",
            )
            sprint_ref = TaskDocumentRef(repository=repo_id, path="sprint/task.json")
            master_ref = TaskDocumentRef(
                repository=repo_id,
                path=f"{contract.task_root.name}/task.json",
            )

            with patch.dict(checkout_coordination._declared, {"mode": "test"}):
                base_config = load_config(settings_path)
                scoped_config = task_scoped_mcp_config_for_task(
                    base_config,
                    task_ref,
                    contract.contract_path,
                )

            self.assertNotEqual(contract.code_worktree.name, repo_id)
            self.assertEqual(scoped_config.repositories[repo_id].path, contract.code_worktree)
            self.assertEqual(
                scoped_config.repositories[repo_id].memory_root, contract.memory_worktree
            )
            workspace_path = contract.worktree_group
            task_reports = contract.task_root / "notes" / "reports"
            task_reports.mkdir(parents=True, exist_ok=True)
            workspace = {
                "path": workspace_path.as_posix(),
                "contractPath": contract.contract_path.as_posix(),
                "codeRoot": contract.code_worktree.as_posix(),
                "memoryRoot": contract.memory_worktree.as_posix(),
                "taskReportRoot": task_reports.resolve().as_posix(),
                "taskReportAccessRoot": _bind_task_report_access(
                    workspace_path, task_reports
                ).as_posix(),
            }
            for role in ("worker", "reviewer", "curator"):
                with self.subTest(role=role):
                    context = resolve_orca_role_context(
                        base_config,
                        OrcaSelection(
                            role=role,
                            sprintDocumentRef=sprint_ref,
                            masterDocumentRef=master_ref,
                            taskDocumentRef=task_ref,
                        ),
                    )
                    request_id = uuid.uuid4()
                    ar_mcp_context = orca_task_preparation._ar_mcp_context(
                        base_config,
                        context,
                        workspace,
                    )
                    bind_worktree_services(build_default_worktree_services())
                    try:
                        prepared = _compile_handover(
                            OrcaHandoverRequest(
                                config=base_config,
                                context=context,
                                workspace=workspace,
                                agent_id="claude",
                                ar_mcp_context=ar_mcp_context,
                                request_id=request_id,
                            )
                        )
                    finally:
                        reset_worktree_services()
                    handover = json.loads(
                        prepared["prompt"].rsplit(
                            "\n\nAR owner assignment and canonical task handover:\n", 1
                        )[1]
                    )
                    binding = handover["capsule"]["binding"]
                    assert context.task is not None
                    canonical_report = Path(prepared["canonicalTaskReportPath"])
                    documents = handover["documents"]
                    self.assertEqual(handover["requestId"], str(request_id))
                    self.assertEqual(handover["taskDocumentDigest"], prepared["taskDocumentDigest"])
                    self.assertEqual(handover["taskDocumentDigest"], digest(documents))
                    message_binding = handover["nativeOrca"]["messageBinding"]
                    self.assertEqual(
                        message_binding["payloadType"],
                        "The compact AR binding is a canonical JSON object; each native verb uses its own documented string field.",
                    )
                    self.assertEqual(
                        message_binding["arBinding"],
                        {
                            "requestId": str(request_id),
                            "role": role,
                            "operation": role_start_operation(role),
                            "selection": handover["selection"],
                            "taskDocumentDigest": digest(documents),
                            "taskReportPath": prepared["canonicalTaskReportPath"],
                            "capsuleDigest": handover["capsule"]["semanticDigest"],
                        },
                    )
                    projection_reference = _message_binding_projection_reference(
                        scoped_config, request_id, message_binding["arBinding"]
                    )
                    self.assertEqual(message_binding["projection"], projection_reference)
                    self.assertTrue(Path(message_binding["projection"]["path"]).is_absolute())
                    self.assertEqual(
                        prepared["messageBindingProjection"],
                        {
                            "requestId": str(request_id),
                            "binding": message_binding["arBinding"],
                            **projection_reference,
                        },
                    )
                    for native_id in ("runId", "taskId", "dispatchId"):
                        self.assertNotIn(native_id, message_binding["arBinding"])
                    self.assertIn("only when", message_binding["nativeIdsRule"])
                    message_semantics = handover["nativeOrca"]["messageSemantics"]
                    self.assertIn("queued, not read", message_semantics)
                    self.assertIn("--retry-request <original-request-uuid>", message_semantics)
                    self.assertIn("messageBinding.projection.path", message_semantics)
                    self.assertIn("JSON text for `--payload`", message_semantics)
                    self.assertIn("neither ask nor reply", message_semantics)
                    self.assertIn("only inside an active supervised Dispatch", message_semantics)
                    self.assertIn("manual session without a Dispatch", message_semantics)
                    self.assertIn("`--type question`", message_semantics)
                    self.assertIn(
                        "explicit `run:<id>` or `dispatch:<id>` recipient", message_semantics
                    )
                    self.assertIn("Do not invent a Dispatch or sender identity", message_semantics)
                    self.assertIn("mutually exclusive", message_semantics)
                    self.assertIn("Save native `--json` stdout byte-for-byte", message_semantics)
                    self.assertIn("structurally compare it", message_semantics)
                    self.assertIn("that sender's expected binding", message_semantics)
                    self.assertIn("mark it unverified", message_semantics)
                    self.assertIn("string spec passed to Orca `task-create`", message_semantics)
                    self.assertIn("run:<id>", message_semantics)
                    self.assertIn("recipient workspace", message_semantics)
                    self.assertIn("workspace/project listing", message_semantics)
                    self.assertEqual(binding["repositoryId"], repo_id)
                    self.assertEqual(
                        canonical_report.name,
                        f"{context.task.document.id}-{role}-{request_id}.md",
                    )
                    self.assertEqual(prepared["capsuleOperation"], role_start_operation(role))
                    self.assertEqual(handover["operation"], role_start_operation(role))
                    self.assertEqual(binding["operation"], role_start_operation(role))
                    self.assertEqual(binding["taskPath"], task_ref.key)
                    self.assertEqual(binding["role"], role)
                    self.assertEqual(
                        handover["workspace"]["codeRoot"], contract.code_worktree.as_posix()
                    )
                    self.assertEqual(
                        handover["workspace"]["memoryRoot"], contract.memory_worktree.as_posix()
                    )
                    self.assertEqual(
                        handover["contextPacket"]["repo"]["root"], contract.code_worktree.as_posix()
                    )
                    self.assertEqual(
                        handover["contextPacket"]["paths"]["memoryRoot"],
                        contract.memory_worktree.as_posix(),
                    )
                    self.assertEqual(
                        handover["workspace"]["contractPath"], contract.contract_path.as_posix()
                    )
                    reader_context = {
                        "task_document_ref": task_ref.model_dump(mode="json"),
                        "contract_path": contract.contract_path.resolve().as_posix(),
                    }
                    self.assertEqual(
                        {
                            key: handover["arMcpContext"][key]
                            for key in (
                                "schema",
                                "scopeKind",
                                "repositoryId",
                                "taskContext",
                                "readerArguments",
                                "requiredArguments",
                                "requiredCapability",
                            )
                        },
                        {
                            "schema": "ar-mcp-reader-context/v1",
                            "scopeKind": "canonical-leaf",
                            "repositoryId": repo_id,
                            "taskContext": reader_context,
                            "readerArguments": {
                                "context_packet": {
                                    "repo_id": repo_id,
                                    "task_context": reader_context,
                                    "include_providers": False,
                                },
                                "read_ar_files": {
                                    "repo_id": repo_id,
                                    "task_context": reader_context,
                                },
                            },
                            "requiredArguments": {
                                "context_packet": ["repo_id", "task_context"],
                                "read_ar_files": ["repo_id", "files", "task_context"],
                            },
                            "requiredCapability": "ar-task-scoped-readers/v1",
                        },
                    )
                    self.assertIn("task_context", handover["nativeOrca"]["arMcpUsage"])
                    self.assertNotIn("nativeMcpScope", handover)
                    self.assertEqual(
                        handover["nativeOrca"]["guidesOnDemand"],
                        ["orca skills get orca-cli", "orca skills get orchestration"],
                    )
                    self.assertIn("ORCA_TERMINAL_HANDLE", handover["nativeOrca"]["identitySource"])
                    self.assertIn(
                        "Only an active Dispatch worker", handover["nativeOrca"]["messageSemantics"]
                    )
                    self.assertTrue(canonical_report.is_relative_to(task_reports.resolve()))
                    self.assertEqual(len(documents), 3)
                    documents_by_ref = {
                        document.ref.key: document
                        for document in (context.sprint, context.master, context.task)
                        if document is not None
                    }
                    for row in documents:
                        resolved = documents_by_ref[row["canonicalTaskPath"]]
                        source = resolved.document.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        )
                        self.assertNotIn("document", row)
                        self.assertEqual(row["contentDigest"], digest(source))
                        self.assertEqual(row["documentIdentity"]["repositoryId"], source["repo"])
                        read_args = row["taskDocReadArgs"]
                        self.assertEqual(read_args["operation"], "get")
                        self.assertEqual(read_args["repo_id"], resolved.ref.repository)
                        self.assertEqual(read_args["task_name"], resolved.path.parent.name)
                        self.assertEqual(read_args["slug"], resolved.path.stem)
                        self.assertFalse(read_args["slug"].endswith(".json"))
                        read_result = task_doc_tool(
                            scoped_config,
                            TaskDocTarget(
                                repo_id=read_args["repo_id"],
                                task_name=read_args["task_name"],
                                slug=read_args["slug"],
                            ),
                            operation=read_args["operation"],
                            edit=TaskDocEdit(),
                            call=TaskDocCall(),
                        )
                        self.assertTrue(read_result["ok"])
                        self.assertEqual(read_result["docPath"], resolved.path.as_posix())
                    self.assertEqual(prepared["taskDocumentDigest"], digest(documents))

                    outcome = compile_task_capsule(
                        scoped_config,
                        CapsuleCompileRequest(
                            enclosure=EnclosureSelector(contract_path=contract.contract_path),
                            task_path=task_ref.path,
                            operation=role_start_operation(role),
                            role=role,
                        ),
                    )
                    self.assertTrue(outcome.ok, outcome.explanation())
                    assert outcome.result is not None
                    admitted = outcome.result.capsule.binding.admitted
                    self.assertEqual(admitted.repository_id, repo_id)
                    self.assertEqual(admitted.work_branch, contract.code_work_branch)
                    self.assertEqual(prepared["capsuleDigest"], outcome.result.semantic_digest)

    def test_taskless_architect_handover_uses_planning_without_inventing_task_or_repository(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = McpRuntimeConfig(
                config_path=root / "settings" / "ar.json",
                coordination_root=root / "coordination",
                workspace_root=root / "projects",
                transcript_root=root / "coordination" / "logs" / "mcp",
            )
            workspace_root = config.workspace_root
            workspace_root.mkdir(parents=True)
            request_id = uuid.uuid4()
            context = resolve_orca_role_context(
                config,
                OrcaSelection(role="architect"),
            )
            workspace = {"path": workspace_root.as_posix()}
            defaults = {"agent": "codex", "model": None, "effort": None}
            session_options = {"model": "gpt-6-sol"}
            agent_arg_tokens = ()

            with (
                patch.object(
                    orca_task_preparation, "_resolve_workspace", return_value=workspace
                ) as resolve_workspace,
                patch.object(
                    orca_task_preparation,
                    "_role_defaults",
                    return_value=(defaults, ("codex",)),
                ),
                patch.object(
                    orca_task_preparation,
                    "_resolve_agent_selection",
                    return_value=("codex", session_options, agent_arg_tokens),
                ) as resolve_agent,
            ):
                role_handover = prepare_orca_role_handover(
                    config,
                    context,
                    agent_override=None,
                    request_id=request_id,
                )
            prepared = role_handover.handover
            handover = json.loads(
                prepared["prompt"].rsplit(
                    "\n\nAR owner assignment and canonical task handover:\n", 1
                )[1]
            )

        self.assertEqual(
            (
                role_handover.context,
                role_handover.workspace,
                role_handover.agent_id,
                role_handover.session_options,
                role_handover.agent_arg_tokens,
            ),
            (context, workspace, "codex", session_options, agent_arg_tokens),
        )
        resolve_workspace.assert_called_once_with(config, context)
        resolve_agent.assert_called_once_with(config, defaults, ("codex",), None)

        self.assertEqual(prepared["capsuleOperation"], "planning")
        self.assertEqual(handover["operation"], "planning")
        self.assertEqual(handover["selection"]["role"], "architect")
        self.assertEqual(handover["requestId"], str(request_id))
        self.assertEqual(handover["taskDocumentDigest"], prepared["taskDocumentDigest"])
        self.assertEqual(handover["taskDocumentDigest"], digest([]))
        self.assertEqual(
            handover["nativeOrca"]["messageBinding"]["arBinding"],
            {
                "requestId": str(request_id),
                "role": "architect",
                "operation": "planning",
                "selection": handover["selection"],
                "taskDocumentDigest": digest([]),
                "taskReportPath": prepared["canonicalTaskReportPath"],
                "capsuleDigest": prepared["capsuleDigest"],
            },
        )
        projection_reference = _message_binding_projection_reference(
            config, request_id, handover["nativeOrca"]["messageBinding"]["arBinding"]
        )
        self.assertEqual(
            handover["nativeOrca"]["messageBinding"]["projection"], projection_reference
        )
        self.assertTrue(
            Path(handover["nativeOrca"]["messageBinding"]["projection"]["path"]).is_absolute()
        )
        self.assertEqual(
            prepared["messageBindingProjection"],
            {
                "requestId": str(request_id),
                "binding": handover["nativeOrca"]["messageBinding"]["arBinding"],
                **projection_reference,
            },
        )
        self.assertIsNone(handover["selection"]["sprintDocumentRef"])
        self.assertIsNone(handover["selection"]["masterDocumentRef"])
        self.assertIsNone(handover["selection"]["taskDocumentRef"])
        self.assertEqual(handover["documents"], [])
        self.assertIsNone(handover["repositoryContext"]["selectedRepository"])
        self.assertEqual(
            handover["capsule"]["binding"]["taskPath"],
            "free-agent:architect",
        )
        self.assertEqual(handover["capsule"]["binding"]["role"], "architect")
        self.assertEqual(handover["capsule"]["binding"]["operation"], "planning")
        self.assertTrue(prepared["prompt"].startswith("EXPERIMENTAL MANUAL AR ROLE BRIEF:"))
        source = handover["nativeOrca"]["instructionSource"]
        self.assertEqual(source["kind"], "compiled-role-operation-capsule")
        self.assertEqual(source["role"], "architect")
        self.assertEqual(source["operation"], "planning")
        self.assertEqual(source["semanticDigest"], prepared["capsuleDigest"])
        self.assertFalse(source["ambientRoleFilesSelected"])
        self.assertIn("do not load them as a second role", handover["ownerHandover"])
        self.assertIn("Preserve native system/developer instructions", prepared["prompt"])
        self.assertEqual(
            {
                key: handover["arMcpContext"][key]
                for key in (
                    "scopeKind",
                    "repositoryId",
                    "availableRepositoryIds",
                    "readerArguments",
                )
            },
            {
                "scopeKind": "configured-projects",
                "repositoryId": None,
                "availableRepositoryIds": [],
                "readerArguments": None,
            },
        )
        self.assertTrue(prepared["taskReportPath"].endswith(f"/{request_id}.md"))
        self.assertEqual(ROLE_START_OPERATIONS["architect"], "planning")


def _bridge_whose_first_creation_gets_no_answer(agents: dict[str, dict[str, Any]]) -> Any:
    """A stand-in for ``bridge_call``: a catalog, a workspace, and creations after one timeout."""

    answers = [PaseoBridgeFailure("paseo_bridge_timeout", "no answer in time")]

    def bridge(_config: McpRuntimeConfig, command: str, payload: dict[str, Any]) -> Any:
        if command == "catalog":
            return {"providers": [{"id": "codex", "label": "Codex", "models": []}]}
        if command == "workspace-open":
            return {"serverId": "srv", "workspace": {"id": "wks", "directory": payload["cwd"]}}
        if answers:
            raise answers.pop()
        agents[payload["agentId"]] = payload
        agent = {"id": payload["agentId"], "provider": "codex", "workspaceId": "wks"}
        return {"serverId": "srv", "existing": False, "agent": agent}

    return bridge


class RepeatAfterDocumentEditTests(unittest.TestCase):
    """The dispatch route with the real handover compilation and a stand-in for the bridge."""

    def test_a_repeat_runs_the_stored_call_after_the_leaf_document_was_edited(self) -> None:
        agents: dict[str, dict[str, Any]] = {}
        bridge = _bridge_whose_first_creation_gets_no_answer(agents)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(checkout_coordination._declared, {"mode": "test"}):
                contract = open_external_contract_fixture(root)
            repo_id = contract.repo_name
            leaf_slug = contract.leaf_id.lower().replace("_", "-")
            documents: dict[Path, dict[str, Any]] = {
                contract.coordination_root / "tasks" / repo_id / "sprint": {
                    "id": "SPRINT",
                    "slug": "sprint",
                    "title": "Repeat sprint",
                    "kind": "master",
                    "orchestrates": [contract.task_root.name],
                },
                contract.task_root: {
                    "id": contract.task_id,
                    "slug": "task",
                    "title": contract.task_name,
                    "kind": "master",
                    "subTasks": [
                        {
                            "number": contract.leaf_id,
                            "name": "Repeat after an edit",
                            "file": f"{leaf_slug}.md",
                            "status": "inProgress",
                        }
                    ],
                },
            }
            leaf: dict[str, Any] = {
                "id": contract.leaf_id,
                "slug": leaf_slug,
                "title": "Repeat after an edit",
                "kind": "subTask",
                "status": "inProgress",
                "master": "task.md",
                "enclosures": [
                    {"leafId": contract.leaf_id, "enclosurePath": contract.contract_path.as_posix()}
                ],
            }
            shared = {"repo": repo_id, "createdAt": "2026-10-02T05:00:00+00:00"}
            for folder, document in documents.items():
                write_task_doc(folder, TaskDocument.model_validate({**document, **shared}))
            write_task_doc(contract.task_root, TaskDocument.model_validate({**leaf, **shared}))
            settings_path = root / "settings" / "ar.json"
            settings_path.parent.mkdir(parents=True)
            settings = {
                "coordinationRoot": contract.coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {repo_id: {}},
                "paseoRuntime": {
                    "installPrefix": (root / "paseo" / "prefix").as_posix(),
                    "home": (root / "paseo" / "home").as_posix(),
                    "listen": "127.0.0.1:6835",
                    "version": "0.11.0-beta.2",
                    "providers": {},
                    "embed": [],
                },
            }
            settings_path.write_text(json.dumps(settings), encoding="utf-8")
            request = OrcaDispatchRequest.model_validate(
                {
                    "role": "worker",
                    "requestId": uuid.uuid4(),
                    "sprintDocumentRef": {"repository": repo_id, "path": "sprint/task.json"},
                    "masterDocumentRef": {
                        "repository": repo_id,
                        "path": f"{contract.task_root.name}/task.json",
                    },
                    "taskDocumentRef": {
                        "repository": repo_id,
                        "path": f"{contract.task_root.name}/{leaf_slug}.json",
                    },
                    "agentOverride": {"agentId": "codex"},
                }
            )

            def dispatch() -> tuple[int, dict[str, Any]]:
                response = orca_task_routes._orca_dispatch_endpoint(config, request)
                return response.status_code, json.loads(bytes(response.body))

            forget_launcher_catalogs()
            bind_worktree_services(build_default_worktree_services())
            try:
                with (
                    patch.dict(checkout_coordination._declared, {"mode": "test"}),
                    patch.object(paseo_catalog, "bridge_call", bridge),
                    patch.object(paseo_launch, "bridge_call", bridge),
                ):
                    config = load_config(settings_path)
                    first_status, first = dispatch()
                    receipt_path = orca_task_receipts._receipt_path(config, request)
                    saved = json.loads(receipt_path.read_text(encoding="utf-8"))
                    # One ordinary edit of the leaf's task document, as an agent or a person makes.
                    edited = {**leaf, "title": "Repeat after an edit, with a decision added"}
                    write_task_doc(
                        contract.task_root, TaskDocument.model_validate({**edited, **shared})
                    )
                    recompiled = prepare_orca_role_handover(
                        config,
                        resolve_orca_role_context(config, request),
                        agent_override=request.agent_override,
                        request_id=request.request_id,
                    ).handover
                    second_status, second = dispatch()
                    third_status, third = dispatch()
            finally:
                reset_worktree_services()
                forget_launcher_catalogs()

        self.assertEqual((first_status, first["status"], first["canRetry"]), (202, "unknown", True))
        # The edit changes what a compilation of this request yields ...
        self.assertNotEqual(recompiled["taskDocumentDigest"], saved["taskDocumentDigest"])
        self.assertNotEqual(
            recompiled["messageBindingProjection"]["sha256"],
            saved["messageBindingProjection"]["sha256"],
        )
        # ... and the repeat does not compile: it runs the saved call under the minted agent id.
        self.assertEqual((second_status, second["status"]), (200, "running"))
        self.assertEqual(second["execution"]["agentId"], saved["agentId"])
        self.assertEqual(list(agents), [saved["agentId"]])
        self.assertEqual(
            agents[saved["agentId"]]["prompt"], saved["replayRequest"]["agent"]["prompt"]
        )
        self.assertEqual((third_status, third), (200, second))


class LeafEnclosureSprintBindingTests(unittest.TestCase):
    def test_missing_leaf_enclosure_passes_selected_sprint_to_worktree_owner(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_root = root / "coordination" / "tasks" / "agents-remember" / "master"
            task_root.mkdir(parents=True)
            leaf_path = task_root / "01_leaf.json"
            leaf = ResolvedTaskDocument(
                ref=TaskDocumentRef(repository="agents-remember", path="master/01_leaf.json"),
                path=leaf_path,
                document=TaskDocument.model_validate(
                    {
                        "id": "01_LEAF",
                        "slug": "01_leaf",
                        "title": "Leaf enclosure binding",
                        "kind": "subTask",
                        "status": "inProgress",
                        "repo": "agents-remember",
                        "createdAt": "2026-09-25T10:00:00+00:00",
                        "master": "task.json",
                    }
                ),
            )
            sprint = ResolvedTaskDocument(
                ref=TaskDocumentRef(
                    repository="agents-remember",
                    path="260713_improved-agentic-system/task.json",
                ),
                path=root
                / "coordination"
                / "tasks"
                / "agents-remember"
                / "260713_improved-agentic-system"
                / "task.json",
                document=TaskDocument.model_validate(
                    {
                        "id": "IAS",
                        "slug": "260713_improved-agentic-system",
                        "title": "IAS",
                        "kind": "master",
                        "repo": "agents-remember",
                        "createdAt": "2026-09-25T09:00:00+00:00",
                        "orchestrates": ["master"],
                    }
                ),
            )
            master = ResolvedTaskDocument(
                ref=TaskDocumentRef(repository="agents-remember", path="master/task.json"),
                path=task_root / "task.json",
                document=TaskDocument.model_validate(
                    {
                        "id": "MASTER",
                        "slug": "master",
                        "title": "Master",
                        "kind": "master",
                        "repo": "agents-remember",
                        "createdAt": "2026-09-25T09:30:00+00:00",
                    }
                ),
            )
            config = McpRuntimeConfig(
                config_path=root / "settings" / "ar.json",
                coordination_root=root / "coordination",
                workspace_root=root / "projects",
                transcript_root=root / "coordination" / "logs",
            )
            group = root / "enclosure"
            code = group / "code"
            memory = group / "memory"
            for path in (group, code, memory):
                path.mkdir(parents=True)
            status = {
                "ok": True,
                "worktree_group": group.as_posix(),
                "code_worktree": code.as_posix(),
                "memory_worktree": memory.as_posix(),
            }
            statuses = iter(({"ok": False}, status))

            context = OrcaRoleContext(
                role="worker", sprint=sprint, master=master, task=leaf, effective_task=leaf
            )
            with (
                patch(
                    "agents_remember.cli.orca_task_preparation.worktree_status_tool",
                    side_effect=lambda *_args: next(statuses),
                ),
                patch(
                    "agents_remember.cli.orca_task_preparation.worktree_start_tool",
                    return_value={"ok": True},
                ) as start,
            ):
                workspace = _resolve_workspace(config, context)

            # The leaf runs in its enclosure group folder; no host workspace id is resolved here.
            self.assertEqual(workspace["path"], group.resolve().as_posix())
            self.assertEqual(workspace["codeRoot"], code.resolve().as_posix())
            self.assertFalse({"id", "selector"} & set(workspace))
            self.assertEqual(
                (group / "task-reports").resolve(), (task_root / "notes" / "reports").resolve()
            )
            identity = start.call_args.args[1]
            self.assertEqual(identity.repo_id, "agents-remember")
            self.assertEqual(identity.task_name, "master")
            self.assertEqual(identity.leaf_id, "01_LEAF")
            self.assertEqual(identity.parent_task, "260713_improved-agentic-system")

            existing_path = leaf_enclosure_path(task_root, leaf.document.id)
            existing_leaf = ResolvedTaskDocument(
                ref=leaf.ref,
                path=leaf.path,
                document=leaf.document.model_copy(
                    update={
                        "enclosures": [
                            TaskEnclosureRef(
                                leafId=leaf.document.id,
                                enclosurePath=existing_path.as_posix(),
                            )
                        ]
                    }
                ),
            )
            with (
                patch(
                    "agents_remember.cli.orca_task_preparation.worktree_status_tool",
                    return_value=status,
                ),
                patch(
                    "agents_remember.cli.orca_task_preparation.worktree_start_tool"
                ) as reuse_start,
            ):
                contract_path, reused_status = _ensure_leaf_enclosure(
                    config, existing_leaf, parent_task="260713_improved-agentic-system"
                )
            self.assertEqual(contract_path, existing_path.resolve())
            self.assertIs(reused_status, status)
            reuse_start.assert_not_called()


class OrcaReportPathIsolationTests(unittest.TestCase):
    @staticmethod
    def _resolved_document(
        root: Path, *, task_path: str, document_id: str, kind: str
    ) -> ResolvedTaskDocument:
        path = root / task_path
        document = TaskDocument.model_validate(
            {
                "id": document_id,
                "slug": path.parent.name,
                "title": document_id,
                "kind": kind,
                "repo": "agents-remember",
                "createdAt": "2026-09-25T10:00:00+00:00",
                **(
                    {"status": "inProgress", "master": "master/task.json"}
                    if kind == "subTask"
                    else {}
                ),
            }
        )
        return ResolvedTaskDocument(
            ref=TaskDocumentRef(repository="agents-remember", path=task_path),
            path=path,
            document=document,
        )

    def test_manager_reports_are_master_local_and_request_unique(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = {"path": (root / "Projects").as_posix()}
            masters = [
                self._resolved_document(
                    root,
                    task_path=f"series/{name}/task.json",
                    document_id=name.upper(),
                    kind="master",
                )
                for name in ("master-one", "master-two")
            ]
            sprint = self._resolved_document(
                root, task_path="series/sprint/task.json", document_id="SPRINT", kind="master"
            )
            manager_reports: list[Path] = []
            for master in masters:
                context = OrcaRoleContext(
                    role="manager",
                    sprint=sprint,
                    master=master,
                    task=None,
                    effective_task=master,
                )
                request_id = uuid.uuid4()
                report = Path(_role_report_path(context, workspace, request_id=request_id))
                self.assertTrue(
                    report.resolve(strict=False).is_relative_to(
                        (master.path.parent / "notes" / "reports").resolve()
                    )
                )
                self.assertEqual(report.name, f"{master.document.id}-manager-{request_id}.md")
                report.write_text(f"original {master.document.id}\n", encoding="utf-8")
                manager_reports.append(report)

            retry_context = OrcaRoleContext(
                role="manager",
                sprint=sprint,
                master=masters[0],
                task=None,
                effective_task=masters[0],
            )
            retry_report = Path(
                _role_report_path(retry_context, workspace, request_id=uuid.uuid4())
            )
            retry_report.write_text("new request\n", encoding="utf-8")
            self.assertNotEqual(manager_reports[0], manager_reports[1])
            self.assertNotEqual(manager_reports[0], retry_report)
            self.assertEqual(
                manager_reports[0].read_text(encoding="utf-8"), "original MASTER-ONE\n"
            )

    def test_leaf_reports_are_task_local_and_each_request_gets_its_own_file(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sprint = self._resolved_document(
                root, task_path="series/sprint/task.json", document_id="SPRINT", kind="master"
            )
            master = self._resolved_document(
                root, task_path="series/master/task.json", document_id="MASTER", kind="master"
            )
            leaf = self._resolved_document(
                root, task_path="series/leaf/leaf.json", document_id="LEAF-ONE", kind="subTask"
            )
            reports = leaf.path.parent / "notes" / "reports"
            reports.mkdir(parents=True)
            workspace_root = root / "leaf-workspace"
            workspace_root.mkdir()
            report_access = _bind_task_report_access(workspace_root, reports)
            workspace = {
                "path": workspace_root.as_posix(),
                "taskReportRoot": reports.resolve().as_posix(),
                "taskReportAccessRoot": report_access.as_posix(),
            }
            context = OrcaRoleContext(
                role="worker",
                sprint=sprint,
                master=master,
                task=leaf,
                effective_task=leaf,
            )
            first = Path(_role_report_path(context, workspace, request_id=uuid.uuid4()))
            second = Path(_role_report_path(context, workspace, request_id=uuid.uuid4()))
            first.write_text("original leaf report\n", encoding="utf-8")
            second.write_text("second attempt\n", encoding="utf-8")
            self.assertNotEqual(first, second)
            self.assertTrue(first.resolve().is_relative_to(reports.resolve()))
            self.assertTrue(second.resolve().is_relative_to(reports.resolve()))
            self.assertEqual(first.read_text(encoding="utf-8"), "original leaf report\n")
