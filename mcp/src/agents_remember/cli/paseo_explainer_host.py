"""The explainer's seat on the Paseo host: made without a turn, messaged, archived, read.

MIK-R186 rule 5 asks for a seat created with the capsule as its instructions and no first prompt, as
a held role seat is (:mod:`agents_remember.cli.paseo_launch`). The host's ``agent-create`` carries
the tool server (:mod:`agents_remember.canvas.explainer_server`), whose environment names the
workspace with ``AR_EXPLAINER_WORKSPACE`` so Retire can end what the seat started, Chromium included
(rules 3 and 8).

This module is the ``cli`` half: it may call the bridge, and the seat service in ``canvas`` never
does. The service hands in an agent id it derived from the workspace and the generation, so a
create that is repeated after a lost reply reconciles the same host session
(``idempotencyKey``) rather than making a second one.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from agents_remember.application.agent_binding import TOOL_SERVER_NAME
from agents_remember.application.master_architect_record import (
    ExplainerControlHostPorts,
    bind_explainer_control_host,
)
from agents_remember.canvas.explainer_binding import (
    EXPLAINER_BINDING_ENV,
    EXPLAINER_CAPABILITY,
    EXPLAINER_SURFACE,
    validate_explainer_binding,
)
from agents_remember.canvas.explainer_paths import INSTRUCTIONS_DIRECTORY, guard_explainer_path
from agents_remember.canvas.explainer_state import AGENT_MARKER, ExplainerRefused
from agents_remember.canvas.paths import PathRefused
from agents_remember.cli.paseo_bridge import (
    BRIDGE_TIMEOUT,
    SEND_OUTCOME_UNKNOWN,
    PaseoBridgeFailure,
    bridge_call,
    daemon_configuration,
)
from agents_remember.cli.paseo_catalog import LaunchSelectionRefused
from agents_remember.cli.paseo_catalog import settings_role_launch as qualified_launch
from agents_remember.cli.paseo_launch import launching_source_root, tool_server_definition
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel._agentic_settings_core import EXTRA_ARGS_HARNESSES
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo.paseo_daemon_config import (
    CONFIG_FILE,
    host_provider_extra_args,
)

__all__ = ["SERVER_NAME", "PaseoExplainerHost", "explainer_report_sender"]

#: The tool server's name on this seat: the normal AR task server of this build (MIK-R186 @ v2 rule 3).
SERVER_NAME = TOOL_SERVER_NAME
#: The label a seat carries so the host, a curator or a cleanup pass can tell it from any other agent.
CAPABILITY_LABEL = "review-explainer"
#: The one environment name that marks every process of a seat, its children included.
WORKSPACE_MARKER = "AR_EXPLAINER_WORKSPACE"
#: The browser module the seat's publish step loads the page in (a file URL's target, or a module path).
PLAYWRIGHT_ENV = "AR_PLAYWRIGHT"
SEAT_LIVE = "live"
SEAT_CLOSED = "closed"
#: A resume loads the stored session; it is not bounded by an ordinary bridge call's budget.
RESUME_BUDGET_SECONDS = 240.0
SEAT_ARCHIVED = "archived"
SEAT_MISSING = "missing"
PROVENANCE_FILE = "seat-provenance.json"
PROVENANCE_SCHEMA = "ar-explainer-seat-provenance/v1"


@dataclass(frozen=True)
class _ExplainerServerBinding:
    """Adapter supplying the explainer launch environment to :func:`tool_server_definition`."""

    env: dict[str, str]

    def environment(self) -> dict[str, str]:
        return dict(self.env)


def _ran_a_turn(view: dict[str, Any]) -> bool:
    """Whether the host's agent view shows that this seat ever ran a turn (MIK-R105 v2 rule 9)."""

    if view.get("lastUserMessageAt"):
        return True
    last = view.get("lastTurn")
    ended = last.get("state") if isinstance(last, dict) else None
    if ended in {"replied", "unreplied"}:
        return True
    if str(view.get("lastStatus") or "") in {"running", "idle"}:
        return True
    return str(view.get("attentionReason") or "") in {"finished", "failed", "cancelled"}


