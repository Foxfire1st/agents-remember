# settings.json Reference

`system/settings.json` is the machine-readable memory settings file. `system/settings.md` remains the human and agent prose guidance file.

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

## Coordinator Context Providers Example

Coordinator settings may also define optional discovery providers. These
providers help agents choose routes; they do not replace source files,
verified onboarding, drift checks, branch validity, or approval-gated memory
promotion.

```json
{
  "version": 1,
  "coordination": {
    "tasksDir": "tasks",
    "worktreesDir": "worktrees",
    "notesDir": "notes",
    "memoryReposDir": "memory-repos"
  },
  "memoryRepos": {
    "defaultTopology": "external",
    "repositories": []
  },
  "contextProviders": {
    "enabled": true,
    "providers": {
      "grepai-memory": {
        "type": "semantic",
        "scope": "memory",
        "enabled": true,
        "roots": ["<coordination_root>/memory-repos"],
        "runtimeRoot": "<coordination_root>/providers/grepai/memory-repos",
        "watch": {
          "mode": "background",
          "cwd": "<coordination_root>/memory-repos",
          "logDir": "<runtimeRoot>/logs"
        },
        "freshness": {
          "refreshAfter": ["C-03", "C-05"]
        }
      },
      "codegraphcontext-my-app": {
        "type": "relationship",
        "scope": "code",
        "enabled": true,
        "roots": ["/absolute/path/to/my-app"],
        "runtimeRoot": "<coordination_root>/providers/codegraphcontext/my-app",
        "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
        "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
        "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
        "stateFile": "<runtimeRoot>/provider-state.json",
        "env": {
          "CGC_RUNTIME_DB_TYPE": "kuzudb",
          "DEFAULT_DATABASE": "kuzudb",
          "HOME": "<runtimeRoot>",
          "KUZUDB_PATH": "<runtimeRoot>/.codegraphcontext/db/kuzu",
          "CGC_RUNTIME_DB_PATH": "<runtimeRoot>/.codegraphcontext/db/kuzu",
          "FALKORDB_PATH": "<runtimeRoot>/.codegraphcontext/db/falkordb.db",
          "FALKORDB_SOCKET_PATH": "<runtimeRoot>/.codegraphcontext/run/falkordb.sock",
          "LOG_FILE_PATH": "<runtimeRoot>/.codegraphcontext/logs/cgc.log",
          "DEBUG_LOG_PATH": "<runtimeRoot>/.codegraphcontext/logs/debug.log",
          "ENABLE_AUTO_WATCH": "false"
        },
        "watch": {
          "mode": "managed-foreground",
          "cwd": "<runtimeRoot>",
          "logFile": "<runtimeRoot>/.codegraphcontext/logs/watch.log"
        },
        "freshness": {
          "refreshAfter": ["C-09-closeout"]
        }
      }
    },
    "policy": {
      "discoveryOnly": true,
      "sourceProofRequired": true,
      "maxSemanticQueriesPerPacket": 1,
      "maxGraphQueriesPerPacket": 2,
      "transportPolicy": {
        "default": "cli",
        "mcp": "optional",
        "tokenEconomy": "budget-returned-evidence"
      }
    }
  }
}
```

## Fields

`version` identifies the settings shape.

`onboarding.storage.mode` selects storage for eligible onboarding. Current public modes are `repo-sidecar`, `memory-repo`, and explicit inline mode where supported by repository settings and file type.

`onboarding.pathRules` controls which paths and file types are eligible. It does not switch storage by path.

`crossRepo.allow` controls branch-gated adjacent repository context. Keep it empty unless the memory layer explicitly allows a cross-repo relationship.

`contextProviders.enabled` turns optional discovery providers on for the
coordinator. If absent or false, agents should use onboarding-only routing.

`contextProviders.providers` is a map of provider instances. A workspace can
configure one GrepAI provider over the memory repos root and multiple
CodeGraphContext providers, one per code repository.

`contextProviders.providers.<id>.type` describes the retrieval substrate. Use
`semantic` for concept-known/location-unknown memory discovery and
`relationship` for anchor-known/relationship-unknown code discovery.

`contextProviders.providers.<id>.roots` lists the absolute or template-expanded
roots the provider indexes. Code providers should be scoped to one code repo per
provider instance.

`contextProviders.providers.<id>.runtimeRoot` is where Agents Remember-owned
provider logs, process metadata, sockets, and local databases should live. Keep
runtime roots under `ar-coordination/providers/`.

`contextProviders.providers.<id>.venvRoot` points to the provider-type virtual
environment. Prefer one venv per provider type, such as
`providers/_venvs/codegraphcontext`, instead of one global provider venv.

`contextProviders.providers.<id>.requirementsFile` points to the pinned
dependency file used to install or repair the provider environment.

`contextProviders.providers.<id>.patchesRoot` points to version-checked patches
that the lifecycle manager must apply and verify before using the provider.

`contextProviders.providers.<id>.stateFile` points to provider lifecycle state,
including provider version, requirements hash, applied patch identifiers, last
doctor result, last refresh, process metadata, and containment status.

`contextProviders.providers.<id>.env` lists environment variables the lifecycle
manager should apply when running provider commands. Prefer explicit per-provider
paths over user-global provider configuration.

For CodeGraphContext, set `HOME` to the provider runtime root and keep CGC
configuration, ignore rules, KuzuDB data, logs, and run files under
`<runtimeRoot>/.codegraphcontext/`. CGC versions that create `.cgcignore` in the
indexed source repo must be patched, fixed upstream, or explicitly accepted as a
repo-local config choice before managed use.

Some CGC variables are process-only runtime controls. `CGC_RUNTIME_DB_TYPE`,
`KUZUDB_PATH`, and `CGC_RUNTIME_DB_PATH` may be present in
`contextProviders.providers.<id>.env`, but the lifecycle manager should not
persist them into `<runtimeRoot>/.codegraphcontext/.env` for CGC v0.4.10 because
`cgc doctor` treats those persisted keys as invalid config. Keep persisted
`.env` content to CGC-recognized keys such as `DEFAULT_DATABASE`,
`FALKORDB_PATH`, `FALKORDB_SOCKET_PATH`, `LOG_FILE_PATH`, `DEBUG_LOG_PATH`, and
`ENABLE_AUTO_WATCH`.

`contextProviders.providers.<id>.watch` describes how the provider should be
kept fresh. `background` means the provider has its own background watcher.
`managed-foreground` means Agents Remember should supervise the long-running
process.

`contextProviders.providers.<id>.freshness.refreshAfter` lists workflow events
after which the provider may need a refresh or health check.

`contextProviders.policy.discoveryOnly` means provider output is candidate
routing evidence only.

`contextProviders.policy.sourceProofRequired` means source files, verified
onboarding, drift checks, branch validity, and approved memory promotion remain
the proof layer.
