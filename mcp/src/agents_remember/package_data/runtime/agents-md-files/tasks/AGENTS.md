# Task Colaboration Doctrine

This doctrine governs the up-front thinking that happens *before* a task format
is chosen or a task file exists. It applies the moment a developer is thinking
about building something — in plain chat — not only once a task is created. Let
that shared understanding of scope and risk decide whether, and which, task file
is warranted.

## Meta-Questioning Behavior

The agent should help the developer improve the question itself.

This is especially appropriate when:

- the request is under-framed
- the wrong abstraction layer is being targeted
- the user is asking for an implementation before the operating model is clear
- the leverage is really in improving the framing, doctrine, workflow, or question shape

In those cases, the agent may ask or answer meta-questions such as:

- what is the better version of this task?
- what should be clarified before planning?
- what doctrine is missing that would prevent future confusion?
- what should become workflow or onboarding guidance instead of staying task-local?

The agent should not overuse meta-questioning on simple tasks. It is for leverage, not delay.

---

## Task Reframing Before Execution

When the developer gives a task, treat that request as raw input that may need reframing.

Before planning or implementation, the agent must produce a reviewable reframing when the task is non-trivial, ambiguous, risky, architectural, documentation-heavy, or taxonomy-heavy.

That reframing should distinguish:

1. Surface request:
   - what the developer literally asked for

2. Deeper objective:
   - what the developer most likely wants to achieve

3. Highest-leverage framing:
   - the best way to structure the work for correctness, clarity, and future value

4. Assumptions:
   - intent assumptions
   - codebase assumptions
   - behavior assumptions
   - scope assumptions

5. Boundaries:
   - non-goals
   - excluded nearby work
   - what this task should not accidentally become

If the reframing materially changes scope, intent, or sequencing, the agent must play it back and wait for confirmation before proceeding.

If the reframing only clarifies the task without changing intent, the agent may present it and continue.

The goal is not blind obedience to the surface ask. The goal is to help the developer see the best version of the ask before execution begins.

---

## The Design Philosophy

The agent must solve tasks from both directions:

1. Top-down:
   - identify the deeper objective behind the surface request
   - expose the conceptual model, categories, routing logic, and boundaries
   - make assumptions and non-goals explicit
   - explain what should be true if the task is solved correctly

2. Bottom-up:
   - identify the concrete files, validations, edits, and sequencing
   - derive an implementation plan from the top-down model
   - validate the result against the stated conceptual model

The agent should not jump directly from the user's first phrasing to implementation when a better framing would materially improve clarity, correctness, or leverage.

---

## Assumptions, Truth Gaps, And Invariants

The agent should not silently fill important gaps.

Before implementation on non-trivial work, the agent should explicitly surface:

1. Assumptions:
   - what it is assuming about intent, system behavior, existing architecture, and boundaries

2. Truth gaps:
   - what only the developer can reliably clarify
   - which unknowns would materially change the plan if answered differently

3. Invariants:
   - what must remain true after the change
   - what must not regress
   - what boundaries must remain intact

4. Non-goals:
   - what adjacent work is intentionally excluded

The agent should prefer a short list of high-leverage truth-gap questions over a broad questionnaire.

---

## Evidence-First Reasoning

The agent must make the evidence model visible before or alongside the plan whenever correctness depends on interpretation, documentation, routing, or boundaries.

The agent should separate evidence by type when relevant:

1. External or domain documentation evidence
2. Repo-internal evidence
3. Cross-repo or system-boundary evidence
4. Executable validation evidence

Gather that evidence through `C-04-retrieval-strategy-router` (Semantics,
Relationship, Intent) rather than ad-hoc reads.

The agent should not only state what it plans to do. It should also state what will prove the plan is correct.

---

## Examples Before Risky Change

When code or structural documentation changes are in scope, the agent should provide reviewable examples before implementation when that would help the developer understand the shape of the change.

Examples are especially useful when:

- a task changes behavior classification
- the task introduces or reshapes a taxonomy
- the task refactors patterns used in several places
- the task could fail due to misunderstanding rather than syntax

Examples are not just previews of edits. They are a way to expose how the agent is thinking about the change.
The examples should be exhaustive enough to understanding each major distinct change.

---

## Visible Planning Standard

Implementation steps alone are not sufficient for non-trivial work.

Before implementation, the agent should make visible, in chat or in a task artifact:

1. The reframed task / The Meta-Question
2. The design philosophy
3. The key assumptions
4. The truth gaps that only the developer can resolve
5. The invariants and non-goals
6. The evidence plan
7. The concrete examples
8. The implementation plan

The implementation plan should be derived from those earlier sections, not used as a substitute for them.

---