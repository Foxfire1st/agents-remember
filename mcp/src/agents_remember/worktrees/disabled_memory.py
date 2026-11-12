"""The removed disabled memory mode at lifecycle boundaries (MIK-R50).

``disabled`` is a removed memory mode: no task can be started in it, and no
route lets a contract that carries it change or land code. This module is the
one place that decides the refusal's true reason and shapes its facts:

* where the configured memory repository holds converted knowledge, invariant
  work is mandatory (D5) and the refusal says where that knowledge was found;
* elsewhere the mode is refused as removed and unusable, with the setup action
  where the memory repository is not set up;
* when Git cannot say whether knowledge exists, the refusal names the probe
  failure and still refuses;
* where the memory root does not exist at all, there is nothing to probe and
  the caller keeps the existing "memory is missing" error instead.

The cutover lock's own test decides the reason
(:func:`~agents_remember.worktrees.cutover_lock.converted_memory_location`):
the layout marker at the tip of a local branch or in a registered worktree. It
decides only which reason the refusal gives, never whether the refusal
applies.

Every surface publishes the same status (``memory-opt-out-refused``) with the
contract, the mode, the raw cell (when the mode was read from an empty or
unknown cell), the reason and the known way out. No surface asks for a
developer decision, because the action is known.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agents_remember.errors import MemoryOptOutFacts
from agents_remember.kernel.memory_mode import (
    LEAF_WAY_OUT,
    SERIES_WAY_OUT,
    SETUP_FIRST_ACTION,
    START_AGAIN_ACTION,
    disabled_opt_out_detail,
    disabled_opt_out_refusal_fields,
    opt_out_reason_knowledge,
    opt_out_reason_probe_failure,
    opt_out_reason_removed,
)
from agents_remember.worktrees.cutover_lock import (
    CutoverProbeError,
    converted_memory_location,
)
from agents_remember.worktrees.modules.models import WorktreeCommandResult

if TYPE_CHECKING:  # Annotation only; a runtime import would risk a module cycle.
    from agents_remember.worktrees.modules.args import WorktreeArgs

__all__ = [
    "OPT_OUT_STATUS",
    "OptOutSubject",
    "configured_memory_root",
    "contract_raw_memory_cell",
    "disabled_opt_out_result",
    "is_disabled_contract",
    "opt_out_detail_for_contract",
    "opt_out_refusal_result",
    "opt_out_result_fields",
    "start_opt_out_result",
    "subject_for_contract",
]

OPT_OUT_STATUS = "memory-opt-out-refused"
"""The one status every disabled-mode refusal publishes."""

WayOut = Literal["start", "leaf", "series"]
"""Which known action the refusal names: a fresh start, or a legacy escape."""

WAY_OUTS: dict[WayOut, str] = {
    "start": START_AGAIN_ACTION,
    "leaf": LEAF_WAY_OUT,
    "series": SERIES_WAY_OUT,
}


@dataclass(frozen=True)
class OptOutSubject:
    """What a disabled-mode refusal is about: a legacy contract or a start request."""

    contract_path: Path | None = None
    coordination_root: Path | None = None
    repo_name: str = ""
    unknown_cells: tuple[str, ...] = ()


def subject_for_contract(contract: object) -> OptOutSubject:
    """The refusal subject for one loaded contract."""

    unknown = getattr(contract, "unknown_cells", ())
    return OptOutSubject(
        contract_path=getattr(contract, "contract_path", None),
        coordination_root=getattr(contract, "coordination_root", None),
        repo_name=str(getattr(contract, "repo_name", "") or ""),
        unknown_cells=tuple(unknown or ()),
    )


def configured_memory_root(coordination_root: Path, repo_name: str) -> Path:
    """The configured memory repository of a code repository, by convention."""

    return coordination_root / "memory-repos" / f"ar-{repo_name}"


def is_disabled_contract(contract: object) -> bool:
    """Whether a contract carries a non-external memory mode."""

    return getattr(contract, "memory_mode", "external") != "external"


def contract_raw_memory_cell(unknown_cells: tuple[str, ...] | None) -> str | None:
    """The raw cell when the mode was read from an empty or unknown cell.

    The read path degrades an unreadable ``memory_mode`` to ``disabled`` off
    the memory section it governs and records the raw token in
    ``unknown_cells`` (``memory_mode='<token>' read as 'disabled'``); an
    explicit ``disabled`` token needs no raw cell named.
    """

    for entry in unknown_cells or ():
        if isinstance(entry, str) and entry.startswith("memory_mode="):
            return entry
    return None


def _reason_for_root(memory_root: Path) -> tuple[str, bool]:
    """The refusal reason for one configured memory root, plus setup-needed."""

    if not memory_root.exists():
        return opt_out_reason_removed(setup_first=True), True
    try:
        location = converted_memory_location(memory_root)
    except CutoverProbeError as error:
        return opt_out_reason_probe_failure(str(error)), False
    if location is not None:
        return opt_out_reason_knowledge(location), False
    if not (memory_root / ".git").exists():
        return opt_out_reason_removed(setup_first=True), True
    return opt_out_reason_removed(), False


def _subject_facts(
    subject: OptOutSubject, operation: str, way_out: WayOut
) -> tuple[str, MemoryOptOutFacts]:
    """The refusal detail and facts for one subject."""

    raw_cell = contract_raw_memory_cell(subject.unknown_cells)
    repository: str | None = None
    if subject.coordination_root is not None and subject.repo_name:
        memory_root = configured_memory_root(subject.coordination_root, subject.repo_name)
        repository = memory_root.as_posix()
        reason, setup_needed = _reason_for_root(memory_root)
    else:
        reason, setup_needed = opt_out_reason_removed(), False
    action = WAY_OUTS[way_out]
    if setup_needed:
        action = SETUP_FIRST_ACTION if way_out == "start" else f"{SETUP_FIRST_ACTION}; {action}"
    contract = subject.contract_path.as_posix() if subject.contract_path is not None else None
    detail = disabled_opt_out_detail(
        operation=operation,
        contract=contract,
        raw_cell=raw_cell,
        reason=reason,
        way_out=action,
    )
    facts = MemoryOptOutFacts(
        repository=repository,
        reason=reason,
        next_action=action,
        artifact=contract,
        raw_cell=raw_cell,
    )
    return detail, facts


def opt_out_result_fields(
    subject: OptOutSubject, operation: str, way_out: WayOut = "leaf"
) -> dict[str, object]:
    """The publishable refusal facts for a legacy disabled contract."""

    detail, facts = _subject_facts(subject, operation, way_out)
    return disabled_opt_out_refusal_fields(detail, facts=facts)


def opt_out_detail_for_contract(contract: object, operation: str) -> str:
    """The refusal detail for a legacy disabled contract, for raise/text paths."""

    subject = subject_for_contract(contract)
    detail, _facts = _subject_facts(
        subject, operation, "series" if getattr(contract, "kind", "leaf") == "series" else "leaf"
    )
    return detail


def opt_out_refusal_result(contract: object, operation: str) -> WorktreeCommandResult:
    """The entry refusal for a legacy disabled contract on a result-returning route.

    Every lifecycle-layer route that changes or lands a task refuses a contract
    whose mode is not external at its entry (MIK-R50 R4a), independently of the
    configured-authority check. Status, attach and abandon keep reading such a
    contract; they never call this.
    """

    subject = subject_for_contract(contract)
    fields = opt_out_result_fields(
        subject,
        operation,
        "series" if getattr(contract, "kind", "leaf") == "series" else "leaf",
    )
    return WorktreeCommandResult(
        2,
        {
            "state": fields["status"],
            "summary": fields["detail"],
            **fields,
        },
    )


def start_opt_out_result(subject: OptOutSubject) -> dict[str, object] | None:
    """The refusal facts for a start that asks for the removed mode.

    Returns ``None`` where the memory root does not exist at all: that start
    keeps the existing "memory is missing" error instead of this refusal.
    """

    if subject.coordination_root is not None and subject.repo_name:
        memory_root = configured_memory_root(subject.coordination_root, subject.repo_name)
        if not memory_root.exists():
            return None
    detail, facts = _subject_facts(subject, "worktree_start", "start")
    return disabled_opt_out_refusal_fields(detail, facts=facts)


def disabled_opt_out_result(args: WorktreeArgs) -> WorktreeCommandResult | None:
    """Refuse a start that asks for the removed disabled memory mode (MIK-R50).

    Answered here, at the top of ``worktree_start`` beside the removed-vocabulary
    refusal, because this is the last point at which no leaf contract, series
    contract, branch, worktree, task binding or lifecycle binding exists to
    unwind. The dry run and the real run answer identically. It covers every way
    the mode is asked for: the ``memory_mode`` argument, the
    ``memory_choice="disabled-memory"`` answer, and the same two options of the
    module command line (which arrives here as the same two fields).

    The refusal carries its own status (``memory-opt-out-refused``) with the
    mode, the repository, the true reason and the action -- never the removed
    ``internal`` mode's text and never a general invalid-mode text. Where the
    memory root does not exist at all there is nothing to probe, so this
    returns ``None`` and the start keeps the existing "memory is missing"
    error from context resolution.
    """

    if args.memory_mode != "disabled" and args.memory_choice != "disabled-memory":
        return None
    if args.coordination_root is None or args.code_repository_name is None:
        fields = disabled_opt_out_refusal_fields(
            disabled_opt_out_detail(
                operation="worktree_start",
                reason=opt_out_reason_removed(),
                way_out=START_AGAIN_ACTION,
            ),
            facts=MemoryOptOutFacts(
                reason=opt_out_reason_removed(),
                next_action=START_AGAIN_ACTION,
            ),
        )
    else:
        fields = start_opt_out_result(
            OptOutSubject(
                coordination_root=args.coordination_root,
                repo_name=args.code_repository_name,
            )
        )
        if fields is None:
            return None
    return WorktreeCommandResult(
        2,
        {
            "state": fields["status"],
            "summary": fields["detail"],
            **fields,
        },
    )
