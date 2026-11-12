"""Stable refusal codes for role-capsule compilation.

One code per defect class, in its own module, so a caller can branch on a code
without importing the module that raises it and so two refusal sites cannot drift
into two spellings of the same status. The wording that accompanies a code is for
an operator; the code is the contract.
"""

from __future__ import annotations

STATUS_UNKNOWN_ROLE = "unknown-role"
STATUS_UNKNOWN_OPERATION = "unknown-operation"
STATUS_OPERATION_NOT_APPLICABLE = "operation-not-applicable"
STATUS_MISSING_REQUIRED_INSTRUCTION = "missing-required-instruction"
STATUS_SOURCE_NOT_DECLARED = "source-not-declared"
STATUS_SOURCE_ROOT_MISMATCH = "source-root-mismatch"
STATUS_DUPLICATE_IDENTITY = "duplicate-identity"
STATUS_EQUAL_AUTHORITY_CONTRADICTION = "equal-authority-contradiction"
STATUS_UNKNOWN_SUPERSESSION = "unknown-supersession"
STATUS_SUPERSESSION_CONFLICT = "supersession-conflict"
STATUS_SPECIALIZATION_NOT_ADMITTED = "specialization-not-admitted"
STATUS_TOOL_REQUEST_NOT_PERMITTED = "tool-request-not-permitted"
STATUS_TASK_CONTEXT_DIGEST_MISMATCH = "task-context-digest-mismatch"

#: Every code this compiler can emit, so a consumer can enumerate the surface and
#: so a new code added without being registered here is caught by its own test.
CAPSULE_STATUSES: tuple[str, ...] = (
    STATUS_UNKNOWN_ROLE,
    STATUS_UNKNOWN_OPERATION,
    STATUS_OPERATION_NOT_APPLICABLE,
    STATUS_MISSING_REQUIRED_INSTRUCTION,
    STATUS_SOURCE_NOT_DECLARED,
    STATUS_SOURCE_ROOT_MISMATCH,
    STATUS_DUPLICATE_IDENTITY,
    STATUS_EQUAL_AUTHORITY_CONTRADICTION,
    STATUS_UNKNOWN_SUPERSESSION,
    STATUS_SUPERSESSION_CONFLICT,
    STATUS_SPECIALIZATION_NOT_ADMITTED,
    STATUS_TOOL_REQUEST_NOT_PERMITTED,
    STATUS_TASK_CONTEXT_DIGEST_MISMATCH,
)

__all__ = [
    "CAPSULE_STATUSES",
    "STATUS_DUPLICATE_IDENTITY",
    "STATUS_EQUAL_AUTHORITY_CONTRADICTION",
    "STATUS_MISSING_REQUIRED_INSTRUCTION",
    "STATUS_OPERATION_NOT_APPLICABLE",
    "STATUS_SOURCE_NOT_DECLARED",
    "STATUS_SOURCE_ROOT_MISMATCH",
    "STATUS_SPECIALIZATION_NOT_ADMITTED",
    "STATUS_SUPERSESSION_CONFLICT",
    "STATUS_TASK_CONTEXT_DIGEST_MISMATCH",
    "STATUS_TOOL_REQUEST_NOT_PERMITTED",
    "STATUS_UNKNOWN_OPERATION",
    "STATUS_UNKNOWN_ROLE",
    "STATUS_UNKNOWN_SUPERSESSION",
]
