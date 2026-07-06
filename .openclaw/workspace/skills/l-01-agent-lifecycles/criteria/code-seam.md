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
