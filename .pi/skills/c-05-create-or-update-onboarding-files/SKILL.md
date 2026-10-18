---
name: c-05-create-or-update-onboarding-files
description: "Create and maintain file-level onboarding and repo entity catalogs. Enforces strict 1-to-1 source mapping, governing overview links, metadata, and references, and routes package/module/source-slice create, refresh, move, or delete cases to the c-03-repo-bootstrap skill."
---

# c-05-create-or-update-onboarding-files Create Or Update Onboarding Files

This package owns durable onboarding maintenance for two artifact types and one storage-specific adapter layer:

1. file-level onboarding units for concrete source files
2. repo-level entity catalogs for load-bearing cross-layer entities

Inline onboarding does not replace the file-level content model. It reuses the same semantic sections and adds storage-specific syntax, placement, and digest rules through the inline adapter workflow/template.

Planning stays in task artifacts. This package defines how onboarding itself is created, updated, and kept structurally consistent.

Before maintaining onboarding, use `c-08-ar-coordination-context-resolver` to resolve the target repository's active coordination context. It must use the `Domain Documentation` category declared in the resolved `system/sources.md` for the onboarding slice being maintained, rather than assuming that adjacent onboarding alone is sufficient or hard-coding one particular documentation system into the skill.

**Seat routing:** the Worker, Reviewer and Curator hand a leaf's candidate, verdict and memory
change directly to each other; the Manager starts the seats and retains closeout and gate
acceptance. Onboarding create/update duty belongs to the Curator (`l-01-agent-lifecycles`
`roles/curator.md`); the Worker produces code and a turn report. The Curator runs this skill's
workflows from the frozen change set and hand-off list fed directly by the Worker, the passed
freeze/verdict fed directly by the Reviewer, the leaf task doc and notes/,
and routes each item to the right onboarding home (a concrete sidecar or the governing overview
whose subject it is; the L3 Operational-Notes target is last-resort only, never a default). The
strict 1-to-1 source mapping and governing-overview links are unchanged by
this routing. A solo flat session with no separate curator seat runs
this skill itself, exactly as before.

When onboarding maintenance is happening inside a `c-09-git-worktree-manager`
worktree, check `worktree_status` before starting memory entries. If its
`freshness` block reports that the recorded code or memory source branch moved,
run `worktree_sync` before writing onboarding content unless that sync is blocked
by conflicts or an explicit developer decision. Carrying the moved official line
first keeps later onboarding updates and their Git attribution on top of current source
history instead of forcing a conflict-heavy carryover after the task is otherwise
done.

The `c-05-create-or-update-onboarding-files` skill remains the normal public entry point for create or update onboarding requests. When the request is route-level slice creation, refresh, move handling, or deletion cleanup, the `c-05-create-or-update-onboarding-files` skill should route the work to the `c-03-repo-bootstrap` skill `existing-memory-slice-maintenance` mode instead of reducing it to unrelated file-level updates.

## Converted Memory

Check the memory tree first. When it holds `knowledge/layout.json` it is **converted** (MIK-R21), and file cards and
route overviews follow `workflows/converted-card-workflow.md`:
- a card's evidence is `- <finding> [n]` lines over sidecar `references`;
- the curator writes the evidence as a citation table, and `citation_fix` authors the sidecar with resolved anchors
  (a new card, or a reference re-authored by its number);
- a change with no onboarding impact is an `onboarding_trace` row written through `knowledge-ingest`.

Never write sidecar JSON by hand. Old-format memory is refused; convert it by the procedure of MIK-R46 rule 6.
