"""Typed causal-failure artifacts and pytest-side dependency suppression."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

import pytest

from agents_remember.kernel.atomic_write import atomic_write_text

CAUSAL_REPORT_OPTION = "--ar-causal-failure-report"
CAUSAL_REPORT_SCHEMA = "python-causal-failures/v1"


class FailureClass(StrEnum):
    """Repair/retry classes that must not be conflated in reports."""

    SHARED_DEPENDENCY = "deterministic-shared-dependency"
    PROCESS_ENVIRONMENT = "process-environment-sensitive"
    INDEPENDENT = "independent-deterministic-or-unclassified"


@dataclass(frozen=True)
class ExecutionProfile:
    failure_class: FailureClass
    families: tuple[str, ...]
    retry_semantics: str


class _Report(Protocol):
    nodeid: str
    outcome: str
    when: str
    user_properties: list[tuple[str, object]]
    duration: float
    start: float
    stop: float


@dataclass
class _RuntimeState:
    report_path: Path | None = None
    blocked_nodes: dict[str, str] = field(default_factory=dict)
    independent_failures: dict[str, dict[str, object]] = field(default_factory=dict)


_STATE = _RuntimeState()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        CAUSAL_REPORT_OPTION,
        type=Path,
        default=None,
        help="enrich this owner-preflight artifact with blocked and independent node outcomes",
    )


def pytest_configure(config: pytest.Config) -> None:
    _STATE.report_path = config.getoption(CAUSAL_REPORT_OPTION)
    _STATE.blocked_nodes.clear()
    _STATE.independent_failures.clear()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Block only files with a declared graph edge to a failed owner."""

    report_path = config.getoption(CAUSAL_REPORT_OPTION)
    payload = load_causal_report(report_path) if report_path is not None else None
    blocked = _blocked_by_test_path(payload)
    root = Path(str(config.rootpath)).resolve()
    seed = config.getoption("random_order_seed", default=None)
    for item in items:
        relative = Path(item.path).resolve().relative_to(root).as_posix()
        profile = execution_profile(Path(item.path))
        item.user_properties.extend(
            (
                ("arFailureClass", profile.failure_class.value),
                ("arFailureFamilies", ",".join(profile.families)),
                ("arRetrySemantics", profile.retry_semantics),
                ("arRandomOrderSeed", "" if seed is None else str(seed)),
            )
        )
        cause = blocked.get(relative)
        if cause is None:
            continue
        cause_id = _required_string(cause, "causeId")
        owner = _required_string(cause, "correctiveOwner")
        item.user_properties.extend((("arCausalCauseId", cause_id), ("arCausalOwner", owner)))
        item.add_marker(
            pytest.mark.skip(
                reason=f"blocked by causal preflight {cause_id}; corrective owner: {owner}"
            )
        )


def pytest_runtest_logreport(report: _Report) -> None:
    properties = dict(report.user_properties)
    cause_id = properties.get("arCausalCauseId")
    if report.outcome == "skipped" and isinstance(cause_id, str):
        _STATE.blocked_nodes[report.nodeid] = cause_id
        return
    if report.outcome != "failed":
        return
    _STATE.independent_failures[report.nodeid] = runtime_failure_record(report)


def runtime_failure_record(report: _Report) -> dict[str, object]:
    """Preserve the exact retry inputs for one non-causally-blocked failure."""

    properties = dict(report.user_properties)
    return {
        "nodeId": report.nodeid,
        "when": report.when,
        "failureClass": str(properties.get("arFailureClass", FailureClass.INDEPENDENT.value)),
        "failureFamilies": _split_families(properties.get("arFailureFamilies")),
        "retrySemantics": str(
            properties.get("arRetrySemantics", "reproduce-exact-node-before-repair")
        ),
        "randomOrderSeed": str(properties.get("arRandomOrderSeed", "")),
        "workerId": str(getattr(report, "worker_id", "controller")),
        "durationSeconds": round(max(float(report.duration), 0.0), 6),
        "startedAtEpoch": float(report.start),
        "finishedAtEpoch": float(report.stop),
    }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if hasattr(session.config, "workerinput") or _STATE.report_path is None:
        return
    payload = load_causal_report(_STATE.report_path)
    payload["runtimeEvidence"] = {
        "pytestExitCode": int(exitstatus),
        "blockedNodes": [
            {"nodeId": node_id, "causeId": cause_id}
            for node_id, cause_id in sorted(_STATE.blocked_nodes.items())
        ],
        "independentFailures": [value for _, value in sorted(_STATE.independent_failures.items())],
    }
    payload["acceptanceEligible"] = False
    write_causal_report(_STATE.report_path, payload)