def _launch_env(
    *,
    agent_id: str,
    workspace: Path,
    binding: Mapping[str, Any],
    coordination_root: Path | None = None,
    config: McpRuntimeConfig | None = None,
) -> dict[str, str]:
    """The one launch environment this capability produces: both launch words, always."""

    env = {
        AGENT_MARKER: agent_id,
        WORKSPACE_MARKER: str(workspace),
        EXPLAINER_BINDING_ENV: json.dumps(binding),
    }
    # The browser the seat's publish step loads the page in. The launch environment is built here and
    # nowhere else, so a host that has the module must hand it on: without this the seat's tool server
    # never sees the variable, `render` refuses for want of a browser nobody withheld, and no page is
    # ever published (the revision stays the previous generation's, silently).
    playwright = browser_module()
    if playwright:
        env[PLAYWRIGHT_ENV] = playwright
    missing = [name for name, value in env.items() if not str(value).strip()]
    if missing:
        raise ExplainerRefused(
            "explainer-launch-incomplete",
            f"an explainer launch writes every launch word; {', '.join(missing)} would be empty",
        )
    if workspace.is_dir():
        validate_explainer_binding(binding, coordination_root=coordination_root, config=config)
    return env


def browser_module() -> str | None:
    """The browser module this installation can hand a seat, or ``None`` when it has none.

    A seat's publish step loads the page in Chromium through a node script that imports
    ``AR_PLAYWRIGHT``. Inheriting the variable is not enough — the host that launches the seat may be
    started without it, and then the seat reaches ``render`` blind: the load refuses, nothing is
    published, and the previous generation's page stays on screen as if it were current. So the
    module is found here, from this installation's own tree, and handed on: the dashboard's
    ``node_modules`` carries it, and the checkout's ``scripts/`` proof script names the same file.
    """

    from_env = os.environ.get(PLAYWRIGHT_ENV)
    if from_env:
        return from_env
    source = launching_source_root()
    for root in source.parents:
        candidate = root / "dashboard" / "node_modules" / "playwright" / "index.js"
        if candidate.is_file():
            return candidate.as_posix()
    return None


def _verify_features(wanted_features: Mapping[str, Any], stated: Mapping[str, Any]) -> None:
    got_features = stated.get("featureValues")
    if not isinstance(got_features, Mapping):
        raise ExplainerRefused(
            "explainer-seat-config-unapplied",
            f"the host did not report featureValues (expected {dict(wanted_features)!r})",
        )
    for feat_key, feat_val in wanted_features.items():
        if feat_key not in got_features or got_features.get(feat_key) != feat_val:
            raise ExplainerRefused(
                "explainer-seat-config-unapplied",
                f"the host launched the seat with featureValues[{feat_key!r}]="
                f"{got_features.get(feat_key)!r}, not {feat_val!r}",
            )


def _stated_agent_fields(agent: Mapping[str, Any]) -> dict[str, Any]:
    raw_info = agent.get("runtimeInfo")
    runtime_info: Mapping[str, Any] = raw_info if isinstance(raw_info, Mapping) else {}
    raw_features = (
        agent.get("featureValues")
        if isinstance(agent.get("featureValues"), Mapping)
        else runtime_info.get("featureValues")
    )
    return {
        "provider": agent.get("provider") or runtime_info.get("provider"),
        "model": agent.get("model") or runtime_info.get("model"),
        "thinkingOptionId": agent.get("thinkingOptionId")
        or agent.get("effectiveThinkingOptionId")
        or runtime_info.get("thinkingOptionId"),
        "modeId": agent.get("lastModeId")
        or agent.get("modeId")
        or agent.get("currentModeId")
        or runtime_info.get("modeId"),
        "featureValues": dict(raw_features) if isinstance(raw_features, Mapping) else None,
    }


