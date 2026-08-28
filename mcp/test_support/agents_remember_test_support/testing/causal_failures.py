"""Typed causal-failure artifacts and exact-node pytest suppression."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import multiprocessing
import subprocess
from collections.abc import Generator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

import pytest
from agents_remember.kernel.atomic_write import atomic_write_text

CAUSAL_REPORT_OPTION = "--ar-causal-failure-report"
CAUSAL_REPORT_SCHEMA = "python-causal-failures/v3"


class FailureClass(StrEnum):
    """Repair/retry classes that must not be conflated in reports."""

    SHARED_DEPENDENCY = "deterministic-shared-dependency"
    PROCESS_ENVIRONMENT = "process-environment-sensitive"
    INDEPENDENT = "independent-deterministic-or-unclassified"
    UNCLASSIFIED = "explicitly-unclassified-observation"


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


class _CallInfo(Protocol):
    excinfo: pytest.ExceptionInfo[BaseException] | None


class _HookOutcome(Protocol):
    def get_result(self) -> _Report: ...


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
        help="enrich this owner-preflight artifact with exact blocked and independent outcomes",
    )


def pytest_configure(config: pytest.Config) -> None:
    _STATE.report_path = config.getoption(CAUSAL_REPORT_OPTION)
    _STATE.blocked_nodes.clear()
    _STATE.independent_failures.clear()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Block only exact nodes with a source-proved edge to the failed contract."""

    report_path = config.getoption(CAUSAL_REPORT_OPTION)
    payload = load_causal_report(report_path) if report_path is not None else None
    blocked = _blocked_by_node_id(payload)
    seed = config.getoption("random_order_seed", default=None)
    process_topology = _process_topology(config)
    for item in items:
        item.user_properties.extend(
            (
                ("arRandomOrderSeed", "" if seed is None else str(seed)),
                ("arProcessTopology", process_topology),
            )
        )
        cause = blocked.get(item.nodeid)
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


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_makereport(
    item: pytest.Item,
    call: _CallInfo,
) -> Generator[None, _HookOutcome, None]:
    """Classify only an observed failure, never a module import or filename."""

    del item
    outcome = yield
    report = outcome.get_result()
    if report.outcome != "failed":
        return
    profile = execution_profile(call.excinfo.value if call.excinfo is not None else None)
    _set_user_property(report, "arFailureClass", profile.failure_class.value)
    _set_user_property(report, "arFailureFamilies", ",".join(profile.families))
    _set_user_property(report, "arRetrySemantics", profile.retry_semantics)


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
    failure_class = _runtime_failure_class(properties.get("arFailureClass"))
    families = _split_families(properties.get("arFailureFamilies"))
    retry_semantics = properties.get("arRetrySemantics")
    if failure_class is FailureClass.UNCLASSIFIED:
        families = families or ["missing-observed-exception"]
        retry_semantics = "classify-observed-failure-before-retry"
    elif not isinstance(retry_semantics, str) or not retry_semantics:
        retry_semantics = "classify-retry-semantics-before-retry"
    worker_id = str(getattr(report, "worker_id", "controller"))
    process_topology = properties.get("arProcessTopology")
    if not isinstance(process_topology, str) or not process_topology:
        process_topology = f"pytest-worker:{worker_id}"
    return {
        "nodeId": report.nodeid,
        "when": report.when,
        "failureClass": failure_class.value,
        "failureFamilies": families,
        "retrySemantics": retry_semantics,
        "randomOrderSeed": str(properties.get("arRandomOrderSeed", "")),
        "workerId": worker_id,
        "processTopology": process_topology,
        "durationSeconds": round(max(float(report.duration), 0.0), 6),
        "startedAtEpoch": float(report.start),
        "finishedAtEpoch": float(report.stop),
    }


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _should_publish_runtime_evidence(session):
        return
    assert _STATE.report_path is not None
    payload = load_causal_report(_STATE.report_path)
    payload["runtimeEvidence"] = _runtime_evidence(exitstatus)
    payload["acceptanceEligible"] = False
    write_causal_report(_STATE.report_path, payload)


def _should_publish_runtime_evidence(session: pytest.Session) -> bool:
    return not hasattr(session.config, "workerinput") and _STATE.report_path is not None


def _runtime_evidence(exitstatus: int) -> dict[str, object]:
    return {
        "pytestExitCode": int(exitstatus),
        "blockedNodes": [
            {"nodeId": node_id, "causeId": cause_id}
            for node_id, cause_id in sorted(_STATE.blocked_nodes.items())
        ],
        "independentFailures": [value for _, value in sorted(_STATE.independent_failures.items())],
    }


