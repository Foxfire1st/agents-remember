# PNT sandbox

Development tooling for running a PNT build of Agents Remember without touching the live roots
(requirement PNT-R11). The sandbox is one disposable directory that holds everything the build's
dashboard, tool server and Paseo runtime use. It is not part of the shipped wheel.

```
python3 scripts/pnt-sandbox.py build                 # create the sandbox; safe to repeat
python3 scripts/pnt-sandbox.py start <checkout>      # Paseo runtime on 6820, dashboard on 9797
python3 scripts/pnt-sandbox.py stop                  # stop what start started
python3 scripts/pnt-sandbox.py check                 # the safety check on its own
python3 scripts/pnt-sandbox.py reset                 # stop, then delete the sandbox directory
```

`<checkout>` is the PNT build to run, for example this checkout. Every command takes
`--sandbox <directory>`; the default is `~/.local/state/ar-pnt/sandbox`. The commands run with
the system Python (3.10 or newer) or with a checkout's `mcp/.venv`. Two modules are different:
`build_roots.py` and `build_tool_calls.py` run inside the Python environment of the PNT build
under test and import that build's own code.

The directory has no `__init__.py`, on purpose. `scripts/pnt-sandbox.py` puts `scripts/` on the
import path and imports `pnt_sandbox` as a namespace package. A package there would make
`scripts/` an import root in the repository's dependency facts, under which the script-local
imports of `scripts/e2e_harness` no longer resolve and the test-evidence catalog stops validating.

Rules for using it:

- One command at a time. `build`, `start`, `stop` and `reset` hold a lock (`<sandbox>.lock`,
  beside the directory) for the whole command; a second one refuses and names the holder. The
  provision run of a start shares the lock: if the start is killed, the sandbox stays locked
  until that run has ended, and the refusal names it.
- One directory is one sandbox however `--sandbox` spells it: the path is resolved once, so a link
  to the directory and a path with `..` in it name the same lock, settings file and process record.
- Start and stop the Paseo runtime through `start` and `stop` only. Do not run
  `agents-remember paseo provision` directly on the sandbox settings: a daemon started that way
  carries the caller's environment, the calling harness session's variables among them, and
  hands it to every agent. `start` refuses such a daemon and names the variables; `stop`, then
  `start`, replaces it. `agents-remember paseo status` is safe.
- The sandbox directory must not lie under a folder that holds per-project harness configuration,
  nor inside a harness configuration directory (`.claude`, `.codex`, `.pi`, `.hermes`, `.dsh`).

## What the sandbox contains

| Path | Content |
| --- | --- |
| `projects/` | the Projects folder; `projects/sandbox-app` is a small Git repository with one test |
| `remotes/sandbox-app.git` | the repository's origin, so leaf enclosures can be opened |
| `coordination/` | the coordination root: one sprint, one master, two leaves with approved requirement packets, and the memory repository `memory-repos/ar-sandbox-app` |
| `settings/agents-remember-settings.json` | the settings of the dashboard and tool server, with the `paseoRuntime` block |
| `paseo/home`, `paseo/prefix` | the Paseo runtime's home and install prefix |
| `eve/` | the Eve application, its launcher and the list of variables an env file may not set |
| `dagger-authority/` | the registry of the build's quality tools, which the build otherwise keeps in the user's home |
| `run/` | the process record, the dashboard log and the sandbox's own tmux socket |

The coordination root, the memory repository and the task documents are created by the tool
server of the build under test (`runtime_install`, `memory_init`, `memory_baseline_adopt`,
`task_doc`), so they are documents that build can read. A rebuild leaves the repository, the task
documents, the packets, the memory repository and the Paseo install and home alone. It writes the
settings file anew, copies the four Eve application files again from the developer's Eve project
(`eve/app`: `package.json`, `tsconfig.json`, the agent definition and the channel), and writes
`eve/app/agent/instructions.md` and the Eve launcher anew from the tool, so hand edits to those
are lost.

A sandbox built by an earlier version of this tooling is rebuilt once by the next `start`: the
build adds what is new (`dagger-authority/`, `eve/removed-variables.json`, the Pi provider entry
of the settings file).

`reset` deletes the sandbox's marker last. If something cannot be deleted, it says so and names
what is left by its full path. What is left is still marked as this tool's and as being reset:
nothing is built or started there, and `reset` can be run again once the obstacle is gone.

## What start does, in order

1. Refuses a checkout that is not a PNT build, a sandbox directory in a refused place, a
   sandbox whose reset did not finish, and a second command on the same sandbox.
