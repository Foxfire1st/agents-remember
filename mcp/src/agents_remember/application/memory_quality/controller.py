"""Typed sync/start/poll controller for repository-scoped memory quality."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agents_remember.application.memory_quality.census import (
    PreparedMemoryCensus,
    census_curator_candidates,
    prepare_memory_census,
    publish_memory_census,
)
from agents_remember.application.memory_quality.runs import (
    QualityRunIdentity,
    QualityRunSnapshot,
    poll_quality_run,
    start_quality_run,
)
from agents_remember.application.memory_scope import (
    MemoryScope,
    MemoryScopeIdentity,
    resolve_memory_candidate_scope,
    resolve_memory_scope,
    revalidate_memory_candidate_scope,
)
from agents_remember.application.runtime.startup import measuring_build_stamp
from agents_remember.errors import (
    CuratorCoherenceError,
    MemoryCandidatePairError,
    MemoryCandidatePairFailure,
)
from agents_remember.kernel import filesystem
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.route_index import build_route_indexes
from agents_remember.memory_quality.check import (
    DRIFT_CHECK_NAME,
    DriftCheckContext,
    normalize_checks,
    run_memory_quality_check,
)
from agents_remember.memory_quality.curator_checklist import (
    CuratorChecklist,
    split_commit_owned_findings,
    write_curator_checklist,
)
from agents_remember.memory_quality.final_certification import final_catalog_readiness
from agents_remember.memory_quality.final_certification.catalog import (
    ReadinessProjectionInput,
)
from agents_remember.memory_quality.integrity.check_missing_onboarding import (
    check_missing_onboarding,
)
from agents_remember.memory_quality.integrity.governing_overview_resolution import (
    check_governing_overview_resolution,
)
from agents_remember.memory_quality.knowledge_review import (
    AssessmentSummary,
    AssessmentSummaryInput,
    summarise_assessment_state,
)
from agents_remember.models.lifecycles.review_assessment import assessment_subject_id
from agents_remember.models.memory import (
    MemoryQualityPollRequest,
    MemoryQualityStartRequest,
    MemoryQualitySyncRequest,
)
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    ValidatedCuratorCoherence,
    all_assessment_subject_ids,
    curator_coherence_no_impact,
    curator_coherence_paths,
    curator_coherence_subject_assessment_state,
    require_current_curator_coherence,
)
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from agents_remember.worktrees.modules.onboarding import (
    contract_memory_verified_commit,
    validate_memory_refresh_attestations,
)
from agents_remember.worktrees.modules.onboarding_acceptance import OnboardingBodyGateEvidence

_CAPACITY_GUIDANCE = (
    "Poll an existing run or wait for active memory-quality work to finish, then submit "
    "this start request again."
)
_RUN_NOT_FOUND_GUIDANCE = (
    "The run was evicted, belongs to another repository, or the server restarted; "
    "submit a new start request with the original contract_path for a worktree candidate. "
    "Repository id alone is not candidate-pair authority."
)


@dataclass(frozen=True)
class MemoryQualityExecution:
    """One canonical execution and the result-affecting publication decision."""

    config: McpRuntimeConfig
    scope: MemoryScope
    checks: tuple[str, ...]
    detail_limit: int
    publish_curator_report: bool

    @property
    def identity(self) -> QualityRunIdentity:
        return QualityRunIdentity(
            repo_id=self.scope.repo_id,
            scope=self.scope.identity,
            checks=self.checks,
            detail_limit=self.detail_limit,
            publish_curator_report=self.publish_curator_report,
        )


@dataclass(frozen=True)
class _CuratorCandidateInputs:
    code_tree: str
    memory_tree: str


def _run_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualitySyncRequest,
) -> dict[str, object]:
    """Resolve and synchronously execute one explicit sync request."""

    try:
        execution = _resolve_execution(config, request)
    except MemoryCandidatePairError as error:
        return _pair_refusal(request.repo_id, error)
    return _execute_or_refuse(execution)


def _start_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualityStartRequest,
) -> dict[str, object]:
    """Resolve and admit one explicit async-start request."""

    try:
        execution = _resolve_execution(config, request)
    except MemoryCandidatePairError as error:
        return _pair_refusal(request.repo_id, error)
    admission = start_quality_run(
        execution.identity,
        lambda: _execute_or_refuse(execution),
    )
    if admission.state == "capacity-reached":
        return {
            "ok": False,
            "operation": "memory_quality_check",
            "repoId": execution.scope.repo_id,
            "status": "capacity-reached",
            "guidance": _CAPACITY_GUIDANCE,
            **_scope_projection(execution.scope.identity),
        }
    if admission.run_id is None:
        raise RuntimeError("memory-quality admission did not retain its run identity")
    return {
        "ok": True,
        "operation": "memory_quality_check",
        "repoId": execution.scope.repo_id,
        "status": admission.state,
        "runId": admission.run_id,
        **_scope_projection(execution.scope.identity),
    }


def _poll_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualityPollRequest,
) -> dict[str, object]:
    """Poll one run only through its configured canonical repository."""

    repo_id = require_repo(config, request.repo_id).repo_id
    snapshot = poll_quality_run(repo_id, request.run_id)
    if snapshot is None:
        return {
            "ok": False,
            "operation": "memory_quality_check",
            "repoId": repo_id,
            "status": "run-not-found",
            "runId": request.run_id,
            "guidance": _RUN_NOT_FOUND_GUIDANCE,
        }
    scope_identity = snapshot.identity.scope
    mismatch = _poll_scope_mismatch(request, scope_identity)
    if mismatch is not None:
        return mismatch
    if scope_identity.pair_identity is not None:
        try:
            current = resolve_memory_candidate_scope(
                config,
                repo_id=repo_id,
                contract_path=scope_identity.pair_identity.contractPath,
            )
            if current.pair_identity != scope_identity.pair_identity:
                raise MemoryCandidatePairError(
                    "memory-candidate-pair-stale",
                    "the polled result belongs to a code/memory pair that is no longer current",
                    failure=MemoryCandidatePairFailure(
                        field="pairIdentity",
                        contract_path=scope_identity.pair_identity.contractPath,
                        expected={
                            "pairIdentity": scope_identity.pair_identity.model_dump(mode="json")
                        },
                        observed={
                            "pairIdentity": (
                                None
                                if current.pair_identity is None
                                else current.pair_identity.model_dump(mode="json")
                            )
                        },
                        next_action="worktree_sync",
                        next_args={
                            "contract_path": scope_identity.pair_identity.contractPath,
                            "dry_run": True,
                        },
                    ),
                )
        except MemoryCandidatePairError as error:
            return {
                **_pair_refusal(repo_id, error),
                "runId": snapshot.run_id,
            }
    if snapshot.status != "completed":
        return _unfinished_poll_payload(repo_id, snapshot, scope_identity)
    result = dict(snapshot.result or {})
    if result.get("status") == "scope-refused":
        return {**result, "runId": snapshot.run_id}
    return {**result, "status": "completed", "runId": snapshot.run_id}


def _stamped(payload: dict[str, object]) -> dict[str, object]:
    """Name the build that produced this measurement on every memory-quality response (D-33).

    Applied at the three public entry points rather than at each return: the controller answers a
    sync run, an async admission, a poll, and the refusal envelopes around them, and a stamp added
    at one return site is a stamp missing from the others. A reader can then tell a count produced
    by the candidate's own code from one produced by the build the MCP surface happens to serve.
    """

    return {**payload, **measuring_build_stamp()}


def run_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualitySyncRequest,
) -> dict[str, object]:
    """Resolve and synchronously execute one explicit sync request."""

    return _stamped(_run_memory_quality_request(config, request))


def start_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualityStartRequest,
) -> dict[str, object]:
    """Resolve and admit one explicit async-start request."""

    return _stamped(_start_memory_quality_request(config, request))


def poll_memory_quality_request(
    config: McpRuntimeConfig,
    request: MemoryQualityPollRequest,
) -> dict[str, object]:
    """Poll one run only through its configured canonical repository."""

    return _stamped(_poll_memory_quality_request(config, request))


def _unfinished_poll_payload(
    repo_id: str,
    snapshot: QualityRunSnapshot,
    scope_identity: MemoryScopeIdentity,
) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": True,
        "operation": "memory_quality_check",
        "repoId": repo_id,
        "status": snapshot.status,
        "runId": snapshot.run_id,
        **_scope_projection(scope_identity),
    }
    if snapshot.status == "failed":
        result["error"] = snapshot.error
    return result


def _execute_or_refuse(execution: MemoryQualityExecution) -> dict[str, object]:
    try:
        return _execute_memory_quality(execution)
    except MemoryCandidatePairError as error:
        return _pair_refusal(execution.scope.repo_id, error)


def _pair_refusal(repo_id: str, error: MemoryCandidatePairError) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": False,
        "operation": "memory_quality_check",
        "repoId": repo_id,
        "status": "scope-refused",
        **error.response_fields(),
    }
    result["status"] = "scope-refused"
    return result


def _scope_projection(identity: MemoryScopeIdentity) -> dict[str, object]:
    pair = identity.pair_identity
    if pair is None:
        return {
            "scopeAuthority": "official-diagnostic",
            "acceptanceEligible": False,
        }
    return {
        "scopeAuthority": "leaf-candidate",
        "acceptanceEligible": True,
        "contractPath": pair.contractPath,
        "pairIdentity": pair.model_dump(mode="json"),
    }


def _poll_scope_mismatch(
    request: MemoryQualityPollRequest,
    identity: MemoryScopeIdentity,
) -> dict[str, object] | None:
    pair = identity.pair_identity
    requested = request.contract_path
    if pair is None and requested is None:
        return None
    if (
        pair is not None
        and requested is not None
        and Path(requested).resolve() == Path(pair.contractPath).resolve()
    ):
        return None
    contract_path = pair.contractPath if pair is not None else str(requested or "")
    error = MemoryCandidatePairError(
        "memory-candidate-pair-poll-scope-mismatch",
        "poll must repeat the exact scope of the admitted memory-quality run",
        failure=MemoryCandidatePairFailure(
            field="contractPath",
            contract_path=contract_path,
            expected={"contractPath": None if pair is None else pair.contractPath},
            observed={"contractPath": requested},
            next_action="memory_quality_check",
        ),
    )
    return {
        **_pair_refusal(request.repo_id, error),
        "runId": request.run_id,
    }


def _resolve_execution(
    config: McpRuntimeConfig,
    request: MemoryQualitySyncRequest | MemoryQualityStartRequest,
) -> MemoryQualityExecution:
    checks = tuple(sorted(set(normalize_checks(request.checks, include_integrity=True))))
    scope = (
        resolve_memory_scope(config, repo_id=request.repo_id, contract_path=None)
        if request.contract_path is None
        else resolve_memory_candidate_scope(
            config,
            repo_id=request.repo_id,
            contract_path=request.contract_path,
        )
    )
    return MemoryQualityExecution(
        config=config,
        scope=scope,
        checks=checks,
        detail_limit=request.detail_limit,
        publish_curator_report=scope.curator_report_path is not None and not request.checks,
    )


def _execute_memory_quality(execution: MemoryQualityExecution) -> dict[str, object]:
    scope = revalidate_memory_candidate_scope(execution.config, execution.scope)
    quality_code_root = scope.quality_code_root
    quality_context = scope.quality_context
    candidate_inputs = (
        _curator_candidate_inputs(scope) if execution.publish_curator_report else None
    )
    census = prepare_memory_census(scope)
    payload = run_memory_quality_check(
        scope.onboarding_root,
        checks=execution.checks,
        drift_context=DriftCheckContext(
            code_repository_root=quality_code_root,
            context=quality_context,
            detail_limit=execution.detail_limit,
            unstamped_code_commit=(
                None if scope.prepared_code_view is not None else scope.unstamped_code_commit
            ),
            retained_code_history_commits=scope.prepared_code_history_commits,
            report_path=(scope.curator_report_path if execution.publish_curator_report else None),
            include_rows=execution.publish_curator_report,
            write_report=not execution.publish_curator_report,
        ),
        include_report_only_findings=execution.publish_curator_report,
    )
    scope = revalidate_memory_candidate_scope(execution.config, scope)
    if candidate_inputs is not None:
        _require_same_curator_candidate(
            scope,
            expected=candidate_inputs,
            observed=_curator_candidate_inputs(scope),
        )
    response: dict[str, object] = {
        "operation": "memory_quality_check",
        "repoId": scope.repo_id,
        "onboardingRoot": scope.onboarding_root.as_posix(),
        **_scope_projection(scope.identity),
        **payload,
    }
    if census is not None:
        response["memoryCensus"] = publish_memory_census(
            census, detail_limit=execution.detail_limit
        )
    if not execution.publish_curator_report:
        return _bounded_quality_response(response, execution.detail_limit)
    _attach_curator_checklist(
        execution,
        payload,
        response,
        candidate_inputs=candidate_inputs,
        census=census,
    )
    return _bounded_quality_response(response, execution.detail_limit)


def _bounded_quality_response(response: dict[str, object], detail_limit: int) -> dict[str, object]:
    """Bound transport samples after complete checklist and catalog publication."""

    def sampled(payload: dict[str, Any]) -> dict[str, Any]:
        result = dict(payload)
        for field, count_field in (
            ("findings", "findingSampleCount"),
            ("surfacedFindings", "surfacedSampleCount"),
            ("debtFindings", "debtSampleCount"),
            ("reportOnlySample", "reportOnlySampleCount"),
        ):
            findings = result.get(field)
            if isinstance(findings, list):
                result[field] = findings[:detail_limit]
                result[count_field] = len(result[field])
        return result

    result = sampled(response)
    checks = result.get("checks")
    if isinstance(checks, dict):
        result["checks"] = {
            name: sampled(check) if isinstance(check, dict) else check
            for name, check in checks.items()
        }
    return result


def _attach_curator_checklist(
    execution: MemoryQualityExecution,
    payload: dict[str, Any],
    response: dict[str, object],
    *,
    candidate_inputs: _CuratorCandidateInputs | None = None,
    census: PreparedMemoryCensus | None,
) -> None:
    config = execution.config
    scope = revalidate_memory_candidate_scope(config, execution.scope)
    checks = payload.get("checks")
    drift_result = checks.get(DRIFT_CHECK_NAME, {}) if isinstance(checks, dict) else {}
    drift_rows = drift_result.pop("rows", []) if isinstance(drift_result, dict) else []
    report_only = payload.pop("reportOnlyFindings", [])
    findings = payload.get("findings")
    style_findings = (
        [
            finding
            for finding in findings
            if isinstance(finding, dict) and finding.get("check") != DRIFT_CHECK_NAME
        ]
        if isinstance(findings, list)
        else []
    )
    repair_findings, commit_owned_findings = _checklist_finding_sets(
        style_findings,
        payload,
        scope.onboarding_root,
    )
    # D3/D16: a card whose declared `governingOverview` field or whose `## Governing Overview`
    # link resolves to nothing is a route failure. Until this leaf the product validated that a
    # source HAS a card and never that the card's declared route RESOLVES, so a dead declaration
    # reported clean. The findings join the gated repair set rather than a report-only surface:
    # a link a reader can click and land nowhere is a repair obligation, not a statistic.
    #
    # The summary is attached to `response`, not `payload`: `response` is composed from
    # `**payload` before this function runs, so a key added to `payload` here would never be
    # published. It stays OUT of `response["checks"]` because that mapping is the closed
    # `AVAILABLE_CHECKS` population the certification catalog is validated against, and a key
    # outside that population would be a catalog item with no planned identity.
    governing_overviews = check_governing_overview_resolution(scope.onboarding_root)
    response["governingOverviewResolution"] = {
        "check": governing_overviews.check,
        "ok": governing_overviews.ok,
        "cardsWalked": governing_overviews.cardsWalked,
        "cardsFlagged": governing_overviews.cardsFlagged,
        "unresolvedFieldCount": governing_overviews.unresolvedField,
        "unresolvedLinkCount": governing_overviews.unresolvedLink,
        "sectionAbsentCount": governing_overviews.sectionAbsent,
        "findings": [row.to_dict() for row in governing_overviews.findings],
        "observations": [
            {"path": row.card, "code": row.code, "message": row.note}
            for row in governing_overviews.observations
        ],
    }
    repair_findings.extend(row.to_dict() for row in governing_overviews.findings)
    missing_onboarding = check_missing_onboarding(
        code_repository_root=scope.quality_code_root,
        onboarding_root=scope.onboarding_root,
        settings=scope.quality_context.storage,
        code_repository_name=scope.quality_context.code_repository_name,
    )
    route_indexes = build_route_indexes(
        code_root=scope.quality_code_root,
        onboarding_root=scope.onboarding_root,
        repository=scope.quality_context.code_repository_name,
        storage=scope.quality_context.storage,
        dry_run=True,
    )
    scope = revalidate_memory_candidate_scope(config, scope)
    if scope.curator_report_path is None:
        raise RuntimeError("curator publication has no enclosure-local report path")
    if scope.pair_identity is None:
        raise RuntimeError("curator publication has no exact code/memory pair identity")
    if candidate_inputs is None:
        raise RuntimeError("curator publication has no exact candidate tree inputs")
    _require_same_curator_candidate(
        scope,
        expected=candidate_inputs,
        observed=_curator_candidate_inputs(scope),
    )
    if census is None:
        raise RuntimeError("curator publication requires its complete plane-derived census")
    source_candidates = census_curator_candidates(census)
    # The knowledge-review summary is derived from the curator-coherence authority, which only the
    # external-memory leaf path below can read -- but `CuratorChecklist` is built for EVERY scope.
    # It is therefore initialised here, on the same path as the other two accepted-no-impact
    # defaults, so a scope that does not enter the block reports "no recorded assessments" instead
    # of raising UnboundLocalError at the checklist construction below.
    knowledge_review: tuple[AssessmentSummary, ...] = ()
    if (
        scope.contract is not None
        and scope.contract.memory_mode == "external"
        and scope.contract.kind == "leaf"
    ):
        changed_paths = sorted({*census.scope.working_paths, *census.scope.committed_paths})
        current_working_paths = _current_working_code_paths(scope, census.scope.working_paths)
        accepted_no_impact = frozenset()
        accepted_route_no_impact = frozenset()
        try:
            coherence = require_current_curator_coherence(scope.contract)
        except CuratorCoherenceError:
            pass
        else:
            no_impact = curator_coherence_no_impact(coherence)
            accepted_no_impact = no_impact.content_sources
            accepted_route_no_impact = no_impact.source_routes
            knowledge_review = curator_knowledge_review_summaries(coherence)
        try:
            validate_memory_refresh_attestations(
                scope.quality_context,
                changed_paths,
                working_paths=current_working_paths,
                body_gate=OnboardingBodyGateEvidence(
                    memory_tree=scope.onboarding_root.parent,
                    memory_verified_commit=contract_memory_verified_commit(scope.contract),
                    accepted_no_impact=accepted_no_impact,
                ),
                route_body_gate=OnboardingBodyGateEvidence(
                    memory_tree=scope.onboarding_root.parent,
                    memory_verified_commit=contract_memory_verified_commit(scope.contract),
                    accepted_no_impact=accepted_route_no_impact,
                ),
            )
        except RuntimeError as error:
            repair_findings.append(
                {
                    "check": "memory-refresh-attestations",
                    "code": "memory-refresh-attestation-failed",
                    "path": "",
                    "message": str(error),
                }
            )
    repair_findings.extend(
        {
            "check": "memory-census",
            "code": blocker.code,
            "path": blocker.identity.memoryRootRelativePath
            if blocker.identity
            else blocker.sourcePath or "",
            "message": blocker.detail,
        }
        for blocker in census.result.blockers
    )
    checklist = write_curator_checklist(
        CuratorChecklist(
            report_path=scope.curator_report_path,
            repo_id=scope.repo_id,
            code_root=scope.quality_code_root,
            onboarding_root=scope.onboarding_root,
            pair_identity=scope.pair_identity,
            code_candidate_tree=candidate_inputs.code_tree,
            memory_candidate_tree=candidate_inputs.memory_tree,
            quality=payload,
            repair_findings=repair_findings,
            commit_owned_findings=commit_owned_findings,
            missing_onboarding=missing_onboarding,
            stale_route_indexes=route_indexes.stale_indexes,
            source_candidates=source_candidates,
            drift_rows=drift_rows,
            report_only_findings=report_only,
            knowledge_review=knowledge_review,
        )
    )
    response.pop("reportOnlyFindings", None)
    response.update(checklist)
    _attach_coherence_readiness(scope, response)
    _attach_final_full_catalog(
        scope,
        response,
        candidate_inputs=candidate_inputs,
        missing_onboarding=missing_onboarding,
        stale_route_indexes=route_indexes.stale_indexes,
    )


def _checklist_finding_sets(
    style_findings: list[dict[str, Any]],
    payload: dict[str, Any],
    onboarding_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The repairable findings the curator owns, and the closeout-owned rows beside them.

    D-24/D-29: the citation check classifies the rows no curator edit can ever discharge -- an
    anchor that resolves more than once in the cited FILE, which no range a curator may write
    changes -- into its own bucket. They belong in the section the report already keeps for the
    closing stamp, where the decision they wait on is actually made, not in the repairable set
    whose count has to reach zero. ``split_commit_owned_findings`` supplies the other half of the
    same class: a stamp only closeout can write for a card this task created.
    """

    repair, commit_owned = split_commit_owned_findings(style_findings, onboarding_root)
    checks = payload.get("checks")
    declared = (
        [
            row
            for _, result in sorted(checks.items())
            if isinstance(result, dict)
            for row in result.get("closeoutOwnedFindings", [])
        ]
        if isinstance(checks, dict)
        else []
    )
    return repair, [*commit_owned, *declared]


