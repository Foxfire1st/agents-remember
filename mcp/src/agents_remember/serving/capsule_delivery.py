"""How one admitted role capsule reaches the Codex app-server instruction channel.

**Why this module exists, and why it sits at this rank.** The capsule reaches this seam as an
*admitted value*: the compiler (L2) and the admission surface (L4) rank above ``serving`` in
``layers.toml``, so this module must not import them. It therefore defines the value the seam
consumes — :class:`CodexCapsuleDelivery` — as a frozen pure type over ``models``-rank imports, and the
owner that compiles the capsule fills it. Nothing here re-derives, re-renders, re-orders or
re-selects anything: :func:`capsule_delivery_from` copies the compiler's own strings, and the
instruction bytes it carries are the compiler's ``render_instructions()`` output unchanged.

**The authority boundary this module enforces.** Three channels, three owners, no mixing:

===========================  ==========================================  ============================
Channel                      Content                                     Who may write it
===========================  ==========================================  ============================
``developerInstructions``    the capsule's trusted instruction stream    the compiler, via :func:`capsule_delivery_from`
ordinary turn input          the projection's task context               the task projection (L3)
skill references             pointers to separately served skills        the compiler, carried only
===========================  ==========================================  ============================

Task text, MCP text, tool output and model-authored text never enter the instruction channel: they
have no parameter here, and :func:`thread_instruction_params` raises rather than accepting them. A
skill is a pointer, never composed content — ``CapsuleSkillReference`` deliberately cannot reach the
instruction stream.

**Lifetime: one capsule per admitted binding, applied at a supported native boundary.** The installed
app-server (measured 0.151.0) exposes instruction fields on ``thread/start``, ``thread/resume`` and
``thread/fork`` and **none** on ``turn/start``, so an ordinary user message cannot and does not
re-apply the corpus. :func:`plan_refresh` turns that constraint into an explicit decision: the same
binding with the same content digest is delivered in place; a changed revision on a live thread needs
a supported boundary — a bounded fresh thread (:attr:`CapsuleRefresh.Mode.FRESH_THREAD`) or a fork of
the live thread (:attr:`CapsuleRefresh.Mode.FORK_THREAD`) — and when the caller can do neither, the
mode is ``UNSUPPORTED`` and the refusal is reported. Stacking two instruction revisions on one thread
is not a fourth option: :func:`thread_instruction_params` refuses it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from agents_remember.errors import CodexAppServerError

INSTRUCTION_PARAM = "developerInstructions"
"""The supported thread-open field carrying trusted developer/role instructions.

