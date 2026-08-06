"""Evidence-bound conversation telemetry projection (260718-CHATS-L3, R6).

Every projected metric carries value/unit, origin, scope, observed time,
freshness, precision, runtime/helper versions, and fixture evidence. Metrics
are emitted only when their exact-session capability is ``supported`` or
``partial``; missing native data is absent (never zero). THE CONTRACT IS THE
ONLY GATE (developer ruling 2026-07-21, 260718-CHATS-L5F R4): a capability is
demoted only when its contract fails verification or was never probed, never by
a runtime/helper version-string comparison — the runtime/helper versions ride
the metric as informational evidence only. The only currently supported metric on the landed
evidence surface is codex cumulative token usage, read from the latest
``thread/tokenUsage/updated`` frame in the bounded evidence window; every
other documented-but-unobserved contract stays visibly unverified in the
capability view instead of minting a value here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from agents_remember.serving.conversation.control.capabilities import (
    telemetry_capabilities_for,
)
from agents_remember.serving.conversation.control.service import ConversationControlService
from agents_remember.serving.conversation.models import (
    AuthorizationBinding,
    CapabilityEvidence,
    ConversationTelemetry,
    MetricEvidence,
    MetricScope,
    UsageMetricValue,
)
from agents_remember.serving.harness_control_client import read_control_evidence
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry

_FRESH_WINDOW_MS = 15_000


async def conversation_telemetry(
    service: ConversationControlService,
    authorization: AuthorizationBinding,
    ar_session_id: str,
    *,
    expected_bridge_epoch: str,
) -> ConversationTelemetry:
    """Project the exact session's evidence-bound telemetry."""

    del authorization  # resolution is the route's proof; the projection needs no claim
    entry = await service.resolve_entry(ar_session_id)
    epoch = await service.verify_epoch(entry, expected_bridge_epoch)
    snapshot = await service.live_snapshot(entry)
    identity = service.build_identity(entry, bridge_epoch=epoch, snapshot=snapshot)
    capabilities = telemetry_capabilities_for(identity.harness_id, snapshot)
    channel = service.channel(ar_session_id, epoch)
    usage: MetricEvidence[UsageMetricValue] | None = None
    evidence = capabilities.usage.evidence
    if (
        capabilities.usage.state in {"supported", "partial"}
        and identity.harness_id == "codex"
        and evidence is not None
    ):
        usage = await _codex_usage(entry, epoch, evidence)
    semantic_key = _telemetry_key(usage)
    if semantic_key != channel.telemetry_key:
        channel.telemetry_key = semantic_key
        channel.telemetry_revision += 1
    return ConversationTelemetry(
        revision=max(1, channel.telemetry_revision),
        identity=identity,
        usage=usage,
    )


async def _codex_usage(
    entry: TerminalCatalogEntry,
    epoch: str,
    evidence: CapabilityEvidence,
) -> MetricEvidence[UsageMetricValue] | None:
    """The latest tokenUsage frame's cumulative breakdown; absent when unseen."""

    frame_found: tuple[str, dict[str, object]] | None = None
    after = 0
    while True:
        page = await asyncio.to_thread(
            read_control_evidence,
            entry,
            after_sequence=after,
            expected_bridge_epoch=epoch,
        )
        for frame in page.frames:
            usage = frame.raw.get("tokenUsage")
            if isinstance(usage, dict):
                frame_found = (frame.created_at, usage)
        if not page.truncated:
            break
        after = page.frames[-1].sequence
    if frame_found is None:
        return None
    created_at, usage = frame_found
    total = usage.get("total")
    if not isinstance(total, dict):
        return None
    value = UsageMetricValue(
        input_tokens=_int_or_none(total.get("inputTokens")),
        output_tokens=_int_or_none(total.get("outputTokens")),
        cached_tokens=_int_or_none(total.get("cachedInputTokens")),
    )
    return MetricEvidence(
        value=value,
        unit="tokens",
        origin="codex app-server thread/tokenUsage/updated notification",
        scope=MetricScope(kind="conversation"),
        observed_at=created_at,
        freshness=_freshness(created_at),
        precision="exact",
        runtime_version=evidence.runtime_version,
        helper_version=evidence.helper_version,
        fixture_id=evidence.fixture_id,
    )


def _freshness(observed_at: str) -> Literal["fresh", "stale", "unknown"]:
    try:
        then = datetime.fromisoformat(observed_at)
        if then.tzinfo is None:
            then = then.replace(tzinfo=UTC)
        age_ms = (datetime.now(UTC) - then).total_seconds() * 1000
    except ValueError:
        return "unknown"
    return "fresh" if age_ms <= _FRESH_WINDOW_MS else "stale"


def _telemetry_key(usage: MetricEvidence[UsageMetricValue] | None) -> str:
    if usage is None:
        return "none"
    return (
        f"{usage.observed_at}|{usage.value.input_tokens}|{usage.value.output_tokens}"
        f"|{usage.value.cached_tokens}|{usage.freshness}"
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["conversation_telemetry"]
