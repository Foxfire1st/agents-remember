# Criteria Catalog — Code-Seam Review

The standing criteria an **adversarial reviewer** (`../roles/reviewer.md`) MUST run when the change
set under review wires code seams: consumers, enforcement points, gates, addresses, tool contracts.
Binds at the master-exit and super-exit seams and in any loop review whose change set touches code
(see the binding table in `../roles/reviewer.md`). Criteria are never made up on the spot: the
standing list below is the regression floor, run every time; the exploratory mandate and the
promotion ratchet keep the catalog alive.

## Standing Criteria (MUST RUN — the regression floor)

### CS-1 — Production-wiring walk

**Trace the real call path, never a hand-aligned harness.** A claim that a consumer or an
enforcement point exists must be walked through the PRODUCTION call path — the controller, the
tool registration, the store fold — not through a test that hand-assembles the pieces into
alignment. A test can pass while the production consumer is inert.

- Catching evidence: 260703-L8 review 3 (AR3-1) — the integrate consumer was proven **inert**: the
  policy was omitted at the controller and the gate was looked up on the wrong lifecycle, while a
  hand-aligned test passed (L8 decision log, cycle-6 entry).

### CS-2 — Fail-open hunt

**What happens when the address/config is absent or mistyped?** For every identity, address, key,
or config the seam matches on: feed it nothing, feed it a near-miss, and confirm the seam fails
CLOSED (refuses) rather than open (silently proceeds unmatched).

- Catching evidence: 260703-L8 review 4 (AR4-1) — the seam's `enclosure` address had to be pinned
  as an exact-string contract and the enclosure-less raise made a refusal (L8 decision log,
  cycle-7 entry).

### CS-3 — Validate-then-mutate

**Every raise/write validates before it mutates.** A call that records state (a gate raise, a
store append, a contract write) must perform its refusal checks before any durable effect; a
mutation that precedes validation leaves half-states behind on refusal.

- Catching evidence: 260703-L8 cycle 6 — the `wait=false` raise was reworked to be
  validate-then-mutate and seam-kind-restricted (L8 decision log, cycle-6 entry).

## Candidate Criteria (seeded exploratory — one catching engagement each; promote at ≥2)

Run under the exploratory mandate; a candidate is proposed for promotion into the standing list
when it catches in a second engagement (the ratchet below).

### CS-4 — Reused-primitive affordance parity *(candidate — 1 catch)*

**When a screen reuses a shared content primitive, verify every branch of the ORIGINAL's
affordances (banners, status chips, truncation notices, error states) still fires on the reuse
path.** A dropped affordance is a silent regression the reuse's own tests won't catch — they
assert the new screen's happy paths, not the primitive's full surface.

- Catching evidence (single engagement): 260703-L17 review (L17R-2) — the notes reader's
  `DualPane` reuse drops the "first 2 MiB" truncation banner on the markdown path (the banner
  lives in `CodeSide`, which markdown never reaches); text and binary keep it.

### CS-5 — Cross-repo side-effect safety *(candidate — seeded from a clean exemplar, 0 catches)*

**Any step that writes to a repository OTHER than the one it operates in gets the full
validate-then-mutate treatment plus partial-failure and dirty-target analysis.** Check the order
of validation vs the foreign write, the state left behind when the write lands but the caller
fails afterward, behavior against a dirty target repo, and an exact format round-trip with the
foreign artifact's consumer.

- Seeding evidence: 260703-L18 finding 7 (`_reconcile_missing_mapping` writing the official
  memory repo's ledger mid-`worktree_start`) PASSED all four lenses under this analysis — the
  clean exemplar that defined the class. A catch in a later engagement promotes.

## Exploratory Mandate

Beyond the standing list, the reviewer owes **novel lenses** (the brief sets N; default 2): attack
surfaces of THIS change set the catalog does not name yet. Every novel finding-class that survives
refutation is proposed as a catalog amendment in the verdict.

## Promotion Ratchet

- A **candidate** criterion that catches a real defect in **≥2 separate engagements** is promoted
  into the standing list above, with its catching evidence cited — escaped bugs become permanent
  tests. Promotion is proposed in the verdict and lands on the loop owner's acceptance.
- A **standing** criterion that fires nothing for **N consecutive engagements** (default 5)
  demotes to spot-check.
- A criterion that can be **mechanized graduates out of the catalog into a gate** — the closeout
  body gate (which catches history-only "refreshes" at commit time, where prose requests did not)
  is the working example.
