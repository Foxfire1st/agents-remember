from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import NamedTuple

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
from agents_remember.serving.codex_app_server_session import codex_launch_knobs
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    ModelCapability,
    SetResult,
)
from agents_remember.serving.harness_control_models import (
    AdapterHandshake,
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:46).
    def __init__(self) -> None:  # pragma: no cover
        super().__init__()
        self.calls: list[dict[str, object]] = []
        self.token_usage_by_turn: dict[str, dict[str, object]] = {}

    @property
    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:52).
    def pid(self) -> int:  # pragma: no cover
        process = self._process
        assert process is not None
        return process.pid

    # 260731-EFA-L7 R10: AR_CODEX_APP_SERVER_LIVE_SMOKE-gated live transport; runs only with the installed Codex CLI.
    async def request(  # pragma: no cover
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

    # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:80).
    def messages(self) -> AsyncIterator[JsonObject]:  # pragma: no cover
        return self._recording_messages()

    # 260731-EFA-L7 R10: AR_CODEX_APP_SERVER_LIVE_SMOKE-gated live transport; runs only with the installed Codex CLI.
    async def _recording_messages(self) -> AsyncIterator[JsonObject]:  # pragma: no cover
        async for message in super().messages():
            if message.get("method") == "thread/tokenUsage/updated":
                params = message.get("params")
                if isinstance(params, Mapping):
                    turn_id = params.get("turnId")
                    usage = params.get("tokenUsage")
                    if isinstance(turn_id, str) and isinstance(usage, Mapping):
                        self.token_usage_by_turn[turn_id] = _safe_token_usage(usage)
            yield message


# 260731-EFA-L7 R10: AR_CODEX_APP_SERVER_LIVE_SMOKE-gated helper; runs only with the installed Codex CLI.
def _safe_token_usage(usage: Mapping[str, object]) -> dict[str, object]:  # pragma: no cover
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
# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:118).
def anyio_backend() -> str:  # pragma: no cover
    return "asyncio"


@pytest.mark.ar_codex_app_server_live_smoke
@pytest.mark.anyio
@pytest.mark.skipif(
    os.environ.get("AR_CODEX_APP_SERVER_LIVE_SMOKE") != "1",
    reason="credential-safe live Codex smoke requires explicit opt-in",
)
# 260731-EFA-L7 R10: AR_CODEX_APP_SERVER_LIVE_SMOKE-gated test body; runs only with the installed Codex CLI.
async def test_live_handshake_model_menu_and_ephemeral_thread() -> None:  # pragma: no cover
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


@pytest.mark.ar_codex_app_server_live_conformance
@pytest.mark.anyio
@pytest.mark.skipif(
    os.environ.get("AR_CODEX_APP_SERVER_LIVE_CONFORMANCE") != "1",
    reason="bounded live Codex conformance requires explicit opt-in and sends two tiny turns",
)
# 260731-EFA-L7 R10: AR_CODEX_APP_SERVER_LIVE_SMOKE-gated test body; runs only with the installed Codex CLI.
async def test_live_dynamic_launch_and_mid_thread_selection() -> None:  # pragma: no cover
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

    discovery = await _discover_without_starting_a_thread(base_launch)
    catalog = discovery.catalog
    pair = _selection_pair(catalog)
    initial, initial_effort = pair.initial, pair.initial_effort
    target, target_effort = pair.target, pair.target_effort

    selection = resolve_settings_launch(
        harness_id="codex",
        model=initial.key,
        effort=initial_effort,
        workspace=Path.cwd(),
    )
    _assert_launch_selection_is_validated_against_the_catalog(selection, catalog, initial)

    adapter, transport, launch = _configured_adapter(base_launch, selection)
    handshake = await adapter.start(launch)
    try:
        pid = transport.pid
        thread_id = handshake.snapshot.vendor_session_id
        assert thread_id is not None
        verify_effective_launch(selection, adapter.advertise(), require_effort_echo=True)

        unknown_model, unknown_effort = await _refused_unknown_selections(adapter)
        model_result, effort_result = await _queued_mid_thread_switch(
            adapter, target.key, target_effort
        )

        first = await _completed_turn(
            adapter,
            request_id="l5-codex-turn-1",
            text="Reply exactly OK. Do not use tools.",
            submitted_at="2026-07-16T00:00:01+00:00",
        )
        assert transport.pid == pid
        assert (await adapter.snapshot()).vendor_session_id == thread_id
        switched = adapter.advertise()
        assert switched.selected_model_key == target.key
        assert switched.selected_effort == target_effort

        immediate_model = await adapter.set_model(target.key)
        immediate_effort = await adapter.set_effort(target_effort)
        assert immediate_model.ok and immediate_model.acceptance == "immediate"
        assert immediate_effort.ok and immediate_effort.acceptance == "immediate"

        second = await _completed_turn(
            adapter,
            request_id="l5-codex-turn-2",
            text="Reply exactly AGAIN. Do not use tools.",
            submitted_at="2026-07-16T00:00:02+00:00",
        )
        assert transport.pid == pid
        assert (await adapter.snapshot()).vendor_session_id == thread_id
        assert first in transport.token_usage_by_turn
        assert second in transport.token_usage_by_turn

        turn_calls = _accepted_turn_calls(
            transport, thread_id=thread_id, model_key=target.key, effort=target_effort
        )
        _print_conformance_evidence(
            handshake=handshake,
            discovery=discovery,
            session=_LaunchedSession(selection, transport, thread_id, pid),
            switch=_MidThreadSwitch(
                set_results={
                    "model": model_result.acceptance,
                    "effort": effort_result.acceptance,
                    "acceptedModel": immediate_model.acceptance,
                    "acceptedEffort": immediate_effort.acceptance,
                    "unknownModel": unknown_model.acceptance,
                    "unknownEffort": unknown_effort.acceptance,
                },
                effective=switched,
            ),
            turns=_BilledTurns((first, second), turn_calls),
        )
    finally:
        await adapter.stop("forced")


class _Discovery(NamedTuple):
    """What a catalog probe produced, and the evidence that it probed only."""

    catalog: CapabilitySnapshot
    transport: RecordingCodexTransport
    seconds: float


# 260731-EFA-L7 R10: AR_CODEX_APP_SERVER_LIVE_SMOKE-gated helper; runs only with the installed Codex CLI.
async def _discover_without_starting_a_thread(
    base_launch: LaunchSpec,
) -> _Discovery:  # pragma: no cover
    """Probe the dynamic catalog and prove the probe cost nothing but `initialize`/`model/list`."""
    transport = RecordingCodexTransport()
    discoverer = CodexAppServerAdapter(
        CodexAppServerSettings(),
        transport_factory=lambda: transport,
    )
    started = time.perf_counter()
    catalog = await discoverer.discover(base_launch)
    seconds = time.perf_counter() - started
    assert len(catalog.models) >= 2
    assert catalog.selected_model_key is None
    assert catalog.selected_effort is None
    assert {call["method"] for call in transport.calls} <= {"initialize", "model/list"}
    assert all(call["method"] not in {"thread/start", "turn/start"} for call in transport.calls)
    assert not transport.token_usage_by_turn
    return _Discovery(catalog, transport, seconds)


class _SelectionPair(NamedTuple):
    """The launch selection and the distinct mid-thread selection to switch to."""

    initial: ModelCapability
    initial_effort: str
    target: ModelCapability
    target_effort: str


# 260731-EFA-L7 R10: AR_CODEX_APP_SERVER_LIVE_SMOKE-gated helper; runs only with the installed Codex CLI.
def _selection_pair(catalog: CapabilitySnapshot) -> _SelectionPair:  # pragma: no cover
    """Two selectable model/effort pairs that differ, so a mid-thread switch is observable."""
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
    return _SelectionPair(initial, initial_effort, target, target_effort)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:334).
