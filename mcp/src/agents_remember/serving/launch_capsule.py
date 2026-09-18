"""What a launch must carry as instructions, and the one place that decides its mode.

Every production launch point that starts a session resolves this value **before** it builds its
launch request, and the value it gets is one of exactly three modes:

``capsule``
    the seat is role-configured, an admitted capsule was compiled for it, and the carriers below
    are set on the launch. This is the only mode in which a seat runs with instructions.

``legacy``
    the launch point deliberately runs the pre-capsule chain, and it says so: a session with no
    role, or a seat whose role is not an agent role at all (``chat``, ``terminal``). The reason is
    carried in the value rather than implied by an absent field, so "no capsule" is a recorded
    decision at every launch rather than a default nobody chose.

``refused``
    the seat *is* role-configured and no capsule could be supplied. The launch must not proceed:
    a session that would run blind is exactly the defect this module exists to prevent, and a
    refusal a caller can ignore is not a refusal.

**Why the compile is a port and not a call.** The compiler, the admission operation and the task
projection live in ``application`` (rank 21); this module and the launch points in ``serving`` are
rank 17 in ``layers.toml`` and may not import upward. The launch sides that live in ``serving``
therefore take a :data:`LaunchCapsuleResolver` — the same shape the serving runtime already uses for
``register_inbox_execution_evidence`` — and the composition root (``cli/dashboard.py``, which ranks
above both) fills it. The *decision* stays here so every launch point answers "capsule, legacy or
refused" identically; only the compile crosses the port.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.capsule_delivery import CodexCapsuleDelivery
from agents_remember.serving.harness_launch import ResolvedLaunch

#: The operations a launch may select, in the launch's own vocabulary.
LAUNCH_OPERATION = "orientation"
"""The operation a launch compiles.

``orientation`` is not a new selection rule: it is the default the registered
``role_capsule_compile`` operation already declares (``operation: str = "orientation"``), and every
role the corpus admits carries it. A launch therefore compiles the same capsule a caller would get
by asking the shipped operation for that role and leaving its default operation in place.
"""

LEGACY_SESSION_ROLES: frozenset[str] = frozenset({"chat", "terminal"})
"""Seat roles that are deliberately not agents and therefore have no role capsule.

``chat`` is the seat of the identity-free launcher and the fallback for a session that declared no
role; ``terminal`` is a plain shell. Neither is a member of the frozen capsule vocabulary, and
neither has instructions to receive.
"""

CAPSULE_CARRIER_HARNESSES: frozenset[str] = frozenset({"codex", "eve"})
"""The harnesses with a verified instruction channel, and the only two that can be supplied.

