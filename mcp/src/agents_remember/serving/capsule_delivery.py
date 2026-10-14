"""The value one admitted role capsule is carried in to a role launch.

**Why this module exists, and why it sits at this rank.** The capsule reaches a launch as an
*admitted value*: the compiler and the admission surface rank above ``serving`` in
``layers.toml``, so this module must not import them. It therefore defines the value a launch
consumes — :class:`CapsuleDelivery` — as a frozen pure type over ``models``-rank imports, and
the owner that compiles the capsule fills it. Nothing here re-derives, re-renders, re-orders or
re-selects anything: :func:`capsule_delivery_from` copies the compiler's own strings, and the
instruction bytes it carries are the compiler's ``render_instructions()`` output unchanged.

**What the value holds.** The admitted binding (repository, task reference, role, operation), the
trusted instruction stream and the compiler's semantic digest. Task text, tool output and
model-authored text have no field here. The role launch composes its first message from the
instruction stream and reports the binding and the digest.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.errors import CapsuleDeliveryError


@dataclass(frozen=True, slots=True)
class CapsuleBindingIdentity:
    """The admitted binding a delivery belongs to.

    Server-owned: role, operation, task document and repository come from the admitted resolution,
    never from a caller-controlled string.
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
                raise CapsuleDeliveryError(
                    f"capsule binding {name} must be non-empty with no outer whitespace"
                )

    def as_report(self) -> dict[str, str]:
        return {
            "repositoryId": self.repository_id,
            "taskPath": self.task_path,
            "role": self.role,
            "operation": self.operation,
        }


@dataclass(frozen=True, slots=True)
class CapsuleDelivery:
    """One admitted capsule, ready for the launch that carries it.

    ``trusted_instructions`` is the compiler's ``render_instructions()`` output **verbatim**. The
    digest is the compiler's own semantic digest.
    """

    binding: CapsuleBindingIdentity
    trusted_instructions: str
    semantic_digest: str

    def __post_init__(self) -> None:
        if not self.trusted_instructions.strip():
            raise CapsuleDeliveryError(
                "capsule delivery requires a non-empty trusted instruction stream"
            )
        if not self.semantic_digest or not self.semantic_digest.startswith("sha256:"):
            raise CapsuleDeliveryError(
                "capsule delivery requires the compiler's 'sha256:' semantic digest"
            )


LAUNCHER_ROLE_SENTINEL = "launcher"
"""The reported ``role`` for an ambient-launcher binding, which has no role.

The frozen seat is a union: a role seat carries ``role``, and the launcher seat exposes
``role -> None`` with a ``routing_condition`` instead. A launcher still runs an *operation* and still
gets a capsule (L2 composes ``core/launcher.md`` plus ``core/authority.md`` for it), so the value needs
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
        raise CapsuleDeliveryError("CapsuleRoleSeat carries no usable role")
    if kind == "launcher":
        return LAUNCHER_ROLE_SENTINEL
    raise CapsuleDeliveryError(
        "compiled capsule carries an unrecognised seat kind "
        f"{kind!r}; expected 'role' or 'launcher'"
    )


def capsule_delivery_from(compilation: object) -> CapsuleDelivery:
    """Build the launch value from L2's frozen compilation result.

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

    This module keeps its rank by reading those attributes structurally rather than importing the
    compiler's package; every shape above is asserted against the landed types by this leaf's tests,
    so a DTO change fails there rather than being absorbed by another guess here.
    """

    capsule = getattr(compilation, "capsule", None)
    if capsule is None:
        raise CapsuleDeliveryError(
            "capsule_delivery_from requires L2's CapsuleCompilationResult (no .capsule on the input)"
        )
    binding = getattr(capsule, "binding", None)
    admitted = getattr(binding, "admitted", None)
    if binding is None or admitted is None:
        raise CapsuleDeliveryError(
            "compiled capsule carries no admitted binding facts (expected binding.admitted)"
        )
    render = getattr(compilation, "render_instructions", None)
    if not callable(render):
        raise CapsuleDeliveryError(
            "compiled capsule cannot render its instruction stream; render_instructions() lives on "
            "CapsuleCompilationResult"
        )

    task_reference = getattr(admitted, "task_reference", None)
    repository_id = getattr(admitted, "repository_id", None)
    if not isinstance(task_reference, str) or not task_reference.strip():
        raise CapsuleDeliveryError("admitted facts carry no task_reference")
    if not isinstance(repository_id, str) or not repository_id.strip():
        raise CapsuleDeliveryError("admitted facts carry no repository_id")

    return CapsuleDelivery(
        binding=CapsuleBindingIdentity(
            repository_id=repository_id,
            task_path=task_reference,
            role=_seat_role(getattr(admitted, "seat", None)),
            operation=str(getattr(binding, "operation", "")),
        ),
        trusted_instructions=str(render()),
        semantic_digest=str(getattr(capsule, "semantic_digest", "")),
    )
