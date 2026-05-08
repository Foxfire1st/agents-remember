# Task: C-05 Onboarding Philosophy And Overview Polish

**Status:** planning
**Repo:** agents-remember-md
**Type:** Docs / Skill
**Created:** 2026-05-08T19:40

---

## Objective

Improve the C-05 onboarding-maintenance skill so it explains the design philosophy behind onboarding artifacts, not only the mechanical rules for creating them. The intended result is a clearer package for engineers and agents: one that preserves the current C-05 contract while making the artifact model, overview role, and evidence boundaries easier to review and apply.

---

## Design Philosophy

C-05 should present onboarding as verified, path-local current-state commentary. The source file remains the implementation truth; onboarding preserves the things code does not reliably say by itself: invariants, conventions, boundaries, evidence, and why adjacent docs matter.

Overview files should guide navigation through a repo or component area rather than summarize each file. File-level onboarding should stay strict and local: one concrete source file, one companion unit. Entity catalogs should document real cross-layer entities that cause naming, migration, or review confusion. Inline onboarding should remain a storage adapter for the same file-level model, not a separate content model.

The practical rule for this task is: add explanatory prose where it helps humans and future agents understand the system, while keeping normative workflow rules in the existing skill, workflow, and template files.

---

## Review Findings

- C-05 already has a strong rule set for file-level onboarding, reference health checks, C-08 source discovery, and the three evidence buckets.
- The package lacks a local README that explains why those rules exist and how the artifact types relate.
- The C-05 package overview is useful but incomplete: it omits `inline-onboarding-workflow.md`, `repo-entity-catalog-workflow.md`, and `repo-entity-catalog-template.md` from the file index.
- The package-level placement example still shows a component overview and component-scoped mirrored tree, while the newer public README and file-level workflow favor repo-rooted sidecar onboarding plus one coherent repo overview.
- `repo-entity-catalog-template.md` is a load-bearing C-05 template but has no mirrored file-level onboarding companion yet.
- The repo-entity catalog template's `Source References` field is less auditable than the current file-level three-bucket reference model, and should either be tightened or explicitly justified as a catalog-specific simplification.

---

## Requirements

- Add a C-05 package `README.md` that explains the skill's philosophy, artifact model, and operating model without duplicating every rule from `SKILL.md`.
- Keep `SKILL.md` as the normative contract and link or point to the README only for orientation and design rationale.
- Align C-05 placement wording with the current repo-rooted sidecar model and the repo overview guidance used elsewhere in the repo.
- Update the C-05 onboarding overview so it is a navigation surface with a complete package file index and recommended read order.
- Create or update file-level onboarding companions for all source files changed by the implementation, including the new README and the existing repo-entity catalog template if it remains in scope.
- Clarify the repo-entity catalog evidence model so catalog entries remain auditable without blindly copying file-level sections where they do not fit.
- Preserve append-only update histories and refresh verification metadata through C-05 during implementation.
- Run focused wording/search validation and the C-02 drift detector before marking the task complete.

---

## Implementation Steps

### S1 — Add The C-05 Philosophy README

- [ ] Add `README.md` under the C-05 skill package as the human/agent orientation layer.
  - [ ] Explain why C-05 exists and what problem onboarding maintenance solves.
  - [ ] Describe the artifact model: file-level onboarding, repo overview, entity catalog, and inline adapter.
  - [ ] Describe the operating model: resolve with C-08, check drift with C-02, read source and onboarding together, update current-state commentary, health-check references, refresh metadata.
  - [ ] Include a short "what good looks like" section for review heuristics.
  - [ ] Verify the README stays explanatory and does not contradict the normative skill/workflow files.

### S2 — Align The Normative C-05 Contract

- [ ] Update C-05 source contract wording where it conflicts with the current model.
  - [ ] Update `SKILL.md` so the shared placement rules no longer imply stale component-scoped sidecar layout when repo-rooted sidecar onboarding is the current contract.
  - [ ] Add a brief pointer from `SKILL.md` to the new README as design rationale, without moving workflow rules out of the skill.
  - [ ] Review `file-level-onboarding-workflow.md`, `inline-onboarding-workflow.md`, and the templates for any terminology that should align with the new README.
  - [ ] Verify no source artifact presents inline onboarding as a separate semantic content model.

### S3 — Improve Overview And Catalog Coverage

- [ ] Update C-05 onboarding and catalog surfaces to serve future readers better.
  - [ ] Update the C-05 package onboarding `overview.md` with a complete file index, responsibilities, and recommended read order.
  - [ ] Create file-level onboarding for the new C-05 `README.md`.
  - [ ] Create or update file-level onboarding for `templates/repo-entity-catalog-template.md`.
  - [ ] Update repo-entity workflow/template wording if the evidence model needs a clearer citation shape.
  - [ ] Verify same-repository evidence stays in `Repo-Internal References` and true boundary evidence stays in `Cross-Repo References` where file-level onboarding is involved.

