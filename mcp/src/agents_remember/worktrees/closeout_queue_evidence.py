"""Canonical curator and portfolio-judgment evidence for closeout scheduling."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agents_remember.models.closeout_queue import (
    MAX_QUEUE_SHORT_TEXT,
    MAX_QUEUE_TEXT,
    EvidenceFact,
    SchedulingGrade,
    SchedulingGradeInput,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.worktrees.worktree_contract import WorktreeContract

from .closeout_queue_errors import CloseoutQueueError

PRIORITY_RANK = {"critical": 0, "high": 1, "normal": 2, "low": 3}
MAX_CURATOR_SOURCE_CANDIDATES = 2048
JUDGMENT_REGISTER_SECTION = "judgment register (canonical judgment authority)"
PRIORITY_REGISTER_SECTION = "priority register (explicit judgment)"
JUDGMENT_REGISTER_HEADER = (
    "Judgment id",
    "Kind (dependency meaning, execution nature, blast radius, priority, blocker placement, "
    "reprioritization, or leaf move)",
    "Subject",
    "Decision",
    "Rationale",
    "Evidence/fact refs",
    "Author",
    "Confidence",
    "Supersedes",
)
PRIORITY_REGISTER_HEADER = (
    "Candidate/master",
    "Grade (critical, high, normal, or low)",
    "Affected dependents",
    "Judgment id",
)


@dataclass(frozen=True)
class JudgmentAuthority:
    judgment_id: str
    kind: str
    subject: str
    decision: dict[str, str]
    rationale: str
    evidence_refs: tuple[str, ...]
    author: str
    confidence: str
    supersedes: str
    source_row: str


@dataclass(frozen=True)
class PriorityAuthority:
    subject: str
    priority: str
    judgment_id: str
    source_row: str


@dataclass(frozen=True)
class GradeAuthority:
    sprint: ResolvedTaskDocument
    judgments: dict[str, JudgmentAuthority]
    priorities: dict[str, PriorityAuthority]


class _CuratorSourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sourceFile: str = Field(max_length=MAX_QUEUE_TEXT)
    onboardingFile: str = Field(max_length=MAX_QUEUE_TEXT)
    classification: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)

    @field_validator("sourceFile", "onboardingFile", "classification")
    @classmethod
    def _nonblank_candidate_fact(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("curator source-change facts must not be blank")
        return cleaned


class _CuratorDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sourceFile: str = Field(max_length=MAX_QUEUE_TEXT)
    onboardingFile: str = Field(max_length=MAX_QUEUE_TEXT)
    classification: str = Field(max_length=MAX_QUEUE_SHORT_TEXT)
    disposition: Literal[
        "reconciled",
        "preserved",
        "extended",
        "superseded",
        "contradicted",
        "no-content-impact",
        "no-route-impact",
        "capture-candidate",
    ]
    evidence: str = Field(min_length=1, max_length=MAX_QUEUE_TEXT)


class _CuratorAttestation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: Literal["ar-curator-memory-quality/v1"] = Field(alias="schema")
    checklistStatus: Literal["action-required", "ready-for-closeout"]
    curatorActionableCount: int = Field(ge=0)
    memoryRepairCount: int = Field(ge=0)
    missingOnboardingCount: int = Field(ge=0)
    staleRouteIndexCount: int = Field(ge=0)
    sourceChangeCandidateCount: int = Field(ge=0, le=MAX_CURATOR_SOURCE_CANDIDATES)
    sourceChangeCandidates: list[_CuratorSourceCandidate] = Field(
        max_length=MAX_CURATOR_SOURCE_CANDIDATES
    )
    onboardingRoot: str = Field(max_length=MAX_QUEUE_TEXT)
    reportPath: str = Field(max_length=MAX_QUEUE_TEXT)
    reportSha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _source_candidates_are_exact_and_unique(self) -> _CuratorAttestation:
        identities = [
            (row.sourceFile, row.onboardingFile, row.classification)
            for row in self.sourceChangeCandidates
        ]
        if self.sourceChangeCandidateCount != len(identities):
            raise ValueError("curator source-change count does not match its candidate list")
        if len(identities) != len(set(identities)):
            raise ValueError("curator source-change candidates must be unique")
        return self


def curator_evidence(contract: WorktreeContract) -> list[EvidenceFact]:
    """Bind the structured zero gate, rendered checklist, and required dispositions."""

    path = contract.worktree_group / "reports" / "curator-memory-quality.md"
    attestation_path = path.with_suffix(".json")
    try:
        text = path.read_text(encoding="utf-8")
        attestation = _CuratorAttestation.model_validate_json(
            attestation_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise CloseoutQueueError(
            "closeout-candidate-curator-evidence-missing",
            f"required structured curator evidence is unavailable: {exc}",
        ) from exc
    expected_report = path.resolve().as_posix()
    expected_onboarding = (
        (contract.memory_worktree / "onboarding").resolve().as_posix()
        if contract.memory_worktree is not None
        else ""
    )
    status_lines = [line.strip() for line in text.splitlines() if line.startswith("- Status:")]
    if (
        attestation.schema_ != "ar-curator-memory-quality/v1"
        or attestation.checklistStatus != "ready-for-closeout"
        or attestation.curatorActionableCount != 0
        or attestation.memoryRepairCount != 0
        or attestation.missingOnboardingCount != 0
        or attestation.staleRouteIndexCount != 0
        or attestation.reportPath != expected_report
        or attestation.onboardingRoot != expected_onboarding
        or status_lines != ["- Status: **ready-for-closeout**"]
    ):
        raise CloseoutQueueError(
            "closeout-candidate-memory-not-ready",
            "structured curator evidence does not prove the exact ready-for-closeout contract",
        )
    if hashlib.sha256(text.encode()).hexdigest() != attestation.reportSha256:
        raise CloseoutQueueError(
            "closeout-candidate-curator-evidence-stale",
            "curator checklist bytes do not match the structured attestation",
        )
    evidence = [_evidence_fact(path), _evidence_fact(attestation_path)]
    if attestation.sourceChangeCandidates:
        evidence.append(_coherence_evidence(contract, attestation.sourceChangeCandidates))
    return evidence


def curator_evidence_blockers(
    contract: WorktreeContract,
    expected: list[EvidenceFact],
    *,
    required: bool,
) -> list[str]:
    """Compare the exact current curator evidence at a candidate boundary."""

    try:
        current = curator_evidence(contract) if required else []
    except CloseoutQueueError:
        return ["memory-readiness-evidence-stale"]
    return [] if current == expected else ["memory-readiness-evidence-stale"]


def _coherence_evidence(
    contract: WorktreeContract, candidates: list[_CuratorSourceCandidate]
) -> EvidenceFact:
    path = contract.task_root / "notes" / "reports" / f"{contract.leaf_id}-curator-report.md"
    try:
        coherence = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CloseoutQueueError(
            "closeout-candidate-curator-disposition-missing",
            f"source-change candidates require the canonical coherence report: {path}",
        ) from exc
    dispositions = _coherence_dispositions(coherence)
    expected = {
        (candidate.sourceFile, candidate.onboardingFile, candidate.classification)
        for candidate in candidates
    }
    observed = {(row.sourceFile, row.onboardingFile, row.classification) for row in dispositions}
    if len(dispositions) != len(observed) or observed != expected:
        raise CloseoutQueueError(
            "closeout-candidate-curator-disposition-missing",
            "the canonical disposition table must exactly match every structured source-change candidate",
        )
    return _evidence_fact(path)


def _coherence_dispositions(text: str) -> list[_CuratorDisposition]:
    heading = "## Source-change dispositions"
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == heading]
    if len(starts) != 1:
        raise CloseoutQueueError(
            "closeout-candidate-curator-disposition-invalid",
            f"coherence report requires exactly one {heading!r} section",
        )
    body: list[str] = []
    for line in lines[starts[0] + 1 :]:
        if line.strip().startswith("## "):
            break
        if line.strip():
            body.append(line.strip())
    expected_header = [
        "Source file",
        "Onboarding file",
        "Classification",
        "Disposition",
        "Evidence",
    ]
    if len(body) < 2 or _split_markdown_row(body[0]) != expected_header:
        raise CloseoutQueueError(
            "closeout-candidate-curator-disposition-invalid",
            "source-change dispositions require the canonical five-column header",
        )
    separator = _split_markdown_row(body[1])
    if len(separator) != 5 or any(re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator):
        raise CloseoutQueueError(
            "closeout-candidate-curator-disposition-invalid",
            "source-change dispositions require one rectangular Markdown separator row",
        )
    rows: list[_CuratorDisposition] = []
    for line in body[2:]:
        cells = _split_markdown_row(line)
        if len(cells) != 5:
            raise CloseoutQueueError(
                "closeout-candidate-curator-disposition-invalid",
                "source-change disposition rows must have exactly five cells",
            )
        try:
            rows.append(
                _CuratorDisposition.model_validate(
                    {
                        "sourceFile": cells[0],
                        "onboardingFile": cells[1],
                        "classification": cells[2],
                        "disposition": cells[3],
                        "evidence": cells[4],
                    }
                )
            )
        except ValidationError as exc:
            raise CloseoutQueueError(
                "closeout-candidate-curator-disposition-invalid", str(exc)
            ) from exc
    return rows


def canonical_grade(
    raw: dict[str, Any] | None,
    *,
    authority: GradeAuthority,
    candidate_ref: TaskDocumentRef,
    owning_master: TaskDocumentRef,
) -> tuple[SchedulingGrade, str, list[EvidenceFact]]:
    """Resolve a small caller assertion against both canonical planning registers."""

    if raw is None:
        raise CloseoutQueueError("closeout-grade-required", "set-grade requires grade")
    try:
        asserted = SchedulingGradeInput.model_validate(raw)
    except ValidationError as exc:
        raise CloseoutQueueError("closeout-grade-invalid", str(exc)) from exc
    priority = authority.priorities.get(candidate_ref.key) or authority.priorities.get(
        owning_master.key
    )
    if priority is None:
        raise CloseoutQueueError(
            "closeout-grade-priority-missing",
            "candidate and owning master are absent from the canonical Priority Register",
        )
    if priority.priority != asserted.priority or priority.judgment_id != asserted.judgmentId:
        raise CloseoutQueueError(
            "closeout-grade-priority-mismatch",
            "grade does not exactly match the canonical Priority Register row",
        )
    judgment = authority.judgments.get(asserted.judgmentId)
    if judgment is None:
        raise CloseoutQueueError(
            "closeout-grade-judgment-missing",
            f"canonical Judgment Register has no row {asserted.judgmentId!r}",
        )
    _require_matching_judgment(asserted, priority, judgment)
    try:
        grade = SchedulingGrade(
            priority=asserted.priority,
            urgency=asserted.urgency,
            risk=asserted.risk,
            judgmentId=asserted.judgmentId,
            subject=judgment.subject,
            rationale=judgment.rationale,
            evidenceRefs=list(judgment.evidence_refs),
            decidedBy=judgment.author,
            confidence=judgment.confidence,
            supersedes=judgment.supersedes,
        )
    except ValidationError as exc:
        raise CloseoutQueueError("closeout-grade-invalid", str(exc)) from exc
    digest = hashlib.sha256(f"{priority.source_row}\n{judgment.source_row}".encode()).hexdigest()
    evidence = [
        _task_relative_evidence(authority.sprint.path.parent, ref, "grade.evidenceRefs")
        for ref in grade.evidenceRefs
    ]
    return grade, digest, evidence


def canonical_blocker_abort(
    judgment_id: str | None,
    *,
    authority: GradeAuthority,
    master_ref: TaskDocumentRef,
    graph_revision: str,
) -> None:
    """Require a durable strategist/orchestrator judgment before abandoning an atomic block."""

    key = (judgment_id or "").strip()
    judgment = authority.judgments.get(key)
    if (
        judgment is None
        or judgment.kind != "atomic-blocker-abort"
        or judgment.subject != master_ref.key
        or judgment.author not in {"strategist", "orchestrator"}
        or judgment.decision != {"blocker": "abort", "graphRevision": graph_revision}
    ):
        raise CloseoutQueueError(
            "atomic-blocker-abort-judgment-invalid",
            "blocker abort requires an exact canonical strategist/orchestrator judgment for this master and graph revision",
        )
    for ref in judgment.evidence_refs:
        _task_relative_evidence(authority.sprint.path.parent, ref, "blocker abort evidence")


def _require_matching_judgment(
    asserted: SchedulingGradeInput,
    priority: PriorityAuthority,
    judgment: JudgmentAuthority,
) -> None:
    if judgment.kind not in {"priority", "reprioritization"}:
        raise CloseoutQueueError(
            "closeout-grade-judgment-invalid", "grade judgment has the wrong canonical kind"
        )
    if judgment.author not in {"strategist", "orchestrator"}:
        raise CloseoutQueueError(
            "closeout-grade-author-refused",
            "only strategist/orchestrator Judgment Register rows may grade queue candidates",
        )
    if judgment.subject != priority.subject:
        raise CloseoutQueueError(
            "closeout-grade-subject-mismatch",
            "Priority and Judgment Register subjects do not match exactly",
        )
    expected = {"priority": asserted.priority}
    if asserted.urgency is not None:
        expected["urgency"] = asserted.urgency
    if asserted.risk is not None:
        expected["risk"] = asserted.risk
    if judgment.decision != expected:
        raise CloseoutQueueError(
            "closeout-grade-values-mismatch",
            "priority, urgency, and risk must exactly match the canonical judgment decision",
        )


def planning_authorities(
    sprint: ResolvedTaskDocument,
) -> tuple[dict[str, JudgmentAuthority], dict[str, PriorityAuthority]]:
    """Parse the exact canonical Judgment and Priority Register tables once per projection."""

    judgments: dict[str, JudgmentAuthority] = {}
    priorities: dict[str, PriorityAuthority] = {}
    for section in sprint.document.sections:
        heading = section.heading.strip().casefold()
        if heading == JUDGMENT_REGISTER_SECTION:
            _append_judgments(judgments, section.body)
        elif heading == PRIORITY_REGISTER_SECTION:
            _append_priorities(priorities, section.body)
    return judgments, priorities


def _append_judgments(target: dict[str, JudgmentAuthority], body: str) -> None:
    for row, cells in _table_rows(body, 9):
        (
            judgment_id,
            kind,
            subject,
            decision,
            rationale,
            refs,
            author,
            confidence,
            supersedes,
        ) = cells
        if judgment_id in target:
            raise CloseoutQueueError(
                "closeout-grade-judgment-duplicate",
                f"canonical Judgment Register repeats {judgment_id!r}",
            )
        evidence_refs = tuple(_reference_cells(refs))
        if (
            not all((judgment_id, kind, subject, rationale, author, confidence))
            or not evidence_refs
        ):
            raise CloseoutQueueError(
                "closeout-grade-judgment-invalid",
                "canonical judgment rows require identity, subject, rationale, evidence, author, and confidence",
            )
        target[judgment_id] = JudgmentAuthority(
            judgment_id=judgment_id,
            kind=kind,
            subject=subject,
            decision=_decision_values(decision),
            rationale=rationale,
            evidence_refs=evidence_refs,
            author=author,
            confidence=confidence,
            supersedes=supersedes,
            source_row=row,
        )


def _append_priorities(target: dict[str, PriorityAuthority], body: str) -> None:
    for row, cells in _table_rows(body, 4):
        subject, priority, _dependents, judgment_id = cells
        if subject in target:
            raise CloseoutQueueError(
                "closeout-grade-priority-duplicate",
                f"canonical Priority Register repeats {subject!r}",
            )
        if priority not in PRIORITY_RANK or not judgment_id:
            raise CloseoutQueueError(
                "closeout-grade-priority-invalid",
                "Priority Register rows require critical/high/normal/low and a judgment id",
            )
        target[subject] = PriorityAuthority(
            subject=subject,
            priority=priority,
            judgment_id=judgment_id,
            source_row=row,
        )


def _table_rows(body: str, width: int) -> list[tuple[str, list[str]]]:
    expected_header = {
        len(JUDGMENT_REGISTER_HEADER): JUDGMENT_REGISTER_HEADER,
        len(PRIORITY_REGISTER_HEADER): PRIORITY_REGISTER_HEADER,
    }.get(width)
    if expected_header is None:
        raise CloseoutQueueError(
            "closeout-grade-register-shape-invalid",
            f"no canonical scheduling register has {width} columns",
        )
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if (
        len(lines) < 2
        or not lines[0].startswith("|")
        or not lines[0].endswith("|")
        or tuple(_split_markdown_row(lines[0])) != expected_header
    ):
        raise CloseoutQueueError(
            "closeout-grade-register-shape-invalid",
            "canonical scheduling register requires its exact template header",
        )
    if not lines[1].startswith("|") or not lines[1].endswith("|"):
        raise CloseoutQueueError(
            "closeout-grade-register-shape-invalid",
            "canonical scheduling register requires one rectangular Markdown separator row",
        )
    separator = _split_markdown_row(lines[1])
    if len(separator) != width or any(
        re.fullmatch(r":?-{3,}:?", cell) is None for cell in separator
    ):
        raise CloseoutQueueError(
            "closeout-grade-register-shape-invalid",
            "canonical scheduling register requires one rectangular Markdown separator row",
        )
    rows: list[tuple[str, list[str]]] = []
    for line in lines[2:]:
        if not line.startswith("|") or not line.endswith("|"):
            raise CloseoutQueueError(
                "closeout-grade-register-shape-invalid",
                "canonical scheduling register rows require outer Markdown pipes",
            )
        cells = _split_markdown_row(line)
        if len(cells) != width:
            raise CloseoutQueueError(
                "closeout-grade-register-shape-invalid",
                f"canonical register row has {len(cells)} cells, expected {width}",
            )
        rows.append((line, cells))
    return rows


def _split_markdown_row(line: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line[1:-1]:
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(character)
    current.append("\\" if escaped else "")
    cells.append("".join(current).strip())
    return cells


def _decision_values(value: str) -> dict[str, str]:
    parts = [part.strip() for part in value.replace(",", ";").split(";") if part.strip()]
    decision: dict[str, str] = {}
    for part in parts:
        key, separator, raw = part.partition("=")
        key = key.strip()
        raw = raw.strip()
        if separator != "=" or not key or not raw or key in decision:
            raise CloseoutQueueError(
                "closeout-grade-decision-invalid",
                "canonical priority decisions use unique nonblank key=value cells",
            )
        decision[key] = raw
    return decision


def _reference_cells(value: str) -> list[str]:
    return [item.strip().strip("`") for item in value.split(",") if item.strip().strip("`")]


def _task_relative_evidence(root: Path, value: str, label: str) -> EvidenceFact:
    supplied = Path(value.strip())
    if not value.strip() or supplied.is_absolute():
        raise CloseoutQueueError(
            "closeout-candidate-evidence-outside-task", f"{label} must be task-relative"
        )
    resolved_root = root.resolve()
    resolved = (resolved_root / supplied).resolve(strict=False)
    if not resolved.is_relative_to(resolved_root):
        raise CloseoutQueueError(
            "closeout-candidate-evidence-outside-task", f"{label} escapes its task root"
        )
    if not resolved.is_file():
        raise CloseoutQueueError(
            "closeout-candidate-evidence-missing", f"{label} does not exist: {value}"
        )
    return _evidence_fact(resolved, stored_path=supplied.as_posix())


def _evidence_fact(path: Path, *, stored_path: str | None = None) -> EvidenceFact:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise CloseoutQueueError(
            "closeout-candidate-evidence-missing", f"evidence does not exist: {path}"
        ) from exc
    return EvidenceFact(path=stored_path or path.resolve().as_posix(), sha256=digest)