def _attach_final_full_catalog(
    scope: MemoryScope,
    response: dict[str, object],
    *,
    candidate_inputs: _CuratorCandidateInputs,
    missing_onboarding: dict[str, Any],
    stale_route_indexes: list[str],
) -> None:
    """Project the deterministic complete Gate-5 catalog onto the full run result.

    The interactive full contract-scoped run prepares memory before certification
    admission. It cannot hold the R21 Gate 1-4
    certificates or the R07 affected-closure plan, so the projection names the exact
    complete catalog population, every item's typed status, and the still-missing
    certification authorities without claiming certification eligibility.
    """

    coherence_status = str(
        response.get("coherenceStatus") or "not-evaluated-quality-action-required"
    )
    coherence_record_digest = response.get("coherenceRecordDigest")
    if scope.pair_identity is None:
        raise RuntimeError("final full catalog projection requires the exact pair identity")
    response["finalFullCatalog"] = final_catalog_readiness(
        ReadinessProjectionInput(
            executed_checks=_catalog_checks(response),
            missing_onboarding_count=int(missing_onboarding.get("missingCount", 0) or 0),
            stale_route_index_count=len(stale_route_indexes),
            coherence_status=coherence_status if coherence_status == "current" else None,
            coherence_record_digest=(
                str(coherence_record_digest) if coherence_record_digest else None
            ),
            candidate_pair_authority_digest=scope.pair_identity.contractDigest,
            affected_closure_plan_digest=None,
            memory_tree=candidate_inputs.memory_tree,
        )
    )


