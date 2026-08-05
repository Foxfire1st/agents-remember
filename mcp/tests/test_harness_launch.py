from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.errors import HarnessControlError
from agents_remember.serving.claude_stream_capabilities import _select_current_model
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    EffortOption,
    LaunchKnobs,
    ModelCapability,
)
from agents_remember.serving.harness_control_factories import (
    BUILTIN_PROTOCOL_HARNESSES,
    harness_launch_knobs,
)
from agents_remember.serving.harness_control_models import ControlIdentity, LaunchSpec
from agents_remember.serving.harness_launch import (
    ResolvedLaunch,
    apply_launch_knobs,
    resolve_settings_launch,
    validate_launch_selection,
    verify_effective_launch,
)

NOW = "2026-07-15T20:00:00+00:00"


def _selection(
    *,
    harness: str = "claude",
    model: str = "claude-fable-5",
    effort: str = "max",
) -> ResolvedLaunch:
    return ResolvedLaunch(harness, model, effort, Path("/workspace"))


def _snapshot(
    *,
    selected_model: str | None = None,
    selected_effort: str | None = None,
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        models=(
            ModelCapability(
                key="fable",
                resolved_model="claude-fable-5",
                display_name="Fable 5",
                supports_effort=True,
                effort_options=(
                    EffortOption("high", "high"),
                    EffortOption("max", "max"),
                ),
                default_effort="high",
            ),
        ),
        selected_model_key=selected_model,
        selected_effort=selected_effort,
    )


def test_resolved_launch_round_trips_and_requires_complete_settings() -> None:
    selection = _selection()

    assert ResolvedLaunch.from_json(selection.to_json()) == selection
    assert (
        resolve_settings_launch(
            harness_id="claude",
            model="claude-fable-5",
            effort="max",
            workspace=Path("/workspace"),
        )
        == selection
    )
    with pytest.raises(HarnessControlError, match="missing model"):
        resolve_settings_launch(
            harness_id="claude",
            model=None,
            effort="max",
            workspace=Path("/workspace"),
        )
    with pytest.raises(HarnessControlError, match="missing effort"):
        resolve_settings_launch(
            harness_id="claude",
            model="claude-fable-5",
            effort=None,
            workspace=Path("/workspace"),
        )


def test_dynamic_validation_accepts_unique_resolved_model_and_model_gated_effort() -> None:
    selected = validate_launch_selection(_selection(), _snapshot())

    assert selected.key == "fable"
    with pytest.raises(HarnessControlError, match=r"advertised launch efforts: \[high, max\]"):
        validate_launch_selection(_selection(effort="ultracode"), _snapshot())
    with pytest.raises(HarnessControlError, match="absent from the dynamic catalog"):
        validate_launch_selection(_selection(model="claude-unknown"), _snapshot())


def test_pi_requires_the_exact_provider_qualified_catalog_key() -> None:
    snapshot = CapabilitySnapshot(
        models=(
            ModelCapability(
                key="deepseek/deepseek-v4-flash",
                resolved_model="deepseek-v4-flash",
                display_name="DeepSeek V4 Flash",
                supports_effort=True,
                effort_options=(EffortOption("max", "max"),),
                default_effort="max",
            ),
        ),
        selected_model_key=None,
        selected_effort=None,
    )

    with pytest.raises(
        HarnessControlError,
        match=(
            r"must use an exact provider-qualified catalog key; matching alternatives: "
            r"\[deepseek/deepseek-v4-flash\]"
        ),
    ):
        validate_launch_selection(
            _selection(harness="pi", model="deepseek-v4-flash"),
            snapshot,
        )
    selected = validate_launch_selection(
        _selection(harness="pi", model="deepseek/deepseek-v4-flash"),
        snapshot,
    )
    assert selected.key == "deepseek/deepseek-v4-flash"


def test_effective_launch_compares_vendor_echo_to_the_canonical_catalog_key() -> None:
    verify_effective_launch(
        _selection(),
        _snapshot(selected_model="fable", selected_effort=None),
        require_effort_echo=False,
    )
    with pytest.raises(HarnessControlError, match="running harness reported 'other'"):
        verify_effective_launch(
            _selection(),
            _snapshot(selected_model="other", selected_effort="max"),
            require_effort_echo=True,
        )
    with pytest.raises(HarnessControlError, match="reported 'high'"):
        verify_effective_launch(
            _selection(),
            _snapshot(selected_model="fable", selected_effort="high"),
            require_effort_echo=True,
        )


