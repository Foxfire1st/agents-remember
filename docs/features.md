# Features

Agents Remember gives a coding agent a memory of a code repository that the agent can check against
the code. It also runs the work on that repository as tasks, which role agents build, review and
land. This page tours what a user has and links the page that describes each part.
[Concepts](concepts.md) defines the terms.

In short:

- **Memory at a fixed path.** The card of a source file lies at a path derived from the path of the
  source file, so an agent that holds the file reads its card without a search.
- **Staleness that is measured.** Cards record [anchors](concepts.md#anchor), each the record of
  one place in the code; the tools compare the anchors with a selected code tree and report each
  as current or stale.
- **Memory that moves through Git like code.** A task changes code and memory in paired worktrees,
  and closeout commits both, with the memory commit naming its code commit.
- **Role agents on a host.** The host runs each role agent; an Architect talks with the user,
  and Workers, Reviewers and Curators build, review and write memory.
- **A dashboard.** One local web page shows the chats, the task documents, the knowledge and the
  files, and holds the review page.
- **The repository's own instructions.** Each memory repository carries its own commands, coding
  guidelines, design guidelines and landing rules in its `system/` folder.

## Table of Contents

1. [Why agents need this](#why-agents-need-this)
2. [The core idea: the path is the address](#the-core-idea-the-path-is-the-address)
3. [Memory you can trust: deterministic anchors and commit attribution](#memory-you-can-trust-deterministic-anchors-and-commit-attribution)
4. [How an agent reaches knowledge](#how-an-agent-reaches-knowledge)
5. [Isolated worktrees that keep the official line of the memory repository clean](#isolated-worktrees-that-keep-the-official-line-of-the-memory-repository-clean)
6. [Task isolation and the knowledge index](#task-isolation-and-the-knowledge-index)
7. [Role agents and the host](#role-agents-and-the-host)
8. [The dashboard](#the-dashboard)
9. [Task documents](#task-documents)
10. [The review page and the explainer](#the-review-page-and-the-explainer)
11. [Direct execution](#direct-execution)
12. [The tool server](#the-tool-server)
13. [The repository's own instructions: the `system/` folder](#the-repositorys-own-instructions-the-system-folder)
14. [Setup in a harness](#setup-in-a-harness)
15. [Operational guardrails](#operational-guardrails)
16. [How the parts fit](#how-the-parts-fit)
17. [Learn more](#learn-more)

## Why agents need this

A coding agent starts a session without the knowledge that a team keeps outside the code:
invariants, naming contracts, the reasons behind a migration, and edges to other repositories. An
instruction file at the top of a repository does not reach the agent at the moment it changes one
file.

Agents Remember stores this knowledge in a Git repository. The durable memory of record is
Markdown cards, JSON sidecars and shared knowledge records under Git: versioned,
diffable, reviewable, and anchored to the code. An agent trusts a card because Git shows whether
the card's [anchors](concepts.md#anchor), its records of places in the code, still match the code,
not because a search returned the card.

## The core idea: the path is the address

Durable memory is stored as **cards**: Markdown files at paths derived from
source paths. A source file maps to a mirrored card inside its repository's memory
repository:

```text
<code-repository>/src/foo/bar.ts
<coordination-root>/memory-repos/ar-my-repo/onboarding/src/foo/bar.ts.md
```

Each card explains what the code cannot make obvious on its own: its **purpose**,
important **logic**, **conventions**, **invariants and boundaries**, and durable
file-level concerns. The Markdown holds current prose and
numbered citation markers. Its [sidecar](concepts.md#sidecar), the paired JSON file
`onboarding/<source path>.json`, holds three kinds of entries. A reference belongs to one numbered
citation of the prose and names what the citation points at; a place in code or in a test is
named by its anchor. A [realization](concepts.md#realization-and-proof) says that the anchored
code realizes an [invariant](concepts.md#invariant), and a proof says that the anchored test
demonstrates a facet of one. A [route overview](concepts.md#route-overview), the card of a
folder, has a sidecar of its own, `overview.json`, which holds references.

Shared truths live once in `knowledge/<kind>/<ID>-<slug>.json` records: invariants,
families, decisions and other knowledge. Git records their meaning changes. The Curator writes
[judgment rows](concepts.md#judgment-row) into `knowledge/history/<owner>.json`: each row records
its judgment of one change of a task. A recomputed
[worklist](concepts.md#worklist-and-knowledge-gate), the list of the knowledge that a change
reaches, and the exact-tree [knowledge gate](concepts.md#knowledge-gate) check that those
judgments answer the changes of the [candidate](concepts.md#candidate), the code tree and the
memory tree that a task wants to commit, before publication. The [knowledge-system
reference](reference/knowledge-system.md) describes the files, reader, review and
commands. Cards retain current truth without duplicating code or task plans.

Because the path *is* the address, three lookups need no search:

- **Retrieval**: an agent holding a file knows its card's location directly. No
  ranking, no embedding threshold, no index lookup. Reads stay predictable, and
  unrelated-but-similar material stays out of the context window.
- **Reverse lookup**: a card's path names the exact source file it describes, so
  any path that finds a card also finds the code.
- **Coverage**: a missing card or an orphaned one (its source file is gone) is
  visible from the path alone.

This also changes how memory scales. The cost of *reading* memory is proportional
to the files in scope for a task, not to the size of the repository. A large repository
can accumulate a great deal of memory; the agent still only reads the repository
overview, the relevant route-local overview if one exists, and the cards for the
files it is touching.

Durable memory comes in three shapes, matched to scope. **File-level cards** are
the one-to-one core. **Route overviews** (`overview.md`) orient a whole
[route](concepts.md#route) (the folder of a package or a module, or the repository itself),
describing the shape of a region
rather than a single file. And **entity catalogs** (`entities.md`) track the
recurring entities and naming contracts that span many files at once. Overviews
and catalogs add structure on top; they never replace the file-specific facts
beneath them.

Reading a card by its path needs no tool, no index and no running service.

## Memory you can trust: deterministic anchors and commit attribution

A card is only useful if you know whether it still matches the code. Cards
keep prose in Markdown and code references in their JSON sidecars. The
checks compare those anchors with the code.

**Reference anchors bind both blob and content.** An anchor records the path of a file, the
`blob` (the id that Git gives the whole content of that file), the `locator` (a symbol, a line
range or the whole file) and the `content`, a hash of the bytes at the `locator`: the content
identity. A code or test reference is
current only when its recorded blob matches the exact paired code tree and its
`locator` resolves there to the recorded content identity. An edit elsewhere in the
file changes the blob: even unchanged located bytes leave the anchor in the state `stale:blob`
until its blob is recorded anew. A uniquely moved identical range needs its `locator` and
blob refreshed. The tool `citation_fix` makes both repairs in the memory worktree of a task.
Changed or ambiguous content, an absent path or an unresolved
`locator` needs a decision of the Curator. The [drift summary](concepts.md#drift-check), the
result of a call of `context_packet` with `include_drift`, reports current and stale
reference counts and names unreadable sidecars and validator findings separately.

Realization and proof entries use a separate currentness rule at the exact
selected code tree: an entry can remain current when its blob changed but its
resolved content is unchanged. An entry has one of three states:

- `current`: the blob matches or the resolved content is unchanged
- `stale`: content differs, the path is absent or the `locator` does not resolve
- `unverifiable`: the requested tree or needed object cannot be read, or the `locator` of a
  changed blob cannot be re-resolved; the answer names the reason

An invariant takes one of four states from its entries. It is `stale` if any entry is stale;
otherwise it is `unverifiable` if any entry is unverifiable, `unrealized` if it has no
realization entries, and `current` otherwise. The state `unrealized` belongs to an invariant
only; no entry has it. These are mechanical observations, not a judgment that the prose is
correct. Stale knowledge stays visible with its state and reason, so it can inform
investigation without silently becoming current truth.

**Broader units carry references too.** An overview's `overview.json` holds the
anchors supporting its prose. Its currentness follows those references rather
than a blanket directory diff. A [route index](concepts.md#route-index) is a generated file
that describes the structure of a route. The tool `route_index_refresh` writes the route indexes
of a task's memory worktree anew, and so does closeout; that maintenance is distinct from checking
reference anchors. The [entity catalog](concepts.md#entity-catalog) holds a fingerprint over the
files that define each entity. Closeout computes the fingerprint anew for each entity whose files
or whose entry the task changed, and it refuses a candidate whose catalog names a file that the
code tree does not hold. The user runs no check for it.

**Commit attribution keeps memory and code paired.** Memory lives in a separate
repository, so the pairing must be explicit. Each attributed memory-content commit
carries a `Code-Commit: <sha>` trailer in its hashed Git object. The pairs of code commit
and memory commit are derived from those commits, newest first. Agents Remember computes the file
`memory.md`, the [memory attribution cache](concepts.md#memory-attribution-cache), at the
root of the memory repository from the same trailers; Git ignores the file, and no memory commit
holds it. Baseline adoption, closeout, `worktree_sync` and `direct_landing` write the file anew
after their memory commit, so the user does nothing for it. Worktree start, closeout and landing
take the mapping from the commits, not from the file. Answers of the tools name the file in the
field `ledger_path`. Below a block of metadata, the file lists the pairs in this table:

```text
| Code commit | Memory commit |
| ----------- | ------------- |
| a1b2c3…     | f9e8d7…       |
| 9f8e7d…     | 4c5b6a…       |
```

The commit-derived mapping has three consequences. A branch carries the memory
version that matches its code state. A user can restore memory to the exact
version that was synchronized with an earlier code commit: the row of that code commit in
`memory.md` names the memory commit, which is the commit of the memory repository whose trailer
`Code-Commit:` names that code commit, and plain Git checks that commit out. And the
[cross-repository context](concepts.md#cross-repository-context) does not guess: it leaves out a repository
whose code is not on the expected branch. Of a repository that is, it includes the memory only while the
memory repository is on the expected branch too and its `memory.md` can be read, and the code alone otherwise.
[Architecture](architecture.md#the-file-memorymd) tells which reads use `memory.md`.

## How an agent reaches knowledge

An agent often has to find the right file before it can read that file's card. It has these routes:

- **By path.** The agent holds a file; the card lies at the mirrored path.
- **By tool.** `read_ar_files` names, for up to 20 paths, the governing card and the knowledge
  records of each path. `knowledge_read` names the invariants and families attributed to one
  source file, and the realizations and proofs of a named record. Both return names and locations,
  never text; the agent opens the named files itself.
- **By text search.** An agent that knows a concept but no file searches the code and the cards as
  text, then expands the hits with `knowledge_read`.

`context_packet` orients an agent on one code repository in one call: the roots, the memory
repository, the `system/` folder and, on request, the drift summary.

## Isolated worktrees that keep the official line of the memory repository clean

A task runs in an [enclosure](concepts.md#enclosure): one Git worktree of the code repository and
one of the memory repository, each on a private branch. Code *and* memory changes happen on
private branches together, and the official line of the memory repository stays untouched while a
feature or refactor is still in flight. The [official line](concepts.md#official-line) of a
worktree is the branch from which it was cut.

Start, closeout and landing keep code and memory paired:

- **Start cuts each worktree from the tip of its source branch.** The source branch of the memory
  worktree is the branch of the memory repository that has the name of the code source branch.
  The start derives the pairs from the memory commits only to report the newest pair; it chooses
  no memory commit by them.
- **Closeout commits in a fixed order.** The knowledge gate checks the candidate's memory tree
  before either commit. Code commits first; the exact prepared memory tree is
  checked again before the memory-content commit, which carries its code
  attribution. `memory.md` is refreshed as an ignored cache after the memory commit.
- **Landing moves both branches to exact commits.** `worktree_integrate` judges the closed-out
  pair again. It then moves the target branch of the code repository and the target branch of the
  memory repository, each only while the branch still stands at the commit the call read before.
  When a branch moved in between, the call answers `integration-ref-race` and names the call to
  repeat.

Memory is protected by the same branch, review and fast-forward machinery as
code: versioned Markdown and JSON moving through Git. [Workflows](workflows.md) tells the steps from
start to landing.

## Task isolation and the knowledge index

The [enclosure](concepts.md#enclosure) of a task isolates its changes: its code worktree and its
memory worktree lie in it.

The [knowledge index](concepts.md#knowledge-index) is an SQLite view derived from a selected
memory text tree. It is cached by tree identity outside Git working trees, by default in
`<coordination-root>/runtime/knowledge-index/` with one file for each tree, and rebuilt when
absent. It is evicted by age and size: after a build, a file unused for 14 days is removed, then
the oldest files until the folder holds at most 1 GiB. Knowledge writers change the text; they do
not write or merge this index.

## Role agents and the host

Work on a code repository is done by role agents. A role agent is a chat of a harness that runs in
one of eight roles: Architect, Investigator, Orchestrator, Manager, Worker, Reviewer, Curator or
Bootstrap. [Roles and role agents](roles.md) describes each role.

- The user talks with the **Architect**. The Architect writes the requirement packets and the
  task documents and delegates the work.
- A **Manager** coordinates one master. It starts a **Worker** to build a task, a **Reviewer** when
  the Worker freezes its change, and a **Curator** after the code review has passed.
- The Worker writes code. Only the Curator writes memory. The Manager performs closeout and landing.
- An **Orchestrator** coordinates a sprint when two or more masters are worked on at the same time,
  or when the user asks for one. The Architect starts it, or the user from the role launcher.
- A question for the user travels up through the starting agents and arrives in the
  Architect's chat.

The host runs the role agents and shows their chats. It is Paseo; Agents Remember installs a pinned
version and starts it. The tool `role_start` starts a role agent, and so does the
[role launcher](concepts.md#role-launcher), a row of the dashboard's tab "Chats". Role agents send
each other messages with the tool `role_message`. The user settings
file names the harness, the model and the effort of each role.

The host runs on Linux x86_64, WSL2 included. On another platform the tool server and the dashboard
run, and no role agent can be started. [The host](host.md) tells how to check, stop and provision
the host, and [Harnesses and role settings](reference/harnesses.md) tells which harnesses it can
start.

## The dashboard

`agents-remember dashboard --daemon` starts the dashboard, a local web page at
`http://127.0.0.1:8765/` by default. It has four tabs:

- **Chats** holds the role launcher and, under it, the chat of the selected role agent.
- **Operations** lists the tasks as a tree of sprint, master and task, and shows the selected task
  document. The [attention bell](concepts.md#attention-bell) lists what waits for the user: a
  permission question of a role agent, a [held message](concepts.md#held) (a message stored for a
  role agent that cannot take it yet), a started task that has had no agent in a turn for 15
  minutes, a blocked worktree start, cards that need a refresh decision, and a notice for each
  task that landed or was abandoned. The review page opens from a task's page.
- **Knowledge** reads a memory tree: the records by kind, the invariants without proof and the
  knowledge of a path.
- **File Viewer** shows a source file beside its card.

The button "Tray (n)" beside the tabs opens the tray, which collects picked lines and files and
sends them to a chat. [The dashboard](dashboard.md) describes every tab and control.

## Task documents

Every code change belongs to a task. A task belongs to a master, and a sprint groups masters.
Each of the three has a task document: a JSON file that the tool `task_doc` writes, with a rendered
Markdown copy for reading. A task document holds the objective, the requirement packets, the steps,
the decisions and the review rounds of its work.

Task documents hold plans; cards and knowledge records hold approved current state. A question that
changes no code needs no task: a chat answers it. The Architect writes the task document of a task
from the
[leaf-task](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/templates/leaf-task.md)
template. Tools, fields and refusals call a task a `leaf`, as the name of this template does;
[Task](concepts.md#task) tells where each of the two words appears.

The dashboard shows the task documents on its tab "Operations". [Workflows](workflows.md) tells
how a task moves from planning to landing, and the
[MCP tool reference](reference/mcp-tools.md) lists the operations of `task_doc`.

## The review page and the explainer

The button "⇄ Intent review" on a task's page opens the review page. It shows the task's change set:
the changed code, the cards and knowledge records the change reaches, and the
[judgments](concepts.md#knowledge-judgments-and-publication) the Curator recorded. It compares
four Git trees, the code and the memory before and after, and names each recorded comparison
"Comparison #n".

The user asks questions on the page. The first request creates the task's explainer, an agent
that answers with a chat reply or with a page of its own. Each published page is a numbered
revision; to publish one, the explainer needs Playwright and its browser on the machine
([what the explainer needs](guides/review-page-and-explainer.md#what-the-explainer-needs-before-it-can-publish)). On request the explainer grades the blocks of the change set, and the page sorts by its
grades. The explainer changes no code, no memory and no task document.

Before a task starts, the Architect asks the user "go, or review first?", and no role agent
starts on the task until the task document records `go`.
[The review page and the explainer](guides/review-page-and-explainer.md) is the guide.

## Direct execution

A task normally runs in an enclosure. Direct execution is work on a task without one: the role
agents work in the checkouts of the master's branch. It needs a grant for that task. The user
gives it with the switch "Direct execution" on the page of a task in planning, or the Architect
gives it with the tool `direct_execution_grant`. One task at a time runs this way, and the tool
`direct_landing` lands it with its memory. [Workflows](workflows.md#direct-execution) describes the
route.

## The tool server

The model does the judgment: it frames the problem, compares options and asks for approval. The
tool server does the deterministic work, so the same repository state gives the same answer.
[Context resolution](concepts.md#context-resolution), the drift check, commit order and Git
operations run in code, not in a prompt.

The tool server is a single process exposing typed tools, and the
boundary between them is the trust boundary:

- **Read-only tools** orient on a repository, check drift and preview closeout
  without changing working-tree files, the Git index, local branches or task
  documents. They may write the permitted caches, captured objects and reports;
  an explicitly requested freshness read may fetch the configured upstream. The
  [read boundary](architecture.md#what-a-read-may-write) defines those writes.
- **Mutating tools** that write Git history offer a preview: `worktree_closeout_preview`, or the
  argument `dry_run` of the tool itself. `worktree_closeout_apply`, `direct_landing`,
  `memory_carryover_apply` and `worktree_operation_control` also take an `intent_note`: a note in
  which the caller records the approval or the reason for the write. A closeout, a direct landing
  and a carryover refuse a blank note.

The tool server answers a call that carries an argument the tool does not declare as if the
argument were absent. It bounds the size of every
answer: 1,500 tokens by default, and at most 4,000 for some tools, among them `server_info`,
`knowledge_read` and `role_message`. An answer holds the
outcome, what the caller needs for its next step and, when the result is larger, the field `log`:
the place of the file that holds the whole result. That file lies in the folder of the
[tool log](concepts.md#tool-log): `<enclosure>/logs/` for a call of a role agent on a task with an
enclosure, and by default `<coordination-root>/logs/mcp/` for every other call.
[Tool answers](reference/mcp-tools.md#tool-answers) tells the bounds, and the
[MCP tool reference](reference/mcp-tools.md) lists every tool.

## The repository's own instructions: the `system/` folder

A memory repository carries a committed `system/` folder: the repository's own instructions,
versioned beside its memory. The tool `memory_init` creates the folder with `settings.md`,
`tools.md` and `sources.md`. The user adds the other files when the repository needs them. The
folder holds:

| File | Content | Who uses it |
| --- | --- | --- |
| `settings.json` | storage and path rules that decide which source files are eligible for a card, the cross-repository context, and `landing.workflow` | the tool server |
| `settings.md` | notes for people and agents on this repository's memory | whoever opens the path that `context_packet` names; only when `settings.json` is absent, the tool server reads the storage rules, the path rules and `crossRepo` from a fenced YAML block of this file |
| `tools.md` | the commands that build, lint, type-check and test the repository | the Worker builds each worktree's environment as this file says and runs every command in it; the Reviewer takes its check commands from it |
| `sources.md` | a registry of where the project's documentation lies | the Curator and every agent that researches the repository |
| `coding-guidelines.md` | the repository's coding rules | the Worker reads it in full before its first source edit; the Reviewer checks every added or changed line against it |
| `design-guidelines.md` | the look of user interfaces and pages | the Worker and the Reviewer for user-interface work; the explainer for its pages |
| `task-publication.md` | the landing and publication flow, and how tasks are published as GitHub issues | the Architect and the Manager |
| `code-quality-report-template.md` | the shape of a code-quality report | the Worker |
| `skill-writing-guidelines.md` | rules for instruction texts such as skills and role files | the Worker and the Reviewer, for work on such texts |

These files are plain Markdown and JSON. A repository changes how agents work on it by editing
them, not by writing instructions for one harness.

Two of the files carry more than rules for code:

- **Design guidelines.** The example folder holds `design-guidelines.md` with three files it routes
  to, `DESIGN.md`, `TREATMENT.md` and `TECHNIQUES.md`, as examples; it is the folder of example
  files for `system/` that the package ships. Agents Remember itself prescribes
  no design; a repository without the file gets none.
- **Task publication.** No code reads `task-publication.md`; the Architect and the Manager follow
  it. When the file exists, the Architect creates a linked issue when it registers a task. The
  Manager assigns the issue and sets it in progress before the task starts, and closes it after
  the landing and the pushes. Without the file nothing is published.
  [Workflows](workflows.md#task-publication) describes the file.

The folder travels with the memory: every contributor who resolves that memory repository gets the
same instructions. The tool server serves the same [skill](concepts.md#skill), with the
instructions of the roles, to every harness, so the same
`system/` folder gives the same behavior whichever harness runs a role agent. The
[example folder](https://github.com/Foxfire1st/agents-remember/tree/main/system/defaults/examples/memory-repo)
holds an example of each file except `skill-writing-guidelines.md`; the installed package holds
the same folder at `agents_remember/package_data/runtime/system/defaults/examples/memory-repo/`.
The [settings reference](reference/settings-json.md) lists the keys of `settings.json`.

## Setup in a harness

The user registers the tool server in Claude Code, Codex, Cursor, Hermes, Pi or OpenClaw as the
page [Install](install/README.md) tells for that harness.
The tool server serves the skill `l-01-agent-lifecycles`, which holds the instructions of the
roles, from its package.

One call from a chat then starts the setup:

```text
role_start(role="bootstrap", request_id="<UUID>")
```

`request_id` is a UUID that the calling chat generates for this start. A call repeated with the
same `request_id` reconciles the same role agent and starts no second one.

This [Bootstrap call](concepts.md#bootstrap-call) prepares the coordination root and writes the
host block `paseoRuntime` into the [settings file](concepts.md#settings-file),
`~/.config/agents-remember/agents-remember-settings.json`, unless the file has one. It installs and
starts the host, and starts the Bootstrap role agent on the harness of the calling chat. Its answer
names the address of the dashboard, where the setup continues in the chat of that role agent. The
Bootstrap role agent asks the user before each change. It creates the user settings file,
initializes the memory repository and adopts its first baseline. It creates no task and starts no
other role; the user starts an Architect for the first work.
[Getting started](getting-started.md) tells the steps in full.

Two contributors can use different harnesses on one repository. Both resolve the same committed
memory, the same `system/` rules and the same tools. The harness changes how the tool server is
registered; it does not change what the repository asks of the agent.

## Operational guardrails

- **The settings file** lists the code repositories the tool server may work on; a call that names
  another repository is refused. Its key `transcriptRoot` also sets where the tool log goes.
- **Memory baseline adoption** commits the content of an existing memory repository as its
  [baseline](concepts.md#baseline), the first commit of its content.
  `memory_baseline_status` first reports the [drift](concepts.md#drift-check), the difference
  between what the anchors recorded and the code, and the state of the attribution.
  `memory_baseline_adopt` commits only on an explicit call;
  [Adopt Existing Memory](guides/adopt-existing-memory.md) describes it.
- **Branch memory [carryover](concepts.md#carryover)** copies richer cards from the memory of a
  source branch into the memory worktree of a task, only after the matching code has landed. A
  file's card passes without a review only on one of three proofs that the code of the file
  landed. Every commit of the source branch that touches the file is part of the official line (an
  exact commit). Or Git's patch id of the file's change is the same on the source branch and on
  the official line (a patch-id match). Or the bytes of the file are equal on both (a
  final-content match). It does not copy stale branch memory wholesale.
  [Carry Memory From a Branch](guides/carryover-from-branch.md) describes it.
- **Branch-gated [cross-repository context](concepts.md#cross-repository-context)** names another
  repository's code, and its memory when asked for, only while that repository is on the branch
  the entry expects. The entries stand under `crossRepo.allow` in the memory settings file,
  `<memory-root>/system/settings.json`. An entry names the other repository in `repo` and its
  branch in `expectedBranch`, and `includeMemory` asks for its memory; the
  [settings reference](reference/settings-json.md#memory-settings-file-keys) lists the keys.
- **A [benchmark runner](concepts.md#benchmark-runner)**, the module
  `agents_remember.benchmarks.runner`, prepares paired Codex runs with and without memory, captures
  their output and summarizes metrics. `python -m agents_remember.benchmarks.runner list` lists
  its cases. It is a Python module of the package, not a tool of the tool server;
  [Benchmark Methodology](benchmarks-methodology.md#the-benchmark-runner) describes it.

The checks that contributors run on the source of Agents Remember itself stand in
[CONTRIBUTING.md](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md).

## How the parts fit

- The **address rule** makes a known file's card directly reachable; anchor
  comparisons measure currentness against the code.
- **Commit attribution** pairs memory with code across separate repositories.
- **Enclosures** keep in-progress code and memory off the official line of each repository.
- The **knowledge index** is rebuilt from the selected text tree.
- The **tool server** makes every operation deterministic and checks the knowledge gate.
- **Role agents** on the **host** do the work in roles, and the **dashboard** shows it.
- The **`system/` folder** carries each repository's own rules with its memory.
- **Operational guards** cover baselines, carryover and cross-repository context.

The memory itself is small: Markdown, JSON, Git metadata and deterministic paths. The roles, tasks
and checks around it exist to keep stale or speculative content out of that memory.

## Learn more

- [Concepts](concepts.md): the terms.
- [Architecture](architecture.md): the parts and who owns what.
- [Getting started](getting-started.md): the setup in your own workspace.
- [Install](install/README.md): registration of the tool server in each harness.
- [The dashboard](dashboard.md): the four tabs.
- [Roles and role agents](roles.md): the eight roles.
- [The host](host.md): checking, stopping and provisioning the host.
- [Workflows](workflows.md): a task from planning to landing.
- [The review page and the explainer](guides/review-page-and-explainer.md): reviewing a change set.
- [Use The Memory Repository](guides/use-external-memory.md): the memory repository and commit
  attribution.
- [Settings Reference](reference/settings-json.md): the keys of the settings files.
- [MCP Tool Reference](reference/mcp-tools.md): every tool.
- [Knowledge System](reference/knowledge-system.md): the files, the worklist and the knowledge gate.