def _configured_adapter(  # pragma: no cover
    base_launch: LaunchSpec, selection: ResolvedLaunch
) -> tuple[CodexAppServerAdapter, RecordingCodexTransport, LaunchSpec]:
    """The adapter and launch the resolved selection produces, with no secret in the env."""
    knobs = codex_launch_knobs(model_key=selection.model_key, effort=selection.effort)
    assert "CODEX_CONFIG" not in knobs.env
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
    return adapter, transport, apply_launch_knobs(base_launch, knobs)


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:355).
async def _refused_unknown_selections(  # pragma: no cover
    adapter: CodexAppServerAdapter,
) -> tuple[SetResult, SetResult]:
    """A model and an effort the live catalog does not advertise are both refused as unsupported."""
    unknown_model = await adapter.set_model("ar-l5-unknown-model")
    unknown_effort = await adapter.set_effort("ar-l5-unknown-effort")
    assert not unknown_model.ok and unknown_model.acceptance == "unsupported"
    assert not unknown_effort.ok and unknown_effort.acceptance == "unsupported"
    return unknown_model, unknown_effort


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:366).
async def _queued_mid_thread_switch(  # pragma: no cover
    adapter: CodexAppServerAdapter, model_key: str, effort: str
) -> tuple[SetResult, SetResult]:
    """A switch requested between turns is accepted as queued, to take effect on the next turn."""
    model_result = await adapter.set_model(model_key)
    effort_result = await adapter.set_effort(effort)
    assert model_result.ok and model_result.acceptance == "queued"
    assert effort_result.ok and effort_result.acceptance == "queued"
    return model_result, effort_result


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:377).
async def _completed_turn(  # pragma: no cover
    adapter: CodexAppServerAdapter, *, request_id: str, text: str, submitted_at: str
) -> str:
    """Send one tiny turn, wait for it to complete, and return its vendor correlation id."""
    receipt = await adapter.submit(
        PromptRequest(
            request_id=request_id,
            source="terminal",
            text=text,
            submitted_at=submitted_at,
        )
    )
    assert receipt.acceptance == "immediate"
    assert receipt.vendor_correlation_id is not None
    await _wait_for_turn(adapter, receipt.vendor_correlation_id)
    return receipt.vendor_correlation_id


