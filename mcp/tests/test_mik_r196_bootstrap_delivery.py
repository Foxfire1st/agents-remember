"""The one call's continuation and the free seats' discovery through their real boundaries.

MIK-R196 rule 1 promises, in one answer, the seat the call started and where the user continues
it, and adds the free agent to receipt-backed chat discovery. Each case drives the shipped
boundary rather than a helper beside it: the registered ``role_start`` tool through its payload,
strict response model and compact projection; and the document-chat read through its own
receipt-backed records and page bound.
"""

from __future__ import annotations

import asyncio
import json
import types
import uuid
from pathlib import Path
from typing import Any, cast

from agents_remember.application.role_launch_context import RoleLaunchContext
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.cli import (
    dashboard_address as dashboard_address_owner,
)
from agents_remember.cli import (
    paseo_bridge,
    paseo_role_tools,
    role_document_chats,
    role_launch_admission,
    role_launch_first_run,
    role_launch_preparation,
    role_launch_routes,
)
from agents_remember.cli.role_launch_first_run import _FIRST_RUN_REPORT
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.install import runtime
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.mcp.registration import role_agents as role_agent_registration
from agents_remember.mcp.registration.role_agents import register_role_agent_tools
from agents_remember.models.role_agents import RoleStartResponse
from agents_remember.models.role_launcher import RoleDispatchRequest, RoleSelection
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import TOOL_ANSWER_OVERFLOW_MAX_TOKENS, bound_for
from agents_remember.serving import daemon as serving_daemon
from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from paseo_launch_test_support import SERVER_ID
from test_mik_r196_l188_preparation_order import _stub_host, _workspace_config
from test_paseo_role_tools import RoleToolsTestCase

# The host's own web route this installation's dashboard embeds, so a chat address exists.
FRAME_BASE = "http://127.0.0.1:6835"


def authority_runtime(config: McpRuntimeConfig, root: Path) -> None:
    """Give the installation the host authority block a configured launch reads (MIK-R196 rule 3).

    The product reads ``paseoRuntime`` from the MCP authority settings file it was started with,
    so the fixture's block goes there: a host inside the test's own temporary root, with the embed
    entry its chat route is built from.
    """

    path = config.config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    block = {
        "installPrefix": (root / "prefix").as_posix(),
        "home": (root / "home").as_posix(),
        "listen": "127.0.0.1:6835",
        "version": PASEO_VERSION,
        "providers": {},
        "embed": [
            {
                "dashboardOrigin": f"http://127.0.0.1:{config.dashboard.port}",
                "frameBaseUrl": FRAME_BASE,
            }
        ],
    }
    path.write_text(json.dumps({**existing, "paseoRuntime": block}), encoding="utf-8")


# The harness this call's MCP client names, as the transport reports it in ``initialize``.
CALLER_HARNESS = "codex"

# What a cold start established, in the shape the preparation writes onto the receipt. The
# preparation itself is proven elsewhere; here the report only has to cross the answer boundary.
FIRST_RUN_STAGES: tuple[dict[str, Any], ...] = (
    {"stage": "scaffold", "state": "created", "changed": True},
    {"stage": "host-authority", "state": "created", "changed": True},
    {"stage": "host", "state": "running"},
)