def _catalog_checks(response: dict[str, object]) -> dict[str, dict[str, Any]]:
    checks = response.get("checks")
    if isinstance(checks, dict):
        return {name: result for name, result in checks.items() if isinstance(result, dict)}
    return {}


def _curator_candidate_inputs(scope: MemoryScope) -> _CuratorCandidateInputs:
    """Capture both working trees without mutating either repository index."""

    if scope.pair_identity is None:
        raise RuntimeError("curator publication has no exact code/memory pair identity")
    memory_root = scope.onboarding_root.parent
    with (
        TemporaryDirectory(prefix=".memory-quality-code-") as code_temporary,
        TemporaryDirectory(prefix=".memory-quality-memory-") as memory_temporary,
    ):
        return _CuratorCandidateInputs(
            code_tree=worktree_candidate_tree(
                scope.code_root,
                Path(code_temporary) / "index",
            ),
            memory_tree=worktree_candidate_tree(
                memory_root,
                Path(memory_temporary) / "index",
            ),
        )


def _current_working_code_paths(scope: MemoryScope, paths: tuple[str, ...]) -> list[str]:
    """Keep current source targets while retaining deleted paths in the census history."""

    return [path for path in paths if filesystem.is_file(scope.quality_code_root / path)]


def _require_same_curator_candidate(
    scope: MemoryScope,
    *,
    expected: _CuratorCandidateInputs,
    observed: _CuratorCandidateInputs,
) -> None:
    if observed == expected:
        return
    pair = scope.pair_identity
    raise MemoryCandidatePairError(
        "memory-quality-candidate-changed",
        "the exact code or memory candidate changed while memory quality was running",
        failure=MemoryCandidatePairFailure(
            field="candidateTrees",
            contract_path="" if pair is None else pair.contractPath,
            expected={
                "codeCandidateTree": expected.code_tree,
                "memoryCandidateTree": expected.memory_tree,
            },
            observed={
                "codeCandidateTree": observed.code_tree,
                "memoryCandidateTree": observed.memory_tree,
            },
            next_action="memory_quality_check",
            next_args=(
                None
                if pair is None
                else {"repo_id": scope.repo_id, "contract_path": pair.contractPath}
            ),
        ),
    )


