"""L192-F07: the provider-stop prose must describe the chain it actually runs.

Both reference documents say when a leftover provider stack can be stopped. Round 1
required them to name the independent periodic safety loop, and they did. Round 2
measured the stronger claim that the same tick actually stops the stack and found it
false: the tick calls ``run_configured_watchers``, which builds the GENERATED
lifecycle settings, and that document declares no providers after retirement
(``{"enabled": false, "providers": {}}``), so ``provider_enabled`` answers False for
both retired ids and ``watcher_provider_results`` calls no provider stop handler. The
install-time route reads the authority settings document instead, and that one still
parses legacy provider keys.

So the prose is bound to the whole chain, not to the callback: the cadence, the gate,
the stage order, the store home, the settings document each caller reads and what that
document lets it select are read from the modules the loop actually runs.
``test_the_retained_loop_is_still_the_independent_stop_caller`` stays as the other half
of the guarantee: the prose may not be made true by switching the safety loop off.
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import re
from pathlib import Path

import pytest
from agents_remember.install import provider_watchers
from agents_remember.install import runtime as install_runtime
from agents_remember.kernel.primitives.provider_degradation_settings import (
    ProviderDegradationSettings,
    parse_provider_degradation_settings,
)
from agents_remember.providers import degradation, metrics
from agents_remember.providers.lifecycle.provider_settings import provider_enabled
from agents_remember.providers.lifecycle.watchers import watcher_provider_results
from agents_remember.providers.settings import lifecycle_settings_from_config
from agents_remember.providers.watcher_service import run_configured_watchers
from agents_remember.serving import _app_lifespan as lifespan

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_REFERENCE = REPO_ROOT / "docs" / "reference"
SETTINGS_REFERENCE = DOCS_REFERENCE / "settings-json.md"
RUNTIME_LAYOUT = DOCS_REFERENCE / "runtime-layout.md"

# The cadence the loop actually sleeps, rendered the way the prose must say it.
CADENCE = f"every {metrics.DEFAULT_SAMPLE_INTERVAL_SECONDS:g} seconds"

# The loop's stage vocabulary, as stems so a tense change in prose is not a failure.
STAGE_STEMS = {
    "sample": ("sampl",),
    "record": ("record",),
    "degrade/evaluate": ("evaluat",),
    "stop": ("stop",),
    "store/compact": ("compact",),
}

# Ways the prose may say the loop is not scoped to an install or a cleanup.
CONTINUITY_MARKERS = (
    "as long as the dashboard is up",
    "independently of any install",
    "not only during an install",
)

# The three routes that stop a leftover stack today, two of them install/cleanup.
INSTALL_OR_CLEANUP_ROUTES = ("runtime_install", "install refresh", "providers-remove")

STOP_WORD = re.compile(r"\bstopp?(?:ed|ing|s)?\b")
# "only" tied to the stop itself, not to some unrelated noun in the paragraph.
EXCLUSIVE_STOP = re.compile(
    r"\b(?:only|exclusively)\b[^.]{0,80}?\bstopp?",
    re.IGNORECASE,
)


def _paragraphs(path: Path) -> list[str]:
    """The document's blank-line separated blocks, each with whitespace flattened."""

    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", text)
    return [re.sub(r"\s+", " ", block).strip() for block in blocks if block.strip()]


def _loop_paragraphs(path: Path) -> list[str]:
    """Paragraphs that describe the periodic safety loop and its gate."""

    return [
        paragraph
        for paragraph in _paragraphs(path)
        if CADENCE in paragraph.lower() and "failsafeenabled" in paragraph.lower()
    ]


def _store_home() -> str:
    """The provider observer home, read from the stores the loop writes."""

    root = Path("/nonexistent/coordination-root")
    degradation_home = degradation.ProviderDegradationStore(root).state_path.parent
    metrics_home = metrics.ProviderMetricsStore(root).log_path.parent
    assert degradation_home == metrics_home
    return degradation_home.relative_to(root).as_posix()


def _loop_paragraph(path: Path) -> str:
    paragraphs = _loop_paragraphs(path)
    assert paragraphs, (
        f"{path.name} must state the retained periodic provider safety loop: the serving "
        f"lifespan's _metrics_loop runs it {CADENCE} and failSafeEnabled gates its stop"
    )
    assert len(paragraphs) == 1, f"{path.name} states the loop in more than one paragraph"
    return paragraphs[0]


def test_the_retained_loop_is_still_the_independent_stop_caller() -> None:
    """The source half: the safety loop exists, stops, and is not switched off."""

    loop_source = inspect.getsource(lifespan._metrics_loop)
    for stage, stems in STAGE_STEMS.items():
        if stage == "stop":
            continue
        assert any(stem in loop_source for stem in stems), f"loop no longer {stage}s"
    assert "sample_provider_containers" in loop_source
    assert "metrics_store.record" in loop_source
    assert "evaluate_provider_degradation" in loop_source
    assert "metrics_store.compact" in loop_source
    assert "asyncio.sleep(DEFAULT_SAMPLE_INTERVAL_SECONDS)" in loop_source

    # The stop is supplied BY this loop, and it is the watcher stop the other
    # routes use -- not an install refresh and not providers-remove.
    assert "run_configured_watchers" in loop_source
    assert 'action="stop"' in loop_source
    stopper = inspect.signature(degradation.evaluate_provider_degradation).parameters[
        "stop_provider_stacks"
    ]
    assert stopper.default is inspect.Parameter.empty, "the detector must not default its stopper"

    # The gate is the documented key, on the transition into critical.
    detector_source = inspect.getsource(degradation.evaluate_provider_degradation)
    assert "settings.fail_safe_enabled" in detector_source
    assert "_run_critical_failsafe" in detector_source

    # The lifespan starts the loop unconditionally: prose may not be repaired by
    # switching the safety loop off.
    lifespan_source = inspect.getsource(lifespan._serving_lifespan)
    assert "metrics_task = asyncio.create_task" in lifespan_source
    assert "_metrics_loop(runtime.config" in lifespan_source