class _FreeSeatWorld(RoleToolsTestCase):
    """The registered role tools with a caller harness and a host whose chat route answers."""

    def setUp(self) -> None:
        super().setUp()
        # L188-B24 (7c1153ee): the answer publishes the dashboard address only when the product's
        # own dashboard owner has been asked and something answers that port.
        self.replace(
            serving_daemon,
            "ensure",
            return_value=types.SimpleNamespace(action="adopted", detail="this world's dashboard"),
        )
        self.replace(dashboard_address_owner, "_origin_answer", return_value=(True, "HTTP 200"))
        self.replace(role_agent_registration, "caller_harness_name", return_value=CALLER_HARNESS)
        self.replace(role_launch_first_run, "prepare_first_run", side_effect=self.prepare_first_run)
        for module in (
            role_launch_routes,
            role_launch_admission,
            role_launch_preparation,
            paseo_role_tools,
        ):
            if hasattr(module, "resolve_role_launch_context"):
                self.replace(
                    module, "resolve_role_launch_context", side_effect=self.free_seat_context
                )
        # The host's own runtime-info answer: the server id its chat route is built from.
        self.replace(paseo_bridge, "bridge_call", self.host)
        authority_runtime(self.config, self.root)

    def prepare_first_run(self, _config: Any) -> dict[str, Any]:
        """The report a cold start hands the receipt, in the shape ``prepare_first_run`` returns.

        The real stage runner sets this call's report and returns it; the receipt builder reads it
        back from the call's own context, so the stub sets it the same way.
        """

        report = {
            "ok": True,
            "stages": [
                {
                    **stage,
                    **(
                        {"root": self.config.coordination_root.as_posix()}
                        if stage["stage"] == "scaffold"
                        else {}
                    ),
                    **(
                        {"path": self.config.config_path.as_posix()}
                        if stage["stage"] == "host-authority"
                        else {}
                    ),
                }
                for stage in FIRST_RUN_STAGES
            ],
        }
        _FIRST_RUN_REPORT.set(report)
        return report

    def free_seat_context(self, config: Any, selection: Any) -> RoleLaunchContext:
        """The launch context of any selection, the free agent's included (no document at all)."""

        if selection.role == "bootstrap":
            return RoleLaunchContext("bootstrap", None, None, None, None)
        return self.context(config, selection)

    def host(self, config: Any, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command == "runtime-info":
            return {"serverId": SERVER_ID}
        return self.runtime(config, command, payload)

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one registered tool call, as a harness reaches it."""

        async def invoke() -> dict[str, Any]:
            server = FastMCP("free-seat-test")
            register_role_agent_tools(server, self.config)
            with tool_call_context(tool, arguments, config=self.config):
                _content, structured = await server.call_tool(tool, arguments)
            return cast(dict[str, Any], structured)

        return asyncio.run(invoke())

    def start(
        self, role: str, request_id: uuid.UUID | None = None, **choices: Any
    ) -> dict[str, Any]:
        """One registered start; the answer the caller receives."""

        return self.call(
            "role_start", {"role": role, "request_id": str(request_id or uuid.uuid4()), **choices}
        )

    def whole_of(self, answer: dict[str, Any]) -> dict[str, Any]:
        """The whole result the answer's detail file keeps."""

        return cast(dict[str, Any], json.loads(Path(answer["log"]).read_text(encoding="utf-8")))

    def created_agents(self) -> list[dict[str, Any]]:
        return [payload for command, payload in self.runtime.calls if command == "agent-create"]


class FreeSeatDeliveryTests(_FreeSeatWorld):
    """MIK-R196 rule 1: the answer names the seat and the place the user continues it."""

    def test_a_bootstrap_start_answers_with_its_seat_and_the_place_it_is_continued(self) -> None:
        answer = self.start("bootstrap")
        created = self.created_agents()

        self.assertEqual(answer["ok"], True, answer)
        self.assertEqual(len(created), 1)
        # The identity the host created, not a reused or invented one.
        self.assertEqual(answer["agentId"], created[0]["agentId"])
        self.assertEqual(
            answer["dashboardAddress"], f"http://127.0.0.1:{self.config.dashboard.port}/"
        )
        self.assertEqual(
            answer["chatAddress"],
            f"http://127.0.0.1:6835/h/{SERVER_ID}/agent/{created[0]['agentId']}",
        )
        # The continuation survives either governed answer form, including a long log path.
        self.assertIn(answer["dashboardAddress"], answer["nextAction"])
        bound = (
            TOOL_ANSWER_OVERFLOW_MAX_TOKENS if answer.get("overflow") else bound_for("role_start")
        )
        self.assertLessEqual(count_response_tokens(answer), bound)
        # The whole result the detail file keeps carries what the cold start established, and the
        # public model still admits exactly the fields the producer emits.
        whole = self.whole_of(answer)
        self.assertEqual(whole["preparation"]["firstRun"]["stages"][0]["state"], "created")
        self.assertEqual(RoleStartResponse.model_validate(whole).agentId, created[0]["agentId"])
        with (
            self.subTest("the model stays strict about a field no producer emits"),
            self.assertRaises(ValueError),
        ):
            RoleStartResponse.model_validate({**whole, "notAProducedField": True})

    def test_a_dashboard_that_does_not_answer_is_reported_instead_of_published(self) -> None:
        """The address is a continuation, so an unserved one is a reason, not a URL (L188-B24)."""

        self.replace(
            serving_daemon,
            "ensure",
            return_value=types.SimpleNamespace(action="failed", detail="nothing came up"),
        )
        self.replace(dashboard_address_owner, "_origin_answer", return_value=(False, "HTTP 503"))

        answer = self.start("bootstrap")

        self.assertEqual(answer["ok"], True, answer)
        self.assertNotIn("dashboardAddress", answer)
        self.assertIn("no dashboard answers", answer["dashboardAddressDetail"])
        # The whole answer the detail file keeps speaks the same truth; the compact answer may
        # trim the note, never turn it back into an address.
        whole = self.whole_of(answer)
        self.assertIn("dashboard could not be started", whole["nextAction"])
        self.assertNotIn("dashboardAddress", whole)
        # The seat is still named: the missing dashboard costs the user the address, not the call.
        self.assertEqual(answer["agentId"], self.created_agents()[0]["agentId"])

    def test_a_repeat_of_the_same_request_reconciles_that_same_seat(self) -> None:
        request_id = uuid.uuid4()
        first = self.start("bootstrap", request_id)
        self.runtime.calls.clear()
        second = self.start("bootstrap", request_id)

        self.assertEqual(
            (second["agentId"], second["chatAddress"], second["dashboardAddress"]),
            (first["agentId"], first["chatAddress"], first["dashboardAddress"]),
        )
        self.assertEqual(self.created_agents(), [], "a repeat creates no second seat")


class FreeSeatOverrideTests(_FreeSeatWorld):
    """MIK-R196 rule 1: a model-only choice binds the caller's own harness."""

    def setUp(self) -> None:
        super().setUp()
        # The user's settings name another harness, so only the caller's own signal can bind codex.
        self.replace(
            role_launch_preparation,
            "_role_defaults",
            return_value=({"agent": "eve", "model": None, "effort": None}, ("eve", "codex")),
        )

    def test_a_model_only_bootstrap_call_binds_the_callers_own_harness(self) -> None:
        request_id = uuid.uuid4()
        answer = self.start("bootstrap", request_id, model="gpt-a")

        self.assertEqual(answer["ok"], True, answer)
        created = self.created_agents()
        self.assertEqual(len(created), 1)
        self.assertEqual((created[0]["provider"], created[0]["model"]), ("codex", "gpt-a"))
        recorded = _read_receipt(
            _receipt_path(
                self.config,
                RoleDispatchRequest.model_validate(
                    {"role": "bootstrap", "requestId": str(request_id)}
                ),
                request_id,
            )
        )
        self.assertIsNotNone(recorded)
        assert recorded is not None
        self.assertEqual(
            recorded["requestedAgentOverride"], {"agentId": "codex", "modelId": "gpt-a"}
        )

    def test_a_model_the_bound_provider_does_not_offer_is_refused_before_the_seat(self) -> None:
        answer = self.start("bootstrap", model="eve-only-model")

        self.assertEqual((answer["ok"], answer["refusal"]), (False, "launch-refused"), answer)
        self.assertIn("does not offer model", answer["detail"])
        self.assertEqual(self.created_agents(), [])
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(self.receipt_files(), [], "a refused start records nothing")

    def test_another_role_still_refuses_a_model_without_its_agent(self) -> None:
        answer = self.start("architect", model="gpt-a")

        self.assertEqual((answer["ok"], answer["refusal"]), (False, "launch-refused"), answer)
        self.assertIn("without the agent it belongs to", answer["detail"])
        self.assertEqual(self.runtime.launch_calls(), [])
        self.assertEqual(self.receipt_files(), [])


class FreeSeatDiscoveryTests(_FreeSeatWorld):
    """MIK-R196 rule 1 through L188-B08: the free seats are found, in pages that end."""

    def test_a_bootstrap_seat_behind_architect_records_is_reached_across_pages(self) -> None:
        bootstrap = self.start("bootstrap")["agentId"]
        architects = [self.start("architect")["agentId"] for _ in range(4)]
        for archived in architects[2:]:
            self.runtime.agents[archived]["archivedAt"] = "today"
        self.runtime.calls.clear()

        first = role_document_chats.document_chats(self.config, RoleSelection(role="architect"), 0)
        self.assertEqual([row["agentId"] for row in first["agents"]], [architects[1]])
        self.assertEqual(first["nextOffset"], 4, "an unlisted seat is a continuation, not an end")

        self.runtime.calls.clear()
        second = role_document_chats.document_chats(
            self.config, RoleSelection(role="architect"), first["nextOffset"]
        )
        self.assertEqual([row["agentId"] for row in second["agents"]], [bootstrap])
        self.assertIsNone(second["nextOffset"], "every eligible seat is listed; the walk ends")
        self.assertLessEqual(
            [command for command, _payload in self.runtime.calls].count("agent-state"),
            3,
            "one page asks the host at most once per visited record",
        )

    def test_an_explicit_bootstrap_selection_answers_with_the_bootstrap_seat(self) -> None:
        bootstrap = self.start("bootstrap")["agentId"]
        self.start("architect")

        answer = role_document_chats.document_chats(self.config, RoleSelection(role="bootstrap"), 0)

        self.assertEqual([row["agentId"] for row in answer["agents"]], [bootstrap])
        self.assertIsNone(answer["nextOffset"])

    def test_an_exact_request_read_answers_that_request_seat_beyond_the_listing(self) -> None:
        bootstrap_request = uuid.uuid4()
        bootstrap = self.start("bootstrap", bootstrap_request)["agentId"]
        for _ in range(4):
            self.start("architect")

        answer = role_document_chats.document_chats(
            self.config, RoleSelection(role="bootstrap"), 0, bootstrap_request
        )

        self.assertEqual([row["agentId"] for row in answer["agents"]], [bootstrap])


def _preparation_refusal(config) -> dict:
    async def invoke():
        server = FastMCP("preparation-refusal-test")
        register_role_agent_tools(server, config)
        arguments = {"role": "bootstrap", "request_id": str(uuid.uuid4())}
        with tool_call_context("role_start", arguments, config=config):
            _content, answer = await server.call_tool("role_start", arguments)
        return cast(dict[str, Any], answer)

    answer = asyncio.run(invoke())
    assert answer["ok"] is False and answer["status"] == "refused", answer
    assert answer["preparationStatus"], answer
    assert "same request id" in answer["nextAction"], answer
    # The governed compact answer points to the complete detail, including a long effect list.
    whole = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
    return RoleStartResponse.model_validate(whole).model_dump()


def test_installer_failure_reports_the_files_already_written(tmp_path, monkeypatch):
    config = load_config(_workspace_config(tmp_path))

    def fail_assets(*args):
        raise PermissionError("the provider assets are read-only")

    monkeypatch.setattr(runtime, "_install_assets", fail_assets)
    payload = _preparation_refusal(config)

    written = list((config.coordination_root / "docs").rglob("*.md"))
    assert written, "the real installer must have written docs before the injected failure"
    assert all(path.as_posix() in payload["detail"] for path in written), payload
    assert "provider assets are read-only" in payload["detail"]
    assert payload["preparationStatus"] == "bootstrap-scaffold-failed"


def test_an_occupied_providers_file_returns_a_refusal_with_partial_effects(tmp_path):
    config = load_config(_workspace_config(tmp_path))
    config.coordination_root.mkdir(parents=True)
    occupied = config.coordination_root / "providers"
    occupied.write_text("keep this file", encoding="utf-8")

    payload = _preparation_refusal(config)

    written = list((config.coordination_root / "docs").rglob("*.md"))
    assert written
    assert occupied.read_text(encoding="utf-8") == "keep this file"
    assert occupied.as_posix() in payload["detail"]
    assert all(path.as_posix() in payload["detail"] for path in written), payload
    assert payload["preparationStatus"] == "bootstrap-scaffold-failed"


def test_a_partially_written_file_is_reported_when_the_copy_raises(tmp_path, monkeypatch):
    config = load_config(_workspace_config(tmp_path))
    touched = []

    def partial_copy(source, destination):
        path = Path(destination)
        path.write_text("a partial copy", encoding="utf-8")
        touched.append(path)
        raise OSError("copy interrupted after its first bytes")

    monkeypatch.setattr(runtime.shutil, "copy2", partial_copy)
    payload = _preparation_refusal(config)

    assert len(touched) == 1
    assert touched[0].read_text(encoding="utf-8") == "a partial copy"
    assert touched[0].as_posix() in payload["detail"], payload
    assert "copy interrupted" in payload["detail"]


def test_a_partial_tree_removal_names_the_deleted_child(tmp_path, monkeypatch):
    config = load_config(_workspace_config(tmp_path))
    scripts = config.coordination_root / "scripts"
    scripts.mkdir(parents=True)
    child = scripts / "old-helper.py"
    child.write_text("old helper", encoding="utf-8")

    def partial_remove(path, target):
        assert path == scripts
        child.unlink()
        raise PermissionError("removal interrupted")

    monkeypatch.setattr(runtime, "_remove_with_retry", partial_remove)
    payload = _preparation_refusal(config)

    assert scripts.is_dir() and not child.exists()
    assert child.as_posix() in payload["detail"]
    assert '"kind": "absent"' in payload["detail"]
    assert "removal interrupted" in payload["detail"]


def test_provision_failure_reports_each_returned_effect_identity(tmp_path, monkeypatch):
    config = load_config(_workspace_config(tmp_path))
    effects = [
        {"step": "plugin", "action": "installed", "path": str(tmp_path / "host/plugin")},
        {"step": "config", "action": "written", "path": str(tmp_path / "host/settings.json")},
    ]
    _stub_host(
        monkeypatch,
        provision={
            "ok": False,
            "changed": True,
            "daemon": {"action": "kept-running"},
            "changes": effects,
            "error": {"code": "install_failed", "step": "install"},
        },
    )

    payload = _preparation_refusal(config)

    assert "kept-running" in payload["detail"]
    for effect in effects:
        assert effect["path"] in payload["detail"], payload
        assert effect["action"] in payload["detail"]


def test_a_later_http_refusal_keeps_successful_preparation_effects(tmp_path, monkeypatch):
    config = load_config(_workspace_config(tmp_path))
    effect = {"step": "plugin", "action": "installed", "path": str(tmp_path / "host/plugin")}
    _stub_host(
        monkeypatch,
        provision={
            "ok": True,
            "changed": True,
            "daemon": {"action": "started"},
            "changes": [effect],
            "restartRequired": [],
        },
    )

    def refuse_model(*args):
        raise HTTPException(status_code=409, detail="the cold catalog refuses this model")

    monkeypatch.setattr(role_launch_routes, "with_caller_harness", refuse_model)
    payload = _preparation_refusal(config)

    assert "the cold catalog refuses this model" in payload["detail"]
    assert config.config_path.as_posix() in payload["detail"], payload
    assert effect["path"] in payload["detail"]
    assert "started" in payload["detail"]
    written = list((config.coordination_root / "docs").rglob("*.md"))
    assert written
    assert all(path.as_posix() in payload["detail"] for path in written), payload
