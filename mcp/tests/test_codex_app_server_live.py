from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
from agents_remember.errors import HarnessControlError
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.codex_app_server_protocol import (
    CodexStdioTransport,
    JsonObject,
    WriteGuard,
)
from agents_remember.serving.harness_control_models import (
    ControlIdentity,
    LaunchSpec,
    PromptRequest,
)
from agents_remember.serving.harness_launch import (
    ResolvedLaunch,
    apply_launch_knobs,
    resolve_settings_launch,
    validate_launch_selection,
    verify_effective_launch,
)


class RecordingCodexTransport(CodexStdioTransport):
    """Retain only non-secret method and model-selection evidence from a live run."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []
        self.token_usage_by_turn: dict[str, dict[str, object]] = {}

    @property
    def pid(self) -> int:
        process = self._process
        assert process is not None
        return process.pid

    async def request(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        before_write: WriteGuard | None = None,
    ) -> JsonObject:
        result = await super().request(method, params, before_write=before_write)
        evidence: dict[str, object] = {"method": method}
        for key in ("model", "effort", "threadId"):
            value = params.get(key)
            if isinstance(value, str):
                evidence[key] = value
        config = params.get("config")
        if isinstance(config, Mapping):
            for key in ("model", "model_reasoning_effort"):
                value = config.get(key)
                if isinstance(value, str):
                    evidence[f"config.{key}"] = value
        self.calls.append(evidence)
        return result

    def messages(self) -> AsyncIterator[JsonObject]:
        return self._recording_messages()

    async def _recording_messages(self) -> AsyncIterator[JsonObject]:
        async for message in super().messages():
            if message.get("method") == "thread/tokenUsage/updated":
                params = message.get("params")
                if isinstance(params, Mapping):
                    turn_id = params.get("turnId")
                    usage = params.get("tokenUsage")
                    if isinstance(turn_id, str) and isinstance(usage, Mapping):
                        self.token_usage_by_turn[turn_id] = _safe_token_usage(usage)
            yield message


def _safe_token_usage(usage: Mapping[str, object]) -> dict[str, object]:
    evidence: dict[str, object] = {}
    for section in ("total", "last"):
        breakdown = usage.get(section)
        if not isinstance(breakdown, Mapping):
            continue
        evidence[section] = {
            key: value
            for key in (
                "totalTokens",
                "inputTokens",
                "cachedInputTokens",
                "outputTokens",
                "reasoningOutputTokens",
            )
            if isinstance((value := breakdown.get(key)), int)
        }
    return evidence


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.skipif(
    os.environ.get("AR_CODEX_APP_SERVER_LIVE_SMOKE") != "1",
    reason="credential-safe live Codex smoke requires explicit opt-in",
)
async def test_live_handshake_model_menu_and_ephemeral_thread() -> None:
    """Use installed auth/config without printing it, send no prompt, and persist no thread."""

    package = os.environ.get("AR_CODEX_APP_SERVER_PACKAGE")
    argv = ("npx", "--yes", package, "app-server") if package else ("codex", "app-server")
    model = os.environ.get("AR_CODEX_SMOKE_MODEL", "gpt-5.6-sol")
    effort = os.environ.get("AR_CODEX_SMOKE_EFFORT", "xhigh")
    adapter = CodexAppServerAdapter(
        CodexAppServerSettings(
            model=model,
            reasoning_effort=effort,
            ephemeral=True,
            client_name="agents_remember_smoke",
            client_title="Agents Remember smoke",
        )
    )
    launch = LaunchSpec(
        identity=ControlIdentity(
            ar_session_id="credential-safe-smoke",
            tmux_name="ar-codex-smoke",
            created_at="2026-07-14T12:00:00+00:00",
        ),
        harness_id="codex",
        cwd=Path.cwd(),
        argv=argv,
    )
    handshake = await adapter.start(launch)
    try:
        assert handshake.snapshot.control == "ready"
        efforts = handshake.raw["advertisedReasoningEfforts"]
        assert isinstance(efforts, list)
        assert effort in efforts
        assert handshake.raw["effectiveReasoningEffort"] == effort
    finally:
        await adapter.stop("graceful")


@pytest.mark.anyio
@pytest.mark.skipif(
    os.environ.get("AR_CODEX_APP_SERVER_LIVE_CONFORMANCE") != "1",
    reason="bounded live Codex conformance requires explicit opt-in and sends two tiny turns",
)
async def test_live_dynamic_launch_and_mid_thread_selection() -> None:
    """Prove dynamic advertise, configured launch, and same-process subsequent-turn set."""

    identity = ControlIdentity(
        ar_session_id="l5-codex-live-conformance",
        tmux_name="ar-l5-codex-live-conformance",
        created_at="2026-07-16T00:00:00+00:00",
    )
    base_launch = LaunchSpec(
        identity=identity,
        harness_id="codex",
        cwd=Path.cwd(),
        argv=("codex", "app-server"),
    )

    discovery_transport = RecordingCodexTransport()
    discoverer = CodexAppServerAdapter(
        CodexAppServerSettings(),
        transport_factory=lambda: discovery_transport,
    )
    started = time.perf_counter()
    catalog = await discoverer.discover(base_launch)
    discovery_seconds = time.perf_counter() - started
    assert len(catalog.models) >= 2
    assert catalog.selected_model_key is None
    assert catalog.selected_effort is None
    assert {call["method"] for call in discovery_transport.calls} <= {
        "initialize",
        "model/list",
    }
    assert all(
        call["method"] not in {"thread/start", "turn/start"} for call in discovery_transport.calls
    )
    assert not discovery_transport.token_usage_by_turn

    initial = next(model for model in catalog.models if model.is_default)
    preferred_target = os.environ.get("AR_CODEX_CONFORMANCE_TARGET_MODEL", "gpt-5.4-mini")
    target = next(
        (
            model
            for model in catalog.models
            if model.key == preferred_target and model.key != initial.key and model.selectable
        ),
        next(
            model
            for model in catalog.models
            if model.key != initial.key and model.selectable and not model.hidden
        ),
    )
    initial_effort = initial.default_effort
    target_effort = target.default_effort
    assert initial_effort is not None
    assert target_effort is not None
    if target_effort == initial_effort:
        target_effort = next(
            option.key for option in target.effort_options if option.key != initial_effort
        )

    selection = resolve_settings_launch(
        harness_id="codex",
        model=initial.key,
        effort=initial_effort,
        workspace=Path.cwd(),
    )
    validate_launch_selection(selection, catalog)
    with pytest.raises(HarnessControlError, match="absent from the dynamic catalog"):
        validate_launch_selection(
            ResolvedLaunch("codex", "ar-l5-unknown-model", initial_effort, Path.cwd()),
            catalog,
        )
    with pytest.raises(HarnessControlError, match="not advertised as launch-settable"):
        validate_launch_selection(
            ResolvedLaunch("codex", initial.key, "ar-l5-unknown-effort", Path.cwd()),
            catalog,
        )

    launch_adapter = CodexAppServerAdapter(CodexAppServerSettings())
    knobs = launch_adapter.launch_knobs(model_key=selection.model_key, effort=selection.effort)
    assert "CODEX_CONFIG" not in knobs.env
    launch = apply_launch_knobs(base_launch, knobs)
    transport = RecordingCodexTransport()
    adapter = CodexAppServerAdapter(
        CodexAppServerSettings(
            model=selection.model_key,
            reasoning_effort=selection.effort,
            config=knobs.session_config,
            ephemeral=True,
            client_name="agents_remember_l5_conformance",
            client_title="Agents Remember L5 conformance",
        ),
        transport_factory=lambda: transport,
    )
    handshake = await adapter.start(launch)
    try:
        pid = transport.pid
        thread_id = handshake.snapshot.vendor_session_id
        assert thread_id is not None
        verify_effective_launch(selection, adapter.advertise(), require_effort_echo=True)

        unknown_model = await adapter.set_model("ar-l5-unknown-model")
        unknown_effort = await adapter.set_effort("ar-l5-unknown-effort")
        assert not unknown_model.ok and unknown_model.acceptance == "unsupported"
        assert not unknown_effort.ok and unknown_effort.acceptance == "unsupported"

        model_result = await adapter.set_model(target.key)
        effort_result = await adapter.set_effort(target_effort)
        assert model_result.ok and model_result.acceptance == "queued"
        assert effort_result.ok and effort_result.acceptance == "queued"

        first = await adapter.submit(
            PromptRequest(
                request_id="l5-codex-turn-1",
                source="terminal",
                text="Reply exactly OK. Do not use tools.",
                submitted_at="2026-07-16T00:00:01+00:00",
            )
        )
        assert first.acceptance == "immediate"
        assert first.vendor_correlation_id is not None
        await _wait_for_turn(adapter, first.vendor_correlation_id)
        assert transport.pid == pid
        assert (await adapter.snapshot()).vendor_session_id == thread_id
        switched = adapter.advertise()
        assert switched.selected_model_key == target.key
        assert switched.selected_effort == target_effort

        immediate_model = await adapter.set_model(target.key)
        immediate_effort = await adapter.set_effort(target_effort)
        assert immediate_model.ok and immediate_model.acceptance == "immediate"
        assert immediate_effort.ok and immediate_effort.acceptance == "immediate"

        second = await adapter.submit(
            PromptRequest(
                request_id="l5-codex-turn-2",
                source="terminal",
                text="Reply exactly AGAIN. Do not use tools.",
                submitted_at="2026-07-16T00:00:02+00:00",
            )
        )
        assert second.acceptance == "immediate"
        assert second.vendor_correlation_id is not None
        await _wait_for_turn(adapter, second.vendor_correlation_id)
        assert transport.pid == pid
        assert (await adapter.snapshot()).vendor_session_id == thread_id
        assert first.vendor_correlation_id in transport.token_usage_by_turn
        assert second.vendor_correlation_id in transport.token_usage_by_turn

        turn_calls = [call for call in transport.calls if call["method"] == "turn/start"]
        assert len(turn_calls) == 2
        assert all(call["threadId"] == thread_id for call in turn_calls)
        assert all(call["model"] == target.key for call in turn_calls)
        assert all(call["effort"] == target_effort for call in turn_calls)
        assert all("paste" not in str(call["method"]).lower() for call in transport.calls)
        print(
            json.dumps(
                {
                    "cliVersion": handshake.raw["codexCliVersion"],
                    "catalogModels": [
                        {
                            "key": model.key,
                            "efforts": [option.key for option in model.effort_options],
                            "defaultEffort": model.default_effort,
                            "isDefault": model.is_default,
                            "hidden": model.hidden,
                        }
                        for model in catalog.models
                    ],
                    "discoverySeconds": round(discovery_seconds, 4),
                    "discoveryMethods": [call["method"] for call in discovery_transport.calls],
                    "launch": {
                        "model": selection.model_key,
                        "effort": selection.effort,
                        "threadId": thread_id,
                        "processId": pid,
                    },
                    "setResults": {
                        "model": model_result.acceptance,
                        "effort": effort_result.acceptance,
                        "acceptedModel": immediate_model.acceptance,
                        "acceptedEffort": immediate_effort.acceptance,
                        "unknownModel": unknown_model.acceptance,
                        "unknownEffort": unknown_effort.acceptance,
                    },
                    "effective": {
                        "model": switched.selected_model_key,
                        "effort": switched.selected_effort,
                    },
                    "sameProcessAndThread": True,
                    "tokenUsageByTurn": {
                        first.vendor_correlation_id: transport.token_usage_by_turn[
                            first.vendor_correlation_id
                        ],
                        second.vendor_correlation_id: transport.token_usage_by_turn[
                            second.vendor_correlation_id
                        ],
                    },
                    "acceptedTurnCalls": turn_calls,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        await adapter.stop("forced")


async def _wait_for_turn(adapter: CodexAppServerAdapter, turn_id: str) -> None:
    async with asyncio.timeout(180):
        async for event in adapter.subscribe():
            if event.kind == "completed" and event.raw.get("turnId") == turn_id:
                return
    raise AssertionError(f"Codex turn {turn_id} did not complete")
