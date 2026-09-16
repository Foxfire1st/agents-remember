"""Leaf task-document lookup and lifecycle stamping (task domain).

A leaf's JSON-primary task document is the lifecycle-keyed work content of its
enclosure (task_doc stamps ``lifecycleId`` at create time when authored against
a leaf contract). Reopen (L11) makes the binding survive restarts too: the doc
follows the enclosure across lifecycles by explicit restamp, never by read-time
heuristics. The lookup mirrors the observer projection's exact joins — doc id,
``enclosures[]`` refs, then file stem, all case-insensitive (doc ids are
authored labels like ``260628-L11`` while enclosure leaf ids are lowercase
directory names like ``260628-l11``).

Planning a master necessarily authors its leaf documents *before* the master's
first start bootstraps the series contract, so at authoring time the two derived
fields ``seriesContractPath`` and ``enclosures[]`` cannot exist yet and
``task_doc`` deliberately leaves them empty for that documented case. Start is
therefore the moment they become writable, and both planning paths here bind
them: :func:`plan_leaf_doc_lifecycle_restamp` (the start/reopen restamp) and
:func:`plan_leaf_doc_enclosure_registration` (the start/attach publisher). A
document missing only its master link is consequently a candidate for a write,
not a no-op — that omission is the defect this binding exists to close.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.readiness import CompletionBlocker, completion_blockers
from agents_remember.tasks.store import read_task_doc
from agents_remember.tasks.task_paths import (
    leaf_enclosure_path,
    series_contract_path,
)


class TerminalLeafResolutionError(ValueError):
    """A terminal writer could not prove one exact contract-bound leaf document."""


class LeafLifecycleRestampBlocked(ValueError):
    """A lifecycle restamp would republish a false terminal leaf claim."""

    def __init__(self, plan: LeafLifecycleRestampPlan) -> None:
        self.plan = plan
        exact = [blocker.model_dump() for blocker in plan.blockers]
        super().__init__(f"leaf lifecycle restamp refused; unresolved work units: {exact!r}")


@dataclass(frozen=True)
class LeafLifecycleRestampPlan:
    """A read-only lifecycle-restamp decision, safe to compute before start effects."""

    doc_path: Path | None
    lifecycle_id: str
    candidate: TaskDocument | None
    changed: bool
    blockers: tuple[CompletionBlocker, ...] = ()


@dataclass(frozen=True)
class LeafEnclosureRegistrationPlan:
    """Plan the canonical leaf task-document enclosure registration.

    The worktree contract is the address authority.  A start or reattachment may
    repair a missing/stale document reference, but it must prepare the candidate
    through the same JSON-primary task writer used by ordinary task-document edits.
    """

    doc_path: Path
    leaf_id: str
    enclosure_path: str
    lifecycle_id: str | None
    candidate: TaskDocument | None
    state: str
    blockers: tuple[CompletionBlocker, ...] = ()


def _matches_leaf_identity(json_path: Path, doc: TaskDocument, leaf_id: str) -> bool:
    want = leaf_id.strip().lower()
    return (
        (doc.id or "").strip().lower() == want
        or any((ref.leafId or "").strip().lower() == want for ref in doc.enclosures)
        or json_path.stem.strip().lower() == want
    )


def find_leaf_doc(task_root: Path, leaf_id: str) -> tuple[Path, TaskDocument] | None:
    """The leaf's task document under ``task_root``, or None when never authored."""
    want = leaf_id.strip().lower()
    if not want:
        return None
    for json_path in sorted(task_root.glob("*.json")):
        try:
            doc = read_task_doc(json_path)
        except Exception:
            continue
        if doc.kind == "master":
            continue
        if _matches_leaf_identity(json_path, doc, want):
            return json_path, doc
    return None


def resolve_terminal_leaf_doc(
    task_root: Path,
    leaf_id: str,
    *,
    asserted_path: Path | None = None,
) -> tuple[Path, TaskDocument] | None:
    """Resolve exactly one leaf for a terminal write, failing closed on identity doubt.

    Start/reopen deliberately retain :func:`find_leaf_doc`'s fail-soft behavior. A
    terminal transition has a stronger contract: an asserted path is an identity
    assertion and duplicate matches are ambiguous. Unrelated sibling documents are
    normal in a series root and do not prevent a true no-document result for this leaf.
    """
    root = task_root.resolve(strict=False)
    want = leaf_id.strip()
    if not want:
        raise TerminalLeafResolutionError(
            "terminal leaf resolution requires a nonblank leaf id: the leaf has no stamped "
            "contract binding -- re-stamp the series contract (series-contract.md) or use "
            "branch-addressed mode for direct execution"
        )

    asserted = _terminal_asserted_path(root, asserted_path)
    matches = _terminal_matches(root, want, asserted)
    if len(matches) > 1:
        paths = ", ".join(path.as_posix() for path, _doc in matches)
        raise TerminalLeafResolutionError(
            f"leaf identity {leaf_id!r} is ambiguous across terminal task documents: {paths}"
        )
    if asserted is not None:
        _assert_terminal_path(asserted, want, matches)
    return matches[0] if matches else None


