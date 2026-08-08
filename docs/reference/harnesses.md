# Harnesses & Spawn Knobs Reference

The single manual for the spawn surface (260703-L16): what a harness is to
Agents Remember, every role-knob parameter and its delivery vehicle, the
dynamic native catalogs, detection and refusal behavior, and a
worked example that teaches the system a brand-new harness through settings.

Related references: `settings-json.md` (the settings families and merge
rules), `skills/l-01-agent-lifecycles` (the role doctrine the knobs
parameterize).

## What A Harness Is

A **harness** is a TUI coding agent the framework can spawn into a hosted
tmux session — via the dashboard's launch buttons or the agent-facing
`spawn_agent_session` MCP tool (one shared opener; no parallel spawn path).
Each harness is described by an entry with:

| Field | Meaning |
| --- | --- |
| `id` | The stable identifier settings and dashboard launches use (`"claude"`). Never a command. |
| `name` | Display name (dashboard buttons). |
| `command` | The executable probed on `PATH` for detection. |
| `argv` | The exact launch command array, e.g. `["claude"]`. Fixed server-side. |
| `modelFlag` | Optional compatibility mapping for a settings-defined non-native harness. Native adapters do not use it. |
| `effortFlag` + `effortFlagValues` | Optional launch mapping and vocabulary for a settings-defined non-native harness. Declared together. |
| `effortSessionValues` + `effortSessionCommand` | Optional explicit session-command mapping for a settings-defined non-native harness. Declared together. |

### Built-in registry (good defaults, not a wall)

The curated defaults live in `mcp/src/agents_remember/serving/harnesses.py`:

| id | base argv | token-free catalog | initial configuration | acceptance evidence |
| --- | --- | --- | --- | --- |
| `claude` (Claude Code) | `["claude"]` | `list_models` control request | `--model <key> --effort <level>` | model echoed by `system/init`; effort is catalog-validated because stream-json init has no effort echo |
| `codex` (Codex) | `["codex"]` | `model/list` | `thread/start` model + `config.model_reasoning_effort` | thread/model/effort echo validation |
| `pi` (Pi.dev) | `["pi"]` | `get_available_models` | `--model <provider/id> --thinking <level>` plus adapter-owned `--mode rpc` | `get_state` model/thinking echo validation |

For role-configured native spawns, `model` and `effort` are a required pair. They still ride
`AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT` as provenance, but those env names are not the vendor
configuration mechanism. The adapter first discovers the installed/account catalog with the base
argv, validates the model-gated selection, then applies its native launch material to a fresh real
adapter. A stale value never falls back to the vendor default.

### Structured-protocol compatibility

Production adapters launch the installed harness and decide compatibility from
the structured protocol evidence Agents Remember consumes, not from an exact
CLI package-version comparison:

- Claude startup uses three distinct evidence sources. The correlated
  `control_request/initialize` success must contain `commands` rows with `name` and optional
  `aliases`; its envelope may also carry `pending_permission_requests` and
  `pending_user_dialog_requests`. `system/init` must contain `session_id`,
  `claude_code_version`, `cwd`, the current `model`, `permissionMode`, `tools`, and
  `slash_commands`. Agents Remember then separately issues a correlated
  `control_request/list_models`; its dynamic `models` rows contain `value`, `displayName`,
  `description`, optional `resolvedModel`/`disabled`, and model-local
  `supportsEffort`/`supportedEffortLevels`, and the current model must resolve to one row. In
  Claude 2.1.210 neither `control_request/initialize` nor `system/init` provides account or
  catalog data; the separate `list_models` response is the catalog source.
- Codex must complete `initialize`, `model/list`, and `thread/start` or
  `thread/resume`; the reported client identity and thread CLI-version token
  must agree, and the selected model, reasoning-effort menu, cwd, thread state,
  and configured policies must validate.
- Pi RPC does not expose an installed package version in its startup protocol.
  Compatibility therefore comes from correlated `get_state` and `get_entries`
  responses plus the documented event/interaction fields. The exact Pi package
  used by a smoke fixture is not a production launch pin.

Reported version text remains diagnostic evidence. Missing, malformed, or
contradictory required fields fail loudly; there is no pane/log fallback or
version-range guess.

### Serving-cutover restart contract

The dashboard daemon and MCP servers share durable operator-inbox and terminal
catalog files while keeping their Python schemas loaded in memory. A serving
upgrade is therefore one reload boundary, not a dashboard-only restart. After
live work is saved and reported, reload every long-lived consumer before
post-cutover validation:

