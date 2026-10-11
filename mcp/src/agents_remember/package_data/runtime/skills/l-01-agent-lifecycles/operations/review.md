# Operation — Review

Review is independent and runs only when the task or owner requests it. Its inputs are the exact requirement revision, candidate/base, review mode, criteria, and report path. A first cold requirement read has no code candidate or Worker attempt and opens no code round. Once the Worker's freeze arrives, missing candidate or review authority is reported, not guessed.

## Outputs

Write one concise pass/block recommendation with changed-file coverage, findings (stable ID, location, evidence, impact, required repair/proof), checks, and limitations. A baseline covers the complete candidate diff: all changed files, including unattributed changes. Check each requirement against its evidence class and relevant behavior; a test result is evidence, not semantic acceptance. Fix-verification checks only the sealed outstanding findings against the same requirement and candidate lineage. A new contradiction or scope issue is reported separately.

For a leaf, hand the verdict directly to the Worker with `role_message` on `agents-remember-task`; a block names the sealed findings report and asks for the whole repair stretch. Code PASS also goes directly to the Curator for the passed freeze. Review its memory change and return findings or PASS directly to it. Name the leaf, exact object (candidate head and change hash from the Worker's report, or memory change identity), report/verdict path, and the whole next stretch. Use the sibling role and exact leaf task references from the handover; product-supplied IDs stay whole and unchanged. A Worker's factual hand-over gives no review instructions, and the Reviewer does not read the Worker's chat.

The Reviewer opens each ordinary leaf code round with `task_doc(operation="begin_review")` and records its own verdict with `record_review` or applicable `record_route_review`, including any preceding result required before the next begin. Non-leaf seams keep owner recording and their seam's verdict recipient.

Where every finding on the candidate is closed and what remains is a finding on the Worker's record alone (its report, its evidence table, a hash or an anchor in it), the same Reviewer checks the repaired record on the round it last recorded: `begin_review` is not called and the count stays, and only while the candidate is byte for byte the candidate that round was recorded on. With a changed candidate the tool refuses and names `begin_review` as the next step.

When a review's rounds are used up and the only items left were raised before and have since been repaired, the same Reviewer verifies exactly those items in the next round: within the settings limit it is an ordinary fix-verification round; beyond the limit the Manager asks the task's architect for a further round (or the developer) and records the approval. A new finding or a new item is not covered.

## Boundaries

The Reviewer does not edit, record AR task acceptance, start any role, or publish Git. The Manager retains gate acceptance and records the architect's or the developer's approval for any extra round. Curator memory checks keep separately sealed reports, IDs and a count with the same settings limit as code; they open no review-state round, reset no passed code review and add no IDs to its baseline. Any extra memory pass follows `../operations/curation.md` §Handoff / exit states. After code and memory PASS, tell the Manager once that the leaf is ready for closeout with the passed freeze and both verdicts. Other Manager notices are only authority, ownership, recovery, beyond-limit work or Curator code disagreement; it relays no leaf contents. Developer questions follow the role's developer-question rule; candidate questions go directly to the Worker and memory questions to the Curator.

Answer Curator code findings directly to Curator and Worker, even after code PASS: take a valid finding into your findings and reopen the verdict for that point, or state why it does not hold. Preserve the sealed fixed-list baseline; an unresolved disagreement or authority issue goes to the Manager.

Count a hand-over as taken and handle provider, seat, busy and unreachable-Manager refusals as `../operations/implementation.md` §Boundaries states.

Developer questions and relayed answers follow `operations/orientation.md`.

Required input: the permission non-bypass boundary in `operations/orientation.md` — a denied or unanswered harness request is never worked around with another tool, command, helper seat, or later turn.
