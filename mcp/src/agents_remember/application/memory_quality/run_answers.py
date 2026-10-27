"""Shared run-state, scope and refusal answers for background memory operations."""

from __future__ import annotations

from pathlib import Path

from agents_remember.application.memory_quality.answer import quality_run_next_args
from agents_remember.application.memory_quality.runs import QualityRunSnapshot
from agents_remember.application.memory_scope import MemoryScopeIdentity
from agents_remember.errors import MemoryCandidatePairError, MemoryCandidatePairFailure
from agents_remember.models.memory import MemoryQualityPollRequest


def _unfinished_poll_payload(
    repo_id: str,
    snapshot: QualityRunSnapshot,
    scope_identity: MemoryScopeIdentity,
    request: MemoryQualityPollRequest,
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
        # MIK-R110 rule 4: the failed run names its recovery beside its error.
        result["code"] = "quality-run-failed"
        result["nextAction"] = "memory_quality_check"
        result["nextArgs"] = quality_run_next_args(request, mode="start")
    return result


def _pair_refusal(repo_id: str, error: MemoryCandidatePairError) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": False,
        "operation": "memory_quality_check",
        "repoId": repo_id,
        "status": "scope-refused",
        # MIK-R110 rule 4: the refusal's identity doubles as the answer's code
        # and its detail as the one-sentence error, beside the next action.
        "code": error.status,
        "error": error.detail,
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
