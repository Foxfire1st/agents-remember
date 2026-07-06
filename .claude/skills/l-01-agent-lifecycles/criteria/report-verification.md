# Criteria Catalog — Report Verification

The standing criteria an **adversarial reviewer** (`../roles/reviewer.md`) — and the loop OWNER
verifying a builder round — MUST run over every report, claim, and summary in the change set:
builder reports, owner claims, handover packets. **Standing from day one** in every review type:
report-vs-artifact caught real defects in three separate engagements before this catalog existed.
Criteria are never made up on the spot: the standing list below is the regression floor, run every
time; the exploratory mandate and the promotion ratchet keep the catalog alive.

## Standing Criteria (MUST RUN — the regression floor)

### RV-1 — Report-vs-artifact on EVERY claim

**Open the artifact behind every claim.** A report sentence asserting "X was done / is green /
was refreshed" is verified against the artifact itself, not trusted — claim by claim, no sampling
of the load-bearing ones.

- Catching evidence, three separate engagements (260703-L8): review 3 caught a **hand-aligned
  test** behind a wiring claim; cycle 6's closeout gate caught **"refreshed" overviews that were
  history-only**; review 4 caught the **OWNER's own canvas overclaim** (L8 decision log, cycle-7
  entry) — builder reports and owner claims fail the same way.

## Candidate Criteria (seeded exploratory — one catching engagement each; promote at ≥2)

Run under the exploratory mandate; a candidate is proposed for promotion into the standing list
when it catches in a second engagement (the ratchet below).

### RV-2 — CLASS-completeness *(candidate — 1 catch)*

**Hunt the siblings of every found instance.** A finding or a fix names an instance of a CLASS
(a directive surface, a census count, a vocabulary token). Enumerate the class — every sibling
surface, not just the instances the report names — and check each one.

- Catching evidence (single engagement): 260703-L10 round 1 — the reviewer's completeness sweep
  found **six of ten first-action surfaces** still shipping the retired directive when the report
  claimed the flip done (L10 decision log: "hunt siblings of the class, not just the named
  instances").

### RV-3 — Partial-fix-creates-falsehoods *(candidate — 1 catch)*

**Verify claims ABOUT the changed artifacts, not only the artifacts.** A partial fix can turn
previously-true sentences elsewhere false (docs describing the artifact, install guides quoting
it, counts referencing it). Sweep for statements about what changed and re-verify their truth
after the change.

- Catching evidence (single engagement): 260703-L10 round 1 — the partial hook flip made **two
  install-doc claims false** ("injects the same startup directive" no longer held); round 2
  restored byte-identity so the claims became true again (L10 decision log + builder report).

## Exploratory Mandate

Beyond the standing list, the reviewer owes **novel lenses** (the brief sets N; default 2): ways
THIS report could mislead that the catalog does not name yet. Every novel finding-class that
survives refutation is proposed as a catalog amendment in the verdict.

## Promotion Ratchet

- A **candidate** criterion that catches a real defect in **≥2 separate engagements** is promoted
  into the standing list above, with its catching evidence cited — escaped bugs become permanent
  tests (RV-1 is itself the precedent: promoted on three catches). Promotion is proposed in the
  verdict and lands on the loop owner's acceptance.
- A **standing** criterion that fires nothing for **N consecutive engagements** (default 5)
  demotes to spot-check.
- A criterion that can be **mechanized graduates out of the catalog into a gate** — the closeout
  body gate (which catches history-only "refreshes" at commit time, where prose requests did not)
  is the working example.
