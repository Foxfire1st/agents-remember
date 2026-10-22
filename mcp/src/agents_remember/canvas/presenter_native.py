"""Real presenter sessions on the configured host, outside the AR role/acceptance seats.

The Claude launch has only a dedicated, confined tool server. The host removes built-in tools
and inherited MCP servers; the server itself admits exact input bytes and writes only the prepared
presentation folder. No deterministic page composer is selected by production.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.canvas.paths import presenter_root, task_root
from agents_remember.canvas.presenter_files import write_output
from agents_remember.canvas.presenter_host import HostIdentity, HostReceipt, HostStatus, HostTurn
from agents_remember.canvas.presenter_store import (
    MAX_RECORD_BYTES,
    PresenterAttempt,
    PresenterRefused,
    RunnerReceipt,
    StartOutcomeUnknown,
    digest_bytes,
    read_bounded,
)
from agents_remember.cli.paseo_bridge import (
    PaseoBridgeFailure,
    bridge_call,
    daemon_configuration,
)
from agents_remember.cli.paseo_catalog import read_host_role, resolve_agent_selection
from agents_remember.cli.role_instructions import launch_choice_metadata
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo.paseo_daemon_config import (
    CONFIG_FILE,
    host_provider_extra_args,
)

TOOL_NAMES = (
    "inputs",
    "read_input",
    "source",
    "assets",
    "write_asset",
    "carry_kit",
    "documentation",
    "render",
)


def qualified_launch(config: McpRuntimeConfig, repository_id: str) -> tuple[str, dict[str, Any]]:
    repository = config.repositories[repository_id]
    settings = load_agentic_settings(config.coordination_root, repo_root=repository.path)
    knobs = settings.resolved_role_knobs("review-presenter")
    defaults = {
        "agent": knobs.harness or settings.spawn_harness,
        "model": knobs.model,
        "effort": knobs.effort,
        "serviceTier": knobs.service_tier,
        "mode": knobs.mode,
        "featureValues": dict(knobs.feature_values),
        "notes": knobs.notes,
        "role": "review-presenter",
        "notesKey": "orchestration.roles.review-presenter.notes",
        "hostRole": read_host_role(config, "review-presenter"),
        "settingsSources": [str(path) for path in settings.sources],
        "fieldOrigins": {key: asdict(value) for key, value in knobs.origins.items()},
        "useHostDefaultMode": True,
    }
    return resolve_agent_selection(config, defaults, settings.harness_ids, None)


class NativePresenterHarness:
    def __init__(self, config: McpRuntimeConfig, *, identity: HostIdentity, clock: Any = None):
        self._config = config
        self._identity = identity
        self._clock = clock or (lambda: datetime.now(UTC).isoformat())

    def _call(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return bridge_call(self._config, command, payload)
        except PaseoBridgeFailure as error:
            if command == "agent-create" and error.code in {
                "paseo_bridge_timeout",
                "paseo_bridge_invalid_reply",
                "paseo_daemon_unreachable",
                "paseo_send_outcome_unknown",
            }:
                raise StartOutcomeUnknown(error.code, str(error)) from error
            raise PresenterRefused(error.code, str(error)) from error

    def start(self, attempt: PresenterAttempt, instructions: str, working: Path) -> RunnerReceipt:
        self._require_confined_host()
        try:
            provider, options = qualified_launch(self._config, attempt.repository_id)
        except PaseoBridgeFailure as error:
            raise PresenterRefused(error.code, str(error)) from error
        if provider != "claude":
            raise PresenterRefused(
                "presenter-confinement-unavailable",
                f"the selected provider {provider!r} has no qualified presenter tool boundary",
            )
        evidence = presenter_root(task_root(self._config, attempt.repository_id, attempt.task))
        evidence = (
            evidence / attempt.leaf_id / attempt.frame_id / f"{attempt.attempt_id}.evidence.json"
        )
        payload = read_bounded(
            evidence, MAX_RECORD_BYTES, reason="evidence-too-large", detail="evidence"
        )
        if digest_bytes(payload) != attempt.evidence_digest:
            raise PresenterRefused(
                "evidence-changed", "the admitted packet changed before host start"
            )
        write_output(working, "host/assignment.txt", instructions.encode())
        write_output(working, "host/evidence.json", payload)
        grants = {
            "working": str(working),
            "attempt": attempt.attempt_id,
            "inputs": {
                "evidence.json": digest_bytes(payload),
                "assignment.txt": digest_bytes(instructions.encode()),
            },
        }
        write_output(working, "host/grants.json", json.dumps(grants).encode())
        workspace = self._call("workspace-open", {"cwd": str(working)})
        # Stable identity: a lost create reply reconciles the same host session on retry/restart.
        agent_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(working)))
        server = {
            "type": "stdio",
            "command": sys.executable,
            "args": [
                "-P",
                "-m",
                "agents_remember.canvas.presenter_tools",
                "--working",
                str(working),
            ],
            "env": {
                key: value
                for key, value in os.environ.items()
                if key.startswith("XDG_")
                or key in {"PYTHONPYCACHEPREFIX", "AR_CANVAS_PROSPECTIVE_LOAD", "AR_PLAYWRIGHT"}
            },
        }
        create = {
            "agentId": agent_id,
            "idempotencyKey": f"review-presenter:{agent_id}",
            "workspaceId": workspace["workspace"]["id"],
            "provider": provider,
            "title": f"Review presenter · {attempt.leaf_id}",
            "labels": {
                "ar.capability": "review-presenter",
                "ar.presenter-attempt": attempt.attempt_id,
            },
            "mcpServers": {"review-presenter": server},
            "toolPolicy": {
                "preapproved": [
                    {"kind": "mcp", "server": "review-presenter", "tool": name}
                    for name in TOOL_NAMES
                ]
            },
            "systemPrompt": "Use only your review-presenter tools. This is a presentation capability, not an AR role. Read assignment.txt and evidence.json through read_input before authoring. Documentation is not evidence.",
            "prompt": f"Author the requested review page. Focus: {attempt.focus}. Read both admitted inputs. Use assets to inspect any retained starting page before revising it. Write assets/index.html and any local assets, and render the exact draft. Explain what you inspected and the result in this conversation. Do not publish or change review/acceptance state.",
        }
        for option, native in (
            ("model", "model"),
            ("effort", "thinkingOptionId"),
            ("mode", "modeId"),
            ("featureValues", "featureValues"),
        ):
            if options.get(option):
                create[native] = options[option]
        prepared = {
            "create": create,
            "provenance": {
                "choices": launch_choice_metadata(options["choices"]),
                "permissionBoundary": {
                    "provider": provider,
                    "builtInTools": [],
                    "inheritedMcpServers": False,
                    "toolServer": server,
                    "readGrants": grants["inputs"],
                    "writeRoot": str(working),
                },
            },
        }
        write_output(working, "host/prepared-start.json", json.dumps(prepared).encode())
        return self.reconcile(attempt, working)

    def reconcile(self, attempt: PresenterAttempt, working: Path) -> RunnerReceipt:
        """Repeat the immutable native create, with no settings reread or new identity."""
        self._require_confined_host()
        prepared = json.loads(
            read_bounded(
                working / "host/prepared-start.json",
                MAX_RECORD_BYTES,
                reason="prepared-start-too-large",
                detail="the original native start request",
            )
        )
        create = prepared["create"]
        if create["labels"]["ar.presenter-attempt"] != attempt.attempt_id:
            raise PresenterRefused("prepared-start-conflict", "the request names another attempt")
        reply = self._call("agent-create", create)
        return RunnerReceipt(
            execution_id=create["agentId"],
            session_id=create["agentId"],
            model=reply["agent"].get("model"),
            detail="real host presenter reconciled with confined tools; no publication implied",
            provenance={
                **prepared["provenance"],
                "observed": reply["agent"],
                "hostServerId": reply["serverId"],
            },
        )

    def _receipt(self, session_id: str, reply: dict[str, Any], detail: str) -> HostReceipt:
        return HostReceipt(
            session_id=session_id,
            message_id=str(uuid.uuid4()),
            state=str(reply.get("agent", {}).get("status", "unknown")),
            detail=detail,
            execution_id=session_id,
        )

    def _require_confined_host(self) -> None:
        """Refuse a confined step while the host's provider map gives Claude extra arguments.

        A confined seat runs with exactly the arguments of its confinement, at its start and at
        every resume (MIK-R192 rule 8). The host merges its configured options into both, and it
        resumes a stored seat by itself when its chat is shown, which AR cannot check; so the map
        is read here, in the file and in the running daemon, before the agent runs.
        """
        runtime = self._config.paseo_runtime
        if runtime is None:
            return
        try:
            arguments = host_provider_extra_args(
                self._config, "claude", read_config=daemon_configuration
            )
        except PaseoRuntimeFailure as unread:
            # A map that was not read is not a clean one: the step refuses before the host is
            # asked, as it does for a map that names arguments (MIK-R192 rule 8).
            raise PresenterRefused("presenter-host-map-unread", str(unread)) from unread
        if not arguments:
            return
        raise PresenterRefused(
            "presenter-host-extra-arguments",
            f"the host's configuration at {runtime.home / CONFIG_FILE} gives Claude the extra "
            f"arguments {list(arguments)}; a confined presenter never runs with them. Run "
            "runtime_install or `agents-remember paseo provision` to write AR's provider map back.",
        )

    def message(self, session_id: str, *, role: str, text: str) -> HostReceipt:
        self._require_confined_host()
        lane = "system-owner" if role == "system-owner" else "developer"
        message_id = str(uuid.uuid4())
        reply = self._call(
            "agent-send",
            {"agentId": session_id, "text": f"[{lane}] {text}", "messageId": message_id},
        )
        if not reply["delivery"]["delivered"]:
            raise PresenterRefused("presenter-message-refused", reply["delivery"]["detail"])
        return HostReceipt(session_id, message_id, "running", "the host took the conversation turn")

    def status(self, session_id: str) -> HostStatus:
        view = self._call("agent-state", {"agentId": session_id})["agent"]
        if view is None:
            raise PresenterRefused("presenter-session-missing", session_id)
        return HostStatus(
            session_id,
            "",
            view["status"],
            None,
            session_id,
            None,
            self._clock(),
            json.dumps(view),
            bool(view["archivedAt"]),
        )

    def transcript(self, session_id: str) -> tuple[HostTurn, ...]:
        reply = self._call("presenter-transcript", {"agentId": session_id})
        if reply.get("available") is False:
            raise PresenterRefused("presenter-transcript-unavailable", reply["detail"])
        return tuple(
            HostTurn(
                row["at"],
                "system-owner" if row["text"].startswith("[system-owner]") else row["role"],
                "system-owner" if row["text"].startswith("[system-owner]") else "developer",
                row["text"],
            )
            for row in reply["entries"]
        )

    def resume(self, session_id: str) -> HostReceipt:
        """Resume the stored seat, at the confinement its start ran with (MIK-R192 rule 8).

        The host lays its configured provider options over a resume exactly as over a start, and
        resumes the seat with them; so the map is read here, before any host call, as `start`,
        `reconcile` and `message` read it. A refusal leaves the session as it was.
        """
        self._require_confined_host()
        reply = self._call("agent-resume", {"agentId": session_id})
        if reply.get("agent") is None or reply["agent"].get("archivedAt"):
            raise PresenterRefused(
                "presenter-session-unavailable", "the exact session is missing or archived"
            )
        return self._receipt(session_id, reply, json.dumps(reply["resume"]))

    def cancel(self, session_id: str, *, reason: str) -> HostReceipt:
        reply = self._call("presenter-cancel", {"agentId": session_id})
        return self._receipt(session_id, reply, f"host cancellation acknowledged: {reason}")

    def archive(self, session_id: str) -> HostReceipt:
        reply = self._call("agent-archive", {"agentId": session_id})
        return self._receipt(session_id, reply, "host archive acknowledged")
