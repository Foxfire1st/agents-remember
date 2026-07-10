# Orchestration-Task Template

The artifact a **strategist** drafts for the **orchestrator** (`roles/strategist.md`) — the sprint
plan and the sprint scope. The architect proposes the strategist pass, and the strategist drafts
this artifact only after developer approval; the orchestrator adopts the accepted draft into
durable task form (the strategist is a reader, not a mutator). When the developer sanctions a
strategist skip, `roles/orchestrator.md` owns the alternate author-and-adopt path. It is written
under the series/coordination `notes/` path the brief names and revised across drawing-board
rounds.

## Rules

1. **Shown work is required per section.** Every dependency edge carries evidence (a tool query, a
   file, a decision-log entry, a design-section citation, or — for new surfaces — a **declaration
   cross-reference**: the two leaf declarations that couple) — an uncited edge is refutable by
   default. Every blast-radius entry names its derivation (caller-set / doctrine-propagation /
   migration / user-visible). Every leaf move carries from→to + rationale.
2. **Surfaces are two-sided:** the surface lists distinguish **existing** surfaces (mapped against
   the route map) from **new** surfaces (greenfield, mapped by declaration: parent route/location +
   intended shape). New-surface edges come from declaration cross-reference — leaf B naming a
   surface leaf A creates is a pure ORDER edge; two leaves declaring additions under one parent
   route is a CONFLICT-risk edge at the parent.
3. **Coherence findings include the honest failures:** a leaf that can name neither existing
   surfaces nor the parent anchoring of its additions is recorded as **"unplannable as scoped"** —
   never silently guessed around (a merely not-yet-existing surface is NOT unplannable). Directional
   cross-master contradictions are flagged quo-vadis at the top of that section.
4. This is a **draft for adoption**, not a mutation: the strategist never edits task docs; the
   orchestrator (the portfolio owner) adopts the plan and records the adoption in the decision log.
5. The plan is reviewed adversarially (the plan-review criteria catalog,
   `../criteria/plan-review.md`) before the developer's drawing board; revisions append round
   sections rather than rewriting history.
6. Once adopted, this artifact is the sprint's standing scope: in-sprint additions before
   implementation starts trigger re-evaluation; out-of-sprint additions wait for the next sprint.

## Shape

```md
# Orchestration Task — <sprint label> · <repo(s)>

| Field            | Value                                            |
| ---------------- | ------------------------------------------------ |
| strategist       | <this session's agent id>                        |
| masters in scope | <master ids>                                     |
| status           | draft | in-review | round-<n> | adopted           |
| round            | <n> (3-round cap; the drawing board is the escalation) |
| written          | <YYYY-MM-DDTHH:MM>                                |

## Sprint Scope
- IN: <master id> — <why it is in this sprint>
- OUT: <master id> — <why it waits (dependency, capacity, out-of-scope addition)>

## Touch Surfaces (per leaf — two-sided)
| Leaf | Existing surfaces (route-map-mapped) | New surfaces (declared: parent route/location + intended shape) |
| ---- | ------------------------------------ | ---------------------------------------------------------------- |

## Dependency Graph (every edge cited — an uncited edge is refutable by default)
| From (leaf/master) | To | Kind (ORDER | CONFLICT | INDEPENDENT) | Evidence (cgc/grepai query · file · decision-log · design § · declaration cross-reference) |
| ------------------ | -- | ------------------------------------ | ------------------------------------------------------------------------------------------ |

## Blast-Radius Register (feeds the owning seat's loop-tier scoring)
| Leaf | Radius (low | medium | high) | Derivation (caller-set | doctrine-propagation | migration | user-visible) | Evidence |
| ---- | ---------------------------- | ------------------------------------------------------------------------- | -------- |

## Leaf Moves
| Leaf | From (master) | To (master) | Rationale |
| ---- | ------------- | ----------- | --------- |

## Coherence Findings
- ⟁ QUO-VADIS (developer decision required): <two masters heavily disagreeing / directional contradiction> | none
- <cross-master contradiction, duplicate work, vocabulary drift — with citations>
- Unplannable as scoped: <leaf id — what the task doc fails to pin down> | none

## Sprint Order / Waves
1. <wave 1: leaves/masters, parallel-safe because INDEPENDENT edges — cite> (note the
   landing-reconciliation cost of parallel waves as a scheduling consideration)
2. <wave 2 …>

## Open Risks
- <risk the plan carries knowingly, with the evidence limit that leaves it open>

## Re-Evaluation Triggers
- In-sprint master added before implementation starts → re-plan (strategist re-evaluates).
- Master added outside the sprint scope → next sprint's evaluation.
- <plan-specific triggers: a landing that invalidates an ORDER edge, a blocked wave, …>

## Evidence Inventory
- cgc queries (dependencies/callers/callees/complexity):
- grepai queries (search/trace):
- read_ar_files reads (paired source+onboarding) / route indexes read:
- task docs, decision logs, and design sections cited:
```
