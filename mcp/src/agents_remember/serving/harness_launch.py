"""Settings-resolved launch selection and dynamic catalog validation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    LaunchSpec,
)
from agents_remember.serving.harness_capabilities import (
    CapabilitySnapshot,
    LaunchKnobs,
    ModelCapability,
)


@dataclass(frozen=True)
class ResolvedLaunch:
    """The single settings-owned harness/model/effort selection for one workspace."""

    harness_id: str
    model_key: str
    effort: str
    workspace: Path

    def __post_init__(self) -> None:
        for label, value in (
            ("harness", self.harness_id),
            ("model", self.model_key),
            ("effort", self.effort),
        ):
            if not value or value != value.strip():
                raise HarnessControlError(
                    f"resolved launch {label} must be non-empty with no outer whitespace"
                )

    def to_json(self) -> dict[str, str]:
        return {
            "harnessId": self.harness_id,
            "modelKey": self.model_key,
            "effort": self.effort,
            "workspace": str(self.workspace),
        }

    @classmethod
    def from_json(cls, raw: object) -> ResolvedLaunch:
        if not isinstance(raw, dict):
            raise HarnessControlError("resolved launch must be an object")
        return cls(
            harness_id=_required_text(raw, "harnessId"),
            model_key=_required_text(raw, "modelKey"),
            effort=_required_text(raw, "effort"),
            workspace=Path(_required_text(raw, "workspace")),
        )


def resolve_settings_launch(
    *,
    harness_id: str,
    model: str | None,
    effort: str | None,
    workspace: Path,
) -> ResolvedLaunch:
    """Require the complete settings selection instead of falling back to vendor defaults."""

    if model is None:
        raise HarnessControlError(f"settings launch for harness {harness_id!r} is missing model")
    if effort is None:
        raise HarnessControlError(f"settings launch for harness {harness_id!r} is missing effort")
    return ResolvedLaunch(
        harness_id=harness_id,
        model_key=model,
        effort=effort,
        workspace=workspace,
    )


def validate_launch_selection(
    selection: ResolvedLaunch,
    snapshot: CapabilitySnapshot,
) -> ModelCapability:
    """Validate model and effort against one auth-accurate, model-gated catalog."""

    exact = next(
        (model for model in snapshot.models if model.key == selection.model_key),
        None,
    )
    resolved = tuple(
        model for model in snapshot.models if model.resolved_model == selection.model_key
    )
    if selection.harness_id == "pi" and exact is None and resolved:
        alternatives = ", ".join(model.key for model in resolved)
        raise HarnessControlError(
            f"pi launch model {selection.model_key!r} must use an exact provider-qualified "
            f"catalog key; matching alternatives: [{alternatives}]"
        )
    selected = exact or (resolved[0] if len(resolved) == 1 else None)
    if selected is None:
        advertised = ", ".join(model.key for model in snapshot.models) or "<none>"
        raise HarnessControlError(
            f"{selection.harness_id} launch model {selection.model_key!r} is absent from the "
            f"dynamic catalog; advertised models: [{advertised}]"
        )
    if not selected.selectable:
        raise HarnessControlError(
            f"{selection.harness_id} launch model {selection.model_key!r} is advertised but "
            "not selectable on this install/account"
        )
    launch_efforts = tuple(
        option.key for option in selected.effort_options if option.launch_settable
    )
    if selection.effort not in launch_efforts:
        advertised = ", ".join(launch_efforts) or "<none>"
        raise HarnessControlError(
            f"{selection.harness_id} launch effort {selection.effort!r} is not advertised as "
            f"launch-settable for model {selected.key!r}; advertised launch efforts: "
            f"[{advertised}]"
        )
    return selected


def verify_effective_launch(
    selection: ResolvedLaunch,
    snapshot: CapabilitySnapshot,
    *,
    require_effort_echo: bool,
) -> None:
    """Require the running harness to echo the selected values when its protocol can."""

    selected = validate_launch_selection(selection, snapshot)
    if snapshot.selected_model_key != selected.key and not _resolves_to_same_model(
        selected, snapshot
    ):
        raise HarnessControlError(
            f"{selection.harness_id} launch selected model {selection.model_key!r}, but the "
            f"running harness reported {snapshot.selected_model_key!r}"
        )
    if snapshot.selected_effort is None:
        if require_effort_echo:
            raise HarnessControlError(
                f"{selection.harness_id} launch did not report an effective effort"
            )
        return
    if snapshot.selected_effort != selection.effort:
        raise HarnessControlError(
            f"{selection.harness_id} launch selected effort {selection.effort!r}, but the "
            f"running harness reported {snapshot.selected_effort!r}"
        )


def _resolves_to_same_model(selected: ModelCapability, snapshot: CapabilitySnapshot) -> bool:
    """Accept an alias/default key collision where both keys resolve to one underlying model.

    Claude aliases several catalog keys onto one ``resolved_model`` (the "1M context" variants and
    ``default`` share a resolved id); the running harness echoes only the resolved id, which
    ``_select_current_model`` may map back to a different-but-equivalent key. When the requested
    selection and the reported key resolve to the SAME underlying model the launch succeeded — the
    distinction is cosmetic. A genuinely different model (different or absent ``resolved_model``)
    still fails the strict check.
    """

    if selected.resolved_model is None:
        return False
    reported = next(
        (model for model in snapshot.models if model.key == snapshot.selected_model_key),
        None,
    )
    if reported is None or reported.resolved_model is None:
        return False
    return reported.resolved_model == selected.resolved_model


def apply_launch_knobs(launch: LaunchSpec, knobs: LaunchKnobs) -> LaunchSpec:
    """Apply one adapter's native argv/env delta without allowing duplicate selectors."""

    if not launch.argv:
        raise HarnessControlError("harness launch requires a non-empty argv")
    owned_options = set(knobs.owned_argv_options) | {
        argument.split("=", 1)[0] for argument in knobs.argv if argument.startswith("-")
    }
    duplicates = sorted(_owned_argv_overrides(launch.argv[1:], owned_options))
    duplicate_config_keys = sorted(
        set(knobs.owned_config_keys) & _config_override_keys(launch.argv[1:])
    )
    if duplicates:
        raise HarnessControlError(
            f"{launch.harness_id} launch arguments already declare adapter-owned option(s): "
            + ", ".join(duplicates)
        )
    if duplicate_config_keys:
        raise HarnessControlError(
            f"{launch.harness_id} launch arguments already declare adapter-owned config key(s): "
            + ", ".join(duplicate_config_keys)
        )
    env = dict(launch.env)
    for key, value in knobs.env.items():
        if key in env and env[key] != value:
            raise HarnessControlError(
                f"{launch.harness_id} launch environment conflicts on adapter-owned {key}"
            )
        env[key] = value
    return replace(
        launch,
        argv=(launch.argv[0], *knobs.argv, *launch.argv[1:]),
        env=env,
    )