def _require_applied(reply: dict[str, Any], create: dict[str, Any]) -> dict[str, Any]:
    """Refuse a seat the host launched on values other than the ones the settings chose."""

    requested_keys = [
        key for key in ("model", "thinkingOptionId", "modeId", "featureValues") if create.get(key)
    ]
    agent = reply.get("agent") if isinstance(reply, dict) else None
    if not isinstance(agent, dict):
        if requested_keys:
            raise ExplainerRefused(
                "explainer-seat-config-unapplied",
                f"the host returned no agent record to verify {', '.join(requested_keys)}",
            )
        return {}
    stated = _stated_agent_fields(agent)
    if (
        create.get("provider")
        and stated.get("provider")
        and str(create["provider"]) != str(stated["provider"])
    ):
        raise ExplainerRefused(
            "explainer-seat-config-unapplied",
            f"the host launched the seat with provider {stated['provider']!r}, "
            f"not {create['provider']!r}",
        )
    for key in ("model", "thinkingOptionId", "modeId"):
        wanted = create.get(key)
        got = stated.get(key)
        if not wanted:
            continue
        if not got:
            raise ExplainerRefused(
                "explainer-seat-config-unapplied",
                f"the host did not report {key} for the launched seat (expected {wanted!r})",
            )
        if str(wanted) != str(got):
            raise ExplainerRefused(
                "explainer-seat-config-unapplied",
                f"the host launched the seat with {key} {got!r}, not the {wanted!r} the settings chose",
            )
    wanted_features = create.get("featureValues")
    if isinstance(wanted_features, Mapping) and wanted_features:
        _verify_features(wanted_features, stated)
    return stated


def _write_provenance(workspace: Path, provenance: Mapping[str, Any]) -> None:
    if not workspace.is_dir() or workspace.is_symlink():
        return
    with suppress(PathRefused, OSError):
        folder = workspace / INSTRUCTIONS_DIRECTORY
        guard_explainer_path(folder, root=workspace.parent, label="explainer instructions")
        folder.mkdir(exist_ok=True)
        target = folder / PROVENANCE_FILE
        guard_explainer_path(target, root=workspace.parent, label="explainer seat provenance")
        atomic_write_text(target, json.dumps(dict(provenance), indent=2, sort_keys=True) + "\n")


def _resume_payload(config: McpRuntimeConfig, agent_id: str, repository_id: str) -> dict[str, Any]:
    """The resume call's payload: the seat's id, plus the settings role's model/effort/mode."""

    payload: dict[str, Any] = {"agentId": agent_id}
    if not repository_id:
        return payload
    try:
        _provider, options = qualified_launch(config, repository_id)
    except LaunchSelectionRefused as refused:
        raise ExplainerRefused("explainer-seat-config", str(refused)) from refused
    for option, native in (
        ("model", "model"),
        ("effort", "thinkingOptionId"),
        ("mode", "modeId"),
    ):
        if options.get(option):
            payload[native] = options[option]
    return payload


def _resume_answer(reply: dict[str, Any]) -> dict[str, Any]:
    """The resume reply's own part, or an empty record for a reply that has none."""

    answer = reply.get("resume")
    return answer if isinstance(answer, dict) else {}


def _merge_stated_launch(agent_view: Mapping[str, Any], stored: dict[str, Any]) -> None:
    """Merge the host's own statement of a resumed seat under the provenance's names."""

    stated = _stated_agent_fields(agent_view)
    for key, value in (
        ("provider", stated.get("provider")),
        ("model", stated.get("model")),
        ("effort", stated.get("thinkingOptionId")),
        ("mode", stated.get("modeId")),
        ("featureValues", stated.get("featureValues")),
    ):
        if value:
            stored.setdefault(key, value)


