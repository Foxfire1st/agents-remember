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

`build`, `check`, `start` and `stop` accept `--host-port` and `--dashboard-port`.
defaults remain 6820 and 9797. Supply the same chosen pair to each command on that
sandbox; the settings, safety check and listener ownership use that pair.
The ports must differ and cannot be 9785 or 9786. A foreign holder refuses the start;
the tooling never selects another port. `reset` still identifies owned processes by
their exact configuration/home records and needs no port arguments.

For example, with an independent recorded pair:

```text
python3 scripts/pnt-sandbox.py build --sandbox /path/to/sandbox --host-port 6871 --dashboard-port 9871
python3 scripts/pnt-sandbox.py check --sandbox /path/to/sandbox --checkout /path/to/product --host-port 6871 --dashboard-port 9871
python3 scripts/pnt-sandbox.py start /path/to/product --sandbox /path/to/sandbox --host-port 6871 --dashboard-port 9871
python3 scripts/pnt-sandbox.py stop --sandbox /path/to/sandbox --host-port 6871 --dashboard-port 9871
```

A different product checkout may use one named frozen tooling checkout: invoke that
tooling's `scripts/pnt-sandbox.py`, with the product's own SDK and `--checkout` (or
the `start` positional checkout). The sandbox settings owner belongs to the tooling and asks
the product for the host block it needs; the root resolver, tool server, install and dashboard
run the actual product. Record
both checkouts' HEAD/patch identities and the tooling freeze, then qualify safety and
public `server_info` with zero model turns. A missing product API or wrong package/root
refuses; do not substitute the tooling's runtime, copy a legacy settings block or
reinterpret that refusal. After the tooling lands, sync and use the landed version.
Borrowed evidence requires unchanged tooling files; changed inputs repeat only their
dependent qualification.

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
  does not receive the sandbox's complete root, Python and Git selector isolation. Product
  host starts remove the shared session list; the sandbox adds its own isolation. `start`
  refuses a daemon with foreign selectors and names them; `stop`, then `start`, replaces it. `agents-remember paseo status` is safe.
- The sandbox directory must not lie under a folder that holds per-project harness configuration,
  nor inside a harness configuration directory (`.claude`, `.codex`, `.pi`, `.hermes`, `.dsh`).

## What the sandbox contains

| Path | Content |
| --- | --- |
| `projects/` | the Projects folder; `projects/sandbox-app` is a small Git repository with one test |
| `remotes/sandbox-app.git` | the repository's origin, so leaf enclosures can be opened |
| `coordination/` | the coordination root: one sprint, one master, two leaves with approved requirement packets, and the memory repository `memory-repos/ar-sandbox-app` |
| `settings/agents-remember-settings.json` | dashboard/tool server MCP authority settings, including `paseoRuntime` |
| `config/agents-remember/settings.json` | the one user settings file for this sandbox |
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
   the candidate's public `runtime_install` tool and starts the dashboard from the checkout's source. When
   the dashboard does not answer in time, what this start itself started is stopped and the
   failing step and its log file are named. A Paseo runtime that ran before this start is left
   running.

## The safety check

`build_roots.py` runs inside the checkout's Python environment, with the environment the sandbox
gives its processes, and resolves with the build's own configuration loader and resolver every
root the dashboard and the tool server would use for the sandbox settings:

- every path the loaded configuration holds, under whatever key (coordination root, Projects
  folder `workspaceRoot`, transcript and skill roots, repository and memory roots, provider
  roots, the Paseo home and prefix);
- the roots the build derives: task root, leaf enclosures, receipts, reports, observer and
  dashboard directories, product Node and its archive cache, the agentic settings
  file, the Dagger authority root, and what the
  build's context resolver answers for each repository.

