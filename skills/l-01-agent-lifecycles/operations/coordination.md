<!-- ar:roles all -->
# Operation — Coordination

Coordinating roles use this operation to start and message Agents Remember (AR) roles on Paseo. It defines exact task binding, stage order, first intake, delivery and recovery; it grants no additional role authority.

For a coordinating role, Paseo runs the agents. Use `role_start` and `role_message` on `agents-remember-task` to start and reach them. Bind each started role to its exact canonical AR task and requirement. Your tool-server binding identifies you as sender and parent. Address only agents these tools returned or resolved; never invent a recipient. The launcher has no role binding and sends no `role_message`.

## Choose the coordination owner

The Architect first delegates one master to one Manager. For two or more masters worked on at the same time, it delegates to one Orchestrator on the sprint; that Orchestrator starts one Manager per master. Each Manager coordinates distinct Workers, Reviewers and Curators.

Only the developer may choose direct coordination by the Architect. The developer may also request an Orchestrator above a single master. If the Architect's coordinating-agent start is refused, tell the developer the refusal and its reason. Do not substitute direct coordination.

## Start one canonical role assignment

For each distinct assignment, call `role_start` on `agents-remember-task` with the role, its required exact sprint, master and leaf references, and a request identifier you choose.

The call resolves or creates the AR-paired enclosure, writes the complete handover beside the task report, records the launch, and creates the Paseo agent with you as parent. The agent starts its assignment immediately. The result gives its identifier, report path, handover artifact path and status.

The permitted starters are:

| Starter | Roles it may start |
| --- | --- |
| Architect | Investigator, Orchestrator, Manager, Worker, Reviewer and Curator |
| Orchestrator | Manager, Worker, Reviewer, Curator and Investigator under its own sprint |
| Manager | Worker, Reviewer, Curator and Investigator under its own master |
| Other roles | None |

