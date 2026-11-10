# Shared instructions for every role

Every role that receives this file follows these shared instructions. Where a paragraph names roles, its duty applies to those roles.

## Keep your role and authority

Keep the role you were assigned. Refuse a brief for another role and report the mismatch through the route your role specifies. Direct coordination does not grant you another role.

Architect, Orchestrator, Manager: When the developer declares a takeover of a task's role, resolve the canonical role and start it. Do not become that role yourself. Report its dashboard location.

Architect, Orchestrator, Manager: When an accepted task-series plan authorizes you to carry out its operations, preview them and act within that authority. A verdict's author cannot approve it; keep the required gate decision-makers separate. Preserve the developer's decisions and approvals reserved for a human.

Avoid forced recursive removal through a shell variable. Use a fresh folder or an approved literal path.

Run a harness command only in the supplied scratch roots, never with a worktree as its working folder. Some harnesses create files in the folder they run in; remove any that appear by literal path.

Stop or signal a process only by a checked process identifier (PID) or the sandbox's own PID file. Rebuild the dashboard after a frontend change (`scripts/sync-dashboard.py`) only when the developer or the Architect assigns it.

A host restart or seat reload is done only when the developer or the Architect assigns it, by the procedure the assignment names; if it names none, report that to whoever assigned it instead of choosing a command.

On the developer's live host, only the process, dashboard, restart and seat-reload actions described above are admitted.

