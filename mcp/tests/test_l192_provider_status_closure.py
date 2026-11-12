"""MIK-R199 round-1 finding L192-F01: the detailed provider-status / start-only closure is gone.

The provider summary's only production caller is the context packet, and it asks for the
suppressed projection. What remained behind that parameter was a live-status/health/watchers
refresh closure -- ``_watchers_status`` running a lifecycle ``status`` action, the per-provider
detail items, indexing targets and restart recovery actions -- with no reader left. It is retired
here rather than kept because its module still holds the stop/settings users.

The retained facts are asserted too: a configured provider key still reports as configured (legacy
settings reporting), the stop/cleanup route and the settings writer still exist, and the
independent metrics/degradation safety surface is untouched.

Before the repair the summary still took ``include_providers``/``detail_limit``/``target_repo_id``
and reached a live watcher status read, so the negative assertions fail on that candidate.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from agents_remember.cli import providers_remove
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.providers import metrics
from agents_remember.providers import status as provider_status
from agents_remember.providers.cgc.lifecycle import (
    cgc_runner_image_build,
    cgc_runner_image_status,
)
from agents_remember.providers.lifecycle import watchers as watcher_lifecycle
from agents_remember.providers.lifecycle_service import (
    WATCHER_SERVICE_ACTIONS,
    ProviderLifecycleServiceConfig,
    run_watchers_lifecycle,
)
from agents_remember.providers.settings import write_lifecycle_settings


def test_the_detailed_status_closure_is_not_reachable_from_the_surviving_entry_point() -> None:
    signature = inspect.signature(provider_status.provider_summary_packet)
    assert list(signature.parameters) == ["config"], (
        f"the summary still exposes a caller-selected projection: {list(signature.parameters)}"
    )
    for retired in (
        "_watchers_status",
        "_provider_summary_items",
        "_provider_summary_item",
        "_provider_recovery_actions",
        "_provider_indexing_targets",
        "ProviderStatusProjection",
    ):
        assert not hasattr(provider_status, retired), f"{retired} is still reachable"
    assert "include_providers" not in inspect.getsource(provider_status)


def test_a_configured_key_still_reports_as_configured_with_no_live_detail() -> None:
    packet = provider_status.provider_summary_packet(
        cast(McpRuntimeConfig, SimpleNamespace(providers={"grepai-memory": object()}))
    )

    assert packet["configured"] is True
    assert packet["enabled"] is True
    assert packet["state"] == "skipped"
    assert packet["items"] == []
    assert packet["indexing"] == []
    assert packet["recoveryActions"] == []
    assert "ok" not in packet, "a skipped summary states no health it did not read"


def test_an_unconfigured_runtime_still_reports_unconfigured() -> None:
    packet = provider_status.provider_summary_packet(
        cast(McpRuntimeConfig, SimpleNamespace(providers={}))
    )

    assert packet["configured"] is False
    assert packet["enabled"] is False
    assert packet["state"] == "skipped"


def test_the_retained_stop_settings_and_safety_callers_are_still_present() -> None:
    # What the removal must not take with it: the explicit stop/cleanup route, the settings
    # writer that reports legacy keys, and the independent metrics/degradation safety surface.
    assert callable(providers_remove.run_configured_watchers)
    assert callable(write_lifecycle_settings)
    assert hasattr(metrics, "ProviderMetricsStore")
    assert hasattr(metrics, "sample_provider_containers")


def test_no_surviving_action_asks_a_watcher_to_start_or_report_status() -> None:
    """The finite start/status-only closure is gone from the watcher path itself.

    Round 2 measured the residual: the detailed summary was retired, but the lifecycle
    service still admitted ``start`` and ``status``, the action maps still carried entries
    for them, ``watcher_cgc_status_results``/``watcher_cgc_backend_status`` still ran cgc
    status reads, and ``watcher_cgc_result`` still dispatched ``start-all``. Every one of
    those had no production caller: the two callers ask for ``stop`` only.
    """
    assert frozenset({"stop", "shutdown-all"}) == WATCHER_SERVICE_ACTIONS
    for provider in ("grepai", "cgc"):
        assert set(watcher_lifecycle.WATCHER_PROVIDER_ACTIONS[provider]) == {
            "stop",
            "shutdown-all",
        }, f"{provider} still maps a start or status action"

    service_config = ProviderLifecycleServiceConfig(
        coordination_root=Path("/nonexistent-coordination-root"),
        settings_path=Path("/nonexistent-settings.json"),
    )
    for retired_action in ("start", "status"):
        with pytest.raises(ValueError, match="unsupported watcher service action"):
            run_watchers_lifecycle(service_config, action=retired_action)

    for retired_symbol in (
        "watcher_cgc_status_results",
        "watcher_cgc_backend_status",
        "cgc_start_all",
        "cgc_status",
        "cgc_backend_status",
        "cgc_all_layouts_from_settings",
    ):
        assert not hasattr(watcher_lifecycle, retired_symbol), (
            f"{retired_symbol} is still reachable as a start/status branch"
        )


def test_the_status_only_projection_is_gone_and_the_stop_route_and_image_owner_survive(
    tmp_path: Path,
) -> None:
    """What the closure removal must not take with it.

    ``providers/current_state.py`` existed for one branch -- the status-only aggregation in
    ``run_configured_watchers`` -- so the module went with it. The stop route still answers,
    the legacy settings reader that the stop resolves providers from still writes, and the
    cgc runner image keeps its start/stop layout ownership.
    """
    assert importlib.util.find_spec("agents_remember.providers.current_state") is None, (
        "the status-only projection module is still importable"
    )
    assert not hasattr(providers_remove.run_configured_watchers, "__wrapped__")

    settings_path = write_lifecycle_settings()
    try:
        payload = run_watchers_lifecycle(
            ProviderLifecycleServiceConfig(
                coordination_root=tmp_path,
                settings_path=settings_path,
                dry_run=True,
                timeout=5,  # load-independent: dry-run config; nothing waits on it
            ),
            action="stop",
        )
    finally:
        settings_path.unlink(missing_ok=True)

    assert payload["provider"] == "watchers"
    assert payload["action"] == "stop"
    assert payload["ok"] is True, payload
    assert payload["results"] == [], "the generated settings declare no provider to stop"

    assert callable(cgc_runner_image_build)
    assert callable(cgc_runner_image_status)
