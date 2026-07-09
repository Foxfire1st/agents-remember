# settings.json Reference

Agents Remember has FOUR settings families, each with exactly one home:

| Family | Home | Read cadence |
| --- | --- | --- |
| Boot infrastructure (repos, providers, transport, timeoutCaps, dashboard) | MCP authority settings file (outside the coordinator root) | boot |
| Memory topology (`onboarding.storage`, `pathRules`, `crossRepo`) | memory-root `system/settings.json` (beside `settings.md`) | per resolution |
| **Agentic settings** (`orchestration.*`: gate delegation, loops, roles + rolesPerLevel, concurrency, spawn preference, harness definitions) | **coordinator `system/settings.json`** (global), `<code-repo>/system/settings.json` (local override) | per use (`gateDelegation`: boot snapshot) |
| Provider lifecycle settings | server-generated from the authority config (`--from-settings`) | per command |

`system/settings.md` remains the human and agent prose guidance file beside a
memory root's `settings.json`.

The coordinator root's `system/settings.json` is the GLOBAL agentic settings
file — it is NOT an MCP authority file (the server refuses it as `--config`)
and NOT a provider settings source (the old implicit fallback to it is
removed; an explicit `--from-settings` path is still read wherever it points). MCP authority settings
live outside the coordinator root; see `examples/mcp/settings.example.json`.

## Internal Memory Example

```json
{
  "version": 1,
  "onboarding": {
    "storage": {
      "mode": "repo-sidecar"
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
  }
}
```

## External Memory Example

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
      "contractPath": null
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

## Memory Fields

`version` identifies the settings shape, not a release number. Internal
(`repo-sidecar`) memory uses `version` 1; external (`memory-repo`) memory uses
`version` 2, which adds the `crossRepo` block. The version difference reflects
the different schema each storage mode needs, so the internal and external
examples above are both current.

`onboarding.storage.mode` selects storage for eligible onboarding. Current
public modes are `repo-sidecar`, `memory-repo`, and explicit inline mode where
supported by repository settings and file type.

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

`retirement` (optional) configures the auto-land hooks for worktree-backed tmux
seats. `autoLandOnIntegration` and `autoLandOnFinalize` (both default `true`)
gate whether integrating a leaf or finalizing a master marks spent hosted seats
as landed/archive. Landing leaves transcripts inspectable and non-active; it
does not close tmux. The legacy `autoRetireOnIntegration` and
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
`spawn` · `harnesses`), in EITHER layer, is REFUSED naming the offending file.
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
no spawn harness preference (detection-gated spawns).

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
settings are the sole developer-controlled spend surface for ordinary spawned
seats; `spawn_agent_session` callers declare role and level, not
`harness`/`model`/`effort` or direct launch/session spend controls. The
knobs come in a THREE-LAYER model (260703-L16; the full spawn-surface manual
with every parameter, vocabulary, and refusal is
**`docs/reference/harnesses.md`**):

1. **Validated enum knobs** — `harness` (a known harness id: builtin
   `claude`/`codex`/`pi` or an `orchestration.harnesses`-defined one),
   `model`, `effort`. The spawn path seeds `model`/`effort` into the spawn env
   (`AR_SPAWN_MODEL`/`AR_SPAWN_EFFORT`) AND applies them onto the harness
   launch argv per-harness (claude: `--model`/`--effort`; a mapping-less
   harness stays env-only). `effort` is validated per-harness at DISPATCH:
   unknown values refuse loudly naming the harness and its valid sets.
2. **`launchArgs`** (list of strings) — appended VERBATIM to the harness
   launch argv. Never validated; recorded in spawn provenance.
3. **`sessionCommands`** (list of strings; each line pasted + submitted into
   the fresh session BEFORE the brief) and **`promptKeywords`** (list of
   strings prepended as the first line of the dispatch-brief paste). Never
   validated; recorded in spawn provenance.

The claude effort vocabulary (empirical, 2026-07-07) is TWO-VEHICLE:

| Value | Delivery vehicle |
| --- | --- |
| `low`, `medium`, `high`, `xhigh`, `max` | the `--effort` launch flag |
| `ultracode` | the `/effort ultracode` session command, pasted post-launch before the brief |

Rationale: the installed claude CLI **warns-then-silently-degrades** on
unknown `--effort` values (probed with `ultracode`, which its interactive
`/effort` command DOES accept), so unvalidated values would quietly downgrade
the most reasoning-hungry seats — dispatch accepts the union of both sets and
refuses anything in neither.