2. Refuses while a dashboard or a Paseo supervisor of this sandbox runs that no record names.
   Refuses a reserved port held by a process the sandbox did not start: it names the port and
   the process id and never uses another port. Refuses a running Paseo runtime of the sandbox
   that does not carry the sandbox's environment, naming the variables.
3. Reports `already running` with the URL when both of its processes run and hold their ports.
   The safety check is not run again in that case: after editing the settings file by hand, run
   `check`.
4. Builds the sandbox when it is missing or was built by an earlier version of this tooling. The
   build creates the checkout's `mcp/.venv` when that is missing.
5. Builds the checkout's dashboard bundle when it is missing or stale. Environment and bundle
   are ignored build products; nothing else in the checkout is written.
6. Runs the safety check and refuses when it fails, then looks at the two ports again.
7. Deletes a stale Paseo process record (see Processes), provisions the Paseo runtime through
   `agents-remember paseo provision` and starts the dashboard from the checkout's source. When
   the dashboard does not answer in time, what this start itself started is stopped and the
   failing step and its log file are named. A Paseo runtime that ran before this start is left
   running.

## The safety check

`build_roots.py` runs inside the checkout's Python environment, with the environment the sandbox
gives its processes, and resolves with the build's own configuration loader and resolver every
root the dashboard and the tool server would use for the sandbox settings:

- every path the loaded configuration holds, under whatever key (coordination root, Projects
  folder `workspaceRoot`, transcript and skill roots, repository and memory roots, provider
  roots, the Paseo home and prefix, and for example an `orcaRuntime` block);
- the roots the build derives: task root, leaf enclosures, receipts, reports, observer and
  dashboard directories, the agentic settings file, the Dagger authority root, and what the
  build's context resolver answers for each repository.

The check passes only when each of them resolves inside the sandbox directory, every root the
resolver is expected to report is there, the dashboard port is 9797 and not auto-started, the
Paseo listen address is `127.0.0.1:6820`, the Paseo version is the pinned one and both embed
entries frame `http://127.0.0.1:6820`. A root that is outside or cannot be resolved fails the
check and is named. A later change that moves one of the build's functions used by
`build_roots.py` makes that root unresolved until the script is updated.

## Processes

`start` records the dashboard and the Paseo supervisor in `run/processes.json` (process id,
start time, command line, boot id).

- The dashboard is trusted or signalled only while `/proc` shows that exact process, running the
  build's dashboard on this sandbox's settings file with the sandbox as its working directory. A
  record written by the earlier tooling (without a boot id) is read the same way.
- The Paseo runtime is the daemon of the sandbox's own home, named by the home's process record
  `paseo/home/paseo.pid`, as for the stop command of PNT-R01. It is trusted only when the named
  process is a Paseo supervisor that names this home in its own environment. A record that names
  anything else, or no process at all, is stale: `start` and `stop` delete it, say so, and do not
  signal the process it named. No runtime command is run while such a record exists. When the
  named process is a supervisor whose environment cannot be read, nothing is deleted, signalled
  or started, and the command says so.
- What the records do not name is looked for among all running processes, whether or not it
  holds a port: a dashboard by its command line and working directory, a supervisor by the home
  its environment names. Such a process (left by a start that was killed between starting and
  recording it) is reported by `stop` and refused by `start`, and never signalled; `reset` then
  keeps the directory. End it yourself with `kill <pid>`.

`stop` ends the recorded dashboard and every process left in that dashboard's own session, the
Paseo runtime through `agents-remember paseo stop`, and the sandbox's own tmux server when one
runs. It signals nothing else.

## Environment

Every process the sandbox starts receives the caller's environment without the variables that
select another AR runtime, coordination root, repository or Paseo home, and without the variables
that tie a process to the harness session the command was run from; `environment.py` lists each
with its reason. Session variables are removed by exact name, never by the `CLAUDE_`,
`CLAUDE_CODE_` or `CODEX_` prefix: harness logins, credentials and home selectors are kept. Each
child's `PWD` is the directory it is started in. Four variables are set: `TMUX_TMPDIR` (the
sandbox's own tmux server), `PYTHONPYCACHEPREFIX` and `GIT_OPTIONAL_LOCKS=0` (nothing is written
into the checkout) and `AR_DAGGER_AUTHORITY_ROOT` (the registry of the quality tools lies in the
sandbox).

