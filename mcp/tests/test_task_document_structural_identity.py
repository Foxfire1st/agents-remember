"""Task-document identity, topology, migration, and structural gate coverage."""

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


def test_terminal_catalog_migration_maps_every_legacy_identity(tmp_path: Path) -> None:
    migration = importlib.import_module("agents_remember.serving.terminal_catalog_migration")
    leaf = _task_ref("master/leaf.json")
    master = _task_ref("master/task.json")
    sprint = _task_ref("sprint/task.json")
    topology = Mock()

    topology.parent.side_effect = [master, sprint]
    assert migration.task_ref_for_role(topology, leaf, "architect") == sprint
    topology.parent.side_effect = [master]
    assert migration.task_ref_for_role(topology, leaf, "manager") == master
    for role in ("worker", "reviewer", "curator", "terminal"):
        topology.reset_mock()
        assert migration.task_ref_for_role(topology, leaf, role) == leaf

    topology.parent.side_effect = None
    topology.parent.return_value = None
    with pytest.raises(migration.TerminalCatalogMigrationError, match="has no master"):
        migration.task_ref_for_role(topology, leaf, "manager")
    topology.parent.side_effect = [master, None]
    with pytest.raises(migration.TerminalCatalogMigrationError, match="has no sprint"):
        migration.task_ref_for_role(topology, leaf, "architect")
    topology.parent.side_effect = None
    topology.parent.return_value = master
    topology.validate_role.side_effect = migration.TaskDocumentRefError("bad-role", "bad role")
    with pytest.raises(migration.TerminalCatalogMigrationError, match="bad role"):
        migration.task_ref_for_role(topology, leaf, "manager")

    topology = Mock()
    topology.canonical_ref.return_value = master
    topology.altitude.return_value = "master"
    topology.parent.return_value = sprint
    assert migration._legacy_named_scope(topology, {}, "manager") is None
    with pytest.raises(migration.TerminalCatalogMigrationError, match="scope is incomplete"):
        migration._legacy_named_scope(topology, {"spawnRepo": "repo"}, "manager")
    assert (
        migration._legacy_named_scope(
            topology,
            {"spawnRepo": "repo", "spawnSprint": "master"},
            "architect",
        )
        == sprint
    )
    topology.parent.return_value = None
    with pytest.raises(migration.TerminalCatalogMigrationError, match="has no sprint"):
        migration._legacy_named_scope(
            topology,
            {"spawnRepo": "repo", "spawnSprint": "master"},
            "architect",
        )
    topology.parent.return_value = sprint
    assert (
        migration._legacy_named_scope(
            topology,
            {"spawnRepo": "repo", "spawnSprint": "master"},
            "manager",
        )
        == master
    )

    row = {
        "kind": "harness",
        "seatRole": "worker",
        "leafKey": "leaf",
        "replacementForLeaf": "old",
        "spawnRepo": "repo",
        "spawnSprint": "sprint",
    }
    with patch.object(migration, "_legacy_binding_ref", side_effect=[leaf, master]):
        migrated = migration._migrate_row(tmp_path, topology, row)
    assert migrated["taskDocumentRef"] == leaf.model_dump()
    assert migrated["replacementForTaskDocumentRef"] == master.model_dump()
    assert not {"leafKey", "replacementForLeaf", "spawnRepo", "spawnSprint"}.intersection(migrated)
    with patch.object(migration, "_legacy_binding_ref", return_value=None):
        assert "taskDocumentRef" not in migration._migrate_row(tmp_path, topology, {})
    with patch.object(migration, "_migrate_row", return_value={"migrated": True}) as migrate:
        assert migration.migrate_terminal_catalog_v1(tmp_path, [{"legacy": True}]) == [
            {"migrated": True}
        ]
        migrate.assert_called_once()
    assert migration._text(" value ") == "value"
    assert migration._text(" ") is None
    assert migration._text(1) is None


