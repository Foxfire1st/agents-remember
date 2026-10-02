# settings.json Reference

Agents Remember has FOUR settings families, each with exactly one home:

| Family | Home | Read cadence |
| --- | --- | --- |
| Boot infrastructure (repos, providers, transport, timeoutCaps, dashboard, Paseo runtime) | MCP authority settings file (outside the coordinator root) | boot |
| Memory topology (`onboarding.storage`, `pathRules`, `crossRepo`) | memory-root `system/settings.json` (beside `settings.md`) | per resolution |
| **Agentic settings** (`orchestration.*`: gate delegation, loops, roles + rolesPerLevel, concurrency, spawn preference, harness definitions, qualityGate resource policy) | **coordinator `system/settings.json`** (global), `<code-repo>/system/settings.json` (local override) | per use (`gateDelegation`: boot snapshot) |
| Provider lifecycle settings | server-generated from the authority config (`--from-settings`) | per command |

`system/settings.md` remains the human and agent prose guidance file beside a
memory root's `settings.json`.

The coordinator root's `system/settings.json` is the GLOBAL agentic settings
file — it is NOT an MCP authority file (the server refuses it as `--config`)
and NOT a provider settings source (the old implicit fallback to it is
removed; an explicit `--from-settings` path is still read wherever it points). MCP authority settings
live outside the coordinator root; see `examples/mcp/settings.example.json`.

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
example. It names the allowed repositories and provider ids once, then the
server derives provider lifecycle settings such as roots, data directories,
logs, Docker runner images/containers, backend containers, Docker networks, and
watch settings internally.

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

`benchmarksEnabled` (optional, default `false`) gates the `codex_benchmark_prepare`
and `codex_benchmark_run` tools. They are refused unless this is `true`, because a
real run clones third-party repositories and executes the Codex CLI against them.
Even when enabled, `codex_sandbox` defaults to Codex's own `default` sandbox; pass
`"danger-full-access"` only for trusted local runs.

### Paseo runtime

`paseoRuntime` is an optional block that describes the one dedicated Paseo
runtime Agents Remember provisions: a pinned Paseo install, one daemon home and
one listen address. When the block is absent the settings are in the named state
"no Paseo runtime configured" and each consumer refuses in its own words. When
the block is present every key is required and an unknown key fails at start-up.
The dashboard's role launcher and the tools `role_start` and `role_message` use
this block and no other to reach the host.

