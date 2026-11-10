<!-- ar:roles all -->
# Operation — Review

Reviewing roles use this operation for independent review requested by the task or owner. Its inputs are the exact requirement revision, candidate and base, review mode, criteria and report path.

A first cold requirement read has no code candidate or Worker attempt and opens no code round. Once the Worker's freeze arrives, report missing candidate identity or review authority rather than guessing it.

Beside the leaf's Reviewer, the Architect may commission advisory Investigators on other providers. Each stays separate and reports only to the Architect. The Reviewer's verdict remains the grade.

<!-- ar:roles architect -->
## Advisory independent reviews

### Commission a separate view

An advisory review of a leaf's freeze is an exception to normal routing: the Architect commissions it and carries the findings to the Reviewer. The Manager relays no findings.

The Architect may add advisory reviews when the developer asks or when it judges a second view worth its cost. Any number is allowed, each on any provider an installed harness reaches.

Start each advisory Investigator through `role_start` on `agents-remember-task` without a task reference. It holds no leaf seat; the leaf still has one Reviewer seat.

### Give each Investigator the same material

Provide the freeze, packets, rulings, design source and Worker evidence supplied to the Reviewer. Give each advisory Investigator its own output folder, clean copy for anything it runs, and ports.

No advisory reviewer reads the Reviewer's or Curator's files, messages to or from them or the Manager, or another advisory reviewer's folders. Each reports only to the Architect.

The Investigator's capsule does not include this operation, so its brief must carry these rules. Name nothing the Architect or developer has already noticed; preserve the comparison's independence.

### Compare and route the findings

An advisory report changes no verdict by itself; the Reviewer's verdict remains the grade the product reads. Once all reports arrive, compare findings made by all, some and one reviewer.

Send the Reviewer one questions file containing each finding it did not make. Tell the Manager once for the gate and the round.

- The questions file asks the Reviewer to rule on each as on a Curator's code finding: take it into its findings and reopen the verdict for that point, or say why it does not hold. The file states this duty because Reviewer and Manager do not read this Architect-only section.
- The Manager's message states that the leaf's next step waits for the Reviewer's rulings and whether the Architect grants an extra round. An extra round for these findings is the Architect's grant.
- The Reviewer returns its rulings to the Architect. An accepted finding goes to the Worker as usual. The Architect's pre-landing check confirms that every advisory finding has a ruling.
- The Architect and developer settle disagreements by discussing the findings.

### Account for costs and permissions

Each advisory reviewer uses its provider's quota and the machine's memory. Investigator launch choices use admitted `orchestration.roles` settings and applicable `orchestration.rolesPerLevel` overrides.

The Architect sets the mode or runs an approver only within sufficient seat permissions. Before responding to an advisory reviewer's permission prompt, read [Formal permissions](orientation.md#formal-permissions) for the once-only developer notice and existing parent route.

<!-- ar:roles all -->

## Outputs

Write one concise pass/block recommendation with changed-file coverage, findings, checks and limitations. For every finding, give a stable identifier, location, evidence, impact and required repair or proof.

A baseline covers the complete candidate diff, including all changed and unattributed files. Check each requirement against its evidence class and relevant behavior. A test result is evidence, not semantic acceptance.

Fix-verification checks only the sealed outstanding findings against the same requirement and candidate lineage. Report a new contradiction or scope issue separately.

<!-- ar:roles reviewer -->
## Select focused tests

In a baseline review, rerun the Worker's recorded focused-test list and repeat its search on the frozen candidate; a test file the search finds that the Worker did not run is a finding. In fix-verification, apply the search to the repair's own touched names; an unrun matching test keeps the repaired finding open and is not a new finding.

<!-- ar:gives producer-list to curator: list-file, freeze-identity, handoff-check-result -->
## Receive the intake and hand over the leaf verdict

If the opening names no review mode, ask your starter for its first `role_message` on `agents-remember-task` and wait. A Reviewer started from the dashboard has no starter and asks the developer in its own chat instead. Inspect nothing and infer no mode before the answer arrives.

For a leaf:

1. On code `PASS`, first tell the Manager to start the Curator, before peer handover.
2. Send the verdict directly to the Worker through `role_message` on `agents-remember-task`. The message names the verdict file; it never pastes the text. A block names the sealed-findings report and requests the whole repair stretch.
3. Once the Curator exists, send it the passed freeze and code-`PASS` verdict directly.
4. Review the Curator's memory change and return findings or `PASS` directly to it.

A separately assigned packet cold read remains possible without an early normal leaf Reviewer.

