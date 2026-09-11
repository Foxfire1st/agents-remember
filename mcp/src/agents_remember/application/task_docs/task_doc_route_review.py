"""Route-review binding machinery for ``task_doc`` (facade-extracted).

The call-level knobs (``TaskDocCall``), the policy gate for branch-addressed
direct execution (``_enforce_branch_addressed_policy``), the leaf/series
contract binding behind ``record_route_review`` (``_RouteReviewBinding``,
``_record_route_review_bound``, ``_require_route_review_binding``), and the
route-review authority rule (``_enforce_route_review_authority``). Extracted
from ``task_doc_tools.py`` so the facade stays under the file-size cap; the
facade re-exports the names its callers import (same pattern as
``task_reopen.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks import TaskDocument
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.leaf_doc import (
    TerminalLeafResolutionError,
    resolve_terminal_leaf_doc,
)
from agents_remember.worktrees.review_history import (
    ReviewHistoryError,
    begin_task_review,
    record_task_review,
)
from agents_remember.worktrees.route_review import (
    RouteReviewError,
    build_route_review,
    document_ref,
)
from agents_remember.worktrees.route_review_scope import (
    build_master_route_review,
    reject_atomic_child_route_review,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


class TaskDocError(AgentsRememberError):
    """Raised when a task-document operation cannot be completed."""


@dataclass(frozen=True)
class TaskDocCall:
    """Call-level knobs that are not part of the edit.

    ``branch_addressed`` opts into the policy-gated series-contract binding for
    ``record_route_review`` under sanctioned direct execution.
    """

    dry_run: bool = False
    branch_addressed: bool = False


DEFAULT_TASK_DOC_CALL = TaskDocCall()
"""The ordinary call: a real mutation, worktree-contract binding."""


def _enforce_branch_addressed_policy(
    config: McpRuntimeConfig, operation: str, branch_addressed: bool
) -> None:
    """Refuse a branch-addressed call the policy does not sanction."""
    if not branch_addressed:
        return
    if operation != "record_route_review":
        raise TaskDocError(
            "branch_addressed mode is only defined for record_route_review; "
            f"operation {operation!r} resolves its own contract binding"
        )
    if not config.direct_execution_enabled:
        raise TaskDocError(
            "branch_addressed mode is disabled by policy; enable directExecutionEnabled "
            "in the MCP authority settings for sanctioned direct execution"
        )


@dataclass(frozen=True)
class _RouteReviewBinding:
    """How a route-review call binds its leaf: worktree contract or series branch.

    ``branch_addressed`` opts into the policy-gated series-contract binding used
    by sanctioned direct execution (no leaf worktree); the selected document is
    then the leaf identity itself.
    """

    contract: WorktreeContract | None
    task_root: Path
    selected_path: Path
    branch_addressed: bool = False


def _record_route_review(
    doc: TaskDocument,
    payload: dict[str, Any] | None,
    contract: WorktreeContract | None,
    task_root: Path,
    selected_path: Path,
) -> TaskDocument:
    """Legacy positional contract (kept): bind a leaf worktree contract review.

    ``task_doc_tool`` records through the binding form
    (``_record_route_review_bound``) so branch_addressed direct execution stays
    available; this positional form preserves the pre-wave-2 call shape and its
    error dialect.
    """
    return _record_route_review_bound(
        doc,
        payload,
        _RouteReviewBinding(
            contract=contract,
            task_root=task_root,
            selected_path=selected_path,
        ),
    )


def _record_route_review_bound(
    doc: TaskDocument,
    payload: dict[str, Any] | None,
    binding: _RouteReviewBinding,
) -> TaskDocument:
    if payload is None:
        raise TaskDocError("record_route_review requires a review object")
    if doc.kind == "master":
        _require_master_route_review_binding(binding)
        contract = binding.contract
        assert contract is not None
        try:
            review = build_master_route_review(
                contract,
                ResolvedTaskDocument(
                    ref=document_ref(contract, binding.selected_path),
                    path=binding.selected_path,
                    document=doc,
                ),
                _route_payload(payload),
            )
        except (RouteReviewError, ValidationError) as exc:
            raise TaskDocError(str(exc)) from exc
        return _with_route_review_state(doc, payload, review)
    _require_route_review_binding(binding)
    contract = binding.contract
    assert contract is not None  # _require_route_review_binding proves the binding
    try:
        reject_atomic_child_route_review(contract)
    except RouteReviewError as exc:
        raise TaskDocError(str(exc)) from exc
    try:
        review = build_route_review(
            contract,
            ResolvedTaskDocument(
                ref=document_ref(contract, binding.selected_path),
                path=binding.selected_path,
                document=doc,
            ),
            _route_payload(payload),
            branch_addressed=binding.branch_addressed,
        )
    except (RouteReviewError, ValidationError) as exc:
        raise TaskDocError(str(exc)) from exc
    return _with_route_review_state(doc, payload, review)


def _begin_task_review_bound(
    doc: TaskDocument,
    payload: dict[str, Any] | None,
    binding: _RouteReviewBinding | None = None,
) -> TaskDocument:
    """Start or resume the one pending review round owned by the task document."""

    del binding
    try:
        return begin_task_review(doc, payload)
    except ReviewHistoryError as exc:
        raise TaskDocError(f"{exc.status}: {exc}") from exc


def _record_task_review_bound(
    doc: TaskDocument,
    payload: dict[str, Any] | None,
    binding: _RouteReviewBinding | None = None,
) -> TaskDocument:
    """Record generic review state without authoring code-route evidence."""

    del binding
    if payload is None:
        raise TaskDocError("record_review requires a review object")
    try:
        return record_task_review(doc, payload)
    except ReviewHistoryError as exc:
        raise TaskDocError(f"{exc.status}: {exc}") from exc


def _state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep route evidence out of the shared, generic state transition."""

    return {
        key: payload[key]
        for key in ("verdict", "verdictRef", "findings", "remainingFindingIds")
        if key in payload
    }