def test_terminal_catalog_migration_resolves_one_real_leaf(tmp_path: Path) -> None:
    migration = importlib.import_module("agents_remember.serving.terminal_catalog_migration")
    task_root = tmp_path / "tasks" / "repo" / "master"
    task_root.mkdir(parents=True)
    (task_root / "task.json").write_text("{}", encoding="utf-8")
    (task_root / "broken.json").write_text("{", encoding="utf-8")
    (task_root / "wrong.json").write_text("{}", encoding="utf-8")
    for name in ("invalid.json", "other.json", "leaf.json"):
        (task_root / name).write_text(
            json.dumps({"schema": migration.TASK_DOCUMENT_SCHEMA}), encoding="utf-8"
        )
    resolved = SimpleNamespace(task_root=task_root, doc_id="leaf", repo_name="repo")
    topology = Mock()
    expected = _task_ref("master/leaf.json")
    topology.canonical_ref.return_value = expected

    def read(path: Path):
        if path.name == "invalid.json":
            raise ValueError("invalid")
        return SimpleNamespace(id=path.stem)

    with (
        patch.object(migration, "resolve_leaf_ref", return_value=resolved),
        patch.object(migration, "read_task_doc", side_effect=read),
    ):
        assert (
            migration.legacy_leaf_document_ref(tmp_path, topology, "repo/master/leaf") == expected
        )
        (task_root / "duplicate.json").write_text(
            json.dumps({"schema": migration.TASK_DOCUMENT_SCHEMA}), encoding="utf-8"
        )
        with (
            patch.object(
                migration,
                "read_task_doc",
                side_effect=lambda path: SimpleNamespace(
                    id="leaf" if path.name in {"leaf.json", "duplicate.json"} else path.stem
                ),
            ),
            pytest.raises(migration.TerminalCatalogMigrationError, match="2 task documents"),
        ):
            migration.legacy_leaf_document_ref(tmp_path, topology, "repo/master/leaf")

    with (
        patch.object(
            migration,
            "resolve_leaf_ref",
            side_effect=migration.LeafRefResolutionError(
                "missing", repo_name="repo", reason="not-found"
            ),
        ),
        pytest.raises(migration.TerminalCatalogMigrationError, match="leaf ref 'missing'"),
    ):
        migration.legacy_leaf_document_ref(tmp_path, topology, "missing")


def test_task_document_topology_children_and_refusals(tmp_path: Path) -> None:
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    topology = refs.TaskDocumentTopology(tmp_path)
    leaf = _task_ref("master/leaf.json")
    master = _task_ref("master/task.json")
    sprint = _task_ref("sprint/task.json")

    topology.resolve = Mock(return_value=SimpleNamespace(ref=leaf))
    topology.altitude = Mock(return_value="leaf")
    assert topology.children(leaf) == ()

    child_path = tmp_path / "tasks" / "repo" / "master" / "child.json"
    child_path.parent.mkdir(parents=True)
    child_path.write_text("{}", encoding="utf-8")
    master_document = SimpleNamespace(
        subTasks=[
            SimpleNamespace(file=None),
            SimpleNamespace(file="missing.md"),
            SimpleNamespace(file="child.md"),
            SimpleNamespace(file="child.md"),
        ]
    )
    topology.resolve = Mock(
        return_value=SimpleNamespace(
            ref=master, path=child_path.parent / "task.json", document=master_document
        )
    )
    topology.altitude = Mock(return_value="master")
    topology.canonical_ref = Mock(return_value=_task_ref("master/child.json"))
    assert topology.children(master) == (_task_ref("master/child.json"),)

    commanded = (SimpleNamespace(ref=master),)
    topology.resolve = Mock(return_value=SimpleNamespace(ref=sprint))
    topology.altitude = Mock(return_value="sprint")
    topology._commanded_masters = Mock(return_value=commanded)
    assert topology.children(sprint) == (master,)

    topology.altitude = Mock(return_value="leaf")
    with pytest.raises(refs.TaskDocumentRefError, match="has no structural task altitude"):
        topology.validate_role(leaf, "operator")

    escaped = refs.TaskDocumentRef.model_construct(repository="repo", path="../outside.json")
    topology = refs.TaskDocumentTopology(tmp_path)
    with pytest.raises(refs.TaskDocumentRefError, match="escapes"):
        topology.resolve(escaped)
    with pytest.raises(refs.TaskDocumentRefError, match="outside"):
        topology.canonical_ref("repo", tmp_path / "elsewhere.json")
    with pytest.raises(refs.TaskDocumentRefError, match="outside"):
        topology.ref_for_id("repo", tmp_path / "elsewhere", "leaf")
    assert refs.TaskDocumentTopology(tmp_path / "absent")._master_documents("repo") == ()