def execution_profile(error: BaseException | None) -> ExecutionProfile:
    """Classify retry semantics from the observed exception type chain."""

    if error is None:
        return ExecutionProfile(
            FailureClass.UNCLASSIFIED,
            ("missing-observed-exception",),
            "classify-observed-failure-before-retry",
        )
    exceptions = _exception_chain(error)
    families = _observed_runtime_families(exceptions)
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


_OBSERVED_RUNTIME_FAMILIES: tuple[
    tuple[str, tuple[type[BaseException], ...]],
    ...,
] = (
    (
        "async-runtime",
        (asyncio.CancelledError, concurrent.futures.CancelledError),
    ),
    (
        "multiprocessing-runtime",
        (multiprocessing.ProcessError,),
    ),
    ("subprocess-runtime", (subprocess.SubprocessError,)),
    ("process-runtime", (ChildProcessError, ProcessLookupError)),
    ("socket-runtime", (ConnectionError,)),
    ("timeout-runtime", (TimeoutError,)),
    ("environment-os-runtime", (OSError,)),
)


def _observed_runtime_families(
    exceptions: tuple[BaseException, ...],
) -> tuple[str, ...]:
    """Assign each observed exception to its most specific owned runtime family."""

    observed: list[str] = []
    for error in exceptions:
        for family, types in _OBSERVED_RUNTIME_FAMILIES:
            if isinstance(error, types):
                if family not in observed:
                    observed.append(family)
                break
    return tuple(observed)


def _process_topology(config: pytest.Config) -> str:
    """Describe serial or xdist ownership without guessing from a filename."""

    worker_input = getattr(config, "workerinput", None)
    if not isinstance(worker_input, dict):
        return "serial-controller"
    worker_id = str(worker_input.get("workerid", "worker-unidentified"))
    worker_count = worker_input.get("workercount")
    if isinstance(worker_count, int) and worker_count > 0:
        return f"xdist:{worker_id}/{worker_count}"
    return f"xdist:{worker_id}/count-unavailable"


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    pending = [error]
    seen: set[int] = set()
    result: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None:
            pending.append(current.__context__)
    return tuple(result)


def _set_user_property(report: _Report, name: str, value: object) -> None:
    report.user_properties[:] = [(key, item) for key, item in report.user_properties if key != name]
    report.user_properties.append((name, value))


def load_causal_report(path: Path) -> dict[str, object]:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise pytest.UsageError(f"causal preflight report is unavailable: {error}") from error
    if not isinstance(raw, dict):
        raise pytest.UsageError("causal preflight report root is invalid")
    payload = cast(dict[str, object], raw)
    _validate_causal_report(payload)
    return payload


def is_failed_causal_report(path: Path) -> bool:
    """Whether a path contains one complete, non-accepting failed report."""

    try:
        return load_causal_report(path)["status"] == "failed"
    except pytest.UsageError:
        return False


def write_causal_report(path: Path, payload: dict[str, object]) -> None:
    _validate_causal_report(payload)
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    atomic_write_text(path.with_suffix(".md"), render_causal_report(payload))


