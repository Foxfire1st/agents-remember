"""The explainer's seat on the host: its instructions, its tool policy and its confinement.

MIK-R186 rule 5 creates the seat with the capsule as its instructions and with no first prompt;
rule 3's tool policy must preapprove exactly the tools the seat's own server declares, or a call the
server admits is refused at the host; and MIK-R192 rule 8 refuses a confined step while the host's
provider map gives Claude extra arguments, read in the file and in the running daemon.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.canvas import explainer_binding, explainer_server
from agents_remember.canvas.explainer_binding import EXPLAINER_SURFACE
from agents_remember.canvas.explainer_capsule import capability_text
from agents_remember.canvas.explainer_inputs import ExplainerInputs
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    ExplainerRefused,
    agent_id,
    write_binding,
)
from agents_remember.cli import developer_message_files as developer_messages
from agents_remember.cli import paseo_explainer_host as host_module
from agents_remember.cli import provider_limit_hold as limit_module
from agents_remember.cli.paseo_bridge import SEND_OUTCOME_UNKNOWN, PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import LaunchSelectionRefused
from agents_remember.cli.paseo_explainer_host import PaseoExplainerHost, explainer_report_sender
from agents_remember.cli.role_held_messages import (
    MAX_HELD_PER_RECIPIENT,
    HeldMessage,
    hold_message,
    waiting_total,
)
from agents_remember.cli.role_message_files import (
    MessageContent,
    render_message_file,
    store_message_file,
)
from agents_remember.models.knowledge.review_frame import FrameBinding

SERVER_NAME = "agents-remember-task"
from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.mcp.registration.explainer_tools import (
    register_bound_explainer_tools,  # the entry
)
from canvas_host_test_support import REPO, TASK, config, packet, record
from mcp.server.fastmcp import FastMCP

LEAF = "260928-MIK-L183"
CAPSULE = "You are this leaf's review explainer.\n"
NOW = "2026-10-22T08:00:00+00:00"


def _text_of(result: object) -> str:
    """One ``call_tool`` answer's text content, for the JSON bodies these tests assert on."""

    if isinstance(result, tuple):
        result = result[0]
    blocks = result if isinstance(result, list) else getattr(result, "content", None)
    if isinstance(blocks, list):
        for block in blocks:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                return text
    return str(result)


def _workspace(tmp_path: Path) -> Path:
    """A path of the shape the server reads its coordination root back from."""

    workspace = tmp_path / "explainer" / REPO / TASK / LEAF
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _host(tmp_path: Path, *, bridge) -> PaseoExplainerHost:
    runtime = config(tmp_path)
    return PaseoExplainerHost(runtime)


