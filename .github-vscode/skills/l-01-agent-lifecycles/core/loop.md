# Core — The Three-Party Loop (one home — this file owns the loop doctrine)

**OWNER → BUILDER → REVIEWER → owner, at every level that owns work.** The owner never
self-approves; the builder never lands; the reviewer never decides — **verdicts are evidence**. The
owner checks the verdict and either redispatches a builder or escalates. Role files reference this
file; they do not restate it.

| Level | Owner (holds the deliverable, rules, lands) | Builder | Reviewer |
| --- | --- | --- | --- |
| Leaf | the leaf's owning seat (manager; architect in tight/flat mode) | spawned worker (no-commit contract) | spawned reviewer, criteria catalog + liberty |
| Master | the manager | the leaf workers | the master-exit seam reviewer (verdict rides `master-handover-approval`) |
| Portfolio plan | the architect | strategist when approved; orchestrator on a sanctioned strategist skip | reviewer with the plan-review catalog |

## Review is opt-in and explicit

Run independent route review **only** when the developer or the approved task/role brief requests
it; closeout and integration never require or launch it. Every review is dispatched with an explicit
mode — `reviewMode=baseline` or `reviewMode=fix-verification` — and the full mode contract lives in
`operations/review.md`.

Independence requires **another agent**, never builder self-review: the reviewer seat is never the
author/implementer seat. Every requirement verdict must cite evidence of the requirement's class;
evidence of the wrong class is verdict laundering, not a pass.

## Complexity-scored tiers (per leaf, at dispatch, when review is requested)

The owning seat scores three axes — blast radius (doctrine/enforcement/public surface vs
leaf-local) · novelty (new subsystem vs pattern-following) · size (files × steps) — into three
tiers:

- **direct** — ordinary build channel plus the requested independent route review; no additional
  loop machinery.
- **builder-verified** — builder implements; owner additionally verifies report-vs-artifact; the
  requested route review still runs.
- **full loop** — builder + independent reviewer rounds, with the route partition as the review
  scope floor where the leaf owns that seam.

The strategist's blast-radius register is the scoring input when an orchestration task exists. A
leaf's loop mark (tier + scope: manager | orchestrator — the owning level runs the loop with ITS
agent set) is recorded on the leaf doc with a decision-log entry. A master whose leaves all score
`direct` avoids iterative full-loop machinery. Atomic leaves still defer a requested route review to
the one master integration seam; standalone and organizational leaves retain their requested review
scope. **The knobs tune review depth and round machinery; they do not create a review when none was
requested.**

## Rounds and convergence

- A governed review has at most **three rounds**: one thorough baseline and two fix-verification
  rounds. A pending round may resume; a new review counts the next round.
- Residuals of a passing round are verified by the same reviewer where the lifecycle uses follow-up
  verification; fix rounds resume the same builder.
- Reviews 2 and 3 verify **only** the original listed issues. Any outside-list matter goes to the
  developer and is not a successor finding.
- **The convergence rule.** Every fix-verification round must shrink or honestly retain the listed
  open set with fixed/unfixed dispositions. At three rounds, or when a further round is needed, ask
  the developer directly and wait for explicit authorization; record that instruction before any
  authorized extra work. **Do not spin an unapproved round, split the scope, or create a new finding
  list.** No agent may self-authorize an extra round, and no code path needs to prove human
  authorship or build a separate authentication mechanism.

The simple review rule: if no review state is present, the used-round count is zero and the next
review is a baseline; do not create a pristine marker or refuse because legacy review history is
absent. Before reviewer work the owner calls `task_doc(operation="begin_review")`; after the result
the owner records it with `task_doc(operation="record_review")` or the existing
`task_doc(operation="record_route_review")`. Review 1 is thorough — inspect the entire agreed scope,
applicable criteria, required routes, and lenses, then record the fixed original issue list with
precise statements, evidence, and fix-acceptance criteria.

A changed candidate, source, requirement version, model, seat, route, or report label does **not**
reset the list. Unknown, duplicate, rewritten, reintroduced, newly discovered, or outside-list
issues, new criteria, new routes/lenses, whole-review requests, and passing with unresolved items
are refused. This rule applies equally to native, hosted, plan, integration, route, lens,
replacement, resumed, bootstrap, diagnostic, delta, and final review labels.

Mechanical tests and worker diagnosis are evidence and may not publish reviewer findings or reset
review authority.

## Criteria catalogs (the reviewer as test bench)

The baseline review runs its type's standing catalog from `../criteria/` (code-seam · doctrine ·
onboarding-memory · report-verification · plan-review), the required routes, and the exploratory
mandate, under the promotion ratchet. Fix-verification uses the sealed baseline and only the
standing criteria/evidence needed to verify its listed IDs; it has no exploratory mandate,
whole-catalog rediscovery, new-lens duty, or catalog-promotion authority. `roles/reviewer.md` binds
the review mode, the evidence packet, and which catalogs apply.

## Per-level agent sets

Each level runs its loop with its own harness/model/effort set — the orchestrator-level set (the
strongest models) and the manager-level set (cheaper, possibly workflow-free) — configured per level
in the `orchestration.loops` settings block. The architect proposes a strategist pre-run, and it
occurs only after developer approval; settings cannot auto-run it.

## Requirement compilation precedes task topology

After intent and scope are established, the architect compiles every independently falsifiable
obligation into a canonical requirement index with a stable ID and explicit version. Clauses that
can be violated, reviewed, owned, evidenced, or superseded independently are separate requirements.

Before any sprint/master/leaf task document is created, every ID + version has one self-contained,
version-addressed packet using `../../w-02-light-task-workflow/requirement-packet-template.md`,
including the problem, required behavior, rationale, scope and exclusions, preservation boundaries,
failure/recovery behavior, examples, forbidden overreach, expected evidence, authority/provenance,
dependencies, and open truth gaps. Material state, sequence, ownership, and interaction
relationships get diagrams.

A fresh agent cold-reads each packet without the planning transcript and must be able to explain
what changes, what stays unchanged, the important failure states, and proof of conformance. The
architect presents the complete corpus for developer approval and creates task topology only after
that approval; every approved packet records the durable ruling. Masters and leaves carry filtered
ID + version + canonical-packet links, never rewritten requirement contracts. Each leaf owns exactly
one primary requirement revision; several leaves may implement independently executable
manifestations of one revision, while adjacent requirements are dependency/preservation context
only. A requirement change increments its version, cites durable developer approval, invalidates
affected acceptance state, and rebriefs affected leaves.