# 260731-EFA-L7 R10: AR_CODEX_APP_SERVER_LIVE_SMOKE-gated helper; runs only with the installed Codex CLI.
def _accepted_turn_calls(  # pragma: no cover
    transport: RecordingCodexTransport, *, thread_id: str, model_key: str, effort: str
) -> list[dict[str, object]]:
    """The two `turn/start` calls, each on the one thread and carrying the switched selection."""
    turn_calls = [call for call in transport.calls if call["method"] == "turn/start"]
    assert len(turn_calls) == 2
    assert all(call["threadId"] == thread_id for call in turn_calls)
    assert all(call["model"] == model_key for call in turn_calls)
    assert all(call["effort"] == effort for call in turn_calls)
    assert all("paste" not in str(call["method"]).lower() for call in transport.calls)
    return turn_calls


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:409).
def _assert_launch_selection_is_validated_against_the_catalog(  # pragma: no cover
    selection: ResolvedLaunch, catalog: CapabilitySnapshot, initial: ModelCapability
) -> None:
    """The advertised pair validates; an unknown model and an unknown effort are refused."""
    validate_launch_selection(selection, catalog)
    with pytest.raises(HarnessControlError, match="absent from the dynamic catalog"):
        validate_launch_selection(
            ResolvedLaunch("codex", "ar-l5-unknown-model", selection.effort, Path.cwd()),
            catalog,
        )
    with pytest.raises(HarnessControlError, match="not advertised as launch-settable"):
        validate_launch_selection(
            ResolvedLaunch("codex", initial.key, "ar-l5-unknown-effort", Path.cwd()),
            catalog,
        )


class _LaunchedSession(NamedTuple):
    """The one live codex process this run launched and kept.

    The run's whole claim is that a single process and a single thread served both turns
    across a mid-thread switch, so what was asked for (``selection``), what carries it
    (``transport``) and what it became (``thread_id``, ``pid``) are one identity rather than
    four arguments -- the evidence record's ``launch`` block is exactly this tuple.
    """

    selection: ResolvedLaunch
    transport: RecordingCodexTransport
    thread_id: str
    pid: int


class _MidThreadSwitch(NamedTuple):
    """What the mid-thread model/effort switch was told, and what actually took effect."""

    set_results: Mapping[str, object]
    effective: CapabilitySnapshot


class _BilledTurns(NamedTuple):
    """The turns the run sent: their ids (which key the vendor's token usage) and the
    ``turn/start`` calls the vendor accepted for them."""

    ids: tuple[str, str]
    accepted_calls: list[dict[str, object]]


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:456).
def _print_conformance_evidence(  # pragma: no cover
    *,
    handshake: AdapterHandshake,
    discovery: _Discovery,
    session: _LaunchedSession,
    switch: _MidThreadSwitch,
    turns: _BilledTurns,
) -> None:
    """The run's non-secret record: what was advertised, what was launched, what was billed."""
    first_turn, second_turn = turns.ids
    usage = session.transport.token_usage_by_turn
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
                    for model in discovery.catalog.models
                ],
                "discoverySeconds": round(discovery.seconds, 4),
                "discoveryMethods": [call["method"] for call in discovery.transport.calls],
                "launch": {
                    "model": session.selection.model_key,
                    "effort": session.selection.effort,
                    "threadId": session.thread_id,
                    "processId": session.pid,
                },
                "setResults": switch.set_results,
                "effective": {
                    "model": switch.effective.selected_model_key,
                    "effort": switch.effective.selected_effort,
                },
                "sameProcessAndThread": True,
                "tokenUsageByTurn": {
                    first_turn: usage[first_turn],
                    second_turn: usage[second_turn],
                },
                "acceptedTurnCalls": turns.accepted_calls,
            },
            indent=2,
            sort_keys=True,
        )
    )


# 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_codex_app_server_live.py:507).
async def _wait_for_turn(adapter: CodexAppServerAdapter, turn_id: str) -> None:  # pragma: no cover
    async with asyncio.timeout(180):
        async for event in adapter.subscribe():
            if event.kind == "completed" and event.raw.get("turnId") == turn_id:
                return
    raise AssertionError(f"Codex turn {turn_id} did not complete")
