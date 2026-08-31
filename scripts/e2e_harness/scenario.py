"""Real Codex/app-server scenario for ambient dispatch and seat replacement."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.controlplane.operator_inbox_store import OperatorInboxStore
from agents_remember.kernel.primitives.observer_paths import observer_root
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.daemon import DaemonEndpoint
from agents_remember.serving.daemon import ensure as ensure_daemon
from agents_remember.serving.terminal_catalog import TerminalCatalog, terminal_catalog_path
from codex_driver import (
    EXPECTED_CODEX_VERSION,
    codex_log_evidence,
    codex_mcp_registration,
    probe_candidate_mcp,
    run_ambient_codex,
)
from fixture import E2EFixture, create_fixture
from reporting import CheckpointDefinition, CheckpointFailure, CheckpointRecorder
from responses_server import (
    AMBIENT_REPEAT_PROMPT,
    MID_REPLACEMENT,
    POST_REPLACEMENT,
    PRE_REPLACEMENT,
    REPLACE_MANAGER_PROMPT,
    RETIRE_MANAGER_PROMPT,
    WORKER_MID_PROMPT,
    WORKER_POST_PROMPT,
    ResponsesServer,
    ScriptedAddresses,
    ScriptedResponses,
)
from scenario_control import (
    submit_control,
    wait_for_accepted_brief,
    wait_for_entry,
    wait_for_inbox_id,
    wait_for_inbox_response,
    wait_for_new_seat,
    wait_for_seat,
)
from scenario_evidence import (
    C09,
    check_discovery,
    failure_evidence,
    is_canonical_manager_message,
    message_evidence,
    tmux_evidence,
)
from scenario_runtime import prepare_tmux_server, teardown

C00 = CheckpointDefinition(
    "L5-C00",
    requirement="L5-R5,L5-R6",
    expected="disposable candidate dashboard and tmux substrate start from fresh fixture state",
    owner="E2E fixture launch authority",
)
C01 = CheckpointDefinition(
    "L5-C01",
    requirement="L5-R1",
    expected=(
        "real pinned ambient and hosted Codex app-servers report version 0.151.0, reach "
        "connected MCP readiness, execute a discovered dispatch_agent turn whose architect brief "
        "reaches the hosted adapter, and converge a repeated ambient call on the same occupant"
    ),
    owner="Codex 0.151.0 + candidate MCP stdio",
)
C02 = CheckpointDefinition(
    "L5-C02",
    requirement="L5-R2,L5-R7",
    expected="ambient launcher creates architect and the same structural chain reaches worker",
    owner="dispatch_agent structural transaction",
)
C03 = CheckpointDefinition(
    "L5-C03",
    requirement="L5-R3",
    expected="persisted architect dispatch brief byte-matches completed canonical template",
    owner="dispatch brief store + canonical architect template",
)
C04 = CheckpointDefinition(
    "L5-C04",
    requirement="L5-R4",
    expected="pre-replacement worker message uses canonical master+manager address",
    owner="message_parent canonical resolver",
)
C05 = CheckpointDefinition(
    "L5-C05",
    requirement="L5-R4",
    expected="public retire_child creates a real vacant manager seat",
    owner="orchestrator retire_child",
)
C06 = CheckpointDefinition(
    "L5-C06",
    requirement="L5-R4",
    expected=(
        "vacancy message remains a canonical no-hosted-session row with no occupant address and "
        "is eligible for replacement-aware redelivery"
    ),
    owner="message_parent durable inbox",
)
C07 = CheckpointDefinition(
    "L5-C07",
    requirement="L5-R4",
    expected="queued canonical row re-resolves to replacement manager, never retired occupant",
    owner="agent notifier boundary drain",
)
C08 = CheckpointDefinition(
    "L5-C08",
    requirement="L5-R4",
    expected="post-replacement message resolves directly to replacement manager",
    owner="message_parent canonical resolver",
)


@dataclass(frozen=True)
class _ScenarioContext:
    repository_root: Path
    fixture: E2EFixture
    catalog: TerminalCatalog
    inbox: OperatorInboxStore
    script: ScriptedResponses
    recorder: CheckpointRecorder


@dataclass(frozen=True)
class _InitialSeats:
    architect: TerminalCatalogEntry
    orchestrator: TerminalCatalogEntry
    manager: TerminalCatalogEntry
    worker: TerminalCatalogEntry


@dataclass(frozen=True)
class _ArchitectLaunch:
    ambient: dict[str, object]
    ambient_repeat: dict[str, object]
    initial: TerminalCatalogEntry | None
    current: TerminalCatalogEntry | None
    initial_brief: OperatorInboxEntry | None
    brief: OperatorInboxEntry | None


def run_scenario(
    root: Path, *, repository_root: Path, recorder: CheckpointRecorder
) -> dict[str, object]:
    script = ScriptedResponses(_placeholder_addresses())
    with ResponsesServer(script) as responses:
        fixture = create_fixture(
            root,
            repository_root=repository_root,
            responses_base_url=responses.base_url,
        )
        script.addresses = _fixture_addresses(fixture)
        config = load_config(fixture.authority_path)
        context = _ScenarioContext(
            repository_root=repository_root,
            fixture=fixture,
            catalog=TerminalCatalog(terminal_catalog_path(fixture.coordination_root)),
            inbox=OperatorInboxStore(observer_root(config)),
            script=script,
            recorder=recorder,
        )
        try:
            result = _run_flow(context, config)
        except Exception:
            recorder.diagnostic(
                "failure-state",
                failure_evidence(
                    fixture=fixture,
                    catalog=context.catalog,
                    inbox=context.inbox,
                    script=script,
                ),
            )
            raise
        finally:
            cleanup = teardown(fixture, context.catalog)
            recorder.diagnostic("teardown", cleanup)
        result["cleanup"] = cleanup
        return result


def _run_flow(context: _ScenarioContext, config: McpRuntimeConfig) -> dict[str, object]:
    dashboard = _at_boundary(context, C00, lambda: _start_runtime(context, config))
    architect = _at_boundary(context, C01, lambda: _launch_architect(context, dashboard))
    seats = _at_boundary(context, C02, lambda: _wait_for_initial_chain(context, architect))
    _at_boundary(context, C03, lambda: _check_architect_brief(context, architect))
    _at_boundary(context, C04, lambda: _check_pre_replacement(context, seats.manager))
    _at_boundary(context, C05, lambda: _retire_manager(context, seats))
    _at_boundary(context, C06, lambda: _check_vacancy(context, seats.worker))
    manager_b = _at_boundary(context, C07, lambda: _replace_manager(context, seats))
    _at_boundary(context, C08, lambda: _check_post_replacement(context, seats.worker, manager_b))
    _at_boundary(context, C09, lambda: check_discovery(context.script, context.recorder))
    return _scenario_result(context, seats, manager_b)


def _at_boundary[T](
    context: _ScenarioContext,
    definition: CheckpointDefinition,
    operation: Callable[[], T],
) -> T:
    """Turn an unexpected stage exception into the same actionable checkpoint contract."""

    try:
        return operation()
    except CheckpointFailure:
        raise
    except Exception as error:
        try:
            context.recorder.check(
                definition,
                actual={"exception": type(error).__name__, "message": str(error)},
                passed=False,
            )
        except CheckpointFailure as failure:
            raise failure from error
        raise AssertionError("failed checkpoint was not rejected") from error


def _start_runtime(
    context: _ScenarioContext,
    config: McpRuntimeConfig,
) -> dict[str, object]:
    prepare_tmux_server(context.fixture)
    dashboard = _start_dashboard(config)
    context.recorder.check(
        C00,
        actual={
            "dashboard": dashboard,
            "tmuxServer": "ready",
            "tmuxSocketRoot": (context.fixture.root / "tmux-runtime").as_posix(),
        },
        passed=True,
    )
    return dashboard


def _start_dashboard(config: McpRuntimeConfig) -> dict[str, object]:
    dashboard = ensure_daemon(
        config,
        DaemonEndpoint(host="127.0.0.1", port=config.dashboard.port),
    )
    state = dashboard.state
    if dashboard.action not in {"started", "adopted"} or state is None:
        raise RuntimeError(f"clean-room dashboard failed readiness: {dashboard.detail}")
    return {"action": dashboard.action, "pid": state.pid, "port": state.port}


def _launch_architect(
    context: _ScenarioContext,
    dashboard: dict[str, object],
) -> TerminalCatalogEntry:
    fixture = context.fixture
    mcp_registration = codex_mcp_registration(fixture)
    mcp_handshake = probe_candidate_mcp(fixture)
    ambient = run_ambient_codex(fixture)
    initial_architect = context.catalog.active_for_task(fixture.sprint, seat_role="architect")
    initial_brief = wait_for_accepted_brief(context.inbox, initial_architect)
    ambient_repeat: dict[str, object]
    if initial_architect is not None and _brief_accepted(initial_brief):
        ambient_repeat = run_ambient_codex(fixture, prompt=AMBIENT_REPEAT_PROMPT)
    else:
        ambient_repeat = {
            "status": "not-run",
            "reason": "initial ambient call did not complete its one-call brief transaction",
        }
    architect = context.catalog.active_for_task(fixture.sprint, seat_role="architect")
    architect_row = wait_for_accepted_brief(context.inbox, architect)
    launch = _ArchitectLaunch(
        ambient=ambient,
        ambient_repeat=ambient_repeat,
        initial=initial_architect,
        current=architect,
        initial_brief=initial_brief,
        brief=architect_row,
    )
    context.recorder.check(
        C01,
        actual={
            "ambient": ambient,
            "ambientRepeat": ambient_repeat,
            "initialArchitectId": initial_architect.id if initial_architect else None,
            "initialDispatchBrief": _brief_delivery_evidence(initial_brief),
            "architect": _architect_evidence(architect),
            "dispatchBrief": _brief_delivery_evidence(architect_row),
            "mcpRegistration": mcp_registration,
            "mcpHandshake": mcp_handshake,
            "dashboard": dashboard,
            "responseEvents": list(context.script.events),
            "codexLogs": codex_log_evidence(fixture.codex_home),
        },
        passed=_architect_launch_passed(launch, fixture),
    )
    if architect is None:  # the failing checkpoint above always raises first
        raise AssertionError("L5-C01 accepted no architect seat")
    return architect


def _architect_evidence(architect: TerminalCatalogEntry | None) -> object:
    if architect is None:
        return None
    return {
        "status": architect.status,
        "taskDocumentRef": (
            architect.task_document_ref.model_dump() if architect.task_document_ref else None
        ),
        "seatRole": architect.seat_role,
        "controlState": architect.control_state,
        "codexCliVersion": (architect.control_raw or {}).get("codexCliVersion"),
        "requiredMcpTool": (architect.control_raw or {}).get("requiredMcpTool"),
        "controlEndpoint": (
            architect.control_endpoint.as_posix()
            if architect.control_endpoint is not None
            else None
        ),
        "tmux": tmux_evidence(architect),
    }


def _brief_delivery_evidence(row: OperatorInboxEntry | None) -> object:
    if row is None:
        return None
    return {
        "entryId": row.id,
        "deliveryState": row.deliveryState,
        "adapterDeliveryState": row.adapterDeliveryState,
        "deliveryDetail": row.deliveryDetail,
    }


def _brief_accepted(row: OperatorInboxEntry | None) -> bool:
    return bool(
        row is not None
        and row.deliveryState == "delivered"
        and row.adapterDeliveryState in {"accepted", "completed"}
    )


def _architect_launch_passed(
    launch: _ArchitectLaunch,
    fixture: E2EFixture,
) -> bool:
    return all(
        (
            _ambient_call_passed(launch.ambient),
            _ambient_call_passed(launch.ambient_repeat),
            _architect_candidate_preserved(launch, fixture),
            _brief_candidate_preserved(launch),
        )
    )


def _ambient_call_passed(result: dict[str, object]) -> bool:
    return all(
        (
            result.get("status") == "completed",
            result.get("cliVersion") == EXPECTED_CODEX_VERSION,
            _ambient_identity_absent(result),
        )
    )


def _architect_candidate_preserved(
    launch: _ArchitectLaunch,
    fixture: E2EFixture,
) -> bool:
    architect = launch.current
    initial = launch.initial
    if architect is None or initial is None:
        return False
    return all(
        (
            architect.id == initial.id,
            architect.status == "running",
            architect.task_document_ref == fixture.sprint,
            architect.seat_role == "architect",
            _hosted_codex_ready(architect),
        )
    )


def _brief_candidate_preserved(launch: _ArchitectLaunch) -> bool:
    row = launch.brief
    initial = launch.initial_brief
    if row is None or initial is None:
        return False
    return row.id == initial.id and _brief_accepted(row)


def _ambient_identity_absent(result: dict[str, object]) -> bool:
    evidence = result.get("callerIdentity")
    return bool(
        isinstance(evidence, dict)
        and evidence.get("AR_HOSTED_SESSION_ID_present") is False
        and evidence.get("AR_SPAWN_ROLE_present") is False
    )


def _hosted_codex_ready(architect: TerminalCatalogEntry) -> bool:
    raw = architect.control_raw or {}
    readiness = raw.get("requiredMcpTool")
    return bool(
        raw.get("codexCliVersion") == EXPECTED_CODEX_VERSION
        and isinstance(readiness, dict)
        and readiness.get("runtimeStatus") == "connected"
        and readiness.get("toolName") == "dispatch_agent"
    )


def _wait_for_initial_chain(
    context: _ScenarioContext,
    architect: TerminalCatalogEntry,
) -> _InitialSeats:
    fixture = context.fixture
    seats = _InitialSeats(
        architect=architect,
        orchestrator=wait_for_seat(context.catalog, fixture.sprint, "orchestrator"),
        manager=wait_for_seat(context.catalog, fixture.master, "manager"),
        worker=wait_for_seat(context.catalog, fixture.leaf, "worker"),
    )
    context.recorder.check(
        C02,
        actual={
            "architect": seats.architect.id,
            "orchestrator": seats.orchestrator.id,
            "manager": seats.manager.id,
            "worker": seats.worker.id,
        },
        passed=all(entry.status == "running" for entry in _seat_entries(seats)),
    )
    return seats


def _seat_entries(seats: _InitialSeats) -> tuple[TerminalCatalogEntry, ...]:
    return (seats.architect, seats.orchestrator, seats.manager, seats.worker)


def _check_architect_brief(context: _ScenarioContext, architect: TerminalCatalogEntry) -> None:
    row = wait_for_inbox_id(context.inbox, architect.dispatch_brief_entry_id)
    fixture = context.fixture
    context.recorder.check(
        C03,
        actual={
            "entryId": row.id,
            "byteLength": len(row.response.encode("utf-8")),
            "deliveryState": row.deliveryState,
            "adapterDeliveryState": row.adapterDeliveryState,
        },
        passed=(
            row.response == fixture.architect_brief
            and row.taskDocumentRef == fixture.sprint
            and row.recipientRole == "architect"
            and row.deliveryState == "delivered"
            and row.adapterDeliveryState in {"accepted", "completed"}
        ),
    )


def _check_pre_replacement(context: _ScenarioContext, manager: TerminalCatalogEntry) -> None:
    row = wait_for_inbox_response(
        context.inbox,
        PRE_REPLACEMENT,
        predicate=lambda candidate: (
            candidate.deliveredToSession == manager.id
            and candidate.adapterDeliveryState in {"accepted", "completed"}
        ),
    )
    context.recorder.check(
        C04,
        actual=message_evidence(row),
        passed=(
            is_canonical_manager_message(row, context.fixture.master)
            and row.deliveredToSession == manager.id
        ),
    )


def _retire_manager(context: _ScenarioContext, seats: _InitialSeats) -> None:
    submit_control(context.catalog, seats.orchestrator.id, RETIRE_MANAGER_PROMPT)
    retired = wait_for_entry(
        context.catalog,
        seats.manager.id,
        lambda entry: entry.status == "terminated",
    )
    context.recorder.check(
        C05,
        actual={"status": retired.status, "retiredReason": retired.retired_reason},
        passed=(
            retired.status == "terminated"
            and context.catalog.active_for_task(context.fixture.master, seat_role="manager") is None
        ),
    )


def _check_vacancy(context: _ScenarioContext, worker: TerminalCatalogEntry) -> None:
    submit_control(context.catalog, worker.id, WORKER_MID_PROMPT)
    row = wait_for_inbox_response(
        context.inbox,
        MID_REPLACEMENT,
        predicate=lambda candidate: candidate.deliveryState == "no-hosted-session",
    )
    context.recorder.check(
        C06,
        actual=message_evidence(row),
        passed=(
            is_canonical_manager_message(row, context.fixture.master)
            and row.agentId is None
            and row.lifecycleId is None
            and row.deliveredToSession is None
            and row.deliveryState == "no-hosted-session"
            and row.adapterDeliveryState is None
        ),
    )


def _replace_manager(
    context: _ScenarioContext,
    seats: _InitialSeats,
) -> TerminalCatalogEntry:
    submit_control(context.catalog, seats.orchestrator.id, REPLACE_MANAGER_PROMPT)
    replacement = wait_for_new_seat(
        context.catalog,
        context.fixture.master,
        "manager",
        seats.manager.id,
    )
    row = wait_for_inbox_response(
        context.inbox,
        MID_REPLACEMENT,
        predicate=lambda candidate: (
            candidate.deliveredToSession == replacement.id
            and candidate.adapterDeliveryState in {"accepted", "completed"}
        ),
    )
    context.recorder.check(
        C07,
        actual={
            "oldManager": seats.manager.id,
            "newManager": replacement.id,
            **message_evidence(row),
        },
        passed=(
            replacement.id != seats.manager.id
            and row.deliveredToSession == replacement.id
            and row.deliveredToSession != seats.manager.id
        ),
    )
    return replacement


def _check_post_replacement(
    context: _ScenarioContext,
    worker: TerminalCatalogEntry,
    replacement: TerminalCatalogEntry,
) -> None:
    submit_control(context.catalog, worker.id, WORKER_POST_PROMPT)
    row = wait_for_inbox_response(
        context.inbox,
        POST_REPLACEMENT,
        predicate=lambda candidate: (
            candidate.deliveredToSession == replacement.id
            and candidate.adapterDeliveryState in {"accepted", "completed"}
        ),
    )
    context.recorder.check(
        C08,
        actual=message_evidence(row),
        passed=(
            is_canonical_manager_message(row, context.fixture.master)
            and row.deliveredToSession == replacement.id
        ),
    )


def _scenario_result(
    context: _ScenarioContext,
    seats: _InitialSeats,
    replacement: TerminalCatalogEntry,
) -> dict[str, object]:
    fixture = context.fixture
    return {
        "candidate": {
            "repository": context.repository_root.as_posix(),
            "authority": fixture.authority_path.as_posix(),
            "codexHome": fixture.codex_home.as_posix(),
        },
        "seats": {
            "architect": seats.architect.id,
            "orchestrator": seats.orchestrator.id,
            "managerA": seats.manager.id,
            "managerB": replacement.id,
            "worker": seats.worker.id,
        },
        "tmuxNames": [
            seats.architect.tmux_name,
            seats.orchestrator.tmux_name,
            seats.manager.tmux_name,
            replacement.tmux_name,
            seats.worker.tmux_name,
        ],
        "responseEvents": context.script.events,
    }


def _placeholder_addresses() -> ScriptedAddresses:
    return ScriptedAddresses(
        sprint={},
        master={},
        leaf={},
        architect_brief="fixture-not-created",
    )


def _fixture_addresses(fixture: E2EFixture) -> ScriptedAddresses:
    return ScriptedAddresses(
        sprint=fixture.sprint.model_dump(),
        master=fixture.master.model_dump(),
        leaf=fixture.leaf.model_dump(),
        architect_brief=fixture.architect_brief,
    )
