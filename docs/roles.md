# Roles and role agents

Agents Remember runs work on a code repository through role agents. A role agent is a chat of a
harness that the host (Paseo) runs in one role for one assignment. This page names the roles and
tells how you start work and who coordinates it. It also tells what a role agent receives at its
start, how role agents send messages, and where their questions reach you.

Related pages:

- [Workflows](workflows.md) follows a task from your request to its landing.
- [Dashboard](dashboard.md) describes the pages and controls that show role agents.
- [Harnesses](reference/harnesses.md) describes the settings that choose a role agent's harness
  and model.
- [MCP tools](reference/mcp-tools.md) lists every argument and answer of the tools named here.

## The roles

The product has eight roles. A role agent holds exactly one of them.

| Role | What it does | Works on |
| --- | --- | --- |
| Architect | Speaks with you, writes the requirements and the plan for your approval, creates the task documents and hands the work to a coordinating role agent. | no task document |
| Orchestrator | Coordinates one sprint when two or more of its masters are worked on at the same time, or when you ask for one. It starts one Manager for each master. | one sprint |
| Manager | Coordinates one master: it starts the role agents of each task, decides the order of the tasks, and closes out and lands them. | one master |
| Worker | Builds one task in the task's code worktree and writes one report. | one task |
| Reviewer | Reviews one task against its requirement and records a verdict with its findings. It reviews the code change and the memory change. | one task |
| Curator | Brings the memory of the code repository in line with the reviewed code change of one task. | one task |
| Investigator | Looks into one question that belongs to no task and writes one report. It changes no code, no memory and no task document. | no task document, one sprint, or one sprint and one of its masters |
| Bootstrap | Sets up one code repository for a new user. It is the role of the first run. | no task document |

Two names stand beside the roles:

- The role settings accept a row `review-presenter` for the explainer of the review page. It is
  not a role, and no start accepts it.
- The explainer is not a role agent. [The review page and its explainer](guides/review-page-and-explainer.md)
  describes it.

## Start work

You start work in one of three places: a chat of your harness, the chat of the Architect, or the
role launcher of the dashboard.

### A chat of your harness

A chat of your harness starts the tool server. Unless the host started that chat as a role agent,
the chat holds no role. The instructions that the tool server serves tell such a chat what to do
with a request:

- It answers a question that changes no code in the chat itself. Such an answer needs no task
  document and no worktree.
- For work on a code repository it decides which sprint of that repository your request belongs
  to, writes your request into that sprint's task document and starts an Architect with
  `role_start`. The call names no task document.
- It tells you that the Architect is in the dashboard and which sprint document holds your
  request. You name that document to the Architect.
- For the first sprint of a code repository it first creates the sprint document, a master and
  the master's first task.
- It starts another role only when you name both the role and its task.

A chat without a role cannot send a `role_message`. You speak with a role agent in the agent's own
chat. The first run starts the Bootstrap role from such a chat; see
[Getting started](getting-started.md).

### The Architect

You speak with the Architect in its chat. The Architect asks which outcome you want and which
code repository it concerns. It writes the requirements as requirement packets and asks for your
approval before it creates the tasks. It answers a message of yours before it continues other
work.

A message in the Architect's chat that begins `From <role> · <task> · agent <id>` comes from a role
agent. The Architect's instructions tell it not to take such a message as your answer or as your
approval.

### The role launcher of the dashboard

The role launcher of the dashboard starts a role agent in one of seven roles: Architect,
Investigator, Orchestrator, Manager, Worker, Reviewer or Curator. It does not start Bootstrap.
[Dashboard](dashboard.md) describes its controls.

A role agent that the launcher or a chat without a role started has no parent, that is, no role
agent that started it. It puts its questions to you in its own chat.

### What a start selects

Every start names a role. The role decides which task documents the start selects:

| Role | Sprint | Master | Task |
| --- | --- | --- | --- |
| Architect | no | no | no |
| Bootstrap | no | no | no |
| Investigator | optional | optional, with its sprint | never |
| Orchestrator | yes | no | no |
| Manager | yes | yes | no |
| Worker, Reviewer, Curator | yes | yes | yes |

