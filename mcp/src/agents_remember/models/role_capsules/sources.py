"""The locked instruction-unit plan: what the manifest declares for one compilation.

An :class:`CapsuleInstructionUnit` is one *declared* instruction unit — a
composition root plus the block identity it must resolve to — together with the
content-addressed source that was loaded for it. Building the plan before reading
anything is what makes the "a required block is missing" check non-vacuous: the
plan says which identities must resolve, and loading is validated *against* the
plan rather than defining it.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.errors import CapsuleSourceError
from agents_remember.models.role_capsules.types import (
    CAPSULE_COMPOSITION_ORDER,
    SELECTION_SHARED_CORE,
    CapsuleBlockIdentity,
    CapsuleCompositionRoot,
    CapsuleDigest,
    CapsuleSelectionReference,
    compute_content_digest,
)

#: The canonical identity a URL-safe path maps to: ``"<root>:<name>"``.
INSTRUCTION_FILE_SUFFIX = ".md"
SPECIALIZATION_ROOT_DIRECTORY = "specializations"


@dataclass(frozen=True, slots=True)
class CapsuleDeclaredInstruction:
    """One instruction the manifest requires: its identity and why it is required."""

    identity: CapsuleBlockIdentity
    composition_root: CapsuleCompositionRoot
    authorities: tuple[CapsuleSelectionReference, ...]

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("declared instruction identity must be non-blank")
        if not self.identity.startswith(f"{self.composition_root}:"):
            raise ValueError(
                f"declared instruction identity {self.identity!r} does not belong to "
                f"composition root {self.composition_root!r}"
            )


@dataclass(frozen=True, slots=True)
class CapsuleSource:
    """One loaded, content-addressed source on disk.

    ``revision`` is the digest of the bytes as read. ``text`` is the decoded
    instruction content; a source that decodes to nothing is a defect rather than
    a block that silently contributes no obligations.
    """

    identity: CapsuleBlockIdentity
    composition_root: CapsuleCompositionRoot
    path: str
    content: bytes
    revision: CapsuleDigest

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("source identity must be non-blank")
        if not self.path.strip():
            raise ValueError("source path must be non-blank")
        if self.revision != compute_content_digest(self.content):
            raise ValueError(
                f"source {self.path!r} carries a revision that is not the digest of its bytes"
            )

    def text(self) -> str:
        """The decoded instruction content, refusing content that decodes to nothing.

        Non-UTF-8 bytes are an I/O-boundary defect rather than a selection one, so the
        refusal is the typed source error the admission boundary already uses.
        """

        try:
            decoded = self.content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CapsuleSourceError(
                status="source-not-utf8",
                detail=f"source {self.path!r} is not valid UTF-8 instruction text",
                next_action="re-author the source as UTF-8 text, or remove it from the admission",
            ) from error
        if not decoded.strip():
            raise ValueError(
                f"source {self.path!r} is empty; a required instruction block must carry content"
            )
        return decoded


def instruction_identity(composition_root: CapsuleCompositionRoot, name: str) -> str:
    """The canonical instruction identity for one block name inside a root."""

    return f"{composition_root}:{name}"


def specializations_declared_identity(path: str) -> str:
    """The identity an admitted repository-specialization path must resolve to.

    Specialization is the one composition root with no manifest-declared file list —
    it is admitted on the binding — so its identity is defined here rather than left
    to a naming accident. The identity is the instruction file's own name, not its
    path: a nested ``specializations/<group>/<name>.md`` therefore claims the same
    identity as a top-level ``specializations/<name>.md`` and collides with it,
    instead of quietly becoming a second block nobody declared.
    """

    prefix = f"{SPECIALIZATION_ROOT_DIRECTORY}/"
    if not path.startswith(prefix) or not path.endswith(INSTRUCTION_FILE_SUFFIX):
        raise ValueError(
            f"repository specialization {path!r} must live under {SPECIALIZATION_ROOT_DIRECTORY}/ "
            f"and name a {INSTRUCTION_FILE_SUFFIX} instruction file"
        )
    # ``SPECIALIZATION_ROOT_DIRECTORY/.md`` is the only path that would reduce to an
    # empty name, and it is refused earlier as a path with a ``.`` component, so no
    # emptiness check is needed here.
    name = path[len(prefix) : -len(INSTRUCTION_FILE_SUFFIX)].rsplit("/", 1)[-1]
    return instruction_identity("specialization", name)


def skills_declared_identity(origin: str, skill: str) -> str:
    """The canonical identity of a skill reference: origin plus skill name.

    Identity includes the origin because a bare skill name collides across servers,
    and the whole point of carrying an origin is that the same name from two servers
    is two different references.
    """

    if not origin.strip() or not skill.strip():
        raise ValueError("a skill reference identity needs a non-blank origin and skill name")
    return f"{origin}#{skill}"


def root_of_identity(identity: str) -> str:
    """The composition root an instruction identity belongs to.

    ``<root>:<name>`` carries its root; a skill identity is ``<origin>#<name>`` and
    does not, so it is recognised by its separator instead of by a root prefix. Having
    one function answer this keeps the two callers that need it — the admitted-root
    agreement check and the identity index — from disagreeing about skill identities.
    """

    head = identity.split(":", 1)[0]
    if head in CAPSULE_COMPOSITION_ORDER:
        return head
    if "#" in identity:
        return "skill"
    return head


def shared_core_reference(name: str) -> str:
    """The selection reference for a shared-core block."""

    return f"core:{name}:{SELECTION_SHARED_CORE}"


__all__ = [
    "INSTRUCTION_FILE_SUFFIX",
    "SPECIALIZATION_ROOT_DIRECTORY",
    "CapsuleDeclaredInstruction",
    "CapsuleSource",
    "instruction_identity",
    "root_of_identity",
    "shared_core_reference",
    "skills_declared_identity",
    "specializations_declared_identity",
]
