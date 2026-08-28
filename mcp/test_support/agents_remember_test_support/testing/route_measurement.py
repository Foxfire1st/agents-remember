"""Measure representative pytest populations on one Dagger-admitted candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.atomic_write import atomic_write_text

from agents_remember_test_support.testing.candidate_snapshot import candidate_snapshot
from agents_remember_test_support.testing.dagger_admission import require_dagger_admission
from agents_remember_test_support.testing.evidence_lifecycle import EvidenceCategory
from agents_remember_test_support.testing.evidence_provenance import capture_provenance
from agents_remember_test_support.testing.lane_manifest import LaneManifest, load_lane_manifest
from agents_remember_test_support.testing.pytest_phase_reporter import (
    PYTEST_PHASE_REPORT_OPTION,
    PYTEST_PHASE_REPORT_SCHEMA,
)

SCHEMA_VERSION = "ar-representative-route-measurement/v1"


class RouteMeasurementError(RuntimeError):
    """A run or its evidence cannot support the declared comparison."""


@dataclass(frozen=True)
class MeasurementCohort:
    """An exact representative population with one evidence owner."""

    name: str
    category: EvidenceCategory
    nodes: tuple[str, ...]


@dataclass(frozen=True)
class MeasurementTopology:
    """One supported pytest process topology."""

    name: str
    workers: str


@dataclass(frozen=True)
class RunSpec:
    """One member of a balanced cold/warm measurement sequence."""

    ordinal: int
    pair: int
    cache_state: str
    cohort: MeasurementCohort
    topology: MeasurementTopology


@dataclass(frozen=True)
class RunResult:
    """Raw result and artifact references for one exact pytest command."""

    spec: RunSpec
    command: tuple[str, ...]
    exit_code: int
    wall_seconds: float
    phase: Mapping[str, object]
    phase_artifact: Mapping[str, object]
    log_artifact: Mapping[str, object]


COHORTS = (
    MeasurementCohort(
        "pure",
        EvidenceCategory.UNIT_REGRESSION,
        (
            "mcp/tests/test_kernel_pure_regressions.py::"
            "test_stable_provider_id_never_returns_empty",
            "mcp/tests/test_kernel_pure_regressions.py::test_known_gate_kind_passes_through",
            "mcp/tests/test_kernel_pure_regressions.py::test_unknown_gate_kind_is_refused",
            "mcp/tests/test_kernel_pure_regressions.py::test_known_decision_role_passes_through",
            "mcp/tests/test_kernel_pure_regressions.py::"
            "test_unknown_decision_role_is_refused_by_name",
            "mcp/tests/test_kernel_pure_regressions.py::test_normalize_route_root_forms",
            "mcp/tests/test_kernel_pure_regressions.py::"
            "test_normalize_route_strips_slashes_and_backticks",
        ),
    ),
    MeasurementCohort(
        "integration",
        EvidenceCategory.INTEGRATION,
        (
            "mcp/tests/test_git_command.py::DecoyRepositoryTests::"
            "test_a_commit_lands_in_the_real_repository_not_the_decoy",
        ),
    ),
    MeasurementCohort(
        "durability",
        EvidenceCategory.STRESS_DURABILITY,
        (
            "mcp/tests/test_provider_store_durability.py::ProviderStoreDurabilityTests::"
            "test_no_record_is_lost_under_sustained_multi_process_write_and_compaction",
        ),
    ),
)

TOPOLOGIES = (
    MeasurementTopology("serial", "0"),
    MeasurementTopology("default-xdist", "auto"),
)


def measure_representative_routes(
    project_root: Path,
    *,
    output: Path,
    repetitions: int,
) -> dict[str, object]:
    """Run paired cold/warm observations for every cohort and topology."""

    if repetitions < 2:
        raise ValueError("repetitions must be at least 2 for medians and ranges")
    require_dagger_admission(subject="representative route measurement")
    root = project_root.resolve()
    output = output.resolve()
    manifest = load_lane_manifest(root)
    _validate_cohorts(manifest)
    provenance = capture_provenance(root)
    before = candidate_snapshot(root)
    run_root = Path("/tmp/ar-representative-route-measurement")
    shutil.rmtree(run_root, ignore_errors=True)
    raw_root = output.parent / "representative-route-measurement-raw"
    shutil.rmtree(raw_root, ignore_errors=True)
    raw_root.mkdir(parents=True)
    environment = _measurement_environment()
    results = [
        _run_spec(root, run_root, raw_root, environment, spec) for spec in _run_specs(repetitions)
    ]
    after = candidate_snapshot(root)
    if before != after:
        raise RouteMeasurementError("measurement changed the exact Git working candidate")
    payload = _measurement_payload(provenance, manifest.digest, results, repetitions)
    atomic_write_text(output, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def _validate_cohorts(manifest: LaneManifest) -> None:
    for cohort in COHORTS:
        for node in cohort.nodes:
            actual = manifest.category_for_node(node)
            if actual is not cohort.category:
                raise RouteMeasurementError(
                    f"{node}: expected {cohort.category.value}, found {actual.value}"
                )


def _run_specs(repetitions: int) -> tuple[RunSpec, ...]:
    specs: list[RunSpec] = []
    ordinal = 0
    for cohort in COHORTS:
        for pair in range(1, repetitions + 1):
            ordered = TOPOLOGIES if pair % 2 else tuple(reversed(TOPOLOGIES))
            for topology in ordered:
                for cache_state in ("cold", "warm"):
                    ordinal += 1
                    specs.append(RunSpec(ordinal, pair, cache_state, cohort, topology))
    return tuple(specs)


def _run_spec(
    root: Path,
    run_root: Path,
    raw_root: Path,
    environment: Mapping[str, str],
    spec: RunSpec,
) -> RunResult:
    cache_root = run_root / spec.cohort.name / spec.topology.name / f"pair-{spec.pair}"
    if spec.cache_state == "cold":
        shutil.rmtree(cache_root, ignore_errors=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    stem = f"{spec.ordinal:02d}-{spec.cohort.name}-{spec.topology.name}-{spec.cache_state}"
    phase_path = raw_root / f"{stem}-phases.json"
    log_path = raw_root / f"{stem}.log"
    command = _pytest_command(root, cache_root, phase_path, spec)
    started = time.perf_counter()
    completed = subprocess.run(
        list(command),
        cwd=root,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    wall_seconds = round(time.perf_counter() - started, 6)
    _write_run_log(log_path, command, completed)
    phase = _load_phase_report(phase_path, completed.returncode)
    _validate_run(spec, phase, completed)
    return RunResult(
        spec,
        command,
        completed.returncode,
        wall_seconds,
        phase,
        _artifact_ref(phase_path, output_root=raw_root.parent),
        _artifact_ref(log_path, output_root=raw_root.parent),
    )


def _pytest_command(
    root: Path,
    cache_root: Path,
    phase_path: Path,
    spec: RunSpec,
) -> tuple[str, ...]:
    del root
    return (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        f"-n={spec.topology.workers}",
        "-o",
        "addopts=",
        "-o",
        f"cache_dir={(cache_root / 'pytest-cache').as_posix()}",
        "--strict-config",
        "--strict-markers",
        "--basetemp",
        (cache_root / f"basetemp-{spec.cache_state}").as_posix(),
        "-p",
        "agents_remember_test_support.testing.pytest_phase_reporter",
        PYTEST_PHASE_REPORT_OPTION,
        phase_path.as_posix(),
        *spec.cohort.nodes,
    )


def _measurement_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _write_run_log(
    path: Path,
    command: Sequence[str],
    completed: subprocess.CompletedProcess[str],
) -> None:
    rendered = " ".join(shlex.quote(part) for part in command)
    atomic_write_text(
        path,
        f"command: {rendered}\nexitCode: {completed.returncode}\n"
        f"\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
    )


def _load_phase_report(path: Path, expected_exit: int) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RouteMeasurementError(f"pytest phase report is unavailable: {error}") from error
    if not isinstance(raw, dict) or raw.get("schemaVersion") != PYTEST_PHASE_REPORT_SCHEMA:
        raise RouteMeasurementError("pytest phase report schema is invalid")
    if raw.get("pytestExitCode") != expected_exit:
        raise RouteMeasurementError("pytest phase report exit differs from the process")
    return raw


def _validate_run(
    spec: RunSpec,
    phase: Mapping[str, object],
    completed: subprocess.CompletedProcess[str],
) -> None:
    if completed.returncode != 0:
        raise RouteMeasurementError(
            f"{spec.cohort.name}/{spec.topology.name}/{spec.cache_state} failed; "
            "inspect its content-addressed raw log"
        )
    raw_nodes = phase.get("nodes")
    if not isinstance(raw_nodes, list):
        raise RouteMeasurementError("pytest phase report omitted node outcomes")
    outcomes = {
        item.get("nodeId"): item.get("outcome") for item in raw_nodes if isinstance(item, dict)
    }
    if outcomes != {node: "passed" for node in spec.cohort.nodes}:
        raise RouteMeasurementError(
            f"{spec.cohort.name}/{spec.topology.name}: exact-node parity failed: {outcomes}"
        )
    population = phase.get("population")
    if not isinstance(population, dict) or population.get("selected") != len(spec.cohort.nodes):
        raise RouteMeasurementError("pytest phase report selected population is not exact")
    workers = population.get("xdistWorkers")
    if spec.topology.workers == "0" and workers != 0:
        raise RouteMeasurementError("serial measurement unexpectedly started xdist workers")
    if spec.topology.workers == "auto" and (
        not isinstance(workers, int) or isinstance(workers, bool) or workers < 1
    ):
        raise RouteMeasurementError("default-xdist measurement started no workers")
    _required_timings(phase)


def _required_timings(phase: Mapping[str, object]) -> dict[str, float]:
    raw = phase.get("phaseSeconds")
    if not isinstance(raw, dict):
        raise RouteMeasurementError("pytest phase report omitted phase timings")
    timings: dict[str, float] = {}
    for name in (
        "bootstrap",
        "collection",
        "collectionToFirstNodeStart",
        "execution",
        "reporting",
    ):
        value = raw.get(name)
        if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
            raise RouteMeasurementError(f"pytest phase timing {name} is invalid")
        timings[name] = float(value)
    return timings


def _measurement_payload(
    provenance: Mapping[str, object],
    manifest_digest: str,
    results: Sequence[RunResult],
    repetitions: int,
) -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "passed",
        "acceptanceEligible": False,
        "certifying": False,
        "candidateProvenance": dict(provenance),
        "laneManifestDigest": manifest_digest,
        "cohorts": [
            {
                "name": cohort.name,
                "category": cohort.category.value,
                "nodeCount": len(cohort.nodes),
                "nodes": list(cohort.nodes),
            }
            for cohort in COHORTS
        ],
        "method": {
            "repetitionsPerCacheState": repetitions,
            "coldDefinition": (
                "a new pytest cache and basetemp pair inside the same admitted Dagger candidate"
            ),
            "warmDefinition": (
                "the immediately following run reuses only that pair's pytest cache; basetemp "
                "remains isolated"
            ),
            "ordering": (
                "cold then warm within each pair; serial/default-xdist lead alternates by pair"
            ),
            "phaseDefinitions": {
                "bootstrap": "phase-reporter import through pytest session start, including admission",
                "collection": "session start through exact-node collection",
                "firstNode": "bootstrap + collection + collection-to-first-node-start",
                "execution": "first selected node start through report publication",
                "reporting": "phase-report serialization",
            },
            "topologies": [
                {"name": topology.name, "pytestWorkersArgument": topology.workers}
                for topology in TOPOLOGIES
            ],
        },
        "runs": [_run_payload(result) for result in results],
        "summaries": _summaries(results),
        "limitations": [
            "These are focused non-accepting observations; the final full Dagger gate owns acceptance.",
            "Cold means pytest-owned cache cold, not a purged host kernel page cache or package cache.",
            "The immutable Dagger image and installed dependency layer are common controls.",
            "Historical measurements lacking this provenance contract are not synthesized or compared.",
            "One selected stress node represents durability cost; the cadence and release routes own the full lane.",
        ],
    }


def _run_payload(result: RunResult) -> dict[str, object]:
    timings = _required_timings(result.phase)
    population = result.phase["population"]
    return {
        "ordinal": result.spec.ordinal,
        "pair": result.spec.pair,
        "cacheState": result.spec.cache_state,
        "cohort": result.spec.cohort.name,
        "category": result.spec.cohort.category.value,
        "topology": result.spec.topology.name,
        "command": list(result.command),
        "exitCode": result.exit_code,
        "wallSeconds": result.wall_seconds,
        "timeToFirstNodeStartSeconds": round(
            timings["bootstrap"] + timings["collection"] + timings["collectionToFirstNodeStart"],
            6,
        ),
        "phaseSeconds": timings,
        "population": population,
        "nodes": result.phase["nodes"],
        "artifacts": {
            "phaseReport": dict(result.phase_artifact),
            "processLog": dict(result.log_artifact),
        },
    }


def _summaries(results: Sequence[RunResult]) -> dict[str, object]:
    summaries: dict[str, object] = {}
    for cohort in COHORTS:
        cohort_summary: dict[str, object] = {}
        for topology in TOPOLOGIES:
            topology_results = [
                result
                for result in results
                if result.spec.cohort is cohort and result.spec.topology is topology
            ]
            topology_summary: dict[str, object] = {}
            for cache_state in ("cold", "warm"):
                selected = [
                    result for result in topology_results if result.spec.cache_state == cache_state
                ]
                topology_summary[cache_state] = _summary_for(selected)
            cohort_summary[topology.name] = topology_summary
        summaries[cohort.name] = cohort_summary
    return summaries


def _summary_for(results: Sequence[RunResult]) -> dict[str, object]:
    metrics: dict[str, list[float]] = {
        "wallSeconds": [result.wall_seconds for result in results],
        "timeToFirstNodeStartSeconds": [],
        "bootstrapSeconds": [],
        "collectionSeconds": [],
        "collectionToFirstNodeStartSeconds": [],
        "executionSeconds": [],
        "reportingSeconds": [],
    }
    for result in results:
        timings = _required_timings(result.phase)
        metrics["timeToFirstNodeStartSeconds"].append(
            timings["bootstrap"] + timings["collection"] + timings["collectionToFirstNodeStart"]
        )
        for phase in (
            "bootstrap",
            "collection",
            "collectionToFirstNodeStart",
            "execution",
            "reporting",
        ):
            metrics[f"{phase}Seconds"].append(timings[phase])
    return {name: _distribution(values) for name, values in metrics.items()}


def _distribution(values: Sequence[float]) -> dict[str, object]:
    if len(values) < 2:
        raise RouteMeasurementError("every measurement distribution requires repeated values")
    return {
        "samples": [round(value, 6) for value in values],
        "median": round(statistics.median(values), 6),
        "range": [round(min(values), 6), round(max(values), 6)],
    }


def _artifact_ref(path: Path, *, output_root: Path) -> dict[str, object]:
    try:
        content = path.read_bytes()
        relative = path.relative_to(output_root)
    except (OSError, ValueError) as error:
        raise RouteMeasurementError(
            f"measurement artifact is unavailable: {path}: {error}"
        ) from error
    return {
        "path": relative.as_posix(),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        measure_representative_routes(
            args.project_root,
            output=args.output,
            repetitions=args.repetitions,
        )
    except (OSError, RouteMeasurementError, ValueError) as error:
        print(f"representative-route-measurement: FAIL ({error})", file=sys.stderr)
        return 1
    print(
        "representative-route-measurement: PASS "
        f"({args.repetitions} cold and warm observations per cohort/topology)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