def test_task_document_topology_parent_fail_closed_paths(tmp_path: Path) -> None:
    refs = importlib.import_module("agents_remember.tasks.document_refs")
    topology = refs.TaskDocumentTopology(tmp_path)
    master = _task_ref("master/task.json")
    sprint = _task_ref("sprint/task.json")
    master_doc = SimpleNamespace(kind="master", orchestrates=[])
    resolved = SimpleNamespace(ref=master, document=master_doc)
    topology.resolve = Mock(return_value=resolved)

    topology._sprint_parents = Mock(return_value=())
    with pytest.raises(refs.TaskDocumentRefError, match="not commanded"):
        topology.altitude(master)
    topology._sprint_parents = Mock(
        return_value=(
            SimpleNamespace(ref=sprint),
            SimpleNamespace(ref=_task_ref("sprint-2/task.json")),
        )
    )
    with pytest.raises(refs.TaskDocumentRefError, match="multiple sprint"):
        topology.altitude(master)
    with pytest.raises(refs.TaskDocumentRefError, match="cannot resolve one parent"):
        topology.parent(master)

    master_doc.orchestrates = ["master"]
    with pytest.raises(refs.TaskDocumentRefError, match="both commands masters"):
        topology.altitude(master)
    topology._sprint_parents = Mock(return_value=())
    assert topology.parent(master) is None

    invalid_parent = SimpleNamespace(document=SimpleNamespace(kind="subTask"), ref=master)
    topology.canonical_ref = Mock(return_value=master)
    topology.resolve = Mock(side_effect=[invalid_parent])
    leaf = SimpleNamespace(
        path=tmp_path / "tasks" / "repo" / "master" / "leaf.json",
        ref=_task_ref("master/leaf.json"),
        document=SimpleNamespace(id="leaf"),
    )
    with pytest.raises(refs.TaskDocumentRefError, match="not a master"):
        topology._leaf_parent(leaf)

    undeclared = SimpleNamespace(
        document=SimpleNamespace(kind="master", subTasks=[]),
        ref=master,
    )
    topology.resolve = Mock(return_value=undeclared)
    with pytest.raises(refs.TaskDocumentRefError, match="is not declared"):
        topology._leaf_parent(leaf)