For an uncertain start, follow [Start and configure role agents](../core/every-role.md#start-and-configure-role-agents) for same-identifier reconciliation and refusal reporting. The Manager's leaf starts follow the stage procedure below.

A leaf runs in the AR-paired enclosure resolved by the start. Preserve its admitted code, memory and contract pair. Never ask Paseo to create a Git worktree for an AR leaf. Do not substitute a global memory root. Preserve the returned agent identifiers, report paths and handover artifact paths with the exact task references in your durable handover.

<!-- ar:roles architect,orchestrator,manager -->
## Start leaf stages and deliver the first intake

The Manager calls `role_start` on `agents-remember-task` once per stage: Worker first, Reviewer at the freeze, and Curator when curation begins. Start only the Worker at build. The Worker's freeze notice and the Reviewer's code-`PASS` notice go to the Manager first.

For ordinary delivery, curation begins after code review passes. Use `agents-remember-task` under the Manager's own canonical master. Once the Reviewer exists, the Worker sends its freeze directly to it.

Two cases remain separate from ordinary delivery:

- An assigned packet cold read may occur without an early normal leaf Reviewer.
- When an approved packet's prerequisite sync reaches Curator-owned memory reconciliation, the Manager starts the Curator for that reconciliation. Ordinary delivery curation and the knowledge check still follow code `PASS`.

Complete this start and first-intake procedure before the seat's first inspection.

The complete intake contains the mode, canonical task and purpose, seam, exact requirement set and resolved tools. Candidate identity and attempt records arrive with the Worker's freeze, or in the integration/master-exit intake as that procedure specifies.

The Worker, Reviewer and Curator run the repair loop directly through `role_message` on `agents-remember-task`: Worker freeze to Reviewer; verdict and sealed findings to Worker; passed freeze and producer lists to Curator; memory change and verdict between Curator and Reviewer.

Before sending or answering a Curator's code concern, read [Curation failure handling](curation.md#failure-handling) for both recipients, post-`PASS` handling, the finding and answer, and disagreement routing.

<!-- ar:roles architect,orchestrator -->
The Manager reads their reports and decides the gate. It retains closeout, integration, status, acceptance and landing order; it relays no freeze, finding, verdict or memory change.

<!-- ar:roles architect,orchestrator,manager -->
Every handover keeps the paired code, memory and report paths and exact task references.

<!-- ar:roles all -->
## Preserve landing order and review records

Before landing leaves whose reviews passed, follow [Parallel work and waiting](../core/every-role.md#parallel-work-and-waiting) for serial landing order. Each leaf synchronizes once to the landing before it. A Manager starts every leaf whose prerequisites have landed. Dependencies and landing authority determine sequencing.

Before opening or recording an ordinary code review round, read [Record code review rounds](review.md#record-code-review-rounds) for the Reviewer's begin and result records through `task_doc` on `agents-remember-task`.

Before coordinating a memory review, read [Review boundaries](review.md#boundaries) for the separate reports, pass count, code-state isolation and extra-pass approval.

The Manager records the Architect's or developer's approval for an extra round or memory pass and accepts verdicts for the gate. Keep sealed findings and operation refusals unchanged.

<!-- ar:roles manager -->
<!-- ar:gives leaf-review-intake to reviewer: mode, task-and-purpose, seam, requirement-set, tools-file -->
<!-- ar:gives review-intake to reviewer: mode, task-and-purpose, seam, requirement-set, candidate-identity, attempt-records, tools-file -->
## Deliver the Manager's first Reviewer intake

The Manager receives the Worker's freeze notice to start the Reviewer, the Reviewer's code-`PASS` notice to start the Curator, and only the other occasions listed in [Manager leaf notices](../roles/manager.md#handle-leaf-notices-and-maintain-records), which names the authority and recovery occasions.

For a pending handover, give the sender the occasion to retry; relay no contents and ask no developer confirmation of that occasion. Read reports and task records for routine state. Record which seat holds the next step and since when. A start refused with `intent-review-open` waits for the Architect's `go`; never retry it.

After the individual `role_start` on `agents-remember-task`, send the leaf Reviewer's complete first intake as your first `role_message` to its returned agent identifier. Use the assigned review mode and review purpose and resolved `system/tools.md`. The requirement set includes identifiers, versions, packets and the durable [approval record](../core/every-role.md#requirements-and-limits). The Reviewer inspects nothing before its intake.

For integration or master-exit, start the Reviewer on the acceptance and delivery-proof leaf named by the master task data, with its sprint and master. If no such leaf is named, ask the Architect. Send the same intake, adding candidate identity and attempt records, as your first `role_message` on `agents-remember-task` to the returned agent identifier.
<!-- ar:roles all -->

## Address and deliver a message

Send one message to one agent with `role_message` on `agents-remember-task`. Address it by agent identifier or by role and task references. A leaf handover uses the role and exact task reference in `leafSeats.roleMessageArguments`. Use any product-supplied agent identifier whole and unchanged.

The leaf resolver reaches a seat's single live agent. Two live occupants cause a refusal naming both; the tool never selects one. Report an empty-seat refusal once, with its text, through the role's recovery route; choose no candidate. A Manager uses its parent route, never itself. A role address never means you yourself.

A message to a recipient already in a turn may be held until that turn ends. If the host delivers it into the running turn, that turn may be cancelled. If the host has not delivered it, the answer names `held` and its reason.

The host is never sent a message for a recipient at a permission prompt: doing so would answer that permission with a denial.

A closed session is resumed first. An archived or missing agent is refused and left as it is. If an archived identifier has a live successor on the same selection, the refusal names that successor; send the message again to it.

After a provider limit stops seats, do not treat anything sent to them during the stop as answered. Once a seat answers again, inspect its tasks, most stalled first, and name what each awaits. Send a continuation only after the agent's reload has finished.

## Interpret a waiting call

With `wait`, the call returns when the turn that took the message ends. The result contains one of:

- the recipient's final text;
- notice that its turn failed or was cancelled;
- a pending permission, including for a wait on a held message;
- a timeout while the message remains delivered;
- `accepted` without text when the host cannot identify which turn took the message; read the reply later.

A message delivered during a running turn may receive that turn's own text; `detail` identifies this case.

Do not wait on an agent that may be waiting on you. Two mutually waiting agents remain stopped until a wait expires. If your own tool calls end sooner than the wait, pass a shorter `timeout_seconds`.

An Orchestrator, Manager or developer-direct-coordinating Architect follows assignments until complete, blocked or awaiting a real developer decision. Starting a role does not complete coordination.

The Architect releases execution coordination after the handover and continues the developer conversation and status reading. It does not wait on an Orchestrator or Manager for execution completion.

On refusal or uncertainty, act on the named reason. Do not guess a parent or start duplicate work. A dashboard-started role works directly with the developer and needs no parent.

## Message files and receipts

Before locating a filed message or receipt, read [Find filed messages and receipts](../reference/message-handover.md#find-filed-messages-and-receipts) for the message folder and receipt scope to search.

The tool's fields have different size rules:

- `body` has no length limit and goes into the message file only.
- Over-limit `text` is refused. A reply to `wait` is cut at the same limit; the whole reply is filed beside the message and named by the response.
- `attach` names files that exist. The tool records their 256-bit Secure Hash Algorithm (SHA-256) values and copies them into the message folder when they lie outside the task record and leaf workspaces.

If there is no shared task, the tool files the message beside the start briefs of the sender's class.

The recipient reads the message and, when a filed message includes a `body` or attachments, the final `Message file:` line naming the file and its SHA-256. Address a return to the parent's role and task references from the handover's return address, never to an identifier read from an earlier message.

<!-- ar:roles architect,orchestrator,manager -->
## Recover an unfinished closeout

For an unfinished closeout, such as an interrupted one, read its generation number from `worktree_status` on `agents-remember-task` (`lifecycleOperations`). The operation stays addressable even when the closeout queue is absent or invalid-empty. Act on it through `worktree_operation_control`, with `operation_kind="closeout"`, `expected_generation` set to that number, and one of retry, recover, resume, cancel, retire or supersede. A mismatched generation is refused; read the status again rather than guessing.

After a closeout claim, lifecycle, worker, commit and recovery evidence belongs only to the enclosure-root operation journal. Observe it with `worktree_status` on `agents-remember-task` and execute only advertised `worktree_operation_control` actions. Queue absence or invalidation never strands that journal.

The Manager applies closeout under the recorded authority; no release by an Orchestrator is needed. When an Orchestrator coordinates the sprint, send it the apply's result or refusal with the leaf, master and sprint references, blockers and acceptance facts. Report facts only; do not rank other masters.

## Apply task edits and rebuild projections

Task documents remain authoritative during every scheduling and operation phase. Task edits never wait for closeout scheduling. Apply every intrinsically valid `task_doc` mutation on `agents-remember-task` first and inspect its machine-readable `projectionEffects`. These name every sprint in the before/after union and the exact rebuild action for any projection left invalid-empty. Relay every incomplete effect's exact `nextAction` to the Orchestrator for sprint-addressed rebuild.

Never whitelist task operations, patch a stale queue row, or treat queue or operation state as a task lock. If the change affects closeout evidence already accepted for a generation, re-prove it before the transaction is retried.

<!-- ar:roles all -->

## Reassess work from canonical state

When invoked, reassess from canonical AR task facts and actual evidence. Create no background polling or second scheduler. Paseo turn and status data describe execution; they are not another requirements, review, curation or acceptance ledger.

Questions from children follow your role's developer-question rule. Permission notices follow the procedure below.

## Relay a child's permission notice

If `role_message` on `agents-remember-task` returns `held` because a child awaits a permission prompt, or a wait returns `permission-pending`, tell your parent immediately. Without a parent, tell the developer in your own chat.

Name the child's role, task number, what the task does, agent identifier, and the permission as supplied, or "not supplied" if none was named. Each parent passes the notice upward unchanged. At the top, tell the developer which chat to open.

Whenever you say agents await the developer, list each role, task number, what the task does and agent identifier, plus a dashboard chat number if shown. Never give only a count. Do not ask the developer to confirm an answered prompt.

<!-- ar:roles orchestrator,manager -->
Before deciding a formal permission, follow [Formal permissions](orientation.md#formal-permissions) for the Architect's own-seat answers and the prompts the developer decides.

<!-- ar:roles architect,orchestrator,manager -->

Before acting on a denied or unanswered harness request, read [Honor harness permission decisions](orientation.md#honor-harness-permission-decisions) for the waiting, denial and non-bypass rules.

<!-- ar:roles architect,orchestrator,manager -->
Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.
<!-- ar:roles all -->
