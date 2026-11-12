# Settings Reference

Agents Remember reads four settings files: the settings file, the user settings file, the
repository settings file and the memory settings file. Three of them have the file name
`settings.json`; the table tells them apart by their location. Each file is plain JSON; a comment
in a file refuses the load.

| File | Location | Holds | Read |
| --- | --- | --- | --- |
| Settings file | `~/.config/agents-remember/agents-remember-settings.json`, or the file that `--config` names | the two roots, the code repositories, the dashboard, the [tool log](#tool-log) and the host block `paseoRuntime` | when the tool server or a command starts; `paseoRuntime` at each use |
| User settings file | `~/.config/agents-remember/settings.json` | the role settings under `orchestration` | at each use |
| Repository settings file | `<code-repository>/system/settings.json` (optional) | `orchestration` values for one code repository, laid over the user settings file | at each use |
| Memory settings file | `<memory-root>/system/settings.json` | where the cards are stored, which source files are eligible for a card, which other repositories may be read and the landing workflow | at each resolution of a repository's context |

Both `~/.config` locations stand under `XDG_CONFIG_HOME` when that variable is set.

A key that a loader does not know is handled in one of two ways:

- Inside `orchestration`, `dashboard`, `toolLog`, `providerDegradation`, `timeoutCaps` and
  `paseoRuntime`, an unknown key refuses the load. So does a key other than `instanceId` and
  `scope` in an entry of the top-level `providers`, which lists
  [search providers](../concepts.md#search-provider) by the ids `grepai-memory` and
  `codegraphcontext-code`; another id refuses the load as well. The message names the key; under
  `orchestration` it also names the file.
- At the top level of the settings file, inside an entry of `repositories` and everywhere in the
  memory settings file, an unknown key loads without a message and is read by nothing. Check the
  spelling of such a key yourself.

`<memory-root>/system/settings.md` is the prose guidance file beside the memory settings file.
When `settings.json` is absent there, the loader reads a fenced block of `settings.md` in its
place. It reads only a block fenced as `yaml` or `yml`, or a block without a language. Keep one
machine-readable source in a memory repository.

`memory_init` creates `<memory-root>/system/settings.md` when it is missing. When a memory
repository holds no `system/settings.md` and `<coordination-root>/system/settings.md` exists, the
resolution of the repository's context reads the user settings file as its memory settings file.

## Settings file

The tool server reads the settings file that `--config` names. Without `--config` it reads
`~/.config/agents-remember/agents-remember-settings.json`. The tool server refuses three files as
`--config`: the user settings file, a file whose path ends in `/system/settings.json`, and a
file that lies inside the coordination root. The refusal of the last case reads
`MCP settings must not live inside the coordinator root`; this refusal and two more of the
loader say "coordinator root" for the coordination root.

This example is a complete settings file:

```json
{
  "coordinationRoot": "/home/<you>/ar-coordination",
  "workspaceRoot": "/home/<you>/projects",
  "repositories": {
    "my-repo": {
      "certificationProfile": "ci/certification-profile.json"
    }
  },
  "dashboard": {
    "autoStart": false,
    "port": 8765
  },
  "toolLog": {
    "keepDays": 14,
    "maxBytes": 268435456,
    "enclosureMaxBytes": 67108864
  }
}
```

The repository holds a second example, `examples/mcp/settings.example.json`. Its three roots are
written in Windows form, and the loader refuses them as written: replace them with absolute
paths of your machine.

### Roots

`coordinationRoot` (required, an absolute path) names the coordination root. Every tool and
command works under it. The memory repository of the code repository `<id>` is
`<coordinationRoot>/memory-repos/ar-<id>`.

`workspaceRoot` (required, an absolute path) names the workspace root. The tool server takes
`<workspaceRoot>/<id>` as the path of the code repository `<id>`.

A root that is not an absolute path refuses the load, for example with
`MCP settings.coordinationRoot must be an absolute path`.

### Tool log

`transcriptRoot` (optional, an absolute path) names the folder of the tool log for calls outside
an enclosure, the transcript root. When the key is absent or `null`, that folder is
`<coordinationRoot>/logs/mcp`.

The tool server appends one line for every tool call to the file `ar-tools-<YYYY-MM-DD>.jsonl`
of the UTC day. It logs a refused call like any other. A call by a role agent that is bound to a
task with an enclosure is logged in that enclosure's `logs/`. Every other call is logged in the
transcript root. When a whole result is larger than the answer, the server writes the result to
a detail file beside the log, and the answer's `log` names that file.

A log line holds the arguments of the call. The tool server protects credentials in three ways:

- It writes `<omitted>` for the value of an argument key whose name names a credential, such as
  `password`, `secret`, `api_key`, `access_token` or `cookie`.
- It replaces by `<env>` every value, six characters or longer, of an environment variable whose
  name holds `key`, `token`, `secret`, `password`, `passwd`, `credential`, `auth` or `cookie`,
  wherever that value occurs in an argument.
- It replaces an argument value longer than 200 characters by its length and its digest.

`toolLog.keepDays` (a whole number from 1, default 14) bounds the age of the log in the
transcript root. The first call of a day removes the log files and detail folders of days older
than that number. In an enclosure's `logs/` the tool server removes nothing by age; the folder is
removed with the enclosure.

`toolLog.maxBytes` (a whole number from 1, default 268435456, which is 256 MiB) bounds the log
folder of the transcript root in bytes. Before a write that would exceed the bound, the tool
server removes earlier days whole, oldest first. A write that still does not fit is dropped and
counted, and the call succeeds.

`toolLog.enclosureMaxBytes` (a whole number from 1, default 67108864, which is 64 MiB) bounds
the `logs/` of one enclosure in the same way.

`agents-remember tool-log-stats <log folder>` reads one log folder and prints a line for each
tool and operation: the calls, then the median, the 90th percentile, the largest and the sum of
the tokens of its [tool answers](mcp-tools.md#tool-answers), and how often an answer passed its
bound and was cut. `--since` and `--until` each take a day as
`YYYY-MM-DD`, and `--json` prints the rows as JSON. The command only reads.

Before you conclude that a role message was not sent, search the message folder of its task and
the tool log in both places: the enclosure's `logs/` and the transcript root.
[MCP tools](mcp-tools.md#message-files-and-the-tool-log) describes how messages are filed.

### repositories

`repositories` (an object keyed by repository id) lists the code repositories the tool server
works on. An entry is an object with two optional keys.

`repositories.<id>.contractPath` names the file of a
[worktree contract](../concepts.md#worktree-contract). A relative path is resolved
against the coordination root, and a path outside the coordination root refuses the load. The
resolution of the repository's context and the dashboard's view of the repository read the key
to select that enclosure. `null` and an absent key select none.

`repositories.<id>.certificationProfile` names the one certification profile of the code
repository. The value is a path relative to `<workspaceRoot>/<id>`, in POSIX form, without `..`
and without a leading `/`; another form refuses the load. The tool server checks the form of
the value when it loads the settings file. No tool reads the profile through this key: a
certification that you request names its profile file itself. A repository without the key
closes out and lands like every other repository.
[Repository Certification Profiles](repository-certification-profile.md) describes the profile.

### dashboard

`dashboard.autoStart` (`true` or `false`, default `false`) is read at every start of the tool
server. With `true`, the tool server makes sure that a dashboard runs in the background on
`dashboard.port`: it adopts a running dashboard of its own build, starts one when none runs,
and restarts one whose version, Python source digest, host or port differs.

`dashboard.port` (1 to 65535, default 8765) is the port of that dashboard. The first setup of the
host also writes it into the two `dashboardOrigin` values of `paseoRuntime.embed`.

The background dashboard keeps its state and its log under `<coordinationRoot>/logs/dashboard/`.
`agents-remember dashboard --status` reports the dashboard and the host, and
`agents-remember dashboard --stop` stops the dashboard. `--status` gives the host five seconds
to answer. It exits with `0` when the dashboard runs and the host runs or is not configured,
with `1` when the dashboard does not run, and with `3` when the dashboard runs and the
configured host does not.

Every start of the dashboard starts the installed host when the host does not run. It installs
nothing and rewrites no configuration or plug-in file of the host. A dashboard started with
`--sim` touches no host. Stopping or restarting the dashboard leaves the host and its agents
running.

### providerDegradation

`providerDegradation` (optional) configures the provider degradation detector. Two keys switch
it: `enabled` (default `true`) runs the detector, and `failSafeEnabled` (default `true`) lets it
call a stop. A provider here is a [search provider](../concepts.md#search-provider); its Docker
containers carry the label `agents-remember.provider`. The detector runs in the dashboard's
server, the process that serves the dashboard. The stop it may call is the watcher stop: it
stops the watchers of the search providers, as `runtime_install` does before it refreshes its
files.

The detector is a safety check over the containers of search providers that run on the machine;
no part of Agents Remember starts such a container. It is a
background loop of the dashboard process, not an install step: every 30 seconds
it samples the Docker state of every labelled provider container, appends that sample to
`<coordinationRoot>/logs/observer/providers/metrics.jsonl`, republishes `metrics-current.json`,
evaluates the recent window and compacts the log, for as long as the dashboard is up,
independently of any install or cleanup. It is not a revival: it starts nothing, installs
nothing, indexes nothing and queries nothing. A change of state writes the degradation state and
an event under `<coordinationRoot>/logs/observer/providers/`. On the change into `critical`,
`failSafeEnabled: true` makes that same sample tick call the watcher stop,
the same stop that `runtime_install` calls, and the event records the attempt and its result
under `criticalFailsafe`. With `failSafeEnabled: false` the
event records `{"enabled": false}` and no stop is called. No tool of the tool server performs
that stop.

An install writes no provider block: the lifecycle document that it generates declares no
providers (`{"enabled": false, "providers": {}}`). With no provider declared, the stop selects
nothing, stops nothing, and the event still records the attempt and its result under
`criticalFailsafe`.

Fourteen more keys set the thresholds of the detector. A ratio is a number above 0 and at most 1;
every other key takes a whole number from 1.

| Keys | Defaults | What they set |
| --- | --- | --- |
| `memoryDegradedRatio`, `memoryCriticalRatio` | 0.80, 0.92 | the share of its memory limit at which a container counts as degraded or critical |
| `watcherLagDegradedCommits`, `watcherLagCriticalCommits` | 5, 20 | the number of files that the index of a search provider is behind; the names say commits, the detector counts files |
| `watcherLagDegradedMinutes`, `watcherLagCriticalMinutes` | 10, 30 | the minutes for which a lagging index has been served |
| `probeDegradedMs`, `probeCriticalMs` | 2000, 10000 | the time a probe of a search provider takes, in milliseconds |
| `setupFailureDegradedStreak`, `setupFailureCriticalStreak` | 2, 3 | the number of failed setups in a row |
| `degradedSamples`, `criticalSamples`, `healthySamples` | 3, 2, 3 | how many recent samples must be degraded, or critical, for the state `degraded` or `critical`, and how many healthy samples in a row return the state to `healthy` |
| `recentSampleLimit` | 120 | how many recent samples the detector reads |

### timeoutCaps.providerSetupSeconds

`timeoutCaps` holds two keys, `providerSetupSeconds` and `toolSeconds`; another key refuses the
load, and the message names the two. `timeoutCaps.providerSetupSeconds` (a whole number that is
not negative, default 1800) bounds in seconds the watcher stop of `runtime_install` and the
provider setup of `worktree_start`.

### timeoutCaps.toolSeconds

`timeoutCaps.toolSeconds` takes a whole number that is not negative. The key is accepted and
enforces nothing: no tool call is bounded by it.

### orchestration in the settings file

The role settings belong in the user settings file. A role setting in the settings file, such as
`orchestration.roles`, refuses the start of the tool server, and the refusal names the user
settings file.

### paseoRuntime

`paseoRuntime` is the host block of the settings file. The host is Paseo. The block describes
one installation of the host, one home folder of the host and one listen address. The host commands, the
dashboard's role launcher and the tools `role_start` and `role_message` read the block at each
use; `runtime_install` reads it at each call and never writes it.

On an installation whose settings file holds no block, the first start of the Bootstrap role
creates the block once, adds the key `settingsInit` with the value `"host-authority"`, which
nothing reads, and keeps the other keys of the file. It never changes an existing block. A start
of another role is refused while the file holds no block. Run one setup at a time: two writers
of the settings file at once are not supported.

This fragment shows the block as the product creates it, with one provider entry added, for
Hermes, for which the host has no built-in host provider:

```json
{
  "paseoRuntime": {
    "installPrefix": "/home/<you>/.local/share/agents-remember/paseo/prefix",
    "home": "/home/<you>/.local/state/agents-remember/paseo",
    "listen": "127.0.0.1:8766",
    "providers": {
      "hermes": { "extends": "acp", "label": "Hermes", "command": ["hermes", "acp"] }
    },
    "embed": [
      {
        "dashboardOrigin": "http://127.0.0.1:8765",
        "frameBaseUrl": "http://127.0.0.1:8766"
      },
      {
        "dashboardOrigin": "http://localhost:8765",
        "frameBaseUrl": "http://127.0.0.1:8766"
      }
    ]
  }
}
```

A block that is present must hold `installPrefix`, `home`, `listen`, `providers` and `embed`.
An unknown key in the block is refused at the next read of the block, not when the tool server
starts.

| Key | Fact |
| --- | --- |
| `installPrefix` | Absolute folder that holds the host's command `paseo` as a private npm install (`<installPrefix>/node_modules/.bin/paseo`). Nothing is installed globally. |
| `home` | Absolute home folder of the host (`PASEO_HOME`). Every call of the host's command names it with `--home`; no other home is read, changed or stopped. |
| `listen` | The host's listen address as `host:port`, with a port from 1 to 65535. Provision binds exactly this address and never chooses another port. |
| `providers` | Provider entries for harnesses that the host drives through the Agent Client Protocol, keyed by provider id. The object is written unchanged to the host's `agents.providers`; the host validates it. May be `{}`. |
| `embed` | The embed list: objects with exactly the two keys `dashboardOrigin` and `frameBaseUrl`. `dashboardOrigin` is the origin a browser opens the dashboard on, written exactly as a browser reports it: lower-case scheme; a host of `a-z`, `0-9`, dot and hyphen only (an international name in its `xn--` form); an IPv4 address as four decimal numbers; an IPv6 address in brackets, in hexadecimal groups with the longest run of zero groups collapsed (`[::1]`, not `[0:0:0:0:0:0:0:1]` and not `[::ffff:127.0.0.1]`); an optional port, but not the default port (`:80` for http, `:443` for https); no path. Any other spelling is refused, because the value is compared with a browser's origin as text. `frameBaseUrl` is the URL a browser on that origin uses to reach the host; it holds no credentials, query or fragment. Each origin appears once. May be `[]`. |

A provider entry may carry values that should stay private, for example an `env` map with a key
for the harness. Provision never puts a provider entry on a command line and never prints one.
It writes `agents.providers` into the host's own `config.json`, which only the host's user
can read. It has the host reload that file when the host runs, or read it back when the host is
stopped, and it names only the provider ids (`providerIds`) in its report. If the host refuses
the entries, provision undoes what it wrote and reports `provider_entries_refused` with the
host's text. If that call fails for another reason, for example a timeout, provision undoes the
write as well and reports `paseo_command_failed`. The host's refusal text names the offending
path and may quote a provider id and the value of an `extends` field, so neither is a place for
a secret. An answer of the host of an unexpected shape is never copied into a report. The settings file itself
holds the private value, so protect the file accordingly. The other keys of the block hold no
secret.

The storage folder of a harness belongs in the `env` of its provider entry, so that a provision
from another shell keeps the same sessions. The entries reach the host unchanged. This fragment
is the value of `paseoRuntime.providers`:

```json
{"codex": {"env": {"CODEX_HOME": "/home/<you>/codex", "CODEX_SQLITE_HOME": "/home/<you>/codex-state"}}}
```

`status` and `provision` report under `inheritedHarnessHomes` the names of storage variables
that the running host inherited and the provider entries do not state, with the remedy of
moving them into `env`. The package file
[`host-harness-homes.json`](https://github.com/Foxfire1st/agents-remember/blob/main/mcp/src/agents_remember/package_data/host-harness-homes.json)
lists those variables for each harness.
Before a restart, provision compares the values the running host has with the values this run
would supply. A difference refuses with `harness-home-would-change` before the host is stopped;
only that refusal names the old and the new values. Run provision with the old values, put them
into the provider's `env`, or accept the change with
`agents-remember paseo provision --config <settings file> --accept-harness-home-change`. Other
reports and command logs hold the names of the variables only.

A provider entry states no `extraArgs` for Claude, and none for an entry that `extends` it.
`options.extraArgs` and `params.extraArgs` of such an entry are refused when the block is read.
The message names the user settings file, the key and
`orchestration.harnesses.claude.extraArgs`. The host lays its configured provider options over
every start and every resume, including the resumes of a confined explainer that Agents
Remember cannot check. Extra arguments for role agents therefore go through `orchestration`; see
`orchestration.harnesses.<harness>.extraArgs` below.

The install brings the whole host tree, locked by the build, with `npm ci` and the product's own
Node. It checks the Node archive against the official SHA before it unpacks the archive. The
host is supported on Linux x86_64 with `/proc` only. Node lives under
`<data>/agents-remember/node/node-v<version>-linux-x64`. The download is staged under
`<cache>/agents-remember/node`; the staging folder is removed after success and after failure,
and the next pass removes an interrupted one. A target folder that the product did not create is
refused and never overwritten. The Node folder of another version stays: remove it yourself
when no host process uses it.

Every command of the host, every npm call and every call of Agents Remember to the running host
starts that Node by its absolute path. Only the
npm child gets the Node folder first on `PATH`; the agents of the host keep the `PATH` of the
process that started the host. The install changes no shell file, no global link and no npm
configuration of yours. A host start removes the host's own selector variables (names that begin
with `PASEO_` or `AR_SPAWN_`), the bindings of a role agent (such as `AR_ROLE` and
`AR_REQUEST_ID`) and the session names of a harness (such as `CLAUDECODE`,
`CLAUDE_CODE_SESSION_ID` and `CODEX_THREAD_ID`) from the environment, and keeps the logins. The
package file
[`host-session-environment.json`](https://github.com/Foxfire1st/agents-remember/blob/main/mcp/src/agents_remember/package_data/host-session-environment.json)
lists such bindings and session names, each with its reason. A running host that carries
session names is reported under `sessionVariables`, without their values; stop the host with
`paseo stop` and start it again to remove them.

Commands and tools that talk to a running host use the Node that this version of the package
names, when it is installed. Otherwise they use the Node executable of the running host:
`paseo status`, `role_start`, `role_message` and the dashboard reach a host that runs on another
Node. They start no host with that Node. `status` then names the reason under `restartRequired` and the command
that restarts the host under `restartRemedy`.

Three commands act on the block, and each prints one JSON document:

```text
agents-remember paseo provision --config <settings file>
agents-remember paseo status    --config <settings file>
agents-remember paseo stop      --config <settings file>
```

`--config` is required and names the settings file that holds the block. `runtime_install`
provisions the host during the install of the product, so no host command is an extra install
step. A repeated run with nothing to change only reads. A dry run reports what would happen to
Node, the host version, the start and a restart, and changes nothing. When a running host needs
a restart to take another Node, host version, host tree, listen address or start-only setting,
the install reports `restartRequired` and leaves the host running; settings that apply live are
still applied. `agents-remember paseo provision` in a terminal performs that restart.

The exit status is `0` on success, `1` when a step failed (`error.step` names the step and
`error.detail` carries the host's text) and `2` when the command refused before it did anything,
for example with `paseo_runtime_not_configured` when the block is absent. A settings file that
cannot be read as UTF-8 JSON is refused with `settings_invalid`. Every failure ends in the JSON
document with an `error` object of `code`, `step`, `message` and `detail`; a refusal has `code`
and `message`. No failure ends in a traceback.

`provision` brings the host to the configured state and lists every change it made under
`changes`. A command that failed is reported under `error`, not as a change. A run with nothing
to change reports `"changed": false`, makes only reading calls and writes no file. Provision
writes exactly these settings of the host and leaves every other key of the host's `config.json`
alone:

| Host setting | Value | Applied |
| --- | --- | --- |
| `daemon.listen` | `listen` | at start |
| `daemon.relay.enabled` | `false` | live |
| `daemon.mcp.injectIntoAgents` | `false` | live |
| `agents.idleRuntimeCleanup` | `false` | live |
| `features.webUi.enabled` | `true` | at start |
| `features.dictation.enabled` | `false` | at start |
| `features.voiceMode.enabled` | `false` | at start |
| `pluginsEnabled` | `true` | live |
| `agents.providers` | `providers` | live (written into the file, then reloaded) |

Dictation and voice mode are switched off before the first start, so the host downloads no
speech model. The host's own agent tools are switched off for every agent. Idle runtime cleanup is
switched off: with it on, the host closes an agent that has been idle for 60 minutes, and Agents
Remember reads a closed agent as ended.

`daemon.action` in the report is `started` (the host was not running), `restarted` (it ran
another version, or a setting that applies only at start changed; `daemon.reasons` says which),
`reloaded` (only live settings, the embed list or the plug-in changed) or `untouched`. A restart
closes the sessions of running agents; the agents stay resumable. A change of only the host
part of `listen` is such a restart when the home's own host process holds the new address: the
same port, and a wildcard host or two host names for one address. Any other new address is
probed first. If another process holds it, provision refuses and does not touch the running
host.

Agents Remember keeps three things under `<home>/agents-remember/`: `plugin/`, the installed
copy of the plug-in `ar-plugin` that ships inside the package; `embed.json`, the embed list the
plug-in reads; and `plugin-loaded.json`, a stamp of what the running plug-in last loaded. A change
to the plug-in's content or to the embed list replaces the files and reloads the plug-in.

While provider entries are being replaced, two more files exist there: `config-previous.json`,
the host configuration they replace, and `config-written.sha256`, the digest of the file that
provision wrote. Both are removed once the host has accepted the new file. A provision run that
finds them first undoes that unfinished write, and undoes only what the earlier run changed.
While `config.json` is still the file that run wrote, the kept file goes back whole
(`"restored": "file"` in the change record). If the host has written the file in the meantime,
only `agents.providers` is put back and every other key stays (`"restored": "providers"`). A host
that keeps running is then told to reload the file, because it may have loaded the file that was
undone. The same rule applies when the host refuses the entries. Only a provision run repairs:
`status` and `stop` never change the file, and while a file that the host refuses is in place,
`status` fails with the host's "Invalid config" text. A provision run also deletes temporary
files that a killed write left beside those two. A `frameBaseUrl` whose host name is not
`localhost` or an IP address also needs that name in the host's `daemon.hostnames`; provision
does not write that key.

`status` reports `running` and, for a running host, `version`, `serverId`, `listen`, the
plug-in's `state` and `error`, the `embed` list in effect and each provider with its
availability. When the host does not run, the other fields are `null`. `stop` stops the host
process that the process record of the configured home names, and reports `"action": "stopped"` or
`"not running"`. It never looks a process up by port. Stopping the host ends running turns and
open permission prompts; the agents can be resumed afterwards.

The process record of the home (`<home>/paseo.pid`) holds a process id, and the host's own
`daemon stop` signals whatever live process that id names. After a crash the operating system
can give the id to another process. Before any command of the host that would act on the record,
all three commands therefore require three things: the recorded process is alive, its command
line is that of the host's supervisor, and its own environment names this home (`PASEO_HOME`, read from
`/proc/<pid>/environ`). A record that fails this is stale, and nothing is signalled. `stop`
then reports `"not running"`, names the record under `staleRecord` (`pid` and `reason`) and
deletes the record file. `provision` deletes it, reports
`{"step": "daemon", "action": "removed-stale-record"}` and continues as for a host that does
not run. `status` reports the host as not running, names the record under `staleRecord` and
leaves the file. `staleRecord` is `null` otherwise. If the recorded process is alive but cannot
be inspected, because it belongs to another user or the system has no `/proc`, the command
refuses with `process_record_unverifiable` and signals nothing.

## User settings file

The role settings are everything under the top-level key `orchestration`. Two files hold them,
and every read merges the two:

- The user settings file, `~/.config/agents-remember/settings.json`. An absent file loads as an
  empty one: every key takes its default.
- The repository settings file, `<code-repository>/system/settings.json` (optional). It holds
  the same `orchestration` shape, and its values go over the values of the user settings file.

**Merge.** The merge goes key by key into nested objects. A value in the repository settings
file replaces the value of the same key in the user settings file, and the neighbouring keys
stay. A list replaces a list whole.

**Unknown keys.** An unknown key anywhere under `orchestration` refuses the load, and the
message names the file.

**`null`.** A `null` at one of these keys refuses the load in either file: `loops`, `roles`,
`rolesPerLevel`, `concurrency`, `spawn`, `harnesses`, `roleMessageLimit`, `landing`
and `qualityGate`. `null` never means "use
the default": remove the key to take the value of the user settings file or the default.

**When the files are read.** Every reader reads the files at its own call, so an edit holds for
the next call without a restart.

| Key | In a repository settings file | Read by |
| --- | --- | --- |
| `orchestration.roles`, `orchestration.rolesPerLevel` | accepted, except `mode`, `notes`, `stepUp`, `extraArgs` and the permission features of `featureValues` | every start of a role agent |
| `orchestration.spawn.harness` | accepted | every start of a role agent |
| `orchestration.harnesses.<id>` | accepted, except `extraArgs` | every start of a role agent |
| `orchestration.loops.defaults.maxRounds` | accepted and not applied | `task_doc`, when a review round begins |
| `orchestration.roleMessageLimit` | accepted | every `role_message` call |
| `orchestration.landing.commitCheckTimeoutSeconds` | accepted | every landing commit check |

### Closing the user's file to writing

`user_settings_init` creates the user settings file with mode 600, open to writing. You can
close the file (mode 444) and its folder (mode 555, closed to new and removed entries):

```sh
chmod 444 "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember/settings.json" && chmod 555 "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember"
```

The operating system then refuses a test, a helper, a script or code of a task that tries to
write, replace or remove the file, unless that process has opened the permissions itself. The
product does not open the permissions of an existing file or folder.

Before an edit, open the folder and the file for yourself. The folder is opened with the file
because an editor that saves by replacing the file makes a new entry in the folder:

```sh
chmod u+w "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember" "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember/settings.json"
```

Close both again afterwards with the first command.

`${XDG_CONFIG_HOME:-$HOME/.config}` is the configuration folder the product reads, so both
commands reach the same file under a custom `XDG_CONFIG_HOME`. The quotes keep a path that holds
a space whole.

`server_info` names a file or folder that it finds open to writing under `openUserSettings`,
with both commands written out for the exact path. It names nothing while both are closed.
Naming the state changes nothing: the modes are read, and the modes and the file stay as they
are.

### user_settings_init

The tool `user_settings_init` creates the user settings file when the file is absent. Its
argument `target` is required and takes `role-settings`; the value `host-authority` is refused,
because the preparation of the host writes the `paseoRuntime` block. The tool writes one thing:
`orchestration.roles.<role>.harness` for the roles its call names. The argument `harness` names
one harness for every role, and the argument `roles` names a harness for each role, as an object
from role to harness; `harness` fills the roles that `roles` does not name. Model, effort and
every other key stay unset and take their defaults. With the argument `dry_run` set to `true`, the
tool shows the file it would write and writes nothing:

```text
user_settings_init(target="role-settings", harness="claude", dry_run=true)
```

The tool never writes, completes or merges an existing file. It reads the file and reports the
choices the file holds. To change a key of an existing file, edit the file yourself as the
section above describes.

### orchestration.roles, orchestration.rolesPerLevel

`orchestration.roles.<role>` holds the launch choices of one role. The roles are `architect`,
`orchestrator`, `manager`, `worker`, `curator`, `reviewer`, `investigator` and `bootstrap`.
Every start of a role agent reads the row of its role, through `role_start` and through the
dashboard's launcher.

| Role key | Value | Effect on a start |
| --- | --- | --- |
| `harness` | a harness id: the id of a [host provider](../concepts.md#host-provider) | The harness of the agent. The id needs no entry under `orchestration.harnesses`. |
| `model` | a model id or label the host offers for that harness | The model of the agent. |
| `effort` | a thinking option the host offers for that model | The thinking option of the agent. |
| `mode` | a mode id or label the host offers for that harness | The permission mode of the agent. User settings file only. |
| `featureValues` | an object: `true` or `false` for a toggle, an option id for a selection | The features of the agent, such as `fast_mode` or `plan_mode`. |
| `serviceTier` | a speed option the host offers, by id or label | The speed of the agent; the same choice as `featureValues.service_tier`. |
| `notes` | text | Your instruction text for the role. User settings file only. |
| `extraArgs` | an object from a flag name to a string, `null` or `false` | Extra command-line arguments for the harness, such as `--chrome`; see the section on `extraArgs` below. User settings file only. |
| `stepUp` | an object with the launch keys above except `extraArgs`, and `afterTries` | One alternative set of launch choices. User settings file only. |

A start matches each name with the ids and the labels of the host's catalog without regard to
case. The host's catalog is what the running host reports: its host providers with their models,
thinking options, modes and features. A choice that the host does not offer refuses the start
before a workspace of the host or an agent is created: `agent_not_offered`, `model_not_offered`,
`effort_not_offered`, `mode_not_offered`, `service_tier_not_offered` or `feature_not_offered`.
[Harnesses](harnesses.md) describes what each harness needs.

This example is a complete user settings file. Replace each model placeholder with an id that
your host offers:

```json
{
  "orchestration": {
    "roles": {
      "reviewer": {
        "harness": "claude",
        "model": "<model id the host offers>",
        "effort": "high",
        "mode": "auto",
        "featureValues": { "fast_mode": true },
        "notes": "Write every finding in German."
      },
      "worker": {
        "harness": "codex",
        "model": "<model id the host offers>",
        "effort": "xhigh",
        "mode": "auto-review",
        "serviceTier": "fast",
        "featureValues": { "plan_mode": false },
        "notes": "<overwrite>\nImplement the selected task and report its evidence."
      }
    },
    "rolesPerLevel": {
      "leaf": { "reviewer": { "effort": "xhigh" } }
    },
    "spawn": { "harness": "claude" }
  }
}
```

**Levels.** `orchestration.rolesPerLevel.<level>.<role>` holds the same keys for one role at one
level. The levels are `leaf`, `master` and `portfolio`. The level `leaf` is the level of a task;
tools call a task a `leaf`. Each role has one level: `leaf` for `worker`, `reviewer` and
`curator`, `master` for `manager`, and `portfolio` for `architect`, `investigator` and
`orchestrator`. A start reads the row at the level of its role and lays it
over the row under `orchestration.roles`, key by key. A row for a role under another level is
accepted and read by nothing. `bootstrap` has no level, so no per-level row applies to it.

**Order of the settings.** Within the merged settings the order is: the per-level row of the
repository settings file, the per-level row of the user settings file, the role row of the
repository settings file, the role row of the user settings file. A key that a per-level row
omits takes the value of the role row.

**Order of the sources.** For the harness, the model, the effort and the speed features a start
takes the first source that states a choice: the start, the
[host profile](../concepts.md#host-profile), the settings, the host's default. For the harness
the settings are the role's `harness`, then `orchestration.spawn.harness`, then the first harness
id that the host offers: the built-in ids first, then the ids added under
`orchestration.harnesses`. For the mode the order is the host profile, then the settings. When
neither states a mode, a start on Claude states `default` and a start on Codex states `auto`,
each only when the host offers it; a start on another harness states no mode. `role_start`
accepts no mode. A choice bound to one harness and model is not carried to another: when a higher
source chooses another harness or model, the start omits the lower choice and records the
reason.

**What only the user settings file may state.** `mode`, `notes`, `stepUp` and `extraArgs` in a
repository settings file refuse the load, in `roles` and in `rolesPerLevel`; the message names
the file and the key. The features are split the same way. `service_tier` and Claude's
`fast_mode` are speed features: either file and a start (`feature_values` of `role_start`)
may state them. Codex's `plan_mode`, the feature `auto_accept` of a harness that the host drives
through the Agent Client Protocol (for Hermes, see [Install for Hermes](../install/hermes.md)) and
every feature the product does not know bear on permission: only the user settings file and
a host profile may state them. The user of a running agent changes its mode in the host's chat;
no repeated start, settings edit or landing changes it.

**Speed.** `serviceTier` and `featureValues.service_tier` name the same choice. The load accepts
both in one row. A start compares them after the merge: equal values are one choice, and
different values refuse the start with `conflicting_service_tier`. Claude's `fast_mode` is a
separate toggle.

**Notes.** The text of `notes` follows the role's own instructions as a marked block that names
its settings key and its source. When the first line that is not blank holds only `<overwrite>`,
that line is removed and the rest replaces the role's own instructions. An `<overwrite>` without
text after it refuses the start with `empty_instruction_overwrite`. The text is plain text: the
product loads no file and no link from it.

**Step-up.** `orchestration.roles.<role>.stepUp` names one alternative `harness`, `model`,
`effort`, `serviceTier`, `mode`, `featureValues` and `notes`. For `worker` and `curator` the key
`afterTries` (a positive whole number, default 3) sets after how many failed review rounds the
alternative is offered. For the Worker a try is a recorded code review round that ends with
findings remaining; for the Curator it is a recorded memory review round. A check that only
records the same candidate again is no further try. Another role accepts a `stepUp` row and
refuses `afterTries`. An unknown key, a `stepUp` inside a `stepUp`, `extraArgs`, and choices
equal to the role's own refuse the load with the file and the key.

When the tries reach `afterTries`, the answer of the recorded review names the step-up as the
next action. The next start validates the alternative against the host's catalog and replaces an
idle agent. It refuses the replacement while the agent is in a turn or has an open permission
prompt. `role_start` with `step_up` set to one sentence of reason, and the launcher's button
**Step up**, choose the same alternative without a count. A choice that the alternative omits is
not taken from the role's own row when the harness or the model differs. Removing the `stepUp`
row changes no running agent; the next start uses the role's own row.

**The explainer.** The same family accepts the name `review-presenter`, which is no role:
the launch choices of the explainer on the review page. Its row obeys every rule above. A start
applies its `harness`, `model`, `effort`, `mode`, `serviceTier`, `featureValues` and `notes`. The
start of the explainer does not read `stepUp`, and `extraArgs` in the row refuses the load.

**Host profiles.** The host keeps agent profiles of its own. Each first start reads them from the
running host, and the read changes nothing. A profile whose `name` equals the role id exactly is
the host profile of that role. When several profiles match, the first in the host's order counts
and the record of the start names the others. The start takes the harness, the model, the
effort, the mode, the feature values and the notes that the host profile states; notes of the
profile that are not blank take the place of the `notes` of the settings. The profile's `id`,
`icon` and `color` are no launch choices. When the profiles cannot be read, the role settings
decide and the record of the start names the reason. Profile resolution reads the host's profiles from the running host and
writes no file of the host's: it never reads or edits `config.json`.

One read of that file is not profile resolution, and it is read-only. A start repeats the tool
rules the host's `config.json` denies in the provider's entry, so a start that hands rules of its
own does not put the user's out of force; a start, a confined step and the explainer's
resume also read the `extraArgs` that file gives the provider, because the host lays its configured
provider options over every start and every resume. Both reads are of the file the host
owns: nothing in a launch creates, changes or deletes it, and the settings are the place a user
states either fact (see `orchestration.harnesses.<harness>.extraArgs` below).

### orchestration.spawn

`orchestration.spawn.harness` names the harness of a role start when neither the start, the
host profile nor the role's row names one. Every start of a role agent reads it. The value must
be a [built-in harness id](harnesses.md#the-harness-ids-of-the-role-settings) (`claude`,
`codex`, `pi` or `eve`) or an id added under `orchestration.harnesses`; another value refuses
the load. A built-in id starts an agent only when the host offers a host provider of that id.

### orchestration.harnesses

`orchestration.harnesses.<id>` is an object keyed by harness id. An id that is not built in adds
that id to the ids `orchestration.spawn.harness` may name, and to the ids a start tries when no
source names a harness. Every start of a role agent reads the ids.

An entry may hold `extraArgs`; the next section describes it. An unknown key refuses the load.
The host's catalog, not this family, names the harnesses, models and efforts a role can use.

### orchestration.harnesses.<harness>.extraArgs, orchestration.roles.<role>.extraArgs

`extraArgs` holds extra command-line arguments for the harness of a role agent, for a capability
that Agents Remember has no setting for. An example is `--chrome`, which gives a role agent on
Claude Code the Claude in Chrome browser tools (`{"chrome": null}`). Every start of a role agent
reads the three places.

**The three places.** `orchestration.harnesses.<harness>.extraArgs` holds the arguments for every
role started on that harness. `orchestration.roles.<role>.extraArgs` holds them for one role, and
`orchestration.rolesPerLevel.<level>.<role>.extraArgs` for one role at its level. The three are
laid key by key: a per-level row over the role row, and the role row over the harness
entry. The value `false` at an upper place removes a name it inherited. It removes settings
entries only and is never sent. It is refused at the harness entry, which has no lower place to
remove from. `extraArgs` itself must be an object; `extraArgs: null` is refused.

**The shape.** An object from a flag name to one of three values:

- a string: sent as `--name value`;
- `null`: sent as the bare flag, `--name`;
- `false`: removes an entry of that name inherited from a lower place; never sent.

A name is written without its leading dashes. It is not empty, and it holds no dash at its
start, no `=` and no whitespace. Any other value type is refused. The value itself may be empty
or blank: a string is carried exactly as the settings file writes it. Every refusal names the
settings file and the key.

```json
{
  "orchestration": {
    "harnesses": { "claude": { "extraArgs": { "chrome": null } } },
    "roles": {
      "reviewer": { "harness": "claude", "extraArgs": { "chrome": false } },
      "investigator": { "harness": "claude", "extraArgs": { "name": "investigator" } }
    }
  }
}
```

With this user settings file every role agent on Claude Code is handed `--chrome`; a Reviewer is
not; an Investigator is handed `--chrome --name investigator`. The arguments reach the harness
through the host's provider options. The host keeps them with the agent for its whole life,
across a resume and a reload. A settings change applies to agents started after it.

**Which harness a start uses.** A harness entry applies to the harness the start actually uses,
whatever chose it: the start, the host profile, the settings, a step-up, or the first
harness the host offers. The `extraArgs` of a role row belong to the harness of the role's
settings: the `harness` of its row, else `orchestration.spawn.harness`. If the start uses another
harness, the `extraArgs` of the role rows are not taken and the record of the start says so.
With no harness in the settings, none is taken and the record says so.

**Which harnesses take them.** Only the host's Claude provider takes extra
arguments. A harness entry for another harness, and a role or per-level row whose own `harness`
is another harness, refuse the load; the message names the harness, the key and the file.

**Reserved names.** A name that Agents Remember, the host or the harness already sets, or that
would change the authority of the harness or its session as a whole, refuses the load, and the
message names the key. The list guards your own file against mistakes. It is no security
boundary, because the file is your own authority. The reserved names are:

<!-- MIK-R192 reserved names: kept in step with RESERVED_EXTRA_ARG_NAMES by a test --> <!-- docs-check: identifier: a test finds this list by the comment -->
```text
add-dir
agent
agents
allow-dangerously-skip-permissions
allowed-tools
allowedTools
append-system-prompt
append-system-prompt-file
background
bare
betas
bg
brief
channels
cloud
continue
dangerously-skip-permissions
debug
debug-file
debug-to-stderr
desktop
disable-slash-commands
disallowed-tools
disallowedTools
effort
environment
exclude-dynamic-system-prompt-sections
fallback-model
file
fork-session
forward-subagent-text
from-pr
hard-fail
ide
include-hook-events
include-partial-messages
input-format
json-schema
managed-settings
max-budget-usd
max-thinking-tokens
max-turns
mcp-config
model
no-session-persistence
output-format
permission-mode
permission-prompts
permission-prompt-tool
plugin-dir
plugin-dir-no-mcp
plugin-url
print
remote-control
replay-user-messages
restricted
resume
resume-drops-turn
resume-session-at
safe-mode
session-id
session-mirror
setting-sources
settings
strict-mcp-config
system-prompt
system-prompt-file
system-prompt-snapshot
task-budget
teleport
thinking
thinking-display
tmux
tools
verbose
workload
worktree
```
<!-- /MIK-R192 reserved names --> <!-- docs-check: identifier: a test finds this list by the comment -->

**Who may state them.** Extra arguments bear on permission, so only the user settings file
carries them, at any of the three places. A repository settings file that states `extraArgs`
refuses the load; the message names the file and the key. The start is no source: `role_start`
and the launcher take no extra arguments, and a host profile carries none. A `stepUp` row that
states `extraArgs` is refused by name.

**A confined agent receives none.** The explainer runs with exactly the arguments of its
confinement, at the start and at every resume.
`orchestration.roles.review-presenter.extraArgs`, and the same key at a level, refuses the load,
and a harness entry for Claude is never applied to the explainer.

**`paseoRuntime.providers` carries no extra arguments for Claude.** The host merges its
configured provider options into every start and every resume, and it resumes a stored agent by
itself when its chat is shown, without Agents Remember. Agents Remember cannot check those
resumes, so the configured map must hold no `extraArgs`:
`paseoRuntime.providers.<id>.options.extraArgs` and `...params.extraArgs` are refused when the
block is read, for the Claude provider and for every entry that `extends` it. The message names
`orchestration.harnesses.claude.extraArgs` as the place for extra arguments. Agents Remember
writes `agents.providers` whole from the settings at every provision (`runtime_install` and
`agents-remember paseo provision`), so a provisioned host holds no such entry. Before each
confined step Agents Remember reads the host's map in two places, the host's `config.json` and
the map that the running host reports. It refuses the step if either holds `extraArgs` for
Claude, and names the host's configuration file, the arguments and the provision that restores
the file. One consequence: the settings cannot give `--chrome` to the Claude chats that you
start in the host yourself, outside Agents Remember.

**What the record shows.** Each start records every extra argument it received with its source
(`"harness entry"`, `"role entry"` or `"per-level entry"`, and the settings file) and lists under
`notTaken` the arguments of role rows that it did not take. A start on Claude also notes
`extraArgs` that it finds in the host's provider map, with the source "host configuration", and
goes on. A start without extra arguments records none.

### orchestration.loops

`orchestration.loops.defaults.maxRounds` (a positive whole number, default 5) is the number of
review rounds a task may run without a further approval. `task_doc` reads it when a review round
begins, from the user settings file only: a value in a repository settings file is accepted and
not applied. A changed number holds for the next round without a restart. When the rounds of a
task have reached the number plus the extra rounds approved for that task, the next round is
refused with `review-budget-exhausted` until an approval of a further round is recorded.

The loader accepts and checks four more keys of `orchestration.loops`, and nothing reads them:

- `orchestration.loops.defaults.reviewerReuse`: text, default `delta-verify`.
- `orchestration.loops.defaults.complexity.fullLoopAt` and `...complexity.builderAt`: `low`,
  `medium` or `high` (defaults `high` and `medium`); another value refuses the load.
- `orchestration.loops.perLevel.<level>.loop`: a text that is not empty, for the levels `leaf`,
  `master` and `portfolio`.
- `orchestration.loops.perMaster.<master>.<level>.loop`: the same, keyed by the task name of a
  master.

An unknown key under `orchestration.loops` refuses the load.

### orchestration.concurrency

`orchestration.concurrency` holds the keys `maxParallelMasters`, `maxParallelLeaves` and
`maxSubAgents`. Each takes a positive whole number. The keys are accepted and enforce nothing:
no tool passes their values to a role agent, and no start is refused because of them.

### orchestration.roleMessageLimit

`orchestration.roleMessageLimit` (a positive whole number, default 1000) bounds the characters
of the text of one `role_message`. Every `role_message` call reads it, so a changed number holds
for the next call without a restart of the tool server. Only the text counts: neither the sender
line nor the file line. A longer text is refused as `message-too-long`. A reply to a `wait` that
is longer than the limit is filed whole beside the message, and the answer says
`textTruncated: true`. The text of the answer is cut to its beginning only where the answer would
pass the [bound of a tool answer](mcp-tools.md#tool-answers). The full text of a message goes
into `body` (any length) or `attach`, and every message is filed whole in the `messages` folder
of its task. Zero, a negative number and a value that is no whole number refuse the load; the
message names the key.

This example is a complete user settings file; the same text is valid as a repository
settings file:

```json
{
  "orchestration": {
    "roleMessageLimit": 1000
  }
}
```

### orchestration.landing

`orchestration.landing.commitCheckTimeoutSeconds` (a positive whole number, default 900) bounds
the seconds one run of the landing commit check may take. Every landing reads it, so a changed
number holds for the next landing without a restart. The check reads the user settings file, the
`system/settings.json` of the checkout it runs in and, in a linked worktree, that of the main
checkout. A run past the bound is stopped with its child processes and refuses the landing.

```json
{
  "orchestration": {
    "landing": {
      "commitCheckTimeoutSeconds": 900
    }
  }
}
```

### Other top-level keys of the user settings file

The loader of the role settings reads the key `orchestration` and no other. Another top-level
key of the user settings file loads without a message.

## Memory Repo Example

This example is a complete memory settings file, `<memory-root>/system/settings.json`:

```json
{
  "version": 2,
  "onboarding": {
    "storage": {
      "mode": "memory-repo"
    },
    "pathRules": {
      "include": {
        "paths": ["README.md", "docs/**", "src/**"],
        "fileTypes": [".md", ".py", ".ts", ".tsx"]
      },
      "exclude": {
        "paths": [
          "node_modules/**",
          "vendor/**",
          "dist/**",
          "build/**",
          "coverage/**",
          ".cache/**",
          ".pytest_cache/**",
          ".venv/**",
          ".idea/**",
          ".env",
          ".env.*",
          "**/generated/**",
          "**/*.generated.*",
          "**/*.Zone.Identifier",
          "**/*:Zone.Identifier"
        ],
        "fileTypes": [".png", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": [
      {
        "repo": "my-other-repo",
        "expectedBranch": "main",
        "includeCode": true,
        "includeMemory": false
      }
    ]
  },
  "landing": {
    "workflow": "local"
  }
}
```

The repository holds a second example, `system/defaults/examples/memory-repo/settings.json`.

## Memory settings file keys

The memory settings file holds no key that refuses a load because it is unknown. Its readers are
the resolution of a repository's context, the knowledge tools and the worktree tools.

`version` is accepted and read by nothing; any value loads.

`onboarding` wraps the storage keys and the path rules. A file may also state `storage` and
`pathRules` at its top level, without `onboarding`.

`onboarding.storage.mode` (text, default `memory-repo`) says where the card of an eligible
source file is stored. With `memory-repo`, an eligible file has a card in the memory repository.
The loader accepts any text, and it reads `onboarding.storage.layout` when `mode` is absent. The
check for missing cards (the Curator's checklist heads it "Missing onboarding") reads the value
for each source file:

- `memory-repo` and `repo-sidecar`: the file needs a card in the memory repository.
- `inline`: the file needs an onboarding block inside its own text.
- `disabled`: the file is not eligible and needs no card.
- `hybrid`: the `storage` of the path rule that includes the file decides, with one of these
  values. For a rule without `storage`, and for a file that no rule includes,
  `onboarding.storage.default` decides; without that key the value stays `hybrid`, which the
  check reports as unsupported storage.
- Any other text: the check reports the file as unsupported storage.

A source file that the path rules exclude has no card. Outside `hybrid`, when the memory settings
file holds at least one rule, a source file that matches no rule has none either.

`onboarding.pathRules` decides which paths and file types are eligible for a card. The value
is one rule or a list of rules. [Path Rules](path-rules.md) describes the keys of a rule.

`crossRepo.allow` lists the other repositories whose content the context of this repository may
include. Each entry is an object:

| Key | Value | Default |
| --- | --- | --- |
| `repo` | the id of the other repository | required |
| `expectedBranch` | the branch that repository must be on | required |
| `includeCode` | `true` or `false` | `true` |
| `includeMemory` | `true` or `false` | `false` |

An entry without `repo` or without `expectedBranch`, an entry whose `includeCode` or
`includeMemory` holds another value than `true` or `false` (`null` takes the default), and an
entry that is not an object, is not used.
The context in the answer of `context_packet` keeps it with `"state": "excluded"` and a `reason`,
and it lists the reason under `crossRepo.errors`. Keep the list empty unless this
repository needs another one.

`landing.workflow` is `local` (the default) or `pull-request`; another value and `null` refuse
the load. The key is read at the top level of the file only; a `landing` inside `onboarding` is
read by nothing. The worktree tools read it when they check the chain of task landings on the
branch of a master: a merge that publishes the landing of a task as a pull request is admitted
only under `pull-request`.
[Workflows](../workflows.md) describes the two workflows.

## Role working folders and assignment reads

This section describes folders and files of a role agent. It names no settings key.

**Reports and scratch.** A role agent of a task writes its reports and its evidence under
`role-reports/<role>/<requestId>` in the enclosure of the task, and its scratch files under
`scratch/<role>/<requestId>`; `<requestId>` is the request id of the start, the argument
`request_id` of `role_start`. Both are real folders inside the working folder of the role agent.
The folder `reports` of the enclosure belongs to the product and is another folder. A role agent
that works without an enclosure keeps the workspace root as its working folder. It gets its own
report and scratch folders under `<workspace-root>/.agents-remember/role-executions/`, a folder
that the installation creates and has Git ignore.

**Scratch outside the workspace root.** Every enclosure also gets one private scratch folder
outside the workspace root, under `/var/tmp/agents-remember/`. The
[handover](../concepts.md#handover), the file that records the assignment of a role agent, names
it to the role agents of the enclosure as `workspace.outsideScratchRoot`. It serves a sandbox and
a tool that refuses a long path. The product removes the folder when the enclosure is cleaned up
or abandoned, and the tool server removes the folders of ended enclosures when it starts.

**Handover locator.** Every start of a role agent publishes a handover locator, a short link to
its report folder, at `~/projects/.agents-remember/handover-locators/<request id>`. The first
message and the recovery note name the handover file through the locator, so a deep task folder
does not make the path too long. The recovery note is the short system prompt of the role agent:
its role, the references of its task documents and the place of its handover file. The locator
always stands under the folder `projects` of your home folder, wherever your workspace root is.
When the agent stops being resumable, the product removes the locator alone and leaves the
report folder.

**Collection.** An outgoing `role_message`, and the cleanup or the abandoning of an enclosure,
copy reports, handovers, evidence and hand-off lists to the notes of the task: the folder
`notes/reports/` beside the task document in its task folder, or
`<workspace-root>/.agents-remember/reports/` for an agent without a task. Reading options, a
result or a status collects no file and removes no folder. Each start has its own source folder and its own
folder in the notes. A file that has not changed is skipped by its file metadata. One
collection handles at most 10,000 files and folders and 2 GiB; archive larger evidence in the
notes of the task first. A collection that fails is recorded and returned beside the delivered
message; it keeps the folders and refuses their removal. Scratch is removed with the enclosure.
The folders of an agent without an enclosure are removed when that agent is archived or
replaced. Agents Remember archives the Worker, the Reviewer and the Curator of a task on the host
when the task is finalized, cleaned up or abandoned.

**Task documents.** A role agent reads a task document as rendered Markdown with the file tools
of its harness. For a Worker, a Reviewer and a Curator the product publishes a copy of the
task's own document under `task-documents/` in the enclosure before the first turn, at the
relative path that the document has in `<coordination-root>/tasks/<id>/`. `INDEX.json` there
holds one row for each copied file: its kind, reference, repository, the SHA-256 digests of source and copy, the path, the time of
publication, and a flag for a file that is named but absent. A write that renders the document
refreshes the copy in every enclosure of that task, file by file. Cleanup removes the copies
with the enclosure. The copies stay outside the code worktree and the memory worktree. An
Orchestrator and a Manager read their own rendered document without a copy. The result of the
start names the paths and says whether they lie outside the working folder. A role agent
without a task has no task document. `read_ar_files` does not read task documents.

A role agent changes a task document through `task_doc`. A procedure covers these writes:
`task_doc` edits, including an admitted task retirement or folder movement, `knowledge_write`,
`citation_fix`, `citation_note_revision` and `curator_coherence` publication. When such a
covered write is refused although its input is correct, the agent follows the
[correct-input write-refusal procedure](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/refused-write.md)
for the same tool and input class before applying a stop. If no admitted route works, record the refusal, tell the owner, then make that write openly by hand and log it; never keep a blanket ban on writing it by hand. The agent
records the inputs, the refusal and the origin of the manual write, and tells the owner of the
record before it amends the task document itself. The owner's next publication refreshes the
rendered copies; a rendered copy is never parsed back. A refusal of a read, a check, a
validation or a sync, a refused start, a product guard and a commit gate keep their stop, and a
failed gate is repaired.

**First tool call.** For a role agent on Claude Code the start hands the host a grant for the
tool `server_info` on the tool server `agents-remember-task`, before the first message: the
agent calls that one tool without a permission prompt. Every other
tool stays under the permission mode of the agent, and the start writes no settings file of the
harness.
Only a role agent on Claude Code gets this grant. A global allow rule of your own can hide
whether the narrower grant applies.
