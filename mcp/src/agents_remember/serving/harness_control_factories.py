"""Built-in protocol-adapter construction from settings-owned spawn state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import ControlIdentity, LaunchSpec
from agents_remember.serving.capsule_delivery import CodexCapsuleDelivery
from agents_remember.serving.codex_app_server_adapter import CodexAppServerAdapter
from agents_remember.serving.codex_app_server_session import (
    CodexAppServerSettings,
    codex_launch_knobs,
)
from agents_remember.serving.eve_adapter import EveSessionAdapter
from agents_remember.serving.eve_runtime_launch import (
    EveLaunchSelection,
    eve_launch_knobs,
    launch_spec_selection,
)
from agents_remember.serving.harness_capabilities import LaunchKnobs
from agents_remember.serving.harness_control_adapter import (
    LaunchableHarnessProtocolAdapter,
    UnsupportedHarnessProtocolAdapter,
)
from agents_remember.serving.harness_control_claude import (
    ClaudeStreamJsonAdapter,
    claude_launch_knobs,
)
from agents_remember.serving.harness_launch import ResolvedLaunch
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from agents_remember.serving.pi_rpc_protocol import pi_launch_knobs

BUILTIN_PROTOCOL_HARNESSES = frozenset({"claude", "codex", "pi", "eve"})

_LAUNCH_KNOBS = {
    "claude": claude_launch_knobs,
    "codex": codex_launch_knobs,
    "pi": pi_launch_knobs,
    "eve": eve_launch_knobs,
}


def harness_launch_knobs(harness_id: str, *, model_key: str, effort: str | None) -> LaunchKnobs:
    """How one harness spells a model/effort selection at launch, before any adapter exists.

    Answered from the harness id alone because none of the three implementations reads adapter
    state; a custom id has no native launch vocabulary and is refused rather than defaulted.
    """

    knobs = _LAUNCH_KNOBS.get(harness_id)
    if knobs is None:
        raise HarnessControlError(f"no protocol adapter is registered for {harness_id!r}")
    return knobs(model_key=model_key, effort=effort)


@dataclass(frozen=True, slots=True)
class LaunchSelection:
    """The settings-owned selection and the launch knobs it implies, as one value.

    These two were always supplied together: the factory refuses a ``resolved_launch`` that arrives
    without ``launch_knobs``, because the knobs are what the selection means at launch time. Pairing
    them makes that invariant structural and keeps the factory's parameter list within the repo's
    argument limit.
    """

    resolved_launch: ResolvedLaunch
    launch_knobs: LaunchKnobs


def _require_consistent_launch(
    harness_id: str,
    selection: LaunchSelection | None,
) -> None:
    """A resolved selection must belong to this adapter and arrive with its launch knobs."""

    if selection is None:
        return
    if selection.resolved_launch.harness_id != harness_id:
        raise HarnessControlError(
            "resolved launch harness does not match the adapter factory harness"
        )


def _require_launch_knobs_present(selection: LaunchSelection | None) -> None:
    """The pair is structural now, but a caller may still hand-build one field via ``object``."""

    if selection is not None and selection.launch_knobs is None:
        raise HarnessControlError("resolved launch requires adapter-produced launch knobs")


def _require_thread_boundary(harness_id: str, resume_thread_id: str | None) -> None:
    """Only the Codex adapter resumes a vendor thread, and only by an exact id."""

    if resume_thread_id is None:
        return
    if harness_id != "codex":
        raise HarnessControlError("resume_thread_id is only supported for the codex harness")
    if not resume_thread_id or resume_thread_id != resume_thread_id.strip():
        raise HarnessControlError("resume_thread_id must be non-empty with no outer whitespace")


def _require_capsule_channel(
    harness_id: str, capsule_delivery: CodexCapsuleDelivery | None
) -> None:
    """A capsule may only go to a harness whose instruction channel is verified.

    Refusing is the point: a caller that asked for a capsule and silently received a capsule-free
    process would be worse than an error at the factory.
    """

    if capsule_delivery is not None and harness_id != "codex":
        raise HarnessControlError(
            f"capsule delivery is only implemented for the codex harness; {harness_id!r} has no "
            "verified instruction channel"
        )


def create_harness_protocol_adapter(
    harness_id: str,
    *,
    env: Mapping[str, str],
    selection: LaunchSelection | None = None,
    resume_thread_id: str | None = None,
    capsule_delivery: CodexCapsuleDelivery | None = None,
) -> LaunchableHarnessProtocolAdapter:
    """Create a protocol-negotiating built-in adapter; custom ids stay explicitly unsupported.

    ``capsule_delivery`` is the admitted role capsule this launch must apply, or ``None`` for the
    unmodified legacy launch. Only the Codex app-server has a verified instruction channel, so a
    capsule handed to any other harness is refused here rather than silently dropped: a caller that
    asked for a capsule and got a capsule-free process would be the worst possible outcome.
    """

    # Runtime environment stays on LaunchSpec. In particular, AR_SPAWN_* provenance must never
    # become roleless adapter selection authority here.
    del env
    resolved_launch = selection.resolved_launch if selection is not None else None
    launch_knobs = selection.launch_knobs if selection is not None else None
    _require_consistent_launch(harness_id, selection)
    _require_launch_knobs_present(selection)
    _require_thread_boundary(harness_id, resume_thread_id)
    _require_capsule_channel(harness_id, capsule_delivery)
    if harness_id == "claude":
        return ClaudeStreamJsonAdapter(expected_launch=resolved_launch)
    if harness_id == "codex":
        # Until L4 supplies a typed per-session selection, a roleless/dashboard open follows the
        # native catalog defaults. Ambient AR_SPAWN_* values are provenance, not authority.
        model = resolved_launch.model_key if resolved_launch is not None else None
        effort = resolved_launch.effort if resolved_launch is not None else None
        return CodexAppServerAdapter(
            CodexAppServerSettings(
                model=model,
                reasoning_effort=effort,
                resume_thread_id=resume_thread_id,
                config=dict(launch_knobs.session_config) if launch_knobs is not None else {},
                capsule_delivery=capsule_delivery,
            )
        )
    if harness_id == "pi":
        return PiRpcAdapter(expected_launch=resolved_launch)
    if harness_id == "eve":
        return EveSessionAdapter(
            expected_launch=_eve_expected_selection(resolved_launch, launch_knobs),
        )
    return UnsupportedHarnessProtocolAdapter(harness_id)


def _eve_expected_selection(
    resolved_launch: ResolvedLaunch | None,
    launch_knobs: LaunchKnobs | None,
) -> EveLaunchSelection | None:
    """What the settings resolution pinned for this eve launch, or ``None`` when unselected.

    The selection arrives on the launch knobs the runner already applied, so the adapter verifies
    the runtime against the same values that reached the child instead of re-deriving them.
    """

    if resolved_launch is None:
        return None
    if launch_knobs is None:
        raise HarnessControlError("resolved eve launch requires adapter-produced launch knobs")
    probe = LaunchSpec(
        identity=ControlIdentity(
            ar_session_id="eve-factory-probe",
            tmux_name="eve-factory-probe",
            created_at="",
        ),
        harness_id="eve",
        cwd=resolved_launch.workspace,
        argv=("eve",),
        env=dict(launch_knobs.env),
    )
    return launch_spec_selection(probe)
