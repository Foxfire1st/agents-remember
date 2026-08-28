"""Focused contract tests for representative Dagger route measurement."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast

import pytest
from agents_remember_test_support.testing import route_measurement


def _phase(spec: route_measurement.RunSpec) -> dict[str, object]:
    workers = 0 if spec.topology.workers == "0" else 4
    return {
        "schemaVersion": "python-pytest-phase-report/v1",
        "pytestExitCode": 0,
        "phaseSeconds": {
            "bootstrap": 1.0,
            "collection": 2.0,
            "collectionToFirstNodeStart": 0.5,
            "execution": 3.0,
            "reporting": 0.1,
        },
        "population": {
            "collected": len(spec.cohort.nodes),
            "selected": len(spec.cohort.nodes),
            "deselected": 0 if workers == 0 else None,
            "reported": len(spec.cohort.nodes),
            "xdistWorkers": workers,
            "xdistCollectionConsistent": True,
        },
        "nodes": [{"nodeId": node, "outcome": "passed"} for node in spec.cohort.nodes],
    }


def _result(spec: route_measurement.RunSpec) -> route_measurement.RunResult:
    artifact = {"path": "raw.json", "bytes": 1, "sha256": "a" * 64}
    return route_measurement.RunResult(
        spec=spec,
        command=("python", "-m", "pytest", *spec.cohort.nodes),
        exit_code=0,
        wall_seconds=float(spec.ordinal),
        phase=_phase(spec),
        phase_artifact=artifact,
        log_artifact=artifact,
    )


def test_measurement_matrix_repeats_cold_and_warm_for_every_exact_population() -> None:
    specs = route_measurement._run_specs(2)

    assert len(specs) == 24
    assert {(spec.cohort.name, spec.topology.name, spec.cache_state) for spec in specs} == {
        (cohort.name, topology.name, cache_state)
        for cohort in route_measurement.COHORTS
        for topology in route_measurement.TOPOLOGIES
        for cache_state in ("cold", "warm")
    }
    for cohort in route_measurement.COHORTS:
        selected = [spec for spec in specs if spec.cohort is cohort]
        assert [spec.topology.name for spec in selected[0:4:2]] == [
            "serial",
            "default-xdist",
        ]
        assert [spec.topology.name for spec in selected[4:8:2]] == [
            "default-xdist",
            "serial",
        ]


def test_run_validation_requires_exact_passed_nodes_and_actual_topology() -> None:
    serial_spec = route_measurement._run_specs(2)[0]
    completed = subprocess.CompletedProcess(["pytest"], 0, "", "")

    route_measurement._validate_run(serial_spec, _phase(serial_spec), completed)
    mismatched = _phase(serial_spec)
    mismatched["nodes"] = []
    with pytest.raises(route_measurement.RouteMeasurementError, match="parity failed"):
        route_measurement._validate_run(serial_spec, mismatched, completed)


def test_measurement_payload_keeps_raw_runs_distributions_and_limitations() -> None:
    results = [_result(spec) for spec in route_measurement._run_specs(2)]

    payload = route_measurement._measurement_payload(
        {"candidate": {"candidateTree": "tree"}, "environmentId": "environment"},
        "manifest",
        results,
        2,
    )

    assert payload["acceptanceEligible"] is False
    assert len(cast(list[object], payload["runs"])) == 24
    summaries = cast(dict[str, object], payload["summaries"])
    pure = cast(dict[str, object], summaries["pure"])
    pure_serial = cast(dict[str, object], pure["serial"])
    cold = cast(dict[str, object], pure_serial["cold"])
    warm = cast(dict[str, object], pure_serial["warm"])
    cold_wall = cast(dict[str, object], cold["wallSeconds"])
    warm_wall = cast(dict[str, object], warm["wallSeconds"])
    assert len(cast(list[float], cold_wall["samples"])) == 2
    assert len(cast(list[float], warm_wall["range"])) == 2
    assert "package cache" in " ".join(cast(list[str], payload["limitations"]))


def test_artifact_reference_is_content_addressed_and_confined(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    artifact = raw / "run.log"
    artifact.write_text("result", encoding="utf-8")

    reference = route_measurement._artifact_ref(artifact, output_root=tmp_path)

    assert reference["path"] == "raw/run.log"
    assert reference["bytes"] == 6
    assert len(cast(str, reference["sha256"])) == 64


def test_repetitions_below_two_are_refused_before_admission(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 2"):
        route_measurement.measure_representative_routes(
            tmp_path,
            output=tmp_path / "result.json",
            repetitions=1,
        )
