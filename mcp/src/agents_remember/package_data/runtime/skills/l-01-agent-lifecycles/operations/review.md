<!-- ar:roles all -->
# Operation — Review

Review is independent and runs only when the task or owner requests it. Its inputs are the exact requirement revision, candidate/base, review mode, criteria, and report path. A first cold requirement read has no code candidate or Worker attempt and opens no code round. Once the Worker's freeze arrives, missing candidate or review authority is reported, not guessed.

Beside a leaf's Reviewer, the Architect may commission advisory Investigators on other providers; each is kept apart, reports only to the Architect, and the Reviewer's verdict stays the grade.

<!-- ar:roles architect -->
## Advisory independent reviews

- **An exception to routing.** An advisory review of a leaf's freeze is a check the Architect commissions itself. The Architect carries the advisory findings to the Reviewer itself; the Manager relays no finding.
- **Who decides.** The Architect may add advisory reviews beside a leaf's Reviewer seat when the developer asks or when the Architect judges a second view worth its cost. There may be any number of them, each on any provider an installed harness reaches.
- **The seats.** They are Investigators started with `role_start` on `agents-remember-task` without a task reference. They hold no leaf seat; the one Reviewer seat per leaf stays.
- **The same material.** Each receives the same material as the Reviewer: the freeze, packets, rulings, design source and Worker's evidence. Each gets its own output folder, its own clean copy for anything it runs, and its own ports.
- **Kept apart.** No advisory reviewer reads the Reviewer's or Curator's files, any message to or from them or the Manager, or another advisory reviewer's folders. Each reports only to the Architect. The Investigator's capsule does not include this operation, so the Architect's brief to each carries these rules. The brief names nothing the Architect or developer has already noticed, preserving the comparison's independence.
- **Advisory.** The Reviewer's verdict stays the grade the product reads; an advisory report changes no verdict by itself. Once all reports are in, the Architect compares what was found by all, by some and by one. It sends the Reviewer one questions file with each finding the Reviewer did not make, and tells the Manager once, for the gate and the round.
  - The questions file asks the Reviewer to rule on each as on a Curator's code finding: take it into its findings and reopen the verdict for that point, or say why it does not hold. The file states this duty because Reviewer and Manager do not read this Architect-only section.
  - The Manager's message states that the leaf's next step waits for the Reviewer's rulings and whether the Architect grants an extra round. An extra round for these findings is the Architect's grant.
  - The Reviewer sends its rulings to the Architect; an accepted finding goes to the Worker as usual. The Architect's pre-landing check confirms that every advisory finding has a ruling.
  - A disagreement is settled by the Architect and developer discussing the findings.
- **Costs and permissions.** Each advisory reviewer uses its provider's quota and the machine's memory. The Investigator role has no settings entry and starts in the least permissive mode. The Architect sets its mode or runs an approver only within sufficient seat permissions; default/auto-review or a refused approval leaves the decision to the user, notified once, without bypass. Preserve a parent chain where one exists; a parentless Architect does not route through itself.
- **What the comparison showed.** In the L89 trial, only one finding was common to all three reviews, five were shared by two, and 29 of 35 distinct findings came from one reviewer alone. The official Reviewer went deep on routes and evidence contracts; one advisory review found browser layout and packet-text defects, while the other found an omitted full-suite failure and a missing width measurement. All three missed the developer's concerns about the top text wall and provenance labels. After an initial send to the Manager, advisory findings went directly to the Reviewer for individual rulings; the Architect sent its disposition directly and told the Manager once for the gate, with no extra round.
<!-- ar:roles all -->

## Outputs

Write one concise pass/block recommendation with changed-file coverage, findings (stable ID, location, evidence, impact, required repair/proof), checks, and limitations. A baseline covers the complete candidate diff: all changed files, including unattributed changes. Check each requirement against its evidence class and relevant behavior; a test result is evidence, not semantic acceptance. Fix-verification checks only the sealed outstanding findings against the same requirement and candidate lineage. A new contradiction or scope issue is reported separately.

<!-- ar:roles reviewer -->
<!-- ar:gives producer-list to curator: list-file, freeze-identity, handoff-check-result -->
If your opening names no review mode, wait for your starter's first `role_message` on `agents-remember-task`; inspect nothing before it.