def render_causal_report(payload: dict[str, object]) -> str:
    _validate_causal_report(payload)
    candidate = cast(dict[str, object], payload["candidate"])
    preflights = cast(list[dict[str, object]], payload["preflights"])
    blocked = cast(list[dict[str, object]], payload["blockedGroups"])
    runtime = cast(dict[str, object], payload["runtimeEvidence"])
    runtime_blocked = cast(list[dict[str, object]], runtime["blockedNodes"])
    failures = cast(list[dict[str, object]], runtime["independentFailures"])
    lines = [
        "# Causal failure localization",
        "",
        f"- Candidate tree: `{candidate.get('tree')}`",
        f"- Environment: `{candidate.get('environmentId')}`",
        f"- Preflight status: **{payload['status']}**",
        f"- First causal failure: `{payload['firstCausalFailure'] or 'none'}`",
        "- Acceptance authority: **not granted by this artifact**",
        "",
        "## Owner preflights",
        "",
    ]
    for row in preflights:
        dependent = cast(list[str], row["dependentNodes"])
        lines.extend(
            (
                f"- `{row['causeId']}` — {row['status']}",
                f"  - Evidence altitude: `{row['evidenceAltitude']}`",
                f"  - Contract owner: `{row['owner']}`",
                f"  - Corrective owner: `{row['correctiveOwner']}`",
                "  - Affected exact nodes: "
                + (", ".join(f"`{node}`" for node in dependent) or "none"),
                f"  - Detail: {row['detail']}",
            )
        )
    lines += ["", "## Causally blocked exact nodes", ""]
    for row in blocked:
        chain = " -> ".join(cast(list[str], row["dependencyChain"]))
        lines.extend(
            (
                f"- `{row['nodeId']}` via `{row['causeId']}`",
                f"  - Evidence altitude: `{row['evidenceAltitude']}`",
                f"  - Corrective owner: `{row['correctiveOwner']}`",
                f"  - Exact dependency chain: {chain}",
            )
        )
    if not blocked:
        lines.append("- None.")
    lines += ["", "## Runtime evidence", ""]
    lines.append(f"- Pytest exit code: `{runtime['pytestExitCode']}`")
    lines.append("- Observed blocked nodes:")
    lines.extend(f"  - `{row['nodeId']}` via `{row['causeId']}`" for row in runtime_blocked)
    if not runtime_blocked:
        lines.append("  - None recorded.")
    lines += ["", "## Independent failures", ""]
    for row in failures:
        lines.extend(
            (
                f"- `{row['nodeId']}` — `{row['failureClass']}` at `{row['when']}`",
                "  - Families: " + (", ".join(cast(list[str], row["failureFamilies"])) or "none"),
                f"  - Retry: {row['retrySemantics']}",
                f"  - Seed / worker: `{row['randomOrderSeed']}` / `{row['workerId']}`",
                f"  - Process topology: `{row['processTopology']}`",
                f"  - Timing: `{row['durationSeconds']}` seconds "
                f"(`{row['startedAtEpoch']}` to `{row['finishedAtEpoch']}`)",
            )
        )
    if not failures:
        lines.append("- None recorded.")
    return "\n".join(lines) + "\n"


def _validate_causal_report(payload: dict[str, object]) -> None:
    status, raw_preflights, raw_blocked = _validate_report_header(payload)
    preflights = _preflight_index(raw_preflights)
    _validate_first_failure(payload, status, preflights)
    blocked_population = _validate_blocked_rows(raw_blocked, preflights)
    _validate_runtime_evidence(
        payload.get("runtimeEvidence"),
        preflights,
        blocked_population,
    )


def _validate_report_header(
    payload: dict[str, object],
) -> tuple[str, list[object], list[object]]:
    if payload.get("schemaVersion") != CAUSAL_REPORT_SCHEMA:
        raise pytest.UsageError("causal preflight report schema is invalid")
    if payload.get("acceptanceEligible") is not False:
        raise pytest.UsageError("causal preflight report must be explicitly non-accepting")
    if not isinstance(payload.get("candidate"), dict):
        raise pytest.UsageError("causal preflight candidate identity is invalid")
    status = payload.get("status")
    if status not in {"passed", "failed"}:
        raise pytest.UsageError("causal preflight report status is invalid")
    raw_preflights = payload.get("preflights")
    raw_blocked = payload.get("blockedGroups")
    if not isinstance(raw_preflights, list) or not isinstance(raw_blocked, list):
        raise pytest.UsageError("causal preflight report populations are invalid")
    return cast(str, status), raw_preflights, raw_blocked


def _validate_first_failure(
    payload: dict[str, object],
    status: str,
    preflights: dict[str, dict[str, object]],
) -> None:
    failures = [cause for cause, row in preflights.items() if row["status"] == "failed"]
    first = payload.get("firstCausalFailure")
    if status == "failed" and (not failures or first != failures[0]):
        raise pytest.UsageError("causal preflight first failure is invalid")
    if status == "passed" and (failures or first is not None):
        raise pytest.UsageError("passing causal preflight reports cannot carry a failure")


