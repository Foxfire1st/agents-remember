"""Coverage for structural-leaf seams not exercised by the domain suites."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import runpy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from agents_remember.application import provider_runtime
from agents_remember.code_quality import layering
from agents_remember.kernel.coordination_context.models import (
    CoordinationRequest,
    EnclosureResolution,
    EnclosureSelector,
)
from agents_remember.kernel.primitives import gate_policy, gate_vocab, version
from agents_remember.models.conversations import control_wire as conversation_control_wire
from agents_remember.models.conversations import evidence as conversation_evidence
from agents_remember.models.terminal_catalog import (
    TerminalCatalogEntry,
    _optional_object_list,
)
from agents_remember.providers import degradation
from agents_remember.serving.conversation.active.projector import (
    agent_authority,
    echo_ingestion,
    interaction_projection,
)
from agents_remember.serving.conversation.library.open_service import (
    _read_submission_authority,
)
from agents_remember.serving.harness_control_client import ControlPlaneClient
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.worktree_contract import ContractError, WorktreeContract


def _layers(extra: str = "") -> str:
    return (
        """
[contract]
order = ["errors", "kernel", "models", "serving"]

[package.errors]
path = "."
root_modules = ["errors.py", "__init__.py"]
present = true

[package.kernel]
path = "kernel/"
present = true

[package.models]
path = "models/"
present = true

[package.serving]
path = "serving/"
present = true
"""
        + extra
    )


def test_active_projector_components_are_importable() -> None:
    """Keep the split projector components in the leaf quality gate's derived test scope."""
    assert agent_authority.__name__.endswith("agent_authority")
    assert echo_ingestion.__name__.endswith("echo_ingestion")
    assert interaction_projection.__name__.endswith("interaction_projection")


