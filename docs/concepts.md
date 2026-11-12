# Concepts

This page defines the terms the documentation uses for the parts of Agents Remember. Every other
page uses a term in the meaning this page gives it. The terms stand in five groups: the programs,
the settings and folders, memory, roles and tasks, and review.

## The programs

### Agents Remember

Agents Remember is the product. It is the Python package `agents-remember-mcp`, which installs two
commands: `agents-remember` and `agents-remember-mcp`.

### Tool server

The tool server is the MCP server that the command `agents-remember-mcp --config <settings file>`
starts. A harness starts it and calls its tools. The tool server also serves the skill of the
package to the harness as MCP resources. The [MCP tool reference](reference/mcp-tools.md) lists
the tools.

A role agent has a tool server of its own under the name `agents-remember-task`. The host starts
it together with the role agent.

### Tool answer

A tool answer is what a tool of the tool server returns to its caller: the outcome, what the
caller needs for its next step and, when the whole result is larger, in `log` the place of the
file that holds it. An answer has a bound in tokens, and an answer over its bound arrives as a
short form with `overflow: true`. [Tool answers](reference/mcp-tools.md#tool-answers) tells the
bounds.

### Skill

A skill is a folder of instruction files that the package holds and the tool server serves to a
harness as MCP resources; the resource `skill://index.json` lists the skills. Agents Remember
ships one skill, `l-01-agent-lifecycles`, which holds the instructions of the roles.

### Role instructions

The role instructions are the files of the skill `l-01-agent-lifecycles`: one role file for each
role, the operation files, the instructions for a chat without a role, the report templates and
the review criteria. A role agent receives its part of them as its role capsule. The
[Skills Reference](reference/skills.md) describes the files.

### Harness

A harness is a coding-agent program: Claude Code, Codex, Cursor, Hermes, Pi or OpenClaw. The role
settings and the host name a harness by its harness id, for example `claude` for Claude Code. A
harness has two uses. The user registers the tool server in a harness and calls the tools from a
chat. The host starts a harness for each role agent; the role launcher of the dashboard calls a
harness that the host can start an "agent". The page [Install](install/README.md) tells the
registration for each harness, and [Harnesses and role settings](reference/harnesses.md) lists the
harness ids and tells which harnesses the host can start.

### Model provider

A model provider is the service whose models a harness calls. Its usage limit is the provider
limit: while a role agent has reached it, a message to that role agent is held with the reason
`provider-limit`.

### Chat

A chat is the conversation of one agent in a harness. The host shows the chats of the role agents
and of the explainer, and the dashboard shows them in the chat frame.

### Host

The host is the program that runs role agents and shows their chats. It is Paseo. The help of
`agents-remember paseo` and some messages call the running host the daemon, and keys and codes of
the product carry the name Paseo, for example `paseoRuntime` and `no-paseo-runtime-configured`.
Agents Remember installs a pinned version of Paseo and starts it; the block `paseoRuntime` of the
settings file describes the installation. To **provision** the host is to install that version,
write the host's configuration, install the plug-in and start the host. The **host's home** is the
folder that the key `paseoRuntime.home` names: the host keeps its configuration there, and Agents
Remember puts the plug-in into it. [The host](host.md) tells how to check, stop and provision it.

### Host provider

A host provider is the host's entry for one harness that it can start. The host has built-in host
providers; the user adds another as a **provider entry** under `paseoRuntime.providers` in the
settings file. `agents-remember paseo status` lists the host providers under `providers`.

### Plug-in

The plug-in is `ar-plugin`, a plug-in for the host that Agents Remember ships and installs.
[The host](host.md#the-plug-in) tells what it does.

### Dashboard

The dashboard is the local web page that `agents-remember dashboard` serves, by default at
`http://127.0.0.1:8765/`. It has four tabs: "Chats", "Operations", "Knowledge" and
"File Viewer". The dashboard's server is the process that `agents-remember dashboard` starts and
that serves the page. [The dashboard](dashboard.md) describes each tab.

### Attention bell

The attention bell is the control in the head of the dashboard's task list that lists what waits
for the user. Its label reads "Attention: N waiting" or "Attention: nothing waiting".

### Chat frame

The chat frame is the frame in which the dashboard shows the host's page with the chats.

### Chat sidebar

The chat sidebar is the right column of a dashboard page that holds a chat. The control that shows
or hides it is named "Chat sidebar" on the review page and "River and chat sidebar" on the tab
"Operations".

### Serving build

The serving build is the build of Agents Remember that a running tool server or the dashboard's
server loaded when it started: its version, its commit and a digest of the loaded code. The tool
`server_info` reports it in `servingBuild`. The dashboard shows it as a stamp in its top bar; the
hover text of the stamp begins "Serving build".

### Benchmark runner

The benchmark runner is the module `agents_remember.benchmarks.runner` of the package. It lists
and prepares benchmark cases, runs them with Codex and analyzes the results. The tool server
registers no benchmark tool; [Benchmark Methodology](benchmarks-methodology.md) describes the
runner's use.

## Settings and folders

### Settings file

The settings file is `~/.config/agents-remember/agents-remember-settings.json`. It holds the tool
server's settings, among them the coordination root, the workspace root, the code repositories,
the host block `paseoRuntime` and the limits of the tool log; the
[settings reference](reference/settings-json.md) describes the file. The tool server reads the file
that `--config` names. When `XDG_CONFIG_HOME` is set, the file lies in
`$XDG_CONFIG_HOME/agents-remember/`. Messages and help texts of the product call it the MCP
settings file. It is one of four settings files; the three others always carry their qualifier:
the user settings file, the repository settings file and the memory settings file.

### User settings file

The user settings file is `~/.config/agents-remember/settings.json`, beside the settings file. It
holds the role settings: for each role the harness, the model, the effort and the permission mode,
under `orchestration.roles.<role>`. That object is the **role row** of the role. The tool
`user_settings_init` creates the file with one harness for each role.

### Workspace root

The workspace root is the folder that holds the code repositories. The settings file names it in
`workspaceRoot`.

### Coordination root

The coordination root is the folder in which Agents Remember keeps its working state. The settings
file names it in `coordinationRoot`. It holds the task documents under `tasks/`, the memory
repositories under `memory-repos/`, the enclosures under `worktrees/`, and notes, logs, reports and
caches. [Architecture](architecture.md) shows the folders.

### Code repository

A code repository is a Git repository that the settings file lists under `repositories`. Its key
there is its id, and its folder is `<workspace-root>/<id>`. Tools name a code repository by this id
in the argument `repo_id`. Where a page writes "the repository" without a qualifier, it means the
code repository.

### Memory repository

The memory repository of a code repository is the Git repository
`<coordination-root>/memory-repos/ar-<id>`. It holds that code repository's memory: the cards under
`onboarding/`, the knowledge records under `knowledge/`, local copies of domain documentation
under `docs/`, and the repository's own instruction files and the memory settings file under
`system/`. Tools and messages call this placement `external`: the memory lies outside the code
repository, and the product knows no other placement.

The memory is a repository of its own, so it can be branched, committed and reviewed separately
from the code it describes. The folder in which a tool reads or writes memory is the
[memory root](#memory-root).

### Memory root

The memory root is the folder in which a tool reads or writes memory: the task's memory worktree
for a task with an enclosure, and the folder of the memory repository otherwise. Answers of the
tools name it in `memory_root` or `memoryRoot`, and a path that begins with `<memory-root>` lies
in it.

### Repository settings file and memory settings file

Two more settings files belong to one code repository. The repository settings file is the
optional file `<code-repository>/system/settings.json`; it holds role settings that go over the
user settings file. The memory settings file is `<memory-root>/system/settings.json`; it decides
which source files are eligible for a card, which other repositories the context may include, and
the landing workflow.

### Path rules

Path rules are the entries of `onboarding.pathRules` in the memory settings file. They decide which
files of a code repository are eligible for a card: the rules are tried in their order, and the
first rule whose `include` matches a file decides. [Path Rules](reference/path-rules.md) is the
reference.

### Search provider

A search provider is one of two search programs whose files the install copies from the package
into `<coordination-root>/providers/`. No part of Agents Remember starts, indexes or queries a
search provider.

### Task folder

A task folder is a folder of task documents under the coordination root. The task folder of a code
repository is `<coordination-root>/tasks/<id>/`; a sprint and a master each have a task folder of
their own inside it. Answers of the tools name a task folder in the field `task_root`.

### Tool log

The tool log is the record of every tool call: the tool server appends one line for each call to
the file `ar-tools-<YYYY-MM-DD>.jsonl` of the day. A call of a role agent on a task with an
enclosure is logged in `<enclosure>/logs/`. Every other call is logged in the **transcript root**,
the folder that the key `transcriptRoot` of the settings file names, by default
`<coordination-root>/logs/mcp/`.
The help of `agents-remember tool-log-stats` calls such a folder a tool-call log folder.

### Scratch folder and report folder

Each role agent gets two folders with its start: the scratch folder for its working files, and the
**report folder** for its reports and the files of its start. For a Worker, a Reviewer or a
Curator they lie in the enclosure, as `<enclosure>/scratch/<role>/<request-id>/` and
`<enclosure>/role-reports/<role>/<request-id>/`. For every other role agent they lie under
`<workspace-root>/.agents-remember/role-executions/<request-id>/`.
[Runtime Layout](reference/runtime-layout.md) lists the paths.

### Context resolution

Context resolution is the step in which the product finds the facts of one code repository: its
memory root, the settings files that apply, its task folder and the cross-repository context that
is allowed. A harness asks for it with the tool `context_packet`;
[Product Context Resolution](reference/resolver-c08.md) is the reference.

## Memory

### Card

A card is a Markdown file in the memory repository that describes one source file or one
[route](#route). The tools call a card an onboarding card, and the tab "File Viewer" of the
dashboard lists the cards under "Onboarding".

The card of a source file lies under `onboarding/` at the path of the source file; that place is
the **mirrored path** of the source file:

```text
<code-repository>/src/foo/bar.ts
<coordination-root>/memory-repos/ar-my-repo/onboarding/src/foo/bar.ts.md
```

The card explains what the code does not make obvious: invariants, intent,
boundaries, conventions, edges to other repositories and risky assumptions. Its Markdown is
paired with `onboarding/src/foo/bar.ts.json`: a structured [sidecar](#sidecar)
holding references, realizations and, for tests, proofs. The prose describes
current truth; shared invariants, families and decisions live in records under
`knowledge/`, rather than being copied into each card. See the [knowledge-system
reference](reference/knowledge-system.md).

### Route

A route is a folder of a code repository that has a card of its own, the route overview: the
mirrored folder under `onboarding/` holds the file `overview.md`. The root route is `.`, the whole
repository. The route that governs a source file is the nearest route above the file, and a family
names the routes in which its members are realized.

### Route overview

A route overview is the card of a route: the file `overview.md` in the route's folder under
`onboarding/`. The root overview gives orientation for the whole repository. Larger repositories
can also use route-local overviews in the mirrored folders under `onboarding/` when a package,
module, or source slice needs its own durable context.

Each source file has a card of its own. Route-local overviews add structure; they do not replace
the card of a file when file-specific facts matter.

The Curator writes an overview in a fixed order. The title comes first, then an optional section
"Governing Overview", then the purpose of the repository or the area. The overview next explains
how the area works and where its boundaries lie. All numbered citation lines stand together at the
end. The Curator puts a change into the section that owns its meaning.

### Route index

A route index is the generated file `overview.index.json` of a route; no commit holds it. The tool
`route_index_refresh` writes the route indexes of a task's memory worktree anew. An agent needs no
index to find a card: the card of a source file lies at the mirrored path.

### Sidecar

A sidecar is the JSON file that is paired with the Markdown file of a card, for example
`onboarding/src/foo/bar.ts.json` beside `onboarding/src/foo/bar.ts.md`. The sidecar of a file's
card holds references, realizations and, for a test file, proofs. A reference belongs to one
numbered citation of the card's prose and names what the citation points at: a place in the code
or in a test, which it holds as an anchor, or a record, a requirement or an outside document. A
realization and a proof each hold an anchor. The sidecar of a route overview is `overview.json` and holds references.

### Anchor

An anchor is the record of one place in a code file or a test file: the path, the **locator** of
the anchor (a symbol, a line range or the whole file), the Git blob of the file and a hash of the
content at that place. A realization and a proof each hold one, and a reference holds one for each place in the code or in a test that it names: the
anchor of a reference is a **reference anchor**. A reference to a record, a requirement or an outside document holds none. An anchor is **stale** when
the code at that place differs from the recorded content (for a line range also when the lines only moved), when its symbol does not name exactly one
place, or when the file is absent. Where memory is committed or landed, a reference anchor is stale as well when only the Git blob of its file changed.

### Entity catalog

An entity catalog is the file `entities.md` at the root of `onboarding/`. It describes entities
that carry different names in different layers of the code, and the files that define each.

### Knowledge record

A knowledge record is a JSON file under `knowledge/` in the memory repository. It holds one shared
truth with a stable ID: an invariant, a family, a decision, or one of the other kinds. The other
kinds are incident, assumption, limitation, failure mode, scenario, diagnostic, term, weakness and
dependent. A **decision record** holds one decision: its context, at least two alternatives of
which exactly one is chosen, and the condition under which to reconsider it. The
[knowledge-system reference](reference/knowledge-system.md) describes every file.

### Invariant

An invariant is a knowledge record that states one rule about the code. The record owns the
statement; the sidecars of cards name the code that realizes it and the tests that prove it.

### Family

A family is a knowledge record that groups invariants as its members and names the routes in which
its members are realized.

### Realization and proof

A realization is an entry `realizes` in the sidecar of a card: it says that the anchored code
realizes an invariant. A proof is an entry `proves` in the sidecar of a test file's card: it says
that the anchored test demonstrates one facet of an invariant. The review page prints
"realization claim" for a realization.

### Knowledge index

The knowledge index is an SQLite cache that Agents Remember derives from the text of one memory
tree and keeps outside Git, by default under `<coordination-root>/runtime/knowledge-index/`. It is
built anew when it is absent; a write of knowledge changes the text and never the index.
`agents-remember knowledge-index` builds or reuses it and reports it.

### Knowledge foundation

The knowledge foundation of a code repository is its first set of knowledge records. A Curator
writes it in a regular task that the Architect plans; `memory_init` reports in its block
`knowledge` whether records exist. A **foundation wave** is a write of knowledge records that
belongs to no task: `agents-remember knowledge-bootstrap` makes one, and its judgment rows go to
`knowledge/history/<wave>.json`.

### How an agent finds memory

An agent that holds a source file finds its card at the mirrored path under `onboarding/`. No
search, ranking or index stands between the file and its card.

Two tools name the memory that belongs to source files. `read_ar_files` names, for up to 20 paths,
the governing card and the knowledge records of each path. `knowledge_read` names the invariants
and families attributed to one source file. Both tools return names and locations, never text; the
agent opens the named files itself. An agent that knows a concept but no file searches the code and
the cards as text first. The skill holds the
[knowledge-use guidance](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/knowledge/use.md).

### Drift check

**Drift** is the difference between what the sidecar of a card recorded about a place in the code
and the code as it is; the drift check measures it. Realizations, proofs and the references to a place in the code or in a test carry
anchors in JSON sidecars. Currentness compares those recorded anchors with the selected
code tree. Read the card beside its sidecar and source; a card’s prose alone does
not establish that an anchor still names the current code.

A drift check is a call of `context_packet` with `include_drift`; without the argument nothing is
measured. Its result is the **drift summary**: it counts reference anchors as `current` or `stale`
and reports validation findings and unreadable sidecars. Separately, knowledge reads report
invariant currentness as `current`, `stale`, `unverifiable`, or `unrealized`, using
the recorded anchor content at the selected code tree. A stale invariant stays in the answer,
with the state and the reason of each of its entries that is not current.

A stale card is a card whose sidecar holds a stale anchor. It can still be read directionally
when that trust level is explicit, but it should not silently become current truth.
[Memory Quality Control](reference/drift-c02.md) is the reference.

### Approval-gated memory

Cards and knowledge records hold approved current state. They hold no plan and no speculation;
plans stand in task documents.

That means:

- a task is built against requirement packets that the user approved
- no role agent starts on a task while its intent review is open
- the Curator writes memory after the code review of the task has passed, not before
- closeout commits memory only when the knowledge gate passes on the exact code and memory trees

### Knowledge gate

The knowledge gate is the check that decides whether a closeout or a landing may commit. It
computes the worklist and validates the exact code tree and the exact memory tree; an open
worklist item or a refusing validator violation refuses with `knowledge-gate-refused`. The refusal
text and the descriptions of the closeout tools call it the invariant gate.

### Baseline

The baseline of a memory repository is the first commit of its content. The message of that
commit records, in the trailer `Code-Commit:`, which commit of the code the content describes.
The tools call it the first attributed baseline.
[Adopt existing memory](guides/adopt-existing-memory.md) describes the adoption.

### Knowledge Judgments and Publication

`knowledge/layout.json` is the **layout marker** of a memory tree; `memory_init` writes it into a
new memory repository. A record's Git history
records changes to its meaning; Curator-authored rows in
`knowledge/history/<owner>.json` record judgments about the changes that a task, a foundation wave
or a `crossing` sync made. Closed history files remain frozen; a reopened task uses an
attempt-qualified successor file.

### Judgment row

A judgment row is one row of a history file `knowledge/history/<owner>.json` in the memory
repository. The Curator writes it: it records the Curator's judgment of one change, and a current
row answers an item of the worklist. The review page lists the rows under "Judgment rows:".

### Worklist and knowledge gate

A worklist compares base and candidate code and memory trees. Its items identify
knowledge the changes reach; a current satisfying judgment row answers each item.
The mandatory knowledge gate recomputes the worklist and validates the exact
candidate trees. Open items, unreadable inputs and refusing validator violations
block publication. These checks do not invent the Curator's judgment.

The tool `memory_quality_check` computes the worklist of a task and names it in `knowledgeWorklist`.

### Validator

The validator checks a memory tree against the rules of the knowledge system. Every step that
commits memory runs it first, and a refusing violation stops the commit; no argument skips it. The
tool `knowledge_integrity_check` and `agents-remember knowledge-validate` run it on request.

### Curator's checklist

The Curator's checklist is the file `<enclosure>/reports/curator-memory-quality.md`, with a JSON
file of the same name beside it. A full run of the tool `memory_quality_check` on a task writes it
anew, so the Curator has one current list of what the check found in the task's memory.

### Coherence record

The coherence record is the record of one task that the Curator prepares and publishes with the
tool `curator_coherence`. The task is ready for closeout only when the record validates; until
then `memory_quality_check` reports `coherence-required`.
Some texts of the product call it the curator-coherence record.

### Hand-off list

A hand-off list is the list of items that a Worker or a Reviewer hands the Curator of a task; its
writer is the **producer** of the list. The Curator gives a hand-off list to the tool
`knowledge_write` or to `agents-remember knowledge-ingest`, which write its items into the task's
memory worktree. `agents-remember handoff-check` checks the lists against a candidate and writes
nothing.

### Carryover

Carryover copies cards and knowledge records from the memory of a source branch into the memory
worktree of one open task. It is no Git merge, and it commits nothing: the closeout of the task
commits the result. The tools `memory_carryover_plan` and `memory_carryover_apply` perform it, and
[Carry Memory From a Branch](guides/carryover-from-branch.md) describes the steps.

### Memory Attribution Cache

The memory attribution cache is the file `memory.md` at the root of the memory repository. Every
commit of memory content names, in the trailer `Code-Commit:` of its message, the commit of the
code that the content describes. Agents Remember computes `memory.md` from these trailers: the
file lists each pair of code commit and memory commit, newest first. Git ignores the file, because
Agents Remember adds the line `/memory.md` to the memory repository's `.gitignore`. No memory
commit holds the file, and no closeout or landing takes a decision from it: the code commit and
the memory commit remain the durable pair. Answers of the tools name the file in `ledger_path`.

Worktree start, closeout and landing take the pairs from the commits. A missing or outdated
`memory.md` does not block them, and it is no evidence that a baseline exists or is absent.
Baseline adoption, closeout, `worktree_sync` and `direct_landing` write the file anew after their
memory commit; `worktree_sync` is the tool that pulls the official line into a task's worktrees.

These reads use the content of the file:

- `context_packet` with `include_freshness` reports whether the file maps the checked-out code
  commit.
- The cross-repository context includes the memory of another repository only when that
  repository's `memory.md` can be read; otherwise it includes that repository's code only.
- Closeout reports how the file differed from the pairs it computed.
- The dashboard's server reads the newest pairs of the file.

### Cross-repository context

The cross-repository context is what a call may read of another code repository: its code, and
its memory where the entry allows it. The entries stand under `crossRepo.allow` in the memory
settings file, and each names the branch on which the other repository is expected. A repository
on another branch is left out. Its memory is included only when its memory repository exists, is
on the expected branch and holds a readable `memory.md`.

## Roles and tasks

### User

The user is the person who installs Agents Remember and works with it: the user talks with the
Architect, approves requirement packets and reviews changes. A page that gives instructions
addresses the user as "you". Texts, fields and folders of the product call the user the developer,
for example the value `developer` that records who gave an approval, and the folder
`developer-messages`.

### Role

A role is one of the eight roles Architect, Investigator, Orchestrator, Manager, Worker, Reviewer,
Curator and Bootstrap. A role fixes what an agent does and what it may write:

- The **Architect** talks with the user, writes requirements and plans, and delegates.
- The **Orchestrator** coordinates one sprint.
- The Manager coordinates one master and its tasks, and performs closeout and landing.
- The Worker builds one task in its enclosure.
- The Reviewer reviews one task independently and gives a verdict.
- The Curator writes the memory that the change of one task needs.
- The Investigator looks into one scoped concern and writes one report.
- The **Bootstrap** role sets up one code repository for a new user.

[Roles and role agents](roles.md) describes each role and who starts whom.

### Role agent

A role agent is a chat of a harness that the host runs in one role for one assignment. The tool
`role_start` or the role launcher of the dashboard starts it. Its first message carries the role
capsule and the facts of its assignment. A role agent has an agent id; the answer of `role_start`
names it in `agentId`, and tools take it in `agent_id`. Some codes and labels of the product say
"seat" for a role agent or for its place in a task, for example `held-seat-unopened`. An
**archived role agent** is one whose chat the host has archived, for example after the tool
`retire_child`; a message to it is refused with `recipient-archived`.

### Chat without a role

A chat without a role is a chat that the user opens in a harness and that no role start launched.
It answers questions, and it starts a role agent with `role_start` when the user asks for work on
a code repository.

### Role launcher

The role launcher is the row of the dashboard's tab "Chats" from which the user starts a role
agent in one of seven roles; it offers no Bootstrap. The user chooses the role and the task there,
and may choose another harness, model or effort than the role settings name.

### Role capsule

A role capsule is the instruction text that a role agent receives with its first message: the text
of its role, the text of one operation that fits the assignment, and the rules that hold for every
role. Agents Remember compiles it from the files of the skill; the
[role-capsule reference](reference/role-capsules.md) describes it.

### Handover

A handover is the record of one role agent's assignment: the selected task documents, the working
folders, the report folder and the parent. The tool `role_start` writes it as a JSON file and
names that file in `handoverFilePath`; the first message of the role agent carries a short form.

### Hand-off

A hand-off is what a role agent delivers before it ends a turn: a delivered `role_message` or a
write to a task document. When a Worker, a Reviewer or a Curator ends a turn without one, the
plug-in sends it a message that begins `[ar-plugin] turn ended with no hand-off:`.

### Message file

A message file is the file in which Agents Remember keeps one message to a role agent whole: the
sender, the recipient, the text and the full text of what the message attaches. The tool
`role_message` names it in `messageFile`, and the delivered message names it in a line
`Message file: <path> (SHA-256 <digest>)`. A message that the user sends from the dashboard is
filed in the same way.

### Bootstrap call

The Bootstrap call is `role_start(role="bootstrap", request_id="<a new UUID>")`, made in a chat of
a harness. It creates the coordination root unless it exists, writes the block `paseoRuntime`
unless the settings file has one, provisions the host, and starts one role agent in the role
Bootstrap, the Bootstrap role agent. [Getting started](getting-started.md) tells the call.

### Held

`held` is a status in the answers of `role_start`, `role_start_set` and `role_message`. A held
start means that the role agent exists in the host and has taken no turn; the first message
addressed to it opens its first turn. A **held message** is stored for a recipient that cannot
take it at that moment: the product delivers it later, in the order taken, and the sender does not
send it again.

### Helper

A helper is an agent that a role agent starts inside its own harness with the means of that
harness; harnesses call it a sub-agent. A helper is no role agent: it holds no role and starts
none. The dashboard and the plug-in say "subagents" for helpers, in the state
"waiting for subagents" and in the line `WAITING FOR SUBAGENTS:`.

### Step-up

A step-up is a second set of role settings for one role, for work that the first set did not
finish. A Worker or a Curator takes it after a set number of review rounds that left findings,
and any role takes it when its start asks for it.
[Harnesses and role settings](reference/harnesses.md#step-up) describes it.

### Host profile

A host profile is an agent profile that the host keeps in its own configuration. A profile whose
name equals a role id, such as `worker`, is the host profile of that role: `role_start` reads it
from the running host, and Agents Remember never writes it.

### Permission mode

The permission mode is the mode in which the harness of a role agent asks for permissions. The
host profile of the role names it, or else the key `mode` of the role row in the user settings
file; a start cannot set it. Where neither names one, the product takes the least permissive mode
that it knows for the harness: `default` for Claude Code and `auto` for Codex.

### Task document

A task document is the JSON document of a sprint, a master or a task, with a rendered Markdown
copy beside it. The tool `task_doc` writes both files. The JSON file is the source: an edit of the
Markdown copy is lost at the next write. The task documents of a code repository lie under
`<coordination-root>/tasks/<id>/`. A task document has one of the statuses `planning`,
`inProgress`, `Completed` and `abandoned`. A new document has the status `planning`. No agent has
to set the status: `worktree_start` sets `inProgress`, `lifecycle_finalize_task` sets `Completed`
and `worktree_abandon` sets `abandoned`. The dashboard shows the status as the stage of the
document: `planning` as "planned", `inProgress` as "started", `Completed` as "completed" and
`abandoned` as "aborted".

### Sprint

A sprint is a task document that groups masters: it commands each master that its field
`orchestrates` names. The `kind` of its document is `master`, like that of a master. Its field
`integrationBranch` names the branch on which the work of its masters lands.

### Master

A master is a task document that groups tasks. Its field `executionNature` is `atomic` or
`organizational`. An atomic master has a branch of its own, on which its tasks land before the
master lands on the sprint's branch. The tasks of an organizational master land on the sprint's
branch. In a sprint without an `executionGraph` every master runs as an atomic master. One button
of the dashboard and the kind `series` of a worktree contract say "series" for a master.

### Execution topology

The execution topology of a sprint says how its masters run and which of them wait for others:
the field `executionNature` of each master and the field `executionGraph` of the sprint's task
document hold it. [Execution-topology authoring](reference/execution-topology.md) describes both
fields and the operation that writes the graph.

### Task

A task is the unit that one Worker builds. It belongs to one master; every code change belongs to
a task, also a change of one file. The product shows two words for a task. The dashboard's task
list, a task's page and the role launcher say "task". Tools, fields and refusals call it a `leaf`,
and so do some texts of the dashboard, for example "No leaf tasks" in the role launcher and
"This leaf changed no recorded meaning." on the review page. The kind of its task document is
`subTask`, and tool arguments such as `leaf_id` name it. The Architect writes the task document of
a task from the
[leaf-task](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/templates/leaf-task.md)
template.

### Attempt

An attempt is one run of a task from its start to its completion. A task has one attempt unless
the tool `task_reopen` reopens it after its completion; the reopened task then runs attempt 2, and
the judgment rows of a later attempt stand in `knowledge/history/<task id>-attempt-<n>.json`.

### Wave

A wave is a group of tasks or of masters that may run at the same time. Agents Remember derives
the waves at each read from what each task or master waits for, and stores none. The dashboard
shows them in the block "Order", and the rendered Markdown copy of a master's task document shows
them in the column "Wave". A [foundation wave](#knowledge-foundation) is another thing.

### Requirement packet

A requirement packet is the approved requirement text that a task is built against. It is one
Markdown file in the folder of the task documents, with a stable ID and a version. The user
approves a packet; the task document names the packets of its task.

### Enclosure

An enclosure is the folder of one task:
`<coordination-root>/worktrees/<repository>/<worktree-name>-ar/`. It holds
the task's code worktree, its memory worktree, a scratch folder and a report folder for each role
agent, and copies of the task's documents. Fields of the tools name the enclosure
`worktree_group`, `worktreeGroup`, `enclosureRoot` or `roots.enclosure`. The start of a Worker,
Reviewer or Curator on the task creates the enclosure when the task has none.

The [worktree contract](#worktree-contract) of the task records the worktrees and the branches of
the enclosure. The **enclosure locator** is the record through which the product finds the
enclosure from the path of that contract.

### Worktree contract

A worktree contract is a file `series-contract.md` that records the worktrees and the branches of
a task or of a master. A task's contract lies among the task documents under
`enclosures/<task id>/`, a master's in the master's task folder. The worktree tools take its path
in the argument `contract_path`. The help of the commands calls a task's contract the leaf
enclosure contract, and one refusal says "series contract" for a worktree contract.
[Workflows](workflows.md#the-enclosure-a-start-creates) names where the file lies.

### Direct execution

Direct execution is work on a task without an enclosure. It needs a grant for that task, the
**direct-execution grant**. The user gives the grant with the switch "Direct execution" on the
task's page in the dashboard, or the Architect gives it with the tool `direct_execution_grant`.
The role agents then work in the checkouts of the master's branch, on one such task at a time.

### Candidate

The candidate of a task is the code tree and the memory tree that the task wants to commit. The
knowledge gate validates exactly these trees before a closeout commits them.

### Closeout

Closeout is the step that commits a task's code and memory. The tool `worktree_closeout_apply`
commits the code first and then the memory, after the knowledge gate has passed on both trees. The Manager or
the Orchestrator performs closeout. In a run without a Manager and without an Orchestrator the
Architect performs it, with the user's approval of that closeout or under an authority that the
user delegated and the task document records.

### Closeout queue

The closeout queue of a sprint lists the tasks of that sprint whose closeout door waits. Agents
Remember derives it from the task documents and the waiting closeout doors. The tool
`closeout_queue` reads or rebuilds it and calls it a projection in its description; the dashboard
shows it in the panel "Closeout queue". No tool of the tool server creates a closeout door, so the
queue holds no task. Once a `task_doc` write or a `rebuild` has built it, `status` answers
`valid-built` with no member; `invalid-empty` is the state of a sprint whose queue file does not
exist yet.

### Closeout door

The closeout door of a task is the record that says whether the task waits for closeout. It is
kept in the file `<enclosure>/reports/closeout-door.json`, which holds its current closeout-door
generation with one of the dispositions `waiting`, `deferred`, `withdrawn` or `claimed`. No tool
of the tool server creates a closeout door, and `worktree_closeout_apply` reads none: it runs the
whole closeout inside the call.

### Certification

A certification is a full run of a code repository's checks and tests in a clean container. A run
starts only when a person asks for one: the tool server registers no tool that starts a
certification, and `agents-remember` has no sub-command for it. The
**repository certification profile** is the file in which the repository says what the run
executes; the key `certificationProfile` of the repository's entry in the settings file names it.
[Repository Certification Profiles](reference/repository-certification-profile.md) is the
reference.

### Certification gate

A certification gate is one of the ordered parts of a certification. The repository certification profile selects
the work of Gate 1 to Gate 4, and [Repository Certification Profiles](reference/repository-certification-profile.md)
says what each of these four means. A closeout runs none of them.

### Landing

Landing brings closed-out work onto the master's or the sprint's branch. The tool
`worktree_integrate` lands a task from its enclosure, and `direct_landing` lands a task under
direct execution. The **landing workflow** is the value of the key `landing.workflow` in the
memory settings file: work lands locally (`local`) or through a pull request (`pull-request`), and
locally where the key is absent.

### Official line

The official line of a worktree is the branch from which the worktree was cut: for the code
worktree and the memory worktree of a task, the source branches that the worktree contract
records. The tool `worktree_sync` pulls the official line into a live worktree.

### Operation journal

The operation journal of an enclosure is the record that Agents Remember keeps of each direct
landing under the enclosure's worktree contract; a closeout and `worktree_integrate` run inside
their call and leave no record in it. It lies in the folder `.lifecycle/` of the enclosure.
Cleanup archives it before it removes the enclosure.

### Archive record

The archive of an enclosure is the copy of the enclosure's manifest and operation journal that
cleanup writes under `<coordination-root>/controlplane/lifecycle-enclosure-archives/`. The archive
record is the receipt beside it: it states that the copy was read back, and Agents Remember
removes the enclosure only after the record stands.

### Task publication

Task publication is the publication of a task outside Agents Remember, for example as an issue.
The optional file `<memory-root>/system/task-publication.md` says how the repository does it: the
Architect and the Manager follow the file, and without it they publish nothing. No tool of the
product reads the file.

## Review

### Intent review

The intent review is the review of a task's change on the review page. Before a task starts, the
Architect asks the user "go, or review first?" and records the answer in the field `intentReview`
of the task document: `pending`, `review-first` or `go`. Until the field says `go`, Agents
Remember refuses every start of a role agent on the task with `intent-review-open`.

### Review page

The review page is the dashboard page that the button "⇄ Intent review" on a task's page opens.
Its head reads "Intent review · <task id>". It shows the change set of the task with the knowledge
it reaches, and the explainer's chat. The tabs "Review" and "Explanation" of its top line choose
what the reading column shows: the change set or the explainer's page.
[The review page and the explainer](guides/review-page-and-explainer.md) describes its use.

### Change set

The change set of a task is what the task changed, as one comparison records it: the changed code
and the changed memory. The review page lists it under the heading "Change set", and "All changes"
opens the change-set page with every changed file.

### Comparison

A comparison is the set of four Git trees that the review page compares: the code before and after,
and the memory before and after. Each recorded comparison has a number, and the page names it
"Comparison #n".

### Route review

A route review is the recorded verdict of the code review of a task's change. It is bound to the
exact code tree that was reviewed and holds one verdict for each reviewed route. The Reviewer
records it with the `task_doc` operation `record_route_review`. A closeout does not read it and
refuses no code tree for a missing, blocking or stale route review.

### Explainer

The explainer is the agent that answers the user's requests on the review page. A task has at
most one. The first request that the user sends creates it; nothing runs before. The explainer
changes no code, no memory and no task document. It takes its harness and model from the role row
`orchestration.roles.review-presenter` of the user settings file or of the repository settings
file.

The explainer works in a folder of its own, the explainer's workspace:
`<coordination-root>/explainer/<repository>/<master folder>/<task id>/`. The first request builds
it; one heading of the dashboard calls it the explainer's environment. The **binding** of the
explainer is the comparison that the explainer's workspace was built for. The button "Retire chat"
retires the explainer: the host archives its chat, and the workspace is deleted.

### Canvas host

The canvas host is the part of the dashboard's server that serves the page an explainer publishes,
under `/canvas/page/<session>/`. It is not the host: it runs no role agent.

### Presentation kit

The presentation kit is the set of pinned libraries that Agents Remember ships for the pages of
the explainer. The explainer's workspace holds the kit in the folder `kit/`, and a page loads a
kit file from its own revision, without the network.

### Revision

A revision is one saved page of the explainer's explanation. Each time the explainer publishes,
the task keeps a new numbered revision. The revisions of a task share a size limit of 512 MiB: a
publication beyond it is refused until the user removes old revisions. The revision that the user
agreed to before work started is marked "agreed" and cannot be removed. A retained revision is one
that the user has not removed.

### Grade

A grade is the explainer's assessment of one block of the change set in one **category**. The
explainer defines a category with two to five grades when the user asks for it. The review page
shows a grade as a chip on the block and offers the category as a sort order, under the head
"Explainer’s judgment". A grade is the explainer's inference; it is no recorded fact about the
change.

### Tray

The tray is the dashboard's collection of picked lines, files and review blocks that the user sends
to a chat. The button "Tray (n)" beside the tabs opens it. Its panel is headed "**AR tray**". The
user adds a note, chooses the receiving chat and presses "Send".