```json
{
  "paseoRuntime": {
    "installPrefix": "/absolute/path/to/paseo-prefix",
    "home": "/absolute/path/to/paseo-home",
    "listen": "127.0.0.1:6820",
    "version": "0.11.0-beta.2",
    "providers": {
      "hermes": { "extends": "acp", "label": "Hermes", "command": ["hermes", "acp"] }
    },
    "embed": [
      {
        "dashboardOrigin": "http://127.0.0.1:9797",
        "frameBaseUrl": "http://127.0.0.1:6820"
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
| `version` | One exact Paseo version (`MAJOR.MINOR.PATCH` with an optional prerelease). A range, a tag such as `beta`, or a partial version is refused. A prefix that holds any other version is replaced. |
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

The daemon inherits the environment of the process that runs `provision`. Agents
Remember writes no harness configuration and handles no harness credential.
Installing uses `npm` as the user has configured it, including the user's npm
cache and logs under `~/.npm`; that cache is what makes a repeated install fast.
Everything else provision writes lies under `installPrefix` and `home`.

Three commands act on the block and print one JSON document each:

```text
agents-remember paseo provision --config <MCP settings file>
agents-remember paseo status    --config <MCP settings file>
agents-remember paseo stop      --config <MCP settings file>
```

`--config` is required and only the `paseoRuntime` block of that file is read.
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

`harnessSkillRoot` is optional. It is only needed for the `skills_install`
maintenance/manual tool. By default, when the MCP settings file lives under
`<registration-root>/mcp/`, `skills_install` copies packaged skills into
`<registration-root>/skills/`. Set `harnessSkillRoot` only for non-standard
harness layouts where the registration folder and skill folder are not siblings.
When neither inference nor the override is available, the MCP server can still
run, but `skills_install` refuses to install because the target root is not
configured. The package-based first-run path gets skills from the copied
harness starter package and does not need this field.

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

`providers` is an allow-list keyed by supported provider id. Provider entries
must be empty objects because runtime roots, data roots, logs, requirements,
patches, backend container names, and watch settings are derived by the server.

`timeoutCaps` holds non-negative integer caps for MCP operations. `toolSeconds`
caps MCP tool operations. `providerSetupSeconds` caps provider image build and
dependency install (default 1800). Docker control operations such as
start, stop, and status use a fixed internal cap and are not configurable.
Indexing and database seed or clone are never capped because they scale with
repository size. A value of `0` means unlimited for any cap.

`dashboard` (optional) supervises the mission-control dashboard from the MCP
server. With `dashboard.autoStart` set to `true` (default `false`), every
server boot ensures a detached dashboard daemon on `dashboard.port` (default
`8765`): a healthy same-version daemon is adopted, a missing one is spawned,
and a version or port mismatch restarts it, so an upgrade is picked up by the
next session's boot. Daemon state and logs live under
`<coordinationRoot>/logs/dashboard/`; `agents-remember dashboard --status` /
`--stop` manage the same daemon from the CLI. Unknown `dashboard` keys are
rejected.

`providerDegradation` (optional) configures the provider-only degradation
detector that runs over the central provider metrics log. Defaults enable the
detector and the critical fail-safe. The detector evaluates memory pressure,
restart-loop signals, watcher/index lag, probe latency when a metrics row
carries it, and setup-failure streak rows when present. State transitions write
durable degradation state/events under `<coordinationRoot>/logs/observer/providers/`
and post `degradation-alert` inbox rows to the orchestrator and active managers.
At `critical`, `failSafeEnabled: true` runs the always-legal `provider_watchers
stop` path. Threshold keys are `memoryDegradedRatio`, `memoryCriticalRatio`,
`degradedSamples`, `criticalSamples`, `healthySamples`,
`watcherLagDegradedCommits`, `watcherLagCriticalCommits`,
`watcherLagDegradedMinutes`, `watcherLagCriticalMinutes`, `probeDegradedMs`,
`probeCriticalMs`, `setupFailureDegradedStreak`,
`setupFailureCriticalStreak`, and `recentSampleLimit`. Unknown
`providerDegradation` keys are rejected.

`retirement` (optional) configures completion cleanup for worktree-backed tmux
seats. `autoLandOnIntegration` and `autoLandOnFinalize` (both default `true`)
gate whether cleanup runs after integrating a leaf or finalizing a master.
`autoCloseCompletedSeats` defaults `true`: worker, reviewer, and curator seats
with a durable turn report for that exact leaf are retired through the normal
graceful-stop/tmux-kill path; their transcripts remain inspectable. A seat with
no durable report remains live and is returned as deferred. Setting
`autoCloseCompletedSeats` to `false` restores the previous landed/archive
behavior, which removes those roles from the active rail without closing tmux.
Manager and orchestrator seats are never included. The legacy `autoRetireOnIntegration` and
`autoRetireOnFinalize` keys are accepted as aliases for existing settings files.
Unknown `retirement` keys are rejected.

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

- **Global:** `<coordinationRoot>/system/settings.json`. Seeded by
  `runtime_install()` copy-if-missing with every knob at its documented
  default; the c-13 install skill interviews the developer and writes it.
  User-owned: an install never overwrites an existing file.
- **Repo-local override:** `<code-repo>/system/settings.json` (optional). The
  same `orchestration.*` shape; repo-local values supersede global ones.

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
no spawn harness preference (detection-gated spawns), and a host-managed full
quality gate with normal RAM and swap behavior (see `orchestration.qualityGate`).

### orchestration.gateDelegation

GLOBAL-LAYER ONLY: the boot snapshot reads the coordinator file exclusively, and the
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

### orchestration.roles, orchestration.rolesPerLevel

`orchestration.roles.<role>` overrides a role file's knob block per role
(`architect`, `orchestrator`, `designer`, `strategist`, `manager`, `worker`, `curator`,
`system-specialist`, `reviewer`).
Precedence: role-file defaults < global settings < repo-local settings. These
settings are the sole developer-controlled spend surface for ordinary dispatched
seats. Agent callers submit a canonical child task document and role to the structural
dispatcher; the plane derives level and resolves the settings-owned launch selection. Agents do
not submit `harness`/`model`/`effort` or direct launch/session spend controls. The
knobs come in a THREE-LAYER model (260703-L16; the full spawn-surface manual
with every parameter, vocabulary, and refusal is
**`docs/reference/harnesses.md`**):

1. **Validated native selection** — `harness` (a known harness id: builtin
   `claude`/`codex`/`pi` or an `orchestration.harnesses`-defined one),
   `model`, `effort`. Role-configured native spawns require all three. The spawn
   path records model/effort in `AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT` provenance and
   carries one typed selection to the adapter. The adapter discovers its token-free,
   installed/account-accurate catalog, validates effort under the selected model, and
   then applies Claude `--model`/`--effort`, Pi `--model`/`--thinking`, or Codex
   `thread/start` model + `config.model_reasoning_effort`. Unknown values fail at the
   runner launch boundary before the configured real vendor session starts.
2. **`launchArgs`** (list of strings) — appended VERBATIM to the harness
   base argv and recorded in spawn provenance. Adapter-owned selector conflicts refuse.
3. **`sessionCommands`** (list of strings; each explicit line submitted through the protocol as
   fresh-session launch configuration before task assignment) and **`promptKeywords`** (list of
   strings prepended exactly once to the later post-readiness `dispatch-brief`). Never validated;
   recorded in spawn provenance. For evidence-capable harness logs (currently Claude), the brief
   retro-proves every launch command and re-issues only a missing/errored command; the durable brief
   remains pending until all required proofs succeed. Other harnesses preserve launch-time transport
   with an explicitly unproven outcome rather than entering an impossible proof retry loop.
   Session-command application by itself does not prove brief delivery.

These settings are consumed by the same public `dispatch_agent` transaction for both caller kinds.
A plane-hosted seat is authorized from injected identity and direct-child scope; an identity-free
developer launcher is authorized by canonical target-document resolution and target role altitude.
The request does not carry caller identity or a mode selector, and a plane refusal never falls back
to ambient. Harness/model/effort remain settings-owned in both modes.

Inside the private control plane, hosted role dispatch uses an exact runtime correlation through
three states: create returns `spawned-unbriefed`; readiness advances that occupant to
harness-ready; one durable exact-pinned `dispatch-brief` starts the briefed-by deadline row. This
correlation is never returned by `dispatch_agent`. Spawned-only and
not-ready seats are not active work. Briefed requires both `deliveryState=delivered` and
`deliveryDetail=harness-log-confirmed`; failure leaves the original row pending without duplicate
brief or respawn.

Native effort options are dynamic and model-gated. Claude currently advertises launch-settable
`low|medium|high|xhigh|max`; `ultracode` is not converted into a session command. Pi validation also
prevents its native silently-clamped thinking behavior from hiding a stale setting. After startup,
Pi and Codex provide model/effort echo evidence; Claude echoes model while its initial effort is
reported as catalog-validated native flag evidence because stream-json init has no effort echo.

`orchestration.rolesPerLevel.<level>.<role>` (ruling 2026-07-07T08:15) adds
the per-LEVEL agent sets the L12 doctrine promises: `leaf` | `master` |
`portfolio` (the `loops.perLevel` vocabulary), each holding the same
knob-override shape. A level override deep-merges over the flat
`orchestration.roles` default at field granularity (harness inherited unless overridden; arrays
replace). The structural dispatcher derives `leaf`, `master`, or `portfolio` from the child role
and its canonical document altitude. Full spend resolution chain:
repo-local level override > global level override > repo-local role default >
global role default > detection-gated default. The resolved level rides spawn
provenance (`spawnLevel`/`spawnLevelSource`). Legacy caller-supplied
`harness`/`model`/`effort`, direct `launch_args`/`prompt_keywords`/
`session_commands`, `env.AR_SPAWN_MODEL`/`env.AR_SPAWN_EFFORT`, or
harness-native spend/endpoint env keys for the built-in Claude/Anthropic and
Codex/OpenAI families refuse with `spend-override-unsupported` before
spawning; move those choices into these settings families.

### orchestration.harnesses

Extends/overrides the builtin harness registry (developer ruling 2026-07-07:
the registry is good defaults, not a wall). Entries are keyed by harness id:
a NEW id adds a harness (`command` and/or `argv` required — the command array
launches it exactly the way you would run it yourself), an EXISTING id
pre-customizes the builtin defaults (its `argv` replaces ours). Optional compatibility
knob-mapping fields for a NEW non-native id: `name`, `modelFlag`, `effortFlag` + `effortFlagValues`,
`effortSessionValues` + `effortSessionCommand` (pairs required together).
Native Claude/Codex/Pi model and effort always belong to their adapter, even when settings override
the builtin base argv.
Detection still gates dispatch; an id known nowhere refuses loudly pointing
at the manual. Schema, semantics, and a worked add-`hermes` example:
`docs/reference/harnesses.md`.

### orchestration.agentNotifier

`orchestration.agentNotifier` configures the deterministic agent-notifier sweep (the
supervisor renamed to its relay role). All fields are optional; an empty block keeps
the safe defaults.

> Compatibility window: the loader still accepts the legacy `orchestration.supervisor`
> key as an explicit alias, with a loud deprecation warning, until the window closes
> (the rename is carried by the agent-notifier reform master). A file setting both keys
> is refused. Remove `orchestration.supervisor` from any live settings file and use
> `orchestration.agentNotifier`.

| Field | Default | Notes |
| --- | --- | --- |
| `enabled` | `true` | Turns the sweep loop on or off. |
| `intervalSeconds` | `10` | Sweep cadence. |
| `staleCutoffSeconds` | `60` | Age after which the agent-notifier heartbeat is reported stale. |
| `redeliverRateLimitSeconds` | store default (`900`) | Per-row floor between redelivery attempts. Values below `900` seconds are refused. |
| `signalCooldownSeconds` | `900` | Minimum interval between repeated pane/seat-liveness owner signals for the same target, leaf, finding kind, and detail. Values below `900` seconds are refused. |
| `redeliverBudget` | `1` | Maximum inbox redelivery attempts per sweep. Harness-log confirmation is synchronous and bounded per input, so backlogs drain across sweeps without multiplying that wait inside one heartbeat tick. |
| `escalationBudget` | `250` | Per-sweep load-shed cap on owner-signal emissions (seat-liveness + dead-upstream), the twin of `redeliverBudget`. Shed findings re-fire next sweep (level-triggered). Not a policy knob: the timed escalation ladder is retired. |

`enabled: false` is the emergency kill switch for the agent-notifier loop. During the
2026-07-09 redelivery-cadence incident the global coordinator settings disabled
the agent-notifier until the 15-minute redelivery and signal-cooldown fix landed and
passed smoke.

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

### orchestration.concurrency, orchestration.spawn

`orchestration.concurrency` caps parallel orchestration fan-out:
`maxParallelMasters`, `maxParallelLeaves`, `maxSubAgents` (positive integers;
omitted means uncapped). The caps are doctrine input for the spawning seats.

`orchestration.spawn.harness` names the default harness the private launch seam uses when no
role/level knob supplies one. Resolution order at that seam:
role knobs (level-merged) > repo-local settings > global settings >
detection-gated default (the first
effective-registry harness found on PATH; the repo-local layer is selected by the canonical task
document's repository). Values are validated against
the effective harness ids (builtin + `orchestration.harnesses`) and gated by
detection — a settings value can never inject a command through a reference;
argv is definable only in the explicit `orchestration.harnesses` family.

```jsonc
"orchestration": {
  "roles": {
    "architect":    { "harness": "claude", "model": "claude-opus-4-8", "effort": "high" },
    "orchestrator": { "harness": "claude", "model": "claude-opus-4-8", "effort": "high" },
    "strategist":   { "harness": "claude", "model": "claude-fable-5", "effort": "max" },
    "reviewer":     { "harness": "claude", "model": "claude-sonnet-5", "effort": "high" },
    "system-specialist": { "harness": "claude", "model": "claude-fable-5", "effort": "high" },
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
  accumulated canonical master at master-to-parent integration; standalone and organizational
  leaves retain independent leaf review.
- `perLevel.leaf.loop: "scored"` — the owning seat scores each leaf at
  dispatch. `perLevel.master.loop: "seam-required"` names the default loop
  posture; `"none"` configures a manager without iterative loop rounds (a master whose
  leaves all score direct still runs the applicable candidate-bound route review at each
  owning altitude).
  **This knob governs the LOOP only (review rounds): it cannot disable an applicable
  standalone/organizational leaf route review, curator/closeout admission, or the master-exit
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
  direction, 2026-07-06). Today provider configuration is authority-file
  territory (the server derives lifecycle settings from `providers.*`), and
  the OLD implicit fallback that read `contextProviders` from this file was
  retired with L13. A future `contextProviders` key at the top level of the
  global file is tolerated-not-parsed until that migration lands.
- Other top-level keys (`$comment`, `version`, and any future family) are
  likewise tolerated-not-parsed by the agentic loader; only documented
  `orchestration.*` keys are read, and only they fail loud.