Every handover names the leaf, exact object, report or verdict path, and whole next stretch. Identify a code candidate by the head and change hash in the Worker's report, or identify the exact memory change. Use the sibling role and exact leaf task references from the handover, preserving product-supplied identifiers whole and unchanged.

The Worker's message names the freeze and report. Its factual handover neither briefs nor limits the review. The Reviewer does not read the Worker's chat.

For a cleanly synced leaf, expect no new freeze and open no round. When no path changed on both sides, the Worker reruns focused tests and the Manager's closeout preview decides. If a path changed on both sides or a focused test fails, the Worker sends exactly that repair for one look.

When a freeze arrives, compare every finding with its entry in the Worker's freeze report. Return a freeze that lacks the entries without counting a round.

Before sending your hand-off list, run `handoff-check` against that freeze with the serving build's interpreter, identified by `server_info` on `agents-remember-task`. Name the result.
<!-- ar:roles all -->

## Record code review rounds

The Reviewer opens each ordinary leaf code round with `task_doc(operation="begin_review")` and records its own verdict through `record_review` or the applicable `record_route_review`. Include any preceding result required before the next begin. Non-leaf seams retain owner recording and their seam's verdict recipient.

<!-- ar:roles reviewer -->
## Recheck a repaired Worker record

When all candidate findings are closed and only a finding on the Worker's record remains, check the repaired record on the last recorded round. The record may be its report, evidence table, hash or anchor.

Do not call `begin_review`; the count stays. This applies only while the candidate is byte-for-byte the candidate that round recorded. If it changes, the tool refuses and names `begin_review` as the next step.
<!-- ar:roles all -->

## Verify repaired findings at the round limit

When review rounds are used up and the only remaining items were raised before and have since been repaired, the same Reviewer verifies exactly those items in the next round.

Within the settings limit, this is an ordinary fix-verification round. Beyond the limit, the round needs the Architect's or developer's approval, which the Manager records. No new finding or item is covered.

## Messages

Before filling or reading message fields, read [Message files and receipts](coordination.md#message-files-and-receipts) for its body, attachments, size limit, file line and return address.

Before locating a filed message or receipt, read [Find filed messages and receipts](../reference/message-handover.md#find-filed-messages-and-receipts) for the message folder and receipt scope to search.

### Handle delivery receipts

Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.

## Boundaries

Before a code edit, task-acceptance write, role start or Git publication, read [Permitted work and boundaries](../roles/reviewer.md#permitted-work-and-boundaries) for the Reviewer's standing restrictions. The Manager retains gate acceptance and records the Architect's or developer's extra-round approval.

Memory review uses separate sealed reports, finding identifiers and its own pass count. The Reviewer records its own lane with `begin_review` and `record_review`, using `reviewKind=memory`. Code state stays unchanged. Memory review opens no code review-state round, resets no passed code review, code verdict or code baseline, and adds no identifiers to that baseline. Its ordinary pass limit is the same as code review's. A pass beyond the settings limit needs the Architect's or developer's instruction recorded by the Manager.

After both code and memory return `PASS`, tell the Manager once that the leaf is ready for closeout, naming the passed freeze and both verdicts.

Besides the code-`PASS` start notice and ready-for-closeout notice, Manager notices concern only authority, ownership, recovery, beyond-limit work or Curator code disagreement. The Manager relays no leaf contents. Developer questions follow the role's developer-question rule; candidate questions go directly to the Worker and memory questions to the Curator.

<!-- ar:roles reviewer -->
Before answering a Curator code finding, read [Curation failure handling](curation.md#failure-handling) for the Curator's evidenced concern and the Reviewer's recorded finding or explained rejection. Send your answer directly to the Curator and Worker.

Two routes may add a finding after the list is sealed:

- a valid code finding from the Curator;
- a valid finding from an advisory review the Architect commissioned and forwarded in its questions file.

For both routes, add a new sealed finding with its own identifier and reopen the verdict for that point only. Earlier sealed findings stay as they are. Give the reason for an invalid finding. The Worker repairs an accepted finding in a successor.

A repair round for a Curator finding counts under the settings limit. Beyond it, send the extra-round question to the instructed reporting recipient where your role has one; otherwise send it through your parent, or ask in your own chat when you have no parent. An extra round for advisory findings is the Architect's grant. Every other new observation follows your role's developer-question route.

Send an unresolved disagreement or authority issue to the Manager.
<!-- ar:roles all -->

Before responding to a non-work reply, empty or doubled-seat refusal, or an unreachable-Manager notice, read [Repair defects and report a refused handover](implementation.md#repair-defects-and-report-a-refused-handover) for its seat/problem report and retry procedure.

Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules.
