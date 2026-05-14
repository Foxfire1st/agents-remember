# Governing Route Map — <repo>

## Purpose

This file decides where durable route-local `overview.md` files should be placed in the mirrored onboarding hierarchy.

## Placement Principles

- Place `overview.md` at the source route where the area begins.
- Prefer locality over detached architecture folders.
- Do not create an overview merely because a folder exists.
- Create an overview when the subtree has a shared model, workflow, invariant, routing burden, docs dependency, cross-repo boundary, or multiple hotspots.
- File-level onboarding remains separate and must link back to the nearest governing overview.

## Proposed Governing Routes

| Source Route | Onboarding Overview | Governs | Reason | Confidence |
|---|---|---|---|---|
| `src/helpdesk/` | `src/helpdesk/overview.md` | helpdesk controllers, mappers, view models | shared helpdesk status/view-model model | [HIGH] |

## Routes Considered But Deferred

| Source Route | Reason Deferred | Revisit Trigger |
|---|---|---|
| `src/dto/` | mostly passive data containers | promote if DTOs encode behavior or cross-repo schema |

## Cross-Cutting Concepts

| Concept | Primary Local Anchor | Secondary Local Mentions |
|---|---|---|
| Command lifecycle | `src/commands/overview.md` | `frontend/helpdesk/overview.md`, `src/events/overview.md` |

## Parent / Child Overview Relationships

| Parent Overview | Child Overview | Reason Child Exists |
|---|---|---|
| `src/helpdesk/overview.md` | `src/helpdesk/mappers/overview.md` | mapper-specific conventions and load-bearing status transforms |

## Developer Questions

- Is this the right local anchor for `<area>`?
- Does this route actually own the concept, or only participate in it?
- Are any folders missing that new developers usually misunderstand?