def test_the_seat_is_created_with_the_capsule_and_no_first_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 5: the capsule is the seat's instructions, and the seat is left idle."""

    calls: list[tuple[str, dict]] = []

    def bridge(_config, command, payload):
        calls.append((command, payload))
        return {"workspace": {"id": "w1"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("claude", {}))
    host = _host(tmp_path, bridge=bridge)

    host.create_seat(
        agent_id="agent-1",
        workspace=_workspace(tmp_path),
        title="Explainer · " + LEAF,
        capsule=CAPSULE,
        labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
    )

    created = dict(calls)["agent-create"]
    assert created["systemPrompt"] == CAPSULE
    assert "prompt" not in created
    assert getattr(host_module, "SERVER_NAME", None) == "agents-remember-task"
    env = created["mcpServers"][SERVER_NAME]["env"]
    # MIK-R186 @ v2 rule 3: the marker stays as the retirement scanner's cleanup handle, and the launch
    # binding is what supplies the server its authority.
    assert env["PASEO_AGENT_ID"] == "agent-1"
    assert env["AR_EXPLAINER_WORKSPACE"] == str(_workspace(tmp_path))
    assert "PYTHONPATH" in env
    binding = json.loads(env["AR_EXPLAINER_BINDING"])
    assert binding["capability"] == "explainer"
    assert binding["seat"] == "agent-1" and binding["agent"] == "agent-1"
    assert binding["repository"] == REPO and binding["task"] == TASK
    assert binding["leaf"] == LEAF and binding["workspace"] == str(_workspace(tmp_path))
    # One server, and it is the normal AR task server of this build -- not a module of its own.
    args = created["mcpServers"][SERVER_NAME]["args"]
    # The canonical seat spelling (`cli/paseo_launch.py`), so a seat is the same shape everywhere.
    assert args[:2] == ["-P", "-m"] and args[2] == "agents_remember.mcp"
    assert "agents_remember.canvas.explainer_server" not in args
    assert "--config" in args


def test_the_policy_preapproves_exactly_the_tools_the_server_declares(tmp_path: Path) -> None:
    """Rule 3: a tool the server admits is a tool the host lets the seat call, and no other."""

    server = FastMCP("review-explainer")
    register_bound_explainer_tools(server, workspace=_workspace(tmp_path))
    declared = {tool.name for tool in asyncio.run(server.list_tools())}

    assert declared == set(EXPLAINER_SURFACE)
    # The six read-only Git subcommands are named one by one, never as one "git" tool.
    assert {"git_log", "git_show", "git_diff", "git_ls_tree", "git_cat_file", "git_rev_parse"} <= (
        declared
    )
    assert "knowledge_read" in declared


def test_the_seat_is_instructed_with_the_capability_its_loader_admits() -> None:
    """Rule 12: the instructions are the admitted capability text, not a paraphrase."""

    text = capability_text()

    assert text.startswith("# Review explainer")
    assert "You are not the Reviewer" in text
    assert "You write only in your working folder." in text


def _configured_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, extra: bool) -> None:
    """A host whose daemon home holds a config.json, optionally giving Claude extra arguments."""

    home = tmp_path / "paseo-home"
    home.mkdir()
    options = {"extraArgs": {"chrome": None}} if extra else {}
    (home / "config.json").write_text(
        json.dumps({"agents": {"providers": {"claude": {"options": options}}}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        type(config(tmp_path)),
        "paseo_runtime",
        property(lambda _self: SimpleNamespace(home=home)),
    )
    monkeypatch.setattr(host_module, "daemon_configuration", lambda _config: {"providers": {}})


def test_a_host_map_with_extra_arguments_refuses_the_confined_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R192 rule 8: a confined seat never runs with the host's extra arguments."""

    _configured_host(tmp_path, monkeypatch, extra=True)
    host = _host(tmp_path, bridge=lambda *_args: {})

    with pytest.raises(ExplainerRefused) as refused:
        host.create_seat(
            agent_id="agent-1",
            workspace=_workspace(tmp_path),
            title="t",
            capsule=CAPSULE,
            labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
        )

    assert refused.value.reason == "explainer-host-extra-arguments"
    assert "chrome" in refused.value.detail


def test_a_map_that_cannot_be_read_refuses_the_confined_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R192 rule 8: a map that was not read is not a clean one."""

    _configured_host(tmp_path, monkeypatch, extra=False)

    def unread(_config):
        raise PaseoRuntimeFailure("daemon-config-unverified", "config", "the daemon did not answer")

    monkeypatch.setattr(host_module, "daemon_configuration", unread)
    host = _host(tmp_path, bridge=lambda *_args: {})

    with pytest.raises(ExplainerRefused) as refused:
        host.send("agent-1", "explain this")

    assert refused.value.reason == "explainer-host-map-unread"


def test_the_seat_state_reads_the_hosts_own_agent_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 5: live, closed, archived and never-ran are read from `turnActive` and `lastTurn`.

    The bridge's agent view names the turn `turnActive` and states how the last turn ended; a seat
    read by any other field name would report a running seat as missing, and an Update would never
    see a turn in progress.
    """

    views = {
        "running": {"id": "a", "status": "running", "archivedAt": None, "turnActive": True},
        # Open, no turn: the host keeps the session open and the page shows it as live.
        "idle": {
            "id": "a",
            "status": "idle",
            "archivedAt": None,
            "turnActive": False,
            "lastTurn": {"state": "replied", "text": "done"},
        },
        # A closed session that ran a turn: the bridge reads no `lastTurn` for a closed agent, so the
        # host's attention reason is the witness that it ran one.
        "closed": {
            "id": "a",
            "status": "closed",
            "archivedAt": None,
            "turnActive": False,
            "attentionReason": "finished",
            "lastTurn": None,
        },
        # Closed with no evidence a turn ever ran: rule 5 counts it as missing.
        "never": {
            "id": "a",
            "status": "closed",
            "archivedAt": None,
            "turnActive": False,
            "attentionReason": None,
            "lastUserMessageAt": None,
            "lastTurn": None,
        },
        # The shape the sandbox host actually published after a restart: closed, attention null,
        # no lastTurn (the bridge reads that only for an idle agent), and the record's own stamp.
        "closed-with-the-record-stamp": {
            "id": "a",
            "status": "closed",
            "archivedAt": None,
            "turnActive": False,
            "attentionReason": None,
            "lastUserMessageAt": "2026-10-22T11:40:12.000Z",
            "lastTurn": None,
        },
        "archived": {"id": "a", "status": "idle", "archivedAt": "2026-10-22T10:00:00Z"},
        "absent": None,
    }
    seen: dict[str, str] = {}

    def bridge(_config, _command, payload):
        return {"agent": views.get(payload["agentId"])}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    host = _host(tmp_path, bridge=bridge)
    expected = {
        "running": "live",
        "idle": "live",
        "closed": "closed",
        "never": "missing",
        "closed-with-the-record-stamp": "closed",
        "archived": "archived",
        "absent": "missing",
    }
    for name in expected:
        seen[name] = host.seat_state(name)
    assert seen == expected
    assert host.in_turn("running") is True
    assert host.in_turn("closed") is False


def test_the_settings_role_owns_the_seats_model_effort_and_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R105 v2 rule 9: the role's model, effort and permission mode reach the create, and hold.

    A seat launched on the provider's defaults is not the seat the settings describe, so the values
    are carried into the create under the host's own names and the reply is read back: a host that
    states a different model or mode is refused rather than silently obeyed.
    """

    calls: list[tuple[str, dict]] = []

    def bridge(_config, command, payload):
        calls.append((command, payload))
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        return {
            "agent": {
                "id": "agent-1",
                "provider": "claude",
                "model": "claude-opus-5-5",
                "thinkingOptionId": "high",
                "lastModeId": "auto-review",
                "featureValues": {"service_tier": "fast"},
            }
        }

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(
        host_module,
        "qualified_launch",
        lambda _config, _repo: (
            "claude",
            {
                "model": "claude-opus-5-5",
                "effort": "high",
                "mode": "auto-review",
                "featureValues": {"service_tier": "fast"},
                "choices": {"model": {"value": "claude-opus-5-5", "source": "our settings"}},
            },
        ),
    )
    host = _host(tmp_path, bridge=bridge)

    provenance = host.create_seat(
        agent_id="agent-1",
        workspace=_workspace(tmp_path),
        title="Explainer · " + LEAF,
        capsule=CAPSULE,
        labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
    )

    created = dict(calls)["agent-create"]
    assert created["provider"] == "claude"
    assert created["model"] == "claude-opus-5-5"
    assert created["thinkingOptionId"] == "high"
    assert created["modeId"] == "auto-review"
    assert created["featureValues"] == {"service_tier": "fast"}
    assert provenance["provider"] == "claude"
    assert provenance["model"] == "claude-opus-5-5"
    assert provenance["effort"] == "high"
    assert provenance["mode"] == "auto-review"
    assert provenance["featureValues"] == {"service_tier": "fast"}
    assert (_workspace(tmp_path) / "instructions" / "seat-provenance.json").is_file()


def test_a_role_setting_the_runtime_cannot_honour_is_a_named_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R106 rule 11: a page is answered with a refusal, never with an internal error.

    The role's own launch choices are resolved against the host's catalog. When that catalog does not
    offer the harness or model the settings name, the answer is the page's to show: the seat step
    raises the refusal the routes render, so the chat column names the setting instead of a 500.
    """

    def refuse(_config, _repo):
        raise LaunchSelectionRefused(
            "launch-selection-refused",
            "Role review-presenter: the Paseo runtime does not offer model 'x' for agent 'hermes'.",
        )

    monkeypatch.setattr(host_module, "qualified_launch", refuse)
    host = _host(tmp_path, bridge=lambda _config, _command, _payload: {})

    with pytest.raises(ExplainerRefused) as refused:
        host.create_seat(
            agent_id="agent-1",
            workspace=_workspace(tmp_path),
            title="Explainer · " + LEAF,
            capsule=CAPSULE,
            labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
        )

    assert refused.value.reason == "explainer-seat-config"
    assert "does not offer model" in refused.value.detail


def test_a_seat_the_host_launched_on_other_values_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 9: the host's own statement of what it created is the check, not the request."""

    def bridge(_config, command, payload):
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        return {"agent": {"id": "agent-1", "model": "claude-opus-5", "lastModeId": "auto"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(
        host_module,
        "qualified_launch",
        lambda _config, _repo: ("claude", {"model": "claude-opus-5-5", "mode": "auto-review"}),
    )
    host = _host(tmp_path, bridge=bridge)

    with pytest.raises(ExplainerRefused) as refused:
        host.create_seat(
            agent_id="agent-1",
            workspace=_workspace(tmp_path),
            title="Explainer · " + LEAF,
            capsule=CAPSULE,
            labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
        )
    assert refused.value.reason == "explainer-seat-config-unapplied"
    assert "claude-opus-5-5" in refused.value.detail


def test_a_seat_without_the_explainer_label_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R105 v2 rule 9: the capability label is what tells this seat from any other agent."""

    def bridge(_config, command, payload):
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        return {"agent": {"id": "agent-1"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("claude", {}))
    host = _host(tmp_path, bridge=bridge)

    with pytest.raises(ExplainerRefused) as refused:
        host.create_seat(
            agent_id="agent-1",
            workspace=_workspace(tmp_path),
            title="Explainer · " + LEAF,
            capsule=CAPSULE,
            # A seat like any other: the repository it works in, and no explainer label.
            labels={"ar.repository": REPO, "ar.task": TASK},
        )
    assert refused.value.reason == "explainer-seat-label-refused"


def test_any_harness_the_settings_name_is_launched_without_a_provider_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R105 @ v4: the harness is the settings role's, and no harness is admitted or refused here.

    The boundary this product enforces is the bound server's surface, so a seat is created for whatever
    `review-presenter` names -- Codex included -- and what that harness brings itself is a D154 note.
    """

    calls: list[tuple[str, dict]] = []

    def bridge(_config, command, payload):
        calls.append((command, payload))
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        return {"agent": {"id": payload.get("agentId"), "provider": "codex"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("codex", {}))
    host = _host(tmp_path, bridge=bridge)

    host.create_seat(
        agent_id="agent-1",
        workspace=_workspace(tmp_path),
        title="Explainer · " + LEAF,
        capsule=CAPSULE,
        labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
    )

    assert dict(calls)["agent-create"]["provider"] == "codex"


def test_a_real_explainer_launch_always_carries_both_launch_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guarantee behind the refused-lost-binding rule, proved at the end that produces it.

    A seat with neither launch word takes the ordinary branch by design, so the safety of that branch
    rests entirely on every explainer launch writing the marker. This builds a seat through the real
    launcher and reads what the host would actually spawn.
    """

    calls: list[tuple[str, dict]] = []

    def bridge(_config, command, payload):
        calls.append((command, payload))
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        return {"agent": {"id": payload.get("agentId"), "provider": "codex"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("codex", {}))
    host = _host(tmp_path, bridge=bridge)

    host.create_seat(
        agent_id="agent-1",
        workspace=_workspace(tmp_path),
        title="Explainer · " + LEAF,
        capsule=CAPSULE,
        labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
    )

    env = dict(calls)["agent-create"]["mcpServers"][SERVER_NAME]["env"]
    # Both words, always: the authority, and the marker that makes a lost authority readable.
    assert env["AR_EXPLAINER_BINDING"].strip(), (
        "a launch without its binding would take the ordinary branch"
    )
    assert env["AR_EXPLAINER_WORKSPACE"].strip(), "a launch without the marker hides a lost binding"
    assert json.loads(env["AR_EXPLAINER_BINDING"])["workspace"] == env["AR_EXPLAINER_WORKSPACE"]


def test_the_launch_environment_builder_refuses_to_leave_a_word_empty(tmp_path: Path) -> None:
    """One place writes those words, and it will not emit an incomplete environment."""

    with pytest.raises(host_module.ExplainerRefused) as refused:
        host_module._launch_env(
            agent_id="",
            workspace=_workspace(tmp_path),
            binding={"capability": "explainer", "workspace": str(_workspace(tmp_path))},
        )
    assert refused.value.reason == "explainer-launch-incomplete"


def test_binding_validation_rejects_stale_generations_unequal_seat_and_wrong_root(
    tmp_path: Path,
) -> None:
    """MIK-R186 @ v2 rule 3: binding validation checks disk workspace, generation and seat identity."""

    ws = _workspace(tmp_path)
    (ws.parent / f"{LEAF}.generation").write_text("2\n", encoding="utf-8")
    (ws / "generation.json").write_text(
        json.dumps(
            {
                "repository": REPO,
                "master": TASK,
                "leaf": LEAF,
                "generation": 2,
                "agentId": "seat-2",
                "seatId": "seat-2",
            }
        ),
        encoding="utf-8",
    )
    valid = {
        "capability": "explainer",
        "agent": "seat-2",
        "seat": "seat-2",
        "request": "req-2",
        "repository": REPO,
        "task": TASK,
        "leaf": LEAF,
        "workspace": str(ws),
        "generation": 2,
    }
    validate_fn = getattr(explainer_binding, "validate_explainer_binding", None)
    assert validate_fn is not None, "validate_explainer_binding missing from explainer_binding"
    checked = validate_fn(valid, coordination_root=tmp_path)
    assert checked.generation == 2 and checked.agent == "seat-2"

    # Stale positive generation (1 != current 2) is rejected.
    with pytest.raises(ExplainerRefused) as stale_err:
        validate_fn({**valid, "generation": 1}, coordination_root=tmp_path)
    assert stale_err.value.reason == "explainer-binding-invalid"

    # Unequal agent and seat is rejected.
    with pytest.raises(ExplainerRefused) as seat_err:
        validate_fn({**valid, "seat": "other-seat"}, coordination_root=tmp_path)
    assert seat_err.value.reason == "explainer-binding-invalid"

    # Another root with the same suffix is rejected.
    other_ws = tmp_path / "other-root" / "explainer" / REPO / TASK / LEAF
    other_ws.mkdir(parents=True)
    with pytest.raises(ExplainerRefused) as root_err:
        validate_fn({**valid, "workspace": str(other_ws)}, coordination_root=tmp_path)
    assert root_err.value.reason == "explainer-binding-invalid"


def test_in_turn_fails_closed_when_turn_facts_are_absent_or_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R186 rule 6: absent or incomplete turn facts fail closed as in-turn."""

    views = {
        "missing-agent": None,
        "missing-turn-active": {"id": "a", "status": "idle"},
        "null-turn-active": {"id": "a", "status": "idle", "turnActive": None},
        "missing-status": {"id": "a", "turnActive": False},
        "explicit-idle": {"id": "a", "status": "idle", "turnActive": False},
        "stamped-closed": {
            "id": "a",
            "status": "closed",
            "turnActive": False,
            "lastUserMessageAt": "2026-10-22T11:40:12.000Z",
        },
        # The host's own shape after a turn ends: the seat is closed and no `turnActive` is restated.
        # Reading that as "facts absent" refused every Update of a closed seat as `explainer-in-turn`.
        "closed-without-turn-field": {
            "id": "a",
            "status": "closed",
            "lastUserMessageAt": "2026-10-22T11:40:12.000Z",
        },
    }
    monkeypatch.setattr(
        host_module,
        "bridge_call",
        lambda _cfg, _cmd, payload: {"agent": views.get(payload["agentId"])},
    )
    host = _host(tmp_path, bridge=lambda *_args: {})
    assert host.in_turn("missing-agent") is True
    assert host.in_turn("missing-turn-active") is True
    assert host.in_turn("null-turn-active") is True
    assert host.in_turn("missing-status") is True
    assert host.in_turn("explicit-idle") is False
    assert host.in_turn("stamped-closed") is False
    assert host.in_turn("closed-without-turn-field") is False


def test_require_applied_rejects_missing_or_mismatched_effort_and_features() -> None:
    """MIK-R105 rule 9: requested effort and featureValues must be reported and match."""

    create = {
        "provider": "claude",
        "model": "claude-opus-5-5",
        "thinkingOptionId": "high",
        "modeId": "auto-review",
        "featureValues": {"service_tier": "fast"},
    }
    # Missing thinkingOptionId fails closed.
    with pytest.raises(ExplainerRefused):
        host_module._require_applied(
            {"agent": {"model": "claude-opus-5-5", "modeId": "auto-review"}}, create
        )
    # Mismatched featureValues fails closed.
    with pytest.raises(ExplainerRefused):
        host_module._require_applied(
            {
                "agent": {
                    "model": "claude-opus-5-5",
                    "thinkingOptionId": "high",
                    "modeId": "auto-review",
                    "featureValues": {"service_tier": "slow"},
                }
            },
            create,
        )


def test_the_launch_environment_carries_the_browser_module_when_the_host_has_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launch environment is built in one place, so the seat's browser must be handed on there.

    A host that has the browser module but does not pass it leaves the seat's tool server without one:
    ``render`` refuses, nothing is published, and the previous generation's page stays on screen. The
    variable is forwarded when the host carries it and stays absent when it does not, so a host without
    a browser answers the seat's own named refusal instead of a silent one.
    """

    workspace = tmp_path / "leaf"
    binding = {"schema": "ar-explainer-binding/v1", "binding": {"leaf_id": "SBX-M1-L1"}}
    monkeypatch.delenv("AR_PLAYWRIGHT", raising=False)
    without = host_module._launch_env(agent_id="a", workspace=workspace, binding=binding)
    # Not inherited from the ambient environment, so either this installation has a module of its own
    # (found from the package tree) or the launch carries none and the seat answers a named refusal.
    if "AR_PLAYWRIGHT" in without:
        assert without["AR_PLAYWRIGHT"].endswith("playwright/index.js")
        assert Path(without["AR_PLAYWRIGHT"]).is_file()

    monkeypatch.setenv("AR_PLAYWRIGHT", "/opt/mod/playwright/index.js")
    carried = host_module._launch_env(agent_id="a", workspace=workspace, binding=binding)
    assert carried["AR_PLAYWRIGHT"] == "/opt/mod/playwright/index.js"
    assert carried[host_module.AGENT_MARKER] == "a"
    assert carried[host_module.WORKSPACE_MARKER] == str(workspace)


def test_a_resume_carries_the_settings_roles_model_effort_and_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S21: resume re-applies the settings role's model/effort/mode and records what it saw."""

    calls: list[tuple[str, dict]] = []

    def bridge(_config, command, payload, timeout_seconds=None):
        calls.append((command, dict(payload)))
        if command == "agent-resume":
            return {
                "resume": {"resumed": True},
                "agent": {
                    "id": "agent-1",
                    "provider": "claude",
                    "model": "claude-opus-5-5",
                    "thinkingOptionId": "high",
                    "lastModeId": "auto-review",
                    "featureValues": {"service_tier": "fast"},
                },
            }
        return {"workspace": {"id": "w1"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(
        host_module,
        "qualified_launch",
        lambda _config, _repo: (
            "claude",
            {
                "model": "claude-opus-5-5",
                "effort": "high",
                "mode": "auto-review",
                "featureValues": {"service_tier": "fast"},
            },
        ),
    )
    host = _host(tmp_path, bridge=bridge)
    host._provenance["agent-1"] = {"agentId": "agent-1", "repository": REPO}

    provenance = host.resume("agent-1")

    payload = dict(calls)["agent-resume"]
    assert payload["agentId"] == "agent-1"
    assert payload["model"] == "claude-opus-5-5"
    assert payload["thinkingOptionId"] == "high"
    assert payload["modeId"] == "auto-review"
    assert provenance["model"] == "claude-opus-5-5"
    assert provenance["effort"] == "high"
    assert provenance["mode"] == "auto-review"
    assert provenance["resumed"] is True


def test_a_resumed_seat_on_other_values_than_the_settings_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S21: the resumed seat's own statement is the check, not the resume request."""

    def bridge(_config, command, payload, timeout_seconds=None):
        if command == "agent-resume":
            return {
                "resume": {"resumed": True},
                "agent": {"id": "agent-1", "model": "claude-opus-5", "lastModeId": "auto-review"},
            }
        return {"workspace": {"id": "w1"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(
        host_module,
        "qualified_launch",
        lambda _config, _repo: ("claude", {"model": "claude-opus-5-5", "mode": "auto-review"}),
    )
    host = _host(tmp_path, bridge=bridge)
    host._provenance["agent-1"] = {"agentId": "agent-1", "repository": REPO}

    with pytest.raises(ExplainerRefused) as refused:
        host.resume("agent-1")

    assert refused.value.reason == "explainer-seat-config-unapplied"
    assert "claude-opus-5-5" in refused.value.detail


def test_a_resume_pending_a_permission_decision_is_reported_not_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S21: a permission-pending resume is preserved and reported; it is never claimed as done."""

    def bridge(_config, command, payload, timeout_seconds=None):
        if command == "agent-resume":
            return {"resume": {"resumed": False, "permissionPending": True}}
        return {"workspace": {"id": "w1"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("claude", {}))
    host = _host(tmp_path, bridge=bridge)
    host._provenance["agent-1"] = {"agentId": "agent-1", "repository": REPO}

    provenance = host.resume("agent-1")

    assert provenance["resumed"] is False
    assert provenance["permissionPending"] is True


def test_a_send_to_a_seat_pending_permission_is_held_not_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S21: the host asks permission; the message is held, never answered by a false denial."""

    def bridge(_config, command, payload):
        return {"delivery": {"delivered": False, "refused": "busy", "permissionPending": True}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    host = _host(tmp_path, bridge=bridge)

    answer = host.send("agent-1", "explain this", message_id="m-1")

    assert answer["status"] == "held"
    assert answer["permissionPending"] is True


def test_a_cancel_targets_the_exact_seat_and_request_and_closes_the_pending_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S21 (ruling bbdfeb5f): cancel ends the turn through the host at the exact seat and request,
    the permission-pending prompt does not stay as an open decision, and the seat's mode is
    unchanged."""

    calls: list[tuple[str, dict]] = []

    def bridge(_config, command, payload):
        calls.append((command, dict(payload)))
        if command == "agent-create":
            return {
                "workspace": {"id": "w1"},
                "agent": {
                    "id": "agent-1",
                    "provider": "claude",
                    "model": "claude-opus-5-5",
                    "thinkingOptionId": "high",
                    "modeId": "auto-review",
                },
            }
        if command == "agent-send":
            return {"delivery": {"delivered": False, "refused": "busy", "permissionPending": True}}
        if command == "agent-cancel":
            return {
                "cancelled": True,
                "agent": {
                    "id": "agent-1",
                    "provider": "claude",
                    "model": "claude-opus-5-5",
                    "thinkingOptionId": "high",
                    "modeId": "auto-review",
                    "status": "idle",
                    "turnActive": False,
                    "pendingPermissions": [],
                },
            }
        return {"workspace": {"id": "w1"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(
        host_module,
        "qualified_launch",
        lambda _config, _repo: (
            "claude",
            {"model": "claude-opus-5-5", "effort": "high", "mode": "auto-review"},
        ),
    )
    host = _host(tmp_path, bridge=bridge)

    provenance = host.create_seat(
        agent_id="agent-1",
        workspace=_workspace(tmp_path),
        title="Explainer · " + LEAF,
        capsule=CAPSULE,
        labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
    )
    assert provenance["model"] == "claude-opus-5-5"
    assert provenance["effort"] == "high"
    assert provenance["mode"] == "auto-review"
    mode_before = provenance["mode"]

    held = host.send("agent-1", "explain this", message_id="m-1")
    assert held["status"] == "held" and held["permissionPending"] is True
    host._provenance["agent-1"]["permissionPending"] = True

    answer = host.cancel("agent-1", request_id="req-1")

    payload = dict(calls)["agent-cancel"]
    assert payload["agentId"] == "agent-1" and payload["requestId"] == "req-1"
    assert answer["status"] == "cancelled"
    assert answer["turnStopped"] is True
    assert answer["permissionClosed"] is True
    assert answer["requestUncertain"] is False
    # The pending prompt does not stay as an open decision, and the seat's mode is unchanged.
    assert host._provenance["agent-1"]["permissionPending"] is False
    assert host._provenance["agent-1"]["mode"] == mode_before
    assert answer["agent"]["modeId"] == mode_before


class _ReportRoot:
    """The one field the developer-message reader needs; the server casts its own."""

    coordination_root: Path

    def __init__(self, root: Path) -> None:
        self.coordination_root = root


def _file_tray_message(root: Path, message_id: str, seat: str) -> Path:
    folder = developer_messages.target_folder(_ReportRoot(root), REPO, TASK, seat)
    when = datetime.fromisoformat(NOW)
    content = render_message_file(
        MessageContent(
            when=when,
            sender_line="developer",
            sender_task=TASK,
            recipient_role="review-presenter",
            recipient_id=seat,
            recipient_task=TASK,
            message_id=message_id,
            text="Prefer the shorter page.\nKeep the comparison table.",
        )
    )
    stem = developer_messages.message_stem(when, "review-presenter", seat, message_id)
    return store_message_file(folder, stem, content)


def _architect_record(root: Path, task_id: str, architect: str) -> None:
    path = root / "notes" / "reports" / "master-architect" / REPO / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"architectAgentId": architect, "act": "manager-start", "at": NOW}, sort_keys=True
        )
    )


def _architect_receipt(root: Path, architect: str, status: str = "running") -> Path:
    folder = root / "notes" / "reports" / "role-executions"
    folder.mkdir(parents=True, exist_ok=True)
    workspace = root / "workspace" / f"arch-{architect[:4]}"
    workspace.mkdir(parents=True, exist_ok=True)
    receipt = folder / f"{architect[:8]}.json"
    receipt.write_text(
        json.dumps(
            {"agentId": architect, "status": status, "workspace": {"path": workspace.as_posix()}}
        ),
        encoding="utf-8",
    )
    return workspace


def _deliver_with_production_sender(
    root: Path,
    config: Any,
    *,
    architect: str,
    folder: Path | None,
    report_path: Path,
) -> tuple[str, str | None, Path, dict[str, Any] | None]:
    report = explainer_server._Report(
        draft=explainer_server._Draft(
            message_id="m-1",
            seat="seat-1",
            copied="the copied words",
            perspective="the page is sound",
            revision=1,
            task_id=TASK,
            leaf=LEAF,
        ),
        report_id="report-1",
        path=report_path,
        digest="sha256:" + "0" * 64,
    )
    report_path.write_text("the delivered report", encoding="utf-8")
    ctx = explainer_server._DeliveryContext(
        coordination_root=root,
        architect=architect,
        report=report,
        text="From explainer\n\nthe delivered report",
        folder=folder,
        tray_folder=root / "tray",
        now=NOW,
    )
    state, reason, _arch, path, pick = explainer_server._deliver_report_to_architect(
        ctx, sender=explainer_report_sender(config)
    )
    return state, reason, path, pick


def test_the_production_report_sender_composes_the_limit_hold_and_terminal_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I10: the real report delivery with the production sender answers each host state once."""

    runtime = config(tmp_path)
    root = tmp_path
    (root / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    architect_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    architect_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    workspace_a = _architect_receipt(root, architect_a)
    _architect_receipt(root, architect_b)

    bridge_calls: list[tuple[str, dict]] = []

    def bridge(_config, command, payload):
        bridge_calls.append((command, dict(payload)))
        return {"delivery": {"delivered": True}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)

    # Delivered: the sender reports through the real bridge, to the recorded architect alone.
    state, _reason, _path, _pick = _deliver_with_production_sender(
        root,
        runtime,
        architect=architect_a,
        folder=workspace_a,
        report_path=root / "report-a.md",
    )
    assert state == "reported"
    assert bridge_calls[-1] == (
        "agent-send",
        {"agentId": architect_a, "text": "From explainer\n\nthe delivered report"},
    )

    # Busy: the sender answers held, and the server holds it in the architect's own store.
    workspace_b = _architect_receipt(root, architect_b)
    busy_calls: list[tuple[str, dict]] = []

    def busy_bridge_recording(_config, command, payload):
        busy_calls.append((command, dict(payload)))
        return {"delivery": {"delivered": False, "refused": "busy"}}

    monkeypatch.setattr(host_module, "bridge_call", busy_bridge_recording)
    state, _reason, _path, _pick = _deliver_with_production_sender(
        root,
        runtime,
        architect=architect_b,
        folder=workspace_b,
        report_path=root / "report-b.md",
    )
    assert state == "held"
    assert waiting_total(workspace_b, architect_b) == 1
    assert busy_calls[-1][0] == "agent-send" and busy_calls[-1][1]["agentId"] == architect_b

    # Limited (MIK-R190 rule 9): the check the production sender makes holds the report before
    # anything is sent.
    architect_c = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    workspace_c = _architect_receipt(root, architect_c)
    calls_before = len(bridge_calls)
    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(
        limit_module,
        "provider_limit_hold",
        lambda _config, agent: (
            {"heldReason": "provider-limit", "limitedUntil": "2099-01-01T00:00:00+00:00"}
            if agent == architect_c
            else None
        ),
    )
    state, _reason, _path, _pick = _deliver_with_production_sender(
        root,
        runtime,
        architect=architect_c,
        folder=workspace_c,
        report_path=root / "report-c.md",
    )
    assert state == "held"
    assert len(bridge_calls) == calls_before, "a limited recipient is held before anything is sent"
    assert waiting_total(workspace_c, architect_c) == 1
    monkeypatch.setattr(limit_module, "provider_limit_hold", lambda _config, _agent: None)


def test_the_production_report_sender_answers_terminal_outcomes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I10: archived, bridge-error and timeout each answer one terminal state, once."""

    runtime = config(tmp_path)
    root = tmp_path
    (root / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    architect = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    _architect_receipt(root, architect)

    # Archived: not reported, kept back for his tray.
    def archived_bridge(_config, command, payload):
        return {"delivery": {"delivered": False, "refused": "archived"}}

    monkeypatch.setattr(host_module, "bridge_call", archived_bridge)
    state, reason, path, pick = _deliver_with_production_sender(
        root,
        runtime,
        architect=architect,
        folder=None,
        report_path=root / "report-a.md",
    )
    assert state == "not reported" and reason == "bridge-error"
    assert pick is not None and pick["kind"] == "message-file"
    assert path.name.endswith(".undelivered.md")

    # A bridge error is kept back for his tray, never claimed delivered.
    def error_bridge(_config, command, payload):
        raise PaseoBridgeFailure("paseo_daemon_unreachable", "no daemon")

    monkeypatch.setattr(host_module, "bridge_call", error_bridge)
    state, reason, path, pick = _deliver_with_production_sender(
        root,
        runtime,
        architect=architect,
        folder=None,
        report_path=root / "report-a.md",
    )
    assert state == "not reported" and reason == "bridge-error"
    assert pick is not None

    # A full held store sends the report back to his tray, never dropped and never sent twice.
    workspace_full = _architect_receipt(root, architect)
    for index in range(MAX_HELD_PER_RECIPIENT):
        hold_message(
            workspace_full,
            architect,
            HeldMessage(
                sender_line="sender",
                sender_agent_id=f"sender-{index}",
                text="waiting",
                reason="busy",
            ),
        )
    monkeypatch.setattr(host_module, "bridge_call", archived_bridge)
    state, reason, path, pick = _deliver_with_production_sender(
        root,
        runtime,
        architect=architect,
        folder=workspace_full,
        report_path=root / "report-a.md",
    )
    assert state == "not reported" and reason == "recipient-busy"
    assert pick is not None and pick["kind"] == "message-file"

    # A timeout answers "sent, outcome unknown" once; the file is kept, never a tray pick.
    architect_timeout = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    receipts_folder = root / "notes" / "reports" / "role-executions"
    (receipts_folder / "ffffffff.json").write_text(
        json.dumps({"agentId": architect_timeout, "status": "running"}), encoding="utf-8"
    )

    def timeout_bridge(_config, command, payload):
        raise PaseoBridgeFailure(SEND_OUTCOME_UNKNOWN, "bridge timed out")

    monkeypatch.setattr(host_module, "bridge_call", timeout_bridge)
    state, reason, path, pick = _deliver_with_production_sender(
        root,
        runtime,
        architect=architect_timeout,
        folder=None,
        report_path=root / "report-a.md",
    )
    assert state == "sent, outcome unknown" and reason is None
    assert not path.name.endswith(".undelivered.md") and pick is None


def test_the_registered_report_tool_logs_the_production_senders_last_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I10: the registered composition delivers through the production sender and the report
    log's last line names the outcome truthfully."""

    runtime = config(tmp_path)
    root = tmp_path
    workspace = _workspace(tmp_path)
    seat = agent_id(workspace, 1)
    task_root = root / "tasks" / REPO / TASK
    task_root.mkdir(parents=True, exist_ok=True)
    architect = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    _architect_record(root, TASK, architect)
    _architect_receipt(root, architect)
    write_binding(
        workspace,
        ExplainerBinding(
            binding=FrameBinding.from_packet(packet(record(7)).comparison),
            snapshot="snapshot-1",
            prepared_at=NOW,
            packet_digest="sha256:" + "0" * 64,
        ),
    )
    (workspace.parent / f"{LEAF}.generation").write_text("1\n", encoding="utf-8")
    message_id = "20000000-0000-4000-8000-000000000021"
    _file_tray_message(root, message_id, seat)

    server = FastMCP("review-explainer")
    register_bound_explainer_tools(
        server,
        workspace=workspace,
        seat=seat,
        inputs=ExplainerInputs(
            task_root=task_root,
            coordination_root=root,
            sender=explainer_report_sender(runtime),
            now=lambda: NOW,
        ),
    )
    monkeypatch.setattr(
        host_module, "bridge_call", lambda *_args: {"delivery": {"delivered": True}}
    )

    result = asyncio.run(
        server.call_tool(
            "report_to_architect",
            {"messageId": message_id, "perspective": "the page is sound"},
        )
    )
    body = json.loads(_text_of(result))

    assert body["state"] == "reported"
    assert body["architect"] == architect
    log = root / "notes" / "reports" / "developer-messages" / REPO / TASK / "reports.jsonl"
    last = explainer_server.report_state(log, message_id)
    assert last is not None and last["state"] == "reported"
    assert last["architect"] == architect


def test_a_second_masters_report_reaches_only_its_own_architect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I10: two masters each file their report through the production sender; each one reaches
    only its own recorded architect and logs its own line (MIK-R189 rule 7 step 3)."""

    runtime = config(tmp_path)
    root = tmp_path
    second_task = "260928-other-master"
    second_root = root / "tasks" / REPO / second_task
    second_root.mkdir(parents=True, exist_ok=True)
    second_architect = "33333333-3333-4333-8333-333333333333"
    _architect_record(root, second_task, second_architect)
    _architect_receipt(root, second_architect)
    second_workspace = tmp_path / "explainer" / REPO / second_task / LEAF
    second_workspace.mkdir(parents=True)
    second_seat = agent_id(second_workspace, 1)
    second_binding = FrameBinding.from_packet(packet(record(7)).comparison).model_copy(
        update={"task_id": second_task}
    )
    write_binding(
        second_workspace,
        ExplainerBinding(
            binding=second_binding,
            snapshot="snapshot-1",
            prepared_at=NOW,
            packet_digest="sha256:" + "0" * 64,
        ),
    )
    (second_workspace.parent / f"{LEAF}.generation").write_text("1\n", encoding="utf-8")
    second_message_id = "20000000-0000-4000-8000-000000000022"
    _file_tray_message(root, second_message_id, second_seat)

    second_server = FastMCP("review-explainer")
    register_bound_explainer_tools(
        second_server,
        workspace=second_workspace,
        seat=second_seat,
        inputs=ExplainerInputs(
            task_root=second_root,
            coordination_root=root,
            sender=explainer_report_sender(runtime),
            now=lambda: NOW,
        ),
    )
    monkeypatch.setattr(
        host_module, "bridge_call", lambda *_args: {"delivery": {"delivered": True}}
    )
    second_result = asyncio.run(
        second_server.call_tool(
            "report_to_architect",
            {"messageId": second_message_id, "perspective": "the page is sound"},
        )
    )
    second_body = json.loads(_text_of(second_result))
    assert second_body["state"] == "reported"
    assert second_body["architect"] == second_architect
    second_log = (
        root / "notes" / "reports" / "developer-messages" / REPO / second_task / "reports.jsonl"
    )
    second_last = explainer_server.report_state(second_log, second_message_id)
    assert second_last is not None and second_last["state"] == "reported"
    assert second_last["architect"] == second_architect
