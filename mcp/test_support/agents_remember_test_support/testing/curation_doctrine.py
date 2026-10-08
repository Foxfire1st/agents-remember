"""Retired instruction wording and the current native-capsule curation policy.

This module guards exact retired wording and verifies the compact curation surfaces state their
MIK normal-authoring contract: complete memory quality and required coherence. A report-only
admission remains bounded and claims none of that normal authoring pass complete. It is deliberately free of
pytest and repository constants so a case can point it at a staged or synthetic tree; the caller
supplies the repository root.

Matching is on a normalized reading -- markdown emphasis stripped, line wrapping collapsed -- and
against the whole retired sentence, so a statement re-inserted with different emphasis or at a
different column is still the same statement, while doctrine that legitimately survives (an
explicit developer request still governs full code quality and full tests) cannot read as a
regression. A statement is reported only on the files that shipped it.

The retired-wording scan is not a semantic check. A corpus that denied the rule in fresh vocabulary
this registry has never seen would pass; the positive checks cover the exact compact sources whose
scope is part of the native capsule contract.

A second, independent registry covers the loop gate's **field names**, which are facts about the
shipped tool rather than matters of doctrine. The memory-quality result publishes the raw checklist
status as ``qualityChecklistStatus`` and the combined status as ``checklistStatus``, so a sentence
that gates the repair loop on ``checklistStatus=ready-for-closeout`` names a condition the repair
loop never reaches. The combined field is rewritten to ``coherence-required`` only when the raw
status is ready and the coherence record is then missing or stale; while any repair remains it
carries the raw actionable status, and when the record is already current it keeps the incoming
``ready-for-closeout`` with ``closeoutReady`` true. That registry is matched as the exact field
pairing rather than as a sentence, because the carriers phrase the gate differently from each other
-- see :data:`RETIRED_LOOP_GATE_FIELD_PAIRING`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .retired_leaf_handover_wording import RETIRED_LEAF_HANDOVER_WORDING

#: The normal MIK curation pass remains complete on both canonical surfaces.
COMPLETE_CURATION_RULE = (
    "Curation is always complete: a named scoped check never stands in for the full operation"
)

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
    """One shipped sentence an instruction ruling retired, with where it lived.

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
        statement="When a System Specialist is needed, start it with `role_start` on `agents-remember-task`; you may start that role. Give the returned agent ID, report path, handover artifact path and status to the Manager or Orchestrator coordinating the work; neither gains permission to start that role.",
        sources=("skills/l-01-agent-lifecycles/roles/architect.md",),
        probe="When a System Specialist is needed, start it with `role_start` on `agents-remember-task`; you may start that role. Give the returned agent ID, report path, handover artifact path and status to the Manager or Orchestrator coordinating the work; neither gains permission to start that role.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="under developer-chosen direct coordination, assign it to a leaf's Worker or Reviewer, including a job that belongs to no leaf.",
        sources=("skills/l-01-agent-lifecycles/roles/architect.md",),
        probe="under developer-chosen direct coordination, assign it to a leaf's Worker or Reviewer, including a job that belongs to no leaf.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="You may start Manager, Worker, Reviewer, and Curator.",
        sources=("skills/l-01-agent-lifecycles/roles/orchestrator.md",),
        probe="You may start Manager, Worker, Reviewer, and Curator.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="Never start that role yourself. The Architect may start a needed System Specialist and supply its returned agent ID, report path, handover artifact path and status so you can coordinate its work.",
        sources=("skills/l-01-agent-lifecycles/roles/orchestrator.md",),
        probe="Never start that role yourself. The Architect may start a needed System Specialist and supply its returned agent ID, report path, handover artifact path and status so you can coordinate its work.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="You may start Worker, Reviewer and Curator; none of them starts a role.",
        sources=("skills/l-01-agent-lifecycles/roles/manager.md",),
        probe="You may start Worker, Reviewer and Curator; none of them starts a role.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="Assign a check or investigation that belongs to no leaf to the Worker or Reviewer of the nearest leaf.",
        sources=("skills/l-01-agent-lifecycles/roles/manager.md",),
        probe="Assign a check or investigation that belongs to no leaf to the Worker or Reviewer of the nearest leaf.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement=" Never start that role yourself. The Architect may start a needed System Specialist and supply its returned agent ID, report path, handover artifact path and status to you or the Orchestrator above you.",
        sources=("skills/l-01-agent-lifecycles/roles/manager.md",),
        probe=" Never start that role yourself. The Architect may start a needed System Specialist and supply its returned agent ID, report path, handover artifact path and status to you or the Orchestrator above you.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="an Orchestrator Manager, Worker, Reviewer, and Curator under its own sprint; a Manager Worker, Reviewer, and Curator under its own master;",
        sources=("skills/l-01-agent-lifecycles/operations/coordination.md",),
        probe="an Orchestrator Manager, Worker, Reviewer, and Curator under its own sprint; a Manager Worker, Reviewer, and Curator under its own master;",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="A taskless Architect or System Specialist uses the developer's request and asks for a missing outcome/repository/concern; do not invent a task or trust packet.",
        sources=("skills/l-01-agent-lifecycles/operations/orientation.md",),
        probe="A taskless Architect or System Specialist uses the developer's request and asks for a missing outcome/repository/concern; do not invent a task or trust packet.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="System Specialist",
        sources=("skills/l-01-agent-lifecycles/SKILL.md",),
        probe="System Specialist",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="System Specialist",
        sources=("skills/l-01-agent-lifecycles/roles/investigator.md",),
        probe="System Specialist",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="System Specialist",
        sources=("skills/l-01-agent-lifecycles/roles/architect.md",),
        probe="System Specialist",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="System Specialist",
        sources=("skills/l-01-agent-lifecycles/roles/orchestrator.md",),
        probe="System Specialist",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="System Specialist",
        sources=("skills/l-01-agent-lifecycles/roles/manager.md",),
        probe="System Specialist",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="System Specialist",
        sources=("skills/l-01-agent-lifecycles/operations/orientation.md",),
        probe="System Specialist",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="System Specialist",
        sources=("skills/l-01-agent-lifecycles/composition-manifest.json",),
        probe="System Specialist",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="Keep work within the stated provider/system scope.",
        sources=("skills/l-01-agent-lifecycles/roles/investigator.md",),
        probe="Keep work within the stated provider/system scope.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement="No repository-wide redesign, code changes, onboarding writes, task status changes, closeout, or self-approval unless a separate explicit assignment grants that role.",
        sources=("skills/l-01-agent-lifecycles/roles/investigator.md",),
        probe="No repository-wide redesign, code changes, onboarding writes, task status changes, closeout, or self-approval unless a separate explicit assignment grants that role.",
        reason="MIK-R98@v1: Investigator name, scoped investigation and start/report authority replace the earlier role restriction",
    ),
    RetiredCurationStatement(
        statement='An explicitly requested narrow `memory_quality_check` or `curator_coherence` diagnostic, always with `contract_path="<enclosure-contract-path>"`; these are never routine closeout/integration prerequisites.',
        sources=("skills/l-01-agent-lifecycles/templates/curator-brief.md",),
        probe="An explicitly requested narrow",
        reason="fragment 1: the curator's own brief called the complete operation a narrow optional diagnostic that is never a routine prerequisite",
    ),
    RetiredCurationStatement(
        statement="Do not run a full memory suite or create a curator certification for routine curation. Full memory quality is a separate operation only on explicit developer request.",
        sources=("skills/l-01-agent-lifecycles/templates/curator-brief.md",),
        probe="is a separate operation only on",
        reason="fragment 2: it forbade the complete operation for routine curation",
    ),
    RetiredCurationStatement(
        statement="a narrow `memory_quality_check` or `curator_coherence` only on an explicit developer request for a named affected check or curator certification",
        sources=("skills/l-01-agent-lifecycles/roles/curator.md",),
        probe="only on an explicit developer request",
        reason="fragment 3: the curator role's write surface made completeness a developer decision",
    ),
    RetiredCurationStatement(
        statement="A finding count implausible for this change set is a measurement problem to investigate and escalate, not permission to pass incomplete onboarding.",
        sources=("skills/l-01-agent-lifecycles/templates/curator-brief.md",),
        probe="not permission to pass incomplete onboarding",
        reason="fragment 4: the right teeth in a hedge's frame, so it read as permission to hand off onboarding that was never completed",
    ),
    RetiredCurationStatement(
        statement="memory-quality suites, curator certification, or independent review; full code quality, full tests, and full memory quality run only after an explicit developer request",
        sources=(
            "skills/l-01-agent-lifecycles/roles/manager.md",
            "skills/l-01-agent-lifecycles/templates/manager-brief.md",
            "skills/l-01-agent-lifecycles/operations/closeout.md",
            "skills/l-01-agent-lifecycles/core/authority.md",
        ),
        probe="full memory quality run only after",
        reason="the transaction boundary was stated over curation too, so a seat reading it deferred curation",
    ),
    RetiredCurationStatement(
        statement="curator_coherence, full memory quality, and certification are separate explicit operations",
        sources=("skills/l-01-agent-lifecycles/roles/manager.md",),
        probe="separate explicit operations",
        reason="the manager's curator dispatch described the complete operation as separate and explicit",
    ),
    RetiredCurationStatement(
        statement="Do not run or claim a full suite / full quality result unless the developer or the task brief explicitly requests that operation.",
        sources=("skills/l-01-agent-lifecycles/operations/closeout.md",),
        probe="explicitly requests that operation.",
        reason="the targeted-check contract stated the full-suite rule without the curation exception",
    ),
    RetiredCurationStatement(
        statement="Full memory-quality or drift suites are separate developer-requested operations and are not routine closeout or integration gates.",
        sources=("skills/l-01-agent-lifecycles/templates/onboarding-coherency.md",),
        probe="separate developer-requested operations",
        reason="the curator's own report template declared the complete operation separate and not a gate",
    ),
    RetiredCurationStatement(
        statement="Full memory quality is an explicit developer-requested operation through `c-02-memory-quality-control`, not a closeout precondition.",
        sources=("skills/c-12-closeout/SKILL.md",),
        probe="is not a closeout precondition",
        reason="closeout was told curation's completed result is not its precondition",
    ),
    RetiredCurationStatement(
        statement="curator_coherence runs only when the developer explicitly requests that separate diagnostic.",
        sources=("skills/l-01-agent-lifecycles/operations/curation.md",),
        probe="runs only when the developer explicitly requests",
        reason="the curation procedure made the coherence authority an explicit-request diagnostic",
    ),
    RetiredCurationStatement(
        statement="Full branch quality evidence is a separate explicit developer request.",
        sources=("skills/l-01-agent-lifecycles/roles/reviewer.md",),
        probe="Full branch quality evidence is a separate explicit developer request",
        reason="the reviewer's onboarding lens read the complete operation as a separate request rather than the pass it verifies",
    ),
    RetiredCurationStatement(
        statement="Run the scoped checks required by the task. Run the full `memory_quality_check` operation only when the task or owner explicitly requests it; when requested, do not substitute a narrower check.",
        sources=(
            "skills/l-01-agent-lifecycles/operations/curation.md",
            "skills/l-01-agent-lifecycles/roles/curator.md",
        ),
        probe="operation only when the task or owner explicitly requests it",
        reason="the prototype made the normal MIK authoring pass's complete operation optional",
    ),
    RetiredCurationStatement(
        statement="If the work is small, directly coordinate distinct Worker, Reviewer, and Curator agents.",
        sources=("skills/l-01-agent-lifecycles/roles/architect.md",),
        probe="If the work is small",
        reason="MIK-R72@v2: If the work is small — small scope no longer selects direct Architect coordination",
    ),
    RetiredCurationStatement(
        statement="Do not insert an Orchestrator or Manager by default.",
        sources=("skills/l-01-agent-lifecycles/roles/architect.md",),
        probe="Do not insert an Orchestrator",
        reason="MIK-R72@v2: Do not insert an Orchestrator — one Manager is required for the single-master default",
    ),
    RetiredCurationStatement(
        statement="For larger work, assign an Orchestrator or Manager only when it improves coordination.",
        sources=("skills/l-01-agent-lifecycles/roles/architect.md",),
        probe="only when it improves coordination",
        reason="MIK-R72@v2: only when it improves coordination — the number of concurrent masters, not a benefit judgment, selects the coordinator",
    ),
    RetiredCurationStatement(
        statement="Flat work is first-class: the Architect may directly coordinate distinct Workers, Reviewers, and Curators.",
        sources=("skills/l-01-agent-lifecycles/operations/planning.md",),
        probe="Flat work is first-class",
        reason="MIK-R72@v2: Flat work is first-class — direct Architect coordination requires the developer to choose it",
    ),
    RetiredCurationStatement(
        statement="Add an Orchestrator or Manager for genuinely larger coordination, not as a required rung.",
        sources=("skills/l-01-agent-lifecycles/operations/planning.md",),
        probe="not as a required rung",
        reason="MIK-R72@v2: not as a required rung — one Manager or one sprint Orchestrator is the required first delegation",
    ),
    RetiredCurationStatement(
        statement="Choose a flat graph for small work: Architect directly assigns distinct Workers, Reviewers, and Curators.",
        sources=("skills/l-01-agent-lifecycles/operations/coordination.md",),
        probe="Choose a flat graph for small work",
        reason="MIK-R72@v2: Choose a flat graph for small work — small work no longer selects a flat graph",
    ),
    RetiredCurationStatement(
        statement="For a larger sprint, an Orchestrator may coordinate; a Manager may own one selected master.",
        sources=("skills/l-01-agent-lifecycles/operations/coordination.md",),
        probe="For a larger sprint",
        reason="MIK-R72@v2: For a larger sprint — each concurrent master has a Manager under the sprint Orchestrator",
    ),
    RetiredCurationStatement(
        statement="These roles are optional according to scale.",
        sources=("skills/l-01-agent-lifecycles/operations/coordination.md",),
        probe="optional according to scale",
        reason="MIK-R72@v2: optional according to scale — coordination roles are not optional by scale",
    ),
    RetiredCurationStatement(
        statement="You coordinate one selected sprint when the work benefits from a portfolio owner.",
        sources=("skills/l-01-agent-lifecycles/roles/orchestrator.md",),
        probe="when the work benefits from a portfolio owner",
        reason="MIK-R72@v2: when the work benefits from a portfolio owner — two simultaneous masters or a developer override select the Orchestrator",
    ),
    RetiredCurationStatement(
        statement="A Manager is optional for a sufficiently large master; simple work can stay flat under the Architect.",
        sources=("skills/l-01-agent-lifecycles/roles/orchestrator.md",),
        probe="simple work can stay flat under the Architect",
        reason="MIK-R72@v2: simple work can stay flat under the Architect — one Manager owns each master rather than being optional for large work",
    ),
    RetiredCurationStatement(
        statement="A simple project may be run directly by the Architect without a Manager; do not require extra hierarchy for its own sake.",
        sources=("skills/l-01-agent-lifecycles/roles/manager.md",),
        probe="A simple project may be run directly",
        reason="MIK-R72@v2: A simple project may be run directly — the developer must choose direct Architect coordination",
    ),
    RetiredCurationStatement(
        statement="Choose the smallest owner graph that can deliver independent evidence.",
        sources=("skills/l-01-agent-lifecycles/operations/planning.md",),
        probe="Choose the smallest owner graph",
        reason="MIK-R72@v2: Choose the smallest owner graph — the coordinator default precedes optional graph minimization",
    ),
    RetiredCurationStatement(
        statement="Return peer questions to the agent that started you with `role_message` to the agent ID in your handover; an Orchestrator started from the dashboard has no parent, needs none, and must not invent one.",
        sources=("skills/l-01-agent-lifecycles/roles/orchestrator.md",),
        probe="Return peer questions to the agent that started you",
        reason="MIK-R72@v2: Return peer questions to the agent that started you — ordinary peer questions are decided by the Orchestrator, not relayed",
    ),
    RetiredCurationStatement(
        statement="Flat ownership is valid.",
        sources=("skills/l-01-agent-lifecycles/SKILL.md",),
        probe="Flat ownership is valid",
        reason="MIK-R72@v2: Flat ownership is valid — flat ownership requires an explicit developer choice",
    ),
    RetiredCurationStatement(
        statement="An Architect may coordinate distinct Worker, Reviewer, and Curator roles directly; add an Orchestrator or Manager only when the scope benefits from that coordination.",
        sources=("skills/l-01-agent-lifecycles/SKILL.md",),
        probe="add an Orchestrator or Manager only when the scope benefits",
        reason="MIK-R72@v2: add an Orchestrator or Manager only when the scope benefits — the concurrent-master count selects the default, not scope benefit",
    ),
    RetiredCurationStatement(
        statement="Do not require every hierarchy rung.",
        sources=("skills/l-01-agent-lifecycles/SKILL.md",),
        probe="Do not require every hierarchy rung",
        reason="MIK-R72@v2: Do not require every hierarchy rung — one coordinating agent is required unless the developer chooses otherwise",
    ),
    RetiredCurationStatement(
        statement="A plan delta or missing authority returns to the agent that started you, with `role_message` on `agents-remember-task` to the agent ID in your handover",
        sources=("skills/l-01-agent-lifecycles/roles/manager.md",),
        probe="A plan delta or missing authority returns",
        reason="MIK-R72@v2: A plan delta or missing authority returns — only developer-needed plan or authority decisions go upward",
    ),
    RetiredCurationStatement(
        statement="For the accepted objective, assign distinct tasks to distinct Workers, Reviewers, and Curators: start each with `role_start` on the `agents-remember-task` tool server, on a selection under your own sprint.",
        sources=("skills/l-01-agent-lifecycles/roles/orchestrator.md",),
        probe="For the accepted objective, assign distinct tasks",
        reason="MIK-R72@v2: For the accepted objective, assign distinct tasks — Managers own the inner Worker, Reviewer and Curator loops",
    ),
    RetiredCurationStatement(
        statement="Follow your assignments until the work is complete, blocked, or a true developer decision is needed; do not start a role and stop.",
        sources=("skills/l-01-agent-lifecycles/operations/coordination.md",),
        probe="Follow your assignments until",
        reason="MIK-R72@v2: Follow your assignments until — the follow-through rule now distinguishes the delegated and developer-direct Architect duties",
    ),
    RetiredCurationStatement(
        statement="Shape a bounded plan and requirement set; preserve flat ownership when it fits.",
        sources=("skills/l-01-agent-lifecycles/composition-manifest.json",),
        probe="Shape a bounded plan and requirement set; preserve flat ownership when it fits.",
        reason="MIK-R72@v2: operations.planning.purpose — the description must state one Manager for one master, the concurrent-master Orchestrator and the developer exception",
    ),
    RetiredCurationStatement(
        statement="Projects-level semantic owner; may be launched taskless and may coordinate flat work.",
        sources=("skills/l-01-agent-lifecycles/composition-manifest.json",),
        probe="Projects-level semantic owner; may be launched taskless and may coordinate flat work.",
        reason="MIK-R72@v2: roles.architect.seat — the description must state one Manager for one master, the concurrent-master Orchestrator and the developer exception",
    ),
    RetiredCurationStatement(
        statement="Optional sprint portfolio coordinator of role agents.",
        sources=("skills/l-01-agent-lifecycles/composition-manifest.json",),
        probe="Optional sprint portfolio coordinator of role agents.",
        reason="MIK-R72@v2: roles.orchestrator.seat — the description must state one Manager for one master, the concurrent-master Orchestrator and the developer exception",
    ),
    RetiredCurationStatement(
        statement="Optional selected-master coordinator; coordinates leaf owners and evidence.",
        sources=("skills/l-01-agent-lifecycles/composition-manifest.json",),
        probe="Optional selected-master coordinator; coordinates leaf owners and evidence.",
        reason="MIK-R72@v2: roles.manager.seat — the description must state one Manager for one master, the concurrent-master Orchestrator and the developer exception",
    ),
    *(
        RetiredCurationStatement(
            statement=statement,
            sources=(path,),
            probe=normalize_statement(statement)[:80],
            reason="leaf handover, review bookkeeping or harness autonomy instruction was replaced",
        )
        for path, statement in RETIRED_LEAF_HANDOVER_WORDING
    ),
    RetiredCurationStatement(
        statement="Put questions for the developer in your own chat.",
        sources=("skills/l-01-agent-lifecycles/roles/worker.md",),
        probe="Put questions for the developer in your own chat.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="Put questions requiring the developer's decision in your own chat.",
        sources=("skills/l-01-agent-lifecycles/roles/worker.md",),
        probe="Put questions requiring the developer's decision in your own chat.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="Questions requiring the developer's decision stay in your own chat.",
        sources=("skills/l-01-agent-lifecycles/roles/reviewer.md",),
        probe="Questions requiring the developer's decision stay in your own chat.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="At that limit ask the developer directly, wait for explicit authorization,",
        sources=("skills/l-01-agent-lifecycles/roles/reviewer.md",),
        probe="At that limit ask the developer directly, wait for explicit authorization,",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="Developer questions stay in your own chat.",
        sources=("skills/l-01-agent-lifecycles/roles/curator.md",),
        probe="Developer questions stay in your own chat.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="Return the report and ask questions in your own chat; when a parent agent started you, also tell it",
        sources=("skills/l-01-agent-lifecycles/roles/investigator.md",),
        probe="Return the report and ask questions in your own chat; when a parent agent started you, also tell it",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="Put questions requiring the developer's decision in your own chat",
        sources=("skills/l-01-agent-lifecycles/operations/review.md",),
        probe="Put questions requiring the developer's decision in your own chat",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="Developer decisions stay in your own chat.",
        sources=("skills/l-01-agent-lifecycles/operations/curation.md",),
        probe="Developer decisions stay in your own chat.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="Put questions for the developer in your own chat; peer questions and results go through `role_message` on `agents-remember-task`.",
        sources=("skills/l-01-agent-lifecycles/operations/orientation.md",),
        probe="Put questions for the developer in your own chat; peer questions and results go through `role_message` on `agents-remember-task`.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="ask the developer directly and wait for explicit authorization; record that instruction before any authorized extra work.",
        sources=("skills/l-01-agent-lifecycles/core/loop.md",),
        probe="ask the developer directly and wait for explicit authorization; record that instruction before any authorized extra work.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="ask the developer directly.",
        sources=("skills/l-01-agent-lifecycles/core/authority.md",),
        probe="ask the developer directly.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="At three rounds, ask the developer directly, wait for explicit authorization, and record the instruction before any extra review.",
        sources=("skills/l-01-agent-lifecycles/templates/verdict.md",),
        probe="At three rounds, ask the developer directly, wait for explicit authorization, and record the instruction before any extra review.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="after round 3 ask the developer directly.",
        sources=("skills/l-01-agent-lifecycles/templates/manager-brief.md",),
        probe="after round 3 ask the developer directly.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="Put every question for the developer in your own chat, as your reply in this session; the developer reads it there and answers there.",
        sources=("skills/l-01-agent-lifecycles/SKILL.md",),
        probe="Put every question for the developer in your own chat, as your reply in this session; the developer reads it there and answers there.",
        reason="MIK-R93@v1: developer questions follow the parent chain, and only a parentless or unreachable-parent case uses the own chat",
    ),
    RetiredCurationStatement(
        statement="As the exception, an Orchestrator or Manager started by another agent sends what needs the developer's decision to that parent with `role_message` on `agents-remember-task`, following its handover's developerQuestions rule, keeps working and does not end its turn on the question; this exception takes precedence over the own-chat sentence for those two roles with a parent.",
        sources=("skills/l-01-agent-lifecycles/SKILL.md",),
        probe="As the exception, an Orchestrator or Manager started by another agent sends what needs the developer's decision to that parent with `role_message` on `agents-remember-task`, following its handover's developerQuestions rule, keeps working and does not end its turn on the question; this exception takes precedence over the own-chat sentence for those two roles with a parent.",
        reason="MIK-R93@v1: parent routing applies to every parented role, not only coordinators",
    ),
    RetiredCurationStatement(
        statement="Do not forward role agents' messages or send a notice of a landing.",
        sources=(
            "skills/l-01-agent-lifecycles/roles/manager.md",
            "skills/l-01-agent-lifecycles/roles/orchestrator.md",
        ),
        probe="Do not forward role agents' messages",
        reason="MIK-R93@v1: developer questions and permission notices must be forwarded through the parent chain",
    ),
)

