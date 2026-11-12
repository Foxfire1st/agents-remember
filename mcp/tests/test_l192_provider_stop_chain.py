"""L192-F07: which settings source each provider-stop route actually selects.

The provider-stop chain has two entry points. The dashboard metrics loop calls
``providers.watcher_service.run_configured_watchers``, which writes
``providers.settings.lifecycle_settings_from_config()`` to a temporary file and
passes it as ``--from-settings``; the install route
(``install.provider_watchers.stop_provider_watchers_before_refresh`` ->
``run_provider_watcher_lifecycle``) serialises ``rebind.settings``, which
``install.runtime`` fills from ``scope.provider_deps.settings``.

This module binds both selections to source so a claim that "the tick stops the
leftover provider stack" can be judged from code. Every case is provider
SELECTION only: ``dry_run`` is True throughout, no docker runs, and no provider
stop handler is reached (test 4 guards the two dispatch functions so a future
selection change fails here instead of launching a real stop).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agents_remember.install.provider_watchers import (
    ProviderWatcherRebind,
    run_provider_watcher_lifecycle,
)
from agents_remember.providers import lifecycle
from agents_remember.providers.lifecycle import watchers
from agents_remember.providers.lifecycle.provider_settings import (
    context_provider_enabled,
    provider_enabled,
)
from agents_remember.providers.settings import lifecycle_settings_from_config

GREPAI_ID = "grepai-memory"
CGC_ID = "codegraphcontext-code"


def legacy_authority_settings() -> dict[str, Any]:
    """The legacy shape ``install.runtime`` reads from the authority settings map.

    ``scope.provider_deps.settings`` is the authority settings' own
    ``contextProviders`` block; a legacy installation still declares the
    retired providers there as enabled, which is the only input that makes a
    stop handler selectable.
    """

    return {
        "contextProviders": {
            "enabled": True,
            "providers": {GREPAI_ID: {"enabled": True}},
        }
    }


def write_settings(tmp_path: Path, document: dict[str, Any], name: str) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def test_generated_lifecycle_settings_disable_context_providers() -> None:
    """Claim under test: the generated document can select no provider at all."""

    generated = lifecycle_settings_from_config()

    context = generated["contextProviders"]
    assert context["enabled"] is False
    assert context["providers"] == {}
    assert provider_enabled(generated, GREPAI_ID) is False
    assert provider_enabled(generated, CGC_ID) is False


def test_provider_enabled_disables_retired_ids_on_generated_document() -> None:
    """``provider_enabled`` reads the retired ids as never-enabled."""

    generated = lifecycle_settings_from_config()

    assert provider_enabled(generated, GREPAI_ID) is False
    assert provider_enabled(generated, CGC_ID) is False

    legacy = legacy_authority_settings()
    assert provider_enabled(legacy, GREPAI_ID) is True
    assert provider_enabled(legacy, CGC_ID) is False


def test_context_provider_enabled_reads_legacy_enabled_provider(tmp_path: Path) -> None:
    """The same two ids, read back from disk through the lifecycle reader."""

    legacy_path = write_settings(tmp_path, legacy_authority_settings(), "legacy-settings.json")

    _, grepai_enabled = context_provider_enabled(legacy_path, GREPAI_ID)
    _, cgc_enabled = context_provider_enabled(legacy_path, CGC_ID)
    assert grepai_enabled is True
    assert cgc_enabled is False

    generated_path = write_settings(
        tmp_path, lifecycle_settings_from_config(), "generated-settings.json"
    )
    _, generated_grepai = context_provider_enabled(generated_path, GREPAI_ID)
    _, generated_cgc = context_provider_enabled(generated_path, CGC_ID)
    assert generated_grepai is False
    assert generated_cgc is False


def test_watchers_stop_selects_no_provider_from_generated_settings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The generated document leaves ``watcher_provider_results`` with nothing to run."""

    generated_path = write_settings(
        tmp_path, lifecycle_settings_from_config(), "generated-settings.json"
    )

    def refuse_dispatch(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("no provider dispatch may be selected by generated settings")

    # Guards, not experiments: they prove the empty results are selection, not silence.
    monkeypatch.setattr(watchers, "watcher_grepai_result", refuse_dispatch)
    monkeypatch.setattr(watchers, "watcher_cgc_result", refuse_dispatch)

    args = argparse.Namespace(
        provider="watchers",
        action="stop",
        from_settings=generated_path,
        coordination_root=tmp_path,
        dry_run=True,
        json=True,
        timeout=5,  # load-independent: dry-run field; nothing in this path waits on it
    )

    payload = watchers.watchers_run(args, "stop")

    # Provider SELECTION only: nothing below reaches docker, a container or a stop handler.
    assert payload["settingsFile"] == generated_path.as_posix()
    assert payload["dryRun"] is True
    assert payload["enabled"] == {GREPAI_ID: False, CGC_ID: False}
    assert payload["results"] == []
    assert payload["recoveryActions"] == []


def test_install_stop_route_carries_authority_settings_not_generated_document(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The install stop hands ``watchers_run`` the authority settings, not the empty document."""

    captured: dict[str, Any] = {}

    def capture(settings_args: argparse.Namespace, action: str) -> dict[str, Any]:
        settings_path = Path(settings_args.from_settings)
        captured["path"] = settings_path
        captured["document"] = json.loads(settings_path.read_text(encoding="utf-8"))
        captured["action"] = action
        captured["dry_run"] = settings_args.dry_run
        return {
            "provider": "watchers",
            "action": action,
            "ok": True,
            "partial": False,
            "results": [],
        }

    monkeypatch.setattr(lifecycle, "watchers_run", capture)

    rebind = ProviderWatcherRebind(
        coordination_root=tmp_path,
        settings=legacy_authority_settings(),
        dry_run=True,
        timeout=5,  # load-independent: dry-run field; nothing in this path waits on it
    )

    result = run_provider_watcher_lifecycle(rebind, "stop")

    assert captured["action"] == "stop"
    assert captured["dry_run"] is True
    handed_over = captured["document"]["contextProviders"]
    assert handed_over["enabled"] is True
    assert handed_over["providers"] == {GREPAI_ID: {"enabled": True}}
    assert provider_enabled(captured["document"], GREPAI_ID) is True
    assert result["action"] == "stop"
    # The generated document is not what this route reads.
    assert captured["document"] != lifecycle_settings_from_config()


def _recording_dispatch(calls: list[tuple[str, str]]) -> Any:
    """A stand-in for a provider stop handler that records instead of stopping."""

    def dispatch(_args: argparse.Namespace, action: str) -> dict[str, Any]:
        calls.append(("dispatch", action))
        return {"provider": "grepai", "action": action, "ok": True}

    return dispatch


def test_an_enabling_document_invokes_the_real_stop_handler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The positive half of the boundary: selection actually reaches a handler.

    Without this case the empty result list could mean "the gate let nothing through" or
    "the dispatch is broken for everyone". This document enables GrepAI the way a legacy
    authority file does, and the stop handler is then invoked exactly once, with the
    provider-level action the map names.
    """

    legacy_path = write_settings(tmp_path, legacy_authority_settings(), "legacy-settings.json")
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(watchers, "grepai_run", _recording_dispatch(calls))
    monkeypatch.setattr(
        watchers,
        "cgc_stop_all",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cgc is not enabled in this document")
        ),
    )

    args = argparse.Namespace(
        provider="watchers",
        action="stop",
        from_settings=legacy_path,
        coordination_root=tmp_path,
        dry_run=True,
        json=True,
        timeout=5,  # load-independent: dry-run field; nothing in this path waits on it
    )

    payload = watchers.watchers_run(args, "stop")

    assert calls == [("dispatch", watchers.WATCHER_PROVIDER_ACTIONS["grepai"]["stop"])]
    assert payload["enabled"] == {GREPAI_ID: True, CGC_ID: False}
    assert [result["provider"] for result in payload["results"]] == ["grepai"]
    assert payload["ok"] is True


def test_the_generated_document_invokes_no_stop_handler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The negative half, measured by invocation count rather than by absence of output.

    Same command, same map, same dispatcher: the only difference is the settings document.
    Zero recorded calls is what makes "the tick stops nothing today" a statement about the
    gate and not about the code being unable to stop anything.
    """

    generated_path = write_settings(
        tmp_path, lifecycle_settings_from_config(), "generated-settings.json"
    )
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(watchers, "grepai_run", _recording_dispatch(calls))
    monkeypatch.setattr(watchers, "cgc_stop_all", _recording_dispatch(calls))

    args = argparse.Namespace(
        provider="watchers",
        action="stop",
        from_settings=generated_path,
        coordination_root=tmp_path,
        dry_run=True,
        json=True,
        timeout=5,  # load-independent: dry-run field; nothing in this path waits on it
    )

    payload = watchers.watchers_run(args, "stop")

    assert calls == [], "a stop handler ran from a document that enables no provider"
    assert payload["results"] == []
    assert payload["enabled"] == {GREPAI_ID: False, CGC_ID: False}
