"""Route-neutral pytest node outcomes and reproducible phase timings."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest

PYTEST_PHASE_REPORT_OPTION = "--ar-pytest-phase-report"
PYTEST_PHASE_REPORT_SCHEMA = "python-pytest-phase-report/v1"


class _Report(Protocol):
    nodeid: str
    outcome: str
    when: str


_IMPORTED_MONOTONIC = time.monotonic()
_IMPORTED_AT = datetime.now(UTC).isoformat()


@dataclass
class _PhaseState:
    session_started_monotonic: float | None = None
    session_started_at: str | None = None
    collection_finished_monotonic: float | None = None
    collection_finished_at: str | None = None
    first_node_started_monotonic: float | None = None
    first_node_started_at: str | None = None
    node_outcomes: dict[str, str] = field(default_factory=dict)


_STATE = _PhaseState()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        PYTEST_PHASE_REPORT_OPTION,
        type=Path,
        default=None,
        help="replace this JSON file with route-neutral pytest phase and node evidence",
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    _STATE.session_started_monotonic = time.monotonic()
    _STATE.session_started_at = datetime.now(UTC).isoformat()
    _STATE.collection_finished_monotonic = None
    _STATE.collection_finished_at = None
    _STATE.first_node_started_monotonic = None
    _STATE.first_node_started_at = None
    _STATE.node_outcomes.clear()


def pytest_collection_finish(session: pytest.Session) -> None:
    del session
    _STATE.collection_finished_monotonic = time.monotonic()
    _STATE.collection_finished_at = datetime.now(UTC).isoformat()


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    del nodeid, location
    if _STATE.first_node_started_monotonic is None:
        _STATE.first_node_started_monotonic = time.monotonic()
        _STATE.first_node_started_at = datetime.now(UTC).isoformat()


def pytest_runtest_logreport(report: _Report) -> None:
    if report.when == "call" or (
        report.when in {"setup", "teardown"} and report.outcome != "passed"
    ):
        _STATE.node_outcomes[report.nodeid] = report.outcome


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if hasattr(session.config, "workerinput"):
        return
    report_path = session.config.getoption(PYTEST_PHASE_REPORT_OPTION)
    if report_path is None:
        return
    reporting_started_monotonic = time.monotonic()
    reporting_started_at = datetime.now(UTC).isoformat()
    payload = _payload(
        exitstatus,
        reporting_started_monotonic=reporting_started_monotonic,
        reporting_started_at=reporting_started_at,
        reporting_finished_monotonic=reporting_started_monotonic,
        reporting_finished_at=reporting_started_at,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    reporting_finished_monotonic = time.monotonic()
    reporting_finished_at = datetime.now(UTC).isoformat()
    payload = _payload(
        exitstatus,
        reporting_started_monotonic=reporting_started_monotonic,
        reporting_started_at=reporting_started_at,
        reporting_finished_monotonic=reporting_finished_monotonic,
        reporting_finished_at=reporting_finished_at,
    )
    report_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _payload(
    exitstatus: int,
    *,
    reporting_started_monotonic: float,
    reporting_started_at: str,
    reporting_finished_monotonic: float,
    reporting_finished_at: str,
) -> dict[str, object]:
    session_started = _STATE.session_started_monotonic
    collection_finished = _STATE.collection_finished_monotonic
    first_node_started = _STATE.first_node_started_monotonic
    return {
        "schemaVersion": PYTEST_PHASE_REPORT_SCHEMA,
        "pytestExitCode": int(exitstatus),
        "timestamps": {
            "reporterImportedAt": _IMPORTED_AT,
            "sessionStartedAt": _STATE.session_started_at,
            "collectionFinishedAt": _STATE.collection_finished_at,
            "firstNodeStartedAt": _STATE.first_node_started_at,
            "reportingStartedAt": reporting_started_at,
            "reportingFinishedAt": reporting_finished_at,
        },
        "phaseSeconds": {
            "bootstrap": _duration(session_started, _IMPORTED_MONOTONIC),
            "collection": _duration(collection_finished, session_started),
            "collectionToFirstNodeStart": _duration(
                first_node_started,
                collection_finished,
            ),
            "execution": (
                0.0
                if collection_finished is not None and first_node_started is None
                else _duration(reporting_started_monotonic, first_node_started)
            ),
            "reporting": _seconds(reporting_finished_monotonic - reporting_started_monotonic),
        },
        "nodes": [
            {"nodeId": node_id, "outcome": outcome}
            for node_id, outcome in _STATE.node_outcomes.items()
        ],
    }


def _duration(finished: float | None, started: float | None) -> float | None:
    if finished is None or started is None:
        return None
    return _seconds(finished - started)


def _seconds(value: float) -> float:
    return round(max(value, 0.0), 6)