def test_structural_gate_authorization_decision_and_listing(tmp_path: Path) -> None:
    gates = importlib.import_module("agents_remember.application.structural.gate_tools")
    leaf = _task_ref("master/leaf.json")
    master = _task_ref("master/task.json")
    caller = SimpleNamespace(binding_role="orchestrator", binding_task_document_ref=master)
    resolver = Mock()
    gates._authorize_gate_target(resolver, caller, master)
    resolver.authorize_child.assert_called_with(caller, document=master, role="manager")
    caller.binding_role = "manager"
    gates._authorize_gate_target(resolver, caller, leaf)
    caller.binding_role = "architect"
    gates._authorize_gate_target(resolver, caller, master)
    caller.binding_role = "worker"
    with pytest.raises(gates.StructuralSeatError, match="cannot decide"):
        gates._authorize_gate_target(resolver, caller, leaf)

    raw = {
        "ok": True,
        "gate": {"state": "open", "kind": "reviewer-approval"},
        "wait": {"state": "waiting", "timedOut": False, "note": "pending"},
    }
    assert gates._raise_payload(raw, document=leaf, role="worker")["detail"] == "pending"
    raw["wait"].pop("note")
    assert "detail" not in gates._raise_payload(raw, document=leaf, role="worker")

    config = Mock(coordination_root=tmp_path)
    topology = Mock()
    topology.resolve.return_value = SimpleNamespace(ref=leaf, document=SimpleNamespace(id="leaf"))
    caller = SimpleNamespace(binding_role="manager", binding_task_document_ref=master)
    request = SimpleNamespace(
        task_document_ref=leaf,
        kind="reviewer-approval",
        decision="approve",
        note="approved",
        evidence_refs=[],
    )
    open_gate = SimpleNamespace(
        id="g1",
        lifecycleId="l1",
        state="open",
        kind=request.kind,
        enclosure="leaf",
        repoId="repo",
        decidingRole=None,
        evidenceRefs=[],
    )
    store = Mock()
    store.all_current.return_value = {"g1": open_gate}
    with (
        patch.object(gates, "_context", return_value=(topology, resolver, caller)),
        patch.object(gates, "GateStore", return_value=store),
        patch.object(
            gates,
            "gate_decide_tool",
            return_value={
                "ok": True,
                "state": "decided",
                "decidedVia": "orchestration",
                "decidingRole": "manager",
                "evidenceRefs": [],
            },
        ),
    ):
        decided = gates.structural_gate_decide_tool(config, request)
        assert decided["status"] == "decided"
        store.all_current.return_value = {}
        assert (
            gates.structural_gate_decide_tool(config, request)["status"]
            == "structural-gate-missing"
        )
        store.all_current.return_value = {"g1": open_gate, "g2": open_gate}
        assert (
            gates.structural_gate_decide_tool(config, request)["status"]
            == "structural-gate-ambiguous"
        )

    topology.children.return_value = (leaf,)
    topology.resolve.side_effect = [
        SimpleNamespace(document=SimpleNamespace(id="master")),
        SimpleNamespace(document=SimpleNamespace(id="leaf")),
    ]
    ignored = SimpleNamespace(enclosure=None)
    unrelated = SimpleNamespace(enclosure="other", repoId="repo")
    store.all_current.return_value = {"ignored": ignored, "unrelated": unrelated, "gate": open_gate}
    with (
        patch.object(gates, "_context", return_value=(topology, resolver, caller)),
        patch.object(gates, "GateStore", return_value=store),
    ):
        listed = gates.structural_gate_list_tool(config)
    assert listed["status"] == "listed"
    assert len(listed["gates"]) == 1


def test_structural_lifecycle_gate_and_context_refusals(tmp_path: Path) -> None:
    gates = importlib.import_module("agents_remember.application.structural.gate_tools")
    leaf = _task_ref("master/leaf.json")
    config = Mock(coordination_root=tmp_path)
    caller = SimpleNamespace(binding_role="worker", binding_task_document_ref=leaf)
    topology = Mock()
    topology.resolve.return_value = SimpleNamespace(document=SimpleNamespace(id="leaf"))
    request = SimpleNamespace(
        kind="reviewer-approval",
        ask="review",
        packet=None,
        required_decision=None,
        evidence_refs=[],
        wait=False,
    )
    raw = {
        "ok": True,
        "gate": {"state": "decided", "kind": request.kind},
        "wait": {"state": "resolved", "timedOut": False},
    }
    with (
        patch.object(gates, "_context", return_value=(topology, Mock(), caller)),
        patch.object(gates, "raise_lifecycle_gate", return_value=raw),
    ):
        assert gates.structural_lifecycle_gate_tool(config, request)["status"] == "resolved"
    for operation in (
        lambda: gates.structural_lifecycle_gate_tool(config, request),
        lambda: gates.structural_gate_decide_tool(
            config,
            SimpleNamespace(task_document_ref=leaf, kind="x"),
        ),
        lambda: gates.structural_gate_list_tool(config),
    ):
        with patch.object(gates, "_context", side_effect=gates.AmbientSeatError("no-seat", "none")):
            assert operation()["status"] == "no-seat"


