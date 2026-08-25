"""Focused tests for canonical curator and Markdown evidence parsing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.worktrees.queue import closeout_queue_evidence as evidence
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError


def _contract(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _disposition_report(*rows: str) -> str:
    return "\n".join(
        (
            "# Curator report",
            "",
            "## Source-change dispositions",
            "| Source file | Onboarding file | Classification | Disposition | Evidence |",
            "| --- | --- | --- | --- | --- |",
            *rows,
            "",
            "## Next section",
        )
    )


def test_disposition_parser_accepts_exact_rows_and_refuses_shape_drift() -> None:
    report = _disposition_report(
        "| mcp/a.py | onboarding/a.md | refresh | reconciled | checked owner |"
    )
    rows = evidence._coherence_dispositions(report)
    assert len(rows) == 1
    assert evidence._disposition_identity(rows[0]) == (
        "mcp/a.py",
        "onboarding/a.md",
        "refresh",
    )
    candidate = evidence._CuratorSourceCandidate(
        sourceFile="mcp/a.py",
        onboardingFile="onboarding/a.md",
        classification="refresh",
    )
    assert evidence._candidate_identity(candidate) == evidence._disposition_identity(rows[0])

    invalid_reports = (
        "# no disposition section",
        report.replace("## Source-change dispositions", "## Other"),
        report.replace("| Evidence |", "| Wrong |"),
        report.replace("| --- | --- | --- | --- | --- |", "| -- | --- | --- | --- | --- |"),
        report.replace("| checked owner |", "| unknown | checked owner |"),
        report.replace("| reconciled |", "| invented |"),
    )
    for invalid in invalid_reports:
        with pytest.raises(CloseoutQueueError):
            evidence._coherence_dispositions(invalid)


def test_section_and_register_parsers_cover_termination_and_fail_closed() -> None:
    lines = ["before", "## Exact", "", "| A | B |", "| --- | --- |", "## Later"]
    assert evidence._required_section_start(lines, "## Exact") == 1
    assert evidence._heading_indices(lines, "## Exact") == [1]
    assert evidence._section_body(lines[2:]) == ["| A | B |", "| --- | --- |"]
    assert evidence._section_body(["row", "", "tail"]) == ["row", "tail"]
    assert evidence._nonblank_lines(["", "one", "", "two"]) == ["one", "two"]
    with pytest.raises(CloseoutQueueError, match="exactly one"):
        evidence._required_section_start(["## Exact", "## Exact"], "## Exact")
    with pytest.raises(CloseoutQueueError, match="five-column"):
        evidence._require_disposition_header([], ["one"] * 5)

    header = "| " + " | ".join(evidence.PRIORITY_REGISTER_HEADER) + " |"
    separator = "| --- | --- | --- | --- |"
    evidence._require_register_header([header, separator], evidence.PRIORITY_REGISTER_HEADER)
    evidence._require_register_separator(separator, 4)
    source, cells = evidence._register_row("| leaf | high | two | J-1 |", 4)
    assert source.startswith("| leaf")
    assert cells == ["leaf", "high", "two", "J-1"]

    invalid_calls = (
        lambda: evidence._require_register_header([], evidence.PRIORITY_REGISTER_HEADER),
        lambda: evidence._require_register_header(
            [header.replace("Candidate/master", "Candidate"), separator],
            evidence.PRIORITY_REGISTER_HEADER,
        ),
        lambda: evidence._require_register_separator("--- | ---", 2),
        lambda: evidence._require_register_separator("| -- | --- |", 2),
        lambda: evidence._register_row("leaf | high |", 2),
        lambda: evidence._register_row("| leaf | high |", 3),
    )
    for call in invalid_calls:
        with pytest.raises(CloseoutQueueError):
            call()


def test_coherence_evidence_binds_exact_candidates_and_report_bytes(tmp_path: Path) -> None:
    report_path = tmp_path / "notes" / "reports" / "L1-curator-report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        _disposition_report(
            "| mcp/a.py | onboarding/a.md | refresh | reconciled | checked owner |"
        ),
        encoding="utf-8",
    )
    candidate = evidence._CuratorSourceCandidate(
        sourceFile="mcp/a.py",
        onboardingFile="onboarding/a.md",
        classification="refresh",
    )
    contract = _contract(task_root=tmp_path, leaf_id="L1")
    fact = evidence._coherence_evidence(contract, [candidate])
    assert fact.path == report_path.as_posix()

    duplicate = report_path.read_text(encoding="utf-8").replace(
        "## Next section",
        "| mcp/a.py | onboarding/a.md | refresh | reconciled | duplicate |\n## Next section",
    )
    report_path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(CloseoutQueueError, match="exactly match"):
        evidence._coherence_evidence(contract, [candidate])


def test_curator_evidence_requires_exact_attestation_and_digest(tmp_path: Path) -> None:
    reports = tmp_path / "group" / "reports"
    reports.mkdir(parents=True)
    report = reports / "curator-memory-quality.md"
    text = "# Curator memory quality\n- Status: **ready-for-closeout**\n"
    report.write_text(text, encoding="utf-8")
    onboarding = tmp_path / "memory" / "onboarding"
    contract = _contract(
        worktree_group=tmp_path / "group",
        memory_worktree=tmp_path / "memory",
        task_root=tmp_path,
        leaf_id="L1",
    )
    attestation = {
        "schema": "ar-curator-memory-quality/v1",
        "checklistStatus": "ready-for-closeout",
        "curatorActionableCount": 0,
        "memoryRepairCount": 0,
        "missingOnboardingCount": 0,
        "staleRouteIndexCount": 0,
        "sourceChangeCandidateCount": 0,
        "sourceChangeCandidates": [],
        "onboardingRoot": onboarding.resolve().as_posix(),
        "reportPath": report.resolve().as_posix(),
        "reportSha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    report.with_suffix(".json").write_text(json.dumps(attestation), encoding="utf-8")
    facts = evidence.curator_evidence(contract)
    assert [Path(fact.path).name for fact in facts] == [
        "curator-memory-quality.md",
        "curator-memory-quality.json",
    ]
    assert evidence._expected_onboarding_root(_contract(memory_worktree=None)) == ""

    evidence._require_curator_report_digest(text, attestation["reportSha256"])
    with pytest.raises(CloseoutQueueError, match="stale"):
        evidence._require_curator_report_digest(text, "0" * 64)
    parsed = evidence._CuratorAttestation.model_validate(attestation)
    evidence._require_curator_attestation(
        parsed,
        expected_report=report.resolve().as_posix(),
        expected_onboarding=onboarding.resolve().as_posix(),
        status_lines=["- Status: **ready-for-closeout**"],
    )
    with pytest.raises(CloseoutQueueError, match="ready-for-closeout"):
        evidence._require_curator_attestation(
            parsed,
            expected_report="other",
            expected_onboarding=onboarding.resolve().as_posix(),
            status_lines=["- Status: **ready-for-closeout**"],
        )


def test_curator_evidence_includes_required_source_coherence(tmp_path: Path) -> None:
    reports = tmp_path / "group" / "reports"
    reports.mkdir(parents=True)
    report = reports / "curator-memory-quality.md"
    text = "# Curator memory quality\n- Status: **ready-for-closeout**\n"
    report.write_text(text, encoding="utf-8")
    report.with_suffix(".json").write_text("{}", encoding="utf-8")
    candidate = evidence._CuratorSourceCandidate(
        sourceFile="mcp/a.py",
        onboardingFile="onboarding/a.md",
        classification="refresh",
    )
    attestation = SimpleNamespace(
        sourceChangeCandidates=[candidate],
        reportSha256=hashlib.sha256(text.encode()).hexdigest(),
    )
    contract = _contract(
        worktree_group=tmp_path / "group",
        memory_worktree=None,
    )
    coherence = evidence.EvidenceFact(path="coherence.md", sha256="a" * 64)
    with (
        pytest.MonkeyPatch.context() as monkeypatch,
    ):
        monkeypatch.setattr(
            evidence._CuratorAttestation,
            "model_validate_json",
            lambda _text: attestation,
        )
        monkeypatch.setattr(evidence, "_require_curator_attestation", lambda *_a, **_k: None)
        monkeypatch.setattr(evidence, "_require_curator_report_digest", lambda *_a: None)
        monkeypatch.setattr(
            evidence,
            "_evidence_fact",
            lambda path: evidence.EvidenceFact(path=path.as_posix(), sha256="b" * 64),
        )
        monkeypatch.setattr(evidence, "_coherence_evidence", lambda *_a: coherence)
        facts = evidence.curator_evidence(contract)
    assert facts[-1] == coherence
