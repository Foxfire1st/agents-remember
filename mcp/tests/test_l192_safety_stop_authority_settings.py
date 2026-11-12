"""L192-F07 (amendment e7f19b15): the safety stop reads the authority settings.

MIK-R199 rule 5 keeps live periodic safety — sample, record, degradation, stop — and says that
empty or revoked *launch* settings never authorize suppressing safety. Since MIK-R112 the safety
tick's stop was built from ``lifecycle_settings_from_config()``, which by design emits no providers,
so the stop selected nothing whatever the sample found. The Architect's ruling e7f19b15 puts the
stop on the user's authority settings, through the same reader and shape the install's old-watcher
stop uses: stop only, no new reader, no new key, generated documents still empty.

These are the ruling's rule 3 cases. The first fails on the pre-amendment candidate — the stop
selects no provider even though the authority settings name an enabled one and the sample is
critical — and passes after it. The second is the control: no entry selects none and reports
nothing new.
"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.providers.degradation import evaluate_provider_degradation
from agents_remember.providers.lifecycle import watchers
from agents_remember.providers.metrics import (
    ContainerSample,
    MetricsSnapshot,
    ProviderMetricsStore,
)
from agents_remember.providers.watcher_service import run_configured_watchers

GREPAI_ID = "grepai-memory"
T1 = "2026-10-22T08:00:00+00:00"


def _authority_settings(root: Path, *, legacy_entry: bool):
    """An authority settings document, with or without a legacy provider entry.

    Rule 4 keeps the legacy ``contextProviders`` block in the authority settings, and reading it
    for a stop grants none of the install/prepare/seed/start/query/watch authority that block
    governs, so this invents no authority and no new key.
    """

    document: dict[str, Any] = {
        "version": 1,
        "coordinationRoot": str(root / "ar-coordination"),
        "workspaceRoot": str(root / "workspace"),
        "repositories": {"agents-remember": {}},
        "providers": {},
        "providerDegradation": {
            "memoryDegradedRatio": 0.75,
            "memoryCriticalRatio": 0.90,
            "degradedSamples": 2,
            "criticalSamples": 2,
            "healthySamples": 2,
        },
    }
    if legacy_entry:
        document["contextProviders"] = {
            "enabled": True,
            "providers": {GREPAI_ID: {"enabled": True}},
        }
    path = root / ".codex" / "mcp" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return load_config(path), path


def _critical_samples(root: Path) -> None:
    """Two samples over the critical memory ratio, which is what the transition needs."""

    store = ProviderMetricsStore(
        load_config(root / ".codex" / "mcp" / "settings.json").coordination_root
    )
    for index, ratio in enumerate((0.95, 0.96), start=1):
        store.record(
            MetricsSnapshot(
                schema="ar-provider-metrics-sample/v1",
                sampledAt=T1,
                containers=[
                    ContainerSample(
                        name=f"grepai-{index}",
                        provider=GREPAI_ID,
                        instance="workspace",
                        running=True,
                        restarts=0,
                        mem_bytes=int(ratio * 1_000),
                        mem_limit_bytes=1_000,
                    )
                ],
            )
        )


class _RecordStop:
    """Patch the grepai stop handler for the duration of one evaluation."""

    def __init__(self, recorded: list[str]) -> None:
        self._recorded = recorded

    def handler(self, _args: Any, action: str) -> dict[str, Any]:
        self._recorded.append(action)
        return {"provider": "grepai", "action": action, "ok": True, "results": []}

    def __enter__(self) -> None:
        self._original = watchers.grepai_run
        watchers.grepai_run = self.handler  # type: ignore[assignment]

    def __exit__(self, *_: object) -> None:
        watchers.grepai_run = self._original  # type: ignore[assignment]


def _run_tick(config: Any, recorded: list[str]) -> dict[str, Any]:
    with _RecordStop(recorded):
        return evaluate_provider_degradation(
            config,
            stop_provider_stacks=partial(run_configured_watchers, action="stop", dry_run=True),
        )


def test_a_legacy_authority_entry_makes_the_safety_stop_select_it(tmp_path: Path) -> None:
    """Rule 3, first case: a legacy entry plus a degradation sample selects that entry.

    Before the amendment this fails: the tick's stop writes an empty generated document, selects
    no provider, and nothing is recorded even though the authority settings enable one.
    """

    config, _path = _authority_settings(tmp_path, legacy_entry=True)
    _critical_samples(tmp_path)
    recorded: list[str] = []

    result = _run_tick(config, recorded)

    assert result["state"] == "critical", result
    assert recorded == [watchers.WATCHER_PROVIDER_ACTIONS["grepai"]["stop"]], (
        "the safety stop did not select the provider the authority settings enable"
    )
    failsafe = result["event"]["criticalFailsafe"]
    assert failsafe["enabled"] is True, failsafe
    selected = failsafe["result"]["results"]
    assert [entry["provider"] for entry in selected] == ["grepai"], failsafe


def test_no_authority_entry_selects_none_and_reports_nothing_new(tmp_path: Path) -> None:
    """Rule 3, second case: with no authority entry the stop selects none."""

    config, _path = _authority_settings(tmp_path, legacy_entry=False)
    _critical_samples(tmp_path)
    recorded: list[str] = []

    result = _run_tick(config, recorded)

    assert result["state"] == "critical", result
    assert recorded == [], "a stop handler ran although the authority settings enable no provider"
    failsafe = result["event"]["criticalFailsafe"]
    assert failsafe["enabled"] is True, failsafe
    assert failsafe["result"]["results"] == [], failsafe