def _terminal_asserted_path(root: Path, asserted_path: Path | None) -> Path | None:
    if asserted_path is None:
        return None
    asserted = asserted_path.resolve(strict=False)
    if asserted.parent != root or asserted.suffix != ".json":
        raise TerminalLeafResolutionError(
            f"asserted task document must be a direct JSON child of {root}: {asserted}"
        )
    if not asserted.exists():
        raise TerminalLeafResolutionError(f"asserted task document does not exist: {asserted}")
    return asserted


def _terminal_matches(
    root: Path,
    leaf_id: str,
    asserted: Path | None,
) -> list[tuple[Path, TaskDocument]]:
    matches: list[tuple[Path, TaskDocument]] = []
    for json_path in sorted(root.glob("*.json")):
        resolved_path = json_path.resolve(strict=False)
        try:
            doc = read_task_doc(json_path)
        except (OSError, ValueError) as exc:
            if resolved_path == asserted or json_path.stem.strip().lower() == leaf_id.lower():
                raise TerminalLeafResolutionError(
                    f"cannot read terminal leaf candidate {resolved_path}: {exc}"
                ) from exc
            continue
        if doc.kind == "master":
            continue
        if _matches_leaf_identity(json_path, doc, leaf_id):
            matches.append((resolved_path, doc))
    return matches


def _assert_terminal_path(
    asserted: Path,
    leaf_id: str,
    matches: list[tuple[Path, TaskDocument]],
) -> None:
    try:
        asserted_doc = read_task_doc(asserted)
    except (OSError, ValueError) as exc:
        raise TerminalLeafResolutionError(
            f"cannot read asserted task document {asserted}: {exc}"
        ) from exc
    if asserted_doc.kind == "master" or not _matches_leaf_identity(asserted, asserted_doc, leaf_id):
        raise TerminalLeafResolutionError(
            f"asserted task document {asserted} is not bound to contract leaf {leaf_id!r}"
        )
    if not matches or matches[0][0] != asserted:
        resolved = matches[0][0] if matches else None
        raise TerminalLeafResolutionError(
            f"asserted task document {asserted} does not equal contract-bound leaf {resolved}"
        )


def _derived_bindings(
    task_root: Path,
    leaf_id: str,
    *,
    series_contract_path_missing: bool,
    enclosures_missing: bool,
) -> dict[str, object]:
    """The derived master-link fields this leaf doc is missing, or an empty mapping.

    ``leaf_id`` follows the same rule as the rest of this module: the enclosure
    directory is derived from it, and only a nonblank id names an enclosure.
    """

    bindings: dict[str, object] = {}
    if series_contract_path_missing:
        bindings["seriesContractPath"] = series_contract_path(task_root).as_posix()
    if enclosures_missing and leaf_id.strip():
        bindings["enclosures"] = [
            {
                "leafId": leaf_id,
                "enclosurePath": leaf_enclosure_path(task_root, leaf_id).as_posix(),
            }
        ]
    return bindings


def _derived_leaf_bindings(
    task_root: Path, leaf_id: str, document: TaskDocument
) -> dict[str, object]:
    """Bind only what is absent, so an existing binding is never rewired here."""

    return _derived_bindings(
        task_root,
        leaf_id,
        series_contract_path_missing=not document.seriesContractPath,
        enclosures_missing=not document.enclosures,
    )


def plan_leaf_doc_lifecycle_restamp(
    task_root: Path, leaf_id: str, lifecycle_id: str
) -> LeafLifecycleRestampPlan:
    """Plan the start/reopen write and expose terminal blockers without mutating bytes.

    The candidate carries every derived field the document is missing as well as
    the lifecycle binding: a doc whose ``lifecycleId`` already matches is still a
    candidate when its master link is absent (that is a leaf authored before the
    series contract existed), and no candidate means nothing was missing.
    """
    found = find_leaf_doc(task_root, leaf_id)
    if found is None:
        return LeafLifecycleRestampPlan(None, lifecycle_id, None, False)
    json_path, doc = found
    changed = doc.lifecycleId != lifecycle_id
    bindings = _derived_leaf_bindings(task_root, leaf_id, doc)
    if not changed and not bindings:
        return LeafLifecycleRestampPlan(json_path, lifecycle_id, None, False)
    data = doc.model_dump(by_alias=True)
    data["lifecycleId"] = lifecycle_id
    data.update(bindings)
    updated = TaskDocument.model_validate(data)
    blockers = tuple(completion_blockers(updated)) if updated.status == "Completed" else ()
    return LeafLifecycleRestampPlan(json_path, lifecycle_id, updated, True, blockers)


