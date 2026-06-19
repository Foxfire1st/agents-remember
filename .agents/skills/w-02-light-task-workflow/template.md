# Light Task Template

Use this template for `task.md` inside any task wrapper created by `w-02-light-task-workflow`.

This template is also the **render spec** for the JSON-primary task document: the `task_doc` MCP tool renders an `ar-task-document/v1` JSON into exactly this `task.md` shape. For tool-managed `light` and `subTask` documents, edit the JSON through `task_doc` and let it re-render — do not hand-edit the generated markdown. (Series *master* files are not tool-managed yet; they stay hand-authored.)

Implementation sections use checkbox-based steps and nested checkbox items. Keep every checklist item on its own line, and indent nested checklist items by two spaces beneath their parent checkbox. The checklist is the live execution state during implementation and review.

````markdown
# Task: <Title>

**Status:** planning
**Repo:** <primary repo>
**Type:** <Docs | Skill | Config | Other>
**Created:** <YYYY-MM-DDTHH:MM>

---

## Objective

<What is changing and why. Keep this brief and concrete.>

---

## Requirements

- <requirement>
- <requirement>

---

## Design

<Settled design for this task; depth scales with its nature — follow the Task
Collaboration Doctrine (`tasks/AGENTS.md`). Implementation Steps derive from this.
Straightforward change → "No design reasoning needed.">

---

## Implementation Steps

### S1 — <title>

- [ ] <step outcome>
            - [ ] <substep>
            - [ ] <substep>
            - [ ] <verification or review-ready check>

### S2 — <title>

- [ ] <step outcome>
            - [ ] <substep>
            - [ ] <substep>
            - [ ] <verification or review-ready check>

---

## Proposed Code Examples

### E1 — <title>

Distinct change covered: <what kind of implementation change this example represents>

Why this example is included: <why this is the representative example the developer should review>

```<language>
<example snippet>
```

### E2 — <title or "Not needed for this task">

Distinct change covered: <second distinct change type, or explain why no further code examples are needed>

Why this example is included: <reason>

```<language>
<example snippet or short note>
```

---

## Decision Log

| Date-Time          | Decision           | Rationale |
| ------------------ | ------------------ | --------- |
| <YYYY-MM-DDTHH:MM> | <what was decided> | <why>     |

---

## Open Questions

- None.

---

## References

- <related file, ticket, or discussion>
````

## Usage Rules

1. Keep the section structure even for small tasks.
2. Use `c-08-ar-coordination-context-resolver` resolved context paths such as `<task-root>/`, `<onboarding-root>/`, `<docs_root>/`, `<tools_path>`, and `<sources_path>`.
3. Store the light-task artifact as `<task-root>/<task-slug>/task.md`; if the task becomes worktree-backed, the `c-09-git-worktree-manager` skill stores `contract.md` beside it in the same wrapper folder.
4. When code changes are in scope, include proposed code examples for each distinct implementation change. If a planning slice intentionally defers its examples to the plan gate, set `codeExamplesNote` (e.g. "Drafted at the plan gate.") so the rendered section says so instead of reading as if none are needed.
5. For documentation-only or other non-code tasks, keep the section and state that no code examples are needed.
6. Keep every checklist item on its own line.
7. Indent nested checklist items by two spaces beneath their parent checkbox.
8. Treat the parent checkbox as the step outcome, and keep implementation substeps plus the verification check nested under it.
9. Mark nested implementation substeps complete before the nested verification check, and mark the parent step complete only after all nested items are complete.
10. Add or reorder checklist items when scope changes, then get approval again if the change is significant.
11. Use the light-task status values: `planning`, `inProgress`, `Completed`.
12. Use `YYYY-MM-DDTHH:MM` anywhere the template records task-local dates or timestamps, including metadata, decision logs, progress notes, and review outcomes.
13. Treat `## Decision Log` as append-only: preserve superseded entries and add later rows that override, reject, or clarify earlier decisions.
14. Size the `## Design` section to the request per `tasks/AGENTS.md`; for a straightforward change, state that no design reasoning is needed rather than leaving the section blank.
