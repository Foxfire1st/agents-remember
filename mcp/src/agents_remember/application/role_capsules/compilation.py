"""The admitted compile entry point: read the canonical tree, then compile.

One call for a consumer that has an admitted binding and a canonical source tree:
read the explicitly named source set, hand it to the pure compiler, and return
either the capsule or the refusal *with* its explanation projection. A refusal is a
value here rather than an exception, because a caller that has to explain a failure
needs the diagnostic manifest as much as a caller that succeeded needs the capsule —
and because "compilation failure never becomes a partially valid capsule" is easier
to keep true when the two outcomes are different shapes of one result.

This module is also the seam for the later task projection: it accepts any
:class:`~agents_remember.models.role_capsules.types.CapsuleTaskProjectionSource` and
passes it through untouched. Task state is never read, rendered or rewritten here.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.application.role_capsules.sources import (
    CapsuleAdmissionRequest,
    admit_capsule_sources,
)
from agents_remember.errors import CapsuleCompilationError
from agents_remember.models.role_capsules.compiler import (
    compile_role_capsule,
    manifest_for_error,
    refused_manifest,
)
from agents_remember.models.role_capsules.diagnostics import CapsuleManifest
from agents_remember.models.role_capsules.manifest import (
    CapsuleCompositionManifest,
    parse_composition_manifest,
)
from agents_remember.models.role_capsules.sources import CapsuleSource
from agents_remember.models.role_capsules.types import (
    CapsuleBinding,
    CapsuleCapsule,
    CapsuleCompilationResult,
    CapsuleDigest,
    CapsuleTaskProjectionSource,
)


@dataclass(frozen=True, slots=True)
class CapsuleCompilationOutcome:
    """Exactly one of: a compiled capsule, or the explanation of its refusal.

    ``error`` is the typed refusal, so a caller branches on
    :attr:`~agents_remember.errors.CapsuleCompilationError.status` instead of parsing
    prose. ``manifest`` is present in both shapes, so an operator can always see who
    the seat was, which sources were admitted and what stopped the run.
    """

    result: CapsuleCompilationResult | None
    manifest: CapsuleManifest
    error: CapsuleCompilationError | None

    def __post_init__(self) -> None:
        if (self.result is None) == (self.error is None):
            raise ValueError(
                "a compilation outcome is exactly one of a compiled capsule or a refusal"
            )

    @property
    def ok(self) -> bool:
        return self.result is not None

    @property
    def capsule(self) -> CapsuleCapsule | None:
        """The compiled capsule, or ``None`` when this outcome is a refusal."""

        return None if self.result is None else self.result.capsule

    @property
    def semantic_digest(self) -> CapsuleDigest | None:
        """The capsule digest, or ``None`` for a refusal — a refusal has no identity."""

        return None if self.result is None else self.result.semantic_digest

    def render_explanation(self) -> str:
        """One operator-facing line: the digest, or the refusal and its remedy."""

        if self.error is not None:
            return self.error.render()
        return f"capsule {self.semantic_digest} over {len(self.manifest.sources)} source(s)"


def compile_admitted_capsule(
    binding: CapsuleBinding,
    request: CapsuleAdmissionRequest,
    projection: CapsuleTaskProjectionSource | None = None,
) -> CapsuleCompilationOutcome:
    """Admit the requested sources and compile, returning success or refusal.

    A source tree that cannot be read at all — a missing file, a traversal attempt,
    an unreadable root — produces the same refusal shape as a selection defect,
    carrying the admitted-facts half of the manifest, because those facts were true
    regardless and are what an operator needs to act.
    """

    try:
        sources = admit_capsule_sources(request)
    except CapsuleCompilationError as error:
        return CapsuleCompilationOutcome(
            result=None, manifest=refused_manifest(binding, error), error=error
        )
    manifest_bytes = _manifest_bytes(sources, request)
    parsed = _parsed_or_none(manifest_bytes)
    try:
        result = compile_role_capsule(
            binding, manifest_bytes, sources, request.as_selection(), projection
        )
    except CapsuleCompilationError as error:
        return CapsuleCompilationOutcome(
            result=None,
            manifest=_failure_manifest(parsed, binding, error),
            error=error,
        )
    return CapsuleCompilationOutcome(result=result, manifest=result.manifest, error=None)


def _failure_manifest(
    parsed: CapsuleCompositionManifest | None,
    binding: CapsuleBinding,
    error: CapsuleCompilationError,
) -> CapsuleManifest:
    if parsed is None:
        return refused_manifest(binding, error)
    return manifest_for_error(parsed, binding, error)


def _manifest_bytes(sources: tuple[CapsuleSource, ...], request: CapsuleAdmissionRequest) -> bytes:
    for source in sources:
        if source.path == request.manifest:
            return source.content
    return b""


def _parsed_or_none(manifest_bytes: bytes) -> CapsuleCompositionManifest | None:
    try:
        return parse_composition_manifest(manifest_bytes)
    except CapsuleCompilationError:
        return None


__all__ = [
    "CapsuleCompilationOutcome",
    "compile_admitted_capsule",
]
