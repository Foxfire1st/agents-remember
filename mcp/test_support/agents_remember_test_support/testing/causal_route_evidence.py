"""Non-accepting Dagger proof for exact causal suppression and independent execution."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

from agents_remember.kernel.atomic_write import atomic_write_text

from agents_remember_test_support.code_quality.causal_preflight import (
    PREFLIGHTS,
    PreflightSpec,
    candidate_identity,
    evaluate_preflights,
)
from agents_remember_test_support.testing.causal_failures import (
    load_causal_report,
    write_causal_report,
)
from agents_remember_test_support.testing.dagger_admission import require_dagger_admission
from agents_remember_test_support.testing.pytest_phase_reporter import (
    PYTEST_PHASE_REPORT_SCHEMA,
)

FORCE_ENV = "AR_CAUSAL_EVIDENCE_FORCE_DEPENDENT_FAILURE"
EVIDENCE_SCHEMA = "ar-causal-route-evidence/v2"
DEPENDENT_NODES = (
    "mcp/tests/test_causal_failure_localization.py::"
    "CausalFailureLocalizationTests::test_terminal_operation_preserves_candidate_identity",
    "mcp/tests/test_causal_failure_localization.py::"
    "CausalFailureLocalizationTests::test_terminal_operation_preserves_canonical_repair",
    "mcp/tests/test_causal_failure_localization.py::"
    "CausalFailureLocalizationTests::test_terminal_operation_preserves_integration_authority",
)
SAME_FILE_INDEPENDENT_NODE = (
    "mcp/tests/test_causal_failure_localization.py::"
    "CausalFailureLocalizationTests::test_observed_runtime_failures_retain_exact_retry_inputs"
)
UNRELATED_REVERSE_IMPORTER_NODE = (
    "mcp/tests/test_git_command.py::"
    "RunnerContractTests::test_stdin_is_devnull_unless_input_text_is_given"
)
PROOF_NODES = (
    *DEPENDENT_NODES,
    SAME_FILE_INDEPENDENT_NODE,
    UNRELATED_REVERSE_IMPORTER_NODE,
)


def prepare_forced_report(project_root: Path, report: Path) -> None:
    """Publish one controlled failed-owner report bound to the real candidate."""

    require_dagger_admission(subject="Agents Remember causal-route evidence")
    if os.environ.get(FORCE_ENV) != "1":
        raise RuntimeError(f"{FORCE_ENV}=1 is required for the explicit forcing route")

    def fail() -> None:
        raise RuntimeError("controlled owner incompatibility for non-accepting Dagger evidence")

    root = project_root.resolve()
    spec: PreflightSpec = replace(PREFLIGHTS[0], validator=fail)
    payload = evaluate_preflights((spec,), root, candidate=candidate_identity(root))
    blocked = {
        cast(str, row["nodeId"]) for row in cast(list[dict[str, object]], payload["blockedGroups"])
    }
    if blocked != set(DEPENDENT_NODES):
        raise RuntimeError(f"the controlled dependent population differs: {sorted(blocked)}")
    unexpected = blocked.intersection({SAME_FILE_INDEPENDENT_NODE, UNRELATED_REVERSE_IMPORTER_NODE})
    if unexpected:
        raise RuntimeError(f"independent nodes were falsely classified as causal: {unexpected}")
    report.parent.mkdir(parents=True, exist_ok=True)
    write_causal_report(report, payload)


def verify_route_evidence(
    causal_report: Path,
    baseline_phase_report: Path,
    localized_phase_report: Path,
    output: Path,
) -> None:
    """Verify the real pytest route before publishing the bounded evidence summary."""

    causal = load_causal_report(causal_report)
    baseline_payload = _phase_payload(baseline_phase_report, expected_exit=1)
    localized_payload = _phase_payload(localized_phase_report, expected_exit=0)
    baseline = _phase_outcomes(baseline_payload)
    localized = _phase_outcomes(localized_payload)
    expected_baseline = {
        **dict.fromkeys(DEPENDENT_NODES, "failed"),
        SAME_FILE_INDEPENDENT_NODE: "passed",
        UNRELATED_REVERSE_IMPORTER_NODE: "passed",
    }
    expected_localized = {
        **dict.fromkeys(DEPENDENT_NODES, "skipped"),
        SAME_FILE_INDEPENDENT_NODE: "passed",
        UNRELATED_REVERSE_IMPORTER_NODE: "passed",
    }
    if baseline != expected_baseline:
        raise RuntimeError(f"causal baseline outcomes differ: {baseline}")
    if localized != expected_localized:
        raise RuntimeError(f"localized causal outcomes differ: {localized}")
    runtime = cast(dict[str, object], causal["runtimeEvidence"])
    blocked = cast(list[dict[str, object]], runtime["blockedNodes"])
    if [row["nodeId"] for row in blocked] != list(DEPENDENT_NODES):
        raise RuntimeError(f"runtime blocked-node population differs: {blocked}")
    if runtime["independentFailures"]:
        raise RuntimeError("localized route reported an unexpected independent failure")
    payload = {
        "schemaVersion": EVIDENCE_SCHEMA,
        "status": "passed",
        "acceptanceEligible": False,
        "candidate": causal["candidate"],
        "firstCausalFailure": causal["firstCausalFailure"],
        "evidenceAltitude": cast(list[dict[str, object]], causal["preflights"])[0][
            "evidenceAltitude"
        ],
        "correctiveOwner": cast(list[dict[str, object]], causal["preflights"])[0][
            "correctiveOwner"
        ],
        "baseline": {
            "executedNodes": len(PROOF_NODES),
            "failedSymptoms": len(DEPENDENT_NODES),
            "firstActionableTargets": list(DEPENDENT_NODES),
            "symptomLevelEditTargets": len(DEPENDENT_NODES),
            "validationRerunsForOneEditPerTarget": len(DEPENDENT_NODES),
            "phaseSeconds": baseline_payload["phaseSeconds"],
            "outcomes": baseline,
        },
        "localized": {
            "executedNodes": 2,
            "blockedSymptoms": len(DEPENDENT_NODES),
            "independentNodesPreserved": 2,
            "firstActionableTargets": [
                cast(list[dict[str, object]], causal["preflights"])[0]["correctiveOwner"]
            ],
            "symptomLevelEditTargets": 0,
            "ownerLevelEditTargets": 1,
            "validationRerunsForOneEditPerTarget": 1,
            "phaseSeconds": localized_payload["phaseSeconds"],
            "outcomes": localized,
        },
        "repairAmplification": {
            "symptomExecutionsAvoided": len(DEPENDENT_NODES),
            "symptomLevelEditTargetsAvoided": len(DEPENDENT_NODES),
            "validationRerunsAvoidedByPresentedTargetProtocol": len(DEPENDENT_NODES) - 1,
            "firstRepairTarget": causal["firstCausalFailure"],
        },
        "limitations": [
            "Edit and validation-rerun counts are the explicit one-edit-per-presented-target protocol, not observed human behavior.",
            "Phase timings compare the same five-node serial population in one Dagger environment; the localized route performs report loading and skip publication that the baseline does not.",
            "This artifact is non-accepting and proves one reproducible owner cascade, not every future failure topology.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def run_proof_population(
    project_root: Path,
    phase_report: Path,
    causal_report: Path | None,
) -> int:
    """Run the five-node cascade population through certifying pytest composition."""

    require_dagger_admission(subject="Agents Remember causal-route evidence")
    if os.environ.get(FORCE_ENV) != "1":
        raise RuntimeError(f"{FORCE_ENV}=1 is required for the explicit forcing route")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-n=0",
        "-p",
        "agents_remember_test_support.testing.pytest_phase_reporter",
        "--ar-pytest-phase-report",
        phase_report.as_posix(),
    ]
    if causal_report is not None:
        command += ["--ar-causal-failure-report", causal_report.as_posix()]
    command.extend(PROOF_NODES)
    completed = subprocess.run(
        command,
        cwd=project_root.resolve(),
        env=dict(os.environ),
        check=False,
    )
    return completed.returncode


def _phase_payload(path: Path, *, expected_exit: int) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"pytest phase evidence is unavailable: {error}") from error
    if not isinstance(raw, dict) or raw.get("schemaVersion") != PYTEST_PHASE_REPORT_SCHEMA:
        raise RuntimeError("pytest phase evidence schema is invalid")
    if raw.get("pytestExitCode") != expected_exit:
        raise RuntimeError(f"pytest phase evidence expected exit {expected_exit}")
    if not isinstance(raw.get("phaseSeconds"), dict):
        raise RuntimeError("pytest phase timing evidence is invalid")
    return cast(dict[str, object], raw)


def _phase_outcomes(payload: dict[str, object]) -> dict[str, str]:
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise RuntimeError("pytest phase node population is invalid")
    outcomes: dict[str, str] = {}
    for value in nodes:
        if not isinstance(value, dict):
            raise RuntimeError("pytest phase node row is invalid")
        node_id = value.get("nodeId")
        outcome = value.get("outcome")
        if not isinstance(node_id, str) or not isinstance(outcome, str):
            raise RuntimeError("pytest phase node identity is invalid")
        if node_id in outcomes:
            raise RuntimeError(f"pytest phase evidence duplicates node {node_id}")
        outcomes[node_id] = outcome
    return outcomes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--project-root", type=Path, required=True)
    prepare.add_argument("--causal-report", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--project-root", type=Path, required=True)
    run.add_argument("--phase-report", type=Path, required=True)
    run.add_argument("--causal-report", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--causal-report", type=Path, required=True)
    verify.add_argument("--baseline-phase-report", type=Path, required=True)
    verify.add_argument("--localized-phase-report", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        prepare_forced_report(args.project_root, args.causal_report)
    elif args.command == "run":
        return run_proof_population(
            args.project_root,
            args.phase_report,
            args.causal_report,
        )
    else:
        verify_route_evidence(
            args.causal_report,
            args.baseline_phase_report,
            args.localized_phase_report,
            args.output,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
