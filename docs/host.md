# The Host

The host is the program that runs role agents and shows their chats. It is Paseo. The help of
`agents-remember paseo`, its report (`daemon`) and one message of the dashboard call the running
host the daemon. Agents Remember installs it, configures it and starts it. You install nothing
for it yourself.

A role agent is a chat of a harness that runs in one role for one assignment. The tools
`role_start` and `role_message` and the dashboard's launcher need a program that starts such a
chat, keeps it open, delivers messages to it and shows it in a browser. The host is that program.
The tool server and the dashboard start without it.

One installation has one host: one installed copy, one home folder and one listen address.

## What installs and starts it

Two calls and one terminal run install the host.

- The first [Bootstrap call](concepts.md#bootstrap-call),
  `role_start(role="bootstrap", request_id="<a UUID>")`, made in a chat of the harness in which
  you registered the tool server. It writes the host's settings when the settings file holds
  none, installs the host, starts it and checks that the plug-in is loaded.
  [Getting Started](getting-started.md) tells the whole first run.
- The tool `runtime_install`. When the settings file holds a `paseoRuntime` block, the call
  installs what is missing and starts a stopped host. Without the block it reports
  `"configured": false` and installs nothing.
- `agents-remember paseo provision --config <settings file>`, run in a terminal. It needs a
  `paseoRuntime` block, installs what is missing, writes the host's configuration and starts the
  host; it also restarts a running host when a [restart is owed](#when-a-restart-is-owed).

An install needs the network. It fetches two things:

1. **Node for the host.** Agents Remember downloads the Node archive of the version that the build
   pins from `nodejs.org`, compares its SHA-256 digest with the digest the package carries, and
   unpacks it. The download is limited to 128 MiB and 300 seconds. The host never runs on the
   Node of your shell.
2. **The host's packages.** Agents Remember runs `npm ci` with the lock file the package carries,
   which names `registry.npmjs.org`. The packages go into a staging folder. Agents Remember puts
   them in place only when the staged host reports the version the build pins. A failed install
   leaves the earlier one untouched. The step is limited to 900 seconds.

After that the install writes the host's own configuration, copies the plug-in into the host's
home, starts the host with a limit of 120 seconds, and loads the plug-in. A repeated install that
finds nothing to change makes only reading calls.

The build pins one version of the host and one version of Node. Settings cannot choose another.
For a running host, `agents-remember paseo status --config <settings file>` prints the host's
version under `version`, and the build's Node with its version under `node`.

## Where it lives on disk

| What | Default place | Decided by |
| --- | --- | --- |
| Node for the host | `~/.local/share/agents-remember/node/node-v<version>-linux-x64/` | `XDG_DATA_HOME` |
| Download staging, removed after the install | `~/.cache/agents-remember/node/` | `XDG_CACHE_HOME` |
| The host program | `~/.local/share/agents-remember/paseo/prefix/` | `paseoRuntime.installPrefix` |
| The host's home | `~/.local/state/agents-remember/paseo/` | `paseoRuntime.home` |

The host's command is `<installPrefix>/node_modules/.bin/paseo`. The home holds the host's
configuration `config.json`, its process record `paseo.pid`, the host's own files, and a folder
`agents-remember/` with the files Agents Remember keeps there.
[Runtime Layout](reference/runtime-layout.md) lists those files.

The two `paseoRuntime` places are the values the first Bootstrap call writes. That call follows
`XDG_DATA_HOME` and `XDG_STATE_HOME` when they are set.

## Ports

| Program | Default address | Setting |
| --- | --- | --- |
| Dashboard | `127.0.0.1:8765` | `dashboard.port` in the settings file, written `"dashboard": {"port": 8765}`, or `--port` |
| Host | `127.0.0.1:8766` | `paseoRuntime.listen` in the settings file |

Both addresses are on the local machine only.

**When the host's port is held.** Before a start, Agents Remember tests whether the listen address
can be bound. When another program holds the port, nothing is started and the report names
`listen_port_in_use`: "no other port is chosen and no daemon is left running". A Bootstrap call
then answers with `"ok": false`, `"status": "refused"`, `"refusal": "launch-refused"`,
`"preparationStatus": "host-provision-failed"` and a `nextAction`. The reason is the `detail`,
which names the stage that failed; a `detail` that is too long for the answer stands in the file
that `log` names. The call wrote the block
`paseoRuntime` into the settings file before it tried to start the host. Free the port, or edit
that block by hand: change `paseoRuntime.listen` and each `frameBaseUrl` in `paseoRuntime.embed`
to the new address, and leave each `dashboardOrigin`, which names the dashboard, as it is. Then
make the call again with the same `request_id`.

**When the dashboard's port is held.** The dashboard chooses no other port either.
`agents-remember dashboard --daemon` reports `failed` with the end of the dashboard's log, or with
the log's path, and exits with status 1. A Bootstrap call still starts its role agent; its answer carries
`"dashboardAddress": null` and the reason in `dashboardAddressDetail`.

## See its state

`agents-remember dashboard --status` prints the dashboard's state and, as its last line, the
host's state. For a running dashboard it also prints a line on the dashboard's build and lines on
the dashboard's background work. The host is `running`, `not running`, `not answering`,
`not installed` or `not configured`. The command exits with 0 when dashboard and host are up or
no host is configured, with 1 when the dashboard is down, and with 3 when a configured host is
down. It waits at most five seconds for the host.

`agents-remember paseo status --config <settings file>` prints one JSON document about the host,
with these fields. For a host that does not run, `running` is `false` and the fields of a running
host are `null`, empty or absent.

| Field | Meaning |
| --- | --- |
| `ok` | `true` when the command read the state; `false` with an `error` that names the step that failed |
| `home` | the host's home that the settings file names |
| `running` | whether the host of the configured home runs |
| `version`, `node` | the running host's version, and the build's Node with its version and path |
| `nodeExecutable` | the Node program that the running host runs on |
| `listen`, `serverId` | the address the host listens on and the id it gives itself |
| `plugin` | the plug-in's `id`, its `state` and an `error`; the state `running` is the working state |
| `embed` | the embed list the plug-in was given |
| `agentTools` | the host's setting `daemon.mcp.injectIntoAgents`, which gives every agent the host's own tools to create and message agents: `expected` is `false`, the value Agents Remember writes, `configured` the value in the host's configuration, and `differs` is `true` when the two differ |
| `providers` | each [host provider](concepts.md#host-provider) (a harness that the host can start), with `available` and `error` |
| `restartRequired`, `restartRemedy` | `["node"]` when the host runs on another Node than the build's, else an empty list; `restartRemedy` then names the command that restarts the host |
| `inheritedHarnessHomes`, `harnessHomeRemedy` | harness storage variables that the host took from the shell that started it and that no provider entry states; the remedy says to move each into `paseoRuntime.providers.<id>.env` |
| `sessionVariables`, `sessionRemedy` | session variables of a harness, for example `CLAUDE_CODE_SESSION_ID`, that the running host holds from the shell that started it; the remedy says to stop the host and start it anew |
| `staleRecord` | a process record in the home that names no running host |

The three `paseo` commands need `--config` with the absolute path of the settings file. Each
prints one JSON document. The exit status is 0 when the command did its work, 1 when a step
failed, and 2 when the command refused before doing anything: the settings are unusable, or they
hold no `paseoRuntime` block.

## Stop and start

**Stop.** `agents-remember paseo stop --config <settings file>` stops the host of the configured
home and no other. The answer says `"action": "stopped"` or `"action": "not running"`.

A stop has a cost. The command's help states it: "open sessions close, running turns and
permission prompts are lost; sessions can be resumed after the next start."

`agents-remember dashboard --stop` stops the dashboard only. The host and its role agents keep
running.

**Start.** The host has no start command of its own. Each of these starts an installed host that
is not running:

- `agents-remember dashboard --daemon`, or the dashboard started in the foreground;
- a start of the tool server when `dashboard.autoStart` is `true` in the settings file: once in
  each tool-server process, after the harness asked for the tool list or called a tool;
- the tool `runtime_install`;
- a Bootstrap call of `role_start`;
- `agents-remember paseo provision --config <settings file>`.

A dashboard start installs nothing. For a host that is not installed it prints "run
runtime_install to install this build's Node and host" and exits with status 3.

Agents Remember registers no system service for the host. After a reboot of the machine, one of
the starts above brings the host up.

## When a restart is owed

`runtime_install` and a Bootstrap call keep a running host running, so that its role agents go on.
Some changes reach only a host that starts anew. The two calls then change nothing in the running
host and report that a restart is owed.

| Where you see it | What it says |
| --- | --- |
| answer of `runtime_install` | `host.restartRequired` with the reasons, and a `message` that names `agents-remember paseo provision --config <settings file>` |
| answer of a Bootstrap call | the refusal `launch-refused` with `"preparationStatus": "host-restart-required"` |
| `agents-remember dashboard --status` | for a host on another Node, version or listen address, the host line names `agents-remember paseo provision --config <settings file>` |
| `agents-remember paseo status` | `restartRequired` holds `node` for a host on another Node, and `restartRemedy` names `agents-remember paseo provision --config <settings file>` |

The reasons are `node` (the host runs on another Node than the build's), `version`, `packages`
(the installed packages differ from the build's lock file), `listen`, and `setting:<path>` for a
host setting that is read only at a start: `setting:daemon.listen`,
`setting:features.webUi.enabled`, `setting:features.dictation.enabled` and
`setting:features.voiceMode.enabled`.

A host that runs on another Node than the build's, for example after a
[new version of the package](#after-a-new-version-of-the-package) pinned another Node, stays
reachable: `agents-remember paseo status`, `role_start`, `role_message` and the dashboard keep
working with it until the restart.

Make the restart with this command, from a terminal of your own, outside the host:

```bash
agents-remember paseo provision --config /home/<you>/.config/agents-remember/agents-remember-settings.json
```

The command installs what the build pins, writes the host's configuration, restarts the host and
loads the plug-in. Its JSON answer lists every change. The restart closes the chats of the role
agents, and running turns and pending permission prompts are lost; the command's help adds:
"sessions remain resumable."

**`--accept-harness-home-change`.** A harness finds its sign-in and its sessions through storage
variables: `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `CODEX_SQLITE_HOME`, `PI_CODING_AGENT_DIR`,
`HERMES_HOME`, and for OpenCode, a harness that the host can start and that has no install page
in this documentation, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`,
`OPENCODE_CONFIG_DIR` and `OPENCODE_DB`. A running host holds the values of the shell that started
it. Before `paseo provision` stops the host, it compares those values with the ones the new start
would get. When one differs, the command refuses with `harness-home-would-change` and stops
nothing. You then have three ways:

- run the command from a shell that holds the same values;
- write the values into `paseoRuntime.providers.<id>.env`, so that every start gets them;
- repeat the command with `--accept-harness-home-change`, when the role agents shall use the new
  places.

## After a new version of the package

A new version of the package can pin another host or another Node. After you installed it:

1. Reconnect or restart the harness, so that it starts the tool server of the new version.
2. Call `runtime_install`. It renews the copied files, restarts a running dashboard of the older
   version, and brings a stopped host to the new version.
3. Read the `host` part of the answer. When it names `restartRequired`, the running host was left
   as it was; make the restart as the section above describes.

## The `paseoRuntime` block

The block holds the host's settings. It stands in the settings file,
`~/.config/agents-remember/agents-remember-settings.json`, the file the tool server is given with
`--config`. It does not stand in the user settings file.

The first Bootstrap call writes the block once, when the settings file holds none. Agents Remember
never rewrites an existing block. Later changes are yours to make by hand.

Agents Remember reads the block each time it needs it, so an edit needs no restart of the tool
server. To bring an edit to the host, call `runtime_install` or run `agents-remember paseo
provision`. A running host takes a change of `providers` and of `embed` without a restart. A
change of `listen` needs a restart.

```json
{
  "paseoRuntime": {
    "installPrefix": "/home/<you>/.local/share/agents-remember/paseo/prefix",
    "home": "/home/<you>/.local/state/agents-remember/paseo",
    "listen": "127.0.0.1:8766",
    "providers": {},
    "embed": [
      { "dashboardOrigin": "http://127.0.0.1:8765", "frameBaseUrl": "http://127.0.0.1:8766" },
      { "dashboardOrigin": "http://localhost:8765", "frameBaseUrl": "http://127.0.0.1:8766" }
    ]
  }
}
```

The block is optional as a whole. When it is present, the five keys of this table are required,
and an unknown key is refused. A key `version` is accepted and chooses nothing: the build installs
the host version that it pins.

| Key | Meaning | Value the first Bootstrap call writes |
| --- | --- | --- |
| `installPrefix` | absolute folder that holds the host program as a private install | `~/.local/share/agents-remember/paseo/prefix` |
| `home` | absolute folder of the host's home; every call to the host names it | `~/.local/state/agents-remember/paseo` |
| `listen` | the host's listen address as `host:port`, with a port from 1 to 65535 | `127.0.0.1:8766` |
| `providers` | provider entries: host providers that you add, keyed by an id; Agents Remember hands them to the host unchanged | `{}` |
| `embed` | list of pairs: a `dashboardOrigin` that may show the host's chats, and the `frameBaseUrl` at which a browser on that origin reaches the host | the origins `http://127.0.0.1:<dashboard.port>` and `http://localhost:<dashboard.port>`, each with `http://127.0.0.1:8766` |

A provider entry adds a harness that the host does not offer by itself and that speaks the
Agent Client Protocol (ACP), the protocol over which the host runs such a harness. This entry adds
Hermes:

```json
{ "hermes": { "extends": "acp", "label": "Hermes", "command": ["hermes", "acp"] } }
```

The keys of an entry are the host's own, and the host checks them. `extends` names the host
provider that the entry builds on, here the host's provider `acp` for a harness on that protocol;
`label` is the harness's name, and `command` is the command line that starts it.

An entry may also carry an `env` object for one harness, for example with the storage variables
named above. An entry for `claude`, or one that extends it, is refused when its `options` or
`params` state `extraArgs`. Those arguments belong in `orchestration.harnesses.claude.extraArgs` of
the [user settings file](concepts.md#user-settings-file), `~/.config/agents-remember/settings.json`.
The [settings reference](reference/settings-json.md) describes the block in full.

### When the dashboard port changes

The `embed` list names the dashboard by its origin. The first Bootstrap call writes the origins
once, with the dashboard port that is set at that call. When you change `dashboard.port` or start the dashboard with another
`--port`, the list is yours to edit:

1. In each entry, write the new origin in `dashboardOrigin`, exactly as the browser shows it:
   scheme, host and port, without a path. Leave `frameBaseUrl` as it is.
2. Call `runtime_install`, or run `agents-remember paseo provision`, so that the host's plug-in
   gets the new list.

Until then the dashboard shows no chat of the host. Its request for the chat is answered with
`origin-not-listed` and the sentence "… is not a dashboard origin in paseoRuntime.embed".

## What you see in the host

The host serves its own page at its listen address, by default `http://127.0.0.1:8766`. The
dashboard shows the chats of that page inside its own pages. The answer of a Bootstrap call names
the role agent's chat as `chatAddress`.

- **Agents.** The host calls each chat it runs an agent. Each role agent is one agent of the host.
  Its title is `<Role> · <id of its task document>`, for example `Worker · <task id>`. A role
  agent without a task document is titled `<Role> · Projects`.
- **Projects and workspaces.** The host groups its agents in workspaces and its workspaces in
  projects. A role agent whose assignment belongs to a master stands in a host
  project named `<master id> · <master title>`, in a workspace named `<task id> · <task title>`.
  A role agent without a master stays in the workspace Projects.
- **Back to parent.** The host shows the agents of a workspace as a row of agent tabs. The message
  box of a chat shows the button "Back to parent" when the chat's agent was started by another
  agent and its tab is the selected, visible agent tab. The button opens the parent's chat. A
  layout of the host that draws no row of agent tabs shows no such button.

### Host profiles

The host keeps agent profiles in its own configuration, `<home>/config.json`, under
`daemon.agentProfiles`. You add and edit them on the host's own page, in its section "Agent
profiles". A profile whose name equals a role id exactly, such as `worker`, is the
[host profile](concepts.md#host-profile) of that role.

When `role_start` starts a role agent, Agents Remember reads the host profile of the role from the
running host. It takes the harness, the model, the effort, the permission mode, the feature values
and the notes that the profile names; notes of the profile take the place of the `notes` of the
role settings. [Harnesses](reference/harnesses.md) gives the order in which the start, the
host profile and the role settings decide each of them.

Agents Remember reads profiles and never writes them. When the host holds several profiles of
one role name, the first one counts. When the host cannot be read, the role settings decide.

## The plug-in

Agents Remember ships a plug-in for the host, `ar-plugin`, and installs it from a copy it keeps in
the host's home. The plug-in gives the host's page the embed list, the dashboard origins that may
show the host's chats, and inside the dashboard it gives the page the dashboard's theme and fonts.
It publishes what the agents do for the dashboard, and tells a parent agent when a role agent's
turn ended without a [hand-off](concepts.md#hand-off).

## Platforms without a host

The host runs on Linux on x86_64 with `/proc`. WSL counts as Linux. On Windows, run Agents Remember
inside WSL; the package does not run on Windows itself.

On another platform, for example macOS or Linux on ARM, Agents Remember installs no Node and no
host. The refusal is `node_platform_unsupported` and says "Node … for the host supports Linux x86_64
with /proc only; … nothing installed".

- A Bootstrap call creates the folders of the coordination root and writes the `paseoRuntime`
  block. It then answers with the refusal `launch-refused` and
  `"preparationStatus": "host-provision-failed"`, and starts no role agent.
- `agents-remember dashboard --status` says in the host line: "this build does not support a host
  here; no host install or start is available".
- The package is declared for Linux and macOS.
- Role agents do not run there. `role_start`, `role_message` and the dashboard's launcher need the
  host.

## What the host needs

These entries are about the host itself. Each one says what you see without the setting, the
setting with its file and key or command, and who makes it. The pages of the single harnesses are
listed in the [harness reference](reference/harnesses.md).

- **An agent that sits idle for 60 minutes is closed.** That is the host's own default.
  Setting: `agents.idleRuntimeCleanup` is `false` in the host's configuration, `<home>/config.json`.
  It is a key of the host, not of the settings file.
  Who: Agents Remember writes it with every install, also over a value you set; you do not edit it.
- **A harness for which the host has no host provider cannot be chosen for a role.** The start is
  refused with `agent_not_offered`.
  Setting: a provider entry `paseoRuntime.providers.<id>` in the settings file, for a harness that
  speaks the Agent Client Protocol. `agents-remember paseo status --config <settings file>` lists
  each host provider under `providers`, with `available` and `error`.
  Who: you write the entry; Agents Remember hands it to the host unchanged.
- **A role agent also carries the tools of the tool server that you registered under the name
  `agents-remember`.** This holds on every harness whose configuration registers that tool
  server, except Claude Code. The whole result of `role_start`, in the file that `log` names,
  says `"secondCopyLoaded": true` for every start on another harness than Claude Code, also when
  no such registration exists.
  Setting: none. The host offers no setting that keeps one server out of one start of those
  harnesses.
  Who: Agents Remember. The first message of a role agent names the tool server
  `agents-remember-task` for every tool call. On Claude Code, the start keeps the second copy out.
- **A reason you give when you deny a permission prompt does not reach an agent on Codex or on
  Hermes.** The host hands those harnesses only the decision. On Claude Code, the host hands your
  words on. The pages for Codex and for Hermes hold the entry of each harness.
  Setting: none. Deny the prompt, then send the reason to the agent as a message.
  Who: you.
  Checked: in the source of the host this build installs. No prompt was denied for it.
- **A message into a running turn can replace that turn.** The host hands a message into the
  running turn when the harness can take it. When the harness cannot, the host ends the turn and
  starts a new one with the message; Claude Code and Codex cannot take a message while the agent
  compacts its conversation. The answer of `role_message` then says `"taken": "replaced"`.
  Setting: none.
  Who: nobody sets anything; the answer tells you that it happened.
  Checked: in the source of the host this build installs and in the code of Agents Remember. No
  `role_message` was sent for it.
