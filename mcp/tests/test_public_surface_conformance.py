"""L4 acceptance for public dispatch advertisement and starter launch identity."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import sys
import tempfile
import tomllib
import unittest
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.structural.outcomes import StructuralOutcome, structural_payload
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp import public_surface
from agents_remember.mcp.public_surface import PublicSurfaceViolation, validate_public_surface
from agents_remember.mcp.server import create_server
from agents_remember.mcp.tools import PUBLIC_TOOLS
from agents_remember.models.structural.agent import DispatchAgentResponse
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.models.tools.tool_response import finalize_tool_response
from agents_remember.serving.build_info import runtime_source_digest
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from test_config import settings_payload, write_json

from mcp import ClientSession, StdioServerParameters

_PATH_PLACEHOLDER = "<PATH/TO/YOUR/PROJECTS_FOLDER>"
_CURRENT_PACKAGE_ARGS = (
    "--refresh-package",
    "agents-remember-mcp",
    "agents-remember-mcp@latest",
    "--config",
)
_MCP_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class StarterRegistration:
    key: str
    path: str
    command: str
    args: tuple[str, ...]


def _nested(document: dict[str, object], *path: str) -> dict[str, object]:
    current: object = document
    for part in path:
        if not isinstance(current, dict) or part not in current:
            raise AssertionError(f"starter path is missing: {'/'.join(path)}")
        current = current[part]
    if not isinstance(current, dict):
        raise AssertionError(f"starter registration is not an object: {'/'.join(path)}")
    return current


def _yaml_mapping_block(text: str, key: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != f"{key}:":
            continue
        key_indent = len(line) - len(line.lstrip())
        body: list[str] = []
        for candidate in lines[index + 1 :]:
            if candidate.strip():
                candidate_indent = len(candidate) - len(candidate.lstrip())
                if candidate_indent <= key_indent:
                    break
            body.append(candidate)
        return "\n".join(body)
    raise AssertionError(f"YAML mapping is missing: {key}")


def _json_registration(key: str, relative: str, *path: str) -> StarterRegistration:
    document = json.loads(REPOSITORY_ROOT.joinpath(relative).read_text(encoding="utf-8"))
    registration = _nested(document, *path)
    command = registration.get("command")
    args = registration.get("args")
    if (
        not isinstance(command, str)
        or not isinstance(args, list)
        or not all(isinstance(value, str) for value in args)
    ):
        raise AssertionError(f"{relative}: malformed agents-remember command/args")
    return StarterRegistration(
        key=key,
        path=relative,
        command=command,
        args=tuple(args),
    )


def _starter_registrations() -> tuple[StarterRegistration, ...]:
    codex_document = tomllib.loads(
        REPOSITORY_ROOT.joinpath(".codex/config.toml").read_text(encoding="utf-8")
    )
    codex = _nested(codex_document, "mcp_servers", "agents-remember")
    codex_command = codex.get("command")
    codex_args = codex.get("args")
    if (
        not isinstance(codex_command, str)
        or not isinstance(codex_args, list)
        or not all(isinstance(value, str) for value in codex_args)
    ):
        raise AssertionError("Codex starter has malformed agents-remember command/args")
    assert codex.get("env_vars") == ["AR_HOSTED_SESSION_ID", "AR_SPAWN_ROLE"]

    hermes_text = REPOSITORY_ROOT.joinpath(".hermes/config.yaml").read_text(encoding="utf-8")
    hermes_mcp = _yaml_mapping_block(hermes_text, "mcp_servers")
    hermes_entry = _yaml_mapping_block(hermes_mcp, "agents-remember")
    command_match = re.search(r'^\s+command:\s*"([^"]+)"\s*$', hermes_entry, re.MULTILINE)
    if command_match is None:
        raise AssertionError("Hermes starter has no agents-remember command")
    hermes_args = tuple(re.findall(r'^\s+-\s+"([^"]+)"\s*$', hermes_entry, re.MULTILINE))

    json_registrations = (
        _json_registration("claude", ".claude/mcp/mcp.json", "mcpServers", "agents-remember"),
        _json_registration("cursor", ".cursor/mcp.json", "mcpServers", "agents-remember"),
        _json_registration("vscode", ".vscode/mcp.json", "servers", "agents-remember"),
        _json_registration(
            "openclaw",
            ".openclaw/openclaw.merge.json",
            "mcp",
            "servers",
            "agents-remember",
        ),
        _json_registration("pi", ".pi/mcp.json", "mcpServers", "agents-remember"),
        _json_registration(
            "antigravity", ".agents/mcp_config.json", "mcpServers", "agents-remember"
        ),
    )
    return (
        json_registrations[0],
        StarterRegistration(
            "codex",
            ".codex/config.toml",
            codex_command,
            tuple(codex_args),
        ),
        json_registrations[1],
        json_registrations[2],
        StarterRegistration("hermes", ".hermes/config.yaml", command_match.group(1), hermes_args),
        json_registrations[3],
        json_registrations[4],
        json_registrations[5],
    )


def _structured_payload(result: CallToolResult) -> dict[str, object]:
    structured = result.structuredContent
    if structured is not None:
        return dict(structured)
    content = result.content
    text = "".join(block.text for block in content if isinstance(block, TextContent))
    return dict(json.loads(text))


async def _inspect_exact_candidate(
    registration: StarterRegistration, settings_path: Path
) -> tuple[str, str, str]:
    """Inspect the candidate, not the package registry, through one harness boundary.

    The separate starter test pins each production ``uvx ... @latest`` command. Running
    that command here would certify the last published package and could conceal the exact
    stale-install defect under review. This disposable process therefore uses the current
    test interpreter plus current source while preserving the starter-derived settings path,
    stdio transport, hosted-process environment, and public MCP handshake.
    """

    pythonpath = str(MCP_SRC)
    if os.environ.get("PYTHONPATH"):
        pythonpath += os.pathsep + os.environ["PYTHONPATH"]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", str(settings_path)],
        env={
            **os.environ,
            "PYTHONPATH": pythonpath,
            "AR_HOSTED_SESSION_ID": f"l4-controlled-{registration.key}",
        },
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await asyncio.wait_for(session.initialize(), timeout=_MCP_TIMEOUT_SECONDS)
        listed = await asyncio.wait_for(session.list_tools(), timeout=_MCP_TIMEOUT_SECONDS)
        evidence = validate_public_surface(listed.tools)
        result = await asyncio.wait_for(
            session.call_tool("server_info", {}), timeout=_MCP_TIMEOUT_SECONDS
        )
        server_info = _structured_payload(result)

    build = server_info["servingBuild"]
    if not isinstance(build, dict):
        raise AssertionError("server_info servingBuild is not an object")
    expected_root = MCP_SRC.joinpath("agents_remember").resolve()
    expected_digest = runtime_source_digest(expected_root)
    if build.get("sourceDigest") != expected_digest:
        raise AssertionError(f"{registration.key}: server did not launch the current source")
    if build.get("packageRoot") != expected_root.as_posix():
        raise AssertionError(f"{registration.key}: server loaded a different package root")
    if build.get("pythonExecutable") != Path(sys.executable).resolve().as_posix():
        raise AssertionError(f"{registration.key}: server loaded a different interpreter")
    if not build.get("bootedAt"):
        raise AssertionError(f"{registration.key}: server omitted its boot identity")
    reported_tools = server_info.get("tools")
    if not isinstance(reported_tools, list) or tuple(reported_tools) != PUBLIC_TOOLS:
        raise AssertionError(f"{registration.key}: server_info differs from live tools/list")
    return (
        str(build["sourceDigest"]),
        evidence.dispatch_schema_digest,
        evidence.dispatch_response_model,
    )


class PublicDispatchContractTests(unittest.TestCase):
    def _server(self, root: Path):
        settings_path = root / "settings.json"
        write_json(settings_path, settings_payload(root))
        return create_server(load_config(settings_path))

    def test_live_registration_matches_every_public_authority_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = asyncio.run(self._server(Path(tmp)).list_tools())

        evidence = validate_public_surface(tools)
        self.assertEqual(evidence.tool_names, PUBLIC_TOOLS)
        self.assertEqual(evidence.dispatch_response_model, "DispatchAgentResponse")

    def test_dispatch_rejects_spend_override_and_invalid_role_before_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._server(Path(tmp))
            task_ref = {"repository": "agents-remember", "path": "missing.json"}
            with mock.patch(
                "agents_remember.mcp.registration.sessions.dispatch_agent_payload"
            ) as handler:
                with self.assertRaisesRegex(ToolError, "rejects undeclared inputs: model"):
                    asyncio.run(
                        server.call_tool(
                            "dispatch_agent",
                            {
                                "task_document_ref": task_ref,
                                "role": "architect",
                                "brief": "Design the task.",
                                "model": "gpt-5",
                            },
                        )
                    )
                with self.assertRaisesRegex(ToolError, "Input should be"):
                    asyncio.run(
                        server.call_tool(
                            "dispatch_agent",
                            {
                                "task_document_ref": task_ref,
                                "role": "not-a-role",
                                "brief": "Design the task.",
                            },
                        )
                    )
            handler.assert_not_called()

    def test_registered_ambient_unknown_task_is_a_typed_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = self._server(Path(tmp))
            ambient_env = {
                key: value for key, value in os.environ.items() if key != "AR_HOSTED_SESSION_ID"
            }
            with mock.patch.dict(os.environ, ambient_env, clear=True):
                _content, structured = asyncio.run(
                    server.call_tool(
                        "dispatch_agent",
                        {
                            "task_document_ref": {
                                "repository": "agents-remember",
                                "path": "missing.json",
                            },
                            "role": "architect",
                            "brief": "Design the task.",
                        },
                    )
                )

        self.assertIsNotNone(structured)
        response = DispatchAgentResponse.model_validate(structured)
        self.assertFalse(response.ok)
        self.assertEqual(response.status, "task-document-not-found")

    def test_ambient_outcomes_round_trip_the_one_strict_wire_envelope(self) -> None:
        task_ref = TaskDocumentRef(repository="repo", path="sprint/task.json")
        outcomes = (
            StructuralOutcome("dispatch_agent", True, "dispatched", task_ref, "architect"),
            StructuralOutcome("dispatch_agent", True, "dispatch-queued", task_ref, "architect"),
            StructuralOutcome(
                "dispatch_agent",
                False,
                "task-document-not-found",
                task_ref,
                "architect",
            ),
            StructuralOutcome(
                "dispatch_agent",
                False,
                "seat-role-altitude-mismatch",
                task_ref,
                "worker",
            ),
            StructuralOutcome(
                "dispatch_agent",
                False,
                "dispatch-persistence-refused",
                task_ref,
                "architect",
            ),
        )
        for outcome in outcomes:
            with self.subTest(status=outcome.status):
                payload = finalize_tool_response("dispatch_agent", structural_payload(outcome))
                response = DispatchAgentResponse.model_validate(payload)
                self.assertEqual(response.status, outcome.status)
                self.assertNotIn("session", json.dumps(payload).casefold())


class PublicSurfaceFailureTests(unittest.TestCase):
    def _listed_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_path = root / "settings.json"
            write_json(settings_path, settings_payload(root))
            return asyncio.run(create_server(load_config(settings_path)).list_tools())

    def _schema_candidate(self):
        tools = copy.deepcopy(self._listed_tools())
        dispatch = next(tool for tool in tools if tool.name == "dispatch_agent")
        schema = dispatch.inputSchema
        self.assertIsInstance(schema, dict)
        return tools, dispatch, schema

    def _assert_schema_rejected(self, schema: dict[str, object], message: str) -> None:
        tools, dispatch, _original = self._schema_candidate()
        dispatch.inputSchema = schema
        with self.assertRaisesRegex(PublicSurfaceViolation, message):
            validate_public_surface(tools)

    def test_inventory_authorities_each_fail_closed(self) -> None:
        original_public = public_surface.PUBLIC_TOOLS
        original_reserved = public_surface.RESERVED_TOOLS
        original_models = public_surface.TOOL_RESPONSE_MODELS
        original_public_models = public_surface.PUBLIC_TOOL_RESPONSE_MODELS

        extra_models = {**original_models, "not-advertised": DispatchAgentResponse}
        extra_public_models = {
            **original_public_models,
            "not-advertised": DispatchAgentResponse,
        }
        wrong_dispatch_models = {
            name: (original_models["ping"] if name == "dispatch_agent" else model)
            for name, model in original_models.items()
        }
        wrong_dispatch_public = {
            name: (original_models["ping"] if name == "dispatch_agent" else model)
            for name, model in original_public_models.items()
        }
        missing_internal_models = {
            name: model for name, model in original_models.items() if name != "spawn_agent_session"
        }
        cases: tuple[tuple[dict[str, object], str], ...] = (
            (
                {"PUBLIC_TOOLS": (*original_public, original_public[0])},
                "PUBLIC_TOOLS contains a duplicate",
            ),
            (
                {"RESERVED_TOOLS": ("reserved", "reserved")},
                "RESERVED_TOOLS contains a duplicate",
            ),
            (
                {"RESERVED_TOOLS": (original_public[0],)},
                "public and reserved tool inventories overlap",
            ),
            (
                {"PUBLIC_TOOL_RESPONSE_MODELS": dict(reversed(original_public_models.items()))},
                "public response-model registry is not a stable projection",
            ),
            (
                {
                    "TOOL_RESPONSE_MODELS": extra_models,
                    "PUBLIC_TOOL_RESPONSE_MODELS": extra_public_models,
                },
                "public tools and response-model registry disagree",
            ),
            (
                {
                    "TOOL_RESPONSE_MODELS": wrong_dispatch_models,
                    "PUBLIC_TOOL_RESPONSE_MODELS": wrong_dispatch_public,
                },
                "dispatch_agent is not mapped to DispatchAgentResponse",
            ),
            (
                {"TOOL_RESPONSE_MODELS": missing_internal_models},
                "spawn_agent_session must remain internal",
            ),
        )
        self.assertEqual(original_reserved, ())
        for replacements, message in cases:
            with self.subTest(message=message), ExitStack() as stack:
                for attribute, replacement in replacements.items():
                    stack.enter_context(mock.patch.object(public_surface, attribute, replacement))
                stack.enter_context(self.assertRaisesRegex(PublicSurfaceViolation, message))
                public_surface._validate_inventory_authorities()

    def test_schema_reference_failures_are_explicit(self) -> None:
        _tools, _dispatch, baseline = self._schema_candidate()
        cases = (
            (42, "malformed node"),
            ({"$ref": "https://example.invalid/schema"}, "external reference"),
            ({"$ref": "#/$defs/Missing"}, "unresolved ref"),
        )
        for node, message in cases:
            with self.subTest(message=message):
                schema = copy.deepcopy(baseline)
                schema["properties"]["task_document_ref"] = node
                self._assert_schema_rejected(schema, message)

        non_object = copy.deepcopy(baseline)
        non_object["$defs"]["NotObject"] = "not-an-object"
        non_object["properties"]["task_document_ref"] = {"$ref": "#/$defs/NotObject"}
        self._assert_schema_rejected(non_object, "ref is not an object")

    def test_dispatch_envelope_failures_are_explicit(self) -> None:
        _tools, _dispatch, baseline = self._schema_candidate()
        cases = (
            ("additional", True, "must reject undeclared inputs"),
            ("properties", {"brief": {"type": "string"}}, "must advertise exactly"),
            ("required", "brief", "must declare its required inputs"),
            ("required", [], "must require task_document_ref"),
        )
        for field, value, message in cases:
            with self.subTest(field=field, message=message):
                schema = copy.deepcopy(baseline)
                target = "additionalProperties" if field == "additional" else field
                schema[target] = value
                self._assert_schema_rejected(schema, message)

    def test_task_reference_failures_are_explicit(self) -> None:
        _tools, _dispatch, baseline = self._schema_candidate()
        valid_task_ref = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "repository": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["repository", "path"],
        }
        cases = (
            ("additionalProperties", True, "must reject extra identity"),
            ("properties", {"path": {"type": "string"}}, "canonical repository/path"),
            ("required", ["path"], "must require repository and path"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                schema = copy.deepcopy(baseline)
                task_ref = copy.deepcopy(valid_task_ref)
                task_ref[field] = value
                schema["properties"]["task_document_ref"] = task_ref
                self._assert_schema_rejected(schema, message)

    def test_dispatch_value_and_description_failures_are_explicit(self) -> None:
        _tools, _dispatch, baseline = self._schema_candidate()
        cases = (
            ("role", {"type": "string", "enum": ["architect"]}, "role enum drifted"),
            ("brief", {}, "brief must be a string"),
            ("label", {"type": "integer"}, "label must be string-or-null"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                schema = copy.deepcopy(baseline)
                schema["properties"][field] = value
                self._assert_schema_rejected(schema, message)

        tools, dispatch, _schema = self._schema_candidate()
        dispatch.description = "Dispatch one role brief."
        with self.assertRaisesRegex(PublicSurfaceViolation, "omits caller-boundary facts"):
            validate_public_surface(tools)

    def test_consumer_dispatch_validator_refuses_wrong_name_and_non_object_schema(self) -> None:
        _tools, dispatch, schema = self._schema_candidate()
        with self.assertRaisesRegex(
            PublicSurfaceViolation,
            "must name dispatch_agent",
        ):
            public_surface.validate_dispatch_advertisement(
                name="spawn_agent_session",
                description=dispatch.description,
                input_schema=schema,
            )
        with self.assertRaisesRegex(
            PublicSurfaceViolation,
            "input schema must be an object",
        ):
            public_surface.validate_dispatch_advertisement(
                name="dispatch_agent",
                description=dispatch.description,
                input_schema=[],
            )

    def test_live_order_drift_is_rejected(self) -> None:
        tools = self._listed_tools()
        tools[0], tools[1] = tools[1], tools[0]
        with self.assertRaisesRegex(PublicSurfaceViolation, "differs from PUBLIC_TOOLS order"):
            validate_public_surface(tools)


class StarterConformanceTests(unittest.TestCase):
    def test_all_eight_production_starters_keep_self_updating(self) -> None:
        registrations = _starter_registrations()
        self.assertEqual(
            tuple(registration.key for registration in registrations),
            (
                "claude",
                "codex",
                "cursor",
                "vscode",
                "hermes",
                "openclaw",
                "pi",
                "antigravity",
            ),
        )
        for registration in registrations:
            with self.subTest(harness=registration.key, path=registration.path):
                self.assertEqual(registration.command, "uvx")
                self.assertEqual(registration.args[:-1], _CURRENT_PACKAGE_ARGS)
                self.assertTrue(registration.args[-1].startswith(_PATH_PLACEHOLDER + "/"))
                self.assertEqual(registration.args.count("--refresh-package"), 1)
                self.assertEqual(registration.args.count("agents-remember-mcp@latest"), 1)
                self.assertNotIn("--from", registration.args)

    def test_each_controlled_harness_launches_the_exact_candidate_over_stdio(self) -> None:
        identities: set[str] = set()
        schemas: set[str] = set()
        response_models: set[str] = set()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for registration in _starter_registrations():
                with self.subTest(harness=registration.key):
                    controlled_root = root / registration.key
                    template = registration.args[-1]
                    self.assertTrue(template.startswith(_PATH_PLACEHOLDER + "/"))
                    settings_path = Path(
                        template.replace(_PATH_PLACEHOLDER, controlled_root.as_posix())
                    )
                    write_json(settings_path, settings_payload(controlled_root))
                    identity, schema, model = asyncio.run(
                        _inspect_exact_candidate(registration, settings_path)
                    )
                    identities.add(identity)
                    schemas.add(schema)
                    response_models.add(model)

        self.assertEqual(len(identities), 1)
        self.assertEqual(len(schemas), 1)
        self.assertEqual(response_models, {"DispatchAgentResponse"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
