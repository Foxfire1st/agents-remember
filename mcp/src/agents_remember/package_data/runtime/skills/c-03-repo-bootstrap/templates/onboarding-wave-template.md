# Onboarding Wave <number> — <short name>

<!-- The bootstrap orchestrator uses this template to assign a bounded wave of route-overview or file-onboarding work. -->

| Field | Value |
|---|---|
| repo | <repo> |
| generated | <YYYY-MM-DDThh:mm> |
| waveType | route-overview / file-onboarding |
| mode | quick-orientation / safe-starter-memory / cross-repo-focused / domain-doc-focused / existing-memory-slice-maintenance / full-bootstrap |
| status | planned / in-progress / blocked / curator-review / complete |

## Goal

<Create durable onboarding for a specific, bounded set of routes or files.>

## Included Cards

| Priority | Card | Target | Reason |
|---|---|---|---|
| high | `<card path>` | `<overview or source path>` | landmine / boundary / core logic |

## Excluded Or Deferred

| Path | Reason | Revisit Trigger |
|---|---|---|
| `<path>` | routine helper | when touched by active task |

## Evidence Required

| Evidence Pack | Applies To | Required? |
|---|---|---|
| `<docs-pack>` | `<paths>` | yes / no |
| `<boundary-pack>` | `<paths>` | yes / no |

## Worker Instructions

1. Read the assigned card first.
2. Read only the files and evidence listed in the card.
3. Before writing a route overview in a route-overview wave, read and use the [route-local overview template](route-local-overview-template.md).
4. Before writing file onboarding in a file-onboarding wave, read and follow [the Curator card procedure](../../l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md).
5. Keep planning notes out of durable onboarding.
6. Preserve strict file-level 1-to-1 mapping.
7. Add backlinks from file onboarding to nearest governing overview.
8. Do not emit absolute filesystem paths.
9. Do not use source registries or embeddings as evidence.
10. Return changed onboarding paths and unresolved questions only.

## Worker Assignments

| Worker | Card | Output |
|---|---|---|
| worker-1 | `<card>` | `<onboarding path>` |

## Done When

- Every included target has onboarding or an explicit blocker.
- Every docs/cross-repo claim is evidence-backed.
- Every unresolved item is recorded.
- Curator review has passed or listed required fixes.

## Developer Review Questions

1. Does this wave target the right first routes/files?
2. Are any included targets not worth documenting yet?
3. Are any deferred targets actually dangerous?
4. Are the LOW-confidence items answerable now?
