"""One pointer per action per native first message (L84 P5-18).

A pointer is any Markdown link outside a fence whose target resolves to a skill file:
``{{detail:F}}#a``, the served ``skill://…/l-01-agent-lifecycles/F#a`` form, a relative path,
or a same-file ``#a``. Its key is the target *file*: a whole-file link and a section link into
one file, or links to two of its sections, reach the same file. Links into one file from one
sentence (or one table row) count once. A pointer's action is its sentence's opening
Before/When/For clause (``manifest_step_derivation.action_of``); otherwise the sentence text
before the link, a table row's first cell, or, for a sentence that opens with the link, the
text after it. Every link counts, whatever its sentence's opener.

In one first message (every instruction unit of the compiled capsule plus the shared
``core/every-role.md`` block), a file reached from two or more sentences is a duplicate
unless every action is different and each is listed below for that file with the reason it
is a different action, from the source file it is listed for. The same action twice is never
admitted, nor one action under two wordings, nor a listed action re-added in another source.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict

from agents_remember_test_support.testing.manifest_step_derivation import (
    LINK,
    action_of,
    sentence_start,
)

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_SERVED = "l-01-agent-lifecycles/"
_SENTENCE_END = re.compile(r"[.!?](?:\*\*)?(?:\s|$)")

#: target file -> (actions that reach it for different purposes, why they differ).
DISTINCT_ACTIONS: dict[str, tuple[frozenset[str], str]] = {
    "core/every-role.md": (
        frozenset(
            {
                "before landing leaves whose reviews passed",
                "before proposing a requirement change to your parent",
                "before proposing an input limit or a new guard",
                "before sending a requirement to a Worker",
                "for a check that blocks a legal operation",
                "for an uncertain start",
                "for helper collection and continuation",
                "include the review mode, canonical task and review purpose, seam, exact requirement set with identifiers, versions, packets and the durable",
                "the leaf task document, requirement",
                "the requirement set dispatched to the builder: exact stable identifiers and versions, canonical packets and the durable",
                "the requirement set includes identifiers, versions, packets and the durable",
                "when the send answers held",
            }
        ),
        "the shared block's sections serve separate acts: requirement limits, helper collection, landing order, held sends, uncertain starts, and the approval record named inside an intake or input list",
    ),
    "criteria/code-seam.md": (
        frozenset(
            {
                "row: atomic-master integration review",
                "row: leaf code-change review",
                "row: leaf full-loop review",
                "row: master-exit",
                "row: super-exit",
            }
        ),
        "the review-mode table: each row names the criteria of a different review mode",
    ),
    "criteria/doctrine.md": (
        frozenset(
            {
                "row: atomic-master integration review",
                "row: leaf code-change review",
                "row: leaf full-loop review",
                "row: master-exit",
                "row: super-exit",
            }
        ),
        "the review-mode table: each row names the criteria of a different review mode",
    ),
    "criteria/onboarding-memory.md": (
        frozenset(
            {
                "row: atomic-master integration review",
                "row: leaf code-change review",
                "row: leaf full-loop review",
                "row: master-exit",
                "row: super-exit",
            }
        ),
        "the review-mode table: each row names the criteria of a different review mode",
    ),
    "criteria/report-verification.md": (
        frozenset(
            {
                "row: atomic-master integration review",
                "row: leaf code-change review",
                "row: leaf full-loop review",
                "row: master-exit",
                "row: plan review",
                "row: super-exit",
            }
        ),
        "the review-mode table: each row names the criteria of a different review mode",
    ),
    "operations/bootstrap.md": (
        frozenset(
            {
                "before an effectful setup step",
                "before continuing setup with a missing branch choice, memory-repository decision or intended repository",
                "before initializing user settings or handling an existing settings file",
                "before resuming a failed step",
                "opens: owns the ordered steps, their surfaces, failure states and authority gates",
                "state in the report how its claims meet",
                "state the current fact, and report a surface that still states a superseded fact.",
                "use the required workspace facts in",
            }
        ),
        "Bootstrap's role file points to separate steps of its operation: inputs, workflow, effectful steps, settings, missing decisions, failed steps, failure cases and report evidence",
    ),
    "operations/closeout.md": (
        frozenset(
            {
                "before coordinating paired closeout",
                "before responding to gate findings from closeout preview or any landing route",
            }
        ),
        "gate findings during closeout versus coordinating paired closeout",
    ),
    "operations/coordination.md": (
        frozenset(
            {
                "before delegating coordination",
                "before directing handovers, repair loops, review rounds, memory passes or relays",
                "before filling or reading message fields",
                "before messaging a role agent through `role_message` on `agents-remember-task`",
                "before reconciling that start",
                "before restoring an existing AR leaf",
                "before starting a role and delivering its first intake",
                "before starting and coordinating the leaf",
                "before starting or messaging a role",
                "before the Reviewer's first inspection",
                "for a closeout-result notice",
                "when `role_message` on `agents-remember-task` answers `held` because a child waits at a permission prompt, or its wait returns `permission-pending`",
                "when `role_message` on `agents-remember-task` returns `held` because a child waits at a permission prompt, or a wait returns `permission-pending`",
            }
        ),
        "separate coordination acts: delegating, starting, messaging, the Reviewer's first intake, directing running work, notices, held sends, reconciliation and review records",
    ),
    "operations/curation.md": (
        frozenset(
            {
                "before closeout",
                "before contacting another leaf",
                "before contacting the Manager about curation",
                "before continuing after a memory-tool failure",
                "before handover",
                "before intake and writer admission",
                "before reporting a completed curation result",
                "before running curation checks",
                "before sending an evidenced code concern",
                "before validating coherence",
            }
        ),
        "the Curator's separate curation steps: intake, checks, coherence, fact-check, results, concerns, contacts and tool failures",
    ),
    "operations/implementation.md": (
        frozenset(
            {
                "before deciding closeout after a leaf sync",
                "before deciding whether a handover was taken or responding to a provider, seat or busy refusal",
                "before handing over a frozen candidate",
                "before making a measurement-tooling handover",
                "before recovering after that sync",
                "before repairing a native memory-file sync conflict",
                "before repairing a sealed finding in this task's own files",
                "before responding to a non-work reply, an empty or doubled-seat refusal, an unreachable-Manager notice, or failed delivery of a required Manager notice",
                "before responding to a non-work reply, empty or doubled-seat refusal, or an unreachable-Manager notice",
                "before writing the report",
                "when one small item costs about an hour, a second repair round or a second freeze",
                "when ruling on a measured defect or costly small work",
                "when the master line moves, run the supported sync of your own leaf yourself",
            }
        ),
        "separate Worker and sync acts: reports, freeze and measurement handovers, receipts, sealed repairs, sync and its conflicts, cost rulings and seat refusals",
    ),
    "operations/orientation.md": (
        frozenset(
            {
                "before acting on a denied or unanswered harness request",
                "before an admitted canonical task-document edit",
                "before asking a question",
                "before changing a canonical task document",
                "before deciding a formal permission",
                "before deciding or writing memory",
                "before editing a canonical task document or marking your own completed step",
                "before making a formal permission decision",
                "before responding to an advisory reviewer's permission prompt",
                "before resuming a memory decision or write",
                "before sending a developer question or handling a relayed answer",
                "before sending a developer question or recognizing a relayed answer",
                "before sending developer questions or handling relayed answers",
                "preserve the read rules under",
            }
        ),
        "separate orientation acts: developer questions, task-document edits, formal permissions, harness denials, memory decisions and read rules",
    ),
    "operations/planning.md": (
        frozenset(
            {
                "before assigning an issue to another open leaf",
                "before releasing a packet",
            }
        ),
        "releasing a packet versus assigning an issue to another leaf",
    ),
    "operations/review.md": (
        frozenset(
            {
                "before checking repaired findings at the round limit",
                "before commissioning advisory independent reviews",
                "before coordinating a memory review",
                "before handling findings received after sealing, including those arriving after code PASS, or sending the ready-for-closeout notice",
                "before inspecting the assigned packet or candidate",
                "before opening or recording an ordinary code review round",
                "before rechecking a repaired Worker record",
                "before recording a memory round, requesting an extra round or handling provider, seat or busy refusals",
                "before recording an ordinary leaf code round",
                "write and record the verdict, then follow",
            }
        ),
        "separate review acts: inspection, verdict handover, round records, late findings, repaired-finding checks, advisory reviews and memory-review coordination",
    ),
    "reference/bootstrap/foundation-state.md": (
        frozenset(
            {
                "before naming the next step in the conversation and report",
                "before reading and reporting the knowledge foundation's state",
            }
        ),
        "reading and reporting the state versus naming the next step",
    ),
    "reference/coordination/shared/requirement-design.md": (
        frozenset(
            {
                "before authoring or amending a requirement and its task structure",
                "before grouping primary and additional same-topic packets",
            }
        ),
        "authoring a requirement versus grouping same-topic packets",
    ),
    "reference/curator/card-formats-and-citations.md": (
        frozenset(
            {
                "before checking or repairing citations",
                "before creating or maintaining cards or overviews",
                "before maintaining the entity catalog",
                "before writing card history",
            }
        ),
        "cards, the entity catalog, card history and citations are separate sections and acts",
    ),
    "reference/curator/governing-sources.md": (
        frozenset(
            {
                "before authoring a governing decision record",
                "before explaining an assigned contract",
            }
        ),
        "explaining a contract versus authoring a governing decision record",
    ),
    "reference/curator/intent-reconciliation.md": (
        frozenset(
            {
                "before promoting a historical oddity",
                "before reconciling current intent, ruled intent and implementation",
            }
        ),
        "reconciliation itself versus the failure case of promoting an oddity",
    ),
    "reference/knowledge/commands.md": (
        frozenset(
            {
                "before a knowledge-system command or check",
                "before planning repository work",
                "before retrieving its context again",
                "before using a knowledge command or acting on its result",
                "before using a knowledge command or interpreting its result",
                "before using a knowledge command or its result",
                "before using a knowledge-system command or interpreting its result",
            }
        ),
        "a role's own command use versus the shared rule for retrieving lost context without Bootstrap",
    ),
    "reference/knowledge/use.md": (
        frozenset(
            {
                "before checking code and memory",
                "before diagnosing",
                "before diagnosing a failing test",
                "before editing source",
                "before interpreting a knowledge read",
                "before interpreting their resolution",
                "before investigating repository code",
                "before relying on storage or path eligibility, cross-repository allowances, or serving-build metadata",
                "when inspecting changes",
            }
        ),
        "separate read moments: editing, diagnosing, checking memory, interpreting a read or a resolution, investigating, inspecting changes, and the shared settings-source rule",
    ),
    "reference/message-handover.md": (
        frozenset(
            {
                "before delivering a developer question to a recipient or parent",
                "before handing over work",
                "before handing work to a waiting seat",
                "before locating a filed message or receipt",
                "before responding to a full-store refusal",
                "before sending a handover",
                "before sending a message",
            }
        ),
        "one handover pointer per role, plus separate acts: a developer question's delivery, locating a filed message, and a full-store refusal",
    ),
    "reference/refused-write.md": (
        frozenset(
            {
                "before retiring or moving a task",
                "for a correct-input refusal of a covered write (`task_doc` edits (including an admitted task retirement or folder movement), `knowledge_write`, `citation_fix`, `citation_note_revision` and `curator_coherence` publication)",
            }
        ),
        "the Manager's task retirement or movement versus recovering any refused covered write",
    ),
    "reference/tasks/document-authoring.md": (
        frozenset(
            {
                "before authoring a task document or requirement packet",
                "before editing, skipping or previewing task-document fields",
                "before editing, skipping or previewing task-document fields, or using `task_doc append_decision`",
                "before making the change",
                "before using `task_doc append_decision`",
            }
        ),
        "the shared code-change rule (F1 ruling) versus the role's own task-document edits",
    ),
    "roles/bootstrap.md": (
        frozenset(
            {
                "before asking a setup question",
                "before taking a setup action",
                "with an attributed baseline and resolving context, apply",
            }
        ),
        "choosing who continues, asking a setup question and taking a setup action are separate moments",
    ),
    "roles/curator.md": (
        frozenset(
            {
                "before capturing a discovery",
                "before repairing a failed curation step",
                "before writing the handover output",
                "when repairing memory encountered during curation",
            }
        ),
        "the operation's separate returns to the role file: discoveries, failed steps, handover output, and encountered memory",
    ),
    "roles/manager.md": (
        frozenset(
            {
                "for a round beyond the settings limit",
                "the manager receives the worker's freeze notice to start the reviewer, the reviewer's code-`pass` notice to start the curator, and only the other occasions listed in",
            }
        ),
        "a round beyond the limit versus the Manager's leaf-notice occasions",
    ),
    "roles/orchestrator.md": (
        frozenset(
            {
                "forward only questions requiring the developer and the permission notices described under",
                "pending developer questions and held-report state from",
                "the rulings record, requirement-change list and status file paths from",
                "verified manager delivery, with reports, findings, review, curation and git publication separately addressed as",
            }
        ),
        "the report list and the permission-notice forwarding name different sections",
    ),
    "roles/reviewer.md": (
        frozenset(
            {
                "before a code edit, task-acceptance write, role start or Git publication",
                "in `fix-verification`, reuse the sealed evidence and verify only listed identifiers, subject to the sanctioned routes under",
            }
        ),
        "the standing restrictions versus the sanctioned routes in fix-verification",
    ),
    "roles/worker.md": (
        frozenset(
            {
                "before handing over or attempting a role-start, acceptance or publication action",
                "before reading the task inputs",
            }
        ),
        "reading the task inputs versus handover, start, acceptance and publication restrictions",
    ),
    "templates/curator-handoff-list.md": (
        frozenset(
            {
                "before producing the Curator hand-off list",
                "when naming them",
            }
        ),
        "the list's shape versus the record kinds for outside-scope items",
    ),
}
#: target file -> listed action -> the source files it is admitted from. A listed action
#: repeated from another source (another role's wording re-added) is not admitted.
ACTION_SOURCES: dict[str, dict[str, frozenset[str]]] = {
    "core/every-role.md": {
        "before landing leaves whose reviews passed": frozenset(["operations/coordination.md"]),
        "before proposing a requirement change to your parent": frozenset(
            ["roles/investigator.md"]
        ),
        "before proposing an input limit or a new guard": frozenset(["roles/architect.md"]),
        "before sending a requirement to a Worker": frozenset(["roles/architect.md"]),
        "for a check that blocks a legal operation": frozenset(["roles/architect.md"]),
        "for an uncertain start": frozenset(["operations/coordination.md"]),
        "for helper collection and continuation": frozenset(
            [
                "roles/architect.md",
                "roles/curator.md",
                "roles/investigator.md",
                "roles/manager.md",
                "roles/orchestrator.md",
                "roles/reviewer.md",
            ]
        ),
        "include the review mode, canonical task and review purpose, seam, exact requirement set with identifiers, versions, packets and the durable": frozenset(
            ["operations/planning.md"]
        ),
        "the leaf task document, requirement": frozenset(["roles/curator.md"]),
        "the requirement set dispatched to the builder: exact stable identifiers and versions, canonical packets and the durable": frozenset(
            ["roles/reviewer.md"]
        ),
        "the requirement set includes identifiers, versions, packets and the durable": frozenset(
            ["operations/coordination.md"]
        ),
        "when the send answers held": frozenset(["roles/orchestrator.md"]),
    },
    "criteria/code-seam.md": {
        "row: atomic-master integration review": frozenset(["roles/reviewer.md"]),
        "row: leaf code-change review": frozenset(["roles/reviewer.md"]),
        "row: leaf full-loop review": frozenset(["roles/reviewer.md"]),
        "row: master-exit": frozenset(["roles/reviewer.md"]),
        "row: super-exit": frozenset(["roles/reviewer.md"]),
    },
    "criteria/doctrine.md": {
        "row: atomic-master integration review": frozenset(["roles/reviewer.md"]),
        "row: leaf code-change review": frozenset(["roles/reviewer.md"]),
        "row: leaf full-loop review": frozenset(["roles/reviewer.md"]),
        "row: master-exit": frozenset(["roles/reviewer.md"]),
        "row: super-exit": frozenset(["roles/reviewer.md"]),
    },
    "criteria/onboarding-memory.md": {
        "row: atomic-master integration review": frozenset(["roles/reviewer.md"]),
        "row: leaf code-change review": frozenset(["roles/reviewer.md"]),
        "row: leaf full-loop review": frozenset(["roles/reviewer.md"]),
        "row: master-exit": frozenset(["roles/reviewer.md"]),
        "row: super-exit": frozenset(["roles/reviewer.md"]),
    },
    "criteria/report-verification.md": {
        "row: atomic-master integration review": frozenset(["roles/reviewer.md"]),
        "row: leaf code-change review": frozenset(["roles/reviewer.md"]),
        "row: leaf full-loop review": frozenset(["roles/reviewer.md"]),
        "row: master-exit": frozenset(["roles/reviewer.md"]),
        "row: plan review": frozenset(["roles/reviewer.md"]),
        "row: super-exit": frozenset(["roles/reviewer.md"]),
    },
    "operations/bootstrap.md": {
        "before an effectful setup step": frozenset(["roles/bootstrap.md"]),
        "before continuing setup with a missing branch choice, memory-repository decision or intended repository": frozenset(
            ["roles/bootstrap.md"]
        ),
        "before initializing user settings or handling an existing settings file": frozenset(
            ["roles/bootstrap.md"]
        ),
        "before resuming a failed step": frozenset(["roles/bootstrap.md"]),
        "opens: owns the ordered steps, their surfaces, failure states and authority gates": frozenset(
            ["roles/bootstrap.md"]
        ),
        "state in the report how its claims meet": frozenset(["roles/bootstrap.md"]),
        "state the current fact, and report a surface that still states a superseded fact.": frozenset(
            ["roles/bootstrap.md"]
        ),
        "use the required workspace facts in": frozenset(["roles/bootstrap.md"]),
    },
    "operations/closeout.md": {
        "before coordinating paired closeout": frozenset(["roles/manager.md"]),
        "before responding to gate findings from closeout preview or any landing route": frozenset(
            ["roles/manager.md"]
        ),
    },
    "operations/coordination.md": {
        "before delegating coordination": frozenset(["operations/planning.md"]),
        "before directing handovers, repair loops, review rounds, memory passes or relays": frozenset(
            ["roles/manager.md"]
        ),
        "before filling or reading message fields": frozenset(["operations/review.md"]),
        "before messaging a role agent through `role_message` on `agents-remember-task`": frozenset(
            ["roles/architect.md"]
        ),
        "before reconciling that start": frozenset(["operations/recovery.md"]),
        "before restoring an existing AR leaf": frozenset(["operations/recovery.md"]),
        "before starting a role and delivering its first intake": frozenset(["roles/architect.md"]),
        "before starting and coordinating the leaf": frozenset(["roles/manager.md"]),
        "before starting or messaging a role": frozenset(["roles/orchestrator.md"]),
        "before the Reviewer's first inspection": frozenset(["roles/manager.md"]),
        "for a closeout-result notice": frozenset(["roles/manager.md"]),
        "when `role_message` on `agents-remember-task` answers `held` because a child waits at a permission prompt, or its wait returns `permission-pending`": frozenset(
            ["roles/architect.md", "roles/orchestrator.md"]
        ),
        "when `role_message` on `agents-remember-task` returns `held` because a child waits at a permission prompt, or a wait returns `permission-pending`": frozenset(
            ["roles/manager.md"]
        ),
    },
    "operations/curation.md": {
        "before closeout": frozenset(["roles/curator.md"]),
        "before contacting another leaf": frozenset(["roles/curator.md"]),
        "before contacting the Manager about curation": frozenset(["roles/curator.md"]),
        "before continuing after a memory-tool failure": frozenset(["roles/curator.md"]),
        "before handover": frozenset(["operations/curation.md"]),
        "before intake and writer admission": frozenset(["roles/curator.md"]),
        "before reporting a completed curation result": frozenset(["roles/curator.md"]),
        "before running curation checks": frozenset(["roles/curator.md"]),
        "before sending an evidenced code concern": frozenset(["roles/curator.md"]),
        "before validating coherence": frozenset(["roles/curator.md"]),
    },
    "operations/implementation.md": {
        "before deciding closeout after a leaf sync": frozenset(["roles/manager.md"]),
        "before deciding whether a handover was taken or responding to a provider, seat or busy refusal": frozenset(
            ["roles/worker.md"]
        ),
        "before handing over a frozen candidate": frozenset(["roles/worker.md"]),
        "before making a measurement-tooling handover": frozenset(["roles/worker.md"]),
        "before recovering after that sync": frozenset(["operations/recovery.md"]),
        "before repairing a native memory-file sync conflict": frozenset(
            ["operations/curation.md"]
        ),
        "before repairing a sealed finding in this task's own files": frozenset(
            ["roles/worker.md"]
        ),
        "before responding to a non-work reply, an empty or doubled-seat refusal, an unreachable-Manager notice, or failed delivery of a required Manager notice": frozenset(
            ["operations/curation.md"]
        ),
        "before responding to a non-work reply, empty or doubled-seat refusal, or an unreachable-Manager notice": frozenset(
            ["operations/review.md"]
        ),
        "before writing the report": frozenset(["roles/worker.md"]),
        "when one small item costs about an hour, a second repair round or a second freeze": frozenset(
            ["roles/worker.md"]
        ),
        "when ruling on a measured defect or costly small work": frozenset(["roles/architect.md"]),
        "when the master line moves, run the supported sync of your own leaf yourself": frozenset(
            ["roles/worker.md"]
        ),
    },
    "operations/orientation.md": {
        "before acting on a denied or unanswered harness request": frozenset(
            [
                "operations/coordination.md",
                "operations/curation.md",
                "operations/implementation.md",
                "operations/planning.md",
                "operations/review.md",
            ]
        ),
        "before an admitted canonical task-document edit": frozenset(["roles/curator.md"]),
        "before asking a question": frozenset(["roles/worker.md"]),
        "before changing a canonical task document": frozenset(["roles/manager.md"]),
        "before deciding a formal permission": frozenset(["operations/coordination.md"]),
        "before deciding or writing memory": frozenset(["operations/curation.md"]),
        "before editing a canonical task document or marking your own completed step": frozenset(
            ["operations/implementation.md"]
        ),
        "before making a formal permission decision": frozenset(["roles/architect.md"]),
        "before responding to an advisory reviewer's permission prompt": frozenset(
            ["operations/review.md"]
        ),
        "before resuming a memory decision or write": frozenset(["operations/recovery.md"]),
        "before sending a developer question or handling a relayed answer": frozenset(
            ["roles/curator.md", "roles/orchestrator.md"]
        ),
        "before sending a developer question or recognizing a relayed answer": frozenset(
            ["roles/manager.md"]
        ),
        "before sending developer questions or handling relayed answers": frozenset(
            ["roles/reviewer.md"]
        ),
        "preserve the read rules under": frozenset(["roles/architect.md"]),
    },
    "operations/planning.md": {
        "before assigning an issue to another open leaf": frozenset(["operations/planning.md"]),
        "before releasing a packet": frozenset(["roles/architect.md"]),
    },
    "operations/review.md": {
        "before checking repaired findings at the round limit": frozenset(["roles/reviewer.md"]),
        "before commissioning advisory independent reviews": frozenset(["roles/architect.md"]),
        "before coordinating a memory review": frozenset(["operations/coordination.md"]),
        "before handling findings received after sealing, including those arriving after code PASS, or sending the ready-for-closeout notice": frozenset(
            ["roles/reviewer.md"]
        ),
        "before inspecting the assigned packet or candidate": frozenset(["roles/reviewer.md"]),
        "before opening or recording an ordinary code review round": frozenset(
            ["operations/coordination.md"]
        ),
        "before rechecking a repaired Worker record": frozenset(["roles/reviewer.md"]),
        "before recording a memory round, requesting an extra round or handling provider, seat or busy refusals": frozenset(
            ["roles/reviewer.md"]
        ),
        "before recording an ordinary leaf code round": frozenset(["roles/reviewer.md"]),
        "write and record the verdict, then follow": frozenset(["roles/reviewer.md"]),
    },
    "reference/bootstrap/foundation-state.md": {
        "before naming the next step in the conversation and report": frozenset(
            ["operations/bootstrap.md"]
        ),
        "before reading and reporting the knowledge foundation's state": frozenset(
            ["roles/bootstrap.md"]
        ),
    },
    "reference/coordination/shared/requirement-design.md": {
        "before authoring or amending a requirement and its task structure": frozenset(
            ["roles/architect.md"]
        ),
        "before grouping primary and additional same-topic packets": frozenset(
            ["operations/planning.md"]
        ),
    },
    "reference/curator/card-formats-and-citations.md": {
        "before checking or repairing citations": frozenset(["roles/curator.md"]),
        "before creating or maintaining cards or overviews": frozenset(["roles/curator.md"]),
        "before maintaining the entity catalog": frozenset(["roles/curator.md"]),
        "before writing card history": frozenset(["roles/curator.md"]),
    },
    "reference/curator/governing-sources.md": {
        "before authoring a governing decision record": frozenset(["roles/curator.md"]),
        "before explaining an assigned contract": frozenset(["roles/curator.md"]),
    },
    "reference/curator/intent-reconciliation.md": {
        "before promoting a historical oddity": frozenset(["operations/curation.md"]),
        "before reconciling current intent, ruled intent and implementation": frozenset(
            ["roles/curator.md"]
        ),
    },
    "reference/knowledge/commands.md": {
        "before a knowledge-system command or check": frozenset(["roles/reviewer.md"]),
        "before planning repository work": frozenset(["roles/architect.md"]),
        "before retrieving its context again": frozenset(["core/every-role.md"]),
        "before using a knowledge command or acting on its result": frozenset(
            ["roles/orchestrator.md"]
        ),
        "before using a knowledge command or interpreting its result": frozenset(
            ["roles/investigator.md", "roles/manager.md", "roles/worker.md"]
        ),
        "before using a knowledge command or its result": frozenset(["roles/bootstrap.md"]),
        "before using a knowledge-system command or interpreting its result": frozenset(
            ["roles/curator.md"]
        ),
    },
    "reference/knowledge/use.md": {
        "before checking code and memory": frozenset(["roles/curator.md"]),
        "before diagnosing": frozenset(["roles/architect.md"]),
        "before diagnosing a failing test": frozenset(["roles/worker.md"]),
        "before editing source": frozenset(["roles/worker.md"]),
        "before interpreting a knowledge read": frozenset(["roles/curator.md"]),
        "before interpreting their resolution": frozenset(["roles/bootstrap.md"]),
        "before investigating repository code": frozenset(["roles/investigator.md"]),
        "before relying on storage or path eligibility, cross-repository allowances, or serving-build metadata": frozenset(
            ["core/every-role.md"]
        ),
        "when inspecting changes": frozenset(["roles/reviewer.md"]),
    },
    "reference/message-handover.md": {
        "before delivering a developer question to a recipient or parent": frozenset(
            ["operations/orientation.md"]
        ),
        "before handing over work": frozenset(["roles/investigator.md", "roles/reviewer.md"]),
        "before handing work to a waiting seat": frozenset(
            ["roles/architect.md", "roles/manager.md", "roles/orchestrator.md"]
        ),
        "before locating a filed message or receipt": frozenset(
            ["operations/coordination.md", "operations/review.md"]
        ),
        "before responding to a full-store refusal": frozenset(
            [
                "operations/closeout.md",
                "operations/coordination.md",
                "operations/curation.md",
                "operations/implementation.md",
                "operations/orientation.md",
                "operations/planning.md",
                "operations/recovery.md",
                "operations/review.md",
            ]
        ),
        "before sending a handover": frozenset(["roles/worker.md"]),
        "before sending a message": frozenset(["roles/curator.md"]),
    },
    "reference/refused-write.md": {
        "before retiring or moving a task": frozenset(["roles/manager.md"]),
        "for a correct-input refusal of a covered write (`task_doc` edits (including an admitted task retirement or folder movement), `knowledge_write`, `citation_fix`, `citation_note_revision` and `curator_coherence` publication)": frozenset(
            ["core/every-role.md"]
        ),
    },
    "reference/tasks/document-authoring.md": {
        "before authoring a task document or requirement packet": frozenset(["roles/architect.md"]),
        "before editing, skipping or previewing task-document fields": frozenset(
            ["roles/worker.md"]
        ),
        "before editing, skipping or previewing task-document fields, or using `task_doc append_decision`": frozenset(
            ["roles/manager.md"]
        ),
        "before making the change": frozenset(["core/every-role.md"]),
        "before using `task_doc append_decision`": frozenset(["roles/orchestrator.md"]),
    },
    "roles/bootstrap.md": {
        "before asking a setup question": frozenset(["operations/bootstrap.md"]),
        "before taking a setup action": frozenset(["operations/bootstrap.md"]),
        "with an attributed baseline and resolving context, apply": frozenset(
            ["operations/bootstrap.md"]
        ),
    },
    "roles/curator.md": {
        "before capturing a discovery": frozenset(["operations/curation.md"]),
        "before repairing a failed curation step": frozenset(["operations/curation.md"]),
        "before writing the handover output": frozenset(["operations/curation.md"]),
        "when repairing memory encountered during curation": frozenset(["roles/curator.md"]),
    },
    "roles/manager.md": {
        "for a round beyond the settings limit": frozenset(["roles/manager.md"]),
        "the manager receives the worker's freeze notice to start the reviewer, the reviewer's code-`pass` notice to start the curator, and only the other occasions listed in": frozenset(
            ["operations/coordination.md"]
        ),
    },
    "roles/orchestrator.md": {
        "forward only questions requiring the developer and the permission notices described under": frozenset(
            ["roles/orchestrator.md"]
        ),
        "pending developer questions and held-report state from": frozenset(
            ["roles/orchestrator.md"]
        ),
        "the rulings record, requirement-change list and status file paths from": frozenset(
            ["roles/orchestrator.md"]
        ),
        "verified manager delivery, with reports, findings, review, curation and git publication separately addressed as": frozenset(
            ["roles/orchestrator.md"]
        ),
    },
    "roles/reviewer.md": {
        "before a code edit, task-acceptance write, role start or Git publication": frozenset(
            ["operations/review.md"]
        ),
        "in `fix-verification`, reuse the sealed evidence and verify only listed identifiers, subject to the sanctioned routes under": frozenset(
            ["roles/reviewer.md"]
        ),
    },
    "roles/worker.md": {
        "before handing over or attempting a role-start, acceptance or publication action": frozenset(
            ["operations/implementation.md"]
        ),
        "before reading the task inputs": frozenset(["operations/implementation.md"]),
    },
    "templates/curator-handoff-list.md": {
        "before producing the Curator hand-off list": frozenset(["roles/worker.md"]),
        "when naming them": frozenset(["roles/worker.md"]),
    },
}
#: target file -> (source, action) of one sentence that links two or more of its sections: one
#: action reading several sections (one pointer). Any other sentence linking one file twice fails.
MULTI_LINK_SENTENCES: dict[str, frozenset[tuple[str, str]]] = {
    "operations/coordination.md": frozenset(
        {
            ("roles/architect.md", "before starting a role and delivering its first intake"),
            ("roles/manager.md", "before starting and coordinating the leaf"),
            ("roles/orchestrator.md", "before starting or messaging a role"),
        }
    ),
    "operations/curation.md": frozenset(
        {
            ("roles/curator.md", "before intake and writer admission"),
        }
    ),
    "operations/orientation.md": frozenset(
        {
            (
                "roles/curator.md",
                "before sending a developer question or handling a relayed answer",
            ),
            (
                "roles/manager.md",
                "before sending a developer question or recognizing a relayed answer",
            ),
            (
                "roles/orchestrator.md",
                "before sending a developer question or handling a relayed answer",
            ),
        }
    ),
    "reference/coordination/shared/master-state.md": frozenset(
        {
            ("roles/manager.md", "before updating master state through completion"),
        }
    ),
    "templates/curator-handoff-ingestion.md": frozenset(
        {
            (
                "roles/curator.md",
                "before ingesting a producer list or authoring its Curator fields",
            ),
        }
    ),
}
# Wording variants of one action, used by different roles: one message holds at most one.
_EXCLUSIVE = (
    frozenset(
        {
            "before handing over work",
            "before handing work to a waiting seat",
            "before sending a handover",
            "before sending a message",
        }
    ),
    frozenset(
        {
            "before asking a question",
            "before sending a developer question or handling a relayed answer",
            "before sending a developer question or recognizing a relayed answer",
            "before sending developer questions or handling relayed answers",
        }
    ),
    frozenset(
        {
            "before a knowledge-system command or check",
            "before planning repository work",
            "before using a knowledge command or acting on its result",
            "before using a knowledge command or interpreting its result",
            "before using a knowledge command or its result",
            "before using a knowledge-system command or interpreting its result",
        }
    ),
    frozenset(
        {
            "before authoring a task document or requirement packet",
            "before editing, skipping or previewing task-document fields",
            "before editing, skipping or previewing task-document fields, or using "
            "`task_doc append_decision`",
            "before using `task_doc append_decision`",
        }
    ),
)


def _resolve(raw: str, source: str) -> str | None:
    raw = raw.strip("<>")
    if raw.startswith("{{detail:"):
        return raw[len("{{detail:") :].partition("}}")[0]
    if raw.startswith("skill://"):
        return raw.split(_SERVED, 1)[-1].partition("#")[0]
    if raw.startswith("#"):
        return source
    if "://" in raw or raw.startswith("mailto:"):
        return None
    return os.path.normpath(os.path.join(os.path.dirname(source), raw.partition("#")[0]))


def _norm(text: str) -> str:
    text = re.sub(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip(" *_-:;,").lower()


def _action(line: str, start: int, end: int) -> str:
    if line.lstrip().startswith("|"):
        return "row: " + _norm(line.strip().strip("|").split("|", 1)[0])
    action, head = action_of(line, start)
    if action is not None:
        return action
    head = _norm(head)
    if head:
        return head
    tail = line[end:]
    stop = _SENTENCE_END.search(tail)
    return "opens: " + _norm(tail[: stop.start() if stop else len(tail)])


def pointers(text: str, source: str) -> list[tuple[str, str]]:
    """(target file, action) once per sentence and file, fences excluded."""
    found: list[tuple[str, str]] = []
    fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        seen: set[tuple[int, str]] = set()
        row = line.lstrip().startswith("|")
        for match in LINK.finditer(line):
            target = _resolve(match.group(1), source)
            if target is None:
                continue
            begin = 0 if row else sentence_start(line, match.start())
            if (begin, target) in seen:
                continue
            seen.add((begin, target))
            found.append((target, _action(line, match.start(), match.end())))
    return found


def multi_link_sentences(text: str, source: str) -> list[tuple[str, str]]:
    """(target file, action) for each sentence that links one file two or more times."""
    found: list[tuple[str, str]] = []
    fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        counts: dict[tuple[int, str], int] = defaultdict(int)
        first: dict[tuple[int, str], tuple[int, int]] = {}
        row = line.lstrip().startswith("|")
        for match in LINK.finditer(line):
            target = _resolve(match.group(1), source)
            if target is None:
                continue
            key = (0 if row else sentence_start(line, match.start()), target)
            counts[key] += 1
            first.setdefault(key, (match.start(), match.end()))
        for key, count in counts.items():
            if count > 1:
                found.append((key[1], _action(line, *first[key])))
    return found


def duplicate_pointers(units: list[tuple[str, str]]) -> list[str]:
    """Problems for one first message given its (source path, text) units."""
    by_file: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for source, text in units:
        for target, action in pointers(text, source):
            by_file[target].append(action)
            sources[target].add((source, action))
    problems = [
        f"{target}: one sentence links it twice, unlisted {action!r}"
        for source, text in units
        for target, action in multi_link_sentences(text, source)
        if (source, action) not in MULTI_LINK_SENTENCES.get(target, frozenset())
    ]
    for target, actions in sorted(by_file.items()):
        if len(actions) < 2:
            continue
        admitted, _reason = DISTINCT_ACTIONS.get(target, (frozenset(), ""))
        if len(set(actions)) != len(actions):
            problems.append(f"{target}: same action twice {sorted(actions)}")
        elif not set(actions) <= admitted:
            problems.append(f"{target}: unlisted repeat {sorted(set(actions) - admitted)}")
        elif any(len(set(actions) & pair) > 1 for pair in _EXCLUSIVE):
            problems.append(f"{target}: one action under two wordings {sorted(actions)}")
        elif foreign := sorted(
            (source, action)
            for source, action in sources[target]
            if source not in ACTION_SOURCES.get(target, {}).get(action, frozenset())
        ):
            problems.append(f"{target}: listed action from another source {foreign}")
    return problems
