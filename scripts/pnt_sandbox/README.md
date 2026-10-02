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
the system Python (3.10 or newer) or with a checkout's `mcp/.venv`.

## What the sandbox contains

| Path | Content |
| --- | --- |
| `projects/` | the Projects folder; `projects/sandbox-app` is a small Git repository with one test |
| `remotes/sandbox-app.git` | the repository's origin, so leaf enclosures can be opened |
| `coordination/` | the coordination root: one sprint, one master, two leaves with approved requirement packets, and the memory repository `memory-repos/ar-sandbox-app` |
| `settings/agents-remember-settings.json` | the settings of the dashboard and tool server, with the `paseoRuntime` block |
| `paseo/home`, `paseo/prefix` | the Paseo runtime's home and install prefix |
| `eve/` | the Eve application and launcher of the Eve provider entry |
| `run/` | the process record, the dashboard log and the sandbox's own tmux socket |

The coordination root, the memory repository and the task documents are created by the tool
server of the build under test (`runtime_install`, `memory_init`, `memory_baseline_adopt`,
`task_doc`), so they are documents that build can read. A rebuild leaves existing ones alone.

## What start does, in order

1. Refuses a checkout that is not a PNT build, and a reserved port held by a process the sandbox
   did not start. It names the port and the process id and never uses another port.
2. Reports `already running` with the URL when both of its processes run.
3. Creates the checkout's `mcp/.venv` and dashboard bundle when they are missing or stale. These
   are ignored build products; nothing else in the checkout is written.
4. Builds the sandbox when it is missing.
5. Runs the safety check and refuses when it fails.
6. Provisions the Paseo runtime through `agents-remember paseo provision` and starts the
   dashboard from the checkout's source. When the dashboard does not answer in time, what this
   start itself started is stopped and the failing step and its log file are named.

## The safety check

`build_roots.py` runs inside the checkout's Python environment and resolves, with the build's own
configuration loader and resolver, every root the dashboard and the tool server would use for the
sandbox settings: coordination root, Projects folder, repository and memory roots, task root,
leaf enclosures, receipts, reports, transcript and skill roots, and the Paseo home and prefix. The
check passes only when each of them resolves inside the sandbox directory, the dashboard port is
9797 and the Paseo listen address is `127.0.0.1:6820`. A root that is outside or cannot be
resolved fails the check and is named. A later change that moves one of the build's functions
used by `build_roots.py` makes that root unresolved until the script is updated.

## Processes

`start` records the dashboard and the Paseo supervisor in `run/processes.json` (process id,
start time, command line). The dashboard is trusted or signalled only while `/proc` shows that
exact process. The Paseo runtime is the daemon of the sandbox's own home, named by the home's
process record as for the stop command of PNT-R01; it is trusted only when the named process is a
Paseo supervisor and is either exactly the recorded one or names this home in its own
environment. `stop` signals nothing else: the dashboard directly, the Paseo runtime through
`agents-remember paseo stop`. A supervisor it cannot prove is left running and reported.

## Environment

Every process the sandbox starts receives the caller's environment without the variables that
select another AR runtime, coordination root, repository or Paseo home; `environment.py` lists
each with its reason. The variables that tie a process to the harness session the command was run
from are removed as well, by exact name (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`,
`CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_MESSAGING_SOCKET`, `CLAUDE_CODE_MESSAGING_TOKEN`,
`CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_EXECPATH`, `CLAUDE_CODE_SESSION_ATTENDED`, `CLAUDE_EFFORT`,
`CLAUDE_PID`), so an agent started in the sandbox does not join that session or inherit its effort
setting. Nothing is removed by the `CLAUDE_` or `CLAUDE_CODE_` prefix: harness logins and
credentials are kept. Three variables are set:
`TMUX_TMPDIR` (the sandbox's own tmux server), `PYTHONPYCACHEPREFIX` and `GIT_OPTIONAL_LOCKS=0`
(nothing is written into the checkout).

A harness that carries an AR tool server in its own user-level configuration still starts that
server, on the roots that configuration names, whenever the harness starts a session. The sandbox
cannot prevent this; it concerns the installed AR runtime, not the build under test.

## Eve

The Eve provider entry starts `eve-acp-launcher.mjs`, which runs `eve acp` inside
`eve/app`. That application is made from the developer's Eve project (`--eve-project`, default
`~/projects/.eve`): its `package.json`, `tsconfig.json`, agent definition and channel are copied,
its `node_modules` is linked, and its env file is loaded by path at launch and never copied. Its
connections are not copied, because they point at the developer's installed AR tool server.
Without such a project the Eve entry is omitted.