def _validate_blocked_rows(
    rows: list[object],
    preflights: dict[str, dict[str, object]],
) -> set[str]:
    seen_nodes: set[str] = set()
    for value in rows:
        row = _required_mapping(value, "blocked causal row")
        node_id = _required_string(row, "nodeId")
        cause_id = _required_string(row, "causeId")
        if node_id in seen_nodes:
            raise pytest.UsageError(f"causal preflight report duplicates blocked node {node_id}")
        seen_nodes.add(node_id)
        preflight = preflights.get(cause_id)
        if preflight is None or preflight["status"] != "failed":
            raise pytest.UsageError(f"blocked node {node_id} has no failed owning preflight")
        if node_id not in cast(list[str], preflight["dependentNodes"]):
            raise pytest.UsageError(
                f"blocked node {node_id} is absent from its dependent population"
            )
        if row.get("evidenceAltitude") != preflight["evidenceAltitude"]:
            raise pytest.UsageError(f"blocked node {node_id} has conflicting evidence altitude")
        if row.get("correctiveOwner") != preflight["correctiveOwner"]:
            raise pytest.UsageError(f"blocked node {node_id} has conflicting corrective owner")
        chain = row.get("dependencyChain")
        if not _string_list(chain) or cast(list[str], chain)[-1] != node_id:
            raise pytest.UsageError(f"blocked node {node_id} has an invalid dependency chain")
        owner = cast(str, preflight["owner"])
        if not cast(list[str], chain)[0].startswith(f"{owner}::"):
            raise pytest.UsageError(f"blocked node {node_id} chain does not start at its owner")
    return seen_nodes


def _preflight_index(values: list[object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for value in values:
        row = _required_mapping(value, "causal preflight row")
        cause_id = _required_string(row, "causeId")
        if cause_id in result:
            raise pytest.UsageError(f"causal preflight report duplicates cause {cause_id}")
        if row.get("status") not in {"passed", "failed"}:
            raise pytest.UsageError(f"causal preflight {cause_id} has invalid status")
        for key in ("failureClass", "evidenceAltitude", "owner", "correctiveOwner", "detail"):
            _required_string(row, key)
        if not _string_list(row.get("dependentNodes"), allow_empty=True):
            raise pytest.UsageError(f"causal preflight {cause_id} has invalid dependent nodes")
        nodes = cast(list[str], row["dependentNodes"])
        if len(nodes) != len(set(nodes)):
            raise pytest.UsageError(f"causal preflight {cause_id} duplicates dependent nodes")
        result[cause_id] = row
    return result


def _validate_runtime_evidence(
    value: object,
    preflights: dict[str, dict[str, object]],
    blocked_population: set[str],
) -> None:
    runtime = _required_mapping(value, "causal runtime evidence")
    exit_code = runtime.get("pytestExitCode")
    if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
        raise pytest.UsageError("causal runtime pytest exit code is invalid")
    blocked = runtime.get("blockedNodes")
    failures = runtime.get("independentFailures")
    if not isinstance(blocked, list) or not isinstance(failures, list):
        raise pytest.UsageError("causal runtime populations are invalid")
    seen: set[str] = set()
    for item in blocked:
        row = _required_mapping(item, "runtime blocked node")
        node_id = _required_string(row, "nodeId")
        cause_id = _required_string(row, "causeId")
        if node_id in seen or node_id not in blocked_population:
            raise pytest.UsageError(f"runtime blocked node {node_id} is invalid")
        if cause_id not in preflights or preflights[cause_id]["status"] != "failed":
            raise pytest.UsageError(f"runtime blocked node {node_id} has invalid cause")
        seen.add(node_id)
    for item in failures:
        row = _required_mapping(item, "independent runtime failure")
        for key in (
            "nodeId",
            "when",
            "failureClass",
            "retrySemantics",
            "workerId",
            "processTopology",
        ):
            _required_string(row, key)
        if not isinstance(row.get("failureFamilies"), list):
            raise pytest.UsageError("independent runtime failure families are invalid")
        if row["nodeId"] in seen:
            raise pytest.UsageError("a runtime node cannot be blocked and independently failed")


def _blocked_by_node_id(
    payload: dict[str, object] | None,
) -> dict[str, dict[str, object]]:
    if payload is None or payload["status"] != "failed":
        return {}
    rows = cast(list[dict[str, object]], payload["blockedGroups"])
    return {cast(str, row["nodeId"]): row for row in rows}


def _required_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise pytest.UsageError(f"causal preflight report has invalid {label}")
    return cast(dict[str, object], value)


def _required_string(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise pytest.UsageError(f"causal preflight report has invalid {key}")
    return value


def _string_list(value: object, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and bool(item) for item in value)
    )


def _runtime_failure_class(value: object) -> FailureClass:
    try:
        return FailureClass(value)
    except (TypeError, ValueError):
        return FailureClass.UNCLASSIFIED


def _split_families(value: object) -> list[str]:
    return [item for item in str(value or "").split(",") if item]
