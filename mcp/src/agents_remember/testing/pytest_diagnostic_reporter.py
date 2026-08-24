"""Local-only exact-node outcome report for the canonical diagnostic runner."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

import pytest

DIAGNOSTIC_REPORT_ENV = "AR_PYTEST_DIAGNOSTIC_REPORT"
DIAGNOSTIC_REPORT_SCHEMA = "python-direct-pytest-report/v1"


class _Report(Protocol):
    nodeid: str
    outcome: str
    when: str


_NODE_OUTCOMES: dict[str, str] = {}


def pytest_configure(config: pytest.Config) -> None:
    del config
    if not os.environ.get(DIAGNOSTIC_REPORT_ENV):
        raise pytest.UsageError(
            f"{DIAGNOSTIC_REPORT_ENV} is required by the direct diagnostic reporter"
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    del session
    _NODE_OUTCOMES.clear()


def pytest_runtest_logreport(report: _Report) -> None:
    if report.when == "call" or (
        report.when in {"setup", "teardown"} and report.outcome != "passed"
    ):
        _NODE_OUTCOMES[report.nodeid] = report.outcome


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del session
    report_path = Path(os.environ[DIAGNOSTIC_REPORT_ENV])
    payload = {
        "schemaVersion": DIAGNOSTIC_REPORT_SCHEMA,
        "pytestExitCode": exitstatus,
        "nodes": [
            {"nodeId": node_id, "outcome": outcome} for node_id, outcome in _NODE_OUTCOMES.items()
        ],
    }
    report_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
