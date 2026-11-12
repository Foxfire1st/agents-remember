"""The projection's stable refusal vocabulary, as data.

One code per defect class, declared once. A raise site imports its code from here
and a case asserts against the same constant, so a second spelling of a code, a
renamed code, or a new code added without being registered is a failure rather
than an invisible drift. The wording that accompanies a code is for an operator;
the code is the contract.

The sibling owner for the capsule compiler
(:mod:`agents_remember.models.role_capsules.statuses`) solves the same problem the
same way, and this module is deliberately shaped like it rather than inventing a
second convention.

:data:`UNREACHABLE_STATUSES` records the codes that cannot be produced by any
call today, with the reason. Deleting the branch that could never run is the
correct outcome for a dead guard; recording it here is how "no case exercises it"
becomes a stated fact instead of an omission.
"""

from __future__ import annotations

from collections.abc import Mapping

STATUS_BINDING_UNRESOLVED = "projection-binding-unresolved"
STATUS_BINDING_MISMATCH = "projection-binding-mismatch"
STATUS_BRANCH_MISMATCH = "projection-branch-mismatch"
STATUS_CONTRACT_UNAVAILABLE = "projection-contract-unavailable"
STATUS_MEMORY_BINDING_UNAVAILABLE = "projection-memory-binding-unavailable"
STATUS_OPERATION_UNSUPPORTED = "projection-operation-unsupported"
STATUS_REQUIREMENT_DECLARATION_UNRESOLVED = "projection-requirement-declaration-unresolved"
STATUS_REQUIREMENT_PACKET_INVALID = "projection-requirement-packet-invalid"
STATUS_REQUIREMENT_PACKET_MISSING = "projection-requirement-packet-missing"
STATUS_REQUIREMENT_PACKET_UNRESOLVED = "projection-requirement-packet-unresolved"
STATUS_REPOSITORY_MISMATCH = "projection-repository-mismatch"
STATUS_ROLE_ALTITUDE_MISMATCH = "projection-role-altitude-mismatch"
STATUS_TASK_BINDING_MISMATCH = "projection-task-binding-mismatch"
STATUS_TASK_REFERENCE_INVALID = "projection-task-reference-invalid"
STATUS_TASK_REVISION_MISMATCH = "projection-task-revision-mismatch"
STATUS_TASK_UNKNOWN = "projection-task-unknown"

#: Every code this package can emit, so a consumer can enumerate the surface and
#: so a code added at a raise site without being registered here is caught by its
#: own test.
PROJECTION_STATUSES: tuple[str, ...] = (
    STATUS_BINDING_UNRESOLVED,
    STATUS_BINDING_MISMATCH,
    STATUS_BRANCH_MISMATCH,
    STATUS_CONTRACT_UNAVAILABLE,
    STATUS_MEMORY_BINDING_UNAVAILABLE,
    STATUS_OPERATION_UNSUPPORTED,
    STATUS_REQUIREMENT_DECLARATION_UNRESOLVED,
    STATUS_REQUIREMENT_PACKET_INVALID,
    STATUS_REQUIREMENT_PACKET_MISSING,
    STATUS_REQUIREMENT_PACKET_UNRESOLVED,
    STATUS_REPOSITORY_MISMATCH,
    STATUS_ROLE_ALTITUDE_MISMATCH,
    STATUS_TASK_BINDING_MISMATCH,
    STATUS_TASK_REFERENCE_INVALID,
    STATUS_TASK_REVISION_MISMATCH,
    STATUS_TASK_UNKNOWN,
)

#: Codes that no call can produce today, each with the reason it is retained.
#: An entry here is the explicit alternative to "no case exercises it".
UNREACHABLE_STATUSES: Mapping[str, str] = {}


__all__ = [
    "PROJECTION_STATUSES",
    "STATUS_BINDING_MISMATCH",
    "STATUS_BINDING_UNRESOLVED",
    "STATUS_BRANCH_MISMATCH",
    "STATUS_CONTRACT_UNAVAILABLE",
    "STATUS_MEMORY_BINDING_UNAVAILABLE",
    "STATUS_OPERATION_UNSUPPORTED",
    "STATUS_REPOSITORY_MISMATCH",
    "STATUS_REQUIREMENT_DECLARATION_UNRESOLVED",
    "STATUS_REQUIREMENT_PACKET_INVALID",
    "STATUS_REQUIREMENT_PACKET_MISSING",
    "STATUS_REQUIREMENT_PACKET_UNRESOLVED",
    "STATUS_ROLE_ALTITUDE_MISMATCH",
    "STATUS_TASK_BINDING_MISMATCH",
    "STATUS_TASK_REFERENCE_INVALID",
    "STATUS_TASK_REVISION_MISMATCH",
    "STATUS_TASK_UNKNOWN",
    "UNREACHABLE_STATUSES",
]