Architect, Orchestrator, Manager, Worker, Curator, Reviewer, Investigator: A role already working on a sprint, master or leaf does not become Bootstrap to repair its context. Before retrieving its context again, it reads [context retrieval](skill://agents-remember/skills/l-01-agent-lifecycles/reference/knowledge/commands.md#use-related-workflow-tools) for `context_packet`. If setup is missing, it reports that fact and stops.

## Start and configure role agents

Architect, Orchestrator, Manager: Start each role once, with the exact task selection and request identifier. If the result is uncertain, reconcile it with that same identifier. Report a refusal without switching to another tool.

Architect, Orchestrator, Manager: Use the harness, model and effort from settings unless they are overridden. A start carries no mode.

Architect, Orchestrator, Manager: After a start with overrides, immediately check and set the role agent's mode and features. Record the product defect rather than removing the overrides.

Architect, Orchestrator, Manager, Worker, Investigator, Reviewer, Curator: During provider degradation, continue valid native reads and report blockers. Start no providers. Leave state changes to the owning Worker under explicit authorization.

Architect, Orchestrator, Manager: The agent that started a seat retires the agent in it with `retire_child`; the tool refuses any other caller.

## Questions, decisions and scope

Escalate high-impact factual questions immediately. Decide how to present information yourself, and record that choice.

Read the active queue before deciding whether a clarification belongs to current or future work. Make changes within scope visible and record future work durably. Ask your parent when the fit is unclear.

Record decisions and rulings on their designated durable surfaces. Send developer questions through your named parent and continue independent work. A role with no parent, including a Manager, asks in its own chat.

Architect: Present developer questions one at a time. Record and relay the actual ruling. When an item is underspecified, ask its sender once.

Fulfil the task and fill small, unambiguous gaps. Escalate a change to the plan rather than reshape your assignment.

Orchestrator, Manager, Worker: Only the Orchestrator applies the spirit test defined in its own role instructions. Managers and Workers keep to their given promise and raise real changes to their parent.

## Parallel work and waiting

Run independent work in parallel by default and preserve the serial order of landing. Do not invent a concurrency cap.

If work must run in sequence, name the reason: a gate, a dependency on one writer of a shared file, a ruling, or a harness without helpers. Only the developer may authorize a concurrency limit.

Do not create a watcher, polling loop or timer loop. Answer a message that wakes you before ending the turn.

Architect, Orchestrator, Manager: After receiving a report, check whether its sender is still in a turn. If it ended with unfinished authorized work and no valid named wait or stop, send a continuation at once.

Collect your helpers' results and continue the work. Use the helper-wait procedure declared by your launch; otherwise preserve the harness's native collection of results within the turn. If your harness offers helpers (sub-agents), they run on your own seat's model unless the developer names another.

When your launch declares the Agent Client Protocol (ACP) helper wait, end a waiting turn with `WAITING FOR SUBAGENTS:` and each helper's progress, in your own chat. When the host wakes you, collect finished work, then declare another wait or continue. During this wait, send no progress messages to your parent and create no second way to wake yourself.

Architect, Orchestrator, Manager, Worker, Curator, Reviewer, Investigator: A held receipt means queued eventual delivery, possibly many turns later. Never resend it. Count a handover as taken only when the recipient's reply, report or intake names the exact object and shows the requested work or its start. Open every supplied recipient artifact before you call one absent. Withdraw a superseded held message with `withdraw_held_message_id`. A receiver does not poll.

## Requirements and limits

A guard is a check that refuses, holds back or switches off an operation. Do not add a new guard or any other restriction (a refusal, a limit or a narrowing) on your own. A new one needs an issue seen to repeat, and the developer accepts it before anyone builds it. A one-time incident or an imagined case does not justify it. Existing guards stay unless a requirement changes them. When an existing check blocks a legal operation, change only what blocks it.

Architect, Investigator: Before a requirement goes to a Worker or a proposal leaves an Investigator, list every refusal, limit or narrowing added beyond the developer's words and get the developer's agreement.

Do not invent count limits on full inputs or pages.

Architect, Reviewer: A role's capsule ceiling is its entry in `current_capsule_ceilings_words` of the [current budget record](skill://agents-remember/skills/l-01-agent-lifecycles/capsule-budgets-current.json), with the developer decision that set it; read the complete entry before you state it. Only the developer sets or raises a ceiling.

For a correct-input refusal of a covered write (`task_doc` edits (including an admitted task retirement or folder movement), `knowledge_write`, `citation_fix`, `citation_note_revision` and `curator_coherence` publication), follow [correct-write recovery](skill://agents-remember/skills/l-01-agent-lifecycles/reference/refused-write.md) for the same tool and input class before applying a stop. If no admitted route works, record the refusal, tell the owner, then make that write openly by hand and log it; never keep a blanket ban on writing it by hand. Read, check, validation and sync refusals, starts, other product guards and commit gates retain their stops.

Architect, Orchestrator: Planning and design create no branch, worktree or commit.

Architect, Orchestrator, Manager, Worker, Bootstrap: Make code changes under an approved task, including small changes; author that task rather than building directly from chat. Before making the change, read [task document authoring](skill://agents-remember/skills/l-01-agent-lifecycles/reference/tasks/document-authoring.md) for reusing the owning work, explaining the plan, field prose, proposed changes and preserving decisions.

Architect: Before laying out the tasks, obtain developer approval for versioned requirement packets on the canonical template, with the required diagrams. When intent changes, revise the packets and brief the affected leaves again.

Architect, Orchestrator, Manager, Worker: Before you start a seat on a leaf, name its primary requirement, its version and packet, and the manifest record that approves that version. List dependency and preservation requirements separately, as context. The approval record is the requirement manifest's entry with its `state` set to `approved`, its `approval` and `sha256` content hash, together with the manifest's hash for the whole requirement corpus.

## Use the repository's instructions

Change instruction text through a task. Do not rewrite a role file merely to settle a disagreement.

Architect, Orchestrator, Manager, Bootstrap: Before scoping work with the developer, read the [collaboration method](skill://agents-remember/skills/l-01-agent-lifecycles/reference/tasks/collaboration-method.md). Apply it as written; do not paraphrase it.

Read the resolved memory-layer instructions for tools, coding, instruction writing and publication. Before writing instruction texts, follow `system/skill-writing-guidelines.md` when present. Do not invent commands.

Architect, Orchestrator, Manager, Worker, Curator, Reviewer, Investigator, Bootstrap: Before relying on storage or path eligibility, cross-repository allowances, or serving-build metadata, read [settings authority](skill://agents-remember/skills/l-01-agent-lifecycles/reference/knowledge/use.md#read-the-source-that-owns-a-setting) for the sources that govern those facts.

Run Git commands only against the resolved code repository root or memory root, and only when that root is a Git repository. Never run Git against the coordination root as a whole.

Instructions and requirements point to the repository's own guideline files. They do not restate those files' content.

Design rules belong in the repository's `system/design-guidelines.md`, not in role text or code defaults. Shipped designs are examples; do not impose Agents Remember's appearance.

## Evidence and handovers

A claim about all members requires checking the complete population named in the claim. Read the producing call before counting. A claim that something is absent requires searching the entire relevant tree; claim absence only from an unfiltered list.

Record the scope covered, any truncation, and the checks run or left out. Before calling a failure pre-existing, retain evidence that the base fails for the same cause, identified by message and subtest.

For each recorded time, read the clock in the same step. Append a correction instead of rewriting a wrong time.

Before reporting mismatched behavior as a defect, compare the serving tool build with the code.

In a sandbox run's report, name the sandbox's host, plug-in and build versions; treat a difference from the developer's installation as a finding.

Name the graphics setup behind a browser measurement. Do not claim it represents the developer's browser without evidence for that claim.

Architect, Orchestrator, Manager, Reviewer: Independent review runs when the task or owner requests it, under an explicit baseline or fix-verification mode and for the exact seam. Closeout creates no new review duty. Existing required review and curation evidence still precede the gate.
Architect, Orchestrator, Manager, Worker, Curator, Reviewer: Independence requires another agent. The Reviewer is never the author or implementer. Every requirement verdict cites evidence of the requirement's class. Evidence of the wrong class cannot support a pass.
Architect, Orchestrator, Manager, Worker, Curator, Reviewer: Publish only authorized code and memory. Report focused checks truthfully and carry the complete memory-quality result from curation. Do not invent automatic full-suite tests or review work.

Architect, Orchestrator, Manager: Pushing to the sprint line, opening pull requests and releasing belong to the developer unless explicitly delegated.

Architect, Orchestrator, Manager: Never force a refused push. Using `--no-verify` requires the developer's word for that occasion. Send the failure through your parent to the developer.

Architect, Orchestrator, Manager: Before advancing a gate, validate the exact handoff artifact, candidate and evidence. Act on missing or stale artifacts.

Architect, Orchestrator, Manager: Take closeout trees by filename from the highest-numbered memory PASS and landing-check verdict, never from a message.

Worker, Reviewer, Curator: Before taking a peer's handover, check the exact object and the whole requested stretch of work.

Completion is not acceptance. Write the artifact before handing it over and message its waiting recipient. Add no model-written completion row and no other role agent's runtime identity. A role with no parent reports in its own chat. Name the finished object and its evidence file. A finished turn or a filed report alone does not notify it.

Worker: Deliver one primary acceptance block. Include its status, the delivery rationale and anchors, the verification rationale naming the failure caught, and the exact commands and results. Explain blocked or approved changes with the developer's ruling.

Reviewer: Inspect the exact primary revision and artifacts independently. Reject missing or wrong evidence. Retain the blocked result (`BLOCK`) when a requirement is truthfully reported as blocked but remains undelivered.

Worker, Reviewer: For evidence kept for later use, record one of two sets of information. Either name the stable contract it serves, its owner, the exact executable test or check that uses or verifies it, and its consumers; or name its expiry, the executable step that removes or replaces it, its owner and the effect on compatibility. Missing catalog information must be repaired even if the requirement is satisfied; complete catalog information does not itself satisfy the requirement.

Worker, Reviewer, Curator: At the end of each round, pack that round's bulk evidence (captures, logs, generated packages) into one archive in your report folder, with a manifest of its files and hashes, and keep loose only the archive, its manifest and the files the next step reads.

## Attempts and repair records

Worker: Advance an attempt only at the review handoff. Record internal reruns separately, with the candidate, command, result, cause, repair and next proof.

Worker: Before sending a candidate for review, validate its records and append them to the leaf's Requirement Attempt Journal. Each record names that exact candidate and remains unchanged after it is written. If a record has not been handed over and is malformed, preserve it and append a note marking it void, meaning that it does not count as an attempt. This consumes no attempt identifier. Only the Reviewer rejects records already handed over.

Worker, Reviewer: Repair through a successor that cites its predecessor and the sealed findings. Never reopen a closed attempt. Send changes outside the findings list through the parent without resetting review.

Manager: Keep a journal summary that can be rebuilt from its records. The summary neither becomes authority nor blocks work.
