"""The supported memory topology/mode vocabulary, and the refusal for the removed one.

``internal`` memory -- a repo-sidecar root at ``<code-repository-root>/ar-memory`` with a
repo-sidecar coordination tree beside it -- was removed from the product. It is incompatible
with the worktree lifecycle: it keeps onboarding inside the code repository, so there is no
memory repository to branch, no memory commit to attribute and no ledger row to map, and the
capsule/closeout contract has nothing to bind. The supported set is ``external`` and
``disabled``.

Two vocabularies live here because they are one fact seen from two altitudes: a repository's
``Topology`` says where its memory root lives, and a worktree contract's ``MemoryMode`` says
what the started task does with it. Both lost their removed member together, so both are
declared together rather than re-declared per consumer.

A caller that asks for the removed mode is refused by name, with an operator-legible message
that carries the removal, the supported set and the route out. It is never substituted with
``external``, never defaulted, and never degraded into some other mode. Existing state that
records the removed mode (a contract, a settings block, a memory root) is *reported* with its
exact artifact and left on disk untouched: this module names and detects that state, it never
rewrites it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NoReturn, cast, get_args

from agents_remember.errors import MemoryModeUnsupportedError

Topology = Literal["external"]
"""Where a repository's durable memory root lives. ``internal`` was removed."""

SUPPORTED_TOPOLOGIES: tuple[Topology, ...] = get_args(Topology)

MemoryMode = Literal["external", "disabled"]
"""What a worktree contract does with memory. ``internal`` was removed."""

SUPPORTED_MEMORY_MODES: tuple[MemoryMode, ...] = get_args(MemoryMode)

REMOVED_MEMORY_MODES: tuple[str, ...] = ("internal",)
"""Vocabulary members this product no longer produces, accepts, types or documents."""

LEGACY_INTERNAL_MEMORY_DIRNAME = "ar-memory"
"""Directory name of the removed repo-sidecar memory root, kept only to detect and report it."""

LEGACY_INTERNAL_COORDINATION_DIRNAME = "ar-coordination"
"""Directory name of the removed repo-sidecar coordination root, kept only to report it."""

MEMORY_MODE_REMEDIES: tuple[str, ...] = (
    "Re-point the repository to an external memory root at "
    "`<coordination-root>/memory-repos/ar-<code-repository-name>` and set the contract's "
    "`memory_mode` to `external`, or re-initialize the memory root with the "
    "`c-00-initialize-memory-repo` skill.",
    "Nothing is migrated automatically and no existing memory root, contract or settings "
    "file is rewritten in place.",
)
"""The documented route out of the removed mode, stated once for every refusal surface."""


def legacy_internal_memory_root(code_repository_root: Path) -> Path:
    """The removed repo-sidecar memory root, returned only so existing state can be reported."""
    return (code_repository_root / LEGACY_INTERNAL_MEMORY_DIRNAME).resolve()


def legacy_internal_coordination_root(code_repository_root: Path) -> Path:
    """The removed repo-sidecar coordination root, returned only so existing state is reported."""
    return (code_repository_root / LEGACY_INTERNAL_COORDINATION_DIRNAME).resolve()


def is_supported_memory_mode(value: str) -> bool:
    return value in SUPPORTED_MEMORY_MODES


def is_removed_memory_mode(value: str) -> bool:
    return value in REMOVED_MEMORY_MODES


def memory_mode_refusal_message(value: str, *, artifact: str | None = None) -> str:
    """The one refusal text every surface uses, so the removal reads identically everywhere."""
    supported = ", ".join(f"`{mode}`" for mode in SUPPORTED_MEMORY_MODES)
    origin = f"{artifact} records " if artifact is not None else ""
    return (
        f"{origin}memory mode `{value}`, which was removed from Agents Remember: a memory root "
        "inside the code repository has no memory branch, no memory commit and no ledger row to "
        f"map. The supported memory modes are {supported}. " + " ".join(MEMORY_MODE_REMEDIES)
    )


def memory_mode_refusal(value: str, *, artifact: str | None = None) -> MemoryModeUnsupportedError:
    """The one refusal every surface raises or publishes, so the removal reads identically."""
    return MemoryModeUnsupportedError(
        memory_mode_refusal_message(value, artifact=artifact),
        requested=value,
        supported=SUPPORTED_MEMORY_MODES,
        artifact=artifact,
        remedies=MEMORY_MODE_REMEDIES,
    )


def memory_mode_refusal_fields(value: str, *, artifact: str | None = None) -> dict[str, object]:
    """The publishable refusal facts, for a surface that returns a typed result instead.

    The worktree tools answer with a result object rather than an exception, so they need the
    same facts as a payload. Building both from one construction keeps a returned status and a
    raised status from drifting into two spellings of the same refusal.
    """
    return memory_mode_refusal(value, artifact=artifact).response_fields()


def refuse_removed_memory_mode(value: str, *, artifact: str | None = None) -> NoReturn:
    """Refuse a removed memory mode by name, never by substituting a supported one."""
    raise memory_mode_refusal(value, artifact=artifact)


def require_supported_memory_mode(value: str) -> MemoryMode:
    """Narrow a caller-supplied memory mode onto the supported set, or refuse.

    The one narrowing point for the vocabulary: a removed member is refused by name with its
    own typed status, and any other unrecognized token is refused as an invalid argument. A
    caller that skipped this and compared strings inline would answer ``internal`` with the
    same message as a typo, which is how a removed mode quietly becomes "just invalid input".
    """
    if is_removed_memory_mode(value):
        refuse_removed_memory_mode(value)
    if not is_supported_memory_mode(value):
        raise ValueError(
            "memory mode must be one of "
            + ", ".join(f"`{mode}`" for mode in SUPPORTED_MEMORY_MODES)
        )
    return cast(MemoryMode, value)


def require_supported_topology(value: str) -> Topology:
    """Narrow a caller-supplied topology onto the supported set, or refuse."""
    if is_removed_memory_mode(value):
        refuse_removed_memory_mode(value)
    if value not in SUPPORTED_TOPOLOGIES:
        raise ValueError(
            "topology must be one of "
            + ", ".join(f"`{topology}`" for topology in SUPPORTED_TOPOLOGIES)
        )
    return cast(Topology, value)
