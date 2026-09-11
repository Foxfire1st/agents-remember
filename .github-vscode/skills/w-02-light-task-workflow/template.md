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

## Requirement Projection

- **R1 @ v1** — `primary` — [canonical packet](requirements/R1-v1-<slug>.md)
- **R2 @ v1** — `dependency` — [canonical packet](requirements/R2-v1-<slug>.md)

Corpus approval: <durable developer ruling citation>. This is a filtered projection; the linked
packets are the requirement contracts and this task does not rewrite them.

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
- Canonical requirement index: `requirements/README.md`
- Requirement-corpus developer approval: <durable ruling citation>
- Builder per-requirement acceptance envelope: <task-relative report path | pending>
- Independent per-requirement adjudication: <task-relative verdict path | pending>
- Detailed leaf Requirement Attempt Journal: <single append-only journal path containing separate
  worker and independent reviewer records | pending>
````

## Usage Rules

1. Keep the section structure even for small tasks.
2. Use `c-08-ar-coordination-context-resolver` resolved context paths such as `<task-root>/`, `<onboarding-root>/`, `<docs_root>/`, `<tools_path>`, and `<sources_path>`.
3. Store the light-task artifact as `<task-root>/<task-slug>/task.md`; if the task becomes worktree-backed, the `c-09-git-worktree-manager` skill stores its leaf contract at `<task-root>/<task-slug>/enclosures/<leaf-id>/series-contract.md`.
4. When code changes are in scope, include proposed code examples for each distinct implementation change. If a planning slice intentionally defers its examples to the plan gate, set `codeExamplesNote` (e.g. "Drafted at the plan gate.") so the rendered section says so instead of reading as if none are needed.
5. For documentation-only or other non-code tasks, keep the section and state that no code examples are needed.
6. Keep every checklist item on its own line.
7. Indent nested checklist items by two spaces beneath their parent checkbox.
8. Treat the parent checkbox as the step outcome, and keep implementation substeps plus the verification check nested under it.
9. Mark nested implementation substeps complete before the nested verification check, and mark the parent step complete only after all nested items are complete.
10. Add or reorder checklist items when scope changes, then get approval again if the change is significant.
11. Use the light-task status values: `planning`, `inProgress`, `Completed`; a descriptive `statusNote` may follow as a human-readable suffix. A leaf doc may also carry `headerNotes` (extra `**Key:** value` header lines) and freeform `sections` (appended after References) for bespoke prose beyond the template — the escape hatch; the standard sections stay the backbone.
12. Use `YYYY-MM-DDTHH:MM` anywhere the template records task-local dates or timestamps, including metadata, decision logs, progress notes, and review outcomes.
13. Treat `## Decision Log` as append-only: preserve superseded entries and add later rows that override, reject, or clarify earlier decisions.
14. Size the `## Design` section to the request per `tasks/AGENTS.md`; for a straightforward change, state that no design reasoning is needed rather than leaving the section blank.
15. A task that creates, promotes, or retains durable test evidence must record its owner, consumer
    population, and one terminal lifecycle: either a registered stable contract with a real owner
    and executable evidence node, or an explicit expiry date plus executable replacement/removal
    event. "Keep for future debugging" is not a lifecycle decision.
16. Before creating this task document, compile and cold-read the canonical requirement corpus from
    `requirement-packet-template.md`, then obtain developer approval. Every projected requirement
    names its stable ID, exact version, version-addressed canonical packet, and topology role. The
    packet must itself record the matching corpus approval citation. Never rewrite the contract in
    task prose.
17. A leaf owns exactly one `primary` requirement revision. It may list other revisions only as
    `dependency` or `preservation` context and may not claim to close them. Split a leaf that would
    close multiple independently falsifiable requirements; one revision may map to several leaves
    when it has independently executable manifestations.
18. The builder handoff contains one acceptance block for the leaf-owned primary stable ID +
    version with status `satisfied | blocked | approved-change`, delivery rationale/citations,
    verification rationale/citations including the failure caught, and exact command/result or
    durable evidence. `blocked` and `approved-change` also cite the durable developer
    approval/ruling. Code citations use path + symbol; non-code citations use path +
    section/anchor. Dependency and preservation revisions remain contextual checks outside the
    closure envelope and may not be claimed closed.
19. The independent reviewer adjudicates every leaf-owned primary ID + version `accepted |
    rejected` after inspecting the cited artifacts and writes an independent rationale. Missing
    rationale, wrong-class evidence, invalid citations, or missing developer approval for a
    `blocked` or `approved-change` delivery forces rejection; a `satisfied` delivery needs no
    exception ruling.
    No overall pass is valid while any owned ID is rejected or its accepted handoff status remains
    `blocked`. The durable-evidence stable-contract-or-expiry hold point remains separate and does
    not satisfy a requirement acceptance block.
20. A changed requirement increments its version under the same stable ID, cites durable developer
    approval, invalidates affected acceptance state, updates every affected projection, and
    rebriefs affected leaves. Unaffected ID/version acceptance remains valid.
21. Keep semantic requirement versions separate from delivery attempts. Advance an attempt only
    when an exact candidate is handed to independent review, or after reviewer rejection when a
    successor is handed off. Internal implementation/test/evidence reruns are separate experimental
    protocol events with candidate, command, result, failure cause, repair, and expected next proof.
    Before review handoff, append one immutable worker attempt for the owned primary requirement
    revision and this task document's leaf manifestation to its task-local append-only Requirement
    Attempt Journal. Bind the leaf-local attempt ID, predecessor and carried findings, exact
    candidate, requirement-specific status/rationales/citations/findings/failure class, a
    content-addressed immutable expanded-evidence reference, and append time. Do not duplicate the
    complete master envelope or experimental-run body per record; prior records are never edited or
    deleted. An unrelated later candidate does not reopen an accepted attempt.
    Validate the complete record before append and treat append plus exact-candidate review handoff
    as one logical formal-attempt boundary. A malformed pre-handoff row is preserved with an
    append-only `non-attempt-correction`/void reference and consumes no attempt ID; a malformed
    handed-off row requires independent reviewer rejection before a successor can be handed off.
22. The independent reviewer appends a separate `accepted | rejected` record for that exact attempt
    and candidate without modifying the worker record. Every rejection finding uses exactly one of
    `implementation defect`, `evidence gap`, `requirement contradiction/overconstraint`,
    `test/tool defect`, or `external blocker`. Requirement problems require developer-approved
    revision; worker and reviewer cannot rewrite them.
23. A rejected attempt advances through a successor citing predecessor findings. Accepted attempts
    remain closed unless an independent reviewer proves direct regression and the owning manager
    (architect in a flat run) records a bounded invalidation, or an approved new requirement version
    invalidates that manifestation. Detailed leaf records are authority; summaries cannot reopen
    acceptance.