def _route_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep fixed-list state fields out of the existing route-review schema."""

    return {
        key: value
        for key, value in payload.items()
        if key not in {"findings", "remainingFindingIds"}
    }


def _with_route_review_state(
    doc: TaskDocument,
    payload: dict[str, Any],
    review: Any,
) -> TaskDocument:
    try:
        updated = record_task_review(doc, _state_payload(payload))
    except ReviewHistoryError as exc:
        raise TaskDocError(f"{exc.status}: {exc}") from exc
    data = updated.model_dump(by_alias=True)
    data["routeReview"] = review.model_dump(mode="json")
    return _validate(data)


def _require_route_review_binding(binding: _RouteReviewBinding) -> None:
    """Refuse a route-review binding that is not exact for its mode."""
    contract = binding.contract
    if contract is None:
        raise TaskDocError(
            "record_route_review requires a contract binding; no leaf worktree "
            f"contract exists for {binding.task_root} -- re-stamp the series "
            "contract (series-contract.md) or use branch_addressed=true for direct "
            "execution"
        )
    if binding.branch_addressed:
        if contract.kind != "series":
            raise TaskDocError(
                "record_route_review branch_addressed mode requires the task-root "
                f"series contract; {contract.contract_path} is a {contract.kind} contract"
            )
        if not binding.selected_path.resolve().is_relative_to(binding.task_root.resolve()):
            raise TaskDocError(
                "record_route_review branch_addressed target is outside the task root"
            )
        return
    if contract.kind != "leaf":
        raise TaskDocError(
            "record_route_review requires the leaf worktree contract; "
            f"{contract.contract_path} is a {contract.kind} contract -- pass the "
            "leaf enclosure contract, or use branch_addressed=true to bind the "
            "task-root series contract for direct execution"
        )
    try:
        resolved = resolve_terminal_leaf_doc(
            binding.task_root,
            contract.leaf_id,
            asserted_path=binding.selected_path,
        )
    except TerminalLeafResolutionError as exc:
        raise TaskDocError(str(exc)) from exc
    if resolved is None or resolved[0].resolve() != binding.selected_path.resolve():
        raise TaskDocError(
            "record_route_review target is not the exact task document bound to the leaf contract"
        )


def _require_master_route_review_binding(binding: _RouteReviewBinding) -> None:
    """Bind master review publication to the canonical series task root."""

    contract = binding.contract
    if contract is None or contract.kind != "series":
        raise TaskDocError("atomic-master route review requires the canonical series contract")
    expected_root = contract.task_root.resolve()
    if binding.task_root.resolve() != expected_root:
        raise TaskDocError(
            "master route review task root does not match the canonical series contract"
        )
    expected_path = expected_root / "task.json"
    if binding.selected_path.resolve() != expected_path:
        raise TaskDocError("master route review must target the canonical task-root task.json")


def _enforce_route_review_authority(
    operation: str,
    original: TaskDocument | None,
    candidate: TaskDocument,
) -> None:
    candidate_review = candidate.routeReview.model_dump_json() if candidate.routeReview else None
    original_review = (
        original.routeReview.model_dump_json()
        if original is not None and original.routeReview is not None
        else None
    )
    if operation == "create" and candidate_review is not None:
        raise TaskDocError(
            "create cannot author route-review evidence; use task_doc.record_route_review"
        )
    if operation == "replace" and candidate_review != original_review:
        raise TaskDocError(
            "replace cannot add, remove, or change route-review evidence; "
            "use task_doc.record_route_review"
        )


def _validate(data: dict[str, Any]) -> TaskDocument:
    try:
        return TaskDocument.model_validate(data)
    except ValidationError as exc:
        raise TaskDocError(f"invalid task document: {exc}") from exc
