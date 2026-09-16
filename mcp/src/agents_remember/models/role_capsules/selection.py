"""Explicit role, seat-kind and operation selection over the canonical manifest.

Selection is a lookup, not an inference. The seat and the operation arrive on the
admitted binding; the manifest is asked which blocks that pair composes from. There
is no model call, no scoring, no fallback operation, no nearest-match role, and no
combinatorial role-by-phase rule language — three ordinary lookups and two refusals.

Two refusals carry most of the weight here:

* an unknown role or operation is refused by exact membership in the frozen
  vocabulary, so a caller-controlled string can never acquire a role; and
* an operation the selected role cannot run is refused rather than replaced by a
  neighbouring one, because silently substituting a different operation is a silent
  fallback wearing a lookup's clothes.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.manifest import (
    CapsuleCompositionManifest,
    CapsuleRoleEntry,
)
from agents_remember.models.role_capsules.sources import (
    CapsuleDeclaredInstruction,
    instruction_identity,
    shared_core_reference,
)
from agents_remember.models.role_capsules.statuses import (
    STATUS_OPERATION_NOT_APPLICABLE,
    STATUS_UNKNOWN_OPERATION,
    STATUS_UNKNOWN_ROLE,
)
from agents_remember.models.role_capsules.types import (
    SELECTION_OPERATION_BLOCK,
    SELECTION_REPOSITORY_SPECIALIZATION,
    SELECTION_ROLE_BLOCK,
    CapsuleBinding,
    CapsuleLauncherSeat,
    CapsuleOperation,
    CapsuleRole,
    CapsuleRoleSeat,
    CapsuleSeatKind,
)
from agents_remember.models.role_capsules.vocabulary import (
    CAPSULE_OPERATIONS,
    CAPSULE_ROLES,
    is_capsule_operation,
    is_capsule_role,
)


@dataclass(frozen=True, slots=True)
class CapsuleScope:
    """What the binding asks the manifest for, once selection has succeeded.

    Constructed only by :func:`select_scope`, so a caller cannot assemble a scope
    that skipped the vocabulary and applicability checks.
    """

    seat_kind: CapsuleSeatKind
    role: CapsuleRole | None
    role_entry: CapsuleRoleEntry | None
    operation: CapsuleOperation
    core_blocks: tuple[str, ...]
    allowed_operations: tuple[CapsuleOperation, ...]

    def declared(self, binding: CapsuleBinding) -> tuple[CapsuleDeclaredInstruction, ...]:
        """The locked plan: exactly the identities this scope must resolve.

        The manifest declares the core, role and operation blocks; the binding
        declares the repository specializations, because specialization is an
        explicit admission rather than an environmental default. Both halves are
        required, so a specialization the binding admitted but did not supply is a
        missing required block rather than a silently shorter capsule.
        """

        declared: list[CapsuleDeclaredInstruction] = [
            CapsuleDeclaredInstruction(
                identity=instruction_identity("core", name),
                composition_root="core",
                authorities=(shared_core_reference(name),),
            )
            for name in self.core_blocks
        ]
        if self.role_entry is not None:
            declared.append(
                CapsuleDeclaredInstruction(
                    identity=instruction_identity("role", self.role_entry.role),
                    composition_root="role",
                    authorities=(f"role:{self.role_entry.role}:{SELECTION_ROLE_BLOCK}",),
                )
            )
        declared.append(
            CapsuleDeclaredInstruction(
                identity=instruction_identity("operation", self.operation),
                composition_root="operation",
                authorities=(f"operation:{self.operation}:{SELECTION_OPERATION_BLOCK}",),
            )
        )
        declared += [
            CapsuleDeclaredInstruction(
                identity=identity,
                composition_root="specialization",
                authorities=(f"{identity}:{SELECTION_REPOSITORY_SPECIALIZATION}",),
            )
            for identity in binding.specializations
        ]
        return tuple(declared)


def select_scope(parsed: CapsuleCompositionManifest, binding: CapsuleBinding) -> CapsuleScope:
    """Select the composition scope for ``binding``, or refuse with a precise error."""

    operation = narrow_operation(binding.operation)
    seat = binding.admitted.seat
    if isinstance(seat, CapsuleLauncherSeat):
        launcher = parsed.launcher
        _require_operation_allowed(operation, launcher.operations, seat_kind="launcher", role=None)
        return CapsuleScope(
            seat_kind="launcher",
            role=None,
            role_entry=None,
            operation=operation,
            core_blocks=launcher.core,
            allowed_operations=launcher.operations,
        )
    return _role_scope(parsed, seat, operation)


def _role_scope(
    parsed: CapsuleCompositionManifest, seat: CapsuleRoleSeat, operation: CapsuleOperation
) -> CapsuleScope:
    role = narrow_role(seat.role)
    entry = parsed.roles[role]
    _require_operation_allowed(operation, entry.operations, seat_kind="role", role=role)
    return CapsuleScope(
        seat_kind="role",
        role=role,
        role_entry=entry,
        operation=operation,
        core_blocks=entry.core,
        allowed_operations=entry.operations,
    )


def _require_operation_allowed(
    operation: CapsuleOperation,
    allowed: tuple[CapsuleOperation, ...],
    *,
    seat_kind: CapsuleSeatKind,
    role: CapsuleRole | None,
) -> None:
    if operation in allowed:
        return
    subject = f"role {role!r}" if role is not None else f"the ambient {seat_kind}"
    remedy = (
        "admit the operation this seat actually runs; the compiler never selects a neighbouring "
        "operation on the caller's behalf"
        if role is not None
        else "admit the operation this routing condition actually runs; a launcher is not a role "
        "and inherits no role's operations"
    )
    raise CapsuleCompilationError(
        status=STATUS_OPERATION_NOT_APPLICABLE,
        detail=(
            f"{subject} may run {_quoted(allowed)} but this capsule was admitted for operation "
            f"{operation!r}"
        ),
        next_action=remedy,
    )


def narrow_operation(operation: str) -> CapsuleOperation:
    """Narrow ``operation`` to the frozen vocabulary, or refuse the exact value."""

    if not is_capsule_operation(operation):
        raise CapsuleCompilationError(
            status=STATUS_UNKNOWN_OPERATION,
            detail=(
                f"operation {operation!r} is not one of the {len(CAPSULE_OPERATIONS)} frozen "
                f"operations ({', '.join(CAPSULE_OPERATIONS)})"
            ),
            next_action="name a frozen operation; there is no fallback operation",
        )
    return operation  # type: ignore[return-value]


def narrow_role(role: str) -> CapsuleRole:
    """Narrow ``role`` to the frozen registry, or refuse the exact value.

    This answers by exact membership only. It does not normalize case, strip
    surrounding prose out of a longer string, or match a role name it finds inside a
    prompt — each of those would let caller-controlled text acquire a role.
    """

    if not is_capsule_role(role):
        raise CapsuleCompilationError(
            status=STATUS_UNKNOWN_ROLE,
            detail=(
                f"role {role!r} is not one of the {len(CAPSULE_ROLES)} frozen lifecycle roles "
                f"({', '.join(CAPSULE_ROLES)})"
            ),
            next_action=(
                "a dispatched seat is admitted by the AR owner that owns dispatch identity; the "
                "compiler never derives a role from caller text"
            ),
        )
    return role  # type: ignore[return-value]


def _quoted(names: tuple[str, ...]) -> str:
    return ", ".join(repr(name) for name in names) if names else "no operations"


__all__ = ["CapsuleScope", "narrow_operation", "narrow_role", "select_scope"]