#: The loop-gate field pairing the shipped tool retired from its own instruction prose. The
#: memory-quality result publishes the RAW checklist status as ``qualityChecklistStatus`` and the
#: COMBINED status as ``checklistStatus``, which is rewritten to ``coherence-required`` only on the
#: path where the raw status is ready and the coherence record is missing or stale. A sentence gating
#: the repair loop on the combined field with the raw value therefore names a condition the repair
#: loop does not reach, and sends a curator seat back to repair work that is already finished.
RETIRED_LOOP_GATE_FIELD_PAIRING = "checklistStatus=ready-for-closeout"

#: The field names the corrected gate states, and the reason a carrier must carry both: the
#: measured precision gap this registry closes was that ``qualityChecklistStatus`` and
#: ``closeoutReady`` appeared in zero shipped files while the retired pairing appeared in
#: thirty-one across the canonical tree, all nine copies, and the reference documents -- plus one
#: more in this module, which is the base fragment the registry itself has to name.
LOOP_GATE_CORRECTED_FIELDS = ("qualityChecklistStatus", "closeoutReady")

#: Canonical documents outside the skill surfaces that state the same loop, mapped to the corrected
#: field names each must carry. Membership is the census -- the retired pairing must never return to
#: one of these -- and the positive half is asserted with the same reader the skill carriers use, so
#: a document that drops a field name is caught rather than silently leaving the census.
#: ``drift-c02.md`` documents the ``c-02-memory-quality-control`` operation, and ``mcp-tools.md``
#: documents the tool surface itself; both are read by seats that may never open a skill file.
LOOP_GATE_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "docs/reference/drift-c02.md": LOOP_GATE_CORRECTED_FIELDS,
    "docs/reference/mcp-tools.md": LOOP_GATE_CORRECTED_FIELDS,
}