def _tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_layering_cli_and_edges(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _tree(
        root,
        {
            "layers.toml": _layers(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": (
                "from agents_remember.models.gadget import Gadget\n"
            ),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/models/gadget.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    assert layering.main(["--project-root", str(root)]) == 1
    # A clean tree passes via the CLI too.
    clean = tmp_path / "clean"
    _tree(
        clean,
        {
            "layers.toml": _layers(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": (
                "from agents_remember.errors import AgentsRememberError\n"
            ),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    assert layering.main(["--project-root", str(clean)]) == 0
    # Missing layers.toml fails closed.
    assert layering.main(["--project-root", str(tmp_path)]) == 1


def test_layering_import_target_edges(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _tree(
        root,
        {
            "layers.toml": _layers(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": ("import os\nimport agents_remember\n"),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert report.ok


def test_layering_render_and_stale(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _tree(
        root,
        {
            "layers.toml": """
[contract]
order = ["errors", "future"]

[package.errors]
path = "."
root_modules = ["errors.py", "__init__.py"]
present = true

[package.future]
path = "future/"
present = false
arrives_in = "260731-EFA-L99"
""",
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
        },
    )
    monkeypatch.setattr(layering, "_leaf_landed", lambda _leaf: True)
    report = layering.check_layering(root)
    rendered = layering.render(report)
    assert "stale flag" in rendered


def test_coordination_resolver_cli_in_process(tmp_path: Path) -> None:
    cli = importlib.import_module("agents_remember.cli.coordination_resolver")
    workspace = tmp_path / "ws"
    (workspace / "repo-a" / "ar-memory").mkdir(parents=True)
    (workspace / "ar-coordination").mkdir(parents=True)
    code = cli.main(
        [
            "--code-repository-name",
            "repo-a",
            "--workspace-root",
            str(workspace),
            "--coordination-root",
            str(workspace / "ar-coordination"),
            "--format",
            "json",
        ]
    )
    assert code == 0


def test_version_fallback(monkeypatch) -> None:
    def _raise(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", _raise)
    reloaded = importlib.reload(version)
    assert reloaded.SERVER_VERSION


def test_gate_vocabulary_errors() -> None:
    with pytest.raises(ValueError):
        gate_vocab.coerce_gate_kind("not-a-kind")
    with pytest.raises(ValueError):
        gate_policy.coerce_decision_role("not-a-role")
    with pytest.raises(ValueError):
        gate_policy.named_gate_policy("not-a-policy")
    with pytest.raises(ValueError):
        gate_policy.make_gate_policy(
            [gate_policy.GatePolicyRule(kind="integration-approval", delegated_role="manager")]
        )
    with pytest.raises(ValueError):
        gate_policy.make_gate_policy(
            [
                gate_policy.GatePolicyRule(
                    kind="plan-approval",
                    delegated_role=None,
                    require_reviewer_verdict=True,
                )
            ]
        )


def test_drift_snapshot_removal_edges(tmp_path: Path, monkeypatch) -> None:
    drift = importlib.import_module("agents_remember.kernel.primitives.drift_snapshot")
    coordination = tmp_path / "ar-coordination"
    # Already-absent path.
    result = drift.remove_drift_snapshot(
        coordination, repository="repo", branch="main", dry_run=False
    )
    assert result["reason"] == "already-absent"
    # Dry run on an existing snapshot.
    path = drift.drift_snapshot_path(coordination, repository="repo", branch="main")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    dry = drift.remove_drift_snapshot(coordination, repository="repo", branch="main", dry_run=True)
    assert dry["would_remove"] is True

    # OSError path.
    def _unlink(*_args, **_kwargs):
        raise OSError("boom")

    monkeypatch.setattr(Path, "unlink", _unlink)
    failed = drift.remove_drift_snapshot(
        coordination, repository="repo", branch="main", dry_run=False
    )
    assert failed["reason"] == "boom"


def test_resolver_missing_reader_and_contract_edges(tmp_path: Path) -> None:
    resolver = importlib.import_module("agents_remember.kernel.coordination_context.resolver")

    with pytest.raises(ValueError):
        resolver.resolve_coordination_context(
            code_repository_name="repo-a",
            workspace_root=tmp_path,
            request=CoordinationRequest(),
        )
    with pytest.raises(ValueError):
        resolver.build_coordination_context(
            resolver.CodeRepository(name="repo-a", root=tmp_path, workspace=tmp_path),
            roots=resolver.CoordinationRoots(
                topology="internal",
                coordination_root=tmp_path,
                memory_root=tmp_path,
                onboarding_root=tmp_path,
                settings_path=tmp_path,
            ),
            storage=resolver.StorageSettings(),
            cross_repo=resolver.CrossRepoSettings(),
            resolution=EnclosureResolution(),
        )
    contracts = importlib.import_module("agents_remember.kernel.coordination_context.contracts")
    resolved = contracts.resolve_contract(
        EnclosureSelector(contract_path=tmp_path / "missing.md"),
        tmp_path,
        "repo-a",
        reader=object(),  # type: ignore[arg-type]
    )
    assert resolved == (None, tmp_path / "missing.md")


def test_resolve_contract_degrades_on_reader_failure(tmp_path: Path) -> None:
    contracts = importlib.import_module("agents_remember.kernel.coordination_context.contracts")
    contract_path = tmp_path / "contract.md"
    contract_path.write_text("present", encoding="utf-8")

    class _BrokenReader:
        def load_contract(self, _path):
            raise RuntimeError("boom")

    resolved = contracts.resolve_contract(
        EnclosureSelector(contract_path=contract_path),
        tmp_path,
        "repo-a",
        reader=_BrokenReader(),  # type: ignore[arg-type]
    )
    assert resolved == (None, contract_path)


def test_terminal_catalog_method_branches() -> None:
    entry = TerminalCatalogEntry(
        id="s1",
        label="s1",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/tmp"),
        tmux_name="t1",
        command=("x",),
        created_at="2026-08-08T00:00:00Z",
        last_attached_at="2026-08-08T00:00:00Z",
        status="terminated",
    )
    assert entry.with_attachment("2026-08-08T00:00:01Z").status == "running"
    assert entry.with_liveness_success().status == "terminated"
    assert (
        entry.with_liveness_failure(
            evidence="tmux-command-failed",
            checked_at=__import__("datetime").datetime(
                2026, 8, 8, tzinfo=__import__("datetime").timezone.utc
            ),
            failure_threshold=3,
            minimum_failure_window_seconds=5.0,
            pane_gone_failure_threshold=1,
        ).status
        == "terminated"
    )


def test_evidence_and_control_edges() -> None:
    with pytest.raises(conversation_evidence.HarnessControlError):
        conversation_evidence.clip_evidence_payload({"a": "b"}, max_bytes=0)
    with pytest.raises(conversation_evidence.HarnessControlError):
        conversation_evidence.window_native_evidence_page((), cursor=None, limit=0, byte_budget=10)
    with pytest.raises(conversation_evidence.HarnessControlError):
        conversation_evidence.window_native_evidence_page((), cursor=None, limit=1, byte_budget=0)
    frame = conversation_evidence.NativeEvidenceFrame(
        native_id="n1",
        native_parent_id=None,
        native_type="message",
        created_at="2026-08-08T00:00:00Z",
        raw={},
    )
    with pytest.raises(conversation_evidence.HarnessControlError):
        conversation_evidence.window_native_evidence_page(
            (frame,), cursor="absent", limit=1, byte_budget=1024
        )
    snapshot = conversation_control_wire.AdapterSnapshot(
        identity=conversation_control_wire.ControlIdentity(
            ar_session_id="s1", tmux_name="t1", created_at="2026-08-08T00:00:00Z"
        ),
        control="ready",
        activity="idle",
        acceptance="immediate",
    )
    assert snapshot.ar_session_id == "s1"


def test_client_transcript_and_open_helper() -> None:
    client = ControlPlaneClient()
    with patch(
        "agents_remember.serving.harness_control_client.read_control_transcript",
        return_value=(),
    ):
        result = client.read_transcript(
            object(),  # type: ignore[arg-type]
            after_sequence=0,
            limit=1,
        )
    assert isinstance(result, tuple)
    entry = object()  # type: ignore[var-annotated]
    with pytest.raises(AttributeError):
        _read_submission_authority(object(), entry)  # type: ignore[arg-type]


def test_worktree_services_unbound() -> None:
    services = importlib.import_module("agents_remember.worktrees.services")
    services.reset_worktree_services()
    with pytest.raises(services.WorktreeServicesUnboundError):
        services.worktree_services()


def test_contract_reader_branches(tmp_path: Path) -> None:
    reader = importlib.import_module(
        "agents_remember.worktrees.modules.contract_reader"
    ).WorktreeContractReader()
    coordination = tmp_path / "ar-coordination"
    assert reader.find_worktree_contract(coordination, "repo-a", "worktree-x") is None
    tasks = coordination / "tasks" / "repo-a"
    tasks.mkdir(parents=True)
    assert reader.find_worktree_contract(coordination, "repo-a", "worktree-x") is None


def test_serialization_baseline_test_lines() -> None:
    from test_model_split_baseline import (  # noqa: PLC0415
        SHARED_CONTROL,
        SHARED_EVIDENCE,
    )

    assert SHARED_EVIDENCE and SHARED_CONTROL


def test_layering_branch_units(tmp_path: Path) -> None:

    root = tmp_path / "repo"
    _tree(
        root,
        {
            "layers.toml": _layers(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/unknown.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": (
                "from agents_remember.models.gadget import Gadget\n"
                "from agents_remember.kernel.other import x\n"
            ),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/models/gadget.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    contract = layering.load_contract(root / "layers.toml")
    # package_for: unknown directory
    assert layering.package_for(Path("unknown.py"), contract) is None
    # resolve_import_target: non-package import, errors root, nested errors
    assert layering.resolve_import_target("os.path", contract) is None
    assert layering.resolve_import_target("agents_remember.errors", contract) == "errors"
    assert layering.resolve_import_target("agents_remember.errors.deep", contract) is None
    assert layering.resolve_import_target("agents_remember.models.deep", contract) == "models"
    report = layering.check_layering(root)
    assert not report.ok
    # render with violations and cycles
    layered = layering.render(report)
    assert "layering violation" in layered
    cycle_report = layering.LayeringReport(
        violations=[],
        cycles=[("kernel", "models")],
        stale_present_flags=[],
    )
    assert "layering cycle" in layering.render(cycle_report)


def test_layering_main_guard(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    _tree(
        root,
        {
            "layers.toml": _layers(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        ["layering", "--project-root", str(root)],
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(
            str(Path("mcp/src/agents_remember/code_quality/layering.py").resolve()),
            run_name="__main__",
        )
    assert exc.value.code == 0


def test_coordination_cli_main_guard(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "ws"
    (workspace / "repo-a" / "ar-memory").mkdir(parents=True)
    (workspace / "ar-coordination").mkdir(parents=True)
    monkeypatch.setattr(
        "sys.argv",
        [
            "coordination_resolver",
            "--code-repository-name",
            "repo-a",
            "--workspace-root",
            str(workspace),
            "--coordination-root",
            str(workspace / "ar-coordination"),
            "--format",
            "text",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(
            str(Path("mcp/src/agents_remember/cli/coordination_resolver.py").resolve()),
            run_name="__main__",
        )
    assert exc.value.code == 0


def test_gate_policy_human_normalization() -> None:
    policy = gate_policy.make_gate_policy(
        [
            gate_policy.GatePolicyRule(
                kind="plan-approval",
                delegated_role="human",
                require_reviewer_verdict=False,
            )
        ]
    )
    assert policy.rule_for("plan-approval").delegated_role is None


def test_provider_setup_status_stale(tmp_path: Path) -> None:
    contract = WorktreeContract(
        task_id="t",
        task_name="t",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="internal",
        coordination_root=tmp_path,
        task_root=tmp_path / "tasks" / "repo-a" / "t",
        task_artifact=tmp_path / "tasks" / "repo-a" / "t" / "task.json",
        contract_path=tmp_path / "series-contract.md",
        worktree_group=tmp_path / "worktrees" / "g",
        code_repo_path=tmp_path / "repo",
        code_source_branch="main",
        code_work_branch="w",
        code_base_commit="b",
        code_worktree=tmp_path / "repo" / "w",
    )
    progress_path = provider_runtime.setup_progress_path(contract.worktree_group)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(
        json.dumps(
            {
                "schema": "ar-provider-setup-progress/v1",
                "state": "running",
                "updatedAt": "2020-01-01T00:00:00Z",
                "startedAt": "2020-01-01T00:00:00Z",
                "completedPhases": [],
            }
        ),
        encoding="utf-8",
    )
    status = provider_runtime.provider_setup_status(contract)
    assert status is not None and status["state"] == "stale"


def test_contract_reader_series_missing(tmp_path: Path) -> None:
    reader = importlib.import_module(
        "agents_remember.worktrees.modules.contract_reader"
    ).WorktreeContractReader()
    coordination = tmp_path / "ar-coordination"
    tasks = coordination / "tasks" / "repo-a" / "task-a"
    tasks.mkdir(parents=True)
    assert (
        reader.find_task_contract(coordination, "repo-a", "task-a", parent_task=None, leaf_id=None)
        is None
    )


def test_evidence_clip_branches() -> None:
    with pytest.raises(conversation_evidence.HarnessControlError):
        conversation_evidence.clip_evidence_payload(
            {"type": "message", "message": {"role": "user", "content": "x" * 200000}},
            max_bytes=1,
        )
    page = conversation_evidence.window_native_evidence_page(
        (
            conversation_evidence.NativeEvidenceFrame(
                native_id="a",
                native_parent_id=None,
                native_type="message",
                created_at="2026-08-08T00:00:00Z",
                raw={"role": "user", "text": "x" * 300},
            ),
            conversation_evidence.NativeEvidenceFrame(
                native_id="b",
                native_parent_id=None,
                native_type="message",
                created_at="2026-08-08T00:00:00Z",
                raw={"role": "user", "text": "y" * 5000},
            ),
        ),
        cursor=None,
        limit=10,
        byte_budget=500,
    )
    assert page.truncated is True
    assert page.next_cursor == "a"


def test_terminal_catalog_liveness_running() -> None:
    entry = TerminalCatalogEntry(
        id="s1",
        label="s1",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/tmp"),
        tmux_name="t1",
        command=("x",),
        created_at="2026-08-08T00:00:00Z",
        last_attached_at="2026-08-08T00:00:00Z",
        status="running",
    )
    assert entry.with_liveness_success().status == "running"
    assert entry.with_attachment("2026-08-08T00:00:01Z").status == "running"
    assert _optional_object_list([{"a": 1}, None]) is None


def test_layering_remaining_branches(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _tree(
        root,
        {
            "layers.toml": _layers(
                """
[package.future]
path = "future/"
present = false
"""
            ).replace(
                'order = ["errors", "kernel", "models", "serving"]',
                'order = ["errors", "kernel", "models", "serving", "future"]',
            ),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/a.py": (
                "from agents_remember.models.gadget import G\n"
                "from agents_remember.kernel.b import x\n"
                "from agents_remember.future.nope import Nope\n"
            ),
            "mcp/src/agents_remember/kernel/b.py": (
                "from agents_remember.models.gadget import G\n"
            ),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/models/gadget.py": (
                "from agents_remember.kernel.b import x\n"
            ),
            "mcp/src/agents_remember/serving/__init__.py": "",
            "mcp/src/agents_remember/serving/app.py": "",
            "mcp/src/agents_remember/whatever/__init__.py": "",
            "mcp/src/agents_remember/package_data/__init__.py": "",
            "mcp/src/agents_remember/package_data/skills/x.py": "",
        },
    )
    contract = layering.load_contract(root / "layers.toml")
    assert layering.package_for(Path("whatever/x.py"), contract) is None
    assert layering.package_for(Path("future/x.py"), contract) is None
    assert layering.resolve_import_target("agents_remember.whatever.x", contract) is None
    report = layering.check_layering(root)
    assert not report.ok
    rendered = layering.render(report)
    assert "layering violation" in rendered


def test_leaf_landed_runs_git(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert layering._leaf_landed("260731-EFA-L999", project_root=root) is False

    def _boom(*_args, **_kwargs):
        raise OSError("no git")

    monkeypatch.setattr("agents_remember.code_quality.layering.run_git", _boom)
    assert layering._leaf_landed("260731-EFA-L999", project_root=root) is False


def test_evidence_tuple_and_cursor_branches() -> None:
    payload = {"items": ("a" * 1000, "b" * 1000), "type": "message"}
    clipped = conversation_evidence.clip_evidence_payload(payload, max_bytes=256)
    assert "arEvidenceContentTruncated" in clipped or "arEvidenceTruncated" in clipped
    frames = (
        conversation_evidence.NativeEvidenceFrame(
            native_id="a",
            native_parent_id=None,
            native_type="message",
            created_at="2026-08-08T00:00:00Z",
            raw={},
        ),
        conversation_evidence.NativeEvidenceFrame(
            native_id="b",
            native_parent_id=None,
            native_type="message",
            created_at="2026-08-08T00:00:00Z",
            raw={},
        ),
    )
    page = conversation_evidence.window_native_evidence_page(
        frames, cursor="a", limit=10, byte_budget=4096
    )
    assert page.frames[0].native_id == "b"


def test_terminal_catalog_landing_branches() -> None:
    terminated = TerminalCatalogEntry(
        id="s1",
        label="s1",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/tmp"),
        tmux_name="t1",
        command=("x",),
        created_at="2026-08-08T00:00:00Z",
        last_attached_at="2026-08-08T00:00:00Z",
        status="terminated",
    )
    assert terminated.with_landing(at="a", reason="r", edge="e").status == "terminated"
    assert entry_with_landing_status().with_landing(at="a", reason="r", edge="e").status == "landed"


def entry_with_landing_status() -> TerminalCatalogEntry:
    return TerminalCatalogEntry(
        id="s1",
        label="s1",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/tmp"),
        tmux_name="t1",
        command=("x",),
        created_at="2026-08-08T00:00:00Z",
        last_attached_at="2026-08-08T00:00:00Z",
        status="landed",
    )


def test_evidence_scalar_leaf() -> None:
    assert conversation_evidence._truncate_string_leaves(42, 10) == 42
    assert conversation_evidence._truncate_string_leaves(["a" * 100], 10) is not None


def test_terminal_catalog_landed_status() -> None:
    entry = TerminalCatalogEntry(
        id="s1",
        label="s1",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/tmp"),
        tmux_name="t1",
        command=("x",),
        created_at="2026-08-08T00:00:00Z",
        last_attached_at="2026-08-08T00:00:00Z",
        status="landed",
    )
    assert entry.with_liveness_success().status == "landed"


def test_contract_reader_series_exists(tmp_path: Path) -> None:
    reader = importlib.import_module(
        "agents_remember.worktrees.modules.contract_reader"
    ).WorktreeContractReader()
    coordination = tmp_path / "ar-coordination"
    tasks = coordination / "tasks" / "repo-a" / "task-a"
    tasks.mkdir(parents=True)
    series = tasks / "series-contract.md"
    series.write_text("# series\n", encoding="utf-8")
    found = reader.find_task_contract(
        coordination, "repo-a", "task-a", parent_task=None, leaf_id=None
    )
    assert found == series


def test_resolver_contract_load_failure(tmp_path: Path, monkeypatch) -> None:
    resolver = importlib.import_module("agents_remember.kernel.coordination_context.resolver")
    path = tmp_path / "contract.md"
    path.write_text("{}", encoding="utf-8")

    class _BoomReader:
        def load_contract(self, _p):
            raise RuntimeError("bad contract")

    result = resolver._contract_coordination_root(path, None, _BoomReader())
    assert result is None


def test_degradation_role_recipients_delegate(tmp_path: Path) -> None:
    calls = []

    class _Alerts:
        def role_recipients(self, coordination_root: Path, role: object) -> list[str | None]:
            calls.append((coordination_root, role))
            return ["s1"]

    assert (
        degradation._role_recipients(tmp_path, "manager", _Alerts())  # type: ignore[arg-type]
        == ["s1"]
    )
    assert calls == [(tmp_path, "manager")]


def test_contract_reader_load_failure_and_match(tmp_path: Path, monkeypatch) -> None:
    coordination = tmp_path / "ar-coordination"
    tasks = coordination / "tasks" / "repo-a" / "task-a"
    tasks.mkdir(parents=True)
    series = tasks / "series-contract.md"
    series.write_text("x", encoding="utf-8")
    reader = WorktreeContractReader()

    def _boom(_p):
        raise ContractError("bad")

    monkeypatch.setattr(reader, "load_contract", _boom)
    assert reader.find_worktree_contract(coordination, "repo-a", "worktree-x") is None

    class _FakeContract:
        worktree_group = Path("worktree-group")

    monkeypatch.setattr(reader, "worktree_group_for", lambda *a, **k: Path("worktree-group"))
    monkeypatch.setattr(reader, "load_contract", lambda _p: _FakeContract())
    found = reader.find_worktree_contract(coordination, "repo-a", "worktree-x")
    assert found == series


def test_provider_setup_status_fresh(tmp_path: Path) -> None:
    contract = WorktreeContract(
        task_id="t",
        task_name="t",
        repo_name="repo-a",
        workflow_kind="light-task",
        memory_mode="internal",
        coordination_root=tmp_path,
        task_root=tmp_path / "tasks" / "repo-a" / "t",
        task_artifact=tmp_path / "tasks" / "repo-a" / "t" / "task.json",
        contract_path=tmp_path / "series-contract.md",
        worktree_group=tmp_path / "worktrees" / "g",
        code_repo_path=tmp_path / "repo",
        code_source_branch="main",
        code_work_branch="w",
        code_base_commit="b",
        code_worktree=tmp_path / "repo" / "w",
    )
    progress_path = provider_runtime.setup_progress_path(contract.worktree_group)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(
        json.dumps(
            {
                "schema": "ar-provider-setup-progress/v1",
                "state": "running",
                "updatedAt": datetime.now(UTC).isoformat(),
                "startedAt": datetime.now(UTC).isoformat(),
                "completedPhases": [],
            }
        ),
        encoding="utf-8",
    )
    status = provider_runtime.provider_setup_status(contract)
    assert status is not None and status["state"] == "running"


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
        return 0

    with (
        patch.object(identity, "migrate_jsonl_records", side_effect=unsupported),
        patch.object(identity, "OperatorInboxStore"),
        patch.object(identity, "ExpectationRowStore"),
        pytest.raises(ValueError, match="unsupported durable schema"),
    ):
        identity.migrate_control_plane_identity_logs(tmp_path, include_agent_notifier_signals=False)


def test_quality_environment_has_a_windows_branch(tmp_path: Path) -> None:
    gate = importlib.import_module("agents_remember.worktrees.modules.code_quality_gate")
    inherited = {name: "windows-temp" for name in ("TMPDIR", "TMP", "TEMP")}
    with patch.object(gate.os, "name", "nt"), patch.dict(gate.os.environ, inherited):
        environment = gate.quality_environment(tmp_path, invocation="closeout")
    assert {name: environment[name] for name in inherited} == inherited
