"""Explicitly record a live code-only task against its unchanged published knowledge."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import apsw

from agents_remember.application.knowledge_baseline_generation import (
    BaselineRun,
    CapturedBaseline,
    fill_admitted_before_half,
    read_standing_generation,
)
from agents_remember.application.knowledge_before_half import (
    read_baseline_origin,
    read_dataset_identity,
    unreadable_half_refusal,
)
from agents_remember.application.knowledge_composition import open_read_only_store
from agents_remember.application.knowledge_curator_ingest import prepare_curator_candidate
from agents_remember.application.knowledge_publication_route import declared_publication_location
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    candidate_receipt_refusal,
    refusal,
    resolve_review_candidate,
)
from agents_remember.application.review_comparison_freeze import (
    ComparisonFreezeOptions,
    ComparisonGenerationFreeze,
    freeze_resolved_review,
)
from agents_remember.application.review_recorded_knowledge import read_memory_knowledge
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.closed_snapshot import freeze_closed_snapshot
from agents_remember.memory.knowledge.refusals import KnowledgeRefused, KnowledgeStorageError
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.review import ReviewRefusal, ReviewSurfaceRequest


def freeze_unchanged_knowledge_review(
    config: McpRuntimeConfig, request: ReviewSurfaceRequest, options: ComparisonFreezeOptions
) -> ComparisonGenerationFreeze:
    """Verify the code-only claim before freezing; never author or publish knowledge rows."""

    if options.historical_absence:
        return _refused("unchanged knowledge and historical absence cannot select the same pair")
    resolved = resolve_review_candidate(
        config, request.repository_id, request.master, request.leaf_id
    )
    if isinstance(resolved, ReviewRefusal):
        return ComparisonGenerationFreeze(state="refused", refusal=resolved)
    contract = resolved.contract
    if (
        contract is None
        or resolved.closed_leaf is not None
        or contract.memory_repo_path is None
        or not contract.memory_base_commit
    ):
        return _refused("unchanged knowledge requires a live task with a recorded memory base")
    with TemporaryDirectory(prefix="ar-review-unchanged-") as temporary:
        base = read_memory_knowledge(
            contract.memory_repo_path, contract.memory_base_commit, "before", Path(temporary)
        )
        if base.state != "available" or base.identity is None or base.path is None:
            return _refused(base.detail)
        return _prepare_and_record(resolved, request, options, base.identity, Path(temporary))


def _prepare_and_record(
    resolved: ReviewCandidateResolution,
    request: ReviewSurfaceRequest,
    options: ComparisonFreezeOptions,
    expected: SnapshotIdentity,
    temporary: Path,
) -> ComparisonGenerationFreeze:
    contract = resolved.contract
    assert contract is not None
    try:
        published = declared_publication_location(contract).path
        issue = _unchanged_inputs(resolved, published, expected)
        if issue is not None:
            return _refused(issue)
        after = temporary / "after.sqlite"
        with open_read_only_store(published, expected.repository_id) as store:
            freeze_closed_snapshot(store, expected, after)
        issue = _place_pair(resolved, after, published) or _unchanged_inputs(
            resolved, published, expected
        )
        if issue is not None:
            return _refused(issue)
        pair = replace(resolved, repository_id=expected.repository_id)
        return freeze_resolved_review(pair, request, options)
    except (KnowledgeRefused, KnowledgeStorageError, ValueError, OSError, apsw.Error) as error:
        return _refused(str(error))


def _unchanged_inputs(
    resolved: ReviewCandidateResolution, published: Path, expected: SnapshotIdentity
) -> str | None:
    """The published file and any authored task halves must all agree with the recorded base."""

    standing = read_standing_generation(resolved.baseline_database.parent)
    if standing.state == "damaged":
        return standing.detail
    origin = read_baseline_origin(resolved.baseline_database.parent)
    if origin is not None and not resolved.baseline_database.is_file():
        return "the recorded baseline origin has no dataset beside it"
    receipt = unreadable_half_refusal(
        resolved.baseline_database, resolved.candidate_database
    ) or candidate_receipt_refusal(resolved)
    if receipt is not None:
        return receipt.detail
    paths = [published]
    paths.extend(
        path for path in (resolved.baseline_database, resolved.candidate_database) if path.exists()
    )
    for path in paths:
        observed = read_dataset_identity(path)
        if isinstance(observed, str):
            return observed
        if observed != expected:
            return (
                f"{path} differs from the exact recorded memory base: expected "
                f"{expected.model_dump_json()}, observed {observed.model_dump_json()}. "
                "The task may contain unpublished or deliberately changed knowledge"
            )
    return None


def _place_pair(resolved: ReviewCandidateResolution, captured: Path, published: Path) -> str | None:
    """Keep normal candidate admission and the original-baseline owner in charge of placement."""

    contract = resolved.contract
    assert contract is not None
    candidate = prepare_curator_candidate(contract, resolved.candidate_database.parent, captured)
    if candidate.state == "refused" or candidate.identity is None:
        return "candidate preparation refused: " + str(candidate.refusal)
    placement = fill_admitted_before_half(
        half=resolved.baseline_database.parent,
        candidate_directory=resolved.candidate_database.parent,
        captured=CapturedBaseline(origin=published, payload=captured.read_bytes()),
        run=BaselineRun(
            leaf_id=contract.leaf_id,
            contract_path=str(contract.contract_path),
            authorization_ref=str(contract.contract_path),
            code_base_commit=contract.code_base_commit,
        ),
        rebase=False,
    )
    if placement.startswith("not-placed:"):
        return placement
    return None


def _refused(detail: str) -> ComparisonGenerationFreeze:
    return ComparisonGenerationFreeze(
        state="refused",
        refusal=refusal(
            "comparison_refused",
            f"unchanged knowledge was not recorded: {detail}",
            next_action=(
                "reconcile and publish the task's knowledge through the ordinary curator ingest, "
                "then run review-record-comparison without --unchanged-knowledge; use this option "
                "only when the published dataset and any task candidate match the recorded memory base"
            ),
            offending_input="knowledge",
        ),
    )
