# Architecture

Agents Remember consists of one package, three programs (the tool server, the dashboard and the
host), and the files and folders that hold its settings, its working state and its memory. This
page names each part, tells what it holds and tells who writes it. [Concepts](concepts.md)
defines the terms.

| Part | What it is | Where it lies |
| --- | --- | --- |
| The package | the Python package `agents-remember-mcp` with the commands `agents-remember` and `agents-remember-mcp` | where the user installed it |
| The tool server | the MCP server that a harness starts | a process of the package |
| The dashboard | a local web page and the server behind it | a process of the package, by default at `http://127.0.0.1:8765/` |
| The host | Paseo, the program that runs the role agents | `~/.local/share/agents-remember/` and `~/.local/state/agents-remember/` |
| The four settings files | JSON files | `~/.config/agents-remember/`, the code repository and the memory repository |
| The coordination root | the folder of the working state | the folder that `coordinationRoot` names |
| A code repository | the repository the agents change | `<workspace-root>/<id>` |
| A memory repository | the Git repository that holds a code repository's memory | `<coordination-root>/memory-repos/ar-<id>` |

## The package

The package carries everything Agents Remember runs or copies:

- the tool server and the commands;
- the dashboard's server and, in a built package, the dashboard's web pages;
- the [skill](concepts.md#skill) `l-01-agent-lifecycles` with the instructions of the roles,
  which the tool server serves to a harness;
- the version pin of the host and the plug-in that Agents Remember installs into the host;
- the file `tool-details.md` and the files of the two [search providers](concepts.md#search-provider)
  that an install copies into the coordination root, and the benchmark material that
  `runtime_install` copies there with `include_benchmarks=true`.

The package does not carry the host itself. An install downloads the host and the Node version it
runs on.

The source repository holds the package under `mcp/`, the dashboard's source under `dashboard/` and
the skill under `skills/`.
[CONTRIBUTING.md](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md) describes
the source repository and its generated copies.

## The tool server

A harness starts the tool server as a process of its own with the command
`agents-remember-mcp --config <settings file>` and speaks MCP with it over standard input and
output. The host gives every role agent a tool server under the name `agents-remember-task`.

The tool server owns the deterministic work:

- it resolves a code repository and its memory repository from the settings file;
- it reads memory and measures its currentness against a code tree;
- it writes task documents, knowledge records and judgments on behalf of the calling agent;
- it creates enclosures, commits code and memory at closeout, and moves branches at landing;
- it starts role agents on the host and delivers the messages between them.

The tool server reads the settings file when it starts, and the host block `paseoRuntime` at each
use. A code repository that the settings file does not list is refused. The tool server writes each
call into the [tool log](concepts.md#tool-log): under `<coordination-root>/logs/mcp/` by default,
and under `<enclosure>/logs/` for a role agent on a task with an enclosure. The
[MCP tool reference](reference/mcp-tools.md) lists the tools.

## The dashboard

The dashboard's server starts with `agents-remember dashboard`; with `--daemon` it runs in the
background. The server reads the task documents, the memory repositories and the
records of tool calls, and it serves the web page with its four tabs. The chat on the page is the
host's own page in a frame.

The dashboard changes state only when the user acts on it. For example, it starts a role agent
from the role launcher, sends the tray to a chat, gives or revokes a direct-execution grant, and
sends the user's requests to the explainer. It keeps its own state and its log under
`<coordination-root>/logs/dashboard/`. [The dashboard](dashboard.md) describes the page.

## The host

The host is Paseo. It runs each role agent as a chat of a harness, keeps the chat open, delivers
messages to it and shows it in a browser. The tool server and the dashboard run without the host;
`role_start`, `role_message` and the role launcher need it.

Agents Remember installs the host, configures it and starts it. The first
[Bootstrap call](concepts.md#bootstrap-call), a `role_start` for the role Bootstrap, writes
the block `paseoRuntime` into the settings file once and installs the host; `runtime_install`
installs what is missing when the block exists. The host listens on `127.0.0.1:8766` by default.
Its program lies under `~/.local/share/agents-remember/`, and its home under
`~/.local/state/agents-remember/paseo/`.

The host runs on Linux on x86_64, WSL included. On another platform no role agent can be started.
[The host](host.md) tells how to check, stop and provision it.

## The four settings files

Agents Remember reads four settings files:

| File | Location | Holds | Written by |
| --- | --- | --- | --- |
| Settings file | `~/.config/agents-remember/agents-remember-settings.json`, or the file that `--config` names | the coordination root, the workspace root, the code repositories, the dashboard and the host block `paseoRuntime` | the user; the first Bootstrap call adds `paseoRuntime` |
| User settings file | `~/.config/agents-remember/settings.json` | the role settings under `orchestration` | the tool `user_settings_init` creates it; the user edits it |
| Repository settings file | `<code-repository>/system/settings.json`, optional | role settings for one code repository, laid over the user settings file | the user |
| Memory settings file | `<memory-root>/system/settings.json`, in the [memory root](concepts.md#memory-root) | which source files get cards, which other repositories the context may include, and the landing workflow | the user |

Every page calls the first of them "the settings file"; the three others always carry their
qualifier.

The memory settings files are the only settings files inside the coordination root. Agents
Remember refuses a settings file for `--config` that lies inside the coordination root.

The [settings reference](reference/settings-json.md) describes the four files.

## The coordination root

The coordination root is the folder that the settings file names in `coordinationRoot`. The first
Bootstrap call or the tool `runtime_install` creates it with every folder below except `runtime/`:

```text
<coordination-root>/
  docs/              the file tool-details.md
  logs/              the tool log and the dashboard's log
  memory-repos/      the memory repositories, one folder ar-<id> for each code repository
  notes/             notes and reports
  providers/         the files of the two search providers; nothing starts, indexes or queries them
  runtime/           caches and process records, among them the knowledge index
  tasks/             the task documents
  temp/              the whole results of tool calls
  worktrees/         the enclosures, one for each task
```

The folder `runtime/` appears when a tool server or the dashboard's server first starts: each
writes a record of its process into `runtime/servers/`. The tool server serves the skill from the
package. [Runtime Layout](reference/runtime-layout.md) lists every folder, its writer
and the files outside the coordination root.

These parts write the folders:

- The install, which is the first Bootstrap call or `runtime_install`, writes `docs/` and
  `providers/` and creates the empty folders.
- The tool `task_doc` writes the task documents under `tasks/`.
- The tool `memory_init` creates a memory repository under `memory-repos/`.
- The start of a Worker, a Reviewer or a Curator on a task creates the task's enclosure under
  `worktrees/`.
- The tools and the dashboard write `logs/`, `temp/` and `runtime/`.
- The user and the role agents write `notes/`. The tool `role_start` writes there too: the
  receipts of role starts that belong to no task, under `notes/reports/paseo-native-executions/`.

## Code repository

A code repository is the repository the agents change. The settings file lists it under
`repositories`, and its folder is `<workspace-root>/<id>`. The memory of a code repository lies in
its memory repository under the coordination root.

## Memory repository

The memory of a code repository is a Git repository of its own, the memory repository. It lies
under the coordination root in `memory-repos/ar-<id>`:

```text
<coordination-root>/memory-repos/ar-my-repo/
  memory.md                   # computed pairs of code commit and memory commit; ignored by Git
  onboarding/                 # current Markdown cards and JSON sidecars
  knowledge/
    layout.json               # layout marker; memory_init writes it
    invariants/               # one JSON record per shared truth
    families/
    decisions/
    history/                  # owner/attempt judgment files
  docs/                       # local copies of domain documentation
  system/                     # the repository's own instruction and settings files
```

The other record directories hold incidents, assumptions, limitations, failure
modes, scenarios, diagnostics, terms, weaknesses and dependents. The
[sidecars](concepts.md#sidecar) of the cards own the
[realizations and proofs](concepts.md#realization-and-proof); [family](concepts.md#family)
records own their members and routes. The derived
[knowledge index](concepts.md#knowledge-index) is a disposable SQLite cache under
`<coordination-root>/runtime/knowledge-index/`, outside Git working trees. Reads
rebuild it from the selected text tree when needed; it is not a second source of
truth.

The [worklist](concepts.md#worklist-and-knowledge-gate) compares four trees: the code and the
memory of a task, each before and after its change. The [validator](concepts.md#validator) checks
the structure and the references of the [candidate](concepts.md#candidate). The mandatory
[knowledge gate](concepts.md#knowledge-gate) combines current satisfying
[judgment rows](concepts.md#judgment-row) with the validator's results on the exact trees that a
closeout or a landing commits. The review page
presents the change set and stored judgments; the knowledge reader presents a
selected memory tree. Neither display authors a judgment. See [Knowledge
system](reference/knowledge-system.md) for the layout, commands and boundaries.

### The file `memory.md`

`memory.md` is a file at the root of the memory repository. Agents Remember computes it from the
`Code-Commit` trailers of the memory commits: it lists each pair of code commit and memory commit,
newest first. Git ignores the file, because Agents Remember adds the line `/memory.md` to the
memory repository's `.gitignore`, and no memory commit holds it.

Worktree start, closeout and landing take the pairs from the commits. A missing or outdated
`memory.md` does not block them. Baseline adoption, closeout, `worktree_sync` and `direct_landing`
write the file anew after their memory commit.

These reads use the content of the file:

- `context_packet` with `include_freshness` reports whether the file maps the checked-out code
  commit.
- The [cross-repository context](concepts.md#cross-repository-context) includes the memory of
  another repository only when, among the other conditions of its entry, that repository's
  `memory.md` can be read; an unreadable `memory.md` leaves that repository's code only.
- Closeout reports in `memory_ledger_repair` how the file differed from the pairs it computed.
- The dashboard's server reads the newest pairs of the file.

## How a tool finds a repository's memory

A tool call names a code repository by its id in `repo_id`. The tool server resolves the rest:

1. It takes the code repository's folder `<workspace-root>/<id>` from the settings file.
2. It takes the memory repository at `<coordination-root>/memory-repos/ar-<id>/`.

When that memory repository does not exist, the call is refused and names the path it checked.
Agents Remember does not answer with an empty context; the tool `memory_init` creates the memory
repository.

A call for one task names the task: in `task_context` for `context_packet`, in `contract_path` for
the worktree tools. The tool then works on the task's enclosure, where the code worktree and the
memory worktree stand in place of the two repositories.

## How agents reach knowledge

Once the memory repository is resolved, an agent reaches memory by path: the card of a source file
lies at the mirrored path under `onboarding/`. The tools `read_ar_files` and `knowledge_read` name
the cards and knowledge records that belong to a path; the agent opens the named files itself.
The skill holds the
[knowledge-use guidance](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/knowledge/use.md).

## Ownership Boundaries

`runtime_install` owns the files that the package copies into the coordination root.

[Onboard An Existing Repository](guides/onboard-existing-repo.md) describes the creation or repair
of a memory repository; the tool `memory_init` performs it.

The Worker writes code in the code worktree of its task. Only the Curator writes memory.

The Curator follows the [card procedure](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md), the skill's rules for the layout of cards and for checking their citations, for the cards of source files and for the entity catalog of a repository.

The tool server owns the facts of [context resolution](concepts.md#context-resolution) only.
Callers ask for them with `context_packet(repo_id="<repo-id>")`, which the
[MCP tool reference](reference/mcp-tools.md) describes.

The worktree tools own worktree lifecycle, landing, and cleanup; the skill's [closeout operation](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/operations/closeout.md), its instructions for the Manager and the Orchestrator, keeps the boundaries of the roles and of the approvals.

The Manager, or the Orchestrator of a sprint, performs closeout. The closeout tools commit a task
in an enclosure, and `direct_landing` commits a task under direct execution; both commit the code
first and the memory content second. Memory commit messages carry `Code-Commit` attribution.
Writing `memory.md` anew does not rewrite historical commits.

Pushing a branch, opening a pull request and releasing belong to the user, unless the user
delegates them.

## What a Read May Write

A read is a tool that only reads, a `GET` route of the dashboard, `worktree_status`, and every
preview and dry run. A read changes no branch, no tag and no `HEAD`. It changes no file of a
working tree, no Git index, no task document and no requirement packet. A read writes in four
places only:

- It writes cache files under `<coordination-root>/runtime/`.
- When the review page records a [comparison](concepts.md#comparison) of a task's change, the
  read writes the record of the comparison into the task's reports folder, the Git objects of the
  compared trees, and one Git ref under `refs/ar/review/` that keeps those objects from Git's
  garbage collection. Agents Remember removes them when the master's task folder is archived,
  that is, moved into `<coordination-root>/tasks/<id>/0_archive/`, for example by the `task_doc`
  operation `retire_master`.
- A preview or a dry run of a closeout writes the task's worklist into its enclosure, its
  quality reports, and the Git objects of the tree that the knowledge gate judged.
- A `context_packet` call with `include_freshness` runs `git fetch` of the remote that the
  checked-out branch tracks, once in the code repository and once in the memory repository; a
  branch without an upstream is not fetched. The fetch writes inside the Git directory only: the
  fetched objects, the remote-tracking refs, a tag that the fetch follows, and `FETCH_HEAD`. It
  changes no local branch and no file of a working tree. Without the argument the read fetches
  nothing.

A read that cannot keep this boundary, for example because a cache folder cannot be written, is
refused by name. It does not write somewhere else.

A read, a preview and a dry run file no report under `<coordination-root>/temp/tool-reports/`.
Only an executed action that changes something files its report.
