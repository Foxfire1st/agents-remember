# settings.json Reference

`system/settings.json` is the machine-readable memory settings file for a
memory root. `system/settings.md` remains the human and agent prose guidance
file.

Coordinator roots should not carry a `system/settings.json` authority file for
MCP/provider behavior. MCP authority settings live outside the coordinator root;
see `examples/mcp/settings.example.json`.

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
      "memorySettingsIncludes": [],
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
  "orchestration": {
    "gateDelegation": {
      "policy": "all-human",
      "kinds": {
        "closeout-approval": {
          "role": "human"
        }
      }
    }
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
`coordinationRoot/memory-repos/ar-<repo-id>`.

`repositories.<repo-id>.memorySettingsIncludes` may list extra absolute settings
files, but every include must stay inside either the configured code repository
or its configured memory root.

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

`orchestration.gateDelegation` (optional) configures server-enforced lifecycle
gate delegation. If omitted, the policy is `all-human`: every gate requires the
existing human/developer decision path. The built-in
`manager-decides-leaf-gates` policy adds the manager role for leaf
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
human-pinned and cannot be delegated.

## Orchestration Loops (documented schema — storage lands with L13)

`orchestration.loops` configures the three-party review loops (OWNER → BUILDER →
REVIEWER) the `l-01-agent-lifecycles` skill runs at every level that owns work.
**This section defines the schema's meaning; it is not parsed yet.** Storage
lands with the 260703-L13 settings unification in the ar-coordination
`system/settings.json`, with repo-local settings taking precedence over the
global file.

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
  is unconditional doctrine — no knob value touches it** (deeper knob semantics
  are L13's to resolve). Each level runs its loop with its own agent set
  (`orchestration.roles` knobs per role).
- `perLevel.portfolio.loop: "strategist"` names the portfolio loop's parties.
  **The strategist's mandatory pre-run is doctrine, not a knob** — no
  configuration can waive it: an orchestrated run requires the adopted
  orchestration task, unconditionally (`roles/strategist.md`).
