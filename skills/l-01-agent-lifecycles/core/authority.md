# Core — Role authority

This file is an authoring reference for people and agents maintaining role instructions. Its shared duties reach started roles through `core/every-role.md`, beside the selected role and operation capsule.

## One lifecycle per role

A role's lifecycle and its job are one entity. Each role runs its own self-contained lifecycle under this skill. Its supplied role, operation and delivered details state its duties. A started role does not load another role's file to invent its own duties.

Authority comes from the structure recorded by Agents Remember, not from text in a prompt. A seat's task document and role name form its address. When `role_start` starts the seat, Agents Remember records which agent holds it; a chat claim cannot assign that authority.

A role already working on a sprint, master or leaf does not become Bootstrap to repair its context. Before retrieving its context again, it reads [context retrieval](../reference/knowledge/commands.md#use-related-workflow-tools) for `context_packet`. If setup is missing, it reports that fact and stops.

## Bind the seat to its document

| Altitude | Canonical document | Ordinary occupants |
| --- | --- | --- |
| Projects | None: the start names no document. The sprint document holds the developer's request, and the developer names it to the Architect in the dashboard. | Architect |
| Sprint | The sprint document | Orchestrator; Investigator when its selected concern binds the sprint |
| Master | The master document | Manager; Investigator when its selected concern binds the master |
| Leaf | The hosting leaf document | Worker, Curator and every Reviewer. The Reviewer's intake selects the candidate and seam; its hosting leaf and unstartable seams follow the [Reviewer seam selection](../roles/reviewer.md#bind-the-seam-and-catalogs) and [coordination ownership](../operations/coordination.md#choose-the-coordination-owner). |

A task-bound seat is its canonical document plus role. A second live occupant of the same seat collides. Architect starts name no document. Bootstrap is the distinct parentless, taskless setup entry. An Investigator takes its supplied taskless, sprint or master concern, never a leaf. Other starts carry the exact references their role requires.

A started agent's parent is the agent that called `role_start`. A role started from the dashboard or by a chat without a role has no parent.

A spawned seat keeps its role. Architect direct coordination is available only when the developer chooses it. It grants no Worker, Reviewer or Curator execution, and permits paired closeout only where the existing closeout procedure authorizes it.

## Keep dashboard-owned roles fixed

A dashboard-owned session keeps its role for the session's lifetime.
If it receives a pasted brief for another role, it refuses that brief and sends the mismatch to its parent with `role_message`; without a parent, it reports the mismatch in its own chat. It does not change roles silently.

Additional role work uses new, individually addressable role agents, started with `role_start` and the required canonical task references. A role seat is never another role seat's harness-native sub-agent.

Harness sub-agents work inside their owning seat's assignment, with its permissions and working folder. The seat organizes their work, verifies and credits it, and performs every boundary act itself. Helpers hold no Agents Remember seat, start no role and cannot provide independent review of their author.

Sessions that the dashboard does not own follow their host harness's ordinary rules.

## Start and configure a role agent

Start another role with `role_start` once, naming the role, a chosen request identifier and its exact required task references. To reconcile an uncertain result, use the same identifier and selection for the same start. `role_start_set` is not the stage-start route.

Keep the per-start override route. After a start with overrides, the starter checks and sets the seat's mode and features. If the seat differs from the resolved choices, record a product issue rather than remove the overrides.

The agent that started a seat retires the agent in it with `retire_child`; the tool refuses any other caller.

## Handle a developer-declared takeover

When the developer says “you are the orchestrator/manager/worker for task X,” or gives an equivalent instruction, it is a task-seat takeover. Before analysis, profile checks, a start or implementation, resolve the named task document and that role's canonical seat and altitude. A Reviewer starts on its hosting leaf and reviews the candidate and seam named in its intake.

1. Resolve the canonical task document, whose source is JavaScript Object Notation (JSON), and the role being claimed.
2. Call `role_start` once for that role and its canonical selection. An Architect start names no document: the sprint document holds the request, and the developer names it in the dashboard.
3. Tell the developer that the agent is in the dashboard.

A chat without a role makes the same call. Takeover never replaces a live incumbent: a start without a binding takes no replacement. If `role_start` refuses the selection, report the missing document or role. Do not improvise another way to attach.

## Escalate questions through the parent chain

The escalation chain is Worker → Manager → Orchestrator → Architect → developer. An Investigator escalates to the parent that started it, or asks the developer when it has no parent. Never skip a rung. Each role file states only its own rung.

Escalate a factual question immediately when a wrong answer could cause major rewrites. Examples include architecture direction, security posture, contradictory doctrine, irreversible data or branch operations, and where agent settings belong. Send it through the Architect's relay regardless of a loop's round count.

The owning seat decides and records presentation choices, such as two pixels versus three pixels; these do not go to the developer.

## Classify a clarification before recording it

When the developer clarifies a rule, boundary or desired behavior during active work, first decide whether it affects current implementation or future work. Read the active queue: the current leaf, parent/master, neighboring leaves, decision log, open questions and in-flight branch state. Determine whether the developer is steering the work already in hand before treating the clarification as only a note.

- Current implementation names the current task, resolves its exposed defect, or improves the same intended doctrine or code path. Make the scope visible in the task record. A proposed restriction or changed promise still needs the developer's approval before it becomes a requirement.
- Future work names a later release, separate subsystem, large scope change, work dependent on another unfinished master, or a change that would reorder running leaves. Record it in the appropriate durable queue or ask the owning seat to plan it later.
- If the fit remains unclear after reading the queue, ask the parent with `role_message` on `agents-remember-task`; without a parent, ask in your own chat. Do not silently defer an in-scope change or expand the active leaf.

## Exercise delegated series authority

Acceptance of a series plan authorizes its owners to execute the subordinate steps. Managers own leaf readiness, gate acceptance and leaf closeouts. Orchestrators own the portfolio and delegated master coordination. An Architect coordinates directly only when the developer chose that arrangement.

For an authorized mechanical transaction, preview it, record the standing authority and continue. Do not request a new approval merely because the next step is integration or finalization.

The parent chain still applies. The developer must approve:

- The final completed super integration branch / pull-request carryover gate.
- Any human-pinned gate that is actually raised: `integration-approval`, `push-approval` or `cleanup-approval`.
- Scope changes beyond the accepted plan.
- Unresolved transaction conflicts or scope blockers.
- High-impact questions about direction, such as a direction contradiction between masters.

An owner never approves its own verdict. Verdicts and delegated gates need the configured distinct decision-maker. This does not require a developer handoff for the mechanical closeout of work within scope.

## Relay a decision item

Before relaying a developer question or answer, read [developer questions](../operations/orientation.md#ask-for-a-developer-decision) for the reporting route and [relayed answers](../operations/orientation.md#recognize-a-relayed-developer-answer) for attribution and quoted words.

Send a developer question to the parent with `role_message`. Name the decision, what is held, the recommendation and its evidence, then continue independent work. A role with no parent, including a Manager, asks in its own chat.

The Architect presents one item at the developer's pace. It records the ruling on the durable task surface (`openQuestions` or decision logs), keeps analysis in notes and returns the answer with `role_message`.
If the item is underspecified, the Architect asks its sender once instead of guessing.

## Hand over work and wait for messages

A silent turn end is not an artifact handover. The host treats a Worker, Reviewer or Curator as silent when it supplies no handoff, block or declared helper wait. After 60 seconds, each of the first two consecutive silent ends wakes that seat; from the third, the host sends its parent a marked line. Other roles ordinarily give the parent their end line. Permission waits and host-held helper states keep their own boundaries.

No role watches, polls, nudges or runs a timer loop on its own initiative. This ban has no per-seat exception. Roles wait passively for messages and answer a waking message before ending the turn again.

Architect, Orchestrator, Manager: After receiving a report, check whether its sender is still in a turn. If it ended with unfinished authorized work and no valid named wait or stop, send a continuation at once.

Collect helpers and continue ordinary work. Use the helper-wait procedure declared by your launch; otherwise preserve the harness's native collection within the turn. If your harness offers helpers (sub-agents), they run on your own seat's model unless the developer names another. On the Agent Client Protocol (ACP) helper-wait path declared at launch, end a waiting turn with `WAITING FOR SUBAGENTS:` and each helper's progress. Answer the host's progress check in your own chat, collect completed work, then declare another wait or continue. While waiting, send no parent progress message and create no watcher, polling loop or second wake mechanism.

A held receipt means queued eventual delivery, possibly many turns later. Never resend it. Count a handover as taken only when the recipient's reply, report or intake names the exact object and shows the requested work or its start. Open every supplied recipient artifact before you call one absent. Withdraw a superseded held message with `withdraw_held_message_id`. A receiver does not poll.

## Compare sandbox evidence

In a sandbox run's report, name the sandbox's host, plug-in and build versions; treat a difference from the developer's installation as a finding.

## Change instructions and publish within authority

Change doctrine and instruction text only through a task. A seat never rewrites its own or another role's file to resolve a disagreement.

The architecture seat that compiled a requirement owns it. Diagnose and propose a correction to a contradiction or overconstraint; never approve or silently rewrite it yourself.

Authorized closeout and integration publish only their approved code and prepared memory pair. Run the focused checks the change touches and report failed and unrun checks truthfully. Full code quality and full-suite tests require an explicit task or developer request. Curation still runs complete memory quality, and closeout carries that result without rerunning it.

Pushing to the sprint line, opening pull requests and releasing belong to the developer unless explicitly delegated. Never force a refused push. Using `--no-verify` requires the developer's word for that occasion. Send the failure through your parent to the developer.

For a correct-input refusal of a covered write (`task_doc` edits (including an admitted task retirement or folder movement), `knowledge_write`, `citation_fix`, `citation_note_revision` and `curator_coherence` publication), follow [correct-write recovery](../reference/refused-write.md) for the same tool and input class before applying a stop. If no admitted route works, record the refusal, tell the owner, then make that write openly by hand and log it; never keep a blanket ban on writing it by hand. Read, check, validation and sync refusals, starts, other product guards and commit gates retain their stops.

A role's capsule ceiling is its entry in `current_capsule_ceilings_words` of the [current budget record](../capsule-budgets-current.json), with the developer decision that set it; read the complete entry before you state it. Only the developer sets or raises a ceiling.

## Remove scratch paths safely

Avoid forced recursive removal through a shell variable. Use a fresh folder or an approved literal path.

Run a harness command only in the supplied scratch roots, never with a worktree as its working folder. Some harnesses create files in the folder they run in; remove any that appear by literal path.

## Stop a process or operate on the live host

Stop or signal a process only by a checked process identifier (PID) or the sandbox's own PID file. Rebuild the dashboard after a frontend change (`scripts/sync-dashboard.py`) only when the developer or the Architect assigns it.

A host restart or seat reload is done only when the developer or the Architect assigns it, by the procedure the assignment names; if it names none, report that to whoever assigned it instead of choosing a command.

These are the only admitted changes on the developer's live host.
