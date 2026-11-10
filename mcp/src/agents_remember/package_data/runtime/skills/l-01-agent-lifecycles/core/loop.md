# Core — Owner, builder and Reviewer loop

This authoring reference defines how the owner, builder and independent Reviewer work together. The owner validates the deliverable and decides the gate; the builder builds; a distinct Reviewer supplies evidence, not gate acceptance. Role boundaries remain fixed, including when the developer chooses Architect direct coordination.

## Keep the roles distinct at each level

| Level | Owner: holds the deliverable, rules and lands | Builder | Reviewer |
| --- | --- | --- | --- |
| Leaf | Manager, or Architect under developer-chosen direct coordination | Worker | Distinct Reviewer on that leaf |
| Master | Manager | Leaf Workers | Master-exit seam Reviewer; its verdict belongs to `master-handover-approval` |

## Request independent review explicitly

Independent review runs when the task or owner requests it, under an explicit baseline or fix-verification mode and for the exact seam. Closeout creates no new review duty. Existing required review and curation evidence still precede the gate.

Independence requires another agent. The Reviewer is never the author or implementer. Every requirement verdict cites evidence of the requirement's class. Evidence of the wrong class cannot support a pass.

## Dispatch the agreed scope

When review is requested, the owning seat dispatches its scope, mode, exact candidate and requirement revision. The scope follows the agreed seam, not an invented score.

A baseline covers the complete agreed candidate diff, including unattributed changes.
Fix-verification covers only the sealed outstanding findings and their required evidence.
Owner validation of a report against an artifact is neither independent review nor permission to accept its own authorship.

Record the agreed seam and review authority on the task. Organizational leaf review and atomic integration review keep their distinct scopes. A change of label or coordinating level creates no new baseline and no new mandatory review.

## Count rounds under the current settings

A governed review has one thorough baseline followed by fix-verification rounds within the settings limit. `orchestration.loops.defaults.maxRounds` names the number of rounds allowed without approval.

Use the limit in the settings file when the round begins. A changed limit applies to the next round without a restart. The review state returned by `task_doc` carries the task's limit and its approved extra rounds. A pending round may resume; a new review counts as the next round.

Where the lifecycle uses follow-up verification, the same Reviewer verifies the residuals of a passing round. Fix rounds resume the same builder.

Fix-verification addresses only the original listed issues, subject to the two sanctioned routes below. Send every other matter outside that list to the developer; it does not become a successor finding.

## Preserve the original list through convergence

Every fix-verification round must shrink the open list or honestly retain it with fixed/unfixed dispositions. At the settings limit, or when another round is needed, request authorization as [Verify repaired findings at the round limit](../operations/review.md#verify-repaired-findings-at-the-round-limit) states.

Verifying repaired items is an ordinary fix-verification round. If the rounds are used up and the only remaining items were raised earlier and then repaired, the same Reviewer verifies exactly those items in the next round. This is not an exception to the authorization rule.

When no review state exists, the used count is zero and the next review is the baseline. Do not invent a pristine marker. The leaf Reviewer opens its round with `task_doc(operation="begin_review")` and records its result with `record_review` or `record_route_review`. Other seams keep their own recording owner.

The first review covers the complete agreed scope. It fixes the original issue list, statements, evidence and acceptance criteria for later rounds.

Mechanical tests and Worker diagnosis are evidence. They cannot publish Reviewer findings or reset review authority.

Changing the candidate, source, requirement version, model, seat, route or report label does not reset the list. The review refuses:

- Unknown, duplicate, rewritten, reintroduced, newly discovered or outside-list issues, except through the two sanctioned routes below.
- New criteria, routes or lenses.
- Whole-review requests.
- Passing with unresolved items.

This applies to native, plan, integration, route, lens, replacement, resumed, bootstrap, diagnostic, delta and final review labels alike.

## Handle the two sanctioned routes for added findings

Two routes may add a finding after the list is sealed:

- A valid code finding from the Curator.
- A valid finding from an advisory review the Architect commissioned and forwarded in its questions file.

For both, add it as a new sealed finding with its own identifier and reopen the verdict for that point only. Earlier sealed findings stay as they are. The Worker repairs a valid finding in a successor. For an invalid finding, give the reason it does not hold.

A repair round for a Curator finding counts under the settings limit. Beyond it, send the extra-round question to the instructed reporting recipient where your role has one; otherwise send it through your parent, or ask in your own chat when you have no parent. An extra round for advisory findings is the Architect's grant. Every other new observation follows your role's developer-question route.

## Use the catalog for the review mode

A baseline uses its review type's standing catalog from `../criteria/`: code-seam, doctrine, onboarding-memory, report-verification or plan-review. It also uses the required routes and exploratory mandate. For criterion promotion, demotion and automation, follow the shared [Promotion Ratchet](../criteria/code-seam.md#promotion-ratchet), which the selected catalog points to.

Fix-verification uses the sealed baseline and only the standing criteria and evidence needed for its listed identifiers. It has no exploratory mandate, whole-catalog rediscovery, new-lens duty or catalog-promotion authority.

The [Reviewer role](../roles/reviewer.md) binds the review mode, evidence packet and applicable catalogs.

## Resolve role settings without starting extra work

The harness, model and effort resolve through `orchestration.roles` and the applicable `orchestration.rolesPerLevel` override. `orchestration.loops` holds loop defaults and postures.

## Compile requirements before task topology

After intent and scope are established, the Architect compiles every independently falsifiable obligation into a canonical requirement index with a stable identifier and explicit version. Clauses that can be violated, reviewed, owned, evidenced or superseded independently become separate requirements.

For a first sprint only, the launcher creates the sprint document, a master and its first leaf before the Architect starts. They carry the developer's request and scope only: no requirement packets and no approved order. From the Architect's start on, packet approval governs them like any task document, and the Architect may reshape them through the usual task operations.

Except for those first-sprint scope documents, before creating any sprint, master or leaf task document, give every identifier and version one self-contained, version-addressed packet. Use the [requirement-packet template](../templates/requirement-packet.md) and include:

- Problem, required behavior and rationale.
- Scope, exclusions and preservation boundaries.
- Failure and recovery behavior.
- Examples and forbidden overreach.
- Expected evidence and authority/provenance.
- Dependencies and open factual gaps.

Use diagrams for material relationships of state, sequence, ownership and interaction.

Subject to the same first-sprint exception, present the complete requirement corpus for developer approval before laying out the tasks, and record that ruling. Put canonical identifier, version and packet links in tasks rather than rewriting the contracts.

A task owns one primary requirement packet. It may also own further approved packets of the same topic. Keep dependency and preservation context separate.

One developer request is one task; do not split it on your own. Propose a split, with its dependencies, to the developer.

A requirement change needs a new version, durable developer approval, invalidation of affected acceptance and a new brief to affected leaves. Reviews and cold reads provide advice; they never authorize adding a requirement.
