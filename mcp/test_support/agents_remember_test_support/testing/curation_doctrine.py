"""The retired optional-curation doctrine, as a census a test can read.

A shipped instruction sentence is product: a spawned curator reads its brief literally, so a
sentence presenting the memory-quality operation as a developer-request-only diagnostic, or as
something a named scoped check may stand in for, tells that seat complete curation is somebody
else's decision. No per-file case can catch that defect, because each individual sentence is
plausible alone -- what has to hold is the agreement of the shipped corpus with the rule.

This module owns the reading half of that check and nothing else: the registry of retired
sentences with the surface each lived on, the exact statement every canonical source that must
state the rule states it in, and the readers a test calls. It is deliberately free of pytest and
of any repository constant so a case can point it at a staged or synthetic tree; the caller
supplies the repository root.

Matching is on a normalized reading -- markdown emphasis stripped, line wrapping collapsed -- and
against the whole retired sentence, so a statement re-inserted with different emphasis or at a
different column is still the same statement, while doctrine that legitimately survives (an
explicit developer request still governs full code quality and full tests) cannot read as a
regression. A statement is reported only on the files that shipped it.

This is not a semantic check. A corpus that denied the rule in fresh vocabulary this registry has
never seen would pass; what it buys is that the exact retired sentences cannot come back, and
that the shipped corpus keeps the sentence stating the rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: The one sentence every canonical surface that must state the rule carries in some phrasing.
#: A row of :data:`CURATION_COMPLETENESS_STATEMENTS` is the exact form that surface uses.
COMPLETE_CURATION_RULE = "Curation is always complete"

#: The nine generated skill copies ``scripts/sync-skills.py`` owns, relative to the repository
#: root. A stale copy is a real defect: a seat on that harness reads the old sentence.
GENERATED_SKILL_COPIES = (
    "mcp/src/agents_remember/package_data/runtime/skills",
    ".claude/skills",
    ".codex/skills",
    ".cursor/skills",
    ".github-vscode/skills",
    ".hermes/skills",
    ".openclaw/workspace/skills",
    ".pi/skills",
    ".agents/skills",
)

#: Every shipped instruction surface the census ranges over: the canonical tree, then the copies.
CURATION_DOCTRINE_SURFACES = ("skills", *GENERATED_SKILL_COPIES)


def normalize_statement(text: str) -> str:
    """Strip markdown emphasis and collapse whitespace so a sentence is read by its words."""

    return " ".join(re.sub(r"[*`]", "", text).split())


@dataclass(frozen=True)
class RetiredCurationStatement:
    """One shipped sentence the complete-curation ruling retired, with where it lived.

    ``sources`` is the exact set of files that shipped it, and it is also the set in which the
    sentence is a defect when it returns. ``probe`` is the fragment unique to the retired
    wording; it is the short form a report and a failure message name the statement by, and it
    is deliberately NOT the matcher -- see :func:`retired_statement_findings`.
    """

    statement: str
    sources: tuple[str, ...]
    probe: str
    reason: str


RETIRED_CURATION_STATEMENTS: tuple[RetiredCurationStatement, ...] = (
    RetiredCurationStatement(
        statement=(
            "An explicitly requested narrow `memory_quality_check` or `curator_coherence` "
            'diagnostic, always with `contract_path="<enclosure-contract-path>"`; these are '
            "never routine closeout/integration prerequisites."
        ),
        sources=("skills/l-01-agent-lifecycles/templates/curator-brief.md",),
        probe="An explicitly requested narrow",
        reason=(
            "fragment 1: the curator's own brief called the complete operation a narrow optional "
            "diagnostic that is never a routine prerequisite"
        ),
    ),
    RetiredCurationStatement(
        statement=(
            "Do not run a full memory suite or create a curator certification for routine "
            "curation. Full memory quality is a separate operation only on explicit developer "
            "request."
        ),
        sources=("skills/l-01-agent-lifecycles/templates/curator-brief.md",),
        probe="is a separate operation only on",
        reason="fragment 2: it forbade the complete operation for routine curation",
    ),
    RetiredCurationStatement(
        statement=(
            "a narrow `memory_quality_check` or `curator_coherence` only on an explicit "
            "developer request for a named affected check or curator certification"
        ),
        sources=("skills/l-01-agent-lifecycles/roles/curator.md",),
        probe="only on an explicit developer request",
        reason=(
            "fragment 3: the curator role's write surface made completeness a developer decision"
        ),
    ),
    RetiredCurationStatement(
        statement=(
            "A finding count implausible for this change set is a measurement problem to "
            "investigate and escalate, not permission to pass incomplete onboarding."
        ),
        sources=("skills/l-01-agent-lifecycles/templates/curator-brief.md",),
        probe="not permission to pass incomplete onboarding",
        reason=(
            "fragment 4: the right teeth in a hedge's frame, so it read as permission to hand off "
            "onboarding that was never completed"
        ),
    ),
    RetiredCurationStatement(
        statement=(
            "memory-quality suites, curator certification, or independent review; full code "
            "quality, full tests, and full memory quality run only after an explicit developer "
            "request"
        ),
        sources=(
            "skills/l-01-agent-lifecycles/roles/manager.md",
            "skills/l-01-agent-lifecycles/templates/manager-brief.md",
            "skills/l-01-agent-lifecycles/operations/closeout.md",
            "skills/l-01-agent-lifecycles/core/authority.md",
        ),
        probe="full memory quality run only after",
        reason=(
            "the transaction boundary was stated over curation too, so a seat reading it deferred "
            "curation"
        ),
    ),
    RetiredCurationStatement(
        statement=(
            "curator_coherence, full memory quality, and certification are separate explicit "
            "operations"
        ),
        sources=("skills/l-01-agent-lifecycles/roles/manager.md",),
        probe="separate explicit operations",
        reason=(
            "the manager's curator dispatch described the complete operation as separate and "
            "explicit"
        ),
    ),
    RetiredCurationStatement(
        statement=(
            "Do not run or claim a full suite / full quality result unless the developer or the "
            "task brief explicitly requests that operation."
        ),
        sources=("skills/l-01-agent-lifecycles/operations/closeout.md",),
        probe="explicitly requests that operation.",
        reason=(
            "the targeted-check contract stated the full-suite rule without the curation exception"
        ),
    ),
    RetiredCurationStatement(
        statement=(
            "Full memory-quality or drift suites are separate developer-requested operations and "
            "are not routine closeout or integration gates."
        ),
        sources=("skills/l-01-agent-lifecycles/templates/onboarding-coherency.md",),
        probe="separate developer-requested operations",
        reason=(
            "the curator's own report template declared the complete operation separate and not a "
            "gate"
        ),
    ),
    RetiredCurationStatement(
        statement=(
            "Full memory quality is an explicit developer-requested operation through "
            "`c-02-memory-quality-control`, not a closeout precondition."
        ),
        sources=("skills/c-12-closeout/SKILL.md",),
        probe="is not a closeout precondition",
        reason="closeout was told curation's completed result is not its precondition",
    ),
    RetiredCurationStatement(
        statement=(
            "curator_coherence runs only when the developer explicitly requests that separate "
            "diagnostic."
        ),
        sources=("skills/l-01-agent-lifecycles/operations/curation.md",),
        probe="runs only when the developer explicitly requests",
        reason="the curation procedure made the coherence authority an explicit-request diagnostic",
    ),
    RetiredCurationStatement(
        statement="Full branch quality evidence is a separate explicit developer request.",
        sources=("skills/l-01-agent-lifecycles/roles/reviewer.md",),
        probe="Full branch quality evidence is a separate explicit developer request",
        reason=(
            "the reviewer's onboarding lens read the complete operation as a separate request "
            "rather than the pass it verifies"
        ),
    ),
)

#: Every canonical source that must state the rule, and the exact form it states it in. A row
#: exists because that sentence is the shipped answer to the retired wording in that file.
CURATION_COMPLETENESS_STATEMENTS: dict[str, tuple[str, ...]] = {
    "skills/c-02-memory-quality-control/SKILL.md": (COMPLETE_CURATION_RULE,),
    "skills/c-05-create-or-update-onboarding-files/SKILL.md": (
        "The curator's complete handoff enforces this",
    ),
    "skills/c-09-git-worktree-manager/SKILL.md": ("the curator's complete memory-quality result",),
    "skills/c-12-closeout/SKILL.md": ("curation is already complete before it starts",),
    "skills/c-13-install-and-onboard/SKILL.md": ("curation is always complete",),
    "skills/l-01-agent-lifecycles/core/authority.md": ("Curation is never deferred that way",),
    "skills/l-01-agent-lifecycles/operations/closeout.md": ("Curation is never deferred that way",),
    "skills/l-01-agent-lifecycles/operations/curation.md": (
        f"{COMPLETE_CURATION_RULE}: a named scoped check never",
    ),
    "skills/l-01-agent-lifecycles/roles/curator.md": (
        f"{COMPLETE_CURATION_RULE}: a named scoped check never",
        "curatorActionableCount=0",
        "checklistStatus=ready-for-closeout",
    ),
    "skills/l-01-agent-lifecycles/roles/manager.md": ("runs the brief's complete check set",),
    "skills/l-01-agent-lifecycles/roles/orchestrator.md": (
        "curator's complete memory-quality result travel with the edge",
    ),
    "skills/l-01-agent-lifecycles/roles/reviewer.md": (
        "full `memory_quality_check` curation evidence",
    ),
    "skills/l-01-agent-lifecycles/roles/worker.md": ("curation is the one exception",),
    "skills/l-01-agent-lifecycles/templates/curator-brief.md": (
        f"{COMPLETE_CURATION_RULE}: run the full memory-quality operation",
    ),
    "skills/l-01-agent-lifecycles/templates/manager-brief.md": (
        "Curation is never deferred that way",
    ),
    "skills/l-01-agent-lifecycles/templates/master-handover-packet.md": (
        "complete handoff reports",
    ),
    "skills/l-01-agent-lifecycles/templates/onboarding-coherency.md": (
        "Checks — curation is complete",
    ),
    "skills/l-01-agent-lifecycles/templates/verdict.md": (
        "full `memory_quality_check` curation result",
    ),
    "skills/l-01-agent-lifecycles/templates/worker-brief.md": (
        "Curation is the exception: the curator always runs the full",
    ),
    "skills/w-02-light-task-workflow/master-template.md": (COMPLETE_CURATION_RULE,),
}


def _declared_sources(retired: RetiredCurationStatement) -> set[str]:
    """Every surface-relative path in which this statement is a defect when it returns."""

    return {source.removeprefix("skills/") for source in retired.sources}


def retired_statement_findings(reading: str, surface_relative_path: str) -> list[str]:
    """Every retired statement this ALREADY-NORMALIZED reading still carries, as its reason.

    The match is the WHOLE retired sentence, on the normalized reading, restricted to the exact
    files that shipped it. A fragment match would be cheaper and false-positive-prone in both
    directions: "an explicit developer request" is preserved doctrine for full code quality and
    full tests, and "separate developer-requested operations" is the phrasing the replacement
    keeps while dropping the clause that made it a deferral. A whole-sentence match cannot lie
    about either. What it trades away is stated rather than implied: a restatement of the defect
    in different words is out of reach, and this registry does not claim to read meaning.
    """

    findings: list[str] = []
    for retired in RETIRED_CURATION_STATEMENTS:
        if surface_relative_path not in _declared_sources(retired):
            continue
        if normalize_statement(retired.statement) in reading:
            findings.append(retired.reason)
    return findings


def doctrine_files(root: Path, surface: str) -> list[Path]:
    """Every markdown file of one surface under ``root``, canonical tree or generated copy."""

    return sorted((root / surface).rglob("*.md"))


def retired_curation_findings(root: Path) -> list[str]:
    """Every retired statement still present in a shipped surface, as ``path -> reason``."""

    findings: list[str] = []
    for surface in CURATION_DOCTRINE_SURFACES:
        for path in doctrine_files(root, surface):
            reading = normalize_statement(path.read_text(encoding="utf-8"))
            relative = path.relative_to(root / surface).as_posix()
            findings.extend(
                f"{path.relative_to(root).as_posix()}: {reason}"
                for reason in retired_statement_findings(reading, relative)
            )
    return findings


def missing_completeness_statements(root: Path, surface: str) -> list[str]:
    """Every declared canonical source under ``root`` whose completeness statement is absent."""

    missing: list[str] = []
    for relative, statements in CURATION_COMPLETENESS_STATEMENTS.items():
        target = root / relative.removeprefix("skills/")
        if not target.is_file():
            missing.append(f"{surface}: {relative} is missing")
            continue
        reading = normalize_statement(target.read_text(encoding="utf-8"))
        if normalize_statement(COMPLETE_CURATION_RULE) in reading or any(
            normalize_statement(statement) in reading for statement in statements
        ):
            continue
        missing.append(f"{surface}: {relative}")
    return missing


__all__ = [
    "COMPLETE_CURATION_RULE",
    "CURATION_COMPLETENESS_STATEMENTS",
    "CURATION_DOCTRINE_SURFACES",
    "GENERATED_SKILL_COPIES",
    "RETIRED_CURATION_STATEMENTS",
    "RetiredCurationStatement",
    "doctrine_files",
    "missing_completeness_statements",
    "normalize_statement",
    "retired_curation_findings",
    "retired_statement_findings",
]