def curator_knowledge_review_summaries(
    coherence: ValidatedCuratorCoherence,
) -> tuple[AssessmentSummary, ...]:
    """Summarise the stored assessment collection for the checklist's factual section.

    This reads the *already published* authority and decides nothing about it. It is deliberately not
    an input to ``curatorActionableCount`` (see ``CuratorChecklist.knowledge_review``), and a subject
    with no stored assessment produces no row at all -- it is not rendered as a disposition, which is
    ``Doc13:104``'s "missing assessments stay missing" expressed at the last place a projection could
    break it.

    Currentness is not measured here. The checklist is written from the curator's own memory-quality
    run, which is not a read of the current candidate inputs, so the summary reports the recorded
    collection and its counted limitations without claiming that any binding still matches. The
    stale/unresolved limitation counts come from the projection, which is where a measurement of the
    current world belongs.
    """

    summaries: list[AssessmentSummary] = []
    for subject_id in all_assessment_subject_ids(coherence):
        state = curator_coherence_subject_assessment_state(coherence, subject_id)
        records = [
            assessment
            for assessment in coherence.record.assessments
            if assessment_subject_id(assessment) == subject_id
        ]
        summaries.append(
            summarise_assessment_state(
                AssessmentSummaryInput(
                    subjectId=subject_id,
                    assessmentCount=state.assessmentCount,
                    dispositions=tuple(entry.disposition for entry in state.assessments),
                    comparisonRefs=tuple(dict.fromkeys(item.comparisonRef for item in records)),
                    scopeRefs=tuple(dict.fromkeys(item.scopeManifestRef for item in records)),
                    unresolvedCount=state.unresolvedCount,
                    staleCount=state.staleCount,
                )
            )
        )
    return tuple(summaries)


def _attach_coherence_readiness(scope: MemoryScope, response: dict[str, object]) -> None:
    """Join raw memory quality with the same authority validator closeout calls."""

    quality_status = str(response.get("checklistStatus", ""))
    response["qualityChecklistStatus"] = quality_status
    response["closeoutReady"] = False
    if scope.contract is None:
        raise RuntimeError("contract-scoped curator publication lost its leaf contract")
    response["coherenceCanonicalPath"] = curator_coherence_paths(
        scope.contract
    ).canonical.as_posix()
    if quality_status != "ready-for-closeout":
        response["coherenceStatus"] = "not-evaluated-quality-action-required"
        return
    try:
        validated = require_current_curator_coherence(scope.contract)
    except CuratorCoherenceError as exc:
        response["ok"] = False
        response["checklistStatus"] = "coherence-required"
        response["coherenceStatus"] = exc.status
        response["guidance"] = (
            "Memory repairs are complete, but closeout is not ready: publish or refresh the "
            "exact structured curator-coherence authority with curator_coherence, then validate it."
        )
        return
    response["coherenceStatus"] = "current"
    response["coherenceRecordDigest"] = validated.record_digest
    response["closeoutReady"] = True