def test_failsafe_key_is_the_documented_gate() -> None:
    """The documented key ``failSafeEnabled`` is the field the detector reads."""

    assert ProviderDegradationSettings().fail_safe_enabled is True
    assert parse_provider_degradation_settings({"failSafeEnabled": True}).fail_safe_enabled is True
    assert (
        parse_provider_degradation_settings({"failSafeEnabled": False}).fail_safe_enabled is False
    )


@pytest.mark.parametrize(
    ("path", "stages", "store_name"),
    [
        pytest.param(
            SETTINGS_REFERENCE,
            ("sample", "record", "degrade/evaluate", "stop", "store/compact"),
            metrics.ProviderMetricsStore(Path("/root")).log_path.name,
            id="settings-reference",
        ),
        pytest.param(
            RUNTIME_LAYOUT,
            ("sample", "degrade/evaluate", "stop"),
            None,
            id="runtime-layout",
        ),
    ],
)
def test_the_document_states_the_periodic_sample_degrade_stop_loop(
    path: Path, stages: tuple[str, ...], store_name: str | None
) -> None:
    """Each document must carry the loop, its cadence, its stages and its gate."""

    paragraph = _loop_paragraph(path)
    lowered = paragraph.lower()

    for stage in stages:
        stems = STAGE_STEMS[stage]
        assert any(stem in lowered for stem in stems), (
            f"{path.name} describes the loop without its {stage} stage"
        )

    # The gate, the cadence, and the store the loop records into.
    assert "failsafeenabled" in lowered
    assert CADENCE in lowered
    assert _store_home() in paragraph
    if store_name is not None:
        assert store_name in paragraph

    # The loop is independent of install/cleanup, and it revives nothing.
    assert any(marker in lowered for marker in CONTINUITY_MARKERS), (
        f"{path.name} must say the loop runs independently of an install or a cleanup"
    )
    assert "not a revival" in lowered and "starts nothing" in lowered


def test_the_stop_reads_the_authority_settings_not_the_generated_document() -> None:
    """e7f19b15: the safety stop's settings source is the authority file.

    Before the amendment the tick built its stop from ``write_lifecycle_settings()``, whose
    document declares no providers, so the stop selected nothing whatever the sample found. The
    stop now hands the caller's authority settings path to the same reader chain the install's
    old-watcher stop uses; the generated document is only the fallback when no authority file
    exists. ``lifecycle_settings_from_config()`` itself stays empty (R112 F02).
    """

    source = inspect.getsource(run_configured_watchers)
    assert "config.config_path" in source, "the stop no longer reads the authority settings path"
    assert "is_file()" in source, "the authority document is not the primary input"
    assert "write_lifecycle_settings()" in source, "the no-authority fallback is gone"

    generated = lifecycle_settings_from_config()
    assert generated["contextProviders"]["enabled"] is False
    assert generated["contextProviders"]["providers"] == {}
    for provider_id in ("grepai-memory", "codegraphcontext-code"):
        assert provider_enabled(generated, provider_id) is False

    legacy = {
        "contextProviders": {
            "enabled": True,
            "providers": {"grepai-memory": {"enabled": True}},
        }
    }
    assert provider_enabled(legacy, "grepai-memory") is True
    assert watcher_provider_results(argparse.Namespace(), "stop", False, False) == []


def test_the_install_route_reads_the_authority_document_not_the_generated_one() -> None:
    """The contrast the prose relies on, bound to the install module itself."""

    stop_source = inspect.getsource(provider_watchers.stop_provider_watchers_before_refresh)
    assert 'action="stop"' in stop_source
    run_source = inspect.getsource(provider_watchers.run_provider_watcher_lifecycle)
    assert "write_temp_provider_settings(rebind.settings)" in run_source

    field_names = {
        field.name for field in dataclasses.fields(provider_watchers.ProviderWatcherRebind)
    }
    assert "settings" in field_names, "the rebind no longer carries the settings document"
    # Where the install gets that document: the authority settings' own provider block.
    assert "provider_settings = scope.provider_deps.settings" in inspect.getsource(install_runtime)


@pytest.mark.parametrize("path", [SETTINGS_REFERENCE, RUNTIME_LAYOUT], ids=["settings", "layout"])
def test_each_document_states_which_document_each_stop_reader_can_select_from(path: Path) -> None:
    """The prose must carry the consequence, not only the callback.

    A document that says the tick stops the stack is wrong. A document that says the tick
    calls the stop, names the generated settings document it runs against, and says that
    document declares no provider is what the source supports.
    """

    text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
    lowered = text.lower()

    assert '"enabled": false, "providers": {}' in text, (
        f"{path.name} must show the generated document it runs the stop against"
    )
    assert "declares n" in lowered and "provider" in lowered, (
        f"{path.name} must say the generated document declares no provider"
    )
    assert "selects nothing" in lowered or "selects no provider" in lowered, (
        f"{path.name} must state what happens when the settings name no provider"
    )
    assert "generated" in lowered and "not what a stop reads" in lowered, (
        f"{path.name} must keep the generated document and the stop's document apart"
    )
    assert "authority settings file" in lowered and "contextproviders" in lowered, (
        f"{path.name} must name the document the install-time stop reads instead"
    )
    # The three callers stay named, and the loop is still described as a caller.
    for route in ("runtime_install", "providers-remove"):
        assert route in text, f"{path.name} no longer names {route}"
    assert CADENCE in lowered
    assert "failsafeenabled" in lowered
