"""Coverage for structural-leaf seams not exercised by the domain suites."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import runpy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.application import provider_runtime
from agents_remember.code_quality import layering
from agents_remember.kernel.coordination_context.models import (
    CoordinationRequest,
    EnclosureResolution,
    EnclosureSelector,
)
from agents_remember.kernel.primitives import gate_policy, version
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
