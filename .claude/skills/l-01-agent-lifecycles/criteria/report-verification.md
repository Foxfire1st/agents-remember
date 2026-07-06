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

### RV-2 — CLASS-completeness *(promoted to standing at 260703-L18 — 2 catches)*

**Hunt the siblings of every found instance.** A finding or a fix names an instance of a CLASS
(a directive surface, a census count, a vocabulary token). Enumerate the class — every sibling
surface, not just the instances the report names — and check each one.

- Catching evidence: (1) 260703-L10 round 1 — the reviewer's completeness sweep found **six of
  ten first-action surfaces** still shipping the retired directive when the report claimed the
  flip done (L10 decision log). (2) 260703-L18 review (L18R-3) — the fix for
  advertised-but-unimplemented recovery choices named the `cli.py` `custom` residual but missed
  the SIBLING `_load_memory_ledger` block still advertising an inert `reconciliation`; the
  sibling hunt found it.

### RV-4 — Decision-log completeness for scope-expanding disclosures *(promoted to standing at 260703-L18 — 2 catches)*

**A scope-expanding owner supplement or load-bearing builder disclosure must be verifiable in
the task-doc decision log, not only the builder report.** Continuity lives in the task_doc +
durable artifacts, never in transcripts; a ruling or environment finding that exists only in a
report is one lost file away from unrecoverable. Cross-check every supplement and disclosure the
builder report carries against the decision log.

- Catching evidence: (1) 260703-L17 review (L17R-1) — supplement 2 (the wait-loop-era remnant
  sweep with per-hit verdicts and two named follow-ups) was recorded only in the builder report.
  (2) 260703-L18 review (L18R-4) — the load-bearing editable-install/PYTHONPATH environment
  finding + named follow-ups lived only in the builder report; the leaf's `decisions[]` was
  empty. Both folded by the owner at closeout.

## Candidate Criteria (seeded exploratory — one catching engagement each; promote at ≥2)

Run under the exploratory mandate; a candidate is proposed for promotion into the standing list
when it catches in a second engagement (the ratchet below).

### RV-3 — Partial-fix-creates-falsehoods *(candidate — 1 catch)*

**Verify claims ABOUT the changed artifacts, not only the artifacts.** A partial fix can turn
previously-true sentences elsewhere false (docs describing the artifact, install guides quoting
it, counts referencing it). Sweep for statements about what changed and re-verify their truth
after the change.

- Catching evidence (single engagement): 260703-L10 round 1 — the partial hook flip made **two
  install-doc claims false** ("injects the same startup directive" no longer held); round 2
  restored byte-identity so the claims became true again (L10 decision log + builder report).

### RV-5 — Worktree-shadowed regression pins *(candidate — 1 catch)*

**A regression test must bite under the invocation the owner actually runs.** An editable
install pointing at another checkout can shadow the worktree's sources, making a mutation-tested
pin pass vacuously under the canonical invocation while failing only under a hand-set
`PYTHONPATH`. Verify sensitivity by mutating the target and running the CANONICAL invocation;
a pin that only bites under a nonstandard environment is not yet a pin.

- Catching evidence (single engagement): 260703-L18 review (L18R-1) — the inbox
  delivered/unconfirmed pin failed its mutation check only under `PYTHONPATH=src`; under the
  canonical `pytest mcp/tests` the main-repo editable install shadowed the worktree and the
  mutated code still passed. Remedy: the `sys.path` pin idiom the sibling suites carry.

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
