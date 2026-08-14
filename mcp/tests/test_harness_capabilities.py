from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.serving.harness_capabilities import (
    SET_ACCEPTANCE_VALUES,
    CapabilitySnapshot,
    EffortOption,
    ModelCapability,
    SetResult,
    capability_snapshot_json,
    set_result_json,
)


def test_capability_snapshot_projects_exact_acp_sense_one_categories() -> None:
    snapshot = CapabilitySnapshot(
        models=(
            ModelCapability(
                key="reasoning-model",
                display_name="Reasoning Model",
                description="Selected model",
                supports_effort=True,
                effort_options=(
                    EffortOption("none", "none", "No reasoning"),
                    EffortOption("ultra", "ultra", "Maximum reasoning"),
                ),
                default_effort="none",
            ),
            ModelCapability(
                key="other-model",
                display_name="Other Model",
                supports_effort=True,
                effort_options=(EffortOption("off", "off"),),
                default_effort="off",
            ),
            ModelCapability(
                key="disabled-model",
                display_name="Disabled Model",
                effort_options=(),
                default_effort=None,
                selectable=False,
            ),
        ),
        selected_model_key="reasoning-model",
        selected_effort="ultra",
    )

    payload = capability_snapshot_json(snapshot)
    config_options = cast(list[dict[str, object]], payload["configOptions"])

    assert [option["category"] for option in config_options] == [
        "model",
        "thought_level",
    ]
    model_option, effort_option = config_options
    assert model_option["type"] == "select"
    assert model_option["id"] == "model"
    model_values = cast(list[dict[str, object]], model_option["options"])
    assert [item["value"] for item in model_values] == [
        "reasoning-model",
        "other-model",
    ]
    assert effort_option["id"] == "effort"
    assert effort_option["currentValue"] == "ultra"
    effort_values = cast(list[dict[str, object]], effort_option["options"])
    assert [item["value"] for item in effort_values] == ["none", "ultra"]
    models = cast(list[dict[str, object]], payload["models"])
    second_efforts = cast(list[dict[str, object]], models[1]["effortOptions"])
    assert second_efforts[0]["key"] == "off"
    assert models[2]["selectable"] is False


def test_hidden_or_disabled_current_model_remains_in_its_select_projection() -> None:
    snapshot = CapabilitySnapshot(
        models=(
            ModelCapability(
                key="hidden-current",
                display_name="Hidden Current",
                effort_options=(),
                default_effort=None,
                hidden=True,
            ),
            ModelCapability(
                key="disabled",
                display_name="Disabled",
                effort_options=(),
                default_effort=None,
                selectable=False,
            ),
        ),
        selected_model_key="hidden-current",
        selected_effort=None,
    )

    assert [option.value for option in snapshot.config_options[0].options] == ["hidden-current"]
    assert [option.category for option in snapshot.config_options] == ["model"]

    disabled_current = CapabilitySnapshot(
        models=snapshot.models,
        selected_model_key="disabled",
        selected_effort=None,
    )
    assert [option.value for option in disabled_current.config_options[0].options] == ["disabled"]


def test_acp_projection_omits_selects_without_an_honest_current_value() -> None:
    snapshot = CapabilitySnapshot(
        models=(
            ModelCapability(
                key="model",
                display_name="Model",
                effort_options=(EffortOption("high", "high"),),
                default_effort="high",
            ),
        ),
        selected_model_key=None,
        selected_effort=None,
    )
    selected_without_effort = CapabilitySnapshot(
        models=snapshot.models,
        selected_model_key="model",
        selected_effort=None,
    )

    assert snapshot.config_options == ()
    assert [option.category for option in selected_without_effort.config_options] == ["model"]


def test_set_result_serialization_keeps_the_five_value_acceptance_contract() -> None:
    assert {
        "echo-verified",
        "immediate",
        "queued",
        "unknown",
        "unsupported",
    } == SET_ACCEPTANCE_VALUES
    result = SetResult(
        ok=True,
        acceptance="echo-verified",
        requested_value="max",
        effective_value="high",
        detail="provider clamped the requested effort",
    )

    assert set_result_json(result) == {
        "ok": True,
        "acceptance": "echo-verified",
        "requestedValue": "max",
        "effectiveValue": "high",
        "detail": "provider clamped the requested effort",
    }
    with pytest.raises(ValueError, match="unsupported acceptance"):
        set_result_json(SetResult(False, cast(Any, "garbage"), "max"))


def test_native_setter_modules_have_no_terminal_or_chat_paste_dependency() -> None:
    serving = Path(__file__).resolve().parents[1] / "src" / "agents_remember" / "serving"
    sources = "\n".join(
        (serving / name).read_text(encoding="utf-8")
        for name in (
            "harness_capabilities.py",
            "harness_control_adapter.py",
            "harness_control_bridge.py",
            "harness_submission_authority.py",
            "harness_submission_ledger.py",
            "harness_control_claude.py",
            "claude_stream_state.py",
            "claude_stream_protocol.py",
            "claude_stream_transport.py",
            "claude_stream_submission.py",
            "codex_app_server_adapter.py",
            "codex_app_server_session.py",
            "codex_app_server_state.py",
            "codex_app_server_protocol.py",
            "pi_rpc_configuration.py",
            "pi_rpc_adapter.py",
            "pi_rpc_process.py",
            "pi_rpc_protocol.py",
        )
    )

    for forbidden in (
        "RailChat",
        "Chats",
        "sessionCommands",
        "tmux",
        "terminal_paste",
        "injector",
        "composer",
        "harness_terminal_surface",
    ):
        assert forbidden not in sources