### S4 — Validate And Close The Light Task

- [ ] Validate the documentation slice and close only after onboarding is current.
  - [ ] Run focused wording searches across the C-05 package and its onboarding slice for stale placement, overview, and reference-taxonomy wording.
  - [ ] Run C-02 drift detection for `agents-remember-md` and confirm the changed onboarding companions are up to date or honestly marked pending for uncommitted source state.
  - [ ] Update this task checklist, decision log, and validation notes with the final result.
  - [ ] Set status to `Completed` only after the approved implementation, onboarding updates, and checks are finished.

---

## Proposed Code Examples

### E1 — C-05 README Shape

Distinct change covered: Add explanatory package-level prose for the design philosophy and operating model.

Why this example is included: The main implementation risk is writing another checklist instead of a readable orientation document.

```markdown
# C-05 Create Or Update Onboarding Files

## Why This Skill Exists

C-05 keeps onboarding useful by making it local, verified, and scoped to current state.

## Design Philosophy

Onboarding is not a planning layer and not a second codebase. It is verified companion commentary for code and repo structure.

## Artifact Model

- File-level onboarding: one source file, one companion unit.
- Repo overview: navigation and architecture context.
- Entity catalog: real cross-layer entities and naming drift.
- Inline onboarding: storage adapter for the same file-level model.
```

### E2 — Placement Rule Correction

Distinct change covered: Correct stale package-level layout guidance while preserving the strict 1-to-1 file-level contract.

Why this example is included: Placement wording is load-bearing because agents use it to decide where onboarding belongs.

```markdown
<onboarding-root>/
overview.md
entities.md
<mirrored-source-path>.md

File-level sidecar onboarding mirrors the source file's repo-relative path directly under the resolved onboarding root. Repo overview context is maintained in one coherent `overview.md`; deeper area findings should be merged into the relevant overview sections unless a repository-specific rule says otherwise.
```

### E3 — C-05 Overview Read Order

Distinct change covered: Make the package onboarding overview a navigation surface rather than an incomplete file inventory.

Why this example is included: The current overview is helpful but omits several package files that are part of the C-05 artifact model.

```markdown
## Recommended Read Order

1. `README.md` for the philosophy and artifact model.
2. `SKILL.md` for the normative routing and lifecycle contract.
3. `workflows/file-level-onboarding-workflow.md` and `templates/file-level-onboarding-template.md` for sidecar file-level maintenance.
4. `workflows/inline-onboarding-workflow.md` and `templates/inline-onboarding-block-template.md` for inline storage adaptation.
5. `workflows/repo-entity-catalog-workflow.md` and `templates/repo-entity-catalog-template.md` for repo-level entity catalogs.
```

---

## Decision Log

| Date-Time        | Decision                                                          | Rationale                                                                                                                                             |
| ---------------- | ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-05-08T19:40 | Use W-02 light-task workflow for the C-05 polish.                 | The developer requested a light work task file, and the work needs a durable plan plus approval but not the full heavy workflow.                      |
| 2026-05-08T19:40 | Keep the new README explanatory and the existing skill normative. | This preserves the separation between design rationale and executable workflow rules, so agents can orient themselves without weakening the contract. |

---

## Open Questions

- Should the repo-entity catalog template adopt the full `Docs References` / `Repo-Internal References` / `Cross-Repo References` split, or keep a catalog-specific `Source References` field with stricter citation guidance?

---

## References

- [agents-remember-md/AGENTS.md](../agents-remember-md/AGENTS.md)
- [agents-remember-md/README.md](../agents-remember-md/README.md)
- [agents-remember-md/docs/FAQ.md](../agents-remember-md/docs/FAQ.md)
- [agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/SKILL.md](../agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/SKILL.md)
- [agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/workflows/file-level-onboarding-workflow.md](../agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/workflows/file-level-onboarding-workflow.md)
- [agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/templates/file-level-onboarding-template.md](../agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/templates/file-level-onboarding-template.md)
- [agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/workflows/inline-onboarding-workflow.md](../agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/workflows/inline-onboarding-workflow.md)
- [agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/templates/inline-onboarding-block-template.md](../agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/templates/inline-onboarding-block-template.md)
- [agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/workflows/repo-entity-catalog-workflow.md](../agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/workflows/repo-entity-catalog-workflow.md)
- [agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/templates/repo-entity-catalog-template.md](../agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/templates/repo-entity-catalog-template.md)
- [ar-management/onboarding/agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/overview.md](../ar-management/onboarding/agents-remember-md/skills/U-01-core-skills/C-05-create-or-update-onboarding-files/overview.md)
- [ar-management/tasks/260508_file-level-onboarding-reference-taxonomy-alignment.md](../ar-management/tasks/260508_file-level-onboarding-reference-taxonomy-alignment.md)