``codex`` carries the capsule on the app-server's own ``developerInstructions`` thread-open field
(L5), and ``eve`` carries it as the carrier its runtime verifies per request and applies as
system-role dynamic instructions (L7). Every other harness — ``claude``, ``pi``, a settings-defined
id — has no verified channel at all, which is why
:func:`~agents_remember.serving.harness_control_factories.create_harness_protocol_adapter` refuses a
capsule for them. A role-configured session on such a harness therefore runs the legacy chain **by
declared decision**, recorded with this reason, until the cutover leaf gives it a channel.
"""


class LaunchCapsuleMode(Enum):
    """The three answers a launch may get, and the only three."""

    CAPSULE = "capsule"
    LEGACY = "legacy"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class LaunchCapsuleRequest:
    """One launch's instruction identity, as the launch point already knows it.

    ``role`` is the seat role the launch was asked for (``AR_SPAWN_ROLE``), ``task_document_ref``
    the admitted task binding when the seat has one, and ``workspace_root`` the directory the
    session will run in. Nothing here is a value the launch would otherwise not have had.
    """

    role: str | None
    workspace_root: Path
    task_document_ref: TaskDocumentRef | None = None
    harness: str | None = None
    """The harness that will run the seat, when the launch already knows it.

    The carrier is per-harness — Codex's instruction field, eve's launch environment — so the
    resolver is told which one it is supplying and compiles once, for that carrier.
    """

    @property
    def is_role_configured(self) -> bool:
        """Whether this session is an agent seat the capsule vocabulary admits."""

        return isinstance(self.role, str) and self.role.strip() in CAPSULE_ROLES


@dataclass(frozen=True, slots=True)
class LaunchCapsule:
    """One launch's resolved instruction delivery.

    ``codex_delivery`` and ``eve_env`` are the two harness carriers the master already built; a
    launch sets whichever belongs to the harness it is about to start and never both for one
    process. ``report`` is the compact per-run record of what was selected — mode, role, operation,
    digest and byte size — which is published on the launch's own result so a run's instruction mode
    is readable afterwards instead of being inferred from an empty field.
    """

    mode: LaunchCapsuleMode
    role: str | None = None
    reason: str = ""
    codex_delivery: CodexCapsuleDelivery | None = None
    eve_env: Mapping[str, str] = field(default_factory=dict)
    session_workspace: Path | None = None
    """The workspace the carrier this launch supplies **admits**, when it admits one.

    Only a carrier that binds a workspace has one — eve's does (its runtime re-verifies that the
    workspace it runs in is the admitted git worktree, so the value is part of the artifact's
    validity, not a detail beside it); the Codex instruction carrier has no workspace concept at
    all, so it carries ``None``. The value is read back out of the carrier the consumer will
    re-verify, which is what makes the launch's cwd, the resolved settings selection and
    ``AR_WORKSPACE_ROOT`` agree **by construction** instead of by two computations that happen to
    match.
    """
    report: Mapping[str, object] = field(default_factory=dict)
    refusal_status: str = ""
    refusal_detail: str = ""

    @property
    def is_capsule(self) -> bool:
        return self.mode is LaunchCapsuleMode.CAPSULE

    @property
    def is_refusal(self) -> bool:
        return self.mode is LaunchCapsuleMode.REFUSED

    def explain(self) -> str:
        """One operator-facing line naming what this launch selected, or why it refused."""

        if self.is_refusal:
            return (
                f"this launch cannot supply the capsule for role {self.role!r} "
                f"({self.refusal_status}): {self.refusal_detail}"
            )
        if self.mode is LaunchCapsuleMode.LEGACY:
            return f"this launch runs the legacy chain deliberately: {self.reason}"
        return (
            f"this launch delivers the compiled {self.report.get('operation')!r} capsule for role "
            f"{self.role!r} ({self.report.get('semanticDigest')})"
        )


#: The port the serving launch points resolve through; the application tier implements it.
LaunchCapsuleResolver = Callable[[LaunchCapsuleRequest], LaunchCapsule]


def session_workspace(capsule: LaunchCapsule, *, server_workspace: Path) -> Path:
    """The one workspace this session runs in: the admitted one, or the server's own.

    This is the single rule, stated once. A launch whose capsule admits a workspace runs **there** —
    the session's cwd becomes the value the carrier already names, so the cwd and the carrier's own
    ``AR_WORKSPACE_ROOT`` cannot disagree. A launch whose capsule admits none (a free agent, a
    declared legacy launch, or a Codex instruction capsule, none of which binds a workspace) keeps
    the server's workspace exactly as before.
    """

    return capsule.session_workspace or server_workspace


def selection_for_workspace(
    selection: ResolvedLaunch | None, *, workspace: Path
) -> ResolvedLaunch | None:
    """The settings selection, carrying the workspace this session actually runs in.

    ``ResolvedLaunch.workspace`` is the workspace a launch runs in — the runner refuses a launch
    whose selection names another one — so when the capsule moves the session's workspace, the
    selection has to follow it. Only that field moves: the harness, model and effort the settings
    chose are untouched, and a selection already naming ``workspace`` is returned unchanged.
    """

    if selection is None or selection.workspace == workspace:
        return selection
    return replace(selection, workspace=workspace)


def legacy_launch_capsule(
    role: str | None, reason: str, *, report: Mapping[str, object] | None = None
) -> LaunchCapsule:
    """The explicitly-declared legacy launch: no capsule, and the reason recorded."""

    if not reason.strip():
        raise ValueError("a legacy launch declaration must name why it runs the legacy chain")
    return LaunchCapsule(
        mode=LaunchCapsuleMode.LEGACY,
        role=role,
        reason=reason,
        report=dict(report) if report is not None else legacy_report(role, reason),
    )


def legacy_report(role: str | None, reason: str) -> dict[str, object]:
    """The per-run record of a legacy launch: the mode and the decision behind it."""

    return {"mode": LaunchCapsuleMode.LEGACY.value, "role": role, "reason": reason}


def refused_launch_capsule(role: str | None, status: str, detail: str) -> LaunchCapsule:
    """The fail-closed answer: this seat is role-configured and no capsule can be supplied."""

    return LaunchCapsule(
        mode=LaunchCapsuleMode.REFUSED,
        role=role,
        refusal_status=status,
        refusal_detail=detail,
        report={
            "mode": LaunchCapsuleMode.REFUSED.value,
            "role": role,
            "status": status,
            "detail": detail,
        },
    )


def legacy_seat_reason(role: str | None) -> str | None:
    """Why this role runs the legacy chain by decision, or ``None`` when it is role-configured.

    A role that is neither a capsule role nor a declared legacy seat is **not** legacy: it is an
    unknown role, and the capsule cannot be compiled for it. The caller sees the refusal through
    :func:`resolve_launch_capsule` rather than a silent legacy launch.
    """

    if role is None or not role.strip():
        return "the session declared no role, so it is the identity-free launcher and has no seat"
    if role in LEGACY_SESSION_ROLES:
        return (
            f"role {role!r} is not an agent seat: it is declared in LEGACY_SESSION_ROLES as a "
            "deliberately taskless, instruction-free seat"
        )
    return None


def capsule_channel_reason(harness: str | None) -> str | None:
    """Why this harness cannot be supplied a capsule, or ``None`` when it can.

    A launch that starts no harness at all has no instruction channel either: a role-configured
    session opened as a plain terminal is a real shape (the dashboard's shell button with a role),
    and it says so rather than pretending a capsule was delivered.
    """

    harness_id = (harness or "").strip()
    if not harness_id:
        return (
            "this launch starts no harness, so there is no instruction channel a capsule could be "
            "supplied through; the session runs the legacy chain by declaration"
        )
    if harness_id not in CAPSULE_CARRIER_HARNESSES:
        return (
            f"harness {harness_id!r} has no verified capsule channel; the two this master built are "
            f"{', '.join(sorted(CAPSULE_CARRIER_HARNESSES))} (Codex's app-server instruction field "
            "and eve's own carrier). The session runs the legacy chain by declaration until a "
            "channel exists"
        )
    return None


def resolve_launch_capsule(
    resolver: LaunchCapsuleResolver | None, request: LaunchCapsuleRequest
) -> LaunchCapsule:
    """Resolve one launch's instruction delivery, or refuse by name.

    The four outcomes are decided here, once, for every launch point: a declared legacy seat, a
    role that is neither a capsule role nor a declared legacy seat (a refusal, never a quiet legacy
    launch), a harness with no verified channel (legacy by declaration, with the reason), and a
    role-configured seat on a channel-bearing harness — which is compiled through the port, or
    refused when this process has no resolver wired into it.
    """

    reason = legacy_seat_reason(request.role)
    if reason is not None:
        return legacy_launch_capsule(request.role, reason)
    if not request.is_role_configured:
        return refused_launch_capsule(
            request.role,
            "role-not-capsule-addressable",
            (
                f"role {request.role!r} is neither one of the frozen capsule roles "
                f"({', '.join(CAPSULE_ROLES)}) nor a declared legacy seat "
                f"({', '.join(sorted(LEGACY_SESSION_ROLES))}); no capsule can be compiled for it "
                "and this launch will not start a role-configured session without one"
            ),
        )
    channel_reason = capsule_channel_reason(request.harness)
    if channel_reason is not None:
        return legacy_launch_capsule(request.role, channel_reason, report=_channel_report(request))
    if resolver is None:
        return refused_launch_capsule(
            request.role,
            "capsule-resolver-unavailable",
            (
                f"this process was composed without a capsule resolver, so the capsule for role "
                f"{request.role!r} cannot be compiled; a role-configured session is never started "
                "without its instructions"
            ),
        )
    return resolver(request)


def _channel_report(request: LaunchCapsuleRequest) -> dict[str, object]:
    """A legacy-by-channel record that names the harness, not only the mode."""

    reason = capsule_channel_reason(request.harness)
    assert reason is not None  # the caller branched on it
    return {"mode": LaunchCapsuleMode.LEGACY.value, "role": request.role, "reason": reason}


def eve_binding_env() -> tuple[str, ...]:
    """The environment names an eve launch must carry to be bound; the reader declares them."""

    from agents_remember.models.eve_capsule_carrier import (  # noqa: PLC0415 - reader's own names
        BINDING_REF_ENV,
        CAPSULE_DIGEST_ENV,
        CAPSULE_PATH_ENV,
    )

    return (BINDING_REF_ENV, CAPSULE_PATH_ENV, CAPSULE_DIGEST_ENV)


__all__ = [
    "CAPSULE_CARRIER_HARNESSES",
    "LAUNCH_OPERATION",
    "LEGACY_SESSION_ROLES",
    "LaunchCapsule",
    "LaunchCapsuleMode",
    "LaunchCapsuleRequest",
    "LaunchCapsuleResolver",
    "capsule_channel_reason",
    "eve_binding_env",
    "legacy_launch_capsule",
    "legacy_report",
    "legacy_seat_reason",
    "refused_launch_capsule",
    "resolve_launch_capsule",
    "selection_for_workspace",
    "session_workspace",
]
