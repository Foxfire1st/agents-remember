from __future__ import annotations

import os
from pathlib import Path

import pytest
from agents_remember.serving.codex_app_server_adapter import (
    CodexAppServerAdapter,
    CodexAppServerSettings,
)
from agents_remember.serving.harness_control_models import ControlIdentity, LaunchSpec


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
