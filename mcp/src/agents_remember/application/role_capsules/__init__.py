"""Admitted-context resolution and composition boundary for role capsules.

This package owns the I/O the pure compiler refuses to do: resolving a confined
source root, reading the exact source set an admitted binding names, and returning a
refusal as a value with its explanation. It holds no selection rules of its own —
selection lives in :mod:`agents_remember.models.role_capsules`.
"""

from agents_remember.application.role_capsules.compilation import (
    CapsuleCompilationOutcome,
    compile_admitted_capsule,
)
from agents_remember.application.role_capsules.sources import (
    CapsuleAdmissionRequest,
    admit_capsule_sources,
)

__all__ = [
    "CapsuleAdmissionRequest",
    "CapsuleCompilationOutcome",
    "admit_capsule_sources",
    "compile_admitted_capsule",
]
