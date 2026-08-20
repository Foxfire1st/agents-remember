# Criteria Catalog — Doctrine Review

The standing criteria an **adversarial reviewer** (`../roles/reviewer.md`) MUST run when the change
set under review is doctrine: skill files, role lifecycles, templates, instruction surfaces, docs
that agents obey. Binds whenever doctrine/skill files are in the change set (see the binding table
in `../roles/reviewer.md`). Criteria are never made up on the spot: the standing list below is the
regression floor, run every time; the exploratory mandate and the promotion ratchet keep the
catalog alive.

## Standing Criteria (MUST RUN — the regression floor)

### D-1 — Doctrine-vs-code anchoring

**Every "X enforces Y" needs a code anchor.** A doctrine sentence claiming enforcement, refusal,
or automatic behavior must resolve to the code that does it (file + mechanism). Doctrine that
narrates enforcement which does not exist is a defect, not an aspiration.

- Catching evidence: 260703-L8 review 1 (AR-5) — `requireReviewerVerdictAtSeams` was documented as
  binding while the flag was **inert**; the finding forced the wiring (L8 decision log, AR-1
  entry: "closing AR-5's inert flag").

### D-2 — Cross-file contradiction sweep

**Sweep sibling doctrine for statements the change set now contradicts.** A doctrine change is
reviewed against the whole doctrine surface (skills, role files, templates, docs, hooks, canvas
prose), not just the edited files — one file's new invariant is another file's stale sentence.

- Catching evidence: 260703-L10 — the landed "chat is never a build route" invariant contradicted
  surviving chat-build wording inside `c-09` / `w-02` (builder escalation 1, owner-sanctioned
  sweep; L10 builder report).

### D-3 — Stuck-state walk

**Can an agent following the prose deadlock?** Walk the doctrine as the obeying agent: at every
wait, gate, and handover, ask who unblocks it and through which channel. Prose in which two seats
each wait on the other — or a seat waits on a channel nobody is told to serve — is a blocking
finding.

- Catching evidence: 260703-L8 review 2 — the seam deadlock: the manager's blocking raise and the
  orchestrator's decide path could not meet as written. The ruled `wait=false` handover plus the
  structural master-document decision channel resolves it without exposing transport identity.

### D-4 — Topology and authority sweep

**Reject doctrine that treats every master as a Git integration branch or an integration branch as
a repair workbench.** The current topology has two explicit natures: organizational masters are
logical owners whose leaves land directly on super; atomic masters alone own an intermediate
branch and expose it once. Sweep roles, briefs, templates, review packets, and diagrams for the
retired fixed stack. Direct feature/fix commits on main, super, or atomic integration branches are
a blocking contradiction; narrowly scoped conflict resolution and plane-owned ref movement are
the only integration-branch writes.

### D-5 — Detection is not judgment

**Mechanistic facts must not smuggle policy decisions.** Code may report membership, graph shape,
derived waves, readiness, lineage, and route/seam evidence. Execution nature, dependency meaning,
priority, and material reprioritization require an explicit role judgment with durable rationale.
Stable tie-breaking must be named as tie-breaking, not described as a priority decision.

### D-6 — Source-comment requirement identifiers *(added 260815-DAG-L15)*

**Requirement identifiers in source comments are allowed when they name a durable product
requirement; provenance prose is not.** The repo carries `L<leaf>-R<n>` / `L<leaf>-S<n>`
identifiers at scale (83 comment lines across 22 modules at 260815-DAG-L15); the written Source
Comment Scope rule ("no task, leaf, decision-item, review, requirement, or audit identifiers in
source comments", memory `system/coding-guidelines.md` rule 1) and this practice contradict
(260815-DAG-L12 F3, 260815-DAG-L16 F4). The reconciled convention: a bounded
`L<leaf>-R<n>` / `L<leaf>-S<n>` identifier naming a durable leaf requirement or step is allowed
and is the preferred way to tie a comment to its contract. Task, chat, review, decision-item, or
report provenance and paths stay out of source comments — workflow history lives in Git history,
task artifacts, and onboarding, not comments.

## Exploratory Mandate

Beyond the standing list, the reviewer owes **novel lenses** (the brief sets N; default 2): attack
surfaces of THIS doctrine change the catalog does not name yet. Every novel finding-class that
survives refutation is proposed as a catalog amendment in the verdict.

## Promotion Ratchet

- A **candidate** criterion that catches a real defect in **≥2 separate engagements** is promoted
  into the standing list above, with its catching evidence cited — escaped bugs become permanent
  tests. Promotion is proposed in the verdict and lands on the loop owner's acceptance.
- A **standing** criterion that fires nothing for **N consecutive engagements** (default 5)
  demotes to spot-check.
- A criterion that can be **mechanized graduates out of the catalog into a gate** — the closeout
  body gate (which catches history-only "refreshes" at commit time, where prose requests did not)
  is the working example.
