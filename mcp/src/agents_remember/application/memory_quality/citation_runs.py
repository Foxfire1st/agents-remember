"""Citation previews admitted and polled through the quality-run machinery."""

from __future__ import annotations

from collections.abc import Callable

from agents_remember.application.memory_quality.controller import poll_scoped_run
from agents_remember.application.memory_quality.run_answers import _pair_refusal
from agents_remember.application.memory_quality.runs import QualityRunIdentity, start_quality_run
from agents_remember.application.memory_scope import (
    MemoryScope,
    resolve_memory_candidate_scope,
    resolve_memory_scope,
    revalidate_memory_candidate_scope,
)
from agents_remember.application.memory_tools import CitationOperationScope
from agents_remember.errors import MemoryCandidatePairError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.memory import MemoryQualityPollRequest, MemoryQualityStartRequest


def start_citation_preview(
    config: McpRuntimeConfig,
    request: MemoryQualityStartRequest,
    selectors: CitationOperationScope,
    execute: Callable[[], dict[str, object]],
) -> dict[str, object]:
    """Admit one immutable preview selection without running it on the calling thread."""
    selectors.validate(allow_documents=True)
    try:
        scope = _preview_scope(config, request)
    except MemoryCandidatePairError as error:
        return _preview_pair_refusal(request.repo_id, error)
    identity = QualityRunIdentity(
        repo_id=scope.repo_id,
        scope=scope.identity,
        checks=(),
        detail_limit=0,
        publish_curator_report=False,
        operation="citation_fix",
        document=selectors.document,
        documents=selectors.documents,
    )
    admission = start_quality_run(identity, lambda: _execute_preview(config, scope, execute))
    result: dict[str, object] = {
        "ok": admission.state != "capacity-reached",
        "operation": "citation_fix",
        "repoId": scope.repo_id,
        "status": admission.state,
    }
    if admission.run_id is not None:
        result["runId"] = admission.run_id
    else:
        result.update(
            code="capacity-reached",
            error="The check server is at its run capacity; start the preview again shortly.",
            nextAction="citation_fix",
        )
    return result


def poll_citation_preview(
    config: McpRuntimeConfig, request: MemoryQualityPollRequest
) -> dict[str, object]:
    """Use the same run ownership, scope, freshness and failure handling as checks."""
    return poll_scoped_run(config, request, operation="citation_fix")


def _preview_scope(config: McpRuntimeConfig, request: MemoryQualityStartRequest) -> MemoryScope:
    if request.contract_path is not None:
        return resolve_memory_candidate_scope(
            config, repo_id=request.repo_id, contract_path=request.contract_path
        )
    return resolve_memory_scope(config, repo_id=request.repo_id, contract_path=None)


def _execute_preview(
    config: McpRuntimeConfig, scope: MemoryScope, execute: Callable[[], dict[str, object]]
) -> dict[str, object]:
    try:
        revalidate_memory_candidate_scope(config, scope)
        result = execute()
        revalidate_memory_candidate_scope(config, scope)
        return result
    except MemoryCandidatePairError as error:
        return _preview_pair_refusal(scope.repo_id, error)


def _preview_pair_refusal(repo_id: str, error: MemoryCandidatePairError) -> dict[str, object]:
    return {**_pair_refusal(repo_id, error), "operation": "citation_fix"}
