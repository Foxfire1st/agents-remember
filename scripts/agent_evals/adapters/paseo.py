"""Concrete native actor connection using this build's existing Paseo bridge.

These are fixture agents, without AR role labels, role_start or real parent ids.
Construction is read-only. The Architect runs measurements; the Worker may run
explicitly authorized tooling smoke trials. Unit tests use bridge doubles.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agents_remember.cli.paseo_bridge import bridge_call, require_bridge_runtime
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.sidecar_pairing import confine_non_symlink_rel

from ..artifacts import json_digest, read_object, write_object
from .execution import NativeBinding, NativeExecution
from .hermes import hermes_final_stop
from .models import NativeCapture, tool_server
from .stdio_probe import probe_tool_server

BridgeCall = Callable[[str, dict[str, Any]], dict[str, Any]]
PermissionObserver = Callable[[dict[str, Any]], dict[str, Any] | None]


class ActorDeadlineError(AuthorityError):
    """An explicitly bound actor window has no time remaining."""


@dataclass(frozen=True)
class WaitContext:
    agent_id: str
    sent: dict[str, Any]
    deadline: float
    permission_observer: PermissionObserver | None


@dataclass(frozen=True)
class PaseoConnection:
    config: McpRuntimeConfig
    binding: NativeBinding
    run_root: Path
    server_definition: dict[str, Any]
    admitted_batch_home: Path | None = None
    actor_deadline_epoch: float | None = None


@dataclass(frozen=True)
class PaseoRun:
    agent_id: str
    host_counts: dict[str, int]
    applied: dict[str, Any]
    waits: tuple[dict[str, Any], ...]
    capture: NativeCapture | None
    unknowns: tuple[str, ...]


class PaseoNativeAdapter:
    def __init__(
        self,
        connection: PaseoConnection,
        *,
        call: BridgeCall | None = None,
        tool_probe: Callable[[dict[str, Any]], dict[str, Any]] = probe_tool_server,
    ) -> None:
        self.config = connection.config
        self.binding = connection.binding
        self.run_root = connection.run_root
        self.server_definition = connection.server_definition
        self.admitted_batch_home = connection.admitted_batch_home
        self.actor_deadline_epoch = connection.actor_deadline_epoch
        self.call = call or (
            lambda command, payload: bridge_call(connection.config, command, payload)
        )
        self.tool_probe = tool_probe
        self.last_run: PaseoRun | None = None

    def probe(self) -> dict[str, Any]:
        """Read actual authority and host identity before any agent can start."""
        root = self.run_root.resolve(strict=True)
        if self.run_root.is_symlink():
            raise AuthorityError("fixture run root cannot be a symlink")
        binding = self.binding
        self._batch_authority()
        for path in (
            binding.project_root,
            binding.sandbox_home,
            binding.settings_path,
            self.config.coordination_root,
            self.config.workspace_root,
            self.config.transcript_root,
        ):
            if self.admitted_batch_home is not None and path == binding.sandbox_home:
                continue  # Only this explicitly admitted, marker-verified batch home.
            if path.absolute() != root:
                confine_non_symlink_rel(root, os.path.relpath(path.absolute(), root))
        runtime = require_bridge_runtime(self.config)
        if runtime.home.resolve() != binding.sandbox_home.resolve():
            raise AuthorityError("Paseo runtime home differs from fixture sandbox home")
        if self.config.config_path.resolve() != binding.settings_path.resolve():
            raise AuthorityError("Paseo authority differs from bound fixture settings")
        args = self.server_definition.get("args")
        expected = ["-P", "-m", "agents_remember.mcp", "--config", str(binding.settings_path)]
        if self.server_definition.get("type") != "stdio" or args != expected:
            raise AuthorityError(
                "fixture tool-server definition is not this settings-bound AR route"
            )
        self._tool_identity()
        host = self.call("runtime-info", {})
        if not isinstance(host.get("serverId"), str) or not host["serverId"]:
            raise AuthorityError("Paseo host identity was not observed")
        return {
            "serverId": host["serverId"],
            "toolServerDefinitionSha256": json_digest(self.server_definition),
        }

    def _batch_authority(self) -> None:
        home = self.admitted_batch_home
        if home is None:
            return
        if home.is_symlink() or home.resolve(strict=True) != self.binding.sandbox_home.resolve(
            strict=True
        ):
            raise AuthorityError("native home differs from explicitly admitted batch home")
        marker = read_object(home / "agent-eval" / "batch.json")
        if marker.get("schema") != "ar-curator-eval-batch/v1" or marker.get("sandboxHome") != str(
            home.resolve()
        ):
            raise AuthorityError("native batch ownership marker differs from admitted home")
        identity = marker.get("identity")
        try:
            valid = isinstance(identity, str) and str(uuid.UUID(identity)) == identity
        except ValueError:
            valid = False
        if not valid:
            raise AuthorityError("native batch ownership identity is not a canonical UUID")

    def _tool_identity(self) -> None:
        info = self.tool_probe(self.server_definition)
        for field, path in (
            ("configPath", self.config.config_path),
            ("coordinationRoot", self.config.coordination_root),
            ("workspaceRoot", self.config.workspace_root),
        ):
            if info.get(field) != str(path):
                raise AuthorityError(f"actual fixture tool server differs at {field}")
        build = info.get("servingBuild")
        if (
            not isinstance(build, dict)
            or not self.binding.tool_build
            or build.get("sourceDigest") != self.binding.tool_build
        ):
            raise AuthorityError(
                "actual fixture tool server sourceDigest differs from configured trial build"
            )

    def run(
        self,
        *,
        instructions: str,
        prompt: str,
        capture: Callable[[str], NativeCapture],
        timeout_seconds: float = 3600,
        permission_observer: PermissionObserver | None = None,
    ) -> PaseoRun:
        self._remaining_actor_time(timeout_seconds)
        self.probe()
        if timeout_seconds <= 0:
            raise AuthorityError("native measurement timeout must be positive")
        counts = {"before_first_start": self._count()}
        agent_id = str(uuid.uuid4())
        created = False
        applied: dict[str, Any] = {}
        waits: list[dict[str, Any]] = []
        unknowns: list[str] = []
        native: NativeCapture | None = None
        try:
            opened = self.call("workspace-open", {"cwd": str(self.binding.project_root)})
            workspace = opened.get("workspace")
            if not isinstance(workspace, dict) or not isinstance(workspace.get("id"), str):
                raise AuthorityError("Paseo did not open the fixture project")
            create_payload = self._create(agent_id, workspace["id"], instructions)
            reply = self.call("agent-create", create_payload)
            created = reply.get("existing") is False
            if not created:
                raise AuthorityError(
                    "fresh fixture agent id was already present or creation is uncertain"
                )
            applied = reply.get("agent", {})
            if not isinstance(applied, dict):
                raise AuthorityError("native creation returned no applied agent profile")
            self._check_applied(applied, agent_id)
            counts["after_first_start"] = self._count()
            # Replay uses the bridge's own MCP-startup wait and exactly-once first
            # message, rather than race initialization through agent-send.
            bound_prompt = (
                f"{prompt}\n\nNative host seat: {agent_id}; "
                f"provider {self.binding.profile.harness}; project {self.binding.project_root}. "
                "This is the actual actor created for this trial, not another role's seat."
            )
            # CapsuleDelivery's production carrier is the first message. ACP's
            # newSession does not carry systemPrompt; copy the bound bytes here.
            if self.binding.profile.harness == "hermes":
                bound_prompt = f"{instructions}\n\n{bound_prompt}"
            self._remaining_actor_time(timeout_seconds)
            opened_message = self.call("agent-create", {**create_payload, "prompt": bound_prompt})
            repeated_agent = opened_message.get("agent")
            if (
                opened_message.get("existing") is not True
                or not isinstance(repeated_agent, dict)
                or repeated_agent.get("id") != agent_id
            ):
                raise AuthorityError(
                    "native first-message replay did not retain the created fixture actor"
                )
            sent = {"messageId": f"{create_payload['idempotencyKey']}:first-message"}
            deadline = time.monotonic() + self._remaining_actor_time(timeout_seconds)
            self._wait(WaitContext(agent_id, sent, deadline, permission_observer), waits, unknowns)
            native = capture(agent_id)
            self._settle_native_stop(native, waits, unknowns)
        finally:
            if created:
                self.call("agent-archive", {"agentId": agent_id})
            counts["at_end"] = self._count()
            self.last_run = PaseoRun(
                agent_id, counts, applied, tuple(waits), native, tuple(unknowns)
            )
        return self.last_run

    def _settle_native_stop(
        self, native: NativeCapture, waits: list[dict[str, Any]], unknowns: list[str]
    ) -> None:
        # This exact Paseo timeline-window refusal says nothing about the native
        # result. A complete bound Hermes active projection can settle it. No
        # permission, timeout, novel wait reason or unrelated unknown is cleared.
        reason = "native wait unresolved: undecided; permission=None"
        if (
            self.binding.profile.harness != "hermes"
            or native.harness != "hermes"
            or not waits
            or waits[-1].get("state") != "undecided"
            or waits[-1].get("reason")
            != "the message is not among the last 200 timeline entries of the agent"
            or waits[-1].get("permission") is not None
            or any(wait.get("state") not in {"running", "undecided"} for wait in waits)
            or reason not in unknowns
        ):
            return
        proof = hermes_final_stop(native)
        if proof is not None:
            waits[-1]["nativeSettlement"] = proof
            unknowns.remove(reason)

    def execute(
        self,
        *,
        instructions: str,
        prompt: str,
        capture: Callable[[str], NativeCapture],
        timeout_seconds: float = 3600,
        permission_observer: PermissionObserver | None = None,
    ) -> NativeExecution:
        result = self.run(
            instructions=instructions,
            prompt=prompt,
            capture=capture,
            timeout_seconds=timeout_seconds,
            permission_observer=permission_observer,
        )
        stdout = self.run_root / "native.stdout"
        stderr = self.run_root / "native.stderr"
        write_object(stdout, asdict(result))
        stderr.write_text("", encoding="utf-8")
        return NativeExecution(None if result.unknowns else 0, stdout, stderr, result.unknowns)

    def _count(self) -> int:
        reply = self.call("agents-live", {})
        ids = reply.get("agentIds")
        if (
            reply.get("complete") is not True
            or not isinstance(ids, list)
            or not all(isinstance(value, str) for value in ids)
        ):
            raise AuthorityError("complete sandbox host-agent count was not observed")
        return len(ids)

    def host_count(self) -> int:
        """One actual complete listing, including any host-native helpers."""
        return self._count()

    def _remaining_actor_time(self, timeout_seconds: float) -> float:
        if self.actor_deadline_epoch is None:
            return timeout_seconds
        remaining = self.actor_deadline_epoch - time.time()
        if remaining <= 0:
            raise ActorDeadlineError("actor expected end reached")
        return remaining

    def _create(self, agent_id: str, workspace_id: str, instructions: str) -> dict[str, Any]:
        profile = self.binding.profile
        payload: dict[str, Any] = {
            "agentId": agent_id,
            "idempotencyKey": f"eval:{agent_id}",
            "workspaceId": workspace_id,
            "provider": profile.harness,
            "model": profile.model,
            "title": "Curator evaluation fixture",
            "labels": {"agent-eval": agent_id},
            "mcpServers": {"agents-remember-task": self.server_definition},
        }
        if profile.harness == "codex":
            payload["systemPrompt"] = instructions
        if profile.mode is not None:
            payload["modeId"] = profile.mode
        if profile.reasoning is not None:
            payload["thinkingOptionId"] = profile.reasoning
        features: dict[str, Any] = {}
        if profile.service_tier is not None:
            features["service_tier"] = profile.service_tier
        if profile.harness == "hermes":
            features["auto_accept"] = profile.auto_accept
        if features:
            payload["featureValues"] = features
        return payload

    def _check_applied(self, agent: dict[str, Any], agent_id: str) -> None:
        expected = self._create(agent_id, str(agent.get("workspaceId")), "")
        for key in (
            "id",
            "provider",
            "model",
            "cwd",
            "modeId",
            "thinkingOptionId",
            "featureValues",
        ):
            desired = {
                "id": agent_id,
                "cwd": str(self.binding.project_root),
                **expected,
            }.get(key)
            if key == "featureValues" and isinstance(desired, dict):
                actual = agent.get(key)
                if not isinstance(actual, dict) or any(
                    actual.get(name) != value for name, value in desired.items()
                ):
                    raise AuthorityError(
                        "native applied featureValues differs from admitted fixture profile"
                    )
                continue
            if desired is not None and agent.get(key) != desired:
                raise AuthorityError(f"native applied {key} differs from admitted fixture profile")

    def _wait(
        self,
        context: WaitContext,
        waits: list[dict[str, Any]],
        unknowns: list[str],
    ) -> None:
        turn_id = context.sent.get("turnId")
        while time.monotonic() < context.deadline:
            payload: dict[str, Any] = {
                "agentId": context.agent_id,
                "waitMs": max(1, min(1000, int((context.deadline - time.monotonic()) * 1000))),
            }
            if turn_id:
                payload["turnId"] = turn_id
            if context.sent.get("messageId"):
                payload["messageId"] = context.sent["messageId"]
            reply = self.call("agent-wait", payload)
            wait = reply.get("wait")
            if not isinstance(wait, dict):
                unknowns.append("native wait capture absent")
                return
            waits.append(wait)
            if wait.get("state") == "running":
                turn_id = wait.get("turnId") or turn_id
                continue
            if wait.get("state") == "permission" and self._observe_permission(context, wait):
                continue
            if wait.get("state") != "ended":
                unknowns.append(
                    f"native wait unresolved: {wait.get('state')}; "
                    f"permission={wait.get('permission')!r}"
                )
            elif wait.get("outcome") != "finished":
                unknowns.append(f"native run did not finish: {wait.get('outcome')}")
            return
        pending = waits[-1].get("permission") if waits else None
        if not waits:
            waits.append({"state": "deadline"})
        waits[-1]["actorDeadlineReached"] = True
        unknowns.append(f"native measurement timed out; pending permission={pending!r}")

    def _observe_permission(self, context: WaitContext, wait: dict[str, Any]) -> bool:
        if self._answered_permission(context, wait):
            return True
        permission = wait.get("permission")
        if not isinstance(permission, str) or tool_server(permission) not in (
            "agents-remember-task",
            "agents-remember",
        ):
            return False
        # The external sandbox approver alone may answer. Observe its native
        # state transition without answering or delivering a message.
        time.sleep(min(0.1, max(0, context.deadline - time.monotonic())))
        return True

    def _answered_permission(self, context: WaitContext, wait: dict[str, Any]) -> bool:
        """Observe the sandbox approver; this adapter grants no permission itself."""
        if (
            self.binding.profile.harness not in ("codex", "hermes")
            or context.permission_observer is None
        ):
            return False
        observed = context.permission_observer({"agentId": context.agent_id, **wait})
        if not isinstance(observed, dict):
            return False
        server = observed.get("server")
        answered = server in ("agents-remember-task", "agents-remember") and observed == {
            "agentId": context.agent_id,
            "provider": self.binding.profile.harness,
            "server": server,
            "sandboxHome": str(self.binding.sandbox_home),
            "permission": wait.get("permission"),
            "answered": True,
        }
        if answered:
            wait["sandboxApproverObservation"] = observed
        return answered
