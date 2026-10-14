# Criteria Catalog — Onboarding/Memory Review

The standing criteria an **adversarial reviewer** (`../roles/reviewer.md`) MUST run over the
memory side of a change set: sidecars, route overviews, route indexes, update histories. Binds at
the master-exit and super-exit seams (the onboarding-vs-code lens) and in loop reviews whose change
set carries onboarding (see the binding table in `../roles/reviewer.md`). Criteria are never made
up on the spot. A baseline review runs the standing list below as its regression floor and may use the
exploratory mandate and promotion ratchet. A fix-verification uses only the sealed baseline
evidence needed for its outstanding IDs; it does not recensus this catalog or add a criterion.

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

### OM-2 — Independent fact-check of curated knowledge (second reader) *(requirement-bound: MIK-R53 rule 5)*

**A seat that wrote none of the leaf's cards or records checks the curation before closeout.**
On converted memory every changed card, sidecar and record is checked statement by statement
against the leaf's final code; every history row's disposition, effect and reason is checked
against the code diff; and the check says whether anything important about the change is left
unsaid. Blocking findings are repaired before the closeout. This criterion replaces the Update
History criteria: converted cards carry no Update History, and a counted card edit alone never
closes a trace — only a content-bound curator row does (MIK-R53 rules 1–3). Binds converted
memory only; this criterion entered by the requirement, not by the ratchet, and is demoted only
by a developer ruling.

### OM-4 — Admission justifications are plausible *(requirement-bound: MIK-R27 rule 5, developer ruling D14)*

**Each new invariant, family and decision record's admission justification must be plausible for the
criterion it claims.** The validator checks only presence, shape, a justification made only of task,
leaf, requirement or ruling references, commit hashes and provenance words, and the two checkable
criteria (`spans_locations`, `guarded_by_test`). Judge the rest: a `prevents_costly_mistake` that
names no plausible, costly error; a `family_guarantee` the family's guarantee does not need; a
`joint_guarantee` that one member already promises alone; a `real_alternatives` whose rejected
option was never serious; a record that restates one leaf's acceptance criteria, or that a code
change alone motivates. Such a statement stays prose in the onboarding Markdown. Binds converted
memory only (the text format, MIK-R21); this criterion entered by the requirement, not by the
ratchet, and is demoted only by a developer ruling.

## Candidate Criteria (seeded exploratory — one catching engagement; promote at ≥2)

Run under the exploratory mandate during a baseline review only; a candidate is proposed for
promotion into the standing list when it catches in a second engagement (the ratchet below).

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

During a baseline review, beyond the standing list, the reviewer owes **novel lenses** (the brief sets
N; default 2): attack surfaces of THIS memory delta the catalog does not name yet. Every novel
finding-class that survives refutation is proposed as a catalog amendment in the verdict. A
Fix-verification has no novel-lens or new-catalog duty and cannot turn an observation outside
the sealed issue list into a finding.

## Promotion Ratchet

- A **candidate** criterion that catches a real defect in **≥2 separate first-review engagements** is promoted
  into the standing list above, with its catching evidence cited — escaped bugs become permanent
  tests. Promotion is proposed in the verdict and lands on the loop owner's acceptance.
- A **standing** criterion that fires nothing for **N consecutive engagements** (default 5)
  demotes to spot-check.
- A criterion that can be **mechanized graduates out of the catalog into a scoped diagnostic** — the
  curator handoff is the working example, and the former OM-2 (history-only "refresh" detection,
  replaced by the MIK-R53 second-reader criterion above) mechanized exactly so: the gate catches
  unreviewed refreshes at commit time, where prose requests did not.