The check passes only when each of them resolves inside the sandbox directory, every root the
resolver is expected to report is there, the dashboard port is 9797 and not auto-started, the
Paseo listen address is `127.0.0.1:6820`, the Paseo version is the pinned one, both embed
entries frame `http://127.0.0.1:6820` and the settings file's provider entry for Pi is exactly
the one this tool writes (see Pi below): a settings file from which that entry was dropped or
changed fails `check` and `start` under the key `paseoRuntime.providers.pi`. A root that is
outside or cannot be resolved fails the check and is named. A later change that moves one of the build's functions used by
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
child's `PWD` is the directory it is started in. Eight variables are set: `TMUX_TMPDIR` (the
sandbox's own tmux server), `PYTHONPYCACHEPREFIX` and `GIT_OPTIONAL_LOCKS=0` (nothing is written
into the checkout), `AR_DAGGER_AUTHORITY_ROOT` (the registry of the quality tools lies in the
sandbox), `XDG_CONFIG_HOME` (the sandbox's own user settings file, see below), plus
XDG_DATA_HOME, XDG_STATE_HOME and XDG_CACHE_HOME for this sandbox's product data, state and
cache.

A tool server that an agent's harness starts gets these variables only if the harness forwards
its environment or the launch puts them into the tool server's definition. The launch code of the
PNT build (`mcp/src/agents_remember/cli/paseo_launch.py`) does that for all eight: it carries
`GIT_OPTIONAL_LOCKS`, `PYTHONPYCACHEPREFIX`, `TMUX_TMPDIR`, `AR_DAGGER_AUTHORITY_ROOT`,
`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME` into the
definition whenever the launching process has them. `XDG_CONFIG_HOME` is the one that decides
which host an agent's role tools address: Codex and Hermes hand a tool server only a few
variables of their own, so without it in the definition the tool server of a sandbox agent reads
the developer's settings file.

## The tool server

An agent drives a sandbox's tool server through this tooling's helper (`build_tool_calls.py`)
with a request file: `sandbox` names the sandbox the request belongs to, `hostPort` its Paseo
port, `config` the sandbox's MCP settings file, `expect` the roots the server must report, and
`calls` the tools to call. The caller's environment is not the server's: the helper starts the
server with `sandbox_environment` of that sandbox, so the caller's own `XDG_CONFIG_HOME`,
`HOME`-derived settings and `PASEO_*` variables never reach it, and a request that names no
sandbox is refused before anything starts.

Before its first call the helper resolves the Paseo home and the listen address with the build's
own code under that environment and compares them with the sandbox's own layout — home
`<sandbox>/paseo/home`, listen `127.0.0.1:<hostPort>` — refusing, naming both, when they differ.
A settings file inside the sandbox that points at another runtime, and a port that is not the
sandbox's (or one of the live ports), are refused the same way: a helper run from an agent's
shell cannot address another Paseo runtime. The server's environment is read back through the
tooling's own judgment (`environment.foreign_variables`) before it is started, so a selector that
still names another home or address refuses too.

One case is admitted with no host: a fresh build, whose asset bootstrap runs before the sandbox
writes its host settings. Then `host` in the report says `"no host configured"`, and no call
reaches any Paseo home or address — the developer's defaults and a default home under `HOME`
included — because the server's environment carries no selector and a call that needs a host
fails inside the build without one. When a host is configured, `host` names it, next to `ok`,
`packageRoot`, `results` and `error`. The sandbox must be started (`scripts/pnt-sandbox.py
start`) for its host to answer at all.

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
(`paseoRuntime.providers` of the MCP authority settings file); without it only the instructions apply.

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

- **Harness programs started by Paseo.** Agents use the developer's harness logins, so every
  agent writes its sessions into its harness's home, and Paseo's provider checks start the
  harness programs as well. They write their own state there: Codex its session files under
  its home (`CODEX_HOME` when the caller's environment sets it, else `~/.codex`) and its
  databases, model cache and plugin cache; Claude Code `~/.claude.json`, sessions and a project
  folder under `~/.claude`; Hermes its sessions and logs under `~/.hermes`; Pi a session folder
  under `~/.pi/agent`. The build's dashboard starts no harness program itself.
- **The installed AR tool server inside an agent.** Where the developer's user-level
  configuration of a harness lists the installed AR tool server (named `agents-remember`, on the
  live roots), an agent of that harness in the sandbox gets it next to the sandbox's own
  `agents-remember-task`. It is the installed runtime, not the build under test; the sandbox
  cannot remove it there. For a role agent on Claude Code the launch keeps its tools out of what
  the agent carries. Codex and Hermes offer nothing for one launch: there the harness starts
  both servers, the role start's answer says that a second copy is loaded, and the hand-over
  tells the agent to call `agents-remember-task` alone. A Pi agent of the sandbox does not have
  it (see Pi above).
- **Package-manager caches.** Installing Paseo writes npm's cache and logs (`~/.npm/_cacache`,
  `~/.npm/_logs`); creating the checkout's Python environment writes uv's cache.
- **The Eve project.** `eve/app/node_modules` is a link into the developer's Eve project;
  whatever Eve writes below `node_modules` lands there.
- **The citation source-index cache.** The build's memory-quality tools keep it at
  `$XDG_CACHE_HOME/agents-remember/citation-source-index`, or under `/tmp/ar-cache-<uid>` when
  that variable is unset. This sandbox sets XDG_CACHE_HOME to its own cache/
  directory, and the task-specific tool-server definition carries that value even
  when its harness does not forward the parent environment. Its four index slots
  are therefore sandbox-owned rather than shared with the installed runtime.
- **The provider container listing.** The dashboard runs
  `docker ps --all --filter label=agents-remember.provider` on every projection. It reads the
  containers of the installed runtime's providers and shows none of them.

The host install phase uses the candidate's public `runtime_install` tool after
server_info proves the exact sandbox roots and package root. Coordination asset
bootstrap happens before the sandbox setup supplies its paseoRuntime block in
`settings/agents-remember-settings.json`, the MCP authority; no per-harness fallback.
Node data/state/cache variables are confined to this sandbox, and install receipts
are kept under run/. Dashboard starts then ensure the already installed host and
never run npm or rewrite daemon/plugin configuration. One host home lock covers
install/start. The operator serializes runs, checks the current source and
free/owned ports, and retires this disposable tree with the public reset after
needed proof is saved. A surviving tree is not proof of a surviving process after
a machine restart. Each public install archives its prior request/receipt/logs
under run/host-install-history; a cut leaves no stale success at the current
receipt path. Reset owns that history's retirement. A deferred restart is a named
start refusal, so an old host cannot qualify the requested build silently.

Every sandbox process uses `XDG_CONFIG_HOME=<sandbox>/config` for AR user settings.
The developer's HOME and harness login directories are retained. An older sandbox
with `coordination/system/settings.json` requires the explicit whole-file upgrade
under this same owned `XDG_CONFIG_HOME`; build/start never reads that file as a
fallback or chooses between two files. The user settings move does not change the
separate `--config` authority file or the memory repository settings.