Chosen over ``baseInstructions`` deliberately: ``baseInstructions`` replaces the vendor's own base
prompt, which is the vendor's authority and not ours to occupy. Measured on the installed schema in
``mcp/tests/fixtures/codex_app_server_instruction_channels.json``.
"""

REFRESH_REPORT_KEY = "capsuleRefresh"
"""Where the applied revision and refresh mode are reported on the thread-open raw evidence."""


class CapsuleRefresh(Enum):
    """What the seam decided to do about the capsule relative to a thread that already exists."""

    INITIAL = "initial"
    """First delivery for this thread. Instructions are applied at the thread-open boundary."""

    IN_PLACE = "in-place"
    """Same binding, same content digest: the open call re-states the same bytes, so nothing stacks."""

    FRESH_THREAD = "fresh-thread"
    """A changed revision on a live thread, resolved by opening a bounded fresh thread."""

    FORK_THREAD = "fork-thread"
    """A changed revision on a live thread, resolved by a native ``thread/fork`` of that thread."""

    UNSUPPORTED = "unsupported"
    """A changed revision on a live thread with no available boundary. Reported, never faked."""


@dataclass(frozen=True, slots=True)
class CapsuleSkillPointer:
    """One skill the capsule references.

    A pointer is not a capability and not content: it is carried so the seam can report *what* the
    capsule pointed at, while the bytes stay on the skills transport. It has no route into the
    instruction stream by construction.
    """

    identity: str
    origin: str
    uri: str
    revision: str


@dataclass(frozen=True, slots=True)
class CapsuleBindingIdentity:
    """The admitted binding a delivery belongs to.

    Server-owned: role, operation, task document and repository come from the admitted resolution,
    never from a caller-controlled string. Two deliveries compare by binding so a mismatched capsule
    can be refused before it reaches the vendor process.
    """

    repository_id: str
    task_path: str
    role: str
    operation: str

    def __post_init__(self) -> None:
        for name, value in (
            ("repository_id", self.repository_id),
            ("task_path", self.task_path),
            ("role", self.role),
            ("operation", self.operation),
        ):
            if not value or value != value.strip():
                raise CodexAppServerError(
                    f"Codex capsule binding {name} must be non-empty with no outer whitespace"
                )

    @classmethod
    def from_report(cls, report: object) -> CapsuleBindingIdentity | None:
        """The recorded binding from a published report, or ``None`` when it is not usable.

        Used by the refresh decision: an unreadable record is treated as an unknown thread, which
        opens a bounded fresh thread rather than silently reusing another binding's thread.
        """

        if not isinstance(report, dict):
            return None
        fields = {
            name: report.get(name) for name in ("repositoryId", "taskPath", "role", "operation")
        }
        if not all(isinstance(value, str) and value.strip() for value in fields.values()):
            return None
        return cls(
            repository_id=str(fields["repositoryId"]),
            task_path=str(fields["taskPath"]),
            role=str(fields["role"]),
            operation=str(fields["operation"]),
        )

    @classmethod
    def from_json(cls, raw: object) -> CapsuleBindingIdentity | None:
        """Read a binding from its wire form, or ``None`` when the value is not a usable one."""

        if not isinstance(raw, dict):
            return None
        fields = {name: raw.get(name) for name in ("repositoryId", "taskPath", "role", "operation")}
        if not all(isinstance(value, str) and value.strip() for value in fields.values()):
            return None
        return cls(
            repository_id=str(fields["repositoryId"]),
            task_path=str(fields["taskPath"]),
            role=str(fields["role"]),
            operation=str(fields["operation"]),
        )

    def to_json(self) -> dict[str, str]:
        """The wire form, identical to the report shape the seam already publishes."""

        return self.as_report()

    def as_report(self) -> dict[str, str]:
        return {
            "repositoryId": self.repository_id,
            "taskPath": self.task_path,
            "role": self.role,
            "operation": self.operation,
        }


@dataclass(frozen=True, slots=True)
class CodexCapsuleDelivery:
    """One admitted capsule, ready to apply at a thread-open boundary.

    ``trusted_instructions`` is the compiler's ``render_instructions()`` output **verbatim**. The
    digest is the compiler's own semantic digest and is the identity the seam compares when deciding
    whether an open call restates the same capsule or introduces a new revision.
    """

    binding: CapsuleBindingIdentity
    trusted_instructions: str
    semantic_digest: str
    skill_pointers: tuple[CapsuleSkillPointer, ...] = ()

    def __post_init__(self) -> None:
        if not self.trusted_instructions.strip():
            raise CodexAppServerError(
                "Codex capsule delivery requires a non-empty trusted instruction stream"
            )
        if not self.semantic_digest or not self.semantic_digest.startswith("sha256:"):
            raise CodexAppServerError(
                "Codex capsule delivery requires the compiler's 'sha256:' semantic digest"
            )

    @property
    def instruction_channel(self) -> str:
        """The parameter name this delivery occupies; named so the boundary is testable."""

        return INSTRUCTION_PARAM

    @classmethod
    def from_json(cls, raw: object) -> CodexCapsuleDelivery | None:
        """Read a delivery from its wire form, or ``None`` when the value is unusable.

        Used by the runner's encoded launch configuration. A malformed value is ``None`` rather than
        an error so the caller decides: the runner refuses it at its own boundary, which keeps this
        value type free of transport policy.
        """

        if not isinstance(raw, dict):
            return None
        binding = CapsuleBindingIdentity.from_json(raw.get("binding"))
        instructions = raw.get("trustedInstructions")
        digest = raw.get("semanticDigest")
        if binding is None or not isinstance(instructions, str) or not instructions.strip():
            return None
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            return None
        pointers = raw.get("skillPointers") or []
        if not isinstance(pointers, list):
            return None
        try:
            return cls(
                binding=binding,
                trusted_instructions=instructions,
                semantic_digest=digest,
                skill_pointers=tuple(
                    CapsuleSkillPointer(
                        identity=str(pointer["identity"]),
                        origin=str(pointer["origin"]),
                        uri=str(pointer["uri"]),
                        revision=str(pointer["revision"]),
                    )
                    for pointer in pointers
                    if isinstance(pointer, dict)
                ),
            )
        except (KeyError, TypeError, CodexAppServerError):
            return None

    def to_json(self) -> dict[str, object]:
        """The wire form: exactly the fields the seam consumes, nothing derived."""

        return {
            "binding": self.binding.to_json(),
            "trustedInstructions": self.trusted_instructions,
            "semanticDigest": self.semantic_digest,
            "skillPointers": [
                {
                    "identity": pointer.identity,
                    "origin": pointer.origin,
                    "uri": pointer.uri,
                    "revision": pointer.revision,
                }
                for pointer in self.skill_pointers
            ],
        }

    def as_report(self) -> dict[str, object]:
        """The compact, provenance-only record the seam publishes on the thread-open evidence."""

        return {
            "binding": self.binding.as_report(),
            "semanticDigest": self.semantic_digest,
            "instructionBytes": len(self.trusted_instructions.encode("utf-8")),
            "instructionChannel": INSTRUCTION_PARAM,
            "skillPointers": [
                {
                    "identity": pointer.identity,
                    "origin": pointer.origin,
                    "uri": pointer.uri,
                    "revision": pointer.revision,
                }
                for pointer in self.skill_pointers
            ],
        }


LAUNCHER_ROLE_SENTINEL = "launcher"
"""The reported ``role`` for an ambient-launcher binding, which has no role.

