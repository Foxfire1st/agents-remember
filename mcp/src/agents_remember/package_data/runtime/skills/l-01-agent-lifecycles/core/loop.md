# Core — The Three-Party Loop (one home — this file owns the loop doctrine)

The owner validates the deliverable and decides the gate; the builder builds, and a distinct Reviewer supplies evidence rather than gate acceptance. Role boundaries remain fixed, including developer-chosen Architect direct coordination.

| Level | Owner (holds the deliverable, rules, lands) | Builder | Reviewer |
| --- | --- | --- | --- |
| Leaf | Manager, or Architect under developer-chosen direct coordination | Worker | distinct Reviewer on that leaf |
| Master | the manager | the leaf workers | the master-exit seam reviewer (verdict rides `master-handover-approval`) |
| Portfolio plan | the architect | strategist when approved; orchestrator on a sanctioned strategist skip | reviewer with the plan-review catalog |

## Review is opt-in and explicit

Independent review runs when the task or owner requests it, under an explicit baseline or fix-verification mode and the exact seam. Closeout creates no new review duty; existing required review and curation evidence still precede the gate.

Independence requires **another agent**, never builder self-review: the reviewer seat is never the
author/implementer seat. Every requirement verdict must cite evidence of the requirement's class;
evidence of the wrong class is verdict laundering, not a pass.

## Complexity-scored tiers (per leaf, at dispatch, when review is requested)

The owning seat dispatches the review scope, mode, exact candidate and requirement revision; review scope follows the agreed seam rather than an invented score.

- A baseline covers the complete agreed candidate diff, including unattributed changes.
- Owner report-versus-artifact validation is not independent review or permission to accept its own authorship.
- Fix-verification covers only the sealed outstanding findings and their required evidence.

Record the agreed seam and review authority on the task. Organizational leaf review and atomic integration review keep their distinct scopes; changing a label or the coordinating level creates no new baseline and no new mandatory review.

## Rounds and convergence

- A governed review runs one thorough baseline and fix-verification rounds within **the
  settings limit**: the number of rounds without an approval that
  `orchestration.loops.defaults.maxRounds` names. The limit in force is the one the settings
  file names when the round is begun; a changed number holds for the next round without a
  restart. The review state that `task_doc` answers carries the limit for the task, with its
  approved extra rounds. A pending round may resume; a new review counts the next round.
- Residuals of a passing round are verified by the same reviewer where the lifecycle uses follow-up
  verification; fix rounds resume the same builder.
- Fix-verification rounds verify **only** the original listed issues. Any outside-list matter goes to the
  developer and is not a successor finding.
- **The convergence rule.** Every fix-verification round must shrink or honestly retain the listed
  open set with fixed/unfixed dispositions. At the settings limit, or when a further round is needed, request authorization as `../operations/review.md` states. Verifying repaired items is an ordinary fix-verification round: when a review's rounds are used up and the only items left were raised before and have since been repaired, the same reviewer verifies exactly those items in the next round, not as an exception.

With no review state, the used count is zero and the next review is baseline; invent no pristine marker. The leaf Reviewer opens its round with `task_doc(operation="begin_review")` and records its result with `record_review` or `record_route_review`; other seams retain their owner recording. The first review covers the complete agreed scope and fixes its original issue list, statements, evidence and acceptance criteria.

A changed candidate, source, requirement version, model, seat, route, or report label does **not**
reset the list. Unknown, duplicate, rewritten, reintroduced, newly discovered, or outside-list
issues, new criteria, new routes/lenses, whole-review requests, and passing with unresolved items
are refused. This rule applies equally to native, plan, integration, route, lens,
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

Present the complete requirement corpus for developer approval before task topology; record that ruling and project canonical ID/version/packet links into tasks rather than rewriting their contracts. A leaf owns one primary revision, with adjacent context preserved separately. A requirement change needs a new version, durable developer approval, invalidation of affected acceptance and a rebrief of affected leaves. Reviews and cold reads are advice, not authority to add a requirement.