def test_control_plane_identity_migration_addressing_and_row_shapes(tmp_path: Path) -> None:
    identity = importlib.import_module("agents_remember.serving.control_plane_identity_migration")
    leaf = _task_ref("master/leaf.json")
    master = _task_ref("master/task.json")
    exact = SimpleNamespace(binding_task_document_ref=master)
    context = identity.IdentityMigrationContext(
        coordination_root=tmp_path,
        topology=Mock(),
        catalog=Mock(),
    )
    context.catalog.get.return_value = exact
    assert identity._address_ref(context, {"agentId": "a"}, leaf, "agentId", "role") == master
    context.catalog.get.return_value = None
    assert identity._address_ref(context, {}, None, "agentId", "role") is None
    assert identity._address_ref(context, {}, leaf, "agentId", "role") == leaf
    with patch.object(identity, "task_ref_for_role", return_value=master):
        assert (
            identity._address_ref(
                context,
                {"role": "manager"},
                leaf,
                "agentId",
                "role",
            )
            == master
        )
    with patch.object(identity, "task_ref_for_role", side_effect=ValueError("non-structural")):
        assert (
            identity._address_ref(
                context,
                {"role": "operator"},
                leaf,
                "agentId",
                "role",
            )
            == leaf
        )

    base = {"leafKey": "leaf"}
    with (
        patch.object(identity, "legacy_leaf_document_ref", return_value=leaf),
        patch.object(identity, "_address_ref", return_value=master),
    ):
        inbox = identity._migrate_row(context, base, current="inbox/v2", kind="inbox")
        expectation = identity._migrate_row(
            context, base, current="expectation/v2", kind="expectation"
        )
        signal = identity._migrate_row(context, base, current="signal/v2", kind="signal")
        other = identity._migrate_row(context, base, current="other/v2", kind="other")
    assert set(inbox).issuperset(
        {"taskDocumentRef", "subjectTaskDocumentRef", "ownerTaskDocumentRef"}
    )
    assert expectation["taskDocumentRef"] == master.model_dump()
    assert signal["taskDocumentRef"] == master.model_dump()
    assert other == {"schema": "other/v2"}
    row: dict[str, object] = {}
    identity._set_ref(row, "taskDocumentRef", None)
    assert row == {}
    identity._set_ref(row, "taskDocumentRef", leaf)
    assert row["taskDocumentRef"] == leaf.model_dump()
    assert identity._text(" value ") == "value"
    assert identity._text(0) is None


def test_control_plane_identity_migration_schema_dispatch(tmp_path: Path) -> None:
    identity = importlib.import_module("agents_remember.serving.control_plane_identity_migration")

    def current_only(_path, _ownership, model, transform):
        current = {
            identity.OperatorInboxEntry: identity.OPERATOR_INBOX_RECORD_SCHEMA,
            identity.ExpectationRow: identity.EXPECTATION_ROW_SCHEMA,
            identity.AgentNotifierSignalRecord: identity.AGENT_NOTIFIER_SIGNAL_SCHEMA,
        }[model]
        row = {"schema": current}
        assert transform(row) is row
        return 0

    with (
        patch.object(identity, "migrate_jsonl_records", side_effect=current_only),
        patch.object(identity, "OperatorInboxStore"),
        patch.object(identity, "ExpectationRowStore"),
        patch.object(identity, "AgentNotifierSignalCooldownStore"),
    ):
        assert identity.migrate_control_plane_identity_logs(
            tmp_path, include_agent_notifier_signals=True
        ) == {"operatorInbox": 0, "expectations": 0, "agentNotifierSignals": 0}

    def unsupported(_path, _ownership, _model, transform):
        transform({"schema": "unsupported"})

    with (
        patch.object(identity, "migrate_jsonl_records", side_effect=unsupported),
        patch.object(identity, "OperatorInboxStore"),
        patch.object(identity, "ExpectationRowStore"),
        pytest.raises(ValueError, match="unsupported durable schema"),
    ):
        identity.migrate_control_plane_identity_logs(tmp_path, include_agent_notifier_signals=False)
