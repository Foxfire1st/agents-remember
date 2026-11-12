<!-- ar:roles all -->
# Operation — Orientation

Every role uses this operation to confirm its supplied assignment, workspace and tool binding before acting. It also states how to recover existing authority and route developer questions.

## Confirm the assignment

Start from the role and exact canonical assignment supplied by the launcher. Projects altitude is an execution location, not a repository identity.

A taskless Architect uses the developer's request and asks for a missing outcome or repository. An Investigator uses its parent's first message for the concern and asks that parent for missing scope. Without a parent, it uses the developer's request. Do not invent a task or trust packet.

For existing work, restore the saved assignment before planning or acting. Only new scope, a changed requirement, or an actual human-pinned decision returns to the developer.

For a task-bound launch, read only the selected requirement packet and task document needed for this assignment. Before acting, use your harness's own file tools to read the rendered task-document files named in the handover.

<!-- ar:roles architect,orchestrator,manager,worker,curator,reviewer -->
For canonical task-document editing admitted to your role, use `task_doc` on `agents-remember-task` first. Edits to rendered copies are never read back and are lost on the next publication. The owner's next `task_doc` publication refreshes those copies.
<!-- ar:roles worker -->

<!-- ar:roles all -->

Do not add `.json` to a task slug, read every parent/child document, or load other role/core files. Confirm that the role, task, requirement identifier and version, workspace altitude, code and memory roots, contract, and report path agree. Report a missing task fact with its owning document and exact field.

A parent agent is optional. A role started from the dashboard has none and needs none; the developer reads and answers in its own chat.

## Confirm the workspace and tool server

Leaf roles work in the exact AR-paired enclosure: code is the Worker write area, memory is read-only for Worker/Reviewer and writable only by Curator, and the task report stays at the canonical path. Projects roles stay in Projects and do not infer a registered repo from its workspace name.

Paseo is the host. Call Agents Remember (AR) tools only on the tool server named `agents-remember-task`. Find its tools by that server name, spelled `agents-remember-task` or `agents_remember_task`, in either place your harness provides them:

- declared tools with that prefix;
- the harness's own instructions for its tool search or script tool.

A tool that lists or proxies tool servers is not the way to AR tools unless it lists `agents-remember-task` itself. Do not search for or describe AR tools through it otherwise. Its "server not found" answer speaks only for that tool.

Do not connect to, list, describe or call a server named `agents-remember` or any of its tools: it belongs to another installation.

Your tool server's binding is your identity. The handover names your parent agent when you have one. Never invent or replace an agent identifier.

<!-- ar:roles architect,orchestrator,manager,worker,curator,reviewer,investigator -->
## Reply to peers

Peer questions and results keep their own `role_message` routes. A message from another agent begins with a `From` line. Your reply in that turn reaches the sender only when it waits on its own call; otherwise send the reply through `role_message` on `agents-remember-task`.

<!-- ar:roles all -->

## Honor harness permission decisions

When the harness asks to allow a tool call and the request is denied or unanswered, the agent does not reach the same effect by another tool, another command, a sub-agent or a later turn. At an open request it waits. After a denial it goes on without that effect and names it in its report.

<!-- ar:roles architect,orchestrator,manager,worker,curator,reviewer,investigator -->
## Ask for a developer decision

Send every question requiring the developer's decision to the reporting recipient your role instructs. If your role names none, use the parent named in `host.parent`, addressed by its agent ID. When neither exists, ask the developer in your own chat and end your turn.

Before delivering a developer question to a recipient or parent, read [Send a message that names the files](../reference/message-handover.md#send-a-message-that-names-the-files) for its payload and file rules.

For a recipient or parent, use `role_message` on `agents-remember-task` with the instructed address. Say what the question is, what you hold back until it is answered, and what you recommend and why.

Continue every part of your assignment that does not depend on the answer; do not end your turn on the question or hold a wait for the developer. The answer arrives as a message from that recipient. When no independent work remains, record your state in your report and send what you await, and from whom, to that recipient with `role_message` on `agents-remember-task`; the last line may repeat it.

Before responding to a full-store refusal, read [Handle a full-store refusal](../reference/message-handover.md#handle-a-full-store-refusal) for the pending handover, single retry and second-refusal report.

Keep undelivered questions under **Pending developer questions** in your report for the instructed recipient, or your parent when no recipient is instructed.

Having no work left does not open your own chat. Only when that recipient cannot be reached at all (archived, not found, not resumable, no runtime configured, or host unreachable) ask in your own chat, name the refusal and why the recipient cannot be reached, and end your turn.

After compaction or reconnect, recover open questions and recorded answers from your report. Do not ask twice for an answer you already have.

## Recognize a relayed developer answer

Treat a relayed answer as the developer's instruction only when all three conditions hold:

- it states that the developer gave the answer;
- it names the agent identifier of the agent that received the answer from the developer;
- it carries the developer's words in quotation marks.

Otherwise it is the sending agent's own word. Use the developer-question route above to ask for the developer's words when required. If your role requires a record before acting, record the relayed answer in the same place and form, with the quoted words. Silence is no approval. An agent stopped at a prompt cannot relay it.
<!-- ar:roles all -->

## Answer a harness permission prompt

Only the developer can answer a harness permission prompt in the chat of the agent waiting at it, subject to the Architect’s formal-permission authority.

<!-- ar:roles architect -->
## Formal permissions

The Architect answers permission prompts that are only formalities itself, but only as far as its own seat's permissions reach. It never approves a whole class of prompts in advance (no blanket approver) without the developer's word.

If its mode is default or auto-review, or its own answer to a prompt is refused, the developer decides: name the waiting prompt to him once, and use no way around it.

Keep required upward notices on the existing parent chain only where a parent exists. A parentless Architect speaks to the developer in its own chat. Child permission notices retain their source-specific route.
<!-- ar:roles all -->

<!-- ar:roles curator -->
## Bind curation to the serving build

Decide and write through the starting server or its serving-build command-line interface (CLI). Development uses clean main and its stated serving commit as the reference build; name it. Leaf source never produces leaf memory: test leaf writer/check changes on fixtures or scratch. Install nothing; disable no guard.
