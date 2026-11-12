"""The explainer's seat on the host: its instructions and its tool policy.

MIK-R186 rule 5 creates the seat with the capsule as its instructions and with no first prompt.
MIK-R211 v5 rule 3 makes the seat a normal workspace: its tool policy is a task agent's, with the
explainer's own four tools granted beside the identity read, and nothing about the host's provider
map refuses it (MIK-R192 rule 8's confinement is gone)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents_remember.canvas import explainer_binding
from agents_remember.canvas.explainer_binding import EXPLAINER_SURFACE
from agents_remember.canvas.explainer_capsule import capability_text
from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.cli import paseo_explainer_host as host_module
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_catalog import LaunchSelectionRefused
from agents_remember.errors import PaseoRuntimeFailure
from canvas_host_test_support import REPO, TASK, config
from explainer_host_test_support import CAPSULE, LEAF, SERVER_NAME, _host, _workspace


def test_a_retry_adopts_the_seat_the_failed_attempt_left_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A create the host has already seen is a retry, not an error: it lands on that seat."""

    calls: list[tuple[str, dict]] = []

    def bridge(_config, command, payload):
        calls.append((command, payload))
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        if command == "agent-create":
            raise PaseoBridgeFailure(
                "agent_request_key_conflict", "this request key already names another request"
            )
        return {
            "agent": {
                "id": "agent-1",
                "provider": "claude",
                "model": "claude-opus-5-5",
                "workspaceId": "w1",
            }
        }

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("claude", {}))
    host = _host(tmp_path, bridge=bridge)

    provenance = host.create_seat(
        agent_id="agent-1",
        workspace=_workspace(tmp_path),
        title="Explainer · " + LEAF,
        capsule=CAPSULE,
        labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
    )

    assert provenance["adopted"] is True
    assert provenance["agentId"] == "agent-1"
    assert provenance["model"] == "claude-opus-5-5"
    # One create was attempted; the seat the host held was adopted instead of a second one.
    assert [name for name, _payload in calls].count("agent-create") == 1
    assert "agent-archive" not in [name for name, _payload in calls]


def test_a_seat_on_another_provider_is_adopted_and_that_provider_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host's id names one seat, so a retry adopts it and the record says what really runs."""

    names: list[str] = []

    def bridge(_config, command, payload):
        names.append(command)
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        if command == "agent-create":
            raise PaseoBridgeFailure(
                "paseo_call_failed", "agent_id_conflict: this agent id already exists"
            )
        return {"agent": {"id": "agent-1", "provider": "opencode", "model": "gpt-5"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("claude", {}))
    host = _host(tmp_path, bridge=bridge)

    provenance = host.create_seat(
        agent_id="agent-1",
        workspace=_workspace(tmp_path),
        title="Explainer · " + LEAF,
        capsule=CAPSULE,
        labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
    )

    assert provenance["adopted"] is True
    assert provenance["adoptedProvider"] == "opencode"
    assert provenance["model"] == "gpt-5"
    # Nothing is archived: the seat the developer may still be reading is not this build's to retire.
    assert "agent-archive" not in names
    assert names.count("agent-create") == 1


def test_an_archived_seat_the_host_will_not_replace_names_retire_as_the_way_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An archived seat's id is still taken, so the refusal names the one control that clears it."""

    def bridge(_config, command, payload):
        if command == "workspace-open":
            return {"workspace": {"id": "w1"}}
        if command == "agent-create":
            raise PaseoBridgeFailure(
                "paseo_call_failed", "agent_id_conflict: this agent id already exists"
            )
        return {
            "agent": {"id": "agent-1", "provider": "claude", "archivedAt": "2026-10-25T00:00:00Z"}
        }

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("claude", {}))
    host = _host(tmp_path, bridge=bridge)

    with pytest.raises(ExplainerRefused) as held:
        host.create_seat(
            agent_id="agent-1",
            workspace=_workspace(tmp_path),
            title="Explainer · " + LEAF,
            capsule=CAPSULE,
            labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
        )

    assert held.value.reason == "explainer-seat-held"
    assert "Retire" in held.value.detail


