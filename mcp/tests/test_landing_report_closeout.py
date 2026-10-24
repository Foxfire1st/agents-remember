"""The landing populations judged by the real gate, closeout phase and curator checklist.

F06 of MIK-R120@v1: a landed candidate (one cut card and one added file) must be judged
exactly as a clean one is, on every real consumer -- the quality check, the invariant gate,
the closeout quality phase, the workspace adapter and the checklist. The report's own cases
and the fixtures both modules share are in ``test_landing_report.py`` and
``landing_report_test_support.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.knowledge_gate.gate import (
    evaluate_leaf_gate,
    validation_findings,
)
from agents_remember.application.worktree_services import MemoryQualityAdapter
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.memory_quality.curator_checklist import (
    split_commit_owned_findings,
    write_curator_checklist,
)
from agents_remember.worktrees.modules.quality.closeout_memory import (
    run_memory_quality_phase,
)
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from landing_report_test_support import (
    _checklist,
    _clean_pair,
    _commit_all,
    _gate_candidate,
    _gate_trees,
    _land,
    _landing_counts,
    _leaf_pair,
    _leaf_port,
    _operation_context,
    _operation_contract,
    _operation_pair,
    _paired_gate_contract,
)


def test_clean_check_reports_both_with_ok_true(tmp_path: Path) -> None:
    """F06: the real check passes clean and still reports both populations."""

    for index, landed_flag in enumerate((True, False)):
        pair = _clean_pair(tmp_path / f"clean-check-{index}")
        if landed_flag:
            _land(pair)
        context = _operation_context(tmp_path / f"clean-check-{index}", pair["memory"])
        payload = run_memory_quality_check(
            pair["memory"] / "onboarding",
            checks=["integrity.onboarding_drift_check.summary"],
            drift_context=DriftCheckContext(
                code_repository_root=pair["code"],
                context=context,
                knowledge_base=None,
                landing_base=_leaf_port(pair),
            ),
            include_report_only_findings=True,
        )
        assert payload["ok"] is True
        assert payload["findingCount"] == 0
        assert payload["findings"] == []
        expected = (1, 1) if landed_flag else (0, 0)
        assert _landing_counts(payload["checks"]["knowledge.converted"]) == expected


def test_clean_gate_ready_with_landing_aboard(tmp_path: Path) -> None:
    """F06: the gate refuses nothing on a landed candidate it is ready for."""

    for index, landed_flag in enumerate((True, False)):
        pair = _clean_pair(tmp_path / f"clean-gate-{index}")
        if landed_flag:
            _land(pair)
            _commit_all(pair["memory"], "land the cut and the addition")
        refusing, reports = validation_findings(_gate_trees(pair))
        assert refusing == []
        assert reports == []


def test_clean_closeout_adapter_ok_true(tmp_path: Path) -> None:
    """F06: the closeout adapter passes clean with both populations aboard."""

    adapter = MemoryQualityAdapter()
    for index, landed_flag in enumerate((True, False)):
        pair = _clean_pair(tmp_path / f"clean-adapter-{index}")
        if landed_flag:
            _land(pair)
        root = tmp_path / f"clean-adapter-{index}"
        context = _operation_context(root, pair["memory"])
        context.contract_path = _operation_contract(root, pair)
        payload = adapter.run_check(
            pair["memory"] / "onboarding",
            checks=("integrity.onboarding_drift_check.summary",),
            drift_context=adapter.drift_context(pair["code"], context, 50),
        )
        assert payload["ok"] is True
        assert payload["findingCount"] == 0
        expected = (1, 1) if landed_flag else (0, 0)
        assert _landing_counts(payload["checks"]["knowledge.converted"]) == expected


def test_real_gate_evaluates_landed_candidate_clean(tmp_path: Path) -> None:
    """F06: evaluate_leaf_gate judges the landed candidate with no refusal."""

    for index, landed_flag in enumerate((True, False)):
        pair = _clean_pair(tmp_path / f"real-gate-{index}")
        contract = _paired_gate_contract(tmp_path / f"real-gate-{index}", pair)
        if landed_flag:
            _land(pair)
            _commit_all(pair["memory"], "land the cut and the addition")
        result = evaluate_leaf_gate(
            contract, _gate_candidate(pair), parent_memory_tip=pair["memory_base"]
        )
        assert result is not None
        assert result.worklist["state"] == "complete"
        assert result.findings == ()
        assert result.reports == ()
        assert not [
            finding for finding in (*result.findings, *result.reports) if "landing" in str(finding)
        ]


def test_real_closeout_phase_passes_with_landing_aboard(tmp_path: Path) -> None:
    """F06: the closeout quality phase passes clean with both populations."""

    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=mock.Mock(),
            memory_quality=MemoryQualityAdapter(),
            citation_guard=mock.Mock(),
            knowledge_validation=mock.Mock(),
            knowledge_gate=mock.Mock(),
        )
    )
    try:
        for index, landed_flag in enumerate((True, False)):
            pair = _clean_pair(tmp_path / f"real-phase-{index}")
            if landed_flag:
                _land(pair)
            root = tmp_path / f"real-phase-{index}"
            context = SimpleNamespace(
                onboarding_root=pair["memory"] / "onboarding",
                code_repository_root=pair["code"],
                contract_path=_operation_contract(root, pair),
            )
            result = run_memory_quality_phase(
                context, ("integrity.onboarding_drift_check.summary",)
            )
            assert result["ok"] is True
            assert result["findingCount"] == 0
            expected = (1, 1) if landed_flag else (0, 0)
            assert _landing_counts(result["checks"]["knowledge.converted"]) == expected
    finally:
        reset_worktree_services()


def test_operations_hold_both_populations_without_new_refusals(tmp_path: Path) -> None:
    """F06: the real check holds both populations without new refusals."""

    landed_pair, landed_drift = _operation_pair(tmp_path / "landed", True)
    control_pair, control_drift = _operation_pair(tmp_path / "control", False)
    landed = run_memory_quality_check(
        landed_pair["memory"] / "onboarding",
        checks=["integrity.onboarding_drift_check.summary"],
        drift_context=landed_drift,
        include_report_only_findings=True,
    )
    control = run_memory_quality_check(
        control_pair["memory"] / "onboarding",
        checks=["integrity.onboarding_drift_check.summary"],
        drift_context=control_drift,
        include_report_only_findings=True,
    )
    landed_check = landed["checks"]["knowledge.converted"]
    control_check = control["checks"]["knowledge.converted"]
    assert _landing_counts(landed_check) == (1, 1)
    assert _landing_counts(control_check) == (0, 0)
    assert landed["findingCount"] == control["findingCount"]
    assert landed["findings"] == control["findings"]
    assert landed["ok"] == control["ok"]


def test_checklist_actionable_parity_on_real_populations(tmp_path: Path) -> None:
    """F06: the checklist renders the real payloads with equal actionability."""

    landed_pair, landed_drift = _operation_pair(tmp_path / "landed", True)
    control_pair, control_drift = _operation_pair(tmp_path / "control", False)
    landed = run_memory_quality_check(
        landed_pair["memory"] / "onboarding",
        checks=["integrity.onboarding_drift_check.summary"],
        drift_context=landed_drift,
        include_report_only_findings=True,
    )
    control = run_memory_quality_check(
        control_pair["memory"] / "onboarding",
        checks=["integrity.onboarding_drift_check.summary"],
        drift_context=control_drift,
        include_report_only_findings=True,
    )
    summaries = []
    rendered = []
    for payload, pair, name in (
        (landed, landed_pair, "landed.md"),
        (control, control_pair, "control.md"),
    ):
        onboarding = pair["memory"] / "onboarding"
        code_files = sorted(
            path.name for path in pair["code"].iterdir() if path.is_file() and path.suffix == ".py"
        )
        assert code_files == ["a.py"]
        assert (onboarding / "a.py.json").is_file()
        assert list(onboarding.rglob("overview.index.json")) == []
        repair, owned = split_commit_owned_findings(payload["findings"], onboarding)
        summary = write_curator_checklist(
            _checklist(
                tmp_path / name,
                payload["reportOnlyFindings"],
                repair_findings=repair,
                commit_owned_findings=owned,
                finding_count=payload["findingCount"],
            )
        )
        summaries.append(summary)
        rendered.append((tmp_path / name).read_text(encoding="utf-8"))
    assert summaries[0]["checklistStatus"] == summaries[1]["checklistStatus"]
    assert summaries[0]["curatorActionableCount"] == summaries[1]["curatorActionableCount"]
    for payload in (landed, control):
        assert not [
            finding
            for finding in payload["findings"]
            if str(finding.get("check", "")).startswith("knowledge.landing.")
        ]
    assert "onboarding/a.py.md" in rendered[0]
    assert "notes/working.txt" in rendered[0]


def test_gate_refuses_nothing_landing(tmp_path: Path) -> None:
    """F06: the gate judges the candidate on its own rules alone."""

    pairs = []
    for index, landed_flag in enumerate((True, False)):
        pair = _leaf_pair(tmp_path / f"gate-{index}")
        pairs.append(pair)
        if landed_flag:
            memory = pair["memory"]
            (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
            (memory / "notes" / "working.txt").write_text("scratch\n", encoding="utf-8")
            _commit_all(memory, "land the cut and the addition")
    for pair in pairs:
        refusing, reports = validation_findings(_gate_trees(pair))
        for finding in list(refusing) + list(reports):
            assert "landing" not in str(finding)


def test_existing_refusal_fires_identically_with_landing(tmp_path: Path) -> None:
    """F06: an existing refusal fires the same with and without populations."""

    refusals = []
    for index, landed_flag in enumerate((True, False)):
        pair = _leaf_pair(tmp_path / f"refusal-{index}")
        if landed_flag:
            memory = pair["memory"]
            (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
            (memory / "notes" / "working.txt").write_text("scratch\n", encoding="utf-8")
        (pair["memory"] / "onboarding" / "a.py.json").write_text("{not json}\n", encoding="utf-8")
        _commit_all(pair["memory"], "break one sidecar with landing aboard")
        refusing, _ = validation_findings(_gate_trees(pair))
        assert refusing
        refusals.append([str(finding) for finding in refusing])
    assert refusals[0] == refusals[1]


def test_closeout_adapter_binds_leaf_base_through_contract(tmp_path: Path) -> None:
    """F06: the closeout adapter reports both on uncommitted work, else none."""

    adapter = MemoryQualityAdapter()
    adapter_payloads = []
    for index, landed_flag in enumerate((True, False)):
        pair = _leaf_pair(tmp_path / f"adapter-{index}")
        if landed_flag:
            memory = pair["memory"]
            (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
            (memory / "notes" / "working.txt").write_text("scratch\n", encoding="utf-8")
        root = tmp_path / f"adapter-{index}"
        context = _operation_context(root, pair["memory"])
        context.contract_path = _operation_contract(root, pair)
        drift = adapter.drift_context(pair["code"], context, 50)
        adapter_payloads.append(
            adapter.run_check(
                pair["memory"] / "onboarding",
                checks=("integrity.onboarding_drift_check.summary",),
                drift_context=drift,
            )
        )
    assert adapter_payloads[0]["findingCount"] == adapter_payloads[1]["findingCount"]
    assert adapter_payloads[0]["findings"] == adapter_payloads[1]["findings"]
    assert _landing_counts(adapter_payloads[0]["checks"]["knowledge.converted"]) == (1, 1)
    assert _landing_counts(adapter_payloads[1]["checks"]["knowledge.converted"]) == (0, 0)
