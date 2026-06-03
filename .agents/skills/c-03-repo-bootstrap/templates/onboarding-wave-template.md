# Onboarding Wave <number> — <short name>

| Field | Value |
|---|---|
| repo | <repo> |
| generated | <YYYY-MM-DDThh:mm> |
| waveType | route-overview / file-onboarding |
| mode | quick-orientation / safe-starter-memory / cross-repo-focused / domain-doc-focused / full-bootstrap |
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
3. For route overview waves, use `route-local-overview-template.md`.
4. For file onboarding waves, invoke or follow `c-05-create-or-update-onboarding-files`.
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
