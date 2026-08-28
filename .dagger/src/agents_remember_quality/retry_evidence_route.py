"""Non-accepting Dagger retry evidence orchestration."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Protocol

import dagger
from dagger import ReturnType

from agents_remember_quality.quality_command import (
    quality_wrapper_command,
    retry_decision_lines,
)


class CandidateBuilder(Protocol):
    def __call__(
        self,
        source: dagger.Directory,
        repository_bundle: dagger.File,
        *,
        attempt_nonce: str,
        reports: str,
    ) -> dagger.Container: ...


@dataclass(frozen=True)
class RetryEvidenceOutcome:
    container: dagger.Container
    exit_code: int


@dataclass(frozen=True)
class RetryEvidenceContext:
    """Stable candidate and cache inputs shared by every evidence attempt."""

    source: dagger.Directory
    repository_bundle: dagger.File
    diff_base: str
    cache_mount_root: str
    build_candidate: CandidateBuilder


@dataclass(frozen=True)
class _Attempt:
    name: str
    exit_code: int
    retry_decisions: tuple[str, ...]
    pytest_results: tuple[str, ...]
    candidate_provenance: dict[str, object] | None

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "exitCode": self.exit_code,
            "retryDecisions": self.retry_decisions,
            "pytestResults": self.pytest_results,
            "candidateProvenance": self.candidate_provenance,
        }


@dataclass(frozen=True)
class _WrapperRequest:
    name: str
    reports: str
    diff_base: str
    mode: str
    candidate_provenance: dict[str, object]


async def run_exact_retry_evidence(
    context: RetryEvidenceContext,
    mode: str,
) -> RetryEvidenceOutcome:
    """Run a real failed proof followed by an exact reuse in a fresh container."""

    evidence_key = secrets.token_hex(16)
    cache_root = f"{context.cache_mount_root}/evidence/{evidence_key}"
    attempts: list[_Attempt] = []
    last_container: dagger.Container | None = None
    for name in ("first", "second"):
        reports = f"/reports/{name}"
        container = await _evidence_container(
            context,
            reports=reports,
            evidence_key=evidence_key,
            cache_root=cache_root,
        )
        container, seed_exit = await _prepare_seed_failure(container, reports)
        if seed_exit == 0:
            container, provenance = await _capture_provenance(container, reports)
            if provenance is not None:
                container, attempt = await _run_wrapper(
                    container,
                    _WrapperRequest(name, reports, context.diff_base, mode, provenance),
                )
            else:
                attempt = _Attempt(name, await container.exit_code(), (), (), None)
        else:
            attempt = _Attempt(name, seed_exit, (), (), None)
        attempts.append(attempt)
        last_container = container
        if not _base_attempt_passes(attempt, exact=name == "second"):
            break
    if last_container is None:  # pragma: no cover - fixed two-attempt loop
        raise RuntimeError("retry evidence did not execute")
    candidates_consistent = _base_candidates_match(attempts)
    passed = _exact_attempts_pass(attempts) and candidates_consistent
    payload = {
        "schemaVersion": "ar-quality-retry-evidence/v4",
        "status": "passed" if passed else "failed",
        "acceptanceEligible": False,
        "mode": mode,
        "diffBase": context.diff_base,
        "seededCandidateProvenance": _first_candidate_provenance(attempts),
        "attemptCandidatesConsistent": candidates_consistent,
        "cacheOwner": "locked Dagger volume with invocation-unique evidence namespace",
        "seedFailure": "real post-pytest diff-coverage failure",
        "population": {
            "attemptNames": ["first", "second"],
            "pytestSelection": "same mode and diff base on both attempts",
        },
        "topology": {
            "freshCandidateContainers": len(attempts),
            "cacheNamespace": "one invocation-unique locked subdirectory",
        },
        "phaseDefinitions": [
            "stage controlled post-pytest coverage failure",
            "run wrapper and publish content-addressed proof",
            "recreate candidate in a fresh container",
            "run wrapper and require exact-candidate proof reuse",
            "delete only the invocation-owned cache namespace",
        ],
        "repetitions": {"requested": 2, "completed": len(attempts)},
        "limitations": [
            "This route is non-accepting and uses one controlled failure shape.",
            "The final full Dagger quality gate remains the acceptance authority.",
        ],
        "attempts": [attempt.payload() for attempt in attempts],
    }
    last_container = last_container.with_new_file(
        "/reports/retry-evidence.json",
        contents=json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    last_container, cleanup_ok = await _cleanup_cache(last_container, cache_root)
    return RetryEvidenceOutcome(last_container, 0 if passed and cleanup_ok else 1)


async def run_retry_matrix_evidence(
    context: RetryEvidenceContext,
) -> RetryEvidenceOutcome:
    """Exercise exact, delta, global-fresh, lane, and context decisions on the real wrapper."""

    evidence_key = secrets.token_hex(16)
    cache_root = f"{context.cache_mount_root}/evidence/{evidence_key}"
    template_cache_root = f"{cache_root}/template"
    attempts: list[_Attempt] = []
    last_container: dagger.Container | None = None
    for name in ("seed", "exact"):
        reports = f"/reports/{name}"
        container = await _evidence_container(
            context,
            reports=reports,
            evidence_key=evidence_key,
            cache_root=cache_root,
        )
        container, seed_exit = await _prepare_seed_failure(container, reports)
        if seed_exit == 0:
            container, provenance = await _capture_provenance(container, reports)
            if provenance is not None:
                container, attempt = await _run_wrapper(
                    container,
                    _WrapperRequest(name, reports, context.diff_base, "full", provenance),
                )
            else:
                attempt = _Attempt(name, await container.exit_code(), (), (), None)
        else:
            attempt = _Attempt(name, seed_exit, (), (), None)
        attempts.append(attempt)
        last_container = container
        if not _base_attempt_passes(attempt, exact=name == "exact"):
            break
    scenario_results: list[dict[str, object]] = []
    template_exit = 1
    if (
        last_container is not None
        and len(attempts) == 2
        and _base_attempt_passes(attempts[-1], exact=True)
    ):
        last_container, template_exit = await _clone_cache(
            last_container,
            source_root=cache_root,
            destination_root=template_cache_root,
        )
    if template_exit == 0:
        for scenario in _SCENARIOS:
            container, result = await _run_scenario(
                context,
                scenario=scenario,
                evidence_key=evidence_key,
                cache_root=cache_root,
                template_cache_root=template_cache_root,
            )
            scenario_results.append(result)
            last_container = container
            if result["status"] != "passed":
                break
    base_candidates_consistent = _base_candidates_match(attempts)
    scenario_provenance_complete = all(
        item.get("candidateProvenance") is not None for item in scenario_results
    )
    passed = bool(
        len(attempts) == 2
        and _base_attempt_passes(attempts[0], exact=False)
        and _base_attempt_passes(attempts[1], exact=True)
        and base_candidates_consistent
        and template_exit == 0
        and len(scenario_results) == len(_SCENARIOS)
        and scenario_provenance_complete
        and all(item["status"] == "passed" for item in scenario_results)
    )
    if last_container is None:  # pragma: no cover - environment construction always returns
        raise RuntimeError("retry matrix evidence did not execute")
    payload = {
        "schemaVersion": "ar-quality-retry-matrix-evidence/v4",
        "status": "passed" if passed else "failed",
        "acceptanceEligible": False,
        "mode": "full",
        "diffBase": context.diff_base,
        "seededCandidateProvenance": _first_candidate_provenance(attempts),
        "baseAttemptCandidatesConsistent": base_candidates_consistent,
        "scenarioProvenanceComplete": scenario_provenance_complete,
        "cacheOwner": "locked Dagger volume with invocation-unique evidence namespace",
        "seedFailure": "real post-pytest diff-coverage failure",
        "population": {
            "baseAttempts": ["seed", "exact"],
            "scenarios": [_scenario_contract(item) for item in _SCENARIOS],
        },
        "topology": {
            "freshCandidateContainers": len(attempts) + len(scenario_results),
            "baseProofContainers": len(attempts),
            "scenarioContainers": len(scenario_results),
            "cacheNamespace": "invocation-unique locked namespace cloned per scenario",
        },
        "phaseDefinitions": [
            "publish one seeded failing proof",
            "prove exact reuse in a fresh container",
            "clone the immutable proof into a scenario-owned namespace",
            "apply and stage one controlled scenario mutation",
            "run the real wrapper and inspect its retry decision",
            "delete only the scenario and invocation-owned cache namespaces",
        ],
        "repetitions": {
            "baseRequested": 2,
            "baseCompleted": len(attempts),
            "scenarioRequested": len(_SCENARIOS),
            "scenarioCompleted": len(scenario_results),
        },
        "limitations": [
            "Each controlled mutation scenario executes once on this machine.",
            "The route proves retry planning and execution, not acceptance.",
            "The final full Dagger quality gate remains the acceptance authority.",
        ],
        "baseAttempts": [attempt.payload() for attempt in attempts],
        "templateCloneExitCode": template_exit,
        "scenarios": scenario_results,
    }
    last_container = last_container.with_new_file(
        "/reports/retry-matrix-evidence.json",
        contents=json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    last_container, active_cleanup_ok = await _cleanup_cache(last_container, cache_root)
    last_container, template_cleanup_ok = await _cleanup_cache(
        last_container,
        template_cache_root,
    )
    cleanup_ok = active_cleanup_ok and template_cleanup_ok
    return RetryEvidenceOutcome(last_container, 0 if passed and cleanup_ok else 1)


async def _evidence_container(
    context: RetryEvidenceContext,
    *,
    reports: str,
    evidence_key: str,
    cache_root: str,
) -> dagger.Container:
    attempt_nonce = secrets.token_hex(16)
    return await (
        context.build_candidate(
            context.source,
            context.repository_bundle,
            attempt_nonce=attempt_nonce,
            reports=reports,
        )
        .with_env_variable("AR_QUALITY_RETRY_EVIDENCE_KEY", evidence_key)
        .with_env_variable("AR_QUALITY_RETRY_CACHE", cache_root)
        .sync()
    )


async def _run_wrapper(
    container: dagger.Container,
    request: _WrapperRequest,
) -> tuple[dagger.Container, _Attempt]:
    command = quality_wrapper_command(
        reports=request.reports,
        diff_base=request.diff_base,
        mode=request.mode,
    )
    container = await container.with_exec(command, expect=ReturnType.ANY).sync()
    output = await container.stdout()
    attempt = _Attempt(
        request.name,
        await container.exit_code(),
        retry_decision_lines(output),
        tuple(line for line in output.splitlines() if line.startswith("result: pytest ")),
        request.candidate_provenance,
    )
    return container, attempt


async def _prepare_seed_failure(
    container: dagger.Container,
    reports: str,
) -> tuple[dagger.Container, int]:
    command = [
        "/opt/ar-venv/bin/python",
        "-m",
        "agents_remember_test_support.testing.retry_route_evidence",
        "seed",
        "--project-root",
        "/workspace",
        "--output",
        f"{reports}/seed.json",
    ]
    container = await container.with_exec(command, expect=ReturnType.ANY).sync()
    return container, await container.exit_code()


async def _capture_provenance(
    container: dagger.Container,
    reports: str,
) -> tuple[dagger.Container, dict[str, object] | None]:
    output = f"{reports}/candidate-provenance.json"
    command = [
        "/opt/ar-venv/bin/python",
        "-m",
        "agents_remember_test_support.testing.evidence_provenance",
        "--project-root",
        "/workspace",
        "--output",
        output,
    ]
    container = await container.with_exec(command, expect=ReturnType.ANY).sync()
    if await container.exit_code() != 0:
        return container, None
    try:
        raw: object = json.loads(await container.file(output).contents())
    except json.JSONDecodeError:
        return container, None
    return container, raw if isinstance(raw, dict) else None


@dataclass(frozen=True)
class _Scenario:
    name: str
    expected_decision: str
    pytest_must_run: bool
    context_variant: bool = False
    disable_retry: bool = False


_SCENARIOS = (
    _Scenario("test", "cache-hit affected-consumers=", True),
    _Scenario("product", "cache-hit affected-consumers=", True),
    _Scenario("support", "cache-hit affected-consumers=", True),
    _Scenario("fixture", "cache-hit affected-consumers=", True),
    _Scenario("plugin", "cache-hit affected-consumers=", True),
    _Scenario("unknown", "cache-miss ownership-incomplete", False),
    _Scenario("lane", "cache-miss manifest-invalid:compatibility-key,lane-digest", False),
    _Scenario("context", "cache-miss manifest-invalid:compatibility-key", False, True),
    _Scenario("corrupt", "cache-miss manifest-invalid-json", False),
    _Scenario("disabled", "disabled (AR_QUALITY_NO_RETRY is set)", False, False, True),
)


async def _run_scenario(
    context: RetryEvidenceContext,
    *,
    scenario: _Scenario,
    evidence_key: str,
    cache_root: str,
    template_cache_root: str,
) -> tuple[dagger.Container, dict[str, object]]:
    reports = f"/reports/{scenario.name}"
    container = await _evidence_container(
        context,
        reports=reports,
        evidence_key=evidence_key,
        cache_root=cache_root,
    )
    if scenario.context_variant:
        container = container.with_env_variable(
            "AR_QUALITY_RETRY_CONTEXT_VARIANT",
            "controlled-matrix-context",
        )
    if scenario.disable_retry:
        container = container.with_env_variable("AR_QUALITY_NO_RETRY", "1")
    container, active_cleanup_ok = await _cleanup_cache(container, cache_root)
    clone_exit = 1
    if active_cleanup_ok:
        container, clone_exit = await _clone_cache(
            container,
            source_root=template_cache_root,
            destination_root=cache_root,
        )
    mutation = [
        "/opt/ar-venv/bin/python",
        "-m",
        "agents_remember_test_support.testing.retry_route_evidence",
        "mutate",
        "--project-root",
        "/workspace",
        "--scenario",
        scenario.name,
        "--cache-root",
        cache_root,
        "--output",
        f"{reports}/mutation.json",
    ]
    mutation_exit = 1
    if clone_exit == 0:
        container = await container.with_exec(mutation, expect=ReturnType.ANY).sync()
        mutation_exit = await container.exit_code()
    attempt: _Attempt | None = None
    provenance: dict[str, object] | None = None
    if mutation_exit == 0:
        container, provenance = await _capture_provenance(container, reports)
        if provenance is not None:
            container, attempt = await _run_wrapper(
                container,
                _WrapperRequest(
                    scenario.name,
                    reports,
                    context.diff_base,
                    "full",
                    provenance,
                ),
            )
    decisions = attempt.retry_decisions if attempt is not None else ()
    pytest_results = attempt.pytest_results if attempt is not None else ()
    full_fallback = any("conservative delta coverage" in line for line in decisions)
    expected_exit = bool(
        attempt is not None
        and (attempt.exit_code == 0 if scenario.pytest_must_run else attempt.exit_code != 0)
    )
    pytest_observation = _pytest_observation_matches(
        pytest_results,
        must_run=scenario.pytest_must_run,
    )
    container, cleanup_ok = await _cleanup_cache(container, cache_root)
    passed = bool(
        clone_exit == 0
        and mutation_exit == 0
        and attempt is not None
        and expected_exit
        and any(scenario.expected_decision in line for line in decisions)
        and pytest_observation
        and not full_fallback
        and cleanup_ok
    )
    return container, {
        "scenario": scenario.name,
        "status": "passed" if passed else "failed",
        "cloneExitCode": clone_exit,
        "mutationExitCode": mutation_exit,
        "wrapper": attempt.payload() if attempt is not None else None,
        "candidateProvenance": provenance,
        "expectedDecision": scenario.expected_decision,
        "pytestMustRun": scenario.pytest_must_run,
        "fullFallbackObserved": full_fallback,
        "cacheCleaned": cleanup_ok,
    }


def _pytest_observation_matches(
    pytest_results: tuple[str, ...],
    *,
    must_run: bool,
) -> bool:
    """Distinguish an executed passing pytest rail from an explicit skip."""

    if must_run:
        return any("pytest PASS" in line for line in pytest_results)
    return bool(pytest_results) and all("pytest SKIPPED" in line for line in pytest_results)


def _base_attempt_passes(attempt: _Attempt, *, exact: bool) -> bool:
    decision = "cache-hit exact-candidate" if exact else "cache-miss no-prior-proof"
    return bool(
        attempt.exit_code != 0
        and any(decision in line for line in attempt.retry_decisions)
        and any("pytest PASS" in line for line in attempt.pytest_results)
    )


def _exact_attempts_pass(attempts: list[_Attempt]) -> bool:
    return bool(
        len(attempts) == 2
        and _base_attempt_passes(attempts[0], exact=False)
        and _base_attempt_passes(attempts[1], exact=True)
    )


def _base_candidates_match(attempts: list[_Attempt]) -> bool:
    digests = [_candidate_digest(item.candidate_provenance) for item in attempts]
    return bool(len(digests) == 2 and all(digests) and len(set(digests)) == 1)


def _first_candidate_provenance(attempts: list[_Attempt]) -> dict[str, object] | None:
    return attempts[0].candidate_provenance if attempts else None


def _candidate_digest(provenance: dict[str, object] | None) -> str:
    if provenance is None:
        return ""
    candidate = provenance.get("candidate")
    if not isinstance(candidate, dict):
        return ""
    digest = candidate.get("digest")
    return digest if isinstance(digest, str) else ""


def _scenario_contract(scenario: _Scenario) -> dict[str, object]:
    return {
        "scenario": scenario.name,
        "expectedDecision": scenario.expected_decision,
        "pytestMustRun": scenario.pytest_must_run,
        "contextVariant": scenario.context_variant,
        "retryDisabled": scenario.disable_retry,
    }


async def _clone_cache(
    container: dagger.Container,
    *,
    source_root: str,
    destination_root: str,
) -> tuple[dagger.Container, int]:
    command = [
        "/opt/ar-venv/bin/python",
        "-m",
        "agents_remember_test_support.testing.retry_route_evidence",
        "clone",
        "--source-cache-root",
        source_root,
        "--destination-cache-root",
        destination_root,
    ]
    container = await container.with_exec(command, expect=ReturnType.ANY).sync()
    return container, await container.exit_code()


async def _cleanup_cache(
    container: dagger.Container,
    cache_root: str,
) -> tuple[dagger.Container, bool]:
    command = [
        "/opt/ar-venv/bin/python",
        "-m",
        "agents_remember_test_support.testing.retry_route_evidence",
        "cleanup",
        "--cache-root",
        cache_root,
    ]
    container = await container.with_exec(command, expect=ReturnType.ANY).sync()
    return container, await container.exit_code() == 0