1. Restart the `agents-remember dashboard` daemon (including an auto-started
   daemon supervised by an MCP server) so its FastAPI routes, projector,
   agent-notifier, inbox store, catalog models, and packaged dashboard assets come
   from the new build.
2. Restart or reload every connected harness/client process that owns an
   Agents Remember MCP server subprocess. Each Claude, Codex, Pi, or other MCP
   client has its own in-memory `OperatorInboxEntry` and catalog reader; leaving
   even one pre-cutover process alive can make `operator_inbox_post`, poll, or
   consume fail against rows written by the new daemon.
3. End and recreate each bridge-backed hosted session that must be validated.
   Its per-session `harness_control_runner` and vendor adapter are separate
   long-lived Python processes and do not hot-reload when only the dashboard
   daemon changes. Preserve/report session work before replacement.
4. Reload open browser dashboard tabs after the daemon is serving the new build
   so the JavaScript projection models match the server response shape.

One-shot CLI commands started after the cutover load the current package and do
not need a separate reload. Settings do not need mutation for this contract.

### Extending or overriding via settings (`orchestration.harnesses`)

The registry is **good defaults, never a rigid wall** (developer ruling
2026-07-07). The `orchestration.harnesses` settings family — global
`<coordinationRoot>/system/settings.json`, repo-local
`<repo>/system/settings.json`, standard L13 merge and fail-loud rules —
merges over the registry **by id**:

- A **new id ADDS a harness** the framework never enumerated. It must declare
  `command` and/or `argv` (`command` defaults to `argv[0]`; `argv` defaults to
  `[command]`).
- An **existing id OVERRIDES the defaults per field**: its `argv` array
  REPLACES ours — launch the harness exactly the way you would run it
  yourself. Native model/effort ownership remains in the adapter, not this registry entry.
- Detection still applies: the `command` is probed on `PATH` at dispatch; an
  undetected harness refuses with `harness-not-detected`.
- Vocabulary fields come in delivery-vehicle pairs and must resolve together:
  `effortFlag` with `effortFlagValues`, `effortSessionValues` with
  `effortSessionCommand`. A flag without a vocabulary would reintroduce the
  silent-degrade risk, so the loader refuses it.
- For a new non-native id, a settings-declared `effortFlagValues` enum is authoritative. Native
  Claude/Codex/Pi ids always validate against their dynamic adapter catalog.
- The `effortSessionCommand` **template** must render with `{value}` and
  reference no other placeholder: a stray field (`/set {mode}={value}`), a
  positional `{}`, or an unmatched brace is refused by the loader naming the
  harness. The check runs post-merge (a builtin override may supply just the
  command), so a bad template surfaces the same structured refusal every other
  bad knob gets instead of crashing the spawn with a raw `str.format` error.

An id known neither in the registry nor in settings refuses **loudly** at
dispatch, naming the known set and pointing here — never a crash.

## The Role Knobs, Parameter By Parameter

Role knobs live in `orchestration.roles.<role>` (flat defaults) and
`orchestration.rolesPerLevel.<level>.<role>` (per-level overrides). For ordinary
agent-driven spawns, these settings are the sole developer-controlled spend
surface: `spawn_agent_session` callers declare `env.AR_SPAWN_ROLE` and `level`,
not `harness`/`model`/`effort` or direct launch/session spend controls. Three
settings layers have distinct validation postures:

### Layer 1 — validated enum knobs

| Knob | Delivery vehicle | Validation |
| --- | --- | --- |
| `harness` | Selects the harness entry (argv comes from it). | Must be a known id: builtin or `orchestration.harnesses`-defined. Checked at settings load AND at dispatch. |
| `model` | Native adapter launch port; also rides env as `AR_SPAWN_MODEL` provenance. | Required for a role-configured native spawn and validated against the installed/account dynamic catalog. A settings-defined non-native harness uses its declared `modelFlag`. |
| `effort` | Native adapter launch port; also rides env as `AR_SPAWN_EFFORT` provenance. | Required for a role-configured native spawn and validated under the selected model against only `launchSettable` dynamic options. A settings-defined non-native harness uses its declared mapping. |

