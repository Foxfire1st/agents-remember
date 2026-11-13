"""Independent expected clauses for current coordination and curation wording."""

CURRENT_COORDINATION_CLAUSES: dict[str, list[str]] = {
    "roles/architect.md": [
        "Answer a developer message before any other work "
        "continues, with an answer, not a status line.",
        "A message that begins with `From <role> · <task> · agent "
        "<id>` is a role agent's message, not the developer's "
        "answer or approval.",
        "For work inside one master, first start one Manager on "
        "that canonical master and hand it the coordination.",
        "When two or more masters are worked on at the same time, "
        "first start one Orchestrator on the selected canonical "
        "sprint; it starts one Manager for each master.",
        "Direct coordination is the exception. Only when the "
        "developer tells you to coordinate the role agents "
        "yourself, directly coordinate distinct Worker, Reviewer, "
        "and Curator agents.",
        "If the developer asks for an Orchestrator above a single master, start one.",
        "Keep one owner per task and one explicitly addressed recipient per message.",
        "If the coordinating agent's start is refused, tell the "
        "developer the refusal and its reason. Do not fall back to "
        "direct coordination without the developer's instruction.",
        "If a second master starts while a Manager works directly "
        "under you, start one Orchestrator on the sprint.",
        "Give it the existing Manager's exact agent ID, canonical "
        "master reference and current state; it reuses that Manager "
        "and starts no duplicate.",
        "Name the Orchestrator's agent ID to the existing Manager in one message.",
        "From then on the Manager sends matters requiring the developer to that Orchestrator.",
        "This changes its reporting recipient, not its fixed launch parent.",
        "For each role assignment, call `role_start` on the "
        "`agents-remember-task` tool server with the role, the "
        "exact canonical sprint, master, and leaf references its "
        "class requires, and a request ID you choose.",
        "It resolves or creates the AR paired enclosure, compiles "
        "the role's capsule and handover, and starts the agent in "
        "Paseo with you as its parent.",
        "The agent begins its assignment at once.",
        "Keep the returned agent ID, report path, and handover artifact path.",
        "You may start Investigator, Orchestrator, Manager, Worker, Reviewer and Curator.",
        (
            "Before starting a role and delivering its first intake, read [Start "
            "one canonical role "
            "assignment](../operations/coordination.md#start-one-canonical-role-assignment) "
            "for the permitted starters of `role_start` and [Start leaf stages and "
            "deliver the first "
            "intake](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake) "
            "for stage timing and the intake fields of the first `role_message` on "
            "`agents-remember-task`."
        ),
        "After review, under developer-chosen direct coordination "
        "send a repair for the specific findings to the same Worker "
        "with `role_message` on `agents-remember-task` instead of "
        "starting another Worker.",
        "Under delegation, send it to the Manager or Orchestrator coordinating the work.",
        "Keep the conversation with the developer, the plan and its "
        "order, the requirements and their text, and the decision "
        "on what needs the developer. Hand the Manager or "
        "Orchestrator the role starts and messages, the order of "
        "the day, operational problems, ordinary requirement "
        "interpretation within the intended promise and operational "
        "rulings, checks before and after a landing, and paired "
        "closeout within its delegated authority.",
        "Read the coordinating agent's rulings record, list of "
        "requirement sentences to change, and status file on your "
        "own; it sends no routine ruling or landing notice. Bring "
        "the requirement texts in line with its rulings within the "
        "intended promise. New or dropped scope and changes to that "
        "promise still need the developer's decision.",
        "After the handover, release execution coordination to the "
        "Orchestrator or Manager and continue the developer "
        "conversation and status reading. Do not hold a `wait` on "
        "an Orchestrator or Manager for execution completion "
        "through `role_message` on `agents-remember-task`; those "
        "coordinators own their completion loops.",
        "When the developer answers or gives a ruling, send that "
        "actual answer or ruling to the requesting Manager or "
        "Orchestrator with `role_message` on "
        "`agents-remember-task`, addressed by its recorded agent "
        "ID. A reply to a coordinating agent's wait that repeats "
        "its question is not the developer's decision; relay the "
        "decision only once you have obtained the developer's "
        "actual answer or ruling.",
        "When an Investigator is needed, start it with `role_start` "
        "on `agents-remember-task` without a task reference, on a "
        "sprint, a standalone master, or both. Send the scoped concern "
        "as your first `role_message` on `agents-remember-task` to "
        "the returned agent ID. A Manager or Orchestrator may also "
        "start an Investigator within its own scope.",
        "You have no parent; ask the developer in your own chat. "
        "Put requests for a ruling there too; the developer answers "
        "there.",
        "Under developer-chosen direct coordination, review the "
        "full candidate diff, including unattributed changes.",
        "Under delegation, the Manager owns the leaf-diff and "
        "repair loop and supplies aggregate evidence through the "
        "Orchestrator where one exists.",
        "Treat invariant-family attribution as an additional review "
        "dimension, not a filter on changed files.",
        "Keep a finished turn, semantic review, curation, and "
        "paired Git publication as separate facts.",
        "Do not accept your own work or convert green checks into requirement acceptance.",
        "Record decisions and task truth through the existing AR task and data owners.",
        "You may check, measure, run tests and builds, read the "
        "code and sources of the product, the host and the "
        "harnesses, and try things out in a sandbox yourself, as "
        "the developer would.",
        "This includes a check before a landing.",
        "Such work informs your rulings, your requirements and the developer.",
        "It never takes the place of a leaf's build, review, curation or gate.",
        "Change no leaf's code or memory: those go through its Worker, Reviewer and Curator.",
        "Grade nothing you wrote.",
        "Run nothing of yours in a candidate's worktree; work on a copy.",
        "Reading code and sources for a check is not loading "
        "another task's documents or another role's instructions.",
        "Preserve the read rules under [Read knowledge and code](#read-knowledge-and-code) "
        "and [Confirm the assignment](../operations/orientation.md#confirm-the-assignment) "
        "for task-document scope and supplied bindings.",
        "Perform no step of a landing except the paired closeout "
        "under developer-chosen direct coordination described "
        "below.",
        "A check before a landing is not a step of it.",
        "For a check or investigation you do not do yourself, give "
        "it to the Manager or Orchestrator coordinating the work.",
        "Under developer-chosen direct coordination, assign a "
        "leaf's check to its Worker or Reviewer and a check or "
        "investigation that belongs to no leaf to an Investigator.",
        "You may author requirements, tasks, plans, and rulings. Do not write onboarding.",
        "Flat mode here means only the developer-chosen direct-coordination exception.",
        (
            "When no Manager or Orchestrator owns closeout, you may coordinate the "
            "existing paired closeout (`worktree_closeout_preview` and "
            "`worktree_closeout_apply` on `agents-remember-task`) only under the "
            "already-delegated authority of [Authorized "
            "Closeout](../operations/closeout.md), after independent review and "
            "required curation evidence exist."
        ),
        "In that case, call the paired closeout yourself and start no Manager for that step alone.",
        "It grants no other execution. Human-pinned approvals "
        "remain human; never substitute raw Git or self-approval.",
        "A high-impact design or security choice, requirement "
        "contradiction, missing authority, or scope change returns "
        "to the developer.",
        "One concise report preserves the decision, references, "
        "evidence, open questions, and limits.",
        "No completion claim replaces inspection of the artifact.",
    ],
    "core/acceptance.md": [
        "It does not establish that the required artifact exists, is current or satisfies its requirement.",
        "On wake, the current gate owner opens and validates the required artifact, candidate identity, evidence and acceptance envelope before advancing lifecycle state.",
    ],
    "roles/orchestrator.md": [
        "You coordinate one selected sprint when two or more "
        "masters are worked on at the same time, or when the "
        "developer explicitly asks for an Orchestrator above a "
        "single master.",
        "You work at Projects altitude, the execution location supplied by your launch.",
        "The launch also supplies the canonical sprint reference and allowed workspace.",
        "An agent's state in Paseo does not record Agents Remember (AR) acceptance.",
        "For the accepted objective, reuse each existing Manager "
        "named in the Architect's handover.",
        "Start one Manager for each other master with "
        "`role_start` on the `agents-remember-task` tool server, "
        "using a selection under your own sprint.",
        "Those Managers coordinate their distinct Workers, Reviewers, and Curators.",
        "You may start Manager, Worker, Reviewer, Curator, and Investigator.",
        (
            "Before starting or messaging a role, read [role "
            "assignments](../operations/coordination.md#start-one-canonical-role-assignment) "
            "for the selection and start rules of `role_start` on "
            "`agents-remember-task`, [the first "
            "intake](../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake) "
            "for stage timing and intake fields, and [message "
            "delivery](../operations/coordination.md#address-and-deliver-a-message) "
            "for the addresses, seats and delivery state of `role_message` on "
            "`agents-remember-task`."
        ),
        "Your tool server's binding names you as the sender.",
        "Address each recipient explicitly by agent ID or by "
        "role and task references, and keep the returned agent "
        "IDs; do not act as an invisible proxy.",
        "Keep each work item tied to its canonical AR task and primary requirement; a start is "
        "not completion.",
        "Handle the order of the day, operational problems, "
        "checks before and after a landing, and paired closeout "
        "within delegated authority.",
        "An Orchestrator started from the dashboard has no parent and needs none.",
        "Put developer decisions in your own chat; do not invent a parent.",
        "Decide ordinary requirement interpretation within the "
        "intended promise and operational rulings yourself, and "
        "keep the work going.",
        "Under the selected task's notes, keep:",
        "one durable rulings record",
        "a list of requirement sentences that should change",
        "one status file",
        "Name their paths in your report.",
        "When the Architect started you, it reads these files "
        "itself and aligns the requirement texts with the "
        "rulings.",
        "These records do not authorize new or dropped scope or a changed promise.",
        "If your permissions prevent a needed role start, report "
        "the blocker to the agent that started you with "
        "`role_message` on `agents-remember-task`.",
        "Without a parent, report it in your own chat.",
        "Do not invent an Architect ID or start the blocked role yourself.",
        "Assign a check or investigation that belongs to no leaf "
        "to an Investigator on your own sprint, or on your "
        "sprint and one of its masters.",
        "Send the scoped concern as your first `role_message` on "
        "`agents-remember-task` to its returned agent ID.",
        "Never select a leaf for this role.",
        "When the Architect started you, message it for a developer decision about:",
        "new or dropped scope, or a change to a requirement's promise",
        "something only the developer can do or approve",
        "an override of a role default",
        "a small item that has cost about an hour, or a second "
        "repair round or freeze; include its hours and a "
        "proposal",
        "a blocker that none of your own decisions can remove",
        "Send these with `role_message` on "
        "`agents-remember-task` to the parent agent ID in your "
        "handover.",
        "Forward only questions requiring the developer and the "
        "permission notices described under [Questions received "
        "from children](#questions-received-from-children).",
        "Send no other role messages or landing notices to the Architect.",
        "Ordinary requirement readings and operational rulings "
        "stay with you; do not ask the Architect to perform "
        "them.",
        "Send one further message with your report path when the "
        "whole assignment is finished or cannot be finished.",
        "Send no other messages to the Architect.",
        "When the send answers held, follow [the held-delivery rule](../core/every-role.md#parallel-work-and-waiting) for queued delivery, consumption and supersession. For a refusal other than held or full-store, write the matter and the refusal in your status file. Either way, continue the work that does not depend on the developer's decision, and do not decide the developer's question yourself.",
        "For a refusal other than held or full-store, write the matter and the refusal in your status file.",
        "Either way, continue the work that does not depend on "
        "the developer's decision, and do not decide the "
        "developer's question yourself.",
        "A turn ending proves only that it ended.",
        "Verify each Manager's delivery and aggregate evidence.",
        "The Manager owns the leaf-diff, repair and evidence "
        "loop; you may inspect supplied actual diffs for "
        "acceptance without taking over that loop.",
        "Request an independent Reviewer when the brief or risk "
        "requires it; a Reviewer never adjudicates its own work.",
        "Request a Curator for affected memory and onboarding when needed.",
        "Keep reports, findings, review, curation, and Git publication separately addressed.",
        "Use the existing AR task and paired Git owners for their semantic records.",
        "A turn ending proves only that it ended. Verify each "
        "Manager's delivery and aggregate evidence.",
        "A review of the sprint plan (plan review) or of the "
        "completed sprint\u2019s handover (super-exit review) has no "
        "startable Reviewer seat.",
        "Bring either review to the Architect, who takes it to "
        "the developer with the sprint-level Reviewer idea.",
    ],
    "roles/manager.md": [
        "Before starting and coordinating the leaf, read [leaf-stage coordination]"
        "(../operations/coordination.md#start-leaf-stages-and-deliver-the-first-intake) "
        "for start timing and intake",
        "the Worker asks you to start the Reviewer at freeze",
        "the Reviewer reports code review passing so you can start the Curator",
        "coordinates one selected master in Agents Remember (AR)",
        "Your canonical assignment is the selected master; you do not own the portfolio.",
        "When another agent starts you, report to that agent's role "
        "with its task references, unless the Architect changes your "
        "reporting recipient as described below.",
        "Read each leaf's reports and task records yourself.",
        # A004 pinned this pointer's earlier form; it follows the P5-14/15 content clause.
        "Before directing handovers, repair loops, review rounds, memory passes or relays, read [leaf coordination procedures](../operations/coordination.md) for stage timing, review records, message delivery and relay limits.",
        "Decide leaf order, agent starts and replacements, operational "
        "problems, ordinary requirement interpretation within the intended "
        "promise, and checks before and after landing.",
        "Inspect the complete changed-file diff and both verdicts before deciding the gate.",
        "Reviews supply evidence; you decide the gate and retain closeout, "
        "integration, status, acceptance writes and landing order.",
        # P5-07 OP-113 (look 1 F1): the Manager's one home of the relay limit.
        "Relay no freeze, finding, verdict or memory change.",
        "Preserve stable requirement and finding identifiers.",
        "Distinguish implementation, review, curation and publication status.",
        "Start, finalize and abandon already publish the task stage "
        "and mirror it into the master\u2019s row; do not follow them with "
        "a separate status write.",
        "Use `task_doc set_status` for an explicit correction and for "
        "organizational-master completion.",
        "That completion act remains yours.",
        "A Manager started from the dashboard has no parent and needs none.",
        "Put a needed developer decision in your own chat; invent no Architect identifier.",
        "Decide leaf order, agent starts and replacements, "
        "operational problems, ordinary requirement interpretation "
        "within the intended promise, and checks before and after "
        "landing.",
        "Decide paired closeout and integration within your delegated authority.",
        "You own the task's construction: step order, evidence form, "
        "which existing test or command demonstrates a property, and "
        "sandbox or scratch instruments that change no product "
        "source.",
        "After a harness, loader or capture fault, you may order one rerun on the same operands.",
        "A second failure of one cause goes upward with the attempts already made.",
        "Assign a check or investigation belonging to no leaf to "
        "an Investigator on your master and sprint if commanded.",
        "Never select a leaf for this role.",
        "Send the scoped concern as your first `role_message` on "
        "`agents-remember-task` to the returned agent identifier.",
        "Keep one durable rulings record, one list of requirement "
        "sentences that should change, and one status file under the "
        "selected master's notes.",
        "Name their paths in your report.",
        "The Architect reads them independently and aligns requirement text with the rulings.",
        "These records authorize no new or dropped scope and no changed promise.",
        "When another agent started you, send your parent or "
        "instructed reporting recipient only matters that require the "
        "developer:",
        "new or dropped scope, or a change to a requirement's promise",
        "something only the developer can do or approve",
        "an override of a role default",
        "a small item that has cost about an hour, or a second repair "
        "round or freeze, with the hours and a proposal",
        "a blocker none of your own decisions can remove",
        "Send these with `role_message` on `agents-remember-task` to "
        "the instructed reporting-recipient identifier in your report "
        "when one is recorded; otherwise use the parent agent "
        "identifier in your handover. If you have neither, put the "
        "developer question in your own chat.",
        (
            "When the Orchestrator owns coordination, do not bypass it to the Architect except for the "
            "skeleton handover above."
        ),
        "Ordinary requirement readings and operational rulings remain yours; keep the work going.",
        (
            "Apart from the skeleton handover above, forward only questions requiring the developer and "
            "the permission notices below."
        ),
        (
            "Apart from that skeleton handover, send no other role messages or landing notices to that "
            "recipient, except the closeout-result notice below and one further message naming your report "
            "path when the whole assignment is finished or cannot be finished."
        ),
        "Address that final message to the parent's role with its task references.",
        "If the Architect that started you names an Orchestrator's "
        "agent identifier in a message when a second master starts, "
        "record the instruction and recipient identifier in your "
        "durable report.",
        "Send developer-needed matters to that Orchestrator from then on.",
        "This changes your reporting recipient, not your fixed launch parent.",
        "No other message changes whom you report to.",
        "If you cannot start a needed role within your permissions, "
        "report the blocker to your parent or instructed recipient "
        "through `role_message` on `agents-remember-task`.",
        "Without a parent, report it in your own chat.",
        "Never start that role yourself or invent an Architect identifier.",
        "If an upward message cannot be delivered, record the matter "
        "in your status file and continue all work independent of the "
        "developer\u2019s decision.",
        "Do not decide the developer\u2019s question yourself.",
    ],
    "operations/planning.md": [
        "Before delegating coordination, read [Choose the coordination "
        "owner](coordination.md#choose-the-coordination-owner) for the Architect's first "
        "Manager or Orchestrator and the developer-chosen direct-coordination exception.",
        "Keep one explicit owner per task and preserve started agents' identifiers.",
        "Do not fabricate a sprint, master or agent identity for a taskless project role.",
    ],
    "operations/coordination.md": [
        "The Manager calls `role_start` on "
        "`agents-remember-task` once per stage: Worker "
        "first, Reviewer at the freeze, and Curator when "
        "curation begins.",
        "The Worker's freeze notice and the Reviewer's code-`PASS` notice go to the Manager first.",
        "Start only the Worker at build.",
        "For ordinary delivery, curation begins after code review passes.",
        "Once the Reviewer exists, the Worker sends its freeze directly to it.",
        "An assigned packet cold read may occur without an early normal leaf Reviewer.",
        "Ordinary delivery curation and the knowledge check still follow code `PASS`.",
        "The Worker, Reviewer and Curator run the repair "
        "loop directly through `role_message` on "
        "`agents-remember-task`: Worker freeze to Reviewer; "
        "verdict and sealed findings to Worker; passed "
        "freeze and producer lists to Curator; memory "
        "change and verdict between Curator and Reviewer.",
        "Before sending or answering a Curator's code "
        "concern, read [Curation failure "
        "handling](curation.md#failure-handling) for both "
        "recipients, post-`PASS` handling, the finding and "
        "answer, and disagreement routing.",
        "The Manager reads their reports and decides the gate.",
        "It retains closeout, integration, status, "
        "acceptance and landing order; it relays no freeze, "
        "finding, verdict or memory change.",
        "Every handover keeps the paired code, memory and report paths and exact task references.",
        "The Architect first delegates one master to one Manager.",
        "For two or more masters worked on at the same "
        "time, it delegates to one Orchestrator on the "
        "sprint; that Orchestrator starts one Manager per "
        "master.",
        "Each Manager coordinates distinct Workers, Reviewers and Curators.",
        "Only the developer may choose direct coordination by the Architect.",
        "The developer may also request an Orchestrator above a single master.",
        "If the Architect's coordinating-agent start is "
        "refused, tell the developer the refusal and its "
        "reason.",
        "Do not substitute direct coordination.",
        "Send one message to one agent with `role_message` on `agents-remember-task`.",
        "Address it by agent identifier or by role and task references.",
        "A leaf handover uses the role and exact task "
        "reference in `leafSeats.roleMessageArguments`.",
        "Use any product-supplied agent identifier whole and unchanged.",
        "The leaf resolver reaches a seat's single live agent.",
        "Two live occupants cause a refusal naming both; the tool never selects one.",
        "Report an empty-seat refusal once, with its text, "
        "through the role's recovery route; choose no "
        "candidate.",
        "A Manager uses its parent route, never itself.",
        "A role address never means you yourself.",
        "A message to a recipient already in a turn may be "
        "held until that turn ends. If the host delivers it "
        "into the running turn, that turn may be cancelled. "
        "If the host has not delivered it, the answer names "
        "`held` and its reason.",
        "If the host has not delivered it, the answer names `held` and its reason.",
        "The host is never sent a message for a recipient "
        "at a permission prompt: doing so would answer that "
        "permission with a denial.",
        "Before acting on a denied or unanswered harness "
        "request, read [Honor harness permission "
        "decisions](orientation.md#honor-harness-permission-decisions) "
        "for the waiting, denial and non-bypass rules.",
        "Before deciding a formal permission, follow "
        "[Formal "
        "permissions](orientation.md#formal-permissions) for the Architect's own-seat "
        "answers and the prompts the developer decides.",
        "A closed session is resumed first.",
        "An archived or missing agent is refused and left as it is.",
        "With `wait`, the call returns when the turn that took the message ends.",
        "the recipient's final text",
        "notice that its turn failed or was cancelled",
        "a pending permission, including for a wait on a held message",
        "a timeout while the message remains delivered",
        "`accepted` without text when the host cannot "
        "identify which turn took the message; read the "
        "reply later.",
        "A message delivered during a running turn may "
        "receive that turn's own text; `detail` identifies "
        "this case.",
        "Do not wait on an agent that may be waiting on you.",
        "Two mutually waiting agents remain stopped until a wait expires.",
        "If your own tool calls end sooner than the wait, pass a shorter `timeout_seconds`.",
        "An Orchestrator, Manager or "
        "developer-direct-coordinating Architect follows "
        "assignments until complete, blocked or awaiting a "
        "real developer decision.",
        "Starting a role does not complete coordination.",
        "The Architect releases execution coordination "
        "after the handover and continues the developer "
        "conversation and status reading.",
        "It does not wait on an Orchestrator or Manager for execution completion.",
        "On refusal or uncertainty, act on the named reason.",
        "Do not guess a parent or start duplicate work.",
        "A dashboard-started role works directly with the developer and needs no parent.",
        "For an uncertain start, follow [Start and configure role agents]"
        "(../core/every-role.md#start-and-configure-role-agents) for "
        "same-identifier reconciliation and refusal reporting.",
    ],
    "SKILL.md": [
        "Use the agent IDs these tools return and the sender line of each message you receive; never "
        "invent a sender, recipient, parent, or delivery result.",
        "AR remains authoritative for canonical tasks, requirements, knowledge, curation, and paired "
        "Git operations.",
        "An Architect first delegates coordination to one Manager for one master, or to one "
        "Orchestrator on the sprint when two or more masters are worked on at the same time. The "
        "Orchestrator starts one Manager per master. Only the developer may choose direct coordination "
        "by the Architect. The developer may also ask for an Orchestrator above a single master. A "
        "role started from the dashboard has no parent agent and needs none. Existing approvals and "
        "rulings remain durable across reconnects and compaction; ask again only for new or changed "
        "scope, a real requirement conflict, or an unresolved human-pinned decision.",
    ],
    "roles/curator.md": [
        "Before intake and writer admission, read [required curation inputs](../operations/curation.md#required-inputs) "
        "for the candidate, scope and lists, and [bound intake and capabilities](../operations/curation.md#bound-paseo-intake-and-capabilities) "
        "for the serving writer and capability checks.",
        "Start the leaf pass only on a leaf task document, never taskless.",
        "Before a repository's first or resumed knowledge foundation, read [repository foundation]({{detail:reference/curator/repository-foundation.md}}) for its task admission and distinct outputs.",
        "Never fabricate a task, worktree or enclosure for either route.",
        "Use mounted `knowledge_write`",
        "Only the tool and input class of a covered correct write admit open manual recovery; "
        "every other write retains its tool.",
        "Report one full-intake worklist of distinct outstanding memory actions.",
        "A dashboard-started Curator needs no parent.",
        "A Curator starts no role.",
        "A harness without helpers can perform the same work.",
    ],
    "operations/curation.md": [
        (
            "Before responding to a non-work reply, an empty or doubled-seat "
            "refusal, an unreachable-Manager notice, or failed delivery of a "
            "required Manager notice, read [Report a refused "
            "handover](implementation.md#report-a-refused-handover) for the "
            "seat/problem report, pending state, independent continuation and the "
            "notice retry through `role_message` on `agents-remember-task`. It "
            "transfers no Worker or transaction authority. The permitted Manager "
            "notice occasions below remain unchanged."
        ),
        "Send evidenced code concerns to both Worker and "
        "Reviewer, including after passing code review. Show "
        "the requirement or report mismatch.",
        "The Reviewer records a finding and reopens the point, "
        "or explains its rejection. The Curator records the "
        "answer.",
        "Report the finding and answer; send unresolved disagreement to the Manager.",
    ],
    "core/every-role.md": [
        "Architect, Orchestrator, Manager: Start each role once, with the exact task "
        "selection and request identifier. If the result is uncertain, reconcile it "
        "with that same identifier. Report a refusal without switching to another tool."
    ],
    "reference/curator/memory-handover.md": [
        "The Reviewer compares the leaf's base and candidate in Git: the code trees and "
        "the knowledge files in both memory trees. The Curator records no separate "
        "comparison."
    ],
}
