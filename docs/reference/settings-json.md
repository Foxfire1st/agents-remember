# settings.json Reference

Agents Remember has five settings families with stated authorities:

| Family | Home | Read cadence |
| --- | --- | --- |
| Boot infrastructure (repos, providers, transport, timeoutCaps, dashboard) | MCP authority settings file (outside the coordinator root) | boot |
| Host runtime (`paseoRuntime`) | active MCP authority settings file selected by `--config` | per use; install reads at each call |
| Memory topology (`onboarding.storage`, `pathRules`, `crossRepo`) | memory-root `system/settings.json` (beside `settings.md`) | per resolution |
| **Agentic settings** (`orchestration.*`: gate delegation, loops, roles + rolesPerLevel, concurrency, spawn preference, harness ids, qualityGate resource policy, roleMessageLimit) | **user configuration `agents-remember/settings.json`** (global), `<code-repo>/system/settings.json` (local override) | per use (`gateDelegation`: boot snapshot) |
| Retired provider block (`providers`; parses, keys reported unused) | server-generated from the authority config (`--from-settings`) | per command |

`system/settings.md` remains the human and agent prose guidance file beside a
memory root's `settings.json`.

The user's `~/.config/agents-remember/settings.json` (under `XDG_CONFIG_HOME` when
set) is the GLOBAL agentic settings file — it is NOT an MCP authority file (the server refuses it as `--config`)
and NOT a provider settings source (the old implicit fallback to it is
removed; an explicit `--from-settings` path is still read wherever it points). MCP authority settings
live outside the coordinator root; see `examples/mcp/settings.example.json`.

### The user's file is closed to writing

The installation keeps the user's file read-only (mode 444) and its folder
closed to new and removed entries (mode 555), so nothing a role agent runs —
a test, a helper, a script, the candidate's code — can write, replace or
remove it. The product itself writes that file in one place only, the
explicit `agents-remember upgrade-user-settings` move, and never opens the
permissions by itself.

Before an edit, open the folder and the file for yourself. The folder is
opened with the file because an editor that saves by replacing the file makes
a new entry in the folder:

```sh
chmod u+w "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember" "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember/settings.json"
```

and close both again afterwards:

```sh
chmod 444 "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember/settings.json" && chmod 555 "${XDG_CONFIG_HOME:-$HOME/.config}/agents-remember"
```

`${XDG_CONFIG_HOME:-$HOME/.config}` is the configuration folder the product
reads, so both commands reach the same file under a custom `XDG_CONFIG_HOME`,
and the quotes keep a path that holds a space whole.

`server_info` names a file or folder it finds open to writing under
`openUserSettings`, with both commands written out for the exact path, and
names nothing while both are closed. `agents-remember upgrade-user-settings`
always reports the state in its `protection` block, with the same two
commands. Naming it changes nothing: the modes are read, and they and the
file stay as they are.

