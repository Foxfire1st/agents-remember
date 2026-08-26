"""Focused behavioral coverage for L19 structural refusal seams."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


def _task_ref(path: str):
    from agents_remember.models.task_document_ref import TaskDocumentRef  # noqa: PLC0415

    return TaskDocumentRef(repository="repo", path=path)


def test_durable_record_migration_is_atomic_and_idempotent(tmp_path: Path) -> None:
    durable = importlib.import_module("agents_remember.controlplane.durable_store")

    class _Record(durable.DurableRecord):
        value: int

    log_path = tmp_path / "records.jsonl"
    ownership = Mock(store="test-records")
    rewrite = Mock()
    no_lock = __import__("contextlib").nullcontext()
    with (
        patch.object(durable, "exclusive_access", return_value=no_lock),
        patch.object(durable, "rewrite_lines", rewrite),
    ):
        log_path.write_text("", encoding="utf-8")
        assert durable.migrate_jsonl_records(log_path, ownership, _Record, dict) == 0

        log_path.write_text('{"schemaVersion":"1.0","value":1}\n', encoding="utf-8")
        assert durable.migrate_jsonl_records(log_path, ownership, _Record, dict) == 0
        rewrite.assert_not_called()

        def increment(row: dict[str, object]) -> dict[str, object]:
            value = row["value"]
            assert isinstance(value, int)
            return {**row, "value": value + 1}

        assert durable.migrate_jsonl_records(log_path, ownership, _Record, increment) == 1
        rewrite.assert_called_once()

        log_path.write_text("[]\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not a JSON object"):
            durable.migrate_jsonl_records(log_path, ownership, _Record, dict)
    assert ownership.check_declared_writer.call_count == 4


def test_structural_seat_current_and_parent_resolution_fail_closed() -> None:
    seats = importlib.import_module("agents_remember.serving.structural_seats")
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    leaf = _task_ref("master/leaf.json")
    master = _task_ref("master/task.json")
    sprint = _task_ref("sprint/task.json")

    def occupant(
        identifier: str,
        *,
        role: str,
        document=None,
        replacement=None,
        status: str = "running",
    ):
        return SimpleNamespace(
            id=identifier,
            status=status,
            binding_role=role,
            task_document_ref=document,
            replacement_for_task_document_ref=replacement,
        )

    catalog = Mock()
    topology = Mock()
    resolver = seats.StructuralSeatResolver(catalog, topology)
    primary = occupant("primary", role="worker", document=leaf)
    replacement = occupant("replacement", role="worker", replacement=leaf)

    topology.validate_role.side_effect = refs.TaskDocumentRefError("bad-role", "bad role")
    with pytest.raises(seats.StructuralSeatError, match="bad role"):
        resolver.current(leaf, "worker")
    topology.validate_role.side_effect = None
    catalog.list.return_value = [primary]
    assert resolver.current(leaf, "worker") is primary
    catalog.list.return_value = [primary, occupant("other", role="worker", document=leaf)]
    with pytest.raises(seats.StructuralSeatError, match="multiple running occupants"):
        resolver.current(leaf, "worker")
    catalog.list.return_value = [replacement]
    assert resolver.current(leaf, "worker") is replacement
    catalog.list.return_value = [
        replacement,
        occupant("other", role="worker", replacement=leaf),
    ]
    with pytest.raises(seats.StructuralSeatError, match="multiple running replacements"):
        resolver.current(leaf, "worker")
    catalog.list.return_value = []
    with pytest.raises(seats.StructuralSeatError, match="no running occupant"):
        resolver.current(leaf, "worker")

    unbound = SimpleNamespace(binding_task_document_ref=None, binding_role="worker")
    with pytest.raises(seats.StructuralSeatError, match="no task document"):
        resolver.parent(unbound)
    topology.parent.side_effect = lambda document: {leaf: master, master: sprint}[document]
    with patch.object(resolver, "current", return_value=primary) as current:
        for role, document, parent_document, parent_role in (
            ("worker", leaf, master, "manager"),
            ("manager", master, sprint, "orchestrator"),
            ("system-specialist", sprint, sprint, "orchestrator"),
            ("orchestrator", sprint, sprint, "architect"),
        ):
            caller = SimpleNamespace(binding_task_document_ref=document, binding_role=role)
            assert resolver.parent(caller) is primary
            current.assert_called_with(parent_document, parent_role)
    unsupported = SimpleNamespace(binding_task_document_ref=leaf, binding_role="operator")
    with pytest.raises(seats.StructuralSeatError, match="no messageable parent"):
        resolver.parent(unsupported)


def test_structural_seat_child_authorization_fail_closed() -> None:
    seats = importlib.import_module("agents_remember.serving.structural_seats")
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    leaf = _task_ref("master/leaf.json")
    master = _task_ref("master/task.json")
    sprint = _task_ref("sprint/task.json")
    topology = Mock()
    resolver = seats.StructuralSeatResolver(Mock(), topology)

    topology.parent.side_effect = None
    topology.parent.return_value = master
    architect = SimpleNamespace(binding_task_document_ref=sprint, binding_role="architect")
    resolver.authorize_child(architect, document=sprint, role="orchestrator")
    with pytest.raises(seats.StructuralSeatError, match="architect children"):
        resolver.authorize_child(architect, document=master, role="manager")

    orchestrator = SimpleNamespace(binding_task_document_ref=sprint, binding_role="orchestrator")
    resolver.authorize_child(orchestrator, document=sprint, role="system-specialist")
    topology.parent.return_value = sprint
    resolver.authorize_child(orchestrator, document=master, role="manager")
    with pytest.raises(seats.StructuralSeatError, match="orchestrator children"):
        resolver.authorize_child(orchestrator, document=leaf, role="worker")

    manager = SimpleNamespace(binding_task_document_ref=master, binding_role="manager")
    with pytest.raises(seats.StructuralSeatError, match="manager children"):
        resolver.authorize_child(manager, document=leaf, role="manager")
    topology.parent.return_value = sprint
    with pytest.raises(seats.StructuralSeatError, match="outside the manager"):
        resolver.authorize_child(manager, document=leaf, role="worker")
    topology.parent.return_value = master
    resolver.authorize_child(manager, document=leaf, role="worker")

    topology.validate_role.side_effect = refs.TaskDocumentRefError("bad-role", "bad role")
    with pytest.raises(seats.StructuralSeatError, match="bad role"):
        resolver.authorize_child(manager, document=leaf, role="worker")
    topology.validate_role.side_effect = None
    non_parent = SimpleNamespace(binding_task_document_ref=leaf, binding_role="worker")
    with pytest.raises(seats.StructuralSeatError, match="does not own subordinate"):
        resolver.authorize_child(non_parent, document=leaf, role="worker")

    topology.parent.side_effect = refs.TaskDocumentRefError("bad-parent", "bad parent")
    with pytest.raises(seats.StructuralSeatError, match="bad parent"):
        resolver._parent_document(leaf)
    topology.parent.side_effect = None
    topology.parent.return_value = None
    with pytest.raises(seats.StructuralSeatError, match="has no parent"):
        resolver._parent_document(leaf)


def test_ambient_seat_resolution_rejects_every_unproven_identity() -> None:
    ambient_seat = importlib.import_module("agents_remember.serving.ambient_seat")
    document = _task_ref("master/leaf.json")
    entry = SimpleNamespace(
        id="seat",
        status="running",
        kind="harness",
        binding_role="worker",
        lifecycle_id="life",
        binding_task_document_ref=document,
    )
    catalog = Mock()
    catalog.get.return_value = entry
    with pytest.raises(ambient_seat.AmbientSeatError, match="no plane-injected"):
        ambient_seat.resolve_ambient_seat(catalog, environ={})
    catalog.get.return_value = None
    with pytest.raises(ambient_seat.AmbientSeatError, match="no running catalog"):
        ambient_seat.resolve_ambient_seat(catalog, environ={"AR_HOSTED_SESSION_ID": "seat"})
    catalog.get.return_value = entry
    entry.kind = "terminal"
    with pytest.raises(ambient_seat.AmbientSeatError, match="hosted harness"):
        ambient_seat.resolve_ambient_seat(catalog, environ={"AR_HOSTED_SESSION_ID": "seat"})
    entry.kind = "harness"
    with pytest.raises(ambient_seat.AmbientSeatError, match="process role"):
        ambient_seat.resolve_ambient_seat(
            catalog,
            environ={"AR_HOSTED_SESSION_ID": "seat", "AR_SPAWN_ROLE": "reviewer"},
        )
    lifecycle = SimpleNamespace(current=SimpleNamespace(id="other"))
    with (
        patch.object(ambient_seat, "ambient", return_value=lifecycle),
        pytest.raises(ambient_seat.AmbientSeatError, match="active lifecycle"),
    ):
        ambient_seat.resolve_ambient_seat(catalog, environ={"AR_HOSTED_SESSION_ID": "seat"})
    entry.lifecycle_id = None
    entry.binding_task_document_ref = None
    with patch.object(ambient_seat, "ambient", return_value=None):
        with pytest.raises(ambient_seat.AmbientSeatError, match="not bound"):
            ambient_seat.resolve_ambient_seat(catalog, environ={"AR_HOSTED_SESSION_ID": "seat"})
        entry.binding_task_document_ref = document
        assert (
            ambient_seat.resolve_ambient_seat(catalog, environ={"AR_HOSTED_SESSION_ID": "seat"})
            is entry
        )


def test_task_document_ref_rejects_ambiguous_path_identity() -> None:
    ref_model = importlib.import_module("agents_remember.models.task_document_ref")
    for repository in ("", ".", "..", "two/parts", "two\\parts"):
        with pytest.raises(ValueError, match="repository"):
            ref_model.TaskDocumentRef(repository=repository, path="leaf.json")
    for path in ("", "/leaf.json", "../leaf.json", "leaf.md"):
        with pytest.raises(ValueError, match="task document path"):
            ref_model.TaskDocumentRef(repository="repo", path=path)


def test_terminal_catalog_disk_reader_refuses_undeclared_shapes(tmp_path: Path) -> None:
    catalog_module = importlib.import_module("agents_remember.serving.terminal_catalog")
    path = tmp_path / "logs" / "dashboard" / "terminal-sessions.json"
    path.parent.mkdir(parents=True)
    catalog = catalog_module.TerminalCatalog(path)
    assert catalog._read_disk() == []

    def write(payload: object) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    write([])
    assert catalog._read_disk() == []
    write({"schema": "ar-dashboard-terminal-sessions/v2", "sessions": [], "extra": True})
    with pytest.raises(ValueError, match="undeclared fields"):
        catalog._read_disk()
    write({"schema": "ar-dashboard-terminal-sessions/v2", "sessions": {}})
    with pytest.raises(ValueError, match="must be a list"):
        catalog._read_disk()
    write({"schema": "ar-dashboard-terminal-sessions/v2", "sessions": [1]})
    with pytest.raises(ValueError, match="objects only"):
        catalog._read_disk()
    write({"schema": "future", "sessions": []})
    with pytest.raises(ValueError, match="unsupported terminal catalog schema"):
        catalog._read_disk()
    write({"schema": "ar-dashboard-terminal-sessions/v1", "sessions": [{}]})
    with patch.object(catalog_module, "migrate_terminal_catalog_v1", return_value=[]) as migrate:
        assert catalog._read_disk() == []
        migrate.assert_called_once()
    write({"schema": "ar-dashboard-terminal-sessions/v2", "sessions": []})
    assert catalog._read_disk() == []


def test_structural_agent_payload_levels_and_plane_only_message_refusal(tmp_path: Path) -> None:
    tools = importlib.import_module("agents_remember.application.structural.agent_tools")
    leaf = _task_ref("master/leaf.json")
    outcome = tools.StructuralOutcome(
        "message_child",
        True,
        "posted",
        leaf,
        "worker",
        "detail",
        "delivered",
        "accepted",
    )
    assert tools._target_payload(outcome) == {
        "ok": True,
        "operation": "message_child",
        "status": "posted",
        "role": "worker",
        "taskDocumentRef": leaf.model_dump(),
        "detail": "detail",
        "deliveryState": "delivered",
        "adapterDeliveryState": "accepted",
    }
    assert tools._level_for_role("worker") == "leaf"
    assert tools._level_for_role("manager") == "master"
    assert tools._level_for_role("orchestrator") == "portfolio"
    context = tools.StructuralMessageContext(
        Mock(), SimpleNamespace(id="s", binding_role="manager"), tools.StructuralAgentRuntime()
    )
    target = tools.StructuralMessageTarget(leaf, "worker")
    message = tools.InboxMessage(ask="work", response="brief", message_kind="dispatch-brief")
    with pytest.raises(ValueError, match="control plane"):
        tools._post_structural_message(Mock(coordination_root=tmp_path), context, target, message)


def test_dispatch_agent_refuses_invalid_spawn_and_unpersisted_brief(tmp_path: Path) -> None:
    tools = importlib.import_module("agents_remember.application.structural.agent_tools")
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    leaf = _task_ref("master/leaf.json")
    config = Mock(coordination_root=tmp_path)
    request = SimpleNamespace(task_document_ref=leaf, role="worker", brief="brief", label="worker")
    runtime = tools.StructuralAgentRuntime(environ={})
    catalog, topology, resolver = Mock(), Mock(), Mock()

    topology.resolve.side_effect = refs.TaskDocumentRefError("bad-document", "bad document")
    with patch.object(tools, "_structural_context", return_value=(catalog, topology, resolver)):
        assert tools.dispatch_agent_tool(config, request, runtime)["status"] == "bad-document"

    topology.resolve.side_effect = None
    topology.resolve.return_value = SimpleNamespace(ref=leaf)
    caller = SimpleNamespace(id="parent", lifecycle_id="life")
    with (
        patch.object(tools, "_structural_context", return_value=(catalog, topology, resolver)),
        patch.object(tools, "resolve_ambient_seat", return_value=caller),
        patch.object(tools, "_implementation_series_admission_refusal", return_value=None),
        patch.object(
            tools,
            "_spawn_dispatch_child",
            return_value={"status": "seat-taken", "detail": "occupied"},
        ),
    ):
        refused = tools.dispatch_agent_tool(config, request, runtime)
    assert refused["status"] == "dispatch-reconciliation-refused"
    assert "private current generation" in refused["detail"]

    posted = Mock(return_value={"ok": False, "status": "durable-refused"})
    failed = Mock(return_value={"status": "rolled-back"})
    with (
        patch.object(tools, "_structural_context", return_value=(catalog, topology, resolver)),
        patch.object(tools, "resolve_ambient_seat", return_value=caller),
        patch.object(tools, "_implementation_series_admission_refusal", return_value=None),
        patch.object(
            tools,
            "_spawn_dispatch_child",
            return_value={"status": "spawned-unbriefed", "session": "child"},
        ),
        patch.object(tools, "_post_initial_dispatch_brief", posted),
        patch.object(tools, "_recover_initial_dispatch_failure", failed),
    ):
        assert tools.dispatch_agent_tool(config, request, runtime) == {"status": "rolled-back"}
    failed.assert_called_once()


def test_dispatch_agent_reports_queued_and_delivered_adapter_states(tmp_path: Path) -> None:
    tools = importlib.import_module("agents_remember.application.structural.agent_tools")
    leaf = _task_ref("master/leaf.json")
    config = Mock(coordination_root=tmp_path)
    request = SimpleNamespace(task_document_ref=leaf, role="worker", brief="brief", label="worker")
    runtime = tools.StructuralAgentRuntime(environ={})
    catalog, topology, resolver = Mock(), Mock(), Mock()
    topology.resolve.return_value = SimpleNamespace(ref=leaf)
    caller = SimpleNamespace(id="parent", lifecycle_id="life")
    post = Mock()
    common = (
        patch.object(tools, "_structural_context", return_value=(catalog, topology, resolver)),
        patch.object(tools, "resolve_ambient_seat", return_value=caller),
        patch.object(tools, "_implementation_series_admission_refusal", return_value=None),
        patch.object(
            tools,
            "_spawn_dispatch_child",
            return_value={"status": "spawned-unbriefed", "session": "child"},
        ),
        patch.object(tools, "_post_initial_dispatch_brief", post),
    )
    with common[0], common[1], common[2], common[3], common[4]:
        post.return_value = {
            "ok": True,
            "deliveryState": "queued",
            "adapterDeliveryState": "queued",
        }
        assert tools.dispatch_agent_tool(config, request, runtime)["status"] == "dispatch-queued"
        post.return_value = {
            "ok": True,
            "deliveryState": "delivered",
            "adapterDeliveryState": "accepted",
        }
        dispatched = tools.dispatch_agent_tool(config, request, runtime)
    assert dispatched["status"] == "dispatched"
    assert dispatched["adapterDeliveryState"] == "accepted"


def test_structural_agent_message_and_child_mutations_report_refusals(tmp_path: Path) -> None:
    tools = importlib.import_module("agents_remember.application.structural.agent_tools")
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    leaf = _task_ref("master/leaf.json")
    config = Mock(coordination_root=tmp_path)
    runtime = tools.StructuralAgentRuntime(environ={})
    catalog, topology, resolver = Mock(), Mock(), Mock()
    caller = SimpleNamespace(id="manager", binding_role="manager", binding_task_document_ref=leaf)
    target = SimpleNamespace(id="child", binding_role="worker", binding_task_document_ref=leaf)
    resolver.parent_address.return_value = (leaf, "manager")
    message = SimpleNamespace(
        task_document_ref=None,
        role=None,
        ask="ask",
        response="response",
        message_kind="question",
        artifact_path=None,
    )
    with (
        patch.object(tools, "_structural_context", return_value=(catalog, topology, resolver)),
        patch.object(tools, "resolve_ambient_seat", return_value=caller),
        patch.object(tools, "_post_structural_message", side_effect=ValueError("refused")),
    ):
        assert tools.message_parent_tool(config, message, runtime)["status"] == "message-refused"

    request = SimpleNamespace(task_document_ref=leaf, role="worker", reason="done", label="new")
    topology.resolve.side_effect = refs.TaskDocumentRefError("bad-document", "bad document")
    with patch.object(tools, "_structural_context", return_value=(catalog, topology, resolver)):
        assert tools.retire_child_tool(config, request, runtime)["status"] == "bad-document"
        assert tools.rename_child_tool(config, request, runtime)["status"] == "bad-document"

    topology.resolve.side_effect = None
    topology.resolve.return_value = SimpleNamespace(ref=leaf)
    resolver.child.return_value = target
    with (
        patch.object(tools, "_structural_context", return_value=(catalog, topology, resolver)),
        patch.object(tools, "resolve_ambient_seat", return_value=caller),
        patch.object(
            tools,
            "exclusive_structural_dispatch_lock",
            side_effect=tools.StructuralDispatchLockError("busy"),
        ),
    ):
        assert (
            tools.retire_child_tool(config, request, runtime)["status"]
            == "retire-serialization-refused"
        )

    with (
        patch.object(tools, "_structural_context", return_value=(catalog, topology, resolver)),
        patch.object(tools, "resolve_ambient_seat", return_value=caller),
        patch.object(tools, "session_retire_tool", return_value={"ok": True, "status": "retired"}),
        patch.object(tools, "session_rename_tool", return_value={"ok": True, "status": "renamed"}),
    ):
        assert tools.retire_child_tool(config, request, runtime)["status"] == "retired"
        assert tools.rename_child_tool(config, request, runtime)["status"] == "renamed"

    with (
        patch.object(tools, "_catalog", return_value=catalog),
        patch.object(tools, "resolve_ambient_seat", return_value=caller),
        patch.object(tools, "session_rename_tool", return_value={"ok": True, "status": "renamed"}),
    ):
        assert tools.rename_self_tool(config, label="self", environ={})["status"] == "renamed"


def test_terminal_tool_task_document_and_open_refusals(tmp_path: Path) -> None:
    terminal_tools = importlib.import_module("agents_remember.application.terminal_tools")
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    leaf = _task_ref("master/leaf.json")
    topology = Mock()
    topology.resolve.side_effect = refs.TaskDocumentRefError("bad-document", "bad document")
    with patch.object(terminal_tools, "TaskDocumentTopology", return_value=topology):
        refused = terminal_tools.attach_terminal_session_to_task_tool(
            Mock(coordination_root=tmp_path), session_id="seat", task_document_ref=leaf
        )
    assert refused["status"] == "task-binding-invalid"

    result = SimpleNamespace(status="task-binding-required", detail=None)
    assert (
        terminal_tools.open_terminal_refusal(
            result,
            harness="codex",
            kind="harness",
            session_id="seat",
            task_document_ref=leaf,
        )["status"]
        == "task-binding-required"
    )
    result.status = "task-binding-invalid"
    assert (
        terminal_tools.open_terminal_refusal(
            result,
            harness="codex",
            kind="harness",
            session_id="seat",
            task_document_ref=leaf,
        )["status"]
        == "task-binding-invalid"
    )


def test_signal_routing_refuses_ambiguous_or_broken_task_containment() -> None:
    routing = importlib.import_module("agents_remember.controlplane.signal_routing")
    leaf = _task_ref("master/leaf.json")
    sprint = _task_ref("sprint/task.json")
    catalog = Mock()
    replacement = SimpleNamespace(
        id="replacement",
        lifecycle_id="life",
        status="running",
        binding_role="worker",
        task_document_ref=None,
        replacement_for_task_document_ref=leaf,
    )
    catalog.list.return_value = [
        replacement,
        SimpleNamespace(**{**replacement.__dict__, "id": "two"}),
    ]
    with pytest.raises(routing.StructuralRoutingError, match="multiple running replacements"):
        routing._current_occupant(catalog, document=leaf, role="worker")

    hierarchy = Mock()
    hierarchy.parent.return_value = None
    with pytest.raises(routing.StructuralRoutingError, match="no structural parent"):
        routing._required_parent(hierarchy, leaf)
    hierarchy.parent.side_effect = lambda document: sprint if document == leaf else leaf
    with pytest.raises(routing.StructuralRoutingError, match="containment cycle"):
        routing._sprint_document(hierarchy, leaf)

    hierarchy.parent.side_effect = None
    parent = routing.RoutedOwner(role="orchestrator", task_document_ref=sprint)
    with patch.object(routing, "_current_occupant", return_value=parent) as current:
        assert (
            routing._structural_parent_owner(
                catalog, hierarchy, document=sprint, role="system-specialist"
            )
            == parent
        )
        current.assert_called_with(catalog, document=sprint, role="orchestrator")
    assert (
        routing._structural_parent_owner(catalog, hierarchy, document=leaf, role="operator") is None
    )
    catalog.get.return_value = None
    assert (
        routing.derive_signal_owner(
            catalog,
            hierarchy,
            sender_agent_id=None,
            message_kind="state-signal",
            task_document_ref=leaf,
        )
        == routing.RoutedOwner()
    )


def test_curator_checklist_helpers_render_empty_and_populated_sections(tmp_path: Path) -> None:
    checklist = importlib.import_module("agents_remember.memory_quality.curator_checklist")
    failed = SimpleNamespace(returncode=1, stderr="git failed", stdout="")
    with (
        patch.object(checklist, "run_git", return_value=failed),
        pytest.raises(RuntimeError, match="git failed"),
    ):
        checklist._tracked_onboarding_paths(tmp_path / "onboarding")

    finding = {"check": "range", "path": "card.md", "code": "bad", "message": "repair"}
    missing = {
        "sourceFile": "source.py",
        "expectedOnboarding": "source.py.md",
        "state": "missing",
        "note": "create",
    }
    drift = {
        "onboarding_file": "source.py.md",
        "source_file": "source.py",
        "classification": "drifted",
        "note": "refresh",
    }
    lines: list[str] = []
    checklist._append_findings(lines, "Findings", [])
    checklist._append_findings(lines, "Findings", [finding])
    checklist._append_missing(lines, [])
    checklist._append_missing(lines, [missing])
    checklist._append_drift(lines, [])
    checklist._append_drift(lines, [drift])
    rendered = "\n".join(lines)
    assert "| range | card.md | bad | repair |" in rendered
    assert "| source.py | source.py.md | missing | create |" in rendered
    assert "| source.py.md | source.py | drifted | refresh |" in rendered


def test_identity_migration_executes_the_legacy_schema_transform(tmp_path: Path) -> None:
    identity = importlib.import_module("agents_remember.serving.control_plane_identity_migration")

    def exercise(_path, _ownership, model, transform):
        if model is identity.OperatorInboxEntry:
            assert transform({"schema": identity._INBOX_V1}) == {"schema": "migrated"}
        return 0

    with (
        patch.object(identity, "migrate_jsonl_records", side_effect=exercise),
        patch.object(identity, "OperatorInboxStore"),
        patch.object(identity, "ExpectationRowStore"),
        patch.object(identity, "_migrate_row", return_value={"schema": "migrated"}) as migrate,
    ):
        assert identity.migrate_control_plane_identity_logs(
            tmp_path, include_agent_notifier_signals=False
        ) == {"operatorInbox": 0, "expectations": 0}
    migrate.assert_called_once()


def test_inbox_delivery_structural_target_is_unique_and_dispatch_is_exact() -> None:
    delivery = importlib.import_module("agents_remember.serving.inbox_delivery")
    leaf = _task_ref("master/leaf.json")
    catalog = Mock()
    assert (
        delivery._structural_target(
            catalog, SimpleNamespace(taskDocumentRef=None, recipientRole="worker")
        )
        is None
    )
    entry = SimpleNamespace(taskDocumentRef=leaf, recipientRole="worker")
    primary = SimpleNamespace(
        id="one",
        status="running",
        binding_role="worker",
        task_document_ref=leaf,
        replacement_for_task_document_ref=None,
    )
    catalog.list.return_value = [primary, SimpleNamespace(**{**primary.__dict__, "id": "two"})]
    with pytest.raises(ValueError, match="multiple running occupants"):
        delivery._structural_target(catalog, entry)
    replacement = SimpleNamespace(
        **{
            **primary.__dict__,
            "task_document_ref": None,
            "replacement_for_task_document_ref": leaf,
        }
    )
    catalog.list.return_value = [
        replacement,
        SimpleNamespace(**{**replacement.__dict__, "id": "replacement-two"}),
    ]
    with pytest.raises(ValueError, match="multiple running replacements"):
        delivery._structural_target(catalog, entry)
    dispatch = SimpleNamespace(messageKind=delivery.DISPATCH_BRIEF_KIND, agentId=None)
    assert delivery.target_session_for_entry(catalog, dispatch) is None


def test_retire_policy_and_manager_lookup_refuse_broken_topology() -> None:
    retire = importlib.import_module("agents_remember.serving.retire_policy")
    signals = importlib.import_module("agents_remember.serving.state_signals")
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    leaf = _task_ref("master/leaf.json")
    master = _task_ref("master/task.json")
    actor = retire.SeatRef("manager", master, "manager")
    target = retire.SeatRef("worker", leaf, "worker")
    topology = Mock()
    topology.parent.side_effect = refs.TaskDocumentRefError("bad-parent", "bad parent")
    with pytest.raises(retire.RetirePolicyError, match="cannot resolve target"):
        retire.check_retire_authority(actor, target, topology)

    subordinate = SimpleNamespace(binding_task_document_ref=leaf, binding_role="worker")
    catalog = Mock()
    hierarchy = Mock()
    hierarchy.parent.return_value = None
    assert signals._manager_for_subordinate(catalog, hierarchy, subordinate) is None
    hierarchy.parent.return_value = master
    manager = SimpleNamespace(
        id="manager",
        status="running",
        binding_role="manager",
        task_document_ref=master,
        replacement_for_task_document_ref=None,
    )
    catalog.list.return_value = [manager, SimpleNamespace(**{**manager.__dict__, "id": "two"})]
    with pytest.raises(ValueError, match="multiple running occupants"):
        signals._manager_for_subordinate(catalog, hierarchy, subordinate)
    replacement = SimpleNamespace(
        **{
            **manager.__dict__,
            "task_document_ref": None,
            "replacement_for_task_document_ref": master,
        }
    )
    catalog.list.return_value = [
        replacement,
        SimpleNamespace(**{**replacement.__dict__, "id": "replacement-two"}),
    ]
    with pytest.raises(ValueError, match="multiple running replacements"):
        signals._manager_for_subordinate(catalog, hierarchy, subordinate)


def test_unbound_child_authorization_and_duplicate_catalog_occupants_fail() -> None:
    seats = importlib.import_module("agents_remember.serving.structural_seats")
    catalog_module = importlib.import_module("agents_remember.serving.terminal_catalog")
    leaf = _task_ref("master/leaf.json")
    resolver = seats.StructuralSeatResolver(Mock(), Mock())
    caller = SimpleNamespace(binding_task_document_ref=None, binding_role="manager")
    with pytest.raises(seats.StructuralSeatError, match="no task document"):
        resolver.authorize_child(caller, document=leaf, role="worker")

    catalog = catalog_module.TerminalCatalog(Path("/tmp/unused-terminal-catalog.json"))
    occupant = SimpleNamespace(
        id="one",
        status="running",
        binding_role="worker",
        task_document_ref=leaf,
    )
    with (
        patch.object(
            catalog,
            "list",
            return_value=[occupant, SimpleNamespace(**{**occupant.__dict__, "id": "two"})],
        ),
        pytest.raises(ValueError, match="multiple running occupants"),
    ):
        catalog.active_for_task(leaf, seat_role="worker")


def test_legacy_binding_resolution_covers_named_and_leaf_scopes(tmp_path: Path) -> None:
    migration = importlib.import_module("agents_remember.serving.terminal_catalog_migration")
    leaf = _task_ref("master/leaf.json")
    master = _task_ref("master/task.json")
    topology = Mock()
    with (
        patch.object(migration, "legacy_leaf_document_ref", return_value=leaf) as legacy,
        patch.object(migration, "task_ref_for_role", return_value=master) as for_role,
    ):
        assert (
            migration._legacy_binding_ref(
                tmp_path, topology, {"leafKey": "legacy"}, "manager", "leafKey"
            )
            == master
        )
    legacy.assert_called_once_with(tmp_path, topology, "legacy")
    for_role.assert_called_once_with(topology, leaf, "manager")

    with patch.object(migration, "_legacy_named_scope", return_value=master):
        assert migration._legacy_binding_ref(tmp_path, topology, {}, "manager", "leafKey") == master
    with patch.object(migration, "_legacy_named_scope", return_value=None):
        assert migration._legacy_binding_ref(tmp_path, topology, {}, "manager", "leafKey") is None
    assert (
        migration._legacy_binding_ref(tmp_path, topology, {}, "manager", "replacementForLeaf")
        is None
    )

    topology.parent.return_value = None
    with pytest.raises(migration.TerminalCatalogMigrationError, match="has no master"):
        migration.task_ref_for_role(topology, leaf, "architect")


def test_terminal_binding_conflicts_reap_dead_occupants_and_keep_live_ones(tmp_path: Path) -> None:
    assignment = importlib.import_module("agents_remember.serving.terminal_task_assignment")
    opener = importlib.import_module("agents_remember.serving.terminal_opener")
    leaf = _task_ref("master/leaf.json")
    catalog = Mock()
    host = Mock()
    assert (
        assignment.task_binding_conflict_owner(
            catalog,
            task_document_ref=None,
            session_id="seat",
            seat_role="worker",
            host=host,
        )
        is None
    )
    owner = SimpleNamespace(id="owner", tmux_name="owner-tmux")
    catalog.active_for_task.side_effect = [owner, None]
    host.has_session.return_value = False
    assert (
        assignment.task_binding_conflict_owner(
            catalog,
            task_document_ref=leaf,
            session_id="seat",
            seat_role="worker",
            host=host,
        )
        is None
    )
    catalog.mark_exited.assert_called_with("owner")

    catalog.active_for_task.side_effect = [owner, owner]
    catalog.mark_exited.reset_mock()
    assert (
        assignment.task_binding_conflict_owner(
            catalog,
            task_document_ref=leaf,
            session_id="seat",
            seat_role="worker",
            host=host,
        )
        == "owner"
    )
    catalog.mark_exited.assert_called_once_with("owner")

    assert (
        assignment.replacement_binding_conflict_owner(
            catalog,
            task_document_ref=None,
            session_id="seat",
            seat_role="worker",
            host=host,
        )
        is None
    )
    skipped = SimpleNamespace(
        id="seat",
        status="running",
        binding_role="worker",
        replacement_for_task_document_ref=leaf,
        tmux_name="self",
    )
    dead = SimpleNamespace(**{**skipped.__dict__, "id": "dead", "tmux_name": "dead-tmux"})
    live = SimpleNamespace(**{**skipped.__dict__, "id": "live", "tmux_name": "live-tmux"})
    catalog.list.return_value = [skipped, dead, live]
    host.has_session.side_effect = lambda tmux: tmux == "live-tmux"
    assert (
        assignment.replacement_binding_conflict_owner(
            catalog,
            task_document_ref=leaf,
            session_id="seat",
            seat_role="worker",
            host=host,
        )
        == "live"
    )
    catalog.mark_exited.assert_called_with("dead")

    runtime = SimpleNamespace(
        catalog=SimpleNamespace(path=tmp_path / "logs" / "dashboard" / "terminal-sessions.json")
    )
    provenance = SimpleNamespace(task_document_ref=None, replacement_for_task_document_ref=None)
    refusal = opener._task_binding_refusal(runtime, provenance, "worker")
    assert refusal is not None and refusal.status == "task-binding-required"
    assert opener._task_binding_refusal(runtime, provenance, "terminal") is None
    provenance.task_document_ref = leaf
    provenance.replacement_for_task_document_ref = leaf
    refusal = opener._task_binding_refusal(runtime, provenance, "worker")
    assert refusal is not None and refusal.status == "task-binding-invalid"


def test_task_topology_resolve_enumeration_and_id_ambiguity(tmp_path: Path) -> None:
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    root = tmp_path / "tasks" / "repo"
    topology = refs.TaskDocumentTopology(tmp_path)
    leaf_path = root / "master" / "leaf.json"
    leaf_path.parent.mkdir(parents=True)
    leaf_path.write_text("{}", encoding="utf-8")
    leaf = _task_ref("master/leaf.json")
    with (
        patch.object(refs, "read_task_doc_with_source", side_effect=OSError("broken")),
        pytest.raises(refs.TaskDocumentRefError, match="cannot read"),
    ):
        topology.resolve(leaf)
    with (
        patch.object(
            refs,
            "read_task_doc_with_source",
            return_value=(SimpleNamespace(repo="other"), object()),
        ),
        pytest.raises(refs.TaskDocumentRefError, match="declares repo"),
    ):
        topology.resolve(leaf)

    for name in ("a.json", "b.json"):
        (root / name).write_text("{}", encoding="utf-8")

    def canonical(_repo, path):
        return _task_ref(Path(path).name)

    topology.canonical_ref = Mock(side_effect=canonical)
    topology.resolve = Mock(return_value=SimpleNamespace(document=SimpleNamespace(id="other")))
    with pytest.raises(refs.TaskDocumentRefError, match="resolved to 0 documents"):
        topology.ref_for_id("repo", root, "wanted")
    topology.resolve = Mock(return_value=SimpleNamespace(document=SimpleNamespace(id="wanted")))
    with pytest.raises(refs.TaskDocumentRefError, match="resolved to 2 documents"):
        topology.ref_for_id("repo", root, "wanted")


def test_task_topology_master_census_and_command_edges(tmp_path: Path) -> None:
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    root = tmp_path / "tasks" / "repo"
    for relative in (
        "0_archive/old/task.json",
        "master/enclosures/x/task.json",
        "master-a/task.json",
        "leaf-container/task.json",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    topology = refs.TaskDocumentTopology(tmp_path)

    def resolve(ref):
        kind = "master" if ref.path == "master-a/task.json" else "subTask"
        return SimpleNamespace(
            ref=ref,
            path=root / ref.path,
            document=SimpleNamespace(kind=kind),
        )

    topology.resolve = Mock(side_effect=resolve)
    documents = refs.repository_master_documents(topology, "repo")
    assert [document.ref.path for document in documents] == ["master-a/task.json"]

    sprint_ref = _task_ref("sprint/task.json")
    match_ref = _task_ref("master-a/task.json")
    other_ref = _task_ref("master-b/task.json")
    sprint = SimpleNamespace(ref=sprint_ref, document=SimpleNamespace(orchestrates=["master-a"]))
    candidates = (
        sprint,
        SimpleNamespace(
            ref=match_ref,
            path=Path("master-a/task.json"),
            document=SimpleNamespace(id="master-a", title="A"),
        ),
        SimpleNamespace(
            ref=other_ref,
            path=Path("master-b/task.json"),
            document=SimpleNamespace(id="master-b", title="B"),
        ),
    )
    with patch.object(refs, "repository_master_documents", return_value=candidates):
        assert topology._commanded_masters(sprint) == (candidates[1],)


def test_dispatch_target_and_library_launch_without_structural_role(tmp_path: Path) -> None:
    dispatch = importlib.import_module("agents_remember.serving.dispatch_brief")
    opening = importlib.import_module("agents_remember.serving.conversation.library.open_service")
    catalog = Mock()
    catalog.get.return_value = None
    delivery = dispatch.HostedDelivery(enabled=True, catalog=catalog)
    with pytest.raises(ValueError, match="exact running target"):
        dispatch.require_dispatch_target(
            message_kind=dispatch.DISPATCH_BRIEF_KIND,
            agent_id="missing",
            delivery=delivery,
        )

    runtime = SimpleNamespace(
        host=Mock(),
        catalog=Mock(),
        harness_registry=lambda: [],
        scope=SimpleNamespace(coordination_root=tmp_path),
    )
    runtime.catalog.get.return_value = None
    service = object.__new__(opening.ConversationOpenService)
    service._runtime = runtime
    service._opener = Mock(return_value=SimpleNamespace(status="opened"))
    record = SimpleNamespace(
        ar_session_id="seat",
        launch_context={},
        absorbed_existing=False,
        scope=SimpleNamespace(canonical_project_scope=str(tmp_path)),
        harness_id="codex",
        launch_args=(),
        resume_thread_id=None,
        ref=SimpleNamespace(vendor_conversation_id="conversation"),
    )
    result = __import__("asyncio").run(service._launch(record))
    assert result.status == "opened"
    launch = service._opener.call_args.kwargs["launch"]
    assert launch.env == {}
    record.launch_context = {"seatRole": "worker"}
    __import__("asyncio").run(service._launch(record))
    assert service._opener.call_args.kwargs["launch"].env == {"AR_SPAWN_ROLE": "worker"}
