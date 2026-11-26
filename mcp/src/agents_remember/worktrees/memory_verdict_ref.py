"""Validate memory-review verdict references and map them to their collected form.

When a Reviewer records a memory round (ordinary or record-only), any supplied
``verdictRef`` must name a real file using one of three accepted forms:

* an absolute path, checked as it stands;
* ``role-reports/<rel>``, checked under the bound leaf contract's worktree group;
* ``notes/reports/<rel>``, accepted when ``<task>/notes/reports/<rel>`` or
  ``<group>/role-reports/<rel>`` exists (because the copy is made only at a
  send or at cleanup).

Code rounds, route reviews, and rounds that omit ``verdictRef`` are not covered
by this check.

At closeout and direct landing, a qualifying round's reference is mapped to
``notes/reports/<rel>`` through :func:`collected_report_ref` using exact relative
paths against the contract's own report directories; a pre-existing reference
that cannot be mapped stays as recorded and is named in the closeout answer.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from agents_remember.tasks.route_review import usable_verdict_ref
from agents_remember.worktrees.review_history import ReviewHistoryError
from agents_remember.worktrees.role_reports import (
    COLLECTED_REPORT_DIRECTORY,
    COLLECTED_REPORT_PARTS,
    LEGACY_ROLE_REPORT_ALIAS,
    ROLE_REPORT_DIRECTORY,
    collected_report_ref,
    is_collected_report_ref,
)
from agents_remember.worktrees.task_resolver import leaf_enclosure_path
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

MEMORY_VERDICT_REF_INVALID = "review-verdict-ref-invalid"
_ACCEPTED_FORMS = (
    "Accepted forms: an absolute path to an existing file, "
    f"{ROLE_REPORT_DIRECTORY}/<rel> under the bound leaf's worktree group, or "
    f"{COLLECTED_REPORT_DIRECTORY}/<rel> (existing under the task folder or the leaf's "
    f"{ROLE_REPORT_DIRECTORY}/). Write the verdict file first and pass one of those paths."
)


def unmapped_verdict_ref_note(reference: str) -> str:
    """Closeout sentence when a pre-existing ``verdictRef`` cannot map to ``notes/reports/<rel>``."""

    return (
        f"The recorded memory review verdictRef {reference!r} could not be mapped to "
        f"{COLLECTED_REPORT_DIRECTORY}/<rel> and was written as recorded."
    )


def memory_verdict_contract(
    doc_kind: str,
    doc_id: str,
    contract: WorktreeContract | None,
    task_root: Path | None,
) -> WorktreeContract | None:
    """Return the bound leaf worktree contract for a memory review, never a series contract."""

    if contract is not None and contract.kind == "leaf":
        return contract
    if doc_kind != "subTask" or task_root is None or not doc_id:
        return None
    enclosure = leaf_enclosure_path(task_root, doc_id)
    if not enclosure.is_file():
        return None
    try:
        loaded = load_contract(enclosure)
    except ContractError:
        return None
    return loaded if loaded.kind == "leaf" else None


def require_bound_memory_verdict_ref(
    doc_kind: str,
    doc_id: str,
    raw: object,
    *,
    contract: WorktreeContract | None,
    task_root: Path | None,
) -> None:
    """Resolve the leaf binding and validate a supplied memory-round ``verdictRef``."""

    if raw is None:
        return
    bound = memory_verdict_contract(doc_kind, doc_id, contract, task_root)
    require_memory_verdict_ref(
        raw,
        task_root=task_root,
        worktree_group=bound.worktree_group if bound is not None else None,
    )


def require_memory_verdict_ref(
    raw: object,
    *,
    task_root: Path | None,
    worktree_group: Path | None,
) -> None:
    """Refuse a supplied memory-round ``verdictRef`` that does not name an existing file."""

    if raw is None:
        return
    ref = _parse_verdict_ref_text(raw)
    if Path(ref).is_absolute():
        if not Path(ref).is_file():
            _refuse(f"memory review verdictRef {ref!r} does not name an existing file.")
        return
    role_rel = _relative_under_prefix(ref, (ROLE_REPORT_DIRECTORY,))
    if role_rel is not None:
        _require_role_report_file(ref, role_rel, worktree_group)
        return
    collected_rel = _relative_under_prefix(ref, COLLECTED_REPORT_PARTS)
    if collected_rel is not None:
        _require_collected_report_file(ref, collected_rel, task_root, worktree_group)
        return
    _refuse(f"memory review verdictRef {ref!r} is not an accepted path form.")


def _parse_verdict_ref_text(raw: object) -> str:
    if not isinstance(raw, str):
        _refuse(f"memory review verdictRef must be a path string, got {type(raw).__name__}.")
    assert isinstance(raw, str)
    ref = usable_verdict_ref(raw)
    if ref is None:
        _refuse(f"memory review verdictRef {raw!r} is blank, non-printable, or too long.")
    assert ref is not None
    return ref


def _require_role_report_file(
    ref: str, role_rel: PurePosixPath, worktree_group: Path | None
) -> None:
    if worktree_group is None:
        _refuse(
            f"memory review verdictRef {ref!r} requires a bound leaf worktree group with "
            f"{ROLE_REPORT_DIRECTORY}/, but none is bound."
        )
    assert worktree_group is not None
    base = worktree_group / ROLE_REPORT_DIRECTORY
    if not _confined_existing_file(base, role_rel):
        _refuse(
            f"memory review verdictRef {ref!r} does not name an existing file at "
            f"{(base / role_rel).as_posix()}."
        )


def _require_collected_report_file(
    ref: str,
    collected_rel: PurePosixPath,
    task_root: Path | None,
    worktree_group: Path | None,
) -> None:
    checked: list[str] = []
    if task_root is not None:
        canonical_base = task_root / COLLECTED_REPORT_DIRECTORY
        if _confined_existing_file(canonical_base, collected_rel):
            return
        checked.append((canonical_base / collected_rel).as_posix())
    if worktree_group is not None:
        local_base = worktree_group / ROLE_REPORT_DIRECTORY
        if _confined_existing_file(local_base, collected_rel):
            return
        checked.append((local_base / collected_rel).as_posix())
    where = " or ".join(checked) if checked else "any bound task or worktree report directory"
    _refuse(f"memory review verdictRef {ref!r} does not name an existing file at {where}.")


def _refuse(detail: str) -> None:
    raise ReviewHistoryError(MEMORY_VERDICT_REF_INVALID, f"{detail} {_ACCEPTED_FORMS}")


def collected_verdict_ref(
    raw: str,
    *,
    worktree_group: Path | None,
    task_root: Path | None,
) -> str:
    """Map a usable ``verdictRef`` to ``notes/reports/<rel>`` when the contract proves ``<rel>``."""

    if is_collected_report_ref(raw):
        rel = _relative_under_prefix(raw, COLLECTED_REPORT_PARTS)
        assert rel is not None
        return collected_report_ref(rel)
    for prefix in ((ROLE_REPORT_DIRECTORY,), (LEGACY_ROLE_REPORT_ALIAS,)):
        rel = _relative_under_prefix(raw, prefix)
        if rel is not None:
            return collected_report_ref(rel)
    supplied = Path(raw)
    if supplied.is_absolute():
        bases: list[Path] = []
        if worktree_group is not None:
            bases.append(worktree_group / ROLE_REPORT_DIRECTORY)
            bases.append(worktree_group / LEGACY_ROLE_REPORT_ALIAS)
        if task_root is not None:
            bases.append(task_root / COLLECTED_REPORT_DIRECTORY)
        for base in bases:
            rel = _exact_relative_to(supplied, base)
            if rel is not None:
                return collected_report_ref(rel)
    return raw


def _relative_under_prefix(reference: str, prefix: tuple[str, ...]) -> PurePosixPath | None:
    if Path(reference).is_absolute():
        return None
    parts = PurePosixPath(reference).parts
    if len(parts) <= len(prefix) or parts[: len(prefix)] != prefix or ".." in parts:
        return None
    return PurePosixPath(*parts[len(prefix) :])


def _confined_existing_file(base: Path, rel: PurePosixPath) -> bool:
    if ".." in rel.parts:
        return False
    resolved_base = base.resolve(strict=False)
    target = (base / rel).resolve(strict=False)
    return target.is_relative_to(resolved_base) and target.is_file()


def _exact_relative_to(path: Path, base: Path) -> PurePosixPath | None:
    for candidate, root in (
        (Path(os.path.normpath(path)), Path(os.path.normpath(base))),
        (path.resolve(strict=False), base.resolve(strict=False)),
    ):
        try:
            rel = candidate.relative_to(root)
        except ValueError:
            continue
        parts = PurePosixPath(rel.as_posix()).parts
        if parts and ".." not in parts:
            return PurePosixPath(*parts)
    return None