## Memory Repo Example

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
          ".vscode/**",
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
    "allow": []
  }
}
```

## MCP Authority Settings

The MCP settings file replaces the removed coordinator context-provider JSON
example. It names the allowed repositories once. The `providers` block is
retired: its keys are only reported once as unused, and no roots, data
directories, logs, Docker runner images/containers, backend containers, Docker
networks, or watch settings are derived from them.

```json
{
  "version": 1,
  "coordinationRoot": "C:/absolute/path/to/ar-coordination",
  "workspaceRoot": "C:/absolute/path/to/workspace",
  "transcriptRoot": "C:/absolute/path/to/ar-coordination/logs/mcp",
  "repositories": {
    "agents-remember": {
      "contractPath": null,
      "certificationProfile": "mcp/certification-profile-v1.json"
    }
  },
  "providers": {
    "codegraphcontext-code": {},
    "grepai-memory": {}
  },
  "timeoutCaps": {
    "toolSeconds": 30,
    "providerSetupSeconds": 1800
  },
  "benchmarksEnabled": false,
  "dashboard": {
    "autoStart": false,
    "port": 8765
  },
  "providerDegradation": {
    "enabled": true,
    "failSafeEnabled": true,
    "memoryDegradedRatio": 0.8,
    "memoryCriticalRatio": 0.92
  },
  "retirement": {
    "autoLandOnIntegration": true,
    "autoLandOnFinalize": true
  }
}
```

`benchmarksEnabled` (optional, default `false`) gated the removed `codex_benchmark_prepare`
and `codex_benchmark_run` tools; it is retained for settings compatibility and gates nothing now. Benchmark cases run through the benchmark runner CLI instead. A
real run clones third-party repositories and executes the Codex CLI against them.
Even when enabled, `codex_sandbox` defaults to Codex's own `default` sandbox; pass
`"danger-full-access"` only for trusted local runs.

### Paseo runtime

`paseoRuntime` lives in the active MCP authority settings file selected by the
server's absolute `--config` path, separately from the user's role-harness settings.
The optional block describes one build-pinned Paseo install, one daemon home and
one listen address. Consumers read that authority per use; `runtime_install` reads
it at each call and never writes it. The one Bootstrap call creates an absent
block once and preserves an existing block unchanged. An absent block means
"no Paseo runtime configured": maintenance reports that state, while cold Bootstrap
preparation establishes it. A present block requires its five operational keys;
an unknown key fails on the next host read. The dashboard's role launcher and the
tools `role_start` and `role_message` reach the host through this same authority.

Setup assumes one Bootstrap at a time; concurrent writers of the host authority settings file are unsupported.

```json
{
  "paseoRuntime": {
    "installPrefix": "/absolute/path/to/paseo-prefix",
    "home": "/absolute/path/to/paseo-home",
    "listen": "127.0.0.1:8766",
    "providers": {
      "hermes": { "extends": "acp", "label": "Hermes", "command": ["hermes", "acp"] }
    },
    "embed": [
      {
        "dashboardOrigin": "http://127.0.0.1:9797",
        "frameBaseUrl": "http://127.0.0.1:8766"
      }
    ]
  }
}
```

| Key | Fact |
| --- | --- |
| `installPrefix` | Absolute directory that holds the Paseo CLI as a private npm install (`<installPrefix>/node_modules/.bin/paseo`). Nothing is installed globally. |
| `home` | Absolute directory of the daemon home (`PASEO_HOME`). Every Paseo call names it with `--home`; no other home is read, changed or stopped. |
| `listen` | The daemon's listen address as `host:port`. Provision binds exactly this address and never chooses another port. |
| `version` | Retired optional selector. The packaged host contract supplies the pin; any other selector loads and is ignored with a notice naming the key, file, requested value and build version. |
| `providers` | Provider entries for harnesses Paseo drives through ACP, keyed by provider id. The object is written unchanged to the daemon's `agents.providers`; Paseo validates it. May be `{}`. |
| `embed` | The embed list: pairs of `dashboardOrigin` and `frameBaseUrl`. `dashboardOrigin` is the origin a browser opens the dashboard on, written exactly as a browser reports it: lower-case scheme; a host of `a-z`, `0-9`, dot and hyphen only (an international name in its `xn--` form); an IPv4 address as four decimal numbers; an IPv6 address in brackets, in hexadecimal groups with the longest run of zero groups collapsed (`[::1]`, not `[0:0:0:0:0:0:0:1]` and not `[::ffff:127.0.0.1]`); an optional port, no default port (`:80` for http, `:443` for https); no path. Any other spelling is refused, because the value is compared with a browser's origin as text. `frameBaseUrl` is the URL a browser on that origin uses to reach the daemon; no credentials, query or fragment. Each origin appears once. May be `[]`. |

A provider entry may carry values that should stay private, for example an `env`
map with a key for the harness. Provision never puts a provider entry on a
command line and never prints one: it writes `agents.providers` into the
daemon's own `config.json` (readable by the daemon's user only), has Paseo
reload that file when the daemon runs or read it back when it is stopped, and
names only the provider ids (`providerIds`) in its report. If Paseo refuses the
entries, what provision wrote is undone and the report says
`provider_entries_refused` with Paseo's text; if that call fails for another
reason, for example a timeout, the write is undone as well and the report says
`paseo_command_failed`. Paseo's refusal text is carried as Paseo wrote it. It
names the offending path and may quote a provider id and the value of an
`extends` field, so neither is a place for a secret; values of `env` and
`command` were not seen quoted by Paseo 0.11.0-beta.2. A Paseo answer of an
unexpected shape is never echoed into a report. The settings file itself is the
place that holds a private value, so protect it accordingly. The other five
facts carry no secret.

A provider entry states no `extraArgs` for Claude, and none for an entry that
`extends` it: `options.extraArgs` and `params.extraArgs` are refused when the
settings load, naming this file, the key and
`orchestration.harnesses.claude.extraArgs`. The host lays its configured provider
options over every start and every resume, including the resumes of a confined
presenter that AR cannot check, so extra arguments for AR's seats go through
`orchestration` instead (MIK-R192; see
`orchestration.harnesses.<harness>.extraArgs` below).

The install brings the whole build-locked host tree with `npm ci` and the
product's own Node26.11.1, checked against the official archive SHA before
unpacking. Supported host platform: Linux x86_64 with `/proc` only. Node lives
under `<data>/agents-remember/node/node-v<version>-linux-x64`; download staging
lives under `<cache>/agents-remember/node` and is removed on success/failure,
with interrupted staging reclaimed on the next pass. A foreign target folder
is refused and never overwritten. Old versioned Node folders are developer
maintenance: remove one only after no host process uses it.

Every CLI/npm/bridge call starts that Node by its absolute path. Only the npm
child receives its bin first on PATH; the host's agent PATH stays the starter's.
No shell file, global bin link or user npm configuration changes. npm keeps its
user cache/logs. A host start strips PASEO selectors, hosted-seat/role bindings
and exact harness session names, retaining logins. A running host carrying
session names is reported without values; the developer chooses `paseo stop`
and a new start to remove them. Node/prefix/home/cache are the managed locations.

Three commands act on the block and print one JSON document each:

```text
agents-remember paseo provision --config <MCP settings file>
agents-remember paseo status    --config <MCP settings file>
agents-remember paseo stop      --config <MCP settings file>
```

`--config` is required for the MCP installation context. The host block is read from
the same user configuration file as the agentic settings; `coordinationRoot` no longer
selects a host settings file.
`runtime_install` already provisions the host during product install; no host
command is an extra install step. A repeat with no changes is read-only. A dry
run reports Node/version/start/restart disposition and performs no mutation.
When a running host needs a Node/version/locked-tree/listen/start-only transition,
install reports restartRequired before changing it. Live settings can still
apply; a late restart-required response is reported while the host keeps running.
Explicit terminal `provision` is the separate transition mechanism.
Exit status is `0` on success, `1` when a step failed (`error.step` names it and
`error.detail` carries Paseo's text) and `2` when the command refused before
doing anything (unusable settings, or `paseo_runtime_not_configured`). A
settings file that cannot be read as UTF-8 JSON is refused with
`settings_invalid`. Every failure ends in that JSON document with an `error`
object of `code`, `step`, `message` and `detail` (a refusal has `code` and
`message`), never in a traceback.

`provision` brings the runtime to the configured state and lists every change it
made in `changes`; a command that failed is reported in `error`, not as a
change. A run with nothing to change reports `"changed": false`, makes only
read-only calls and writes no file. It writes exactly these daemon settings and
leaves every other key of the daemon's `config.json` alone:

| Daemon setting | Value | Applied |
| --- | --- | --- |
| `daemon.listen` | `listen` | at start |
| `daemon.relay.enabled` | `false` | live |
| `features.webUi.enabled` | `true` | at start |
| `features.dictation.enabled` | `false` | at start |
| `features.voiceMode.enabled` | `false` | at start |
| `pluginsEnabled` | `true` | live |
| `agents.providers` | `providers` | live (written into the file, then reloaded) |

Dictation and voice mode are switched off before the first start, so no speech
model is downloaded. `daemon.action` in the report is `started` (the daemon was
not running), `restarted` (it ran another version, or a setting applied only at
start changed; `daemon.reasons` says which), `reloaded` (only live settings, the
embed list or the plugin changed) or `untouched`. A restart closes the sessions
of running agents; they stay resumable. A change of only the host part of
`listen` is such a restart when the home's own daemon is what holds the new
address (the same port, and a wildcard host or two host names for one address):
the daemon is restarted onto it. Any other new address is probed first, and if
another process holds it provision refuses without touching the running daemon.

Agents Remember keeps three things under `<home>/agents-remember/`: `plugin/`,
the installed copy of the AR plugin (id `ar-plugin`) that ships inside the
package; `embed.json`, the embed list the plugin reads; and
`plugin-loaded.json`, a stamp of what the running plugin last loaded. A change
to the plugin's content or to the embed list replaces the files and reloads the
plugin. While provider entries are being replaced, two more files exist there:
`config-previous.json`, the daemon configuration they replace, and
`config-written.sha256`, the digest of the file provision wrote. Both are
removed once Paseo has accepted the new file. A provision run that finds them
undoes that unfinished write before it does anything else, and undoes only what
the earlier run changed: while `config.json` is still the file that run wrote,
the kept file goes back whole (`"restored": "file"` in the change record); if
Paseo has written the file since, only `agents.providers` is put back and every
other key stays as it is (`"restored": "providers"`). A daemon that keeps
running is then told to reload the file, because it may have loaded the file
that was undone. The same rule applies when Paseo refuses the entries. Only a provision run repairs: `status` and
`stop` never change the file, and while a file Paseo refuses is in place
`status` fails with Paseo's "Invalid config" text. A provision run also deletes
temporary files a killed write left beside those two. A frame base URL whose
host is not `localhost` or an IP literal also
needs that host in Paseo's `daemon.hostnames`; provision does not write that
key.

`status` reports `running` and, for a running daemon, `version`, `serverId`,
`listen`, the AR plugin's `state` and `error`, the `embed` list in effect and
each provider with its availability; when the daemon is not running the other
fields are `null`. `stop` stops the daemon recorded in the configured home's own
process record and reports `"action": "stopped"` or `"not running"`; it never
looks a process up by port.

The home's process record (`<home>/paseo.pid`) holds a process id, and Paseo's
own `daemon stop` signals whatever live process that id names. After a crash the
operating system can give the id to another process. Before any Paseo command
that would act on the record, all three commands therefore require that the
recorded process is alive, that its command line is a Paseo supervisor's, and
that its own environment names this home (`PASEO_HOME`, read from
`/proc/<pid>/environ`). A record that fails this is stale and nothing is
signalled: `stop` reports `"not running"`, names the record in `staleRecord`
(`pid` and `reason`) and deletes the record file; `provision` deletes it,
reports `{"step": "daemon", "action": "removed-stale-record"}` and continues as
for a daemon that is not running; `status` reports not running, names it in
`staleRecord` and leaves the file. `staleRecord` is `null` otherwise. If the
recorded process is alive but cannot be inspected (it belongs to another user,
or the system has no `/proc`), the command refuses with
`process_record_unverifiable` and signals nothing.

## Memory Fields

`version` identifies the settings shape, not a release number. External
(`memory-repo`) memory uses `version` 2, which adds the `crossRepo` block. The
`version` 1 repo-sidecar shape belonged to the removed repo-local internal memory
mode and is no longer written.

`onboarding.storage.mode` selects storage for eligible onboarding. Current
public modes are `memory-repo`, `repo-sidecar` (write one artifact beside its
source), and explicit inline mode where supported by repository settings and file
type. `repo-sidecar` here is a per-artifact placement, not a memory topology.

`onboarding.pathRules` controls which paths and file types are eligible. It
does not switch storage by path.

`crossRepo.allow` controls branch-gated adjacent repository context. Keep it
empty unless the memory layer explicitly allows a cross-repo relationship.

## MCP Fields

`coordinationRoot` is the coordinator runtime target. It must be absolute.

`workspaceRoot` is the workspace root used to derive repository paths from repo
ids. It must be absolute.

`transcriptRoot` is optional. If omitted, MCP logs default to
`<coordinationRoot>/logs/mcp`.

`toolLog.keepDays` is optional. It bounds the coordination-root tool-call log
(MIK-R108 rule 3): the first call of a day removes the `ar-tools-<YYYY-MM-DD>`
log files and detail folders older than it. When absent, 14 days are kept. In
an enclosure, no age-based removal happens before cleanup.

`toolLog.maxBytes` is optional. It bounds the transcript-root tool-call log
folder in bytes (MIK-R108 rule 3, D1-D4): before a write that would take the
folder over its budget, the server removes earlier days whole, oldest first;
a write that still does not fit is dropped and counted, and the call still
succeeds. When absent, 268435456 bytes (256 MiB) apply.

`toolLog.enclosureMaxBytes` is optional. It bounds the `logs/` of one
enclosure in bytes (MIK-R108 rule 3, D1-D4), the only removal there before
cleanup. When absent, 67108864 bytes (64 MiB) apply.

Skills are served as MCP resources from the server's packaged canonical tree. There is
no installed harness skill root to configure, and installation creates no harness,
project or coordination-root skill copy. See [Skills](skills.md) for resource discovery.

`repositories` is an allow-list keyed by repo id. The MCP server derives each
code repository path from `workspaceRoot/<repo-id>` and each memory root from
`coordinationRoot/memory-repos/ar-<repo-id>`. (The former
`repositories.<repo-id>.memorySettingsIncludes` key was dead plumbing — parsed,
never consumed — and was removed with 260703-L13; a leftover key in an existing
file is tolerated and ignored.)

`repositories.<repo-id>.contractPath` may point at a coordination-root-local
contract file. It must not point outside the coordinator root.

`repositories.<repo-id>.certificationProfile` selects exactly one
repository-relative certification profile for code closeout and master integration. The path is
resolved inside `workspaceRoot/<repo-id>` and must be canonical, traversal-free, symlink-free, and
name one regular file. It is never discovered by filename, wrapper presence, repository name, or
historical success. A repository may omit this field while it has no code certification to run;
any operation that would certify or commit code then refuses with
`certification-profile-invalid` before a repository rail starts. See
[Repository Certification Profiles](repository-certification-profile.md) for the versioned
contract and authoring procedure.

`providers` is the retired provider block. It still parses so an older settings
file keeps loading, and entries must be empty objects, but its keys never count
as enabled and drive nothing: no runtime root, data root, container, index, or
watch is derived from them. `runtime_install` and `server_info` report the keys
once as unused (`unusedProviderKeys`).

`timeoutCaps` holds non-negative integer caps for MCP operations. `toolSeconds`
caps MCP tool operations. `providerSetupSeconds` caps provider image build and
dependency install (default 1800); provider installation is retired, so it
currently gates nothing. Docker control operations such as
start, stop, and status use a fixed internal cap and are not configurable.
Indexing and database seed or clone are never capped because they scale with
repository size. A value of `0` means unlimited for any cap.

`dashboard` (optional) supervises the mission-control dashboard from the MCP
server. With `dashboard.autoStart` set to `true` (default `false`), every
server boot ensures a detached dashboard daemon on `dashboard.port` (default
`8765`): a healthy same-version daemon is adopted, a missing one is spawned,
and a version or port mismatch restarts it, so an upgrade is picked up by the
next session's boot. Every dashboard start, including adoption, foreground,
reload and autoStart, ensures the installed host without installing/upgrading
or rewriting configuration/plugin files. Sim touches no host. A matching running
host is left alone; mismatches and an unanswering supervisor are reported without
a second start. Serving does not wait for host readiness. Dashboard stop/restart
and TERM/KILL/Ctrl-C leave the detached host and sessions running.

Status prints both processes, names stale records without removing them and
limits host observation to5s. Exit0 means dashboard up plus running/unconfigured
host;1 means dashboard down;3 means dashboard up but configured host down.
`paseo stop` alone stops the host, losing running turns and permission prompts;
sessions can resume later. Fresh setup uses8766; preserve an existing explicit
listen, including9786, and home/prefix. Daemon state and logs live under
`<coordinationRoot>/logs/dashboard/`; `agents-remember dashboard --status` /
`--stop` manage the same daemon from the CLI. Unknown `dashboard` keys are
rejected.

`providerDegradation` (optional) configures the provider-only degradation
detector that runs over the central provider metrics log. Defaults enable the
detector and the critical fail-safe. It is a safety check over leftover stacks
from older installations. The detector evaluates memory pressure, restart-loop
signals, watcher/index lag, probe latency when a metrics row carries it, and
setup-failure streak rows when present. It is a background loop of the serving
(dashboard) process, not an install step: every 30 seconds it samples the Docker
state of every labelled provider container, appends that sample to
`<coordinationRoot>/logs/observer/providers/metrics.jsonl`, republishes
`metrics-current.json`, evaluates the recent window, and compacts the log — for
as long as the dashboard is up, independently of any install or cleanup. It is
not a revival: it starts nothing, installs nothing, indexes nothing and queries
nothing. State transitions write durable degradation state/events
under `<coordinationRoot>/logs/observer/providers/`. On the transition into
`critical`, `failSafeEnabled: true` (the default, beside `enabled`) makes that
same sample tick call the provider runtime's watcher stop — the same route
`runtime_install` and `providers-remove` call — and the event records the attempt
and its result under `criticalFailsafe`; with it `false` the event records
`{"enabled": false}` and no stop is called for. No agent tool performs that stop.

What that call can reach is decided by the settings document it reads, and all
three stop callers read the same one: the authority settings file's own
`contextProviders` block, which still parses legacy provider keys (rule 4 keeps
that block, and reading it for a stop grants none of the authority it governs for
launch). A legacy settings file that enables a provider therefore gets that
provider stopped, whichever of the three callers runs. The generated lifecycle
document is a different document and is unchanged: it still declares no providers
(`{"enabled": false, "providers": {}}`), because a generated install must not
re-create the retired provider block — it is what an install writes, not what a
stop reads. When the authority settings declare no provider, the stop selects
nothing, stops nothing, and still records the attempt and its result under
`criticalFailsafe`.
Threshold keys are
`memoryDegradedRatio`, `memoryCriticalRatio`,
`degradedSamples`, `criticalSamples`, `healthySamples`,
`watcherLagDegradedCommits`, `watcherLagCriticalCommits`,
`watcherLagDegradedMinutes`, `watcherLagCriticalMinutes`, `probeDegradedMs`,
`probeCriticalMs`, `setupFailureDegradedStreak`,
`setupFailureCriticalStreak`, and `recentSampleLimit`. Unknown
`providerDegradation` keys are rejected.

`retirement` (optional) stays accepted but no code reads it in this build; `server_info`
names the keys a file holds under `unusedSettingsKeys`. The worker, reviewer, and curator
agents of a leaf are archived on the host when the leaf is finalized, cleaned up or
abandoned; their reports stay. Manager and orchestrator seats are never included.

`orchestration` in the authority file is LEGACY territory (260703-L13): the
agentic family moved to the global agentic settings file documented below. For
one migration cycle the authority file may still carry
`orchestration.gateDelegation` — it is honored as a fallback when the global
file does not set the key, with a boot warning naming the new home (and it is
ignored, with a warning, when the global file does set it). Any other
`orchestration.*` key in the authority file (`loops`, `roles`, `rolesPerLevel`,
`concurrency`, `spawn`, `harnesses`) fails the boot loudly, pointing at the
global file.

## Agentic Settings (global + repo-local)

The agentic settings family — everything under the top-level `orchestration`
key — lives in TWO JSON files merged on every read (260703-L13):

- **Global:** `~/.config/agents-remember/settings.json` (or
  `$XDG_CONFIG_HOME/agents-remember/settings.json`). The c-13 interview adds the
  developer's agentic preferences after approval. Absent knobs retain built-in
  defaults without a seeded copy. User-owned: runtime_install never writes this file.
  Host provisioning uses `paseoRuntime` in the active MCP authority file selected
  by `--config`.
- **Repo-local override:** `<code-repo>/system/settings.json` (optional). The
  same `orchestration.*` shape; repo-local values supersede global ones.

The whole former `<coordinationRoot>/system/settings.json` file moves once, including
its other settings families. Normal starts, dashboard settings loads and `runtime_install`
refuse an old file with both paths and `agents-remember upgrade-user-settings
--coordination-root <absolute root>`. Run that command with `--dry-run` to preview, then
without it to move. Both files together refuse; the upgrade never overwrites the new file.
A failed transfer retains the old file and may leave a partial new file, so the next load
refuses both paths; there is no automatic choice or recovery. The repository-local and
memory-repository settings files, and MCP's separate `--config` file, stay in place.
The upgrade resolves the user directory of its own process, including an isolated
sandbox home. It changes no running agent mode.

**Merge semantics.** Deep merge at leaf-key granularity: a local scalar or
object leaf overrides the global one, sibling keys survive; arrays REPLACE
(never concatenate).

**Fail-loud rule.** Unknown keys anywhere inside the `orchestration.*` family
are rejected naming the offending file — a typo can never be silently ignored.
Unknown TOP-LEVEL families in the same file are tolerated-not-parsed (see
Reserved Families below).

**Null rule.** A JSON `null` at a known `orchestration.*` family key
(`gateDelegation` · `loops` · `roles` · `rolesPerLevel` · `concurrency` ·
`spawn` · `harnesses` · `qualityGate`), in EITHER layer, is REFUSED naming the offending file.
`null` reads as *absent* to every family parser and the deep merge REPLACES a
non-object, so `"concurrency": null` in the repo-local layer would otherwise
SILENTLY wipe the global caps — the one scalar collision that used to defeat
both the deep-merge and fail-loud invariants. Remove the key to inherit the
global value (or give it a real object); `null` never means reset-to-default.

**Read cadence.** Read PER-USE through the kernel agentic-settings loader
(`kernel/agentic_settings.py`): an edit takes effect on the next use with no
restart. The ONE exception is `orchestration.gateDelegation`, which the MCP
server snapshots at boot (enforcement plumbing is boot-cached): a change needs
a harness/MCP restart.

**Defaults.** An absent file, or an absent key, means: all-human gate
delegation, the loop defaults below, no role overrides, no concurrency caps,
no spawn harness preference, and a host-managed full
quality gate with normal RAM and swap behavior (see `orchestration.qualityGate`).

### orchestration.gateDelegation

GLOBAL-LAYER ONLY: the boot snapshot reads the user file exclusively, and the
loader REFUSES a `gateDelegation` key in a repo-local settings file (a local value
would otherwise validate and silently do nothing — a fail-open shape). Gate posture
is workspace-wide enforcement state, never a per-repo preference.

Configures server-enforced lifecycle gate delegation. If omitted, the policy is
`all-human`: every gate requires the existing human/developer decision path.
The built-in `manager-decides-leaf-gates` policy adds the manager role for leaf
`plan-approval` and `closeout-approval` gates and routes the master-exit
`master-handover-approval` gate to the orchestrator, while leaving human
decisions valid. `kinds` may override individual delegable gate kinds with
`role: "human" | "manager" | "orchestrator"` and
`requireReviewerVerdict: true`; verdict requirements only apply to delegated
decisions. `requireReviewerVerdictAtSeams: true` additionally binds every
delegated seam-kind rule (`master-handover-approval`) to attached
reviewer-verdict evidence. The delegable kinds are `plan-approval`,
`closeout-approval`, and `master-handover-approval`;
`integration-approval`, `push-approval`, and `cleanup-approval` are
human-pinned and cannot be delegated. Boot-snapshot: restart required (see
Read cadence above).

No part of the product raises or decides a gate in this build. The policy is applied to the gates
an earlier build stored: such a gate still refuses the operation it guards, and
`agents-remember removed-stores` names the gate logs and removes them on the user's word.

### orchestration.roles, orchestration.rolesPerLevel

Paseo role launches read these choices from the host's running catalog, not a built-in model table:

| Role key | Host profile field | Effect on a Paseo launch |
| --- | --- | --- |
| `harness` | `provider` | Agent kind |
| `model` | `model` | Model ID offered for that agent |
| `effort` | `thinkingOptionId` | Thinking option offered for that model |
| `mode` | `modeId` | Permission/operating mode offered for that agent; user layer/profile only |
| `featureValues` | `featureValues` | Boolean for each toggle; option ID for each selection |
| `serviceTier` | `featureValues.service_tier` | Speed selection; `fast` normalizes to native `priority` |
| `notes` | `notes` (host page: When to use) | User's plain instruction text; user layer/profile only |
| `extraArgs` | provider options `extraArgs` | Extra command-line arguments for the agent's harness, such as `--chrome` (MIK-R192, below) |
| `stepUp` | none | One user-layer alternative with the same launch fields and optional `afterTries`; never nested. |
| `launchArgs`, `promptKeywords`, `sessionCommands` | none | Accepted; no code reads them in this build. `server_info` names the ones a file holds under `unusedSettingsKeys`. |

The same family admits one non-seat capability id, `review-presenter` (the review canvas's authoring
capability, MIK-R105). Its row is validated by every rule above -- unknown fields and a repository
row's `mode`/`notes`/permission features refuse with the file and key -- but it is never dispatchable
as a lifecycle seat. The capability applies `harness`, `model`, `effort`, `mode`, `serviceTier`,
`featureValues` and `notes` through the native launch. A row naming `stepUp` alone refuses the
capability's launch by name because it owns no seat replacement ladder.

`orchestration.roles.<role>.stepUp` names an alternative `harness`, `model`, `effort`,
`serviceTier`, `mode`, `featureValues`, and `notes`. It is user-layer only. For Worker,
a try is a recorded code-review round ending with findings remaining; for Curator it is
a recorded memory-review round (`reviewKind: "memory"`). A record-only check of the
same candidate is not another try. `afterTries` defaults to three on these roles and must
be a positive whole number; other roles accept a step-up only on request and refuse
`afterTries`. Unknown fields, a nested step-up, and choices equal to the base role refuse
with the file and key. The product supplies no alternative model.

At the threshold, the next start validates the alternative against the host's catalog,
then replaces an idle seat through its normal retirement route. An active turn or an open
permission prompt refuses the replacement without interrupting it. `role_start(step_up="one
sentence explaining why")` and the launcher's **Step up** control select the same user-owned
alternative without a count; neither supplies mode nor permission features. Choices omitted
from a different harness/model never inherit the base role's choices. Their host defaults
remain identified in the answer. The decision log preserves a task's stepped-up seat across
later starts; review rounds, their limit, and sealed findings do not reset. Removing the
alternative does not alter the running agent; the next start uses the base role and says so.

A mode or `notes` in a repository's `system/settings.json` refuses loading, naming the file and
key, in both `roles` and `rolesPerLevel`. Feature authority is classified by the product:
`service_tier` and Claude's `fast_mode` are speed features and may come from either settings layer
or a start call. Codex's `plan_mode`, ACP's `auto_accept`, and every unknown feature are permission
bearing; only the user's configuration settings and host profile may state them. No start route
accepts a mode. `role_start` adds `feature_values` for speed choices beside agent/model/effort;
this feature argument is new. The launcher shows the applied mode and offers speed-feature
controls. It saves no defaults and adds no instruction editor. A running agent's mode is changed
by its user in the host chat, never by a repeat, settings edit or landing.

`serviceTier` and `featureValues.service_tier` are compared after layer and level merging and alias
normalization. Equal native values resolve to one choice; conflicting values refuse loading.
Claude's `fast_mode` is a separate toggle, not a service-tier alias.

Role provider IDs are shape-checked at settings load and validated against the actual host catalog at launch. Custom host providers need no AR harness declaration for a role. Internal provenance retains the original file/key and provider/model binding through global, repository and per-level merges, so a repository provider override cannot reinterpret a lower user mode with an identically spelled ID. Ignored lower choices are recorded with their source and reason.

Each first start reads the host's profiles through a read-only bridge call. A profile matches a
role when its `name` equals the role ID exactly; the first match in host order wins and the record
names skipped matches. Host `id`, `icon` and `color` are not launch choices: ID/name identify the
profile in provenance; AR owns role titles and labels. If profiles cannot be read, role settings
are used and the unavailable-read reason is recorded. Profile resolution reads the host's profiles
through the bridge and writes no file of the host's: it never reads or edits `config.json`.

One read of that file is not profile resolution, and it is read-only. A start repeats the tool
rules the daemon's `config.json` denies in the provider's entry, so a start that hands rules of its
own does not put the user's out of force; a start, a confined step and the review presenter's
resume also read the `extraArgs` that file gives the provider, because the host lays its configured
provider options over every start and every resume (MIK-R192). Both reads are of the file Paseo
owns: nothing in a launch creates, changes or deletes it, and the settings are the place a user
states either fact (see `orchestration.harnesses.<harness>.extraArgs` below).

For agent/model/effort and speed features, resolution is explicit start selection, host profile,
our settings, host default. For mode, it is host profile, our settings, the named least
working offered mode. The existing service tier default stays provider-scoped and is revalidated against the actual model, including a model override. Other lower choices stay bound to their provider/model; a different higher
provider/model omits them with a reason. Features resolve individually. Differing settings/profile
values are not a refusal; an effective unoffered value is, before a workspace/agent is created.
Empty optional strings/maps state no choice. Records carry each value/source and read provenance.
Settings are read per call; profiles are read fresh for each options call and first start. Displayed
defaults are not submitted as explicit start selections. Repeats/retries use the original saved
call; revive resumes the host's stored agent, including any manually changed mode.

With no user/profile mode, new Claude starts explicitly state `default`, new Codex starts explicitly
state `auto`, only when offered. Pi has no selectable mode; for unknown kinds or a missing named
mode, no mode is sent and the record says why. The catalog's `defaultModeId` is the host form's
preselection, not this product's least permissive mode choice. An incomplete or failed catalog
refuses a start because its modes are unknown; an explicitly empty catalog states no mode.
Modes retain the host wire's ID, label, description,
icon and colorTier; the wire offers no unattended marking, so none is manufactured.

Stating Codex `auto` selects the host's workspace-write/on-request/user-reviewer preset; explicit host provider options can override its policy fields. Earlier
unstated-mode Codex starts inherited the user's configuration. The explicit preset concerns new
starts only. Mode labels alone do not prove permission
behavior. Claude's host describes `default` as Always Ask, prompting the first time a tool is used.
Pi extensions can ask for permission. Actual prompt behavior also depends on host/provider
configuration; no universal no-prompt behavior is established by the mode catalog alone.

The user text follows the whole baseline as a marked block naming its settings key/source. If
`<overwrite>` (case sensitive) is alone on the first nonblank line, ignoring surrounding whitespace,
that tag line is removed and the remainder replaces the baseline. Empty replacement refuses before
creation. The handover, binding, parent, server and product start/message permissions stay intact.
Records distinguish baseline/effective digests, raw text digest and delivered blocks; plain text is
never a file or link loader. There is no role skill setting or skill-existence check. The host's
`daemon.appendSystemPrompt` is a separate global text the host adds to every agent; AR neither reads
nor records it. `role_capsule_compile` alone compiles the baseline, not a live profile.

```json
"reviewer": {
  "harness": "claude", "model": "claude-opus-4-6", "effort": "high",
  "mode": "auto", "featureValues": {"fast_mode": true},
  "notes": "Write every finding in German."
}
```

```json
"worker": {
  "harness": "codex", "model": "gpt-6.1-sol", "effort": "xhigh",
  "mode": "auto-review", "serviceTier": "fast",
  "featureValues": {"plan_mode": false},
  "notes": "<overwrite>\nImplement the selected task and report its evidence."
}
```

These are complete shape examples; use only IDs/features the installation's current catalog offers.

`orchestration.roles.<role>` overrides a role file's knob block per role
(`architect`, `orchestrator`, `designer`, `strategist`, `manager`, `worker`, `curator`,
`investigator`, `reviewer`).
The earlier key `orchestration.roles.system-specialist` applies to the Investigator with one notice per load naming the key, file and new ID. Equal entries under both IDs load; conflicting entries are refused with both keys named. Stored settings are not rewritten.
Precedence: role-file defaults < global settings < repo-local settings.

`orchestration.rolesPerLevel.<level>.<role>` (ruling 2026-07-07T08:15) adds
the per-LEVEL agent sets the L12 doctrine promises: `leaf` | `master` |
`portfolio` (the `loops.perLevel` vocabulary), each holding the same
knob-override shape. A level override deep-merges over the flat
`orchestration.roles` default at field granularity (harness inherited unless overridden; arrays
replace). The level follows the role: `leaf` for worker, reviewer and curator, `master` for
manager, `portfolio` for architect, investigator and orchestrator. Resolution chain in the settings:
repo-local level override > global level override > repo-local role default >
global role default.

### orchestration.harnesses

Entries are keyed by harness id: a NEW id adds that id to the ids `orchestration.spawn.harness`
may name. The fields an earlier build read from an entry (`name`, `command`, `argv`, `modelFlag`,
`effortFlag`, `effortFlagValues`, `effortSessionValues`, `effortSessionCommand`) stay accepted but
no code reads them in this build; `server_info` names the ones a file holds under
`unusedSettingsKeys`. Any other field is refused. The host's catalog names the agent kinds, models
and efforts a role can use. `extraArgs` is the one field an entry may also state (MIK-R192).

### orchestration.harnesses.<harness>.extraArgs, orchestration.roles.<role>.extraArgs

MIK-R192. Extra command-line arguments for the agent's harness, for a capability that AR has no
setting for. The developer's case is `--chrome`: it gives a Claude seat the Claude in Chrome
browser tools (`{"chrome": null}`).

**The three places.** `orchestration.harnesses.<harness>.extraArgs` holds the arguments for every
role started on that harness. `orchestration.roles.<role>.extraArgs` holds them for one role, and
`orchestration.rolesPerLevel.<level>.<role>.extraArgs` for one role at one level. The three are
laid key by key: a per-level role entry over the role entry, and the role entry over the harness
entry. The boolean `false` at an upper layer removes a name it inherited; it removes settings
entries only, is never sent, and is refused at the harness entry, which has no lower layer to
remove from. `extraArgs` itself must be an object; `extraArgs: null` is refused.

**The shape.** An object from a flag name to one of three values:

- a string: sent as `--name value`;
- `null`: sent as the bare flag, `--name`;
- `false`: removes an entry of that name inherited from a lower layer; never sent.

A name is written WITHOUT its leading dashes. It is non-empty, and holds no dash at its start, no
`=` and no whitespace. Any other value type is refused. The value itself may be empty or blank: a
string is carried exactly as the settings file writes it, so `{"tools": ""}` is the empty value the
qualified presenter hands Claude itself. Every refusal names the settings file and the key.

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

Every Claude seat is handed `--chrome`; a Reviewer is not; an Investigator is handed
`--chrome --name investigator`. The arguments reach the harness through the host's provider
options (`providerOptions.extraArgs` of the create request), and the host keeps them with the agent
for its whole life, across a resume and a reload, as it keeps every provider option of a start. A
settings change applies to agents started after it.

**Layering and which harness a start uses.** Harness entries apply to the harness the start
actually uses, whatever chose it: the start call, the host role, the settings, a step-up, or the
host's first offered agent. Role entries belong to the role's settings harness -- the row's
`harness`, else `orchestration.spawn.harness` -- as its model does. If the start uses another
harness, the role entries are not taken and the start record says so; with no settings harness at
all, none is taken and the record says so.

**Which harnesses take them.** In this build only Claude's host provider takes extra arguments
(the host contract's `EXTRA_ARGS_PROVIDER_IDS`); Codex's provider options are structured, and the
ACP providers (Hermes) and Pi are configured by their command list. So a harness entry for any
other harness, and a role or per-level row whose own `harness` is any other harness, is refused
when the settings load, naming the harness, the key and the file.

**Reserved names.** A name that AR, the host or the harness already sets, or that would change the
harness's authority or its session as a whole, is refused when the settings load, naming the key.
The list guards the user's own file against mistakes; it is no security boundary, because that file
is the user's authority. It is kept here and in the code
(`RESERVED_EXTRA_ARG_NAMES` in `kernel/_agentic_settings_core.py`), and a test compares the two.
The names come from the Agent SDK 0.3.246's spawn and the Claude Code 2.1.303 CLI's `--help`; the
host runs `claude` from `PATH`:

<!-- MIK-R192 reserved names: kept in step with RESERVED_EXTRA_ARG_NAMES by a test -->
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
<!-- /MIK-R192 reserved names -->

**Who may state them.** Extra arguments bear on permission, so only the user's own settings file
carries them, at any of the three places: a repository's `system/settings.json` that states
`extraArgs` refuses when the settings load, naming the file and the key. A start call is no source
(`role_start` and the launcher's route take no extra arguments), and a host role carries none. A
`stepUp` row that states `extraArgs` is refused by name.

**Confined capabilities receive none.** The review presenter -- and the explainer seat built on it
-- runs with exactly the arguments of its confinement, at its start and at every resume.
`orchestration.roles.review-presenter.extraArgs`, and the same key per level, is refused when the
settings load, and a Claude harness entry is never applied to it.

**`paseoRuntime.providers` carries no extra arguments for Claude.** This is the one closure, and it
is structural. The host merges its configured provider options into every start and every resume,
and it resumes a stored seat by itself when its chat is shown, without AR. AR cannot check those
resumes, so the configured map must hold no `extraArgs`: `paseoRuntime.providers.<id>.options.extraArgs`
and `...params.extraArgs` are refused when the settings load, for the Claude provider and for every
entry that `extends` it, naming the file, the key and
`orchestration.harnesses.claude.extraArgs` as the place for extra arguments. AR writes
`agents.providers` whole from the settings at every provision (`runtime_install`,
`agents-remember paseo provision`), so a provisioned host holds no such entry. Before each confined
step AR reads the host's map in two places -- the daemon's `config.json` and the running daemon's
map through the bridge -- and refuses the step if either holds `extraArgs` for Claude, naming the
host's configuration file, the arguments and the provision that restores it. The cost of this
route: the settings cannot give `--chrome` to the Claude chats the developer starts in the host
himself, outside AR.

**What the record shows.** Each start records every extra argument it received with its source --
harness entry, role entry or per-level entry, and the settings file -- and lists the role entries it
did not take. A Claude start also notes `extraArgs` found in the host's provider map, with the
source "host configuration", and goes on. A start without extra arguments records none.

**The old keys stay unused.** `orchestration.roles.<role>.launchArgs` and
`orchestration.harnesses.<harness>.argv` remain accepted and unread (MIK-R113 rule 4); an old file
does not start to apply under this build.

### orchestration.agentNotifier

`orchestration.agentNotifier`, the earlier key `orchestration.supervisor` and
`orchestration.expectations` stay accepted but no code reads them in this build; `server_info`
names the ones a file holds under `unusedSettingsKeys`.

### orchestration.qualityGate

`orchestration.qualityGate` owns only the optional full-gate memory override. Repository
execution authority belongs to the explicit
`repositories.<repo-id>.certificationProfile`, including the selected sandbox adapter and result
decoder. A repository profile may declare Dagger as its certifying adapter; unavailable declared
runtime prerequisites fail instead of falling back to host execution.

| Field | Default | Notes |
| --- | --- | --- |
| `memoryCapBytes` | omitted (container runtime manages resources) | Optional positive hard cap applied by the Dagger container's inner wrapper. A capped kill fails and names this policy key; there is no host systemd/RLIMIT execution path. |

```jsonc
"orchestration": {
  "qualityGate": {
    "memoryCapBytes": 8589934592
  }
}
```

The memory cap remains a full-master resource policy; deterministic pre-push checks and leaf
integration run no acceptance. An explicit cap is an opt-in restriction, not the default resource
policy; the gate treats a capped kill as a failure, never a skip.

### orchestration.roleMessageLimit

`orchestration.roleMessageLimit` is the bound on the characters of one `role_message` text
(a positive whole number; where the settings name none, 1,000). Only the text counts: neither
the sender line nor the file line. A longer text is refused as `message-too-long`, and the
reply to a `wait` is cut at the same limit with the whole reply filed beside the message. The
full text rides `body` (any length) or `attach`, and every message is filed whole in the
task's `messages` folder. The number in force is the one the settings file names at the call:
a changed number holds for the next call without a restart of a tool server. A settings file
that names zero, a negative number or no whole number is refused at load, naming the field.

```jsonc
"orchestration": {
  "roleMessageLimit": 1000
}
```

### orchestration.landing

`orchestration.landing.commitCheckTimeoutSeconds` is the bound on the seconds one
landing commit-check run may take (a positive whole number; where the settings name
none, 900). A run past the bound is stopped with its children and refuses the
landing. The number in force is the one the settings file names at the landing:
a changed number holds for the next landing without a restart.

```jsonc
"orchestration": {
  "landing": {
    "commitCheckTimeoutSeconds": 900
  }
}
```

### orchestration.concurrency, orchestration.spawn

`orchestration.concurrency` caps parallel orchestration fan-out:
`maxParallelMasters`, `maxParallelLeaves`, `maxSubAgents` (positive integers;
omitted means uncapped). These keys stay accepted but no code reads or enforces them in this build. `maxParallelMasters` means concurrent masters, `maxParallelLeaves` means concurrent leaves (not agents), and `maxSubAgents` means sub-agents. Reaching one refuses no start; leaf limits are deliberately unenforced.

`orchestration.spawn.harness` names the agent kind a role start uses when no
role/level knob supplies one. Resolution order:
role knobs (level-merged) > repo-local settings > global settings
(the repo-local layer is selected by the canonical task
document's repository). Values are validated against
the effective harness ids (builtin + `orchestration.harnesses`).

```jsonc
"orchestration": {
  "roles": {
    "architect":    { "harness": "claude", "model": "claude-opus-4-8", "effort": "high" },
    "orchestrator": { "harness": "claude", "model": "claude-opus-4-8", "effort": "high" },
    "strategist":   { "harness": "claude", "model": "claude-fable-5", "effort": "max" },
    "reviewer":     { "harness": "claude", "model": "claude-sonnet-5", "effort": "high" },
    "investigator": { "harness": "claude", "model": "claude-fable-5", "effort": "high" },
    "curator":      { "harness": "codex", "model": "gpt-5.6-luna", "effort": "medium" },
    "worker":       { "harness": "codex", "model": "gpt-5.6-sol", "effort": "medium" }
  },
  "rolesPerLevel": {
    "master":    { "reviewer": { "model": "claude-opus-4-8", "effort": "xhigh" } },
    "portfolio": { "reviewer": { "model": "claude-fable-5", "effort": "max" } }
  },
  "concurrency": { "maxParallelMasters": 2, "maxParallelLeaves": 3, "maxSubAgents": 4 },
  "spawn": { "harness": "claude" }
}
```

### orchestration.loops

`orchestration.loops` configures the three-party review loops (OWNER → BUILDER →
REVIEWER) the `l-01-agent-lifecycles` skill runs at every level that owns work.
Parsed by the agentic-settings loader into typed models; stored in the global
file with repo-local precedence like every agentic key.

```jsonc
"orchestration": {
  "loops": {
    "defaults": {
      "maxRounds": 3,                 // the HARD cap — only FULL end-to-end rounds count
      "reviewerReuse": "delta-verify", // residuals of a passing round are delta-verified by the SAME reviewer
      "complexity": { "fullLoopAt": "high", "builderAt": "medium" }
    },
    "perLevel": {
      "leaf":      { "loop": "scored" },        // tier scored per leaf at dispatch (direct | builder-verified | full loop)
      "master":    { "loop": "seam-required" }, // loop posture only; "none" = workflow-free manager (the master-exit SEAM stays unconditional)
      "portfolio": { "loop": "strategist" }     // owner = orchestrator · builder = strategist · reviewer with the plan-review catalog
    }
    // local override example (tight mode):
    // "perMaster": { "260703_agent-orchestration": { "leaf": { "loop": "builder-verified" } } }
  }
}
```

Semantics, as the loop doctrine defines them
(`skills/l-01-agent-lifecycles/SKILL.md`, The Three-Party Loop):

- `defaults.maxRounds` (default `3`) is the hard cap per loop. **Only full
  end-to-end rounds count against it**; delta-verifies close rounds, they do
  not open them. The real control is the convergence rule — every round must
  shrink the open finding set, and a non-shrinking round escalates immediately
  regardless of the count — so the cap is the backstop, not the driver.
- `defaults.reviewerReuse: "delta-verify"` names the ruled reuse: the SAME
  reviewer instance is resumed via a follow-up message to verify a passing
  round's landed residuals, and fix rounds resume the SAME builder. A fresh
  reviewer is spawned only for a full round or when new scope opens.
- `defaults.complexity` maps the dispatch-time complexity score (blast radius ·
  novelty · size) to tiers: at/above `fullLoopAt` a leaf runs the full loop
  (builder + applicable independent reviewer); at/above `builderAt` it runs
  builder-verified (builder + owner report-vs-artifact check + the applicable independent
  route review; no iterative full-loop rounds);
  below both it is direct (ordinary build + the applicable independent route review;
  no iterative loop machinery). Atomic child leaves defer independent route review to the
  accumulated canonical master at master-to-parent integration; organizational
  leaves retain independent leaf review.
- `perLevel.leaf.loop: "scored"` — the owning seat scores each leaf at
  dispatch. `perLevel.master.loop: "seam-required"` names the default loop
  posture; `"none"` configures a manager without iterative loop rounds (a master whose
  leaves all score direct still runs the applicable candidate-bound route review at each
  owning altitude).
  **This knob governs the LOOP only (review rounds): it cannot disable an applicable
  organizational leaf route review, curator/closeout admission, or the master-exit
  SEAM gate.** Atomic-child review remains deferred to master integration by execution nature;
  the knob does not disable or move that gate. The master-exit seam
  is unconditional doctrine — no knob value touches it. Loop posture names
  are model-interpreted doctrine (validated as non-empty strings, not a closed
  set). Each level runs its loop with its own agent set
  (`orchestration.roles` knobs per role).
- `perLevel.portfolio.loop: "strategist"` names the portfolio loop's parties.
  **The strategist's mandatory pre-run is doctrine, not a knob** — no
  configuration can waive it: an orchestrated run requires the adopted
  orchestration task, unconditionally (`roles/strategist.md`).

### Reserved Families (the global file's future)

The global agentic file is the earmarked durable settings home beyond the
`orchestration.*` family. The fail-loud rule is deliberately scoped to
`orchestration.*` only, so reserved top-level families cost nothing today:

- **`contextProviders` — reserved; returns here in a follow-up** (developer
  direction, 2026-07-06). The authority file's `providers` block is itself
  retired now — it parses, reports its keys once as unused, and derives
  nothing — and the OLD implicit fallback that read `contextProviders` from this
  file was retired with L13. A future `contextProviders` key at the top level of the
  global file is tolerated-not-parsed until that migration lands.
- Other top-level keys (`$comment`, `version`, and any future family) are
  likewise tolerated-not-parsed by the agentic loader; only documented
  `orchestration.*` keys are read, and only they fail loud.

### Role working folders and assignment reads

Every new leaf execution writes reports/evidence under its group's real `role-reports/<role>/<requestId>` directory and
scratch under `scratch/<role>/<requestId>`. Product-owned `reports` stays distinct. Existing recorded
`task-reports` aliases remain only for old bindings until terminal collection/cleanup; no new start
uses them. Existing bindings keep their recorded paths until their enclosures close.
Controller/taskless roles retain their workspace cwd and receive local per-execution report/scratch
folders under `.agents-remember/role-executions`, which installation creates and ignores in Git.

Reports, handovers, evidence and producer files are copied to canonical notes on outgoing
`role_message` and before cleanup/abandon (including force). Options, result and status reads
neither collect files nor remove execution folders. Each new execution has its own source and
canonical folder; an unchanged file is skipped using source/copy file metadata, without rereading
its bytes. A collection failure is recorded and returned alongside a delivered message, while
failed collection preserves folders and refuses removal. Collection is bounded to
10,000 files/directories / 2 GiB per call, with large evidence archived in canonical notes first. Legacy aliases
are unlinked without following them to delete their targets. Scratch is reclaimed with the leaf;
non-leaf execution folders are reclaimed when that execution is archived or replaced.

Task documents are read as rendered Markdown with the harness's own file tools. For leaf
Worker, Reviewer and Curator starts, the product publishes a real copy of its own leaf document under `task-documents/` in the enclosure group before the first turn, preserving its canonical relative path. `INDEX.json` holds one row per copied file with its kind, reference, repository, source and copy SHA-256 digests, path, publication time, and a missing flag for named-but-absent files. Writes rendering that leaf refresh its
copy in every existing enclosure for that leaf atomically per file;
cleanup removes the copies with the enclosure. Copies stay outside code and memory Git worktrees.
Change documents through `task_doc` only; edits to copies are never read back and are replaced by
its next publication. Controllers read their own canonical rendered file without a copy; the
start whole result (log file) names the paths and whether they sit outside the working folder. Taskless roles have no task
file. The handover names the paths. `read_ar_files` keeps its existing behavior; task documents are read as rendered files, not through `task_doc`.
`server_info` advertises read-only annotations; the other AR tools remain available under the mode.

Claude launches send the installed host's per-tool session grant for `server_info` on `agents-remember-task`, before the first message. Other tools stay under the mode; no harness settings file is written. Prompt behavior depends on the provider and its effective configuration. Codex's host preapproval removes other tools from that server via enabled_tools;
it must not be applied wholesale or worked around with another AR server. Pi/Hermes offer no
per-tool read-grant route in the installed host. A user's global allow can mask whether a narrower
session grant applies; the catalog alone does not establish actual first-call permission behavior.
