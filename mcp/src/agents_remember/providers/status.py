"""Read-only provider summary for the context packet.

The search providers (CGC and GrepAI) are retired: nothing displays their live state, so this
reader reports the retired summary -- ``state: skipped``, no items, no indexing targets, no
recovery actions -- and never refreshes watchers, reads a current-state file or builds detail.
What survives is one fact worth carrying: whether the settings still *configure* providers at all,
which is how a legacy settings key keeps being reported rather than silently disappearing.

Anything richer than that was a live-status/health/start-only closure with no production reader
left: the only caller is the context packet, and it asks for the suppressed projection. The
independent provider-safety sampler keeps its own loop in :mod:`agents_remember.providers.metrics`
and needs nothing from this module.
"""

from __future__ import annotations

from typing import Any, cast

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.providers import ProviderState, ProviderSummary


def provider_summary_packet(config: McpRuntimeConfig) -> dict[str, Any]:
    """The context packet's provider block, as plain JSON."""
    return provider_summary(config).model_dump(mode="json", exclude_none=True)


def provider_summary(config: McpRuntimeConfig) -> ProviderSummary:
    """The retired provider summary: configured-or-not, and nothing it would have to read."""
    configured = bool(config.providers)
    return ProviderSummary(
        configured=configured,
        enabled=configured,
        state=_provider_state("skipped"),
    )


def _provider_state(value: Any) -> ProviderState:
    state = str(value or "unknown")
    if state not in {
        "ready",
        "degraded",
        "failed",
        "disabled",
        "unknown",
        "noProviders",
        "skipped",
    }:
        state = "unknown"
    return cast(ProviderState, state)
