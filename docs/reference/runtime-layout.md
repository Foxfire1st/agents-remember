# Runtime Layout

This page lists what an installation of Agents Remember puts on disk: the folders of the
coordination root, what an install does for the host, where role agents get their scratch folders,
and the files outside the coordination root.

Two calls create these folders. The first Bootstrap call creates the coordination root when it
does not hold the folders `<coordination-root>/docs` and `<coordination-root>/tasks` yet. The tool
`runtime_install` creates what is missing and renews the copied files on every call.

## The coordination root

A first install creates this tree:

```text
<coordination-root>/
  docs/
    tool-details.md
  logs/
    mcp/
    providers/
      codegraphcontext/  grepai/  setup/  status/
  memory-repos/
  notes/
  providers/
    compose/  docker/  patches/  requirements/
    data/
      codegraphcontext/  grepai/
    runners/
      codegraphcontext/  grepai/
  tasks/
  temp/
  worktrees/
```

| Folder | What it holds | Who writes it |
| --- | --- | --- |
| `<coordination-root>/docs/` | `tool-details.md`, the long descriptions that the short tool descriptions refer to | every install renews the file and removes every other file from the folder |
| `<coordination-root>/memory-repos/` | the memory repositories, one folder `ar-<id>` for each code repository | the install creates the folder; the tool `memory_init` creates a repository |
| `<coordination-root>/tasks/` | the task documents | the task-document tools |
| `<coordination-root>/worktrees/` | the enclosures, one for each task | the worktree tools; see [Worktrees And Closeout](worktrees-c09.md) |
| `<coordination-root>/notes/` | notes and reports | you and the role agents; the install creates the folder only |
| `<coordination-root>/temp/` | working files of the product, for example the full results of tools | the tools |
| `<coordination-root>/logs/mcp/` | the [tool log](../concepts.md#tool-log), one file `ar-tools-<date>.jsonl` for each day; the calls of a role agent on a task with an enclosure go to `<enclosure>/logs/` | the tool server; the setting `transcriptRoot` names another folder |
| `<coordination-root>/logs/providers/` | the folders `codegraphcontext/`, `grepai/`, `setup/` and `status/` for the logs of the two [search providers](../concepts.md#search-provider) `codegraphcontext` and `grepai`; no part of the product starts a search provider | the install creates the folders; see [Search provider folders](#search-provider-folders) |
| `<coordination-root>/providers/` | the files of the two search providers: Compose files in `compose/`, Docker build files in `docker/`, a patch in `patches/` and requirement files in `requirements/`; and the folders `data/` and `runners/`, with one empty folder for each search provider. No part of the product starts, indexes or queries a search provider | every install copies the files from the package; see [Search provider folders](#search-provider-folders) |

The tool server serves the skills from its own package; see [Skills](#skills).

More folders appear when the product is used:

| Path | What it holds | Written by |
| --- | --- | --- |
| `<coordination-root>/logs/dashboard/` | the dashboard's state `daemon.json`, its log `dashboard.log`, the log of the start before as `dashboard.log.1`, and `ensure.lock` | `agents-remember dashboard --daemon` |
| `<coordination-root>/logs/observer/` | the records the dashboard reads, among them `workspace/events.jsonl` and the provider samples under `<coordination-root>/logs/observer/providers/` | the product's own processes |
| `<coordination-root>/runtime/servers/<pid>.json` | one record for each running tool server and dashboard | each of those processes |
| `<coordination-root>/runtime/knowledge-index/` | the cache that `agents-remember knowledge-index` keeps when it is called with `--coordination-root <coordination-root>` | that command |
| `<coordination-root>/runtime/review-answers/`, `runtime/review-memos/`, `runtime/changeset-ranges/` and `runtime/capture-indexes/` | what the product keeps for the review page: prepared answers, memos, the ranges of change sets and the indexes of kept comparisons | the product's own processes |
| `<coordination-root>/controlplane/` | state that the product keeps between calls, among them `attention-notices.json` and the folder `lifecycle-enclosures/` | the product's own processes |
| `<coordination-root>/temp/tool-reports/<tool>/` | the full result of a tool call as one JSON file | the tool |
| `<coordination-root>/notes/reports/paseo-native-executions/` | the receipts of role starts that belong to no task, for example those of the Bootstrap role agent | `role_start` |
| `<coordination-root>/benchmarks/` | the benchmark material; see [Benchmarks](#benchmarks) | `runtime_install` with `include_benchmarks=true` |

## What `runtime_install` writes, removes and leaves

The tool takes five arguments. This page names `dry_run` and `include_benchmarks`;
[Tool details](../tool-details.md#runtime_install) describes all five, among them
`install_provider_deps`, `no_cache` and `experiment`.

**It writes:**

- the coordination root and the folders of the tree above, where they are missing;
- `<coordination-root>/docs/tool-details.md` and the files of the search providers, copied from
  the package;
- the folder `<workspace-root>/.agents-remember/role-executions/`, and the line
  `.agents-remember/role-executions/` in `<workspace-root>/.gitignore`;
- with `include_benchmarks=true`, the folder `<coordination-root>/benchmarks/` and the line
  `benchmarks/` in `<coordination-root>/.gitignore`;
- its full result as `<coordination-root>/temp/tool-reports/runtime_install/<time>-install.json`.

**It removes** these when it finds them; the product does not use them:

- the folder `<coordination-root>/scripts`;
- the files `<coordination-root>/AGENTS.md`, `<coordination-root>/system/AGENTS.md` and
  `<coordination-root>/tasks/AGENTS.md`;
- a file under `<coordination-root>/docs/` that the package does not carry;
- a file under `<coordination-root>/providers/` that the package does not carry, except in
  `data/`, and with `install_provider_deps=false` also except in `runners/`.

**It leaves alone** the memory repositories, the task documents, the enclosures and the notes. It
writes neither the user settings file nor the `paseoRuntime` block of the settings file.

With `dry_run=true` the call changes nothing. It counts what it would create, copy and remove,
says what it would do for the host, and writes no result file.

After the files, the call turns to the programs that run:

- **The host.** See the next section.
- **The dashboard.** A running dashboard that belongs to another build of the package is
  restarted. A dashboard that does not run stays stopped.
- **The tool servers.** The call restarts none. Its answer lists each running tool server and
  dashboard under `runningServers` with `currentness`. Its `state` is `current` when the process
  runs the Python files that its package holds now, `stale` when those files changed after the
  process loaded them, and `unknown` when the two cannot be compared. For a stale tool server the
  answer says "Restart or reconnect this MCP server."

## What the install does for the host

The host's settings are the `paseoRuntime` block of the settings file. `runtime_install` reads the
block and never writes it; the first Bootstrap call writes it once.

- **Without the block**, `runtime_install` reports `"configured": false` in the `host` part of its
  answer and installs nothing for the host.
- **With the block**, it downloads the Node that the build pins, installs the host's packages,
  writes the host's configuration, copies the plug-in and starts a host that does not run.
- **A running host keeps running.** When a change needs a restart, the answer names the reasons
  under `host.restartRequired` and the command `agents-remember paseo provision`.

[The Host](../host.md) tells what is fetched, how to see the host's state and how to restart it.

The host's files stand outside the coordination root:

| Path | What it holds |
| --- | --- |
| `~/.local/share/agents-remember/node/node-v<version>-linux-x64/` | the Node the host runs on; under `XDG_DATA_HOME` when that is set |
| `~/.cache/agents-remember/node/` | the download of that Node, removed after the install; under `XDG_CACHE_HOME` when that is set |
| `<installPrefix>/` | the host program; `~/.local/share/agents-remember/paseo/prefix/` in the block a first Bootstrap call writes |
| `<home>/` | the host's home; `~/.local/state/agents-remember/paseo/` in the block a first Bootstrap call writes |
| `<home>/config.json` | the host's own configuration; an install writes the keys it owns and leaves the others |
| `<home>/paseo.pid` | the host's process record |

Agents Remember keeps its own files in the host's home under `<home>/agents-remember/`:

| File | What it holds | Written by |
| --- | --- | --- |
| `plugin/` | the installed copy of the plug-in | the install |
| `embed.json` | the embed list the plug-in reads | the install |
| `plugin-loaded.json` | which plug-in files and which embed list the running plug-in loaded | the install |
| `runtime.lock`, `start-outcome.json` | the lock that lets one install or start run at a time, and the result of the last start | installs and starts |
| `config-previous.json` | the host's configuration as it was before a change of the provider entries; present only until the host accepted the change | the install |
| `role-agents.json` | for each started role agent, its role, its task and the path of its report file; the plug-in reads them | `role_start` |
| `host-activity.json` | what the host's agents do, for the dashboard | the plug-in |
| `acp-providers.json` | which harnesses the host runs over the Agent Client Protocol | the plug-in |
| `provider-limits.json` | the provider limits: for each usage report of the host that shows a used-up limit of a [model provider](../concepts.md#model-provider) account, the time work may resume; and for each role agent, the reports that its last usage read named | the product |
| `turn-ends.jsonl` | one JSON line for each message the plug-in sent back to an agent, each line it sent to a parent agent and each turn end it left alone | the plug-in |
| `turn-ends.1.jsonl` | the earlier `turn-ends.jsonl`; the plug-in turns the file over at 2 MiB | the plug-in |
| `turn-ends.error.json` | a failed write of `turn-ends.jsonl`, with its time and error | the plug-in |

## Scratch folders

A role agent gets a scratch folder and a report folder with its start. Which folders it gets
depends on its role.

| Role agent | Scratch folder | Report folder |
| --- | --- | --- |
| Worker, Reviewer or Curator of a task | `<enclosure>/scratch/<role>/<request-id>/` | `<enclosure>/role-reports/<role>/<request-id>/` |
| every other role agent | `<workspace-root>/.agents-remember/role-executions/<request-id>/scratch/` | `<workspace-root>/.agents-remember/role-executions/<request-id>/role-reports/` |

The install prepares the second row: it creates `<workspace-root>/.agents-remember/role-executions/`
and keeps the folder out of Git with the line in `<workspace-root>/.gitignore`.

Some work cannot stand inside an enclosure, for example the
[sandbox of the whole product](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md#a-sandbox-of-the-whole-product),
a disposable copy of the product for tests, or a program that refuses a long path, such as a Unix
socket, whose path Linux limits to 108 bytes. For that work Agents Remember keeps one folder for
each enclosure outside the coordination root: `/var/tmp/agents-remember/ar-<12 hex digits>/`. The folder is readable by its owner only.
A file `.agents-remember-root.json` in it names the repository and the enclosure it belongs to.
Agents Remember removes the folder when the enclosure's work has ended.

## Files outside the coordination root

| Path | What it is | Written by |
| --- | --- | --- |
| `~/.config/agents-remember/agents-remember-settings.json` | the settings file | you; the first Bootstrap call adds the `paseoRuntime` block |
| `~/.config/agents-remember/settings.json` | the user settings file | the Bootstrap role agent, with the tool `user_settings_init` |
| `<workspace-root>/.agents-remember/role-executions/` | scratch and report folders of role agents | `runtime_install` and `role_start` |
| `<workspace-root>/.gitignore` | one added line | `runtime_install` |
| the host's files (section [What the install does for the host](#what-the-install-does-for-the-host)) | Node, the host program and the host's home | the install |
| `/var/tmp/agents-remember/` | the scratch folders outside the enclosures | `role_start` |
| `~/projects/.agents-remember/handover-locators/<request-id>` | the handover locator: a link to the report folder of one role start, which holds the role agent's report file and its [handover](../concepts.md#handover) file, so that the path to the handover file stays short. The path lies under your home folder whatever the workspace root is. Agents Remember removes the link, and not the folder, when the time in which the role agent can be resumed ends | `role_start` |

The settings file and the user settings file stand under `XDG_CONFIG_HOME` when that variable is
set.

## Search provider folders

The install writes the folder `<coordination-root>/providers/`. Every install copies the package's
files of the two [search providers](../concepts.md#search-provider) `codegraphcontext` and `grepai`
into it: Compose files, Docker build files, a patch and requirement files. The install also creates
the folders `data/` and `runners/` in it, and the folders under
`<coordination-root>/logs/providers/`. No part of the product starts, indexes or queries a
provider.

Two callers ask for the watcher stop, which stops the Docker containers of a search provider that
run on the machine, and neither starts a provider:

- `runtime_install`, before the install renews the files of the search providers;
- the provider-degradation safety loop, which the dashboard's server
  runs for as long as the dashboard is up: every 30 seconds it samples the Docker
  state of the provider containers (the containers that carry the label of a search provider),
  records the sample under
  `<coordination-root>/logs/observer/providers/` and compacts that log, evaluates the recent
  window as `healthy`, `degraded` or `critical`, and — on the transition into `critical`, and
  only while the setting `providerDegradation.failSafeEnabled` is true — calls the same watcher
  stop. It is not a revival: it starts nothing, installs nothing, indexes nothing and
  queries nothing, and it is independent of any install or cleanup.
  [providerDegradation](settings-json.md#providerdegradation) describes its settings.

An install writes no provider block: the lifecycle document, the provider settings that it
generates, declares no providers (`{"enabled": false, "providers": {}}`). With no provider declared, a
stop selects nothing, stops nothing, and the safety loop still records the attempt and its result
in the degradation event, the record of a change of state under
`<coordination-root>/logs/observer/providers/`.

## Benchmarks

With `include_benchmarks=true`, `runtime_install` copies the package's benchmark material to
`<coordination-root>/benchmarks/`: the cases, the documents and the templates. A later install
renews that material. It keeps what you produced under `<coordination-root>/benchmarks/user-runs/`
and removes generated workspaces under `<coordination-root>/benchmarks/workspaces/`. It does not
touch the memory repositories.

The [benchmark runner](../concepts.md#benchmark-runner) builds the workspace of each case, the
folder in which Codex runs the case, under `workspaces/<case-id>/` with two environments:
`source-only/`, where Codex works without memory, and `with-memory/`, where it works with a memory
repository. Both hold the same source checkout, an `AGENTS.md` made from a template and a marker
file `.benchmark-root`, which Codex takes as the root of its project. The environment
`with-memory/` also holds a memory repository and a file `.codex/config.toml` that registers its
tool server. See [benchmark methodology](../benchmarks-methodology.md).

## Skills

The tool server serves the skills of its own package as MCP resources. The resource
`skill://index.json` lists them, and a harness reads a skill and its files from the running
server. The [Skills Reference](skills.md) names the skills.

## The source repository

The layout of the source repository, and the scripts that renew its generated copies, are
described for contributors in
[CONTRIBUTING.md](https://github.com/Foxfire1st/agents-remember/blob/main/CONTRIBUTING.md).