def test_the_request_key_names_the_request_so_a_changed_retry_never_collides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host refuses one key with two payloads, which broke every changed retry (L183)."""

    keys: list[str] = []

    def bridge(_config, command, payload):
        if command == "agent-create":
            keys.append(payload["idempotencyKey"])
        return {"workspace": {"id": "w1"}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    provider = {"name": "claude"}
    monkeypatch.setattr(
        host_module, "qualified_launch", lambda _config, _repo: (provider["name"], {})
    )
    host = _host(tmp_path, bridge=bridge)
    labels = {"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK}

    def create() -> None:
        host.create_seat(
            agent_id="agent-1",
            workspace=_workspace(tmp_path),
            title="Explainer · " + LEAF,
            capsule=CAPSULE,
            labels=labels,
        )

    create()
    # An identical retry must adopt the earlier receipt, so the key is the same.
    create()
    # A changed request -- the developer switched provider -- must not collide with it.
    provider["name"] = "opencode"
    create()

    assert len(keys) == 3
    assert keys[0] == keys[1]
    assert keys[2] != keys[1]
    assert keys[2].startswith("explainer:agent-1:")


def test_the_fingerprint_ignores_field_order_and_names_every_change() -> None:
    assert host_module._request_fingerprint({"a": 1, "b": [2]}) == host_module._request_fingerprint(
        {"b": [2], "a": 1}
    )
    assert host_module._request_fingerprint({"provider": "claude"}) != (
        host_module._request_fingerprint({"provider": "opencode"})
    )
    assert host_module._request_fingerprint({"workspaceId": "w1"}) != (
        host_module._request_fingerprint({"workspaceId": "w2"})
    )


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


def test_the_policy_is_a_task_agents_with_the_four_tools_granted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R211 v5 rule 3: the seat's grant is a task agent's, never a list that confines it.

    Claude's host grants by exact name and keeps every other tool, so the identity read and the
    four kept tools are granted and the harness's and AR's general tools stay with the permission
    mode. Codex's route would remove every tool not listed, so it gets no grant, as a task agent's
    launch does not.
    """

    created: dict[str, dict] = {}
    for provider in ("claude", "codex"):

        def bridge(_config, command, payload, provider=provider):
            if command == "agent-create":
                created[provider] = payload
            return {"workspace": {"id": "w1"}}

        monkeypatch.setattr(host_module, "bridge_call", bridge)
        monkeypatch.setattr(
            host_module,
            "qualified_launch",
            lambda _config, _repo, provider=provider: (provider, {}),
        )
        _host(tmp_path, bridge=bridge).create_seat(
            agent_id=f"agent-{provider}",
            workspace=_workspace(tmp_path),
            title="t",
            capsule=CAPSULE,
            labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
        )

    granted = {entry["tool"] for entry in created["claude"]["toolPolicy"]["preapproved"]}
    assert granted == {"server_info", *EXPLAINER_SURFACE}
    assert set(EXPLAINER_SURFACE) == {"render", "define_category", "remove_category", "grade"}
    assert "toolPolicy" not in created["codex"]


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
    monkeypatch.setattr(
        host_module, "daemon_configuration", lambda _config: {"providers": {}}, raising=False
    )


def test_a_host_map_with_extra_arguments_starts_the_seat_like_any_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R211 v5 rule 3: nothing is refused for being the explainer.

    The host's provider map gives Claude an extra argument, and the daemon's own map cannot be
    read; the seat is created and sent to all the same, as a task agent's would be.
    """

    _configured_host(tmp_path, monkeypatch, extra=True)

    def unread(_config):
        raise PaseoRuntimeFailure("daemon-config-unverified", "config", "the daemon did not answer")

    monkeypatch.setattr(host_module, "daemon_configuration", unread, raising=False)
    calls: list[str] = []

    def bridge(_config, command, _payload):
        calls.append(command)
        return {"workspace": {"id": "w1"}, "delivery": {"delivered": True}}

    monkeypatch.setattr(host_module, "bridge_call", bridge)
    monkeypatch.setattr(host_module, "qualified_launch", lambda _config, _repo: ("claude", {}))
    host = _host(tmp_path, bridge=bridge)

    host.create_seat(
        agent_id="agent-1",
        workspace=_workspace(tmp_path),
        title="t",
        capsule=CAPSULE,
        labels={"ar.capability": "review-explainer", "ar.repository": REPO, "ar.task": TASK},
    )
    host.send("agent-1", "explain this")

    assert "agent-create" in calls and "agent-send" in calls


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