The frozen seat is a union: a role seat carries ``role``, and the launcher seat exposes
``role -> None`` with a ``routing_condition`` instead. A launcher still runs an *operation* and still
gets a capsule (L2 composes ``core/launcher.md`` plus ``core/authority.md`` for it), so the seam needs
a non-empty reported role; the sentinel matches the identity L2 itself derives, where the launcher's
block identity is ``launcher:<routing_condition>``.
"""


def _seat_role(seat: object) -> str:
    """The reported role for either frozen seat kind, refusing an unrecognised one."""

    kind = getattr(seat, "kind", None)
    if kind == "role":
        role = getattr(seat, "role", None)
        if isinstance(role, str) and role.strip():
            return role
        raise CodexAppServerError("CapsuleRoleSeat carries no usable role")
    if kind == "launcher":
        return LAUNCHER_ROLE_SENTINEL
    raise CodexAppServerError(
        "compiled capsule carries an unrecognised seat kind "
        f"{kind!r}; expected 'role' or 'launcher'"
    )


def capsule_delivery_from(compilation: object) -> CodexCapsuleDelivery:
    """Build the seam value from L2's frozen compilation result.

    The mapping is explicit and total for the real object:

    ``compilation``            -> :class:`CapsuleCompilationResult`
    ``capsule.binding``        -> :class:`CapsuleBinding`
    ``binding.operation``      -> ``binding.operation``
    ``binding.admitted``       -> ``CapsuleAdmittedFacts``
    ``admitted.repository_id`` -> ``binding.repository_id``
    ``admitted.task_reference``-> ``binding.task_path``
    ``admitted.seat``          -> ``binding.role`` (role seat's ``role``; launcher sentinel)
    ``render_instructions()``  -> ``trusted_instructions`` (verbatim)
    ``capsule.semantic_digest``-> ``semantic_digest``
    ``capsule.skill_references``, ``capsule.task_context`` carried through unchanged

    This module keeps its rank by reading those attributes structurally rather than importing the
    compiler's package; every shape above is asserted against the landed types by this leaf's tests,
    so a DTO change fails there rather than being absorbed by another guess here.
    """

    capsule = getattr(compilation, "capsule", None)
    if capsule is None:
        raise CodexAppServerError(
            "capsule_delivery_from requires L2's CapsuleCompilationResult (no .capsule on the input)"
        )
    binding = getattr(capsule, "binding", None)
    admitted = getattr(binding, "admitted", None)
    if binding is None or admitted is None:
        raise CodexAppServerError(
            "compiled capsule carries no admitted binding facts (expected binding.admitted)"
        )
    render = getattr(compilation, "render_instructions", None)
    if not callable(render):
        raise CodexAppServerError(
            "compiled capsule cannot render its instruction stream; render_instructions() lives on "
            "CapsuleCompilationResult"
        )

    task_reference = getattr(admitted, "task_reference", None)
    repository_id = getattr(admitted, "repository_id", None)
    if not isinstance(task_reference, str) or not task_reference.strip():
        raise CodexAppServerError("admitted facts carry no task_reference")
    if not isinstance(repository_id, str) or not repository_id.strip():
        raise CodexAppServerError("admitted facts carry no repository_id")

    return CodexCapsuleDelivery(
        binding=CapsuleBindingIdentity(
            repository_id=repository_id,
            task_path=task_reference,
            role=_seat_role(getattr(admitted, "seat", None)),
            operation=str(getattr(binding, "operation", "")),
        ),
        trusted_instructions=str(render()),
        semantic_digest=str(getattr(capsule, "semantic_digest", "")),
        skill_pointers=tuple(
            CapsuleSkillPointer(
                identity=str(getattr(reference, "identity", "")),
                origin=str(getattr(reference, "origin", "")),
                uri=str(getattr(reference, "uri", "")),
                revision=str(getattr(reference, "revision", "")),
            )
            for reference in getattr(capsule, "skill_references", ())
        ),
    )


@dataclass(frozen=True, slots=True)
class ThreadInstructionState:
    """What a live thread already carries, as far as the seam knows it."""

    binding: CapsuleBindingIdentity | None = None
    semantic_digest: str | None = None

    @property
    def is_open(self) -> bool:
        return self.binding is not None


@dataclass(frozen=True, slots=True)
class RefreshPlan:
    """The decision :func:`plan_refresh` reached, with the reason a refusal would be reported."""

    mode: CapsuleRefresh
    reason: str
    applies_instructions: bool = field(default=True)

    @property
    def is_refusal(self) -> bool:
        return self.mode is CapsuleRefresh.UNSUPPORTED

    def as_report(self) -> dict[str, object]:
        return {"mode": self.mode.value, "reason": self.reason}


def plan_refresh(
    delivery: CodexCapsuleDelivery,
    state: ThreadInstructionState,
    *,
    fork_available: bool = False,
) -> RefreshPlan:
    """Decide how this delivery meets the thread that already exists.

    The installed protocol has no ``turn/start`` instruction field, so a changed revision cannot be
    replaced in place. This function never invents a third answer: it returns a supported boundary or
    an explicit ``UNSUPPORTED`` refusal for the caller to report.
    """

    if not state.is_open:
        return RefreshPlan(CapsuleRefresh.INITIAL, "no thread is open for this delivery")

    if state.binding != delivery.binding:
        return RefreshPlan(
            CapsuleRefresh.UNSUPPORTED,
            "the open thread carries a different admitted binding "
            f"({state.binding.as_report() if state.binding else 'none'}); "
            "a capsule is never applied over another binding's thread",
            applies_instructions=False,
        )

    if state.semantic_digest == delivery.semantic_digest:
        return RefreshPlan(
            CapsuleRefresh.IN_PLACE,
            "same binding and same semantic digest: re-stating the identical bytes stacks nothing",
        )

    if fork_available:
        return RefreshPlan(
            CapsuleRefresh.FORK_THREAD,
            "the installed protocol supports thread/fork, so the changed revision rides a fork of "
            "the live thread instead of being stacked on it",
        )
    return RefreshPlan(
        CapsuleRefresh.FRESH_THREAD,
        "the installed protocol has no turn-level instruction field, so a changed revision opens a "
        "bounded fresh thread at a safe boundary",
    )


def thread_instruction_params(
    delivery: CodexCapsuleDelivery,
    plan: RefreshPlan,
) -> dict[str, object]:
    """The thread-open instruction parameters for one planned delivery.

    Returns only :data:`INSTRUCTION_PARAM`. There is deliberately no parameter through which task
    text, skill content, MCP text or model-authored text could arrive, and a refusal plan yields no
    parameters at all — an unsupported refresh is reported by the caller, never smuggled into the
    request as a second instruction revision.
    """

    if plan.is_refusal:
        raise CodexAppServerError(f"refused capsule refresh is not deliverable: {plan.reason}")
    return {INSTRUCTION_PARAM: delivery.trusted_instructions}


LEGACY_PROJECT_DOC_KEY = "project_doc_max_bytes"
"""The config key that stops the host loading its own project instruction document.

Measured on the installed app-server: a thread opened with ``project_doc_max_bytes: 0`` reports an
empty ``instructionSources``, while the same workspace without the key reports its ``AGENTS.md``.
This is the *existing* configuration owner for that behaviour — the per-thread ``config`` object the
adapter already sends — so the switch is scoped to one launch and is not a global configuration
change (packet exclusions forbid one).
"""


@dataclass(frozen=True, slots=True)
class LegacyInstructionSwitch:
    """Whether the experimental launch suppresses the legacy AR startup chain, and why.

    The brief's order is **detect first, then switch**: :attr:`observed_sources` is what the host
    actually loaded on the previous open, and the switch is only requested when a capsule is being
    delivered. A launch with no capsule keeps the legacy behaviour untouched.
    """

    suppress: bool
    reason: str
    observed_sources: tuple[str, ...] = ()

    @property
    def request_config(self) -> dict[str, object]:
        """The per-launch config addition, or nothing when the legacy path is preserved."""

        if not self.suppress:
            return {}
        return {LEGACY_PROJECT_DOC_KEY: 0}

    def as_report(self) -> dict[str, object]:
        return {
            "suppressed": self.suppress,
            "reason": self.reason,
            "observedInstructionSources": list(self.observed_sources),
        }


def legacy_instruction_switch(
    *,
    capsule_delivered: bool,
    observed_sources: tuple[str, ...],
) -> LegacyInstructionSwitch:
    """Decide the legacy-chain switch for one launch from what the host actually loaded.

    With a capsule: the capsule IS the startup instruction chain, so the host's own document is
    suppressed to prevent loading both. Without a capsule: the legacy chain stands, unchanged.
    """

    if not capsule_delivered:
        return LegacyInstructionSwitch(
            suppress=False,
            reason="no capsule is delivered on this launch; the legacy AR startup chain is preserved",
            observed_sources=observed_sources,
        )
    if not observed_sources:
        return LegacyInstructionSwitch(
            suppress=True,
            reason="capsule delivered; the host reported no project instruction document to load",
            observed_sources=observed_sources,
        )
    joined = ", ".join(observed_sources)
    return LegacyInstructionSwitch(
        suppress=True,
        reason=(
            "capsule delivered; suppressing the host's own project instruction document so the "
            f"capsule is the only startup chain (observed on the previous open: {joined})"
        ),
        observed_sources=observed_sources,
    )