A tool server that an agent's harness starts gets these variables only if the harness forwards
its environment or the launch puts them into the tool server's definition. The launch code of the
PNT build (`mcp/src/agents_remember/cli/paseo_launch.py`) does that for all four: it carries
`GIT_OPTIONAL_LOCKS`, `PYTHONPYCACHEPREFIX`, `TMUX_TMPDIR` and `AR_DAGGER_AUTHORITY_ROOT` into the
definition whenever the launching process has them.

## Pi

The settings file gives the sandbox's Paseo runtime a provider entry for Pi that replaces Pi's
command: `pi --no-extensions -e builtin:mcp -e builtin:codemode -e builtin:tool-search`. Pi then
starts without any of the developer's Pi extensions and with its own MCP, script and tool-search
parts, which hold only the tool server a launch gives the agent (`agents-remember-task`).

The reason is one kind of extension: a gateway to the tool servers of the developer's own Pi
configuration. Through it a sandbox agent can search, list, connect to and call the installed AR
tool server on the live roots, and an agent that did not find `agents-remember-task` at once did
connect to it. Instructions forbid that; the entry makes it impossible.

The cost: a Pi agent of the sandbox runs without every extension and package of the developer's
Pi, not only the gateway. Nothing under `~/.pi` is read differently or written: the developer's
Pi configuration, logins and models stay as they are, and a Pi started outside the sandbox loads
its extensions as before. Outside the sandbox the entry is the developer's choice
(`paseoRuntime.providers` of the settings file); without it only the instructions apply.

## Eve

The Eve provider entry starts `eve-acp-launcher.mjs`, which runs `eve acp` inside
`eve/app`. That application is made from the developer's Eve project (`--eve-project`, default
`~/projects/.eve`): its `package.json`, `tsconfig.json`, agent definition and channel are copied,
its `node_modules` is linked, and its env file is loaded by path at launch and never copied. A
name in the env file that the sandbox removes or sets is skipped. The project's connections are
not copied, because they point at the developer's installed AR tool server. Without such a
project the Eve entry is omitted and only the Hermes entry is written.

## Writes outside the sandbox that the sandbox cannot prevent

The live roots are not among them. These are writes of other programs, in their own places:

- **Harness programs started by the dashboard.** The build's dashboard starts the developer's
  harness programs when its harness routes are called (`GET /api/harnesses/<harness>/capabilities`,
  `GET /api/harnesses/<harness>/conversations`): `codex app-server`, `claude -p`, `pi --mode rpc`.
  They write their own state in their homes: Codex its databases, model cache and plugin cache
  under `~/.codex`; Claude Code `~/.claude.json`, sessions and a project folder under `~/.claude`;
  Pi a session folder under `~/.pi/agent`. No model turn is made.
- **Harness programs started by Paseo.** Agents use the developer's harness logins, so every
  agent writes its sessions into its harness's home, and Paseo's provider checks start the
  harness programs as well.
- **The installed AR tool server inside an agent.** Codex, Claude Code, Pi and Hermes each
  carry the developer's installed AR tool server (named `agents-remember`, on the live roots) in
  their user-level configuration. A Codex, Claude Code or Hermes agent in the sandbox therefore
  sees it next to the sandbox's own `agents-remember-task`, and the harness starts it with every
  session. It is the installed runtime, not the build under test; the sandbox cannot remove it
  there. A Pi agent of the sandbox does not have it (see Pi above).
- **Package-manager caches.** Installing Paseo writes npm's cache and logs (`~/.npm/_cacache`,
  `~/.npm/_logs`); creating the checkout's Python environment writes uv's cache.
- **The Eve project.** `eve/app/node_modules` is a link into the developer's Eve project;
  whatever Eve writes below `node_modules` lands there.
- **The citation source-index cache.** The build's memory-quality tools keep it at
  `$XDG_CACHE_HOME/agents-remember/citation-source-index`, or under `/tmp/ar-cache-<uid>` when
  that variable is unset, shared with the installed runtime. The sandbox does not set
  `XDG_CACHE_HOME`, because every harness program would inherit it. The cache has four slots; a
  slot is chosen by a SHA-256 over the pair of code root and memory root paths, and its manifest
  records those two roots, the candidate tree and a content hash per indexed file. A slot whose
  manifest names other roots is rebuilt, so a sandbox closeout can at most cost the installed
  runtime a rebuild of one slot.
- **The provider container listing.** The dashboard runs
  `docker ps --all --filter label=agents-remember.provider` on every projection. It reads the
  containers of the installed runtime's providers and shows none of them.
