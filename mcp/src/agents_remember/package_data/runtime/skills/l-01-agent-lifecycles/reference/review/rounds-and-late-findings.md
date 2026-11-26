# Review rounds, record rechecks, and late findings

Reviewers, Managers, Workers and Curators read this file when a review reaches the settings round limit, needs a record-only recheck on an unchanged candidate, receives findings after sealing, or encounters protocol problems or forged states.

## Verify repaired findings at the round limit

Read the current review-state limit from the `task_doc` answer. Never run an unapproved round or split the scope to avoid that limit.

When review rounds are used up and the only remaining items were raised before and have since been repaired, the same Reviewer verifies exactly those items in the next round. Within the settings limit, this is an ordinary fix-verification round. Beyond the limit, the round needs the Architect's or developer's approval, which the Manager records. No new finding or item is covered.

A repair round for a Curator code finding or a failed final check counts under the settings limit. Beyond that limit, send the extra-round question to the instructed reporting recipient where your role has one; otherwise send it through your parent, or ask in your own chat when you have no parent. An extra round for advisory review findings is the Architect's grant. Every other new observation follows your role's developer-question route.

## Recheck a repaired Worker record

When all candidate findings are closed and only a finding on the Worker's record remains, check the repaired record on the last recorded round. The record may be its report, evidence table, hash or anchor.

Do not call `begin_review`; the round count stays unchanged. This applies only while the candidate is byte-for-byte the candidate that round recorded. If the candidate changes, `task_doc` refuses (`review-candidate-invalid` or `review-kind-invalid`) and names `begin_review` as the next step. Non-leaf seams retain their owner's recording act.

## Post-seal routes

Three routes may add a finding after the baseline list is sealed:

1. A valid code finding from the Curator. Answer directly to the Curator and Worker; see [Memory-tool failures, code concerns, and sync conflicts](../curator/exceptions-and-recovery.md#memory-tool-failures-code-concerns-and-sync-conflicts).
2. A valid finding from an advisory review the Architect commissioned and forwarded in its questions file.
3. A failed final check on the passed tree.

Record each sanctioned post-seal finding with `addedFindings` (identifier, description, route) through `task_doc` (`record_review`, or `record_route_review` on a routed leaf). Recording an added finding spends no round, and no route review attests a pass while an added finding remains open.

For all three routes, add a sealed finding with its own identifier and reopen only that point. Give the reason when rejecting an invalid finding. The Worker repairs an accepted finding in a successor. Send an unresolved disagreement or authority issue to the Manager.

## Report protocol problems and forged states

An unreviewable change set, including a missing diff or task documents, is a blocking finding.

Report these protocol refusals through the developer-question route:

- a whole-review request in `fix-verification`;
- an omitted, unknown, duplicate, rewritten, reintroduced or newly discovered identifier;
- a new criterion under an old identifier;
- a pass with unresolved identifiers;
- an outside-list observation.

Apart from the three sanctioned post-seal routes above, never add, reopen or reset these into the review.

Malformed Worker rows already handed over are formal attempts. Reject them independently in the applicable failure class (`implementation defect`, `evidence gap`, `requirement contradiction/overconstraint`, `test/tool defect`, or `external blocker`); never edit them. A successor arrives only at the Worker's next exact-candidate handoff.

Reject missing, unapproved or mismatched packet versions as invalid citations. Reject stale acceptance, and tell the Manager that the leaf needs a new brief for the newer approved version.

Reject a candidate that moves during review as stale, and require a successor Worker attempt and Reviewer record. A changed candidate, source, requirement version, model, seat, route or report label does not create a new first review. Return unverifiable changed scope to the developer.

States built by hand to defeat a check—forged or copied stamps, files rewound by Git while their history stays, edited histories, or clones the product never makes—get no finding and no guard.

## Omission repairs, whole-set findings, and memory changes

Close an omission finding by listing and checking every behavior introduced by the diff. For a finding about every member of a set, script a scan of the whole candidate and rerun it during verification. State the broken property itself: the case demonstrating the failure is its proof, not the finding itself. A review assumes honest agents: a finding also names the operation or honest step that reaches its starting state; without one it is no finding. Harder adversarial review requires a requirement or developer request.

During memory review, inspect the quality result's reports of cards with substantial text removed (`cutCards`) and files added outside the knowledge (`addedFiles`), reporting a finding when text was removed without a named change or when a file was added outside the knowledge without a place in the task's requirement. Also inspect `memory_quality_check` report-only findings for pre-base-stale dependent confirmations and every `deferral_lifted` row, reading each whole document against current code per `OM-6` in [onboarding-memory](../../criteria/onboarding-memory.md). In memory verdicts, sign each flagged pre-base-stale dependent confirmation and `deferral_lifted` row per `OM-6`, attach base and candidate `knowledge_read` (`view="dependent"`, `staleOnly=true`) receipts from `agents-remember-task`, and block if any lacks a holding whole-document check or confirms a deferred document unedited.

## Review stops, unfinished turns, and step marking

Stop for an open permission prompt, a standing refusal outside covered-write recovery, a question about what the requirement means, or an effect outside the assigned worktree, scratch folder, and task. If you have a parent and remain unfinished at turn end, tell that parent what and whom you await via `role_message`. Mark only your own completed step; if no step names your part, report that and mark nothing. Incomplete, blocked, progress and escalation reports never mark unfinished work done.