def restamp_leaf_doc_lifecycle(
    task_root: Path,
    leaf_id: str,
    lifecycle_id: str,
    *,
    publish: Callable[[Path, TaskDocument], object],
) -> dict | None:
    """Point the leaf's doc at ``lifecycle_id`` (the enclosure's current lifecycle).

    Overwrites any previous lifecycle stamp: the enclosure's newest lifecycle IS
    the doc's binding — a reopened leaf's doc must follow the fresh lifecycle,
    not the finalized one. The same write also binds the derived master link
    (``seriesContractPath``, ``enclosures[]``) when it is absent, because a first
    start finds the doc authored *before* the series contract existed — the only
    moment at which those fields could not be stamped. Returns a small report
    dict, or None when the leaf has no doc yet (a first start against an
    unstarted leaf authors the doc afterwards, already stamped by task_doc).
    """
    plan = plan_leaf_doc_lifecycle_restamp(task_root, leaf_id, lifecycle_id)
    if plan.blockers:
        raise LeafLifecycleRestampBlocked(plan)
    if plan.doc_path is None:
        return None
    if plan.candidate is not None:
        publish(task_root, plan.candidate)
    return {
        "docPath": plan.doc_path.as_posix(),
        "lifecycleId": lifecycle_id,
        "changed": plan.changed,
    }


def _enclosure_registration_candidate(
    document: TaskDocument,
    leaf_id: str,
    expected_path: str,
    missing_link: dict[str, object],
    lifecycle_id: str | None,
) -> TaskDocument:
    """One full-document candidate: the exact enclosure address, its derived link, the stamp."""

    data = document.model_dump(by_alias=True)
    data["enclosures"] = [{"leafId": leaf_id, "enclosurePath": expected_path}]
    data.update(missing_link)
    if lifecycle_id is not None:
        data["lifecycleId"] = lifecycle_id
    return TaskDocument.model_validate(data)


def _enclosure_registration_state(
    document: TaskDocument,
    *,
    address_exact: bool,
    lifecycle_id: str | None,
) -> str:
    """Which repair this candidate is, in the order that matters to the caller.

    An exact address with only the lifecycle moved is a reopened leaf following its
    fresh lifecycle; an exact address with the derived master link absent is the
    start binding that link; anything else is a missing or mismatched enclosure.
    """

    if address_exact:
        if lifecycle_id is not None and document.lifecycleId != lifecycle_id:
            return "lifecycle-mismatch"
        return "master-link-missing"
    return "mismatched" if document.enclosures else "missing"


def plan_leaf_doc_enclosure_registration(
    doc_path: Path,
    leaf_id: str,
    enclosure_path: Path,
    *,
    lifecycle_id: str | None = None,
) -> LeafEnclosureRegistrationPlan:
    """Plan an exact leaf/enclosure binding and optional lifecycle restamp.

    ``doc_path`` is resolved by the canonical parent-row resolver.  This function
    therefore never searches for a plausible sibling document and never mutates
    the task source.  An already exact binding with the requested lifecycle *and*
    its derived master link is a no-op; every other state produces one
    full-document candidate for the normal task publication writer.
    """

    path = doc_path.resolve(strict=False)
    document = read_task_doc(path)
    expected_path = enclosure_path.resolve(strict=False).as_posix()
    address_exact = (
        len(document.enclosures) == 1
        and document.enclosures[0].leafId == leaf_id
        and _same_path(document.enclosures[0].enclosurePath, expected_path)
    )
    missing_link = _derived_leaf_bindings(path.parent, leaf_id, document)
    if (
        address_exact
        and not missing_link
        and (lifecycle_id is None or document.lifecycleId == lifecycle_id)
    ):
        return LeafEnclosureRegistrationPlan(
            path,
            leaf_id,
            expected_path,
            lifecycle_id,
            None,
            "present",
        )

    candidate = _enclosure_registration_candidate(
        document,
        leaf_id,
        expected_path,
        missing_link,
        lifecycle_id,
    )
    blockers = tuple(completion_blockers(candidate)) if candidate.status == "Completed" else ()
    return LeafEnclosureRegistrationPlan(
        path,
        leaf_id,
        expected_path,
        lifecycle_id,
        candidate,
        _enclosure_registration_state(
            document,
            address_exact=address_exact,
            lifecycle_id=lifecycle_id,
        ),
        blockers,
    )


def _same_path(value: str, expected: str) -> bool:
    """Compare persisted and contract paths without accepting relative aliases."""

    try:
        return Path(value).resolve(strict=False).as_posix() == expected
    except (OSError, RuntimeError, ValueError):
        return False