class PaseoExplainerHost:
    """What the seat service asks of the host, answered through the one bridge."""

    def __init__(self, config: McpRuntimeConfig, *, clock: Any = None) -> None:
        self._config = config
        self._clock = clock
        self._provenance: dict[str, dict[str, Any]] = {}
        self._unknown_sends: set[tuple[str, str]] = set()

    def _call(self, command: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return bridge_call(self._config, command, payload)
        except PaseoBridgeFailure as error:
            raise ExplainerRefused(f"host-{error.code}", str(error)) from error

    def _require_confined_host(self) -> None:
        """Refuse a confined step while the host's provider map gives a harness extra arguments.

        MIK-R192 rule 8: a confined capability runs with exactly its confinement's arguments, at
        its start and at every resume. The host merges its configured options into both and resumes
        a stored seat by itself, so the map is read before each step -- in the file and in the
        running daemon -- for every harness whose provider options carry arguments (the set the
        settings owner keeps). The seat's own harness is the settings role's, resolved by the
        launch; no harness is named here.
        """

        runtime = self._config.paseo_runtime
        if runtime is None:
            return
        try:
            arguments = tuple(
                name
                for harness in sorted(EXTRA_ARGS_HARNESSES)
                for name in host_provider_extra_args(
                    self._config, harness, read_config=daemon_configuration
                )
            )
        except PaseoRuntimeFailure as unread:
            raise ExplainerRefused("explainer-host-map-unread", str(unread)) from unread
        if not arguments:
            return
        raise ExplainerRefused(
            "explainer-host-extra-arguments",
            f"the host's configuration at {runtime.home / CONFIG_FILE} gives extra arguments "
            f"{list(arguments)} to a harness whose provider takes them; a confined explainer never "
            "runs with them. Run runtime_install or `agents-remember paseo provision` to write "
            "AR's provider map back.",
        )

    def create_seat(
        self,
        *,
        agent_id: str,
        workspace: Path,
        title: str,
        capsule: str,
        labels: Any = None,
    ) -> dict[str, Any]:
        """Create the seat with the capsule as its instructions and no first prompt."""

        self._require_confined_host()
        named = dict(labels or {})
        repository_id = str(named.get("ar.repository") or "")
        if not repository_id:
            raise ExplainerRefused(
                "explainer-repository-unknown",
                "a seat names the repository whose capsule it is started with",
            )
        if named.get("ar.capability") != CAPABILITY_LABEL:
            raise ExplainerRefused(
                "explainer-seat-label-refused",
                f"an explainer seat carries ar.capability={CAPABILITY_LABEL!r}, not "
                f"{named.get('ar.capability')!r}",
            )
        try:
            provider, options = qualified_launch(self._config, repository_id)
        except LaunchSelectionRefused as refused:
            # A role setting the runtime cannot honour is the page's answer to name, not a traceback:
            # every explainer route hands the page a refusal and never an internal error.
            raise ExplainerRefused("explainer-seat-config", str(refused)) from refused
        opened = self._call("workspace-open", {"cwd": str(workspace)})
        workspace_id = str((opened.get("workspace") or {}).get("id") or "")
        binding = {
            "capability": EXPLAINER_CAPABILITY,
            "agent": agent_id,
            "request": str(named.get("ar.request") or f"create:{agent_id}"),
            "repository": repository_id,
            "task": str(named.get("ar.task") or ""),
            "leaf": workspace.name,
            "workspace": str(workspace),
            "generation": int(str(named.get("ar.explainer-generation") or 1)),
            "seat": agent_id,
        }
        launch_env = _launch_env(
            agent_id=agent_id,
            workspace=workspace,
            binding=binding,
            coordination_root=self._config.coordination_root,
        )
        server = tool_server_definition(
            self._config.config_path, cast("Any", _ExplainerServerBinding(launch_env))
        )
        create: dict[str, Any] = {
            "agentId": agent_id,
            "idempotencyKey": f"explainer:{agent_id}",
            "workspaceId": workspace_id,
            "provider": provider,
            "title": title,
            "labels": named,
            "mcpServers": {SERVER_NAME: server},
            "toolPolicy": {
                "preapproved": [
                    {"kind": "mcp", "server": SERVER_NAME, "tool": name}
                    for name in EXPLAINER_SURFACE
                ]
            },
            "systemPrompt": capsule,
        }
        for option, native in (
            ("model", "model"),
            ("effort", "thinkingOptionId"),
            ("mode", "modeId"),
            ("featureValues", "featureValues"),
        ):
            if options.get(option):
                create[native] = options[option]
        reply = self._call("agent-create", create)
        stated = _require_applied(reply, create)
        provenance: dict[str, Any] = {
            "schema": PROVENANCE_SCHEMA,
            "agentId": agent_id,
            "workspaceId": workspace_id,
            "repository": repository_id,
            "provider": provider,
            "model": stated.get("model") or options.get("model"),
            "effort": stated.get("thinkingOptionId") or options.get("effort"),
            "mode": stated.get("modeId") or options.get("mode"),
            "featureValues": dict(
                stated.get("featureValues") or options.get("featureValues") or {}
            ),
            "choices": dict(options.get("choices") or {}),
            "extraArgs": dict(options.get("extraArgs") or {}),
            "toolServer": {
                "name": SERVER_NAME,
                "command": [server["command"], *server["args"]],
                "pythonpath": server["env"].get("PYTHONPATH", ""),
                "sourceRoot": launching_source_root().as_posix(),
            },
        }
        self._provenance[agent_id] = provenance
        _write_provenance(workspace, provenance)
        return provenance

    def send(self, agent_id: str, text: str, *, message_id: str | None = None) -> dict[str, Any]:
        self._require_confined_host()
        resolved_id = message_id or str(uuid.uuid4())
        if (agent_id, resolved_id) in self._unknown_sends:
            with suppress(ExplainerRefused):
                present = self._call(
                    "agent-has-message", {"agentId": agent_id, "messageId": resolved_id}
                )
                if present.get("present") is True:
                    self._unknown_sends.discard((agent_id, resolved_id))
                    return {
                        "status": "delivered",
                        "messageId": resolved_id,
                        "reconciled": True,
                    }
        try:
            reply = self._call(
                "agent-send",
                {
                    "agentId": agent_id,
                    "text": f"[developer] {text}",
                    "messageId": resolved_id,
                },
            )
        except ExplainerRefused as error:
            if "send_outcome_unknown" in error.reason or "timeout" in error.reason:
                self._unknown_sends.add((agent_id, resolved_id))
            raise
        self._unknown_sends.discard((agent_id, resolved_id))
        delivery = reply.get("delivery") or {}
        if not delivery.get("delivered"):
            if delivery.get("held") or delivery.get("refused") == "held":
                return {"status": "held", "messageId": resolved_id}
            if delivery.get("permissionPending") is True or delivery.get("refused") in {
                "busy",
                "permission-pending",
            }:
                # MIK-R105 rule 8: the host asks permission. The prompt is preserved for the
                # developer and reported truthfully; the message is held, never answered by a
                # delivery that denies it.
                return {
                    "status": "held",
                    "messageId": resolved_id,
                    "permissionPending": True,
                }
            raise ExplainerRefused(
                "explainer-message-refused",
                str(delivery.get("detail") or "the host did not take the message"),
            )
        return {
            "status": "delivered",
            "messageId": resolved_id,
            "turnId": delivery.get("turnId"),
        }

    def resume(self, agent_id: str) -> dict[str, Any]:
        """Open a closed seat again, without a turn, at the settings role's model/effort/mode.

        Rule 5: a host restart leaves the same seat. The settings role's choices are applied at
        resume as at create (MIK-R105 v3), so the host call carries them under the same names and
        the host's own statement of the resumed seat is checked; a seat the host reports pending a
        permission decision answers that truthfully instead of claiming a resume that did not
        happen.
        """

        self._require_confined_host()
        stored: dict[str, Any] = dict(self._provenance.get(agent_id) or {"agentId": agent_id})
        payload = _resume_payload(self._config, agent_id, str(stored.get("repository") or ""))
        try:
            reply = bridge_call(
                self._config,
                "agent-resume",
                payload,
                timeout_seconds=RESUME_BUDGET_SECONDS,
            )
        except PaseoBridgeFailure as error:
            if self.seat_state(agent_id) != SEAT_LIVE:
                raise ExplainerRefused(f"host-{error.code}", str(error)) from error
            return {**stored, "resumed": True}
        resume_answer = _resume_answer(reply)
        agent_view = reply.get("agent") if isinstance(reply, dict) else None
        if isinstance(agent_view, dict):
            _require_applied({"agent": agent_view}, payload)
            _merge_stated_launch(agent_view, stored)
        if resume_answer.get("permissionPending") is True or (
            resume_answer.get("refusal") == "permission-pending"
        ):
            stored["resumed"] = False
            stored["permissionPending"] = True
            self._provenance[agent_id] = stored
            return stored
        if not resume_answer.get("resumed") and self.seat_state(agent_id) != SEAT_LIVE:
            detail = str(resume_answer.get("refusal") or "the host did not resume it")
            raise ExplainerRefused("explainer-seat-refused", detail)
        stored["resumed"] = True
        self._provenance[agent_id] = stored
        return stored

    def archive(self, agent_id: str) -> None:
        reply = self._call("agent-archive", {"agentId": agent_id})
        if not isinstance(reply, dict):
            raise ExplainerRefused(
                "explainer-archive-failed", f"the host returned no archive reply for {agent_id}"
            )
        if reply.get("found") is False or reply.get("archived") is True:
            return
        if "found" not in reply and "archived" not in reply and not reply.get("reason"):
            return
        raise ExplainerRefused(
            "explainer-archive-failed",
            f"the host did not archive agent {agent_id}: {reply.get('reason') or reply}",
        )

    def seat_state(self, agent_id: str) -> str:
        """Live, closed, archived or missing, from the host's own agent view (rule 5)."""

        view = self._call("agent-state", {"agentId": agent_id}).get("agent")
        if view is None or not isinstance(view, dict):
            return SEAT_MISSING
        if view.get("archivedAt"):
            return SEAT_ARCHIVED
        status = str(view.get("status") or "")
        if status in {"running", "idle"} or view.get("turnActive") is True:
            return SEAT_LIVE
        if status == "closed" and _ran_a_turn(view):
            return SEAT_CLOSED
        return SEAT_MISSING

    def in_turn(self, agent_id: str) -> bool:
        """Whether the seat is in a turn now; fails closed when turn facts are absent (rule 6)."""

        reply = self._call("agent-state", {"agentId": agent_id})
        view = reply.get("agent") if isinstance(reply, dict) else None
        if not isinstance(view, dict):
            return True
        if view.get("archivedAt"):
            return False
        turn_active = view.get("turnActive")
        status = str(view.get("status") or "")
        if turn_active is True or status in {"running", "initializing"}:
            return True
        if status == "closed":
            # A closed agent is not in a turn, whatever the turn field says. The host names a seat
            # `closed` after its turn ends and does not restate `turnActive`; reading that as "facts
            # absent" made every closed seat refuse an Update as `explainer-in-turn` for ever.
            return False
        if "turnActive" not in view or turn_active is not False:
            return True
        return status not in {"idle", "closed"}

    def cancel(self, agent_id: str, *, request_id: str) -> dict[str, Any]:
        """Cancel this exact seat's pending turn at the exact request (MIK-R105 v1 rule 9).

        Cancellation is the daemon's named cancel for this one agent: it never archives or
        replaces the seat, and it changes no launch choice. The answer is the host's own
        after-state with the request identity: whether a turn was running and stopped, whether a
        permission decision was open and is now closed, and whether the host names no such seat
        (the request's outcome then stays uncertain).
        """

        self._require_confined_host()
        stored: dict[str, Any] = dict(self._provenance.get(agent_id) or {"agentId": agent_id})
        reply = self._call("agent-cancel", {"agentId": agent_id, "requestId": request_id})
        view = reply.get("agent") if isinstance(reply, dict) else None
        if not isinstance(view, dict):
            return {
                "status": "cancelled",
                "agentId": agent_id,
                "requestId": request_id,
                "turnStopped": False,
                "requestUncertain": True,
            }
        in_turn_after = view.get("turnActive") is True or str(view.get("status") or "") in {
            "running",
            "initializing",
        }
        pending_after = list(view.get("pendingPermissions") or [])
        # The decision the prompt asked for is closed: nothing stays pending in the host's own
        # record of this seat.
        stored["permissionPending"] = False
        self._provenance[agent_id] = stored
        return {
            "status": "cancelled",
            "agentId": agent_id,
            "requestId": request_id,
            "turnStopped": not in_turn_after,
            "permissionClosed": not pending_after,
            "requestUncertain": False,
            "agent": view,
        }


def explainer_report_sender(config: McpRuntimeConfig) -> Any:
    """The production sender `report_to_architect` delivers through (MIK-R189 rule 7 step 4).

    One sender answers one of the report states for one architect recipient, composed with the
    real bridge and the product's one limit check (MIK-R190 rule 9): a limited recipient or one
    the host's activity record reports in a turn is held before anything is sent; the bridge's
    ``agent-send`` then reports, holds a refused-but-live seat, or answers a terminal outcome. A
    bridge timeout answers ``sent, outcome unknown`` once -- the report log keeps that state and
    nothing is ever re-sent, so nothing is delivered twice (rule 7 step 5).

    The sender is built here, in the ``cli`` half, because it is the half that may call the
    bridge; the report's hold, re-addressing and tray fallback stay with the server's own
    delivery (:mod:`agents_remember.canvas.explainer_server`), which composes this sender.
    """

    def send_report(architect: str, text: str) -> str:
        # Imported on the first call: this module loads on the launching build's path, and the
        # limit and turn readers pull the serving projections only when a report actually goes out.
        from agents_remember.cli.provider_limit_hold import provider_limit_hold  # noqa: PLC0415
        from agents_remember.serving.projections.host_activity import agent_in_turn  # noqa: PLC0415

        limited = provider_limit_hold(config, architect)
        if limited is not None:
            return "held"
        if agent_in_turn(config, architect):
            return "held"
        try:
            reply = bridge_call(config, "agent-send", {"agentId": architect, "text": text})
        except PaseoBridgeFailure as error:
            if error.code in {SEND_OUTCOME_UNKNOWN, BRIDGE_TIMEOUT}:
                return "sent, outcome unknown"
            raise
        delivery = reply.get("delivery") if isinstance(reply.get("delivery"), dict) else reply
        if isinstance(delivery, dict) and delivery.get("delivered") is True:
            return "reported"
        refused = delivery.get("refused") if isinstance(delivery, dict) else None
        if refused in {"busy", "in-turn", "permission-pending", "host-unreachable"}:
            return "held"
        if refused in {"archived", "not-found"}:
            return "not reported"
        raise ExplainerRefused(
            "explainer-report-refused",
            str(delivery.get("detail")) if isinstance(delivery, dict) else str(reply),
        )

    return send_report


bind_explainer_control_host(
    ExplainerControlHostPorts(host_factory=PaseoExplainerHost, bridge_send=bridge_call)
)