`orchestration.rolesPerLevel.<level>.<role>` (ruling 2026-07-07T08:15) adds
the per-LEVEL agent sets the L12 doctrine promises: `leaf` | `master` |
`portfolio` (the `loops.perLevel` vocabulary), each holding the same
knob-override shape. A level override deep-merges over the flat
`orchestration.roles` default at leaf-key granularity (harness inherited
unless overridden; arrays replace). The dispatcher declares its level via
`spawn_agent_session(level=...)`, default `leaf`. Full spend resolution chain:
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
pre-customizes the builtin defaults (its `argv` replaces ours). Optional
knob-mapping fields: `name`, `modelFlag`, `effortFlag` + `effortFlagValues`,
`effortSessionValues` + `effortSessionCommand` (pairs required together).
Detection still gates dispatch; an id known nowhere refuses loudly pointing
at the manual. Schema, semantics, and a worked add-`hermes` example:
`docs/reference/harnesses.md`.

### orchestration.supervisor

`orchestration.supervisor` configures the deterministic supervisor sweep. All
fields are optional; an empty block keeps the safe defaults.

| Field | Default | Notes |
| --- | --- | --- |
| `enabled` | `true` | Turns the sweep loop on or off. |
| `intervalSeconds` | `10` | Sweep cadence. |
| `staleCutoffSeconds` | `60` | Age after which the supervisor heartbeat is reported stale. |
| `redeliverRateLimitSeconds` | store default (`900`) | Per-row floor between redelivery attempts. Values below `900` seconds are refused. |
| `signalCooldownSeconds` | `900` | Minimum interval between repeated pane/seat-liveness owner signals for the same target, leaf, finding kind, and detail. Values below `900` seconds are refused. |
| `redeliverBudget` | `250` | Maximum inbox redelivery attempts per sweep. Large backlogs are spread across sweeps while the heartbeat keeps ticking. |
| `escalationBudget` | `250` | Maximum escalation-rung emissions per sweep. A large backlog of rung-due rows is spread across sweeps (rung readiness is level-triggered, so deferred rows re-fire on the next sweep) rather than doing O(backlog) synchronous owner pastes + `escalation.rung` event appends in one sweep. Positive integer. |

`enabled: false` is the emergency kill switch for the supervisor loop. During the
2026-07-09 redelivery-cadence incident the global coordinator settings disabled
the supervisor until the 15-minute redelivery and signal-cooldown fix landed and
passed smoke.

### orchestration.concurrency, orchestration.spawn

`orchestration.concurrency` caps parallel orchestration fan-out:
`maxParallelMasters`, `maxParallelLeaves`, `maxSubAgents` (positive integers;
omitted means uncapped). The caps are doctrine input for the spawning seats.

`orchestration.spawn.harness` names the default harness `spawn_agent_session`
uses when no role/level knob supplies one. Resolution order at the spawn seam:
role knobs (level-merged) > repo-local settings > global settings >
detection-gated default (the first
effective-registry harness found on PATH; the repo-local layer is selected by
the qualified leaf key's repository segment). Values are validated against
the effective harness ids (builtin + `orchestration.harnesses`) and gated by
detection — a settings value can never inject a command through a reference;
argv is definable only in the explicit `orchestration.harnesses` family.

```jsonc
"orchestration": {
  "roles": {
    "architect":    { "harness": "claude", "effort": "high" },
    "orchestrator": { "harness": "claude", "effort": "high" },
    "strategist":   { "effort": "ultracode" },  // session-vocabulary value → "/effort ultracode" post-launch
    "reviewer":     { "harness": "claude", "model": "sonnet", "effort": "high" },
    "system-specialist": { "harness": "claude", "model": "fable", "effort": "high" },
    "curator":      { "harness": "codex",  "effort": "medium" },
    "worker":       { "harness": "codex",  "effort": "medium" }
  },
  "rolesPerLevel": {
    "master":    { "reviewer": { "model": "opus",  "effort": "xhigh" } },
    "portfolio": { "reviewer": { "model": "fable", "effort": "ultracode" } }
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
  (builder + independent reviewer); at/above `builderAt` it runs
  builder-verified (builder + owner report-vs-artifact check, no reviewer);
  below both it is direct (the level's ordinary build channel implements —
  no loop machinery).
- `perLevel.leaf.loop: "scored"` — the owning seat scores each leaf at
  dispatch. `perLevel.master.loop: "seam-required"` names the default loop
  posture; `"none"` configures the workflow-free manager (a master whose
  leaves all score direct carries no loop machinery). **This knob governs the
  LOOP only (review rounds / workflow-free manager): the master-exit SEAM gate
  is unconditional doctrine — no knob value touches it.** Loop posture names
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