For a leaf, on code PASS first tell the Manager to start the Curator, before peer handover. Hand the verdict directly to the Worker with `role_message` on `agents-remember-task`; the verdict travels as a file and the message names its path, never its pasted text; a block names the sealed findings report and asks for the whole repair stretch. Once the Curator exists, send it the passed freeze and code-PASS verdict directly. A separately assigned packet cold read remains possible without an early normal leaf Reviewer. Review its memory change and return findings or PASS directly to it. Name the leaf, exact object (candidate head and change hash from the Worker's report, or memory change identity), report/verdict path, and the whole next stretch. Use the sibling role and exact leaf task references from the handover; product-supplied IDs stay whole and unchanged. A Worker's factual hand-over gives no review instructions, and the Reviewer does not read the Worker's chat. A cleanly synced leaf sends no new freeze to you and opens no round: where no path was changed on both sides, the Worker runs its focused tests once more and the Manager's closeout preview decides. Where a path was changed on both sides, or a focused test fails, the Worker sends exactly that repair for one look. When a freeze arrives, check every finding against its entry in the Worker's freeze report and return a freeze that lacks the entries, with no round counted.

Before you send your hand-off list, run `handoff-check` with the serving build's interpreter (`server_info` on `agents-remember-task`) on it against that freeze, and name its result.
<!-- ar:roles all -->

The Reviewer opens each ordinary leaf code round with `task_doc(operation="begin_review")` and records its own verdict with `record_review` or applicable `record_route_review`, including any preceding result required before the next begin. Non-leaf seams keep owner recording and their seam's verdict recipient.

<!-- ar:roles reviewer -->
Where every finding on the candidate is closed and what remains is a finding on the Worker's record alone (its report, its evidence table, a hash or an anchor in it), the same Reviewer checks the repaired record on the round it last recorded: `begin_review` is not called and the count stays, and only while the candidate is byte for byte the candidate that round was recorded on. With a changed candidate the tool refuses and names `begin_review` as the next step.
<!-- ar:roles all -->

When a review's rounds are used up and the only items left were raised before and have since been repaired, the same Reviewer verifies exactly those items in the next round: within the settings limit it is an ordinary fix-verification round; beyond the limit the Manager asks the task's architect for a further round (or the developer) and records the approval. A new finding or a new item is not covered.

## Messages

Messages wake; files carry. A message says what is asked or what is handed over, and names the files: evidence, verdicts, lists and logs are files, never pasted into a message. The full text goes in `body` and its files in `attach`; the tool files every message in the `messages` folder of the narrowest task the two agents share, and a leaf workspace links that folder as `messages`. The recipient reads the message and, where a file belongs to it, one last line naming the file and its SHA-256. A text holds at most the limit of the settings. A return goes to the parent's role with its task references, never to an id read from an earlier message.

## Boundaries

The Reviewer does not edit, record AR task acceptance, start any role, or publish Git. The Manager retains gate acceptance and records the architect's or the developer's approval for any extra round. Curator memory checks use their own sealed reports, IDs and recorded lane: `begin_review`/`record_review` with `reviewKind=memory`, same settings limit and extra-round approval as code. They open no code review-state round, reset no passed code review and add no IDs to its baseline. Under-way memory reviews finish as begun; recorded lanes apply after landing. Any extra memory pass follows `../operations/curation.md` §Handoff / exit. After code and memory PASS, tell the Manager once that the leaf is ready for closeout with the passed freeze and both verdicts. Apart from the code-PASS start notice and ready-for-closeout notice, Manager notices are only authority, ownership, recovery, beyond-limit work or Curator code disagreement; it relays no leaf contents. Developer questions follow the role's developer-question rule; candidate questions go directly to the Worker and memory questions to the Curator.

<!-- ar:roles reviewer -->
Answer Curator code findings directly to Curator and Worker, even after code PASS: take a valid finding into your findings and reopen the verdict for that point, or state why it does not hold. Preserve the sealed fixed-list baseline; an unresolved disagreement or authority issue goes to the Manager.
<!-- ar:roles all -->

Handle non-work replies, empty or doubled seats and unreachable-Manager notices as `../operations/implementation.md` §Boundaries states.

Developer questions and relayed answers follow `operations/orientation.md`.

Required input: the permission non-bypass boundary in `operations/orientation.md` — a denied or unanswered harness request is never worked around with another tool, command, helper seat, or later turn.

<!-- ar:roles all -->
Read [message handover]({{detail:reference/message-handover.md}}) when you reach a step another seat awaits, a handover, or a held/full-store receipt, not before.