def execution_profile(path: Path) -> ExecutionProfile:
    """Classify reproduction semantics from owned execution primitives, not error text."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return ExecutionProfile(
            FailureClass.INDEPENDENT,
            ("unclassified-source",),
            "reproduce-exact-node-before-repair",
        )
    modules = _imported_modules(tree)
    families = tuple(
        family
        for family, prefixes in _SENSITIVE_IMPORTS
        if any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in modules
            for prefix in prefixes
        )
    )
    if families:
        return ExecutionProfile(
            FailureClass.PROCESS_ENVIRONMENT,
            families,
            "repeat-exact-node-with-seed-worker-timing-and-process-topology",
        )
    return ExecutionProfile(
        FailureClass.INDEPENDENT,
        (),
        "reproduce-exact-node-before-repair",
    )


_SENSITIVE_IMPORTS = (
    ("async-concurrency", ("asyncio", "anyio", "trio")),
    ("process-control", ("multiprocessing", "subprocess", "pty", "signal", "resource")),
    (
        "socket-service",
        ("socket", "ssl", "httpx", "uvicorn", "starlette", "fastapi", "websockets"),
    ),
    ("provider-environment", ("dagger", "docker", "agents_remember.providers")),
)


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def load_causal_report(path: Path) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise pytest.UsageError(f"causal preflight report is unavailable: {error}") from error
    if not isinstance(raw, dict) or raw.get("schemaVersion") != CAUSAL_REPORT_SCHEMA:
        raise pytest.UsageError("causal preflight report schema is invalid")
    if raw.get("status") not in {"passed", "failed"}:
        raise pytest.UsageError("causal preflight report status is invalid")
    if not isinstance(raw.get("preflights"), list) or not isinstance(
        raw.get("blockedGroups"), list
    ):
        raise pytest.UsageError("causal preflight report populations are invalid")
    return cast(dict[str, object], raw)


def write_causal_report(path: Path, payload: dict[str, object]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    atomic_write_text(path.with_suffix(".md"), render_causal_report(payload))


def render_causal_report(payload: dict[str, object]) -> str:
    candidate = cast(dict[str, object], payload.get("candidate", {}))
    preflights = cast(list[dict[str, object]], payload.get("preflights", []))
    blocked = cast(list[dict[str, object]], payload.get("blockedGroups", []))
    runtime = cast(dict[str, object], payload.get("runtimeEvidence", {}))
    failures = cast(list[dict[str, object]], runtime.get("independentFailures", []))
    lines = [
        "# Causal failure localization",
        "",
        f"- Candidate tree: `{candidate.get('tree', 'unavailable')}`",
        f"- Environment: `{candidate.get('environmentId', 'unavailable')}`",
        f"- Preflight status: **{payload.get('status', 'invalid')}**",
        "- Acceptance authority: **not granted by this artifact**",
        "",
        "## Owner preflights",
        "",
    ]
    for row in preflights:
        lines.append(
            f"- `{row.get('causeId')}` — {row.get('status')}; owner "
            f"`{row.get('correctiveOwner')}`; {row.get('detail')}"
        )
    lines += ["", "## Causally blocked groups", ""]
    lines += [
        f"- `{row.get('testPath')}` via `{row.get('causeId')}`: "
        + " -> ".join(cast(list[str], row.get("dependencyChain", [])))
        for row in blocked
    ] or ["- None."]
    lines += ["", "## Independent failures", ""]
    lines += [
        f"- `{row.get('nodeId')}` — {row.get('failureClass')}; retry: {row.get('retrySemantics')}"
        for row in failures
    ] or ["- None recorded."]
    return "\n".join(lines) + "\n"


def _blocked_by_test_path(
    payload: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    if payload is None or payload.get("status") != "failed":
        return {}
    rows = cast(list[object], payload["blockedGroups"])
    return {
        _required_string(row, "testPath"): row
        for value in rows
        if isinstance(value, dict)
        for row in [cast(dict[str, object], value)]
    }


def _required_string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise pytest.UsageError(f"causal preflight report has invalid {key}")
    return value


def _split_families(value: object) -> list[str]:
    return [item for item in str(value or "").split(",") if item]