def _aliased_snapshot(*, selected_model: str | None) -> CapabilitySnapshot:
    """Claude 2.1.216-shape catalog: ``default`` and ``opus[1m]`` alias one resolved model."""

    efforts = (
        EffortOption("low", "low"),
        EffortOption("medium", "medium"),
        EffortOption("high", "high"),
    )
    return CapabilitySnapshot(
        models=(
            ModelCapability(
                key="default",
                resolved_model="claude-opus-4-8[1m]",
                display_name="Default",
                supports_effort=True,
                effort_options=efforts,
                default_effort="medium",
                is_default=True,
            ),
            ModelCapability(
                key="opus[1m]",
                resolved_model="claude-opus-4-8[1m]",
                display_name="Opus 4.8 (1M context)",
                supports_effort=True,
                effort_options=efforts,
                default_effort="medium",
                is_default=False,
            ),
        ),
        selected_model_key=selected_model,
        selected_effort=None,
    )


def test_effective_launch_accepts_alias_collapsed_onto_default_resolved_model() -> None:
    # 260718-CHATS-L5F R2: a launch that explicitly requested ``opus[1m]`` natively succeeds, but
    # the running harness echoes the resolved id which the catalog also aliases as ``default``.
    # Because both keys resolve to the same underlying model the launch VALIDATES — it must not be
    # refused as "requested provenance — never validated". Regression pin for the developer's
    # image3 refused pair.
    verify_effective_launch(
        _selection(harness="claude", model="opus[1m]", effort="medium"),
        _aliased_snapshot(selected_model="default"),
        require_effort_echo=False,
    )


def test_effective_launch_still_refuses_a_genuinely_different_model() -> None:
    # The resolved-identity acceptance never masks a truly different model: ``other`` is not in the
    # catalog at all, so there is no resolved-model to compare and the strict check still fires.
    with pytest.raises(HarnessControlError, match="running harness reported 'other'"):
        verify_effective_launch(
            _selection(harness="claude", model="opus[1m]", effort="medium"),
            _aliased_snapshot(selected_model="other"),
            require_effort_echo=False,
        )


def test_select_current_model_prefers_requested_alias_over_default_collapse() -> None:
    models = _aliased_snapshot(selected_model=None).models
    # No requested key -> the historical default-collapse.
    assert _select_current_model(models, "claude-opus-4-8[1m]").key == "default"
    # The requested alias wins when its resolved model matches the echoed resolution.
    assert (
        _select_current_model(models, "claude-opus-4-8[1m]", requested_key="opus[1m]").key
        == "opus[1m]"
    )


def test_apply_launch_knobs_preserves_fixed_argv_and_refuses_duplicate_authority() -> None:
    launch = LaunchSpec(
        identity=ControlIdentity("session", "tmux", NOW),
        harness_id="pi",
        cwd=Path("/workspace"),
        argv=("pi", "--no-session"),
        env={"KEEP": "yes"},
    )

    applied = apply_launch_knobs(
        launch,
        LaunchKnobs(
            argv=("--model", "provider/model", "--thinking", "high"),
            env={"ADAPTER": "yes"},
        ),
    )

    assert applied.argv == (
        "pi",
        "--model",
        "provider/model",
        "--thinking",
        "high",
        "--no-session",
    )
    assert applied.env == {"KEEP": "yes", "ADAPTER": "yes"}
    with pytest.raises(HarnessControlError, match=r"adapter-owned option.*--model"):
        apply_launch_knobs(
            LaunchSpec(
                identity=launch.identity,
                harness_id="pi",
                cwd=launch.cwd,
                argv=("pi", "--model=other"),
            ),
            LaunchKnobs(argv=("--model", "provider/model")),
        )


@pytest.mark.parametrize(
    ("argv", "knobs", "conflict"),
    (
        (
            ("claude", "--effort", "high"),
            LaunchKnobs(
                argv=("--model", "fable", "--effort", "max"),
                owned_argv_options=("--model", "--effort"),
            ),
            "--effort",
        ),
        (
            ("codex", "-m", "other"),
            LaunchKnobs(
                session_config={"model": "gpt-test"},
                owned_argv_options=("--model", "-m"),
            ),
            "-m",
        ),
        (
            ("codex", "--config=model_reasoning_effort=low"),
            LaunchKnobs(
                session_config={"model_reasoning_effort": "high"},
                owned_config_keys=("model_reasoning_effort",),
            ),
            "model_reasoning_effort",
        ),
    ),
)
def test_apply_launch_knobs_refuses_every_adapter_owned_override(
    argv: tuple[str, ...], knobs: LaunchKnobs, conflict: str
) -> None:
    launch = LaunchSpec(
        identity=ControlIdentity("session", "tmux", NOW),
        harness_id=argv[0],
        cwd=Path("/workspace"),
        argv=argv,
    )

    with pytest.raises(HarnessControlError, match=conflict):
        apply_launch_knobs(launch, knobs)


