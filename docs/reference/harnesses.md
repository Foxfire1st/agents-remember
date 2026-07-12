# Harnesses & Spawn Knobs Reference

The single manual for the spawn surface (260703-L16): what a harness is to
Agents Remember, every role-knob parameter and its delivery vehicle, the
per-harness vocabularies as built, detection and refusal behavior, and a
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
| `modelFlag` | The launch flag the model knob maps onto (`--model <value>`), if any. |
| `effortFlag` + `effortFlagValues` | The launch flag the effort knob maps onto, and the values that flag ACCEPTS. Declared together. |
| `effortSessionValues` + `effortSessionCommand` | Effort values the launch flag rejects but the RUNNING session accepts as a command; the command template (`{value}` placeholder) that delivers them post-launch. Declared together. |

### Built-in registry (good defaults, not a wall)

The curated defaults live in `mcp/src/agents_remember/serving/harnesses.py`:

| id | argv | modelFlag | effortFlag (values) | session effort (command) |
| --- | --- | --- | --- | --- |
| `claude` (Claude Code) | `["claude"]` | `--model` | `--effort` (`low`, `medium`, `high`, `xhigh`, `max`) | `ultracode` → `/effort ultracode` |
| `codex` (Codex) | `["codex"]` | `--model` | `--config model_reasoning_effort={value}` (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`) | — |
| `pi` (Pi.dev) | `["pi"]` | — (env-only) | — (env-only) | — |

**Env-only** (currently Pi.dev) means: the model/effort knobs still ride the spawn env as
`AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT` (session-start visibility), but nothing is
put on the command line and no effort vocabulary is enforced. Growing a
mapping for a builtin is a one-line registry edit — or a settings override,
below.

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
  yourself (our curated knob mapping survives unless you override it too).
- Detection still applies: the `command` is probed on `PATH` at dispatch; an
  undetected harness refuses with `harness-not-detected`.
- Vocabulary fields come in delivery-vehicle pairs and must resolve together:
  `effortFlag` with `effortFlagValues`, `effortSessionValues` with
  `effortSessionCommand`. A flag without a vocabulary would reintroduce the
  silent-degrade risk, so the loader refuses it.
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
| `model` | The harness's `modelFlag` on the launch argv, e.g. `--model opus`; also rides env as `AR_SPAWN_MODEL`. | Model names are NOT enum-validated (they evolve faster than any registry). A settings-defined harness with no `modelFlag` refuses the knob with guidance (`model-invalid`). |
| `effort` | Per value: a flag value rides the harness's `effortFlag` on the argv; a session value rides a post-launch session command; either way it rides env as `AR_SPAWN_EFFORT`. | Validated at DISPATCH against the harness's vocabulary (flag values ∪ session values). Unknown values refuse (`effort-invalid`) naming the harness and BOTH sets. Codex accepts exactly `none|minimal|low|medium|high|xhigh`; Pi.dev remains env-only. |

Why dispatch-time effort validation exists: the installed claude CLI accepts
`--effort low|medium|high|xhigh|max` and **warns-then-silently-degrades** on
anything else (probed 2026-07-07 with `ultracode`). Without the refusal, an
out-of-vocabulary value would quietly downgrade the most reasoning-hungry
seats. The two-vehicle split exists because claude's interactive `/effort`
command accepts `ultracode` ("xhigh + workflows") while the launch flag
rejects it — one harness, two vocabularies, each value carrying its own
delivery vehicle.

### Layer 2 — launch free-form: `launchArgs`

A list of strings appended VERBATIM to the harness launch argv, after the
mapped knob flags. Never validated (it is the escape hatch for flags we never
enumerated); recorded in spawn provenance (the session catalog row and the
tool payload). Example: `["--dangerously-skip-permissions"]`.

### Layer 3 — session free-form: `sessionCommands` and `promptKeywords`

- `sessionCommands`: a list of lines, each pasted into the freshly spawned
  session as its OWN entry and submitted during launch, before any task
  assignment — the vehicle for any session-level harness feature (a
  session-vocabulary effort like claude's `ultracode` is delivered this way
  automatically, ahead of configured session commands). This launch-command outcome is distinct
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
    "portfolio": { "reviewer": { "model": "fable", "effort": "ultracode" } }
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
| `level="portfolio"` | claude (inherited) | fable (portfolio override) | ultracode (portfolio override) | `/effort ultracode` session command |

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

All pre-spawn, nothing mutated:

| Status | Trigger | Message carries |
| --- | --- | --- |
| `harness-unknown` | id known neither in the registry nor in settings | the id, the known set, and a pointer to `orchestration.harnesses` + this manual |
| `harness-not-detected` | known id whose command is not on `PATH` | the id (and the settings source when a configured preference caused it) |
| `effort-invalid` | effort outside the harness's vocabulary, or any effort for a mapping-less settings-defined harness | the harness, BOTH value sets (flag + session), and the launchArgs/sessionCommands guidance |
| `model-invalid` | model knob for a settings-defined harness with no `modelFlag` | the harness and the declare-or-launchArgs guidance |
| `spend-override-unsupported` | ordinary caller supplied removed spend fields (`harness`, `model`, `effort`, launch/session controls, `AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT`, or harness-native spend/endpoint env keys such as `ANTHROPIC_MODEL` / `OPENAI_BASE_URL`) | the removed fields and the settings families that own them |
| `level-invalid` | dispatch level outside `leaf|master|portfolio` | the value and the valid set |
| `leaf-taken` | the target leaf already has a running same-role session | the owning session (server-arbitrated, never overridden) |

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
    "claude": { "argv": ["claude", "--continue"] }   // replaces our argv; knob mapping survives
  }
}
```
