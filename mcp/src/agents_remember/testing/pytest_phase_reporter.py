"""Route-neutral pytest node outcomes and reproducible phase timings."""

from __future__ import annotations

import json
import time
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
_SESSION_STARTED_MONOTONIC: float | None = None
_SESSION_STARTED_AT: str | None = None
_COLLECTION_FINISHED_MONOTONIC: float | None = None
_COLLECTION_FINISHED_AT: str | None = None
_FIRST_NODE_STARTED_MONOTONIC: float | None = None
_FIRST_NODE_STARTED_AT: str | None = None
_NODE_OUTCOMES: dict[str, str] = {}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        PYTEST_PHASE_REPORT_OPTION,
        type=Path,
        default=None,
        help="replace this JSON file with route-neutral pytest phase and node evidence",
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    global _SESSION_STARTED_MONOTONIC, _SESSION_STARTED_AT  # noqa: PLW0603
    global _COLLECTION_FINISHED_MONOTONIC, _COLLECTION_FINISHED_AT  # noqa: PLW0603
    global _FIRST_NODE_STARTED_MONOTONIC, _FIRST_NODE_STARTED_AT  # noqa: PLW0603
    _SESSION_STARTED_MONOTONIC = time.monotonic()
    _SESSION_STARTED_AT = datetime.now(UTC).isoformat()
    _COLLECTION_FINISHED_MONOTONIC = None
    _COLLECTION_FINISHED_AT = None
    _FIRST_NODE_STARTED_MONOTONIC = None
    _FIRST_NODE_STARTED_AT = None
    _NODE_OUTCOMES.clear()


def pytest_collection_finish(session: pytest.Session) -> None:
    del session
    global _COLLECTION_FINISHED_MONOTONIC, _COLLECTION_FINISHED_AT  # noqa: PLW0603
    _COLLECTION_FINISHED_MONOTONIC = time.monotonic()
    _COLLECTION_FINISHED_AT = datetime.now(UTC).isoformat()


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    del nodeid, location
    global _FIRST_NODE_STARTED_MONOTONIC, _FIRST_NODE_STARTED_AT  # noqa: PLW0603
    if _FIRST_NODE_STARTED_MONOTONIC is None:
        _FIRST_NODE_STARTED_MONOTONIC = time.monotonic()
        _FIRST_NODE_STARTED_AT = datetime.now(UTC).isoformat()


def pytest_runtest_logreport(report: _Report) -> None:
    if report.when == "call" or (
        report.when in {"setup", "teardown"} and report.outcome != "passed"
    ):
        _NODE_OUTCOMES[report.nodeid] = report.outcome


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
    session_started = _required_phase(_SESSION_STARTED_MONOTONIC, "session start")
    collection_finished = _required_phase(
        _COLLECTION_FINISHED_MONOTONIC,
        "collection finish",
    )
    first_node_started = _FIRST_NODE_STARTED_MONOTONIC
    return {
        "schemaVersion": PYTEST_PHASE_REPORT_SCHEMA,
        "pytestExitCode": int(exitstatus),
        "timestamps": {
            "reporterImportedAt": _IMPORTED_AT,
            "sessionStartedAt": _SESSION_STARTED_AT,
            "collectionFinishedAt": _COLLECTION_FINISHED_AT,
            "firstNodeStartedAt": _FIRST_NODE_STARTED_AT,
            "reportingStartedAt": reporting_started_at,
            "reportingFinishedAt": reporting_finished_at,
        },
        "phaseSeconds": {
            "bootstrap": _seconds(session_started - _IMPORTED_MONOTONIC),
            "collection": _seconds(collection_finished - session_started),
            "collectionToFirstNodeStart": (
                None
                if first_node_started is None
                else _seconds(first_node_started - collection_finished)
            ),
            "execution": (
                0.0
                if first_node_started is None
                else _seconds(reporting_started_monotonic - first_node_started)
            ),
            "reporting": _seconds(
                reporting_finished_monotonic - reporting_started_monotonic
            ),
        },
        "nodes": [
            {"nodeId": node_id, "outcome": outcome}
            for node_id, outcome in _NODE_OUTCOMES.items()
        ],
    }


def _required_phase(value: float | None, name: str) -> float:
    if value is None:
        raise pytest.UsageError(f"pytest phase reporter did not observe {name}")
    return value


def _seconds(value: float) -> float:
    return round(max(value, 0.0), 6)
