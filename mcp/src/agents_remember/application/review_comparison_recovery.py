"""Explicit successor recovery from a named retained comparison and original curator generation."""

from __future__ import annotations

from dataclasses import replace

from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    refusal,
)
from agents_remember.application.review_committed_leaf import resolve_committed_leaf_review
from agents_remember.application.review_comparison_freeze import (
    ComparisonFreezeOptions,
    ComparisonGenerationFreeze,
    freeze_resolved_review,
)
from agents_remember.application.review_curator_records import RESERVED_CURATOR_OWNERS
from agents_remember.application.review_evidence_records import review_records_for_resolution
from agents_remember.errors import CuratorCoherenceError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import ReviewRefusal, ReviewSurfaceRequest
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    load_curator_coherence_generation,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


def recover_review_comparison(
    config: McpRuntimeConfig,
    contract: WorktreeContract,
    generation_id: str,
    curator_record_digest: str,
    options: ComparisonFreezeOptions,
) -> ComparisonGenerationFreeze:
    """Publish a successor; never mutate its parent or turn its retained capture into a live one."""

    if options.historical_absence or options.records is not None or options.retain_parent_inputs:
        return _refused(
            "recovery selects its exact retained inputs; conflicting live/record inputs were supplied"
        )
    master = contract.parent_task_name or contract.task_name
    resolved = resolve_committed_leaf_review(
        config, contract.repo_name, master, contract.leaf_id, generation_id=generation_id
    )
    if isinstance(resolved, ReviewRefusal):
        return ComparisonGenerationFreeze(state="refused", refusal=resolved)
    issue = _parent_issue(resolved, contract, generation_id)
    if issue is not None:
        return _refused(issue)
    closed = resolved.closed_leaf
    assert (
        closed is not None
        and closed.manifest is not None
        and closed.reopened.generation is not None
    )
    parent, manifest = closed.reopened.generation, closed.manifest
    try:
        original = load_curator_coherence_generation(contract, curator_record_digest)
        if (
            original.record.codeCandidateTree != manifest.source.candidate_code_tree_id
            or original.record.pairIdentity.codeBaseCommit != manifest.source.baseline_code_tree_id
            or original.record.pairIdentity.memoryBaseCommit != contract.memory_base_commit
            or original.record.pairIdentity.repoId != contract.repo_name
        ):
            return _refused(
                "the curator generation does not bind this parent's original source and repository line"
            )
        records = review_records_for_resolution(
            resolved, curator_record_digest=curator_record_digest
        )
    except (CuratorCoherenceError, ValueError, OSError) as error:
        return _refused(f"the original curator generation could not be validated: {error}")
    request = ReviewSurfaceRequest(
        repository_id=contract.repo_name,
        master=master,
        leaf_id=contract.leaf_id,
        history="recorded",
    )
    return freeze_resolved_review(
        resolved,
        request,
        replace(options, records=records, parent=parent, retain_parent_inputs=True),
    )


def _parent_issue(
    resolved: ReviewCandidateResolution, contract: WorktreeContract, generation_id: str
) -> str | None:
    if resolved.contract is None or resolved.contract.contract_path != contract.contract_path:
        return "the named authority and supplied enclosure contract do not select the same leaf"
    closed = resolved.closed_leaf
    if (
        closed is None
        or closed.manifest is None
        or closed.reopened.generation is None
        or closed.reopened.generation.generation_id != generation_id
    ):
        return "the exact named parent comparison could not be reopened"
    manifest = closed.manifest
    if manifest.scope.selected != "task-context":
        return "recovery is scoped to task-context generations produced by the ordinary recording command"
    if manifest.records.assessments or any(
        ref.owner in RESERVED_CURATOR_OWNERS for ref in manifest.evidence
    ):
        return "the parent already captured an assessment collection; recovery cannot replace it"
    if not closed.reopened.available() or any(
        channel.state != "available" for channel in closed.reopened.knowledge
    ):
        return "the parent's exact retained source, knowledge and evidence must all be available"
    return None


def _refused(detail: str) -> ComparisonGenerationFreeze:
    return ComparisonGenerationFreeze(
        state="refused",
        refusal=refusal(
            "comparison_refused",
            detail,
            offending_input="recovery",
            next_action="Select the exact retained parent and original curator generation; no current inputs are substituted.",
        ),
    )