This model-gated dynamic validation prevents both stale package enums and silent CLI clamping. For
example, Claude's launch flag accepts `low|medium|high|xhigh|max`; `ultracode` is not launch-settable
and is refused rather than converted into a `/effort` prompt. Pi's silently clamped thinking input
is likewise verified against `get_state` after launch.

### Layer 2 — launch free-form: `launchArgs`

A list of strings appended VERBATIM to the settings-owned base argv before adapter preparation.
It remains the escape hatch for unrelated flags and is recorded in spawn provenance. A value that
duplicates an adapter-owned selector (`--model`, `--effort`, `--thinking`, or Codex model/reasoning
config) refuses instead of creating two authorities. Example: `["--dangerously-skip-permissions"]`.

### Layer 3 — session free-form: `sessionCommands` and `promptKeywords`

- `sessionCommands`: a list of explicit lines submitted through the protocol bridge during launch,
  before any task assignment. Native model/effort is never synthesized into this list. This
  launch-command outcome is distinct
  from brief delivery and cannot make a seat active work. Once the later dispatch brief binds the
  harness log, an evidence-capable adapter (currently Claude) retroactively proves every pre-brief
  command from command entry + successful stdout; a missing or errored command alone is re-issued,
  and the dispatch row remains pending unless every required proof succeeds. Harnesses without a
  truthful command-entry/output adapter retain their existing launch-time transport semantics and
  keep that outcome explicitly unproven; they are not placed into an impossible retry loop. The
  initial command delivery never moves out of launch. Never caller-validated; recorded in spawn
  provenance.
- `promptKeywords`: a list of keywords prepended as the first line of the
  post-readiness dispatch-brief exactly once (session modes the model interprets, e.g. a prompt
  keyword like `ultracode`). Never validated; recorded in spawn provenance.

Dispatch order is explicit: **spawn (launch argv → settings session commands) →
`hosted_session_readiness` on the exact returned id → one durable exact-agent `dispatch-brief`
(keywords first)**. Spawn success is `spawned-unbriefed`; a spawned-only or not-ready seat is not
active work. Brief delivery is accepted only when the durable row reports
`deliveryState=delivered` and `deliveryDetail=harness-log-confirmed`. A failed attempt leaves that
same row pending for recovery; it does not create a duplicate brief or replacement session.

### The dispatch level: `level` and `orchestration.rolesPerLevel`

The L12 doctrine runs each level with its own agent set; `rolesPerLevel`
makes that expressible (ruling 2026-07-07T08:15):

```jsonc
"orchestration": {
  "roles": {                       // flat DEFAULTS (existing files unchanged)
    "reviewer": { "harness": "claude", "model": "sonnet", "effort": "high" }
  },
  "rolesPerLevel": {               // per-level overrides; vocabulary = leaf|master|portfolio
    "master":    { "reviewer": { "model": "opus",  "effort": "xhigh" } },
    "portfolio": { "reviewer": { "model": "fable", "effort": "max" } }
  }
}
```

`spawn_agent_session(level=...)` declares the dispatch level (`leaf` |
`master` | `portfolio`, default `leaf`); the dispatcher knows its level — a
manager dispatching leaf seats passes `leaf`, the master-seam reviewer
`master`, portfolio/end-to-end seats `portfolio`. The level override
deep-merges over the flat default at leaf-key granularity (unset fields
inherit; lists replace). Full spend resolution chain:

**repo-local level override > global level override > repo-local role default >
global role default > spawn preference/detection.**

Legacy caller-supplied `harness`, `model`, `effort`, `launch_args`,
`prompt_keywords`, `session_commands`, `env.AR_SPAWN_MODEL`, or
`env.AR_SPAWN_EFFORT` values return `spend-override-unsupported` before any
session is spawned. Harness-native spend/endpoint env keys for the built-in
Claude/Anthropic and Codex/OpenAI families, such as `ANTHROPIC_MODEL`,
`ANTHROPIC_BASE_URL`, `OPENAI_MODEL`, `OPENAI_BASE_URL`, and API key/project
selectors, refuse the same way because caller `env` is otherwise seeded into
the spawned harness process. Move those choices into `orchestration.roles`,
`orchestration.rolesPerLevel`, `orchestration.spawn`, or
`orchestration.harnesses`.

The resolution walk for the reviewer economics above (role riding the spawn
env as `AR_SPAWN_ROLE=reviewer`):

