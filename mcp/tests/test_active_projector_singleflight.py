"""Session-scoped projector resolution is singleflight under reconnect traffic."""

from __future__ import annotations

import asyncio
import gc
from unittest.mock import patch

import agents_remember.serving.conversation.active.service as service_module
import pytest
from agents_remember.serving.conversation.active.service import ActiveConversationService
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    AuthorizationBinding,
)
from agents_remember.serving.harness_control_models import AdapterSnapshot, ControlIdentity


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_concurrent_reconnect_replaces_a_retired_projector_once() -> None:
    """Page/events/history convergence cannot orphan multiple poll loops."""

    identity = ActiveConversationRef(
        harness_id="codex",
        vendor_conversation_id="vendor-1",
        project_scope="/workspace",
        identity_digest="digest",
        ar_session_id="ar-1",
        bridge_epoch="epoch-1",
    )
    snapshot = AdapterSnapshot(
        identity=ControlIdentity(
            ar_session_id="ar-1",
            tmux_name="ar-t-1",
            created_at="2026-07-27T12:00:00+00:00",
        ),
        control="ready",
        activity="idle",
        acceptance="immediate",
        vendor_session_id="vendor-1",
    )
    created: list[_Projected] = []

    class _Retired:
        def matches(self, _identity: ActiveConversationRef) -> bool:
            return False

        async def close(self) -> None:
            # Reproduce the real retired-projector cleanup yield that formerly
            # let a second request pop the first request's replacement.
            await asyncio.sleep(0.02)

    class _Projected:
        def __init__(self, **_kwargs: object) -> None:
            created.append(self)

        def matches(self, candidate: ActiveConversationRef) -> bool:
            return candidate == identity

        async def close(self) -> None:
            return None

    service = ActiveConversationService(object())  # type: ignore[arg-type]
    service._projectors["ar-1"] = _Retired()  # type: ignore[assignment]
    service._projector_lru.append("ar-1")
    authorization = AuthorizationBinding(
        principal_id="local-operator:1000",
        tenant_id="/workspace",
    )

    with (
        patch.object(service_module, "resolve_running_entry", return_value=object()),
        patch.object(service_module, "current_bridge_epoch", return_value="epoch-1"),
        patch.object(service_module, "live_snapshot", return_value=snapshot),
        patch.object(service_module, "build_identity", return_value=(identity, object())),
        patch.object(service_module, "ActiveSessionProjector", _Projected),
    ):
        page, events, child_history = await asyncio.gather(
            service._projector_for(
                authorization, "ar-1", expected_bridge_epoch="epoch-1"
            ),
            service._projector_for(
                authorization, "ar-1", expected_bridge_epoch="epoch-1"
            ),
            service._projector_for(
                authorization, "ar-1", expected_bridge_epoch="epoch-1"
            ),
        )

    assert page[0] is events[0] is child_history[0]
    assert len(created) == 1
    assert service.projector_count == 1
    await service.close()
    gc.collect()
    assert len(service._session_locks) == 0
