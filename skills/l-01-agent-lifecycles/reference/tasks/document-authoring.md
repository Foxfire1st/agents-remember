# Task documents and requirement packets

Architects and planning roles read this detail before authoring a task document or requirement packet. It provides the prose conventions and templates for a plan a developer can review on its own.

## Reuse the work that owns the request

Before introducing another task for the same request, compare its intended outcome with the selected active task and requirement. Reuse that work when it already owns the scope. Resolve a difference through the owning role rather than creating a second promise.

## Explain the plan

Write the problem, intended outcome, reasons, design and decisions as connected prose. A reader must understand the proposal without a browser or the planning conversation. Name the current failure or missing ability, who meets it, what changes and what remains unchanged. Explain the chosen approach and the relevant alternatives; scale the detail to the work.

Before authoring a leaf, master or requirement packet, use the [leaf task template](../../templates/leaf-task.md) for one leaf, the [master task template](../../templates/master-task.md) for the shared outcome, and the [requirement packet template](../../templates/requirement-packet.md) for the canonical promise. Keep task facts in those artifacts. The templates guide authoring; the task schema and tool own the stored structure and rendered headings.

Before writing the design, read [the collaboration method](collaboration-method.md) for assumptions, evidence, examples and a visible design plan. Requirement approval, task ownership and changes to scope follow [the planning operation](../../operations/planning.md); this detail creates no additional approval or execution route.

Before creating a leaf through `task_doc`, read [retained-evidence lifecycle](../../core/acceptance.md#record-how-retained-evidence-will-be-maintained) for the contract or expiry information its handoff must record for each new or retained fixture, recording, generator, shared support file or migration proof.

## Put prose in the fields that render it

Author task documents through `task_doc` on `agents-remember-task`. Put the outcome and its reason in `objective`, the settled approach in `design`, exclusions in `nonGoals` and observable completion in `acceptance`. Link approved packets in `requirements`; a task's explanation does not rewrite their contract.

A leaf renders these fields and its steps, examples, decisions, questions and references. Additional narrative belongs in a `freeform` section's `body`. A master renders its top-level prose and its ordered `sections`: use `subTasks` for the generated child list and `sharedDecisions` for the decision table. Put its other narrative, questions and references in `freeform` sections. Do not put a second copy of a top-level field in a section.

## Show the proposed changes

For code work, show the planned structure before any code is written: where each change lives (files and folders added, moved, split or removed, with paths, following the coding guidelines), and its skeleton (new or changed types, modules and function signatures, each with a one-line contract and no body). Add a short code example where a change is hard to judge from the skeleton. Keep contracts short; over-detailed contracts make agents worse.

For a leaf, use `codeExamples`. When its examples are deferred or unnecessary, explain their absence in `codeExamplesNote`; do not combine that note with examples.

For a master, put examples or the explanation for their absence in a `freeform` section. A master accepts neither `codeExamples` nor `codeExamplesNote`.

## Preserve decisions and approved intent

Keep earlier task decisions visible. Append the new decision and its reason when direction changes instead of rewriting the earlier choice. Use the task's decision fields, not a second history section.

Keep an approved packet at its version-addressed path when a successor supersedes it. Record that relationship in the successor and requirement index instead of overwriting the frozen packet. Before changing the promise, follow [the requirement-change rule](../../core/loop.md#compile-requirements-before-task-topology) for approval, affected acceptance and rebriefing.
