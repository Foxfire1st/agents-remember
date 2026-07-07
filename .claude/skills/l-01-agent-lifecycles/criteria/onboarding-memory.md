# Criteria Catalog — Onboarding/Memory Review

The standing criteria an **adversarial reviewer** (`../roles/reviewer.md`) MUST run over the
memory side of a change set: sidecars, route overviews, route indexes, update histories. Binds at
the master-exit and super-exit seams (the onboarding-vs-code lens) and in loop reviews whose change
set carries onboarding (see the binding table in `../roles/reviewer.md`). Criteria are never made
up on the spot: the standing list below is the regression floor, run every time; the exploratory
mandate and the promotion ratchet keep the catalog alive.

## Standing Criteria (MUST RUN — the regression floor)

### OM-1 — Staleness diff vs as-landed code

**Diff every body claim about current behavior against the as-landed code.** A sidecar or overview
body that describes the present tense is checked against what the branch actually ships — deleted
things still described, renamed things under old names, duplicated rows.

- Catching evidence, two separate engagements: 260703-L8 cycle 6 (owner follow-up pass) — route
  overview bodies still described **deleted canvas models** (the panels overview's build-job/frame
  tail) and carried **duplicated table rows** (the tools and controlplane overviews' Layout
  tables), durably recorded in those overviews' own 2026-07-05T19:25 Update History entries;
  260703-L10 — the flowModels sidecar body was still the pre-convergence 8-model census and its
  references row still said "8 models" (the sidecar's 2026-07-06T12:05 history entry + L10R-3).

### OM-2 — History-only-update detection

**"Refreshed" must mean a genuine body edit.** An onboarding file whose only change is a new
Update History line has NOT been refreshed — the body still asserts the old truth. Check that
every claimed refresh touched body sentences, not just the history list.

- Catching evidence: 260703-L8 cycle 6 — the builder reported route overviews as refreshed when
  they were **history-only**; the closeout body gate caught it (L8 decision log, cycle-6 entry:
  "report-vs-artifact verification belongs in the reviewer criteria catalog").

## Candidate Criteria (seeded exploratory — one catching engagement; promote at ≥2)

Run under the exploratory mandate; a candidate is proposed for promotion into the standing list
when it catches in a second engagement (the ratchet below).

### OM-3 — Newest-first with the checker's own semantics *(candidate — 1 catch)*

**Update History sorts newest-first under the CHECKER's datetime parse, not lexicographically.**
The quality checker compares naive timestamps as-is and folds tz-aware stamps to UTC; a merge or
mechanical insert that sorts by string can pass the eye and fail the gate (or worse, pass the gate
in the wrong order). Verify ordering with the checker's semantics.

- Catching evidence (single engagement): 260703-L11 — four onboarding history collisions from the
  parallel wave had to be re-sorted **with the checker's own datetime semantics** (naive compare
  as-is, tz-aware folds to UTC); the quality gates caught the owner's wrong sort key (L11 decision
  log).

## Exploratory Mandate

Beyond the standing list, the reviewer owes **novel lenses** (the brief sets N; default 2): attack
surfaces of THIS memory delta the catalog does not name yet. Every novel finding-class that
survives refutation is proposed as a catalog amendment in the verdict.

## Promotion Ratchet

- A **candidate** criterion that catches a real defect in **≥2 separate engagements** is promoted
  into the standing list above, with its catching evidence cited — escaped bugs become permanent
  tests. Promotion is proposed in the verdict and lands on the loop owner's acceptance.
- A **standing** criterion that fires nothing for **N consecutive engagements** (default 5)
  demotes to spot-check.
- A criterion that can be **mechanized graduates out of the catalog into a gate** — the closeout
  body gate is the working example, and it is THIS catalog's own OM-2 mechanized: the gate catches
  history-only "refreshes" at commit time, where prose requests did not.
