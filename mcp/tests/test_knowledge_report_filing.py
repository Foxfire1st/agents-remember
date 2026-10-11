"""Rule 6: reads, previews and dry runs file no per-call report.

A diagnostic read, a watcher status read, a watcher/install dry-run preview and a
carryover plan must write nothing under ``temp/tool-reports/`` and must carry no
``reportPath`` pointer; their answers keep every content key. Only an executed mutating
action files its report (not adjudicated here: live providers are absent in this seat,
so executed mutating modes stay proof-pending).

Module rule: ``compact_*`` omits ``reportPath`` when no report is filed (the codebase's
own drift-check idiom) and keeps it when one is.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents_remember.application.memory_tools import CarryoverSelection
from agents_remember.application.runtime.install import RuntimeInstallRequest
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.core import (
    compact_runtime_install_payload,
    runtime_install_payload,
)
from agents_remember.mcp.tools.memory import (
    compact_carryover_payload,
    drift_check_payload,
    memory_carryover_plan_payload,
)
from agents_remember.mcp.tools.providers import (
    compact_diagnostics_payload,
    compact_watchers_payload,
    provider_diagnostics_payload,
    provider_watchers_payload,
)
from agents_remember.memory.baseline import base_payload
from agents_remember.memory_quality.integrity.onboarding_drift_check.summary import (
    DriftSummaryOutput,
    run_drift_summary,
)


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root / "workspace",
        transcript_root=root / "logs" / "mcp",
    )


def test_diagnostics_read_files_nothing(tmp_path: Path) -> None:
    payload = provider_diagnostics_payload(_config(tmp_path))
    assert payload.get("ok") is True
    assert "reportPath" not in payload
    assert not (tmp_path / "temp").exists()


def test_watchers_status_and_dry_run_file_nothing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    for dry_run in (False, True):
        payload = provider_watchers_payload(config, action="status", dry_run=dry_run)
        assert payload.get("ok") is True
        assert "reportPath" not in payload
    assert not (tmp_path / "temp").exists()


def test_install_dry_run_files_nothing(tmp_path: Path) -> None:
    # Providerless seats cannot install (ok False is the honest answer); the boundary holds
    # either way: a preview files nothing.
    payload = runtime_install_payload(
        _config(tmp_path), RuntimeInstallRequest(dry_run=True)
    )
    assert "reportPath" not in payload
    assert not (tmp_path / "temp").exists()


def test_carryover_plan_refusal_files_nothing(tmp_path: Path) -> None:
    # The synthetic seat holds no carryover authority, so the plan refuses; a refusal is a
    # read outcome and must write nothing.
    selection = CarryoverSelection(
        repo_id="repo",
        contract_path="master/leaf.json",
        source_memory="branch",
        official_code_ref="abc",
        source_code_ref="def",
        old_base="123",
    )
    with pytest.raises(AuthorityError):
        memory_carryover_plan_payload(_config(tmp_path), selection)
    assert not (tmp_path / "temp").exists()


def test_compacts_omit_the_pointer_only_when_nothing_is_filed() -> None:
    full = {"ok": True, "messages": ["m"], "candidates": [], "steps": []}
    assert "reportPath" not in compact_diagnostics_payload(full, None)
    assert "reportPath" not in compact_watchers_payload(full, None)
    assert "reportPath" not in compact_runtime_install_payload(full, None)
    assert "reportPath" not in compact_carryover_payload(full, None)
    assert compact_diagnostics_payload(full, "r").get("reportPath") == "r"
    assert compact_watchers_payload(full, "r").get("reportPath") == "r"
    assert compact_runtime_install_payload(full, "r").get("reportPath") == "r"
    assert compact_carryover_payload(full, "r").get("reportPath") == "r"


def _drift_context(root: Path) -> SimpleNamespace:
    code = root / "code"
    code.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=code, check=True)
    onboarding = root / "onboarding"
    onboarding.mkdir(parents=True, exist_ok=True)
    memory = root / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        onboarding_root=onboarding,
        memory_root=memory,
        coordination_root=root,
        temp_root=root / "temp",
        storage=None,
        code_repository_name="repo",
    )


def test_drift_summary_read_files_no_temp_report(tmp_path: Path) -> None:
    context = _drift_context(tmp_path)
    packet = run_drift_summary(
        code_repository_root=tmp_path / "code",
        context=context,
        output=DriftSummaryOutput(write_report=False),
    )
    assert packet.get("status") == "checked"
    assert "reportPath" not in packet
    temp = tmp_path / "temp"
    assert not temp.exists() or list(temp.rglob("*.md")) == []
    assert not (tmp_path / "logs").exists()


def test_base_payload_omits_drift_report_without_filing(tmp_path: Path) -> None:
    memory = tmp_path / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    code = tmp_path / "code"
    code.mkdir(parents=True, exist_ok=True)
    context = SimpleNamespace(
        memory_root=memory,
        code_repository_name="repo",
        topology="external",
        code_repository_root=code,
        onboarding_root=tmp_path / "onboarding",
        ledger_path=None,
    )
    context.onboarding_root.mkdir(parents=True, exist_ok=True)
    assert "drift_report" not in base_payload(context, [], None)
    assert base_payload(context, [], tmp_path / "drift.md").get("drift_report") is not None


def test_drift_check_refusal_files_nothing(tmp_path: Path) -> None:
    # The synthetic seat holds no memory scope, so the read refuses by authority.
    with pytest.raises(AuthorityError):
        drift_check_payload(_config(tmp_path), "repo")
    assert not (tmp_path / "temp").exists()


def test_explicit_report_producer_still_writes_both(tmp_path: Path) -> None:
    # The default output is the explicit report-producing caller: markdown report plus
    # observer snapshot are both filed and the pointer is kept. Only read callers opt out.
    context = _drift_context(tmp_path)
    packet = run_drift_summary(
        code_repository_root=tmp_path / "code",
        context=context,
    )
    assert packet.get("status") == "checked"
    assert packet.get("reportPath") is not None
    assert list((tmp_path / "temp").rglob("*.md")) != []
    assert list((tmp_path / "logs" / "observer" / "drift").glob("*.json")) != []
