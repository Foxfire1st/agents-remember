"""Bind review tree records to exact delivered code and memory trees (ICR-R21).

New writes use only MIK-R25's retained four-tree records. Historical generation JSON and v1
receipts remain readable for immutable source evidence; their missing memory-tree proof is explicit.
Receipts measure completed transactions and never gate closeout or integration.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

from pydantic import ValidationError

from agents_remember.application.review_comparison_generation import (
    ComparisonGenerationRef,
    generation_directories,
    read_generation_refs,
)
from agents_remember.application.review_tree_comparison import (
    comparison_directory,
    comparison_records,
)
from agents_remember.kernel.canonical_json import canonical_json_bytes, decoded_json
from agents_remember.memory.knowledge.durable_evidence import (
    DurableEvidencePublication,
    EvidenceReadBack,
    durable_reports_root,
    publish_durable_evidence,
    read_back_evidence,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review_final_output_receipt import (
    FINAL_OUTPUT_RECEIPT_VERSION,
    FINAL_OUTPUT_SELECTION_RULE,
    FinalOutputPhase,
    FinalOutputReceipt,
    LegacyFinalOutputReceipt,
    tree_comparison_digest,
    tree_match_state,
    tree_output_verdict,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.observer.events import now_iso
from agents_remember.worktrees import route_review
from agents_remember.worktrees.modules.git import require_git, worktree_candidate_tree
from agents_remember.worktrees.task_resolver import slugify
from agents_remember.worktrees.worktree_contract import WorktreeContract

FINAL_OUTPUT_RECEIPTS_PREFIX = "final-output"


@dataclass(frozen=True)
class ReviewComparisonSelection:
    state: Literal["selected", "no-comparison", "legacy-limit", "unreadable", "ambiguous"]
    leaf_id: str
    comparison: ReviewTreeComparisonRecord | None
    detail: str


def select_review_comparison(task_root: Path, leaf_id: str) -> ReviewComparisonSelection:
    """Select the highest retained tree-record number; never substitute a historical dataset."""

    directory = comparison_directory(task_root, leaf_id)
    records = comparison_records(task_root, leaf_id)
    paths = tuple(directory.glob("*.json")) if directory.is_dir() else ()
    if len(paths) != len(records) or any(
        record.task_id != task_root.name or record.leaf_id != leaf_id for record in records
    ):
        return ReviewComparisonSelection(
            "unreadable",
            leaf_id,
            None,
            "a retained tree comparison is unreadable or names a different task or leaf; no comparison is selected",
        )
    if records:
        latest = records[-1]
        tied = [record for record in records if record.number == latest.number]
        if len({tree_comparison_digest(record) for record in tied}) != 1:
            return ReviewComparisonSelection(
                "ambiguous",
                leaf_id,
                None,
                "different source records claim the highest tree comparison number; no comparison is selected",
            )
        return ReviewComparisonSelection(
            "selected",
            leaf_id,
            latest,
            f"tree comparison {latest.task_id}/{leaf_id}/{latest.number} has the highest recorded number",
        )
    if generation_directories(task_root, leaf_id):
        return ReviewComparisonSelection(
            "legacy-limit",
            leaf_id,
            None,
            "only historical dataset generations exist; they retain source identity but prove no exact memory candidate tree",
        )
    return ReviewComparisonSelection(
        "no-comparison", leaf_id, None, "no retained tree comparison exists for this leaf"
    )


def comparison_key(record: ReviewTreeComparisonRecord) -> str:
    return f"tree-{record.number}"


def source_comparison_matches(task_root: Path, record: ReviewTreeComparisonRecord) -> bool:
    """Check the complete embedded source record against the retained comparison, not its own seal."""

    return record.task_id == task_root.name and any(
        record == source for source in comparison_records(task_root, record.leaf_id)
    )


def require_comparison_repositories(
    contract: WorktreeContract, record: ReviewTreeComparisonRecord
) -> None:
    expected = (
        contract.code_repo_path,
        contract.code_repo_path,
        contract.memory_repo_path,
        contract.memory_repo_path,
    )
    actual = (record.code_base, record.code_candidate, record.memory_base, record.memory_candidate)
    if any(
        repository is None or Path(side.repository).resolve() != repository.resolve()
        for repository, side in zip(expected, actual, strict=True)
    ):
        raise ValueError(
            "the retained comparison repositories do not match this enclosure's code and memory repositories"
        )


@dataclass(frozen=True)
class FinalOutputReceiptPublication:
    publication: DurableEvidencePublication
    receipt: FinalOutputReceipt
    read_back: EvidenceReadBack


@dataclass(frozen=True)
class FinalOutputReceiptRead:
    state: Literal["recorded", "legacy-limit", "not-recorded", "unreadable"]
    leaf_id: str
    phase: FinalOutputPhase
    destination: Path
    receipt: FinalOutputReceipt | LegacyFinalOutputReceipt | None
    sha256: str | None
    superseded_by: tuple[ComparisonGenerationRef | ReviewTreeComparisonRecord, ...]
    detail: str


def receipt_file_name(leaf_id: str, generation_id: str, phase: FinalOutputPhase) -> str:
    return f"{FINAL_OUTPUT_RECEIPTS_PREFIX}-{slugify(leaf_id)}-{generation_id}-{phase}.json"


def record_final_output_receipt(
    contract: WorktreeContract,
    *,
    phase: FinalOutputPhase,
    code_commit: str,
    memory_content_commit: str,
) -> FinalOutputReceiptPublication:
    selection = select_review_comparison(contract.task_root, contract.leaf_id)
    if selection.comparison is None:
        raise RuntimeError(selection.detail)
    comparison = selection.comparison
    require_comparison_repositories(contract, comparison)
    code_tree = require_git(contract.code_repo_path, ["rev-parse", f"{code_commit}^{{tree}}"])
    memory_tree = (
        require_git(contract.memory_repo_path, ["rev-parse", f"{memory_content_commit}^{{tree}}"])
        if memory_content_commit and contract.memory_repo_path is not None
        else None
    )
    code_match = tree_match_state(comparison.code_candidate.tree, code_tree)
    memory_match = tree_match_state(comparison.memory_candidate.tree, memory_tree)
    receipt = FinalOutputReceipt(
        phase=phase,
        recorded_at=now_iso(),
        repository_id=contract.repo_name,
        master=contract.parent_task_name or contract.task_name,
        leaf_id=contract.leaf_id,
        task_root=str(contract.task_root),
        contract_path=str(contract.contract_path),
        comparison=comparison,
        comparison_digest=tree_comparison_digest(comparison),
        delivered_code_commit=code_commit,
        delivered_code_tree_id=code_tree,
        delivered_memory_content_commit=memory_content_commit if memory_tree else None,
        delivered_memory_tree_id=memory_tree,
        code_match=code_match,
        memory_match=memory_match,
        state=tree_output_verdict(code_match, memory_match),
    )
    publication = publish_durable_evidence(
        contract.task_root,
        receipt_file_name(contract.leaf_id, comparison_key(comparison), phase),
        canonical_json_bytes(receipt.model_dump(mode="json", by_alias=True)).decode("utf-8"),
    )
    return FinalOutputReceiptPublication(publication, receipt, read_back_evidence(publication))


def read_final_output_receipts(
    task_root: Path,
    leaf_id: str,
    generation_id: str,
    *,
    phases: tuple[FinalOutputPhase, ...] = ("closeout", "integration"),
) -> tuple[FinalOutputReceiptRead, ...]:
    return tuple(_read_receipt(task_root, leaf_id, generation_id, phase) for phase in phases)


def _read_receipt(
    task_root: Path, leaf_id: str, key: str, phase: FinalOutputPhase
) -> FinalOutputReceiptRead:
    destination = durable_reports_root(task_root) / receipt_file_name(leaf_id, key, phase)
    try:
        raw = destination.read_bytes()
    except FileNotFoundError:
        return FinalOutputReceiptRead(
            "not-recorded",
            leaf_id,
            phase,
            destination,
            None,
            None,
            (),
            f"no {phase} receipt is recorded at {destination}",
        )
    except OSError as error:
        return FinalOutputReceiptRead(
            "unreadable", leaf_id, phase, destination, None, None, (), str(error)
        )
    try:
        data = decoded_json(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("a receipt must be a JSON object")
        if data.get("receipt_version") == "ar-review-final-output-receipt/v1":
            legacy = LegacyFinalOutputReceipt.model_validate(data)
            if legacy.leaf_id != leaf_id or legacy.phase != phase or legacy.generation_id != key:
                raise ValueError("the historical receipt names another leaf, phase or generation")
            return FinalOutputReceiptRead(
                "legacy-limit",
                leaf_id,
                phase,
                destination,
                legacy,
                hashlib.sha256(raw).hexdigest(),
                _historical_successors(task_root, leaf_id, key),
                "historical v1 receipt retains source and dataset identities but proves no exact reviewed memory tree; tree coverage is unavailable",
            )
        receipt = FinalOutputReceipt.model_validate(data)
        if (
            receipt.leaf_id != leaf_id
            or receipt.phase != phase
            or comparison_key(receipt.comparison) != key
            or not source_comparison_matches(task_root, receipt.comparison)
        ):
            raise ValueError(
                "the receipt does not describe the retained source comparison at this location"
            )
    except (UnicodeDecodeError, ValueError, ValidationError) as error:
        return FinalOutputReceiptRead(
            "unreadable", leaf_id, phase, destination, None, None, (), str(error)
        )
    successors = tuple(
        record
        for record in comparison_records(task_root, leaf_id)
        if record.number > receipt.comparison.number
    )
    return FinalOutputReceiptRead(
        "recorded",
        leaf_id,
        phase,
        destination,
        receipt,
        hashlib.sha256(raw).hexdigest(),
        successors,
        receipt.statement(),
    )


def _historical_successors(
    task_root: Path, leaf_id: str, generation_id: str
) -> tuple[ComparisonGenerationRef, ...]:
    """Retain the existing JSON-only supersession history without claiming memory-tree coverage."""

    refs = read_generation_refs(task_root, leaf_id)
    selected = next((ref for ref in refs if ref.generation_id == generation_id), None)
    if selected is None:
        return ()
    return tuple(ref for ref in refs if ref.generation_index > selected.generation_index)


def final_output_selection_block(
    contract: WorktreeContract,
    prepared_candidate_tree: str | None,
    prepared_memory_tree: str | None = None,
) -> dict[str, Any]:
    if contract.kind != "leaf":
        return {
            "state": "not-applicable",
            "detail": "a series contract owns no leaf tree comparison",
        }
    selection = select_review_comparison(contract.task_root, contract.leaf_id)
    record = selection.comparison
    block: dict[str, Any] = {
        "selection_version": "ar-review-final-comparison-selection/v2",
        "state": selection.state,
        "leaf_id": contract.leaf_id,
        "selection_rule": FINAL_OUTPUT_SELECTION_RULE,
        "comparison": None if record is None else record.model_dump(mode="json", by_alias=True),
        "comparison_digest": None if record is None else tree_comparison_digest(record),
        "prepared_candidate_code_tree_id": prepared_candidate_tree,
        "prepared_candidate_memory_tree_id": prepared_memory_tree,
        "prepared_code_is_reviewed_candidate": None,
        "prepared_memory_is_reviewed_candidate": None,
        "prepared_is_reviewed_candidate": None,
        "detail": selection.detail,
    }
    if record is None:
        return block
    try:
        require_comparison_repositories(contract, record)
    except ValueError as error:
        block.update(state="unreadable", detail=str(error))
        return block
    code = (
        None
        if prepared_candidate_tree is None
        else record.code_candidate.tree == prepared_candidate_tree
    )
    memory = (
        None
        if prepared_memory_tree is None
        else record.memory_candidate.tree == prepared_memory_tree
    )
    block.update(
        prepared_code_is_reviewed_candidate=code,
        prepared_memory_is_reviewed_candidate=memory,
        prepared_is_reviewed_candidate=False
        if False in (code, memory)
        else (True if code is True and memory is True else None),
    )
    return block


def attach_prepared_selection(
    payload: dict[str, Any], contract: WorktreeContract
) -> dict[str, Any]:
    if not payload.get("ok"):
        return payload
    prepared = route_review.code_candidate_tree(contract) if contract.kind == "leaf" else None
    memory = None
    memory_detail = "no memory candidate was captured"
    if contract.kind == "leaf" and contract.memory_worktree is not None:
        try:
            with TemporaryDirectory(prefix="ar-final-memory-") as scratch:
                memory = worktree_candidate_tree(contract.memory_worktree, Path(scratch) / "index")
                memory_detail = (
                    "the complete memory candidate was captured through an isolated index"
                )
        except (OSError, RuntimeError) as error:
            memory_detail = f"the prepared memory candidate could not be captured: {error}"
    payload["final_comparison_selection"] = final_output_selection_block(contract, prepared, memory)
    payload["final_comparison_selection"]["prepared_memory_detail"] = memory_detail
    return payload


def attach_closeout_receipt(payload: dict[str, Any], contract: WorktreeContract) -> dict[str, Any]:
    if payload.get("ok") and payload.get("code_commit"):
        payload["final_output_receipt"] = final_output_result_block(
            contract,
            phase="closeout",
            code_commit=str(payload["code_commit"]),
            memory_content_commit=str(payload.get("memory_content_commit") or ""),
        )
    return payload


def attach_integration_receipt(
    payload: dict[str, Any], contract: WorktreeContract
) -> dict[str, Any]:
    if payload.get("ok") and payload.get("integrated_code_commit"):
        payload["final_output_receipt"] = final_output_result_block(
            contract,
            phase="integration",
            code_commit=str(payload["integrated_code_commit"]),
            memory_content_commit=str(payload.get("integrated_memory_content_commit") or ""),
        )
    return payload


def final_output_result_block(
    contract: WorktreeContract,
    *,
    phase: FinalOutputPhase,
    code_commit: str,
    memory_content_commit: str,
) -> dict[str, Any]:
    if contract.kind != "leaf":
        return {
            "receipt_version": FINAL_OUTPUT_RECEIPT_VERSION,
            "state": "not-applicable",
            "phase": phase,
            "detail": "a series contract owns no leaf tree comparison",
        }
    selection = select_review_comparison(contract.task_root, contract.leaf_id)
    if selection.comparison is None:
        return {
            "receipt_version": FINAL_OUTPUT_RECEIPT_VERSION,
            "state": "not-recorded",
            "phase": phase,
            "leaf_id": contract.leaf_id,
            "selection_state": selection.state,
            "detail": selection.detail,
        }
    try:
        recorded = record_final_output_receipt(
            contract,
            phase=phase,
            code_commit=code_commit,
            memory_content_commit=memory_content_commit,
        )
    except (KnowledgeStorageError, ValidationError, OSError, RuntimeError, ValueError) as error:
        return {
            "receipt_version": FINAL_OUTPUT_RECEIPT_VERSION,
            "state": "not-recorded",
            "phase": phase,
            "leaf_id": contract.leaf_id,
            "detail": str(error),
        }
    block = recorded.receipt.model_dump(mode="json", by_alias=True)
    block.update(
        state="recorded",
        receipt_state=recorded.receipt.state,
        statement=recorded.receipt.statement(),
        destination=str(recorded.publication.destination),
        sha256=recorded.publication.sha256,
        replaced_existing_receipt=recorded.publication.replaced_existing,
        read_back=recorded.read_back.state,
    )
    return block