#: The finding a surface carries while it still states the retired pairing.
LOOP_GATE_REASON = (
    f"gates the repair loop on the combined status field with the raw status value "
    f"({RETIRED_LOOP_GATE_FIELD_PAIRING}); the repair gate is the raw qualityChecklistStatus "
    f"with curatorActionableCount"
)

#: The compact native curation surfaces and their exact scoped-check contract.
CURATION_POLICY_STATEMENTS: dict[str, tuple[str, ...]] = {
    "skills/l-01-agent-lifecycles/operations/curation.md": (
        COMPLETE_CURATION_RULE,
        "prepare → publish → validate",
    ),
    "skills/l-01-agent-lifecycles/roles/curator.md": (
        "Curation is always complete: a named scoped check never stands in for it",
        "prepare → publish → validate",
    ),
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
    """Every instruction document and lifecycle manifest on one canonical or generated surface."""

    return sorted(
        (
            *((root / surface).rglob("*.md")),
            *((root / surface).glob("l-01-agent-lifecycles/composition-manifest.json")),
        )
    )


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


def gates_the_retired_loop_gate_pairing(text: str) -> bool:
    """Whether one reading still names the combined status field with the raw status value."""

    return normalize_statement(RETIRED_LOOP_GATE_FIELD_PAIRING) in normalize_statement(text)


def retired_loop_gate_findings(root: Path) -> list[str]:
    """Every shipped surface still gating the repair loop on the retired field pairing.

    The match is the exact field pairing rather than a whole sentence, and that is deliberate where
    :func:`retired_statement_findings` is not: the carriers phrase the gate differently from each
    other, so no single sentence is the defect while the pairing is. No surviving doctrine quotes
    the pairing either -- the raw value it names belongs to a different published field, so a
    carrier cannot state it correctly by accident, which is what makes a fragment match safe here.

    Scope is the canonical ``skills/**`` tree, the nine generated copies, and the reference
    documents in :data:`LOOP_GATE_DOCUMENTS`. What it does NOT cover, stated rather than implied: a
    restatement of the wrong gate in fresh vocabulary that never writes the pairing (for example
    "repair until the combined status reads ready") passes, and it does not read the tool.
    """

    findings: list[str] = []
    for surface in CURATION_DOCTRINE_SURFACES:
        for path in doctrine_files(root, surface):
            if gates_the_retired_loop_gate_pairing(path.read_text(encoding="utf-8")):
                findings.append(f"{path.relative_to(root).as_posix()}: {LOOP_GATE_REASON}")
    for relative in LOOP_GATE_DOCUMENTS:
        path = root / relative
        if path.is_file() and gates_the_retired_loop_gate_pairing(path.read_text(encoding="utf-8")):
            findings.append(f"{relative}: {LOOP_GATE_REASON}")
    return findings


def missing_loop_gate_statements(root: Path) -> list[str]:
    """Every declared loop-gate document under ``root`` whose corrected field names are absent.

    The positive half of the document census, mirroring :func:`missing_curation_policy_statements`:
    membership in :data:`LOOP_GATE_DOCUMENTS` is a promise that the document states the corrected
    gate, so a document that loses a field name is reported here instead of quietly leaving the set.
    """

    missing: list[str] = []
    for relative, statements in LOOP_GATE_DOCUMENTS.items():
        target = root / relative
        if not target.is_file():
            missing.append(f"{relative} is missing")
            continue
        reading = normalize_statement(target.read_text(encoding="utf-8"))
        missing.extend(
            f"{relative} -> {statement}"
            for statement in statements
            if normalize_statement(statement) not in reading
        )
    return missing


def missing_curation_policy_statements(root: Path, surface: str) -> list[str]:
    """Every declared compact curation source under ``root`` missing its scoped-check rule."""

    missing: list[str] = []
    for relative, statements in CURATION_POLICY_STATEMENTS.items():
        target = root / relative.removeprefix("skills/")
        if not target.is_file():
            missing.append(f"{surface}: {relative} is missing")
            continue
        reading = normalize_statement(target.read_text(encoding="utf-8"))
        if any(normalize_statement(statement) in reading for statement in statements):
            continue
        missing.append(f"{surface}: {relative}")
    return missing


__all__ = [
    "CURATION_DOCTRINE_SURFACES",
    "CURATION_POLICY_STATEMENTS",
    "GENERATED_SKILL_COPIES",
    "LOOP_GATE_CORRECTED_FIELDS",
    "LOOP_GATE_DOCUMENTS",
    "RETIRED_CURATION_STATEMENTS",
    "RETIRED_LOOP_GATE_FIELD_PAIRING",
    "RetiredCurationStatement",
    "doctrine_files",
    "gates_the_retired_loop_gate_pairing",
    "missing_curation_policy_statements",
    "missing_loop_gate_statements",
    "normalize_statement",
    "retired_curation_findings",
    "retired_loop_gate_findings",
    "retired_statement_findings",
]