CODEX_OWNED_KNOBS = LaunchKnobs(
    session_config={
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "xhigh",
    },
    owned_argv_options=("--model", "-m"),
    owned_config_keys=("model", "model_reasoning_effort"),
)


@pytest.mark.parametrize(
    "tail",
    (
        ("--model", "other"),
        ("--model=other",),
        ("-m", "other"),
        ("-mother",),
        ("-m=other",),
        ("--config", "model=other"),
        ("--config=model=other",),
        ("-c", "model=other"),
        ("-c=model=other",),
        ("-cmodel=other",),
        ("--config", "model_reasoning_effort=low"),
        ("--config=model_reasoning_effort=low",),
        ("-c", "model_reasoning_effort=low"),
        ("-c=model_reasoning_effort=low",),
        ("-cmodel_reasoning_effort=low",),
    ),
)
def test_codex_installed_owned_selector_spellings_all_refuse(
    tail: tuple[str, ...],
) -> None:
    launch = LaunchSpec(
        identity=ControlIdentity("session", "tmux", NOW),
        harness_id="codex",
        cwd=Path("/workspace"),
        argv=("codex", "app-server", *tail),
    )

    with pytest.raises(HarnessControlError, match="adapter-owned"):
        apply_launch_knobs(launch, CODEX_OWNED_KNOBS)


@pytest.mark.parametrize(
    "tail",
    (
        ("--sandbox", "workspace-write"),
        ("--config", "feature=true"),
        ("--config=feature=true",),
        ("-c", "feature=true"),
        ("-c=feature=true",),
        ("-cfeature=true",),
    ),
)
def test_codex_unrelated_launch_arguments_pass_owned_selector_preflight(
    tail: tuple[str, ...],
) -> None:
    launch = LaunchSpec(
        identity=ControlIdentity("session", "tmux", NOW),
        harness_id="codex",
        cwd=Path("/workspace"),
        argv=("codex", "app-server", *tail),
    )

    applied = apply_launch_knobs(launch, CODEX_OWNED_KNOBS)

    assert applied.argv == launch.argv


# --- launch-selection refusals, one contract across all three built-in harnesses -----------
#
# `claude_launch_knobs`, `codex_launch_knobs` and `pi_launch_knobs` each open with the same
# two guards, and each spells the resulting knobs differently (Claude and Pi as argv, Codex
# as session config). Driving them through `harness_launch_knobs` -- the registry the
# adapter factory itself uses -- asserts the contract rather than three implementations of
# it, and a fourth harness added to `BUILTIN_PROTOCOL_HARNESSES` is held to it without an
# edit here.
#
# WHY REFUSAL AND NOT NORMALISATION. A padded ` high` is not a typo the launcher may quietly
# fix: the same string is compared against the vendor's echo by `verify_effective_launch`,
# so a selection silently stripped here would be launched as one value and verified as
# another. The guards say so by refusing, and these cases fail if anyone replaces them with
# a `.strip()`.


def _knob_values(knobs: LaunchKnobs) -> set[str]:
    return {*knobs.argv, *(str(value) for value in knobs.session_config.values())}


@pytest.mark.parametrize("harness_id", sorted(BUILTIN_PROTOCOL_HARNESSES))
@pytest.mark.parametrize(
    ("model_key", "effort", "subject"),
    (
        ("", "high", "model"),
        ("   ", "high", "model"),
        (" provider/model", "high", "model"),
        ("provider/model\t", "high", "model"),
        ("provider/model", None, "effort"),
        ("provider/model", "", "effort"),
        ("provider/model", " high", "effort"),
        ("provider/model", "high ", "effort"),
    ),
)
def test_every_harness_refuses_a_blank_or_padded_launch_selection(
    harness_id: str, model_key: str, effort: str | None, subject: str
) -> None:
    with pytest.raises(
        HarnessControlError, match=rf"launch {subject} must be non-empty with no outer whitespace"
    ):
        harness_launch_knobs(harness_id, model_key=model_key, effort=effort)


@pytest.mark.parametrize("harness_id", sorted(BUILTIN_PROTOCOL_HARNESSES))
def test_every_harness_carries_a_clean_selection_into_its_own_launch_vocabulary(
    harness_id: str,
) -> None:
    # The other half of the refusals above: the guards reject exactly the malformed pairs and
    # let a well-formed one through into whichever vocabulary that harness owns.
    knobs = harness_launch_knobs(harness_id, model_key="provider/model", effort="high")

    assert {"provider/model", "high"} <= _knob_values(knobs)
