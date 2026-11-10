# Task Collaboration Method

This method guides the agent and developer as they work out scope and risk before choosing a task format or creating a task file. It applies as soon as the developer considers building something, including in plain chat.

Let that shared understanding of scope and risk decide whether a task file is warranted and which format to use.

## Meta-Questioning Behavior

The agent should help the developer improve the question itself.

This is especially appropriate when:

- the request is poorly framed
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

- **Surface request:** what the developer literally asked for.
- **Deeper objective:** what the developer most likely wants to achieve.
- **Best framing:** how to structure the work for correctness, clarity and future value.
- **Assumptions:** intent, codebase, behavior and scope assumptions.
- **Boundaries:** non-goals, excluded nearby work and what this task should not accidentally become.

If the reframing materially changes scope, intent, or sequencing, the agent must present the reframing to the developer and wait for confirmation before proceeding.

If the reframing only clarifies the task without changing intent, the agent may present it and continue.

Help the developer improve the request before execution begins.

---

## The Design Philosophy

The agent must solve tasks from both directions:

For top-down reasoning:

- identify the deeper objective behind the surface request
- expose the conceptual model, categories, routing logic and boundaries
- make assumptions and non-goals explicit
- explain what should be true if the task is solved correctly

For bottom-up reasoning:

- identify the concrete files, validations, edits and sequencing
- derive an implementation plan from the top-down model
- validate the result against the stated conceptual model

The agent should not jump directly from the user's first phrasing to implementation when a better framing would materially improve clarity, correctness, or leverage.

---

## Assumptions, Truth Gaps, And Invariants

The agent should not silently fill important gaps.

Before implementation on non-trivial work, the agent should explicitly surface:

- **Assumptions:** what it assumes about intent, system behavior, existing architecture and boundaries.
- **Truth gaps:** what only the developer can reliably clarify, and which unknowns would materially change the plan if answered differently.
- **Invariants:** what must remain true after the change, what must not regress and which boundaries must remain intact.
- **Non-goals:** what adjacent work is intentionally excluded.

The agent should prefer a short list of high-leverage truth-gap questions over a broad questionnaire.

---

## Evidence-First Reasoning

The agent must make the evidence model visible before or alongside the plan whenever correctness depends on interpretation, documentation, routing, or boundaries.

The agent should separate evidence by type when relevant:

- External or domain documentation evidence
- Repository-internal evidence
- Cross-repository or system-boundary evidence
- Executable validation evidence

Before gathering that evidence, read [knowledge use](../knowledge/use.md#read-code-and-knowledge-together) for combining attributed knowledge with code reads. Follow that procedure rather than choosing ad hoc reads.

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
The examples should cover every major distinct change well enough for the developer to understand it.

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