| Dispatch | harness | model | effort | effort vehicle |
| --- | --- | --- | --- | --- |
| `level="leaf"` (or omitted) | claude (flat) | sonnet (flat) | high (flat) | `--effort high` |
| `level="master"` | claude (inherited) | opus (master override) | xhigh (master override) | `--effort xhigh` |
| `level="portfolio"` | claude (inherited) | fable (portfolio override) | max (portfolio override) | native `--effort max` |

The RESOLVED level and its source (`explicit`/`default`) are recorded in
spawn provenance (`spawnLevel`/`spawnLevelSource` on the catalog row and the
payload).

## Detection

A harness is launchable only when its `command` resolves on `PATH`
(`shutil.which`). `GET /api/harnesses` reports the effective set with
per-harness detection; `spawn_agent_session` harness resolution order is role
knobs (level-merged) > `orchestration.spawn.harness` preference > the first
detected effective-registry harness.

## Refusals (never crashes)

| Status | Trigger | Message carries |
| --- | --- | --- |
| `harness-unknown` | id known neither in the registry nor in settings | the id, the known set, and a pointer to `orchestration.harnesses` + this manual |
| `harness-not-detected` | known id whose command is not on `PATH` | the id (and the settings source when a configured preference caused it) |
| `effort-invalid` | effort outside the harness's vocabulary, or any effort for a mapping-less settings-defined harness | the harness, BOTH value sets (flag + session), and the launchArgs/sessionCommands guidance |
| `model-invalid` | model knob for a settings-defined harness with no `modelFlag` | the harness and the declare-or-launchArgs guidance |
| `launch-selection-invalid` | role-configured native launch omitted model or effort | the exact missing field; refused before tmux creation |
| `spend-override-unsupported` | ordinary caller supplied removed spend fields (`harness`, `model`, `effort`, launch/session controls, `AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT`, or harness-native spend/endpoint env keys such as `ANTHROPIC_MODEL` / `OPENAI_BASE_URL`) | the removed fields and the settings families that own them |
| `level-invalid` | dispatch level outside `leaf|master|portfolio` | the value and the valid set |
| `leaf-taken` | the target leaf already has a running same-role session | the owning session (server-arbitrated, never overridden) |

Unknown, unselectable, non-launch-settable, or conflicting native selections fail at the hosted
runner launch boundary after the catalog row/tmux exists but before the configured real vendor
session starts. The endpoint remains addressable with `control=failed`, `acceptance=rejected`, and
the exact message in `raw.bridgeError`, so readiness/daemon consumers do not see a generic fallback
or disconnect. Roleless legacy/dashboard opens remain selection-less until the L4 per-session
request/default authority is available.

## Worked Example: Teaching The System `hermes`

Suppose you use a TUI agent called `hermes` that nobody registered. A role
configured with `"harness": "hermes"` refuses at settings load until the harness
is declared:

> orchestration.roles.worker.harness must be a harness registry id or an
> orchestration.harnesses-defined id (claude, codex, pi), got 'hermes'
> (see docs/reference/harnesses.md).

Teach it in the GLOBAL agentic settings
(`<coordinationRoot>/system/settings.json`):

```jsonc
{
  "orchestration": {
    "harnesses": {
      "hermes": {
        "name": "Hermes",
        "command": "hermes",                    // probed on PATH for detection
        "argv": ["hermes", "--tui"],            // launched exactly like this
        "modelFlag": "--model",
        "effortFlag": "--reasoning",            // hermes's own effort flag...
        "effortFlagValues": ["low", "high"]     // ...and the values it accepts
      }
    },
    "roles": {
      "worker": { "harness": "hermes", "model": "h-1", "effort": "high" }
    }
  }
}
```

Now `spawn_agent_session(env={"AR_SPAWN_ROLE": "worker"})` launches
`hermes --tui --model h-1 --reasoning high` (knobs also riding the env), and
`effort: "turbo"` refuses naming `[low, high]`. Had you declared NO
`effortFlag`/vocabulary, the effort knob itself would refuse with guidance —
declare the mapping or carry a deliberate raw argv through settings-owned
`launchArgs` — explicit over guessing a flag that might mean something else. A repo-local
`<repo>/system/settings.json` may override any leaf of the entry (e.g. a
different `argv`) for that repo's dispatches.

Pre-customizing a BUILTIN works the same way — override by id:

```jsonc
"orchestration": {
  "harnesses": {
    "claude": { "argv": ["claude", "--continue"] }   // replaces base argv; native adapter still owns model/effort
  }
}
```
