"""The supported memory topology/mode vocabulary, and the refusals for removed modes.

``internal`` memory -- a repo-sidecar root at ``<code-repository-root>/ar-memory`` with a
repo-sidecar coordination tree beside it -- was removed from the product. It is incompatible
with the worktree lifecycle: it keeps onboarding inside the code repository, so there is no
memory repository to branch, no memory commit to attribute and no ledger row to map, and the
capsule/closeout contract has nothing to bind.

``disabled`` memory -- a task that runs with no memory at all -- was removed from the
product (MIK-R50). A task in it can be neither closed out, synced, integrated nor landed,
and where converted knowledge exists invariant work is mandatory (D5), so no task may opt
out of memory. The supported set is ``external`` alone.

Two vocabularies live here because they are one fact seen from two altitudes: a repository's
``Topology`` says where its memory root lives, and a worktree contract's ``MemoryMode`` says
what the started task does with it. All removed members were lost together, so all are
declared together rather than re-declared per consumer.

A caller that asks for a removed mode is refused by name, with an operator-legible message
that carries the removal, the supported set and the route out. It is never substituted with
``external``, never defaulted, and never degraded into some other mode. Existing state that
records a removed mode (a contract, a settings block, a memory root) is *reported* with its
exact artifact and left on disk untouched: this module names and detects that state, it never
rewrites it.

The two removed modes keep distinct refusals: ``internal`` keeps its own unsupported-mode
refusal, and ``disabled`` is refused with the opt-out refusal, which names the true reason
(converted knowledge where it exists, removal elsewhere) and the known action. Answering
``disabled`` with the ``internal`` text would blame a memory root inside the code repository
that was never the problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NoReturn, cast, get_args

from agents_remember.errors import (
    MemoryModeUnsupportedError,
    MemoryOptOutFacts,
    MemoryOptOutRefusedError,
)
from agents_remember.kernel.recorded_reads import observed_resolve

Topology = Literal["external"]
"""Where a repository's durable memory root lives. ``internal`` was removed."""

SUPPORTED_TOPOLOGIES: tuple[Topology, ...] = get_args(Topology)

MemoryMode = Literal["external"]
"""What a worktree contract does with memory. ``internal`` and ``disabled`` were removed."""

SUPPORTED_MEMORY_MODES: tuple[MemoryMode, ...] = get_args(MemoryMode)

ReadableMemoryMode = Literal["external", "disabled"]
"""What a contract on disk may carry: the supported mode plus the removed one.

A contract that records ``disabled`` stays readable -- status, attach and guarded
abandon need it -- while nothing may start in it or write it anew. Read paths,
response models and projections declare this vocabulary; request and amendment
shapes declare :data:`MemoryMode`, so producing the removed mode is a type error
at the writer.
"""

READABLE_MEMORY_MODES: tuple[ReadableMemoryMode, ...] = get_args(ReadableMemoryMode)

REMOVED_MEMORY_MODES: tuple[str, ...] = ("internal", "disabled")
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
    return observed_resolve(code_repository_root / LEGACY_INTERNAL_MEMORY_DIRNAME)


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
    """Refuse a removed memory mode by name, never by substituting a supported one.

    ``disabled`` keeps its own opt-out refusal (MIK-R50) rather than the
    ``internal`` text: the caller is told the true reason and the known action
    instead of a memory root inside the code repository that was never there.
    """

    if value == "disabled":
        raise disabled_opt_out_refusal(
            _disabled_removed_detail(artifact=artifact),
            facts=MemoryOptOutFacts(
                artifact=artifact,
                reason=_DISABLED_REMOVED_REASON,
                next_action=START_AGAIN_ACTION,
            ),
        )
    raise memory_mode_refusal(value, artifact=artifact)


DISABLED_MEMORY_MODE = "disabled"
"""The removed opt-out mode, named once so no surface re-spells it."""

_DISABLED_UNUSABLE = "a task in it can be neither closed out, synced, integrated nor landed"
"""Why the removed mode refuses everywhere, in one clause."""

_DISABLED_REMOVED_REASON = f"the disabled memory mode is removed: {_DISABLED_UNUSABLE}"
"""The true reason where no converted knowledge decides it."""

START_AGAIN_ACTION = "start again without `memory_mode`, in the default `external` mode"
"""The action every start refusal names."""

SETUP_FIRST_ACTION = (
    "set up the memory repository first (`c-00-initialize-memory-repo`), then "
    "start again without `memory_mode`"
)
"""The action where the memory repository is not set up."""

LEAF_WAY_OUT = (
    "save the changes worth keeping (abandon discards the leaf's worktree and branch), "
    "run `worktree_abandon` (the dry run first), then `worktree_start` at the same address "
    "without `memory_mode`"
)
"""The way out for a leaf contract that carries the removed mode."""

SERIES_WAY_OUT = (
    "abandon the leaves, declare the master abandoned in its task document, abandon the "
    "series contract with `worktree_abandon`, and start the work under a new master"
)
"""The way out under a master whose series contract carries the removed mode."""


def opt_out_reason_knowledge(location: str) -> str:
    """The true reason where converted knowledge exists (D5)."""

    return (
        "invariant work is mandatory, and a task cannot opt out of memory (D5): "
        f"converted knowledge was found at {location}"
    )


def opt_out_reason_removed(*, setup_first: bool = False) -> str:
    """The true reason where no converted knowledge decides the refusal."""

    if setup_first:
        return f"{_DISABLED_REMOVED_REASON}; the memory repository is not set up"
    return _DISABLED_REMOVED_REASON


def opt_out_reason_probe_failure(error: str) -> str:
    """The reason when Git cannot say whether knowledge exists."""

    return (
        "the cutover probe cannot say whether converted knowledge exists "
        f"({error}), and an unanswered probe never authorizes opting out of memory"
    )


def _disabled_removed_detail(artifact: str | None = None) -> str:
    origin = f"{artifact} records " if artifact is not None else ""
    return (
        f"{origin}memory mode `disabled`, which was removed from Agents Remember: "
        f"{_DISABLED_UNUSABLE}. The supported memory modes are `external`. {START_AGAIN_ACTION}."
    )


def disabled_opt_out_detail(
    *,
    operation: str,
    contract: str | None = None,
    raw_cell: str | None = None,
    reason: str,
    way_out: str,
) -> str:
    """The one refusal text every disabled-mode surface uses.

    It names the contract and the mode it carries (with the raw cell when the
    mode was read from an empty or unknown cell), the true reason and the known
    way out. It never asks for a developer decision.
    """

    if contract is not None:
        origin = f"{operation} refuses: contract {contract} carries memory mode `disabled`"
        if raw_cell is not None:
            origin += f" (read from {raw_cell})"
    else:
        origin = f"{operation} refuses memory mode `disabled`"
    return f"{origin}: {reason}. {way_out}."


def disabled_opt_out_refusal(
    detail: str,
    *,
    facts: MemoryOptOutFacts | None = None,
) -> MemoryOptOutRefusedError:
    """The one refusal every disabled-mode surface raises or publishes."""

    return MemoryOptOutRefusedError(
        detail,
        requested=DISABLED_MEMORY_MODE,
        supported=SUPPORTED_MEMORY_MODES,
        facts=facts,
    )


def disabled_opt_out_refusal_fields(
    detail: str,
    *,
    facts: MemoryOptOutFacts | None = None,
) -> dict[str, object]:
    """The publishable refusal facts, for a surface that returns a typed result."""

    return disabled_opt_out_refusal(detail, facts=facts).response_fields()


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