def _config_override_keys(argv: tuple[str, ...]) -> set[str]:
    """Return keys from every installed Codex ``--config/-c`` value spelling."""

    keys: set[str] = set()
    index = 0
    while index < len(argv):
        argument = argv[index]
        value: str | None = None
        if argument in {"--config", "-c"} and index + 1 < len(argv):
            value = argv[index + 1]
            index += 2
        elif argument.startswith("--config="):
            value = argument.removeprefix("--config=")
            index += 1
        elif argument.startswith("-c") and argument != "-c":
            value = argument.removeprefix("-c").removeprefix("=")
            index += 1
        else:
            index += 1
        if value is not None and "=" in value:
            keys.add(value.split("=", 1)[0])
    return keys


def _owned_argv_overrides(argv: tuple[str, ...], owned_options: set[str]) -> set[str]:
    """Return exact, equals-attached, and accepted short-attached owned selectors."""

    matches: set[str] = set()
    for argument in argv:
        for option in owned_options:
            if argument == option or argument.startswith(f"{option}="):
                matches.add(option)
                continue
            if (
                option.startswith("-")
                and not option.startswith("--")
                and argument.startswith(option)
            ):
                # Codex/clap accepts short option values as ``-mVALUE`` as well as
                # ``-m=VALUE``. Long options require the explicit equals separator above.
                matches.add(option)
    return matches


def _required_text(raw: dict[object, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"resolved launch requires non-empty {key}")
    return value