The selected master must belong to the selected sprint, and the selected task must be a task of
the selected master. A start of a Worker, a Reviewer or a Curator is refused with
`intent-review-open` until the task's intent review is `go`; see
[Workflows](workflows.md#the-intent-review-before-a-task-starts).

A start may name the harness (argument `agent`), the model, the effort and feature values for this
one role agent, or ask for the role's step-up. A start cannot set the permission mode. Where a
start names none of these, the role settings decide; see [Harnesses](reference/harnesses.md).

### Which role agent starts which

A role agent starts another role agent with `role_start` and becomes its parent:

- An Architect starts an Investigator, an Orchestrator, a Manager, a Worker, a Reviewer or a
  Curator.
- An Orchestrator starts a Manager, a Worker, a Reviewer, a Curator or an Investigator under its
  own sprint.
- A Manager starts a Worker, a Reviewer, a Curator or an Investigator under its own master.

The product runs one start at a time. A second call waits up to 60 seconds for the first start to
end; after that it is refused, and its answer says to call again with the same arguments.

## Who coordinates

The instructions of the roles set the duties that this section describes; the product checks who
may start whom ([Which role agent starts which](#which-role-agent-starts-which)). Three roles
coordinate, each in its own scope:

- **The Architect** keeps the conversation with you. For work inside one master it starts one
  Manager. When two or more masters of a sprint are worked on at the same time, it starts one
  Orchestrator on the sprint. It coordinates the role agents of a task itself only when you tell
  it to.
- **The Orchestrator** coordinates one sprint. It starts one Manager for each master, and it
  decides ordinary readings of a requirement and operational matters itself.
- **The Manager** coordinates one master. It decides the order of its tasks, the starts and
  replacements of role agents, and operational problems. It also decides the reading of a
  requirement within its meaning, the checks around a landing, and the closeout and the landing
  of its tasks.

A coordinating role agent sends upward only a question that needs your decision.

The Architect performs no step of a landing. The one exception is the closeout of a task when you
told the Architect in its chat to coordinate the role agents itself: it then runs the closeout
only with your approval of that commit, or with an authority that you delegated for the master and
that the task document records. The Architect may check, measure, and run tests and builds
itself, on a copy and never in the worktree of a task. The build, the review and the memory
update of a task stay with its Worker, its Reviewer and its Curator.

### The order on a task

The Manager starts the role agents of a task in this order:

1. **Build.** The Manager starts the Worker. The Worker builds the change in the task's code
   worktree and freezes a [candidate](concepts.md#candidate): it stops changing the code and
   writes a report that names the frozen state.
2. **Code review.** The Manager starts the Reviewer when the Worker freezes its candidate. The
   Reviewer records a verdict. The Worker repairs each finding and freezes again, until the review
   passes.
3. **Memory update.** The Manager starts the Curator when the code review has passed. The Curator
   brings the memory in the task's memory worktree in line with the reviewed change, and the
   Reviewer reviews that memory change.
4. **Closeout and landing.** The Manager closes out the task and lands it; see
   [Workflows](workflows.md#closeout).

With `role_start_set` the Manager starts the three in one call: the Worker opens, and the Reviewer
and the Curator are [held](concepts.md#held) until a first message reaches each. The call's
`reviewer_message` opens the Reviewer at once.

The Worker, the Reviewer and the Curator hand their work to each other directly with
`role_message`. The frozen candidate goes to the Reviewer, the verdict with its findings to the
Worker, the passed candidate to the Curator, and the memory change back to the Reviewer. The
Manager reads their reports. It hears from the three on named occasions only, such as a question
for you, a limit, a disagreement, or readiness for closeout.

Each of the three marks its own step of the task document `done`, with a one-line note, when its
part is finished.

A review takes a limited number of rounds. A round is one review of a candidate, of the code or of
the memory, that the task document records. The limit is five, unless
`orchestration.loops.defaults.maxRounds` in the user settings file names another number. A round
beyond the limit needs the approval of the Architect or yours: the Manager asks for it up the
chain of parents, and the task document records the approval and whether the Architect or you
gave it.

### The Investigator

An Investigator takes one question that belongs to no task. An Architect, an Orchestrator or a
Manager starts it, or you start it from the role launcher. It reads what it needs and writes one
report. It edits no code, changes nothing in a memory repository, changes no task document and
starts no role. The product refuses a further Investigator for a selection while eight
Investigators of that selection are not closed.

### Helpers inside a harness

A role agent may use the helpers that its own harness offers, often called sub-agents. Such a
helper is not a role agent: it holds no role and starts none. The instructions of every role say
that a helper runs on the model of its role agent unless you name another.

## What a role agent receives at its start

The first message of a role agent holds its instructions and its handover:

- The instructions are the role capsule: the text of the role, the text of one operation, and the
  rules that hold for every role. [Role capsules](reference/role-capsules.md) describes it.
- The handover names the assignment: the selected task documents, the working folders, the report
  folder, the parent, and how to reach the other role agents of the task. The first message
  carries a short form of it.

The product stores this message before it sends the start. Three kinds of files lie in the folder
`role-launch` of the role agent's report folder. For a role agent of a task, each name begins with
the id of the task, the role and the request id of the start. The request id is the UUID that
names one start, the argument `request_id` of `role_start`; a repeated call with the same request
id reaches the same role agent.

| File ending | Content |
| --- | --- |
| `.handover.txt` | the text of the first message |
| `.handover.json` | the complete handover |
| `.detail-<sha256>.md` | one file for each detail text: a reference file of the skill that a step of the instructions tells the role agent to read, copied whole |

The first message opens with one line that gives the paths of the two handover files and the
SHA-256 of the text file. The line tells the role agent to read the files again whenever its
assignment has left its context. The answer of `role_start` names the report path and both
handover files.

## Where a role agent's files lie

A Worker, a Reviewer and a Curator work in the enclosure of their task. The first start of one of
them on a task creates the enclosure. Its folder is
`<coordination-root>/worktrees/<repository>/<worktree-name>-ar/` and holds:

| Entry | Content |
| --- | --- |
| `<worktree-name>/` | the code worktree of the task |
| `memory-<worktree-name>/` | the memory worktree of the task |
| `scratch/<role>/<request-id>/` | working files of one role agent |
| `role-reports/<role>/<request-id>/` | reports of one role agent |
| `task-documents/` | copies of the task's documents, with an `INDEX.json` |
| `reference/` | reference files of the task |
| `messages` | a link to the message folder of the task, which holds the messages between its role agents (see [How a long message travels](#how-a-long-message-travels)) |
| `logs/` | the records of the tool calls made in this enclosure |

The product copies a role agent's reports to
`<coordination-root>/tasks/<repository>/<master-folder>/notes/reports/<role>/<request-id>/`. A task
with a direct-execution grant has no enclosure; see
[Workflows](workflows.md#direct-execution).

An Architect, an Investigator, an Orchestrator, a Manager and the Bootstrap role agent work in the
workspace root. Each start gets its own folder
`<workspace-root>/.agents-remember/role-executions/<request-id>/` with the folders `role-reports`
and `scratch`.

## Messages between role agents

A role agent sends a message to another role agent with `role_message`. It names the recipient by
`agent_id`, or by `role` together with the task documents.

A role agent's own chat text reaches no other role agent. A role agent's results, and what it
waits for, travel by `role_message`.

### How a long message travels

A message has a short part and a full part. The argument `text` is the short part. It wakes the
recipient: it says what the message is about and what the recipient does with it. The full part
travels in a file, through two arguments:

- `body` takes a text of any length.
- `attach` names existing files.

The product files every message whole in one message file. The file holds the sender, the
recipient, the text, the body, and each attached file with its SHA-256.

The recipient receives these lines in its chat:

1. One line that names the sender: `From <role> · <task> · agent <id>`.
2. The text.
3. For a message with a body or an attachment, a last line that names the message file:
   `Message file: <path> (SHA-256 <hash>)`.

The recipient opens the named file and reads the full part there.

The argument `text` holds at most 1,000 characters. The key
[`orchestration.roleMessageLimit`](reference/settings-json.md#orchestrationrolemessagelimit) of
the settings changes this number. Only the text counts: the line that names the sender and the
line that names the message file do not. A longer `text` is refused with `message-too-long`, and
nothing is delivered. The refusal names the length of the text and the limit. It tells the sender
what to do: send a shorter `text`, and put the full part into `body` or `attach`.

The product files each message in the message folder of the narrowest task that sender and
recipient share. Two role agents of one task share `notes/messages/<task-slug>/` in the folder of
the task's master. Other pairs share `notes/messages/` in the folder of their master or sprint.
The answer of the call names a detail file in `log`. That file holds the whole result of the
call, with the path of the message file in `messageFile` and its hash in `messageFileSha256`.

### What "held" means

A held message is stored for a recipient that cannot take it at that moment. The product delivers
it, in the order taken, when the recipient can be written to. The sender does not send it again.

The answer of the call carries the status `held`, the reason in `heldReason` and the id of the
stored message in `heldMessageId`. A message is held in these cases:

- the recipient waits at a permission prompt, where its harness asks for your permission before
  it acts;
- the start of the recipient has not finished;
- the recipient is in a turn that the host cannot write into (a turn is the stretch from a message
  that a role agent takes to the end of its answer);
- earlier messages still wait for the recipient;
- the host did not answer;
- the usage limit of the recipient's [model provider](concepts.md#model-provider) stops it.

The sender withdraws a held message with `withdraw_held_message_id`.

A message to a recipient in a turn enters the running turn where the host can steer the
recipient's harness. Where the host replaced the running turn to take the message, the answer
carries `taken` with the value `replaced` and a warning.

One recipient holds at most twenty messages. A further message is refused with `recipient-busy`.

### Waiting for the answer

Without `wait`, the call returns when the host has accepted the message. With `wait`, the call
returns when the turn that consumed the message ends, or after `timeout_seconds` (300 unless the
call names another value, at most 1,800). Neither answer says that the recipient agreed with the
content.

With `wait`, the answer carries the final text of the recipient's turn in `text`. A reply that is
longer than the limit of the text is not refused. The product files the reply whole as a message
file in the same message folder, and the answer says `textTruncated: true`. The answer carries
the reply up to the [size bound of a tool answer](reference/mcp-tools.md#tool-answers): a reply
beyond that bound arrives as its beginning. The detail file that `log` names holds the whole
reply, and it names the reply's message file in `messageFile`.

## Where a question for you arrives

A role agent with a parent sends a question that needs your decision to its parent with
`role_message` and keeps working. Each parent passes it upward: Worker, Manager, Orchestrator,
Architect. You meet the question in the chat of the role agent at the top of that chain, which is
the Architect unless you started another role agent from the role launcher. A role agent without
a parent asks you in its own chat.

The Architect presents one question at a time and records your ruling. Your answer travels down
the chain with your own words in quotation marks.

The [attention bell](concepts.md#attention-bell) in the head of the dashboard's task list does not
show questions. It shows:

- a role agent that waits for a permission answer;
- a message held for a role agent;
- a task that has had no role agent in a turn for 15 minutes;
- a task that landed or was abandoned;
- drift in a code repository, and a blocked start of a worktree.

You answer a question and a permission prompt in the chat of the role agent; an entry of the bell
leads to that chat. When a role agent waits at a permission prompt, each role agent above it
passes a notice upward. The role agent at the top tells you which chat to open and names every
waiting role agent by role, task and agent id.

## When a turn ends

The host's plug-in tells a parent that a turn of one of its role agents has ended. The parent
receives one line for each ended turn. The line names the role agent, its task, the last line of
the role agent's own text shortened to 300 characters, and the place of its newest report:

```text
turn ended: <role> <agent id> · <task>; last line: <text>; newest report: <path>
```

The parent receives no line when the turn delivered a `role_message` to the parent, and none
while the role agent waits at a permission prompt. A role agent that has one of its own role
agents in a turn gives its line when that turn has ended. A parent that is in a turn receives the
line when it is free. When the parent of a Worker, a Reviewer or a Curator is archived, the line
goes to the current Manager of the task's master.

### A turn that ends without a hand-off

A [hand-off](concepts.md#hand-off) is a delivered `role_message` or a write to a task document.
When a Worker, a Reviewer or a Curator completes a turn without a hand-off, the parent hears
nothing at first:

1. The plug-in waits 60 seconds. A new turn of the role agent inside that time cancels the wait.
2. The plug-in sends the role agent a message that begins
   `[ar-plugin] turn ended with no hand-off:`. The message asks it to go on with its stage or to
   send its parent what blocks it.
3. The plug-in sends at most two such messages in a row. From the third such end on, the parent
   receives the turn-end line, marked `silent end <n> in a row`.

The plug-in sends no such message to an Architect, an Orchestrator or a Manager. It sends none
to a role agent whose own helper still runs, and none to a role agent that a provider limit
stops. It messages such a role agent once, 60 seconds after the end of the limit that the model
provider reports.

### Declared waits

A role agent that ends its turn to wait says so in the last message of the turn:

- A line that starts `WAITING FOR SUBAGENTS:` declares a wait for helpers of its harness.
- A line that starts `WAITING FOR BACKGROUND:` declares a wait for a process that the role agent
  started.

Such an end is not an end without a hand-off, and the parent receives no line for it. The plug-in
asks the role agent for progress three minutes later, or after the minutes that the line names
with `check in <N> minutes`, from 1 to 30. The check begins `[ar-plugin] subagent wait:` or
`[ar-plugin] background wait:`. While the wait is open, the dashboard shows the task with the
status `waiting for subagents` or `waiting for background`.

## Close a role agent

- The role agent that started a Worker, a Reviewer or a Curator closes it with `retire_child`. The
  call names the task document, the role and a reason. The host archives the role agent without
  giving it a turn; a role agent in a running turn is archived only with `interrupt_turn`.
- A message to an archived role agent is refused.
- When the worktree of a task is finalized, cleaned up or abandoned, the product archives the role
  agents of that task on the host.
- The dashboard has no control that closes a role agent. Its role launcher resumes a closed
  role agent with "Revive"; see [Dashboard](dashboard.md).
