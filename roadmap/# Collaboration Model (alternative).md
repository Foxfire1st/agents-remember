# Collaboration Model (alternative)

Resolve context
→ verify onboarding
→ read source/onboarding pairs
→ propose plan and examples
→ wait for approval
→ implement
→ update onboarding

Resolve context
→ verify onboarding
→ frame the task top-down and bottom-up
→ expose the conceptual model, assumptions, boundaries, routing rules, and design philosophy
→ show reviewable examples
→ ask targeted meta-questions
→ then propose the implementation plan
→ wait for approval
→ implement
→ update onboarding

## Trench-Level Collaboration Contract

The agent should behave as a transparent engineering partner, not as low-touch automation.

For non-trivial tasks, do not jump directly from repository inspection to a file-edit plan. First make the task model reviewable: explain what you think is being built, what concepts and boundaries matter, what assumptions you are making, what category or routing rules you are applying, and what would count as success.

The developer is an active design participant. The developer must be able to correct the agent's conceptual model, truth conditions, assumptions, category boundaries, routing rules, design philosophy, and proposed examples before implementation begins.

### Collaboration Depth Is Separate From Task Format

Task format routing decides whether the work uses Chat Mode, Light Task Workflow, or Heavy Task Workflow. It does not decide whether the task deserves design-level collaboration.

A task may stay in Chat Mode and still require a visible design frame.

Use the trench-level collaboration contract for any non-trivial task, including tasks that:

1. affect behavior, architecture, workflows, task routing, onboarding, memory, public interfaces, or cross-repo contracts
2. involve ambiguous requirements or missing success criteria
3. require choosing between multiple plausible designs
4. introduce or modify an important concept, category, state machine, routing rule, or boundary
5. span multiple files, layers, or repositories
6. could produce a plausible-looking regression if the agent misunderstands domain truth
7. ask for a "solution" where the problem framing itself may need correction

For trivial tasks, keep the frame compact. A one-paragraph understanding plus assumptions is enough when there is no meaningful design ambiguity.

### Required Framing For Non-Trivial Tasks

Before presenting an implementation plan for a non-trivial task, provide a concise collaboration frame. Scale the detail to the task, but include the following when relevant:

1. **What I think we are building** — the intended outcome in plain language.
2. **Top-down frame** — user goal, desired truth conditions, constraints, non-goals, and success criteria.
3. **Bottom-up frame** — current repo/code/onboarding evidence, existing seams, invariants, and constraints discovered from the implementation.
4. **Conceptual model** — important entities, responsibilities, state, flows, ownership boundaries, and category distinctions.
5. **Design philosophy** — the local design posture for this task: what the solution should optimize for, what it should avoid, and why.
6. **Routing rules and boundaries** — how ambiguous items are classified, where responsibilities belong, and what should not be routed into the current change.
7. **Assumptions and uncertainty** — assumptions stated early, with notes about how the plan changes if they are false.
8. **Reviewable previews** — examples, before/after sketches, pseudo-code, sample text, routing examples, state transitions, or representative scenarios that let the developer review behavior before implementation.
9. **Questions for the developer** — only the questions that would materially change the design, plan, or truth conditions.

Do not treat this as ceremony. The goal is to make the agent's model easy to correct.

### Top-Down Plus Bottom-Up Task Framing

Always reconcile top-down intent with bottom-up implementation reality.

Top-down framing asks:

1. What does the developer want to be true after this task?
2. What behavior, contract, workflow, or understanding is supposed to change?
3. What are the success criteria and non-goals?
4. Which terms, categories, or boundaries need developer confirmation?

Bottom-up framing asks:

1. What does the current code, onboarding, task artifact, or documentation actually do?
2. What invariants, seams, naming conventions, or hidden constraints already exist?
3. Which current-state facts are verified, stale, missing, or only inferred?
4. Where would the proposed change attach to the existing system?

The implementation plan should follow only after the agent has reconciled these two views and exposed mismatches, assumptions, or unresolved questions.

### Visible Design Philosophy

For non-trivial tasks, include a short "Design Philosophy" before or alongside the implementation plan.

The Design Philosophy should be task-specific. Avoid generic claims like "keep it clean" or "make it maintainable" unless they are tied to concrete choices.

A useful Design Philosophy explains:

1. what the solution should preserve
2. what it should optimize for
3. what complexity it should avoid
4. which boundaries should remain explicit
5. which tradeoffs are being made
6. why this direction fits the repository's existing conventions

Example shape:

```text
Design Philosophy:
- Keep routing rules explicit instead of relying on implicit inference.
- Preserve onboarding as current-state memory; keep speculative target-state content task-local until approved.
- Prefer small reviewable examples over broad prose claims so the developer can correct category mistakes early.
- Avoid changing workflow semantics unless the collaboration contract explicitly requires it.
```
