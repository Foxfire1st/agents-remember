# Harnesses & Spawn Knobs Reference

The manual for the role knobs (260703-L16): what a harness is to
Agents Remember, the role-knob parameters and the per-level overrides.

Related references: `settings-json.md` (the settings families and merge
rules), `skills/l-01-agent-lifecycles` (the role doctrine the knobs
parameterize).

## What A Harness Is

A **harness** is the agent kind the host runs for a role agent. The host's catalog names the
kinds, their models and their efforts; whether an agent of an id exists is the host's answer.
A role names its kind in `harness`; a start names it in `agent`.

### Built-in ids

The built-in ids live in `mcp/src/agents_remember/kernel/harnesses.py`.
`orchestration.spawn.harness` is checked against these ids and the ids a settings file adds
under `orchestration.harnesses`. A role launch that names no agent takes the first of them the
host offers, in that order.

### Adding an id via settings (`orchestration.harnesses`)

The `orchestration.harnesses` settings family — global
`~/.config/agents-remember/settings.json` (under `XDG_CONFIG_HOME` when set), repo-local
`<repo>/system/settings.json`, standard L13 merge and fail-loud rules —
is keyed **by id**:

- A **new id ADDS** that id to the effective ids.
- The fields an earlier build read from an entry (`name`, `command`, `argv`, `modelFlag`,
  `effortFlag`, `effortFlagValues`, `effortSessionValues`, `effortSessionCommand`) stay
  accepted but no code reads them in this build; `server_info` names the ones a file holds
  under `unusedSettingsKeys`. Any other field is refused.

## The Role Knobs

Role knobs live in `orchestration.roles.<role>` (flat defaults) and
`orchestration.rolesPerLevel.<level>.<role>` (per-level overrides). The keys, and what each
does on a start, stand in `settings-json.md` (orchestration.roles, orchestration.rolesPerLevel):
`harness`, `model` and `effort` are checked against the host's catalog at the start.
`launchArgs`, `sessionCommands` and `promptKeywords` stay accepted but no code reads them in
this build.

### The level: `orchestration.rolesPerLevel`

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

The level follows the role: `leaf` for worker, reviewer and curator, `master` for manager,
`portfolio` for architect, investigator and orchestrator.
The level override
deep-merges over the flat default at field granularity (unset fields
inherit; lists replace). Resolution chain in the settings:

**repo-local level override > global level override > repo-local role default >
global role default.**
