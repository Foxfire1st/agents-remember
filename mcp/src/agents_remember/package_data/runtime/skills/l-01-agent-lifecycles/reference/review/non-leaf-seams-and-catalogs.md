# Non-leaf review seams and criteria catalogs

Reviewers and coordinating roles read this file to bind criteria catalogs for any review seam and to run master-exit, atomic-master integration, portfolio plan and super-exit reviews.

## Review seams and criteria catalogs

Review the candidate identity supplied in your intake, never the hosting leaf's enclosure candidate.

| Seam | Binds | Start leaf | Verdict recipient |
| --- | --- | --- | --- |
| organizational leaf route review | the leaf | reviewed leaf | Worker; code PASS also to Curator; memory verdict to Curator |
| atomic-master integration review | the canonical master | master's acceptance leaf | integration owner |
| master-exit | the master | master's acceptance leaf | that Manager |
| portfolio plan review | the sprint | none | Architect |
| super-exit | the sprint | none | Orchestrator |

Bind criteria catalogs solely from the review type's row below. A brief that names catalogs does not change them, and the Reviewer chooses none during the review. In the verdict's Catalog Binding section, record the review type, the catalogs assigned by the table, and for each conditional catalog whether it is bound and why, naming the changed surfaces that decide it.

| Review type | Catalogs |
| --- | --- |
| master-exit | [code-seam](../../criteria/code-seam.md), [onboarding-memory](../../criteria/onboarding-memory.md), [report-verification](../../criteria/report-verification.md); also [doctrine](../../criteria/doctrine.md) when doctrine, skills or documentation are included |
| super-exit | [code-seam](../../criteria/code-seam.md), [doctrine](../../criteria/doctrine.md), [onboarding-memory](../../criteria/onboarding-memory.md), [report-verification](../../criteria/report-verification.md), all in full |
| leaf code-change review | [code-seam](../../criteria/code-seam.md), [report-verification](../../criteria/report-verification.md); also [doctrine](../../criteria/doctrine.md) or [onboarding-memory](../../criteria/onboarding-memory.md) when those surfaces are included |
| atomic-master integration review | [code-seam](../../criteria/code-seam.md), [report-verification](../../criteria/report-verification.md), [onboarding-memory](../../criteria/onboarding-memory.md); also [doctrine](../../criteria/doctrine.md) when lifecycle instructions are included |
| leaf full-loop review | [report-verification](../../criteria/report-verification.md), plus [code-seam](../../criteria/code-seam.md) and/or [doctrine](../../criteria/doctrine.md) for the change set; also [onboarding-memory](../../criteria/onboarding-memory.md) when onboarding is included |
| plan review | [plan-review](../../criteria/plan-review.md), [report-verification](../../criteria/report-verification.md) |

## Master, atomic-master, plan, and portfolio exit reviews

- **Master-exit review**: Before the Manager hands over to the Orchestrator, review the accumulated master change set. Inspect execution nature, the exact organizational candidate or atomic branch diff, nature-appropriate commit and leaf references, master and leaf task documents, Worker turn reports and decision logs. Also inspect the draft master-handover packet, changed paths and sidecars, route overviews, code and memory ancestry and ledger state. Include carry-over only when recovery used it. Account for every master requirement, leaf, substep and accepted small gap filled during the work. Completion against task documents is an affirmative duty, not a formatting check. Skipped or reshaped work needs a decision-log trail; a packet that reads as complete while silently omitting unfinished leaf work, a substep or an accepted gap-fill carries a blocking finding. Account for every item in the verdict as `addressed`, `delta justified` or `MISSING` with its evidence anchor; do not sample load-bearing items.
- **Atomic-master integration review**: Bind the canonical master on the master's acceptance leaf and inspect the isolated branch diff against its base using the catalogs in the table above. Return the verdict to the integration owner.
- **Portfolio plan review**: Bind the sprint document with no start leaf, apply `plan-review` and `report-verification`, and return the verdict to the Architect. Because plan review and super-exit review have no startable Reviewer seat, the Orchestrator brings either review to the Architect.
- **Super-exit review**: Before the Orchestrator hands over to the Architect, review the whole portfolio's integrated behavior against the accepted objective. Inspect the super integration branch diff against its base, portfolio and master task documents, prior master-exit verdicts, Orchestrator decision logs, changed paths and sidecars, route overviews, and final carry-over and ledger evidence. Surface cross-master conflicts, duplicate implementations and deferred follow-ups.
- **Non-leaf blocks and recording**: At master-exit or super-exit, send a baseline `BLOCK` to the owning Manager (or to the Orchestrator at super-exit) as decomposable fix leaves, each naming scope, target files or documents, evidence and done-when. A master-exit block without an applicable listed repair is invalid. A fix-verification `BLOCK` returns only sealed outstanding identifiers to the existing Worker and creates no new fix leaf. Integration branches are not repair workbenches. Non-leaf `begin_review` and `record_review` entries remain the seam owner's acts, and non-leaf verdicts retain their seam's transport and recipient.
