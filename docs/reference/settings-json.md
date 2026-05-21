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
        "mode": "workspace",
        "enabled": true,
        "workspace": "agents-remember-memory",
        "mirrorRoots": true,
        "roots": [
          {
            "projectId": "<external-memory-id>",
            "path": "<coordination_root>/memory-repos/<memory-repo>"
          },
          {
            "projectId": "<internal-memory-id>",
            "path": "/absolute/path/to/<repo-directory>/ar-memory"
          }
        ],
        "runtimeRoot": "<coordination_root>/providers/grepai",
        "requirementsFile": "<coordination_root>/providers/requirements/grepai.txt",
        "stateFile": "<runtimeRoot>/state/provider-state.json",
        "embedder": {
          "provider": "ollama",
          "model": "nomic-embed-text",
          "endpoint": "http://localhost:11434",
          "dimensions": 768
        },
        "backend": {
          "id": "grepai-postgres",
          "type": "postgres",
          "mode": "docker",
          "image": "pgvector/pgvector:pg16",
          "imageLockFile": "<coordination_root>/providers/requirements/grepai-postgres-docker.lock",
          "runtimeRoot": "<coordination_root>/provider-data/grepai/postgres",
          "dataRoot": "<backendRuntimeRoot>/data",
          "containerName": "ar-grepai-postgres",
          "postgres": {
            "user": "grepai",
            "password": "grepai",
            "database": "grepai"
          },
          "ports": {
            "postgres": {
              "bindHost": "127.0.0.1",
              "hostPort": "auto",
              "containerPort": 5432
            }
          }
        },
        "watch": {
          "mode": "background",
          "workspace": "<workspace>",
          "logDir": "<runtimeRoot>/logs"
        },
        "freshness": {
          "refreshAfter": ["C-03", "C-05"]
        }
      },
      "codegraphcontext-code": {
        "type": "relationship",
        "scope": "code",
        "enabled": true,
        "roots": [
          {
            "repoId": "<repo-id>",
            "path": "/absolute/path/to/<repo-directory>"
          },
          {
            "repoId": "<second-repo-id>",
            "path": "/absolute/path/to/<second-repo-directory>",
            "cgcignorePatterns": [
              "vendor/generated-sdk/"
            ]
          }
        ],
        "runtimeRoot": "<coordination_root>/providers/codegraphcontext",
        "instanceRootTemplate": "<runtimeRoot>/<repoId>",
        "venvRoot": "<coordination_root>/providers/_venvs/codegraphcontext",
        "requirementsFile": "<coordination_root>/providers/requirements/codegraphcontext.txt",
        "patchesRoot": "<coordination_root>/providers/patches/codegraphcontext",
        "stateFileTemplate": "<instanceRoot>/provider-state.json",
        "backend": {
          "id": "codegraphcontext-falkordb",
          "type": "falkordb-remote",
          "mode": "docker",
          "image": "falkordb/falkordb:v4.18.7",
          "imageLockFile": "<coordination_root>/providers/requirements/codegraphcontext-falkordb-docker.lock",
          "runtimeRoot": "<coordination_root>/provider-data/codegraphcontext/falkordb",
          "dataRoot": "<backendRuntimeRoot>/data",
          "containerName": "ar-cgc-falkordb",
          "ports": {
            "falkordb": {
              "bindHost": "127.0.0.1",
              "hostPort": "auto",
              "containerPort": 6379
            },
            "browser": {
              "bindHost": "127.0.0.1",
              "hostPort": "auto",
              "containerPort": 3000
            }
          }
        },
        "processEnvTemplate": {
          "CGC_RUNTIME_DB_TYPE": "falkordb-remote",
          "DEFAULT_DATABASE": "falkordb-remote",
          "HOME": "<instanceRoot>/.codegraphcontext/run/home",
          "USERPROFILE": "<instanceRoot>/.codegraphcontext/run/home",
          "APPDATA": "<instanceRoot>/.codegraphcontext/run/appdata",
          "LOCALAPPDATA": "<instanceRoot>/.codegraphcontext/run/localappdata",
          "FALKORDB_HOST": "<backend.ports.falkordb.bindHost>",
          "FALKORDB_PORT": "<backend.ports.falkordb.hostPort>",
          "FALKORDB_GRAPH_NAME": "cgc_<repoGraphId>",
          "LOG_FILE_PATH": "<instanceRoot>/.codegraphcontext/logs/cgc.log",
          "DEBUG_LOG_PATH": "<instanceRoot>/.codegraphcontext/logs/debug.log",
          "ENABLE_AUTO_WATCH": "false",
          "PYTHONIOENCODING": "utf-8",
          "PYTHONUTF8": "1"
        },
        "watch": {
          "mode": "managed-foreground",
          "cwdTemplate": "<instanceRoot>",
          "logFileTemplate": "<instanceRoot>/.codegraphcontext/logs/watch.log",
          "requiredBeforeSourceEdits": true,
          "requiredBeforeBranchSwitch": true
        },
        "freshness": {
          "refreshAfter": ["C-09-closeout"],
          "branchSwitch": "watcher-first",
          "hardRefresh": "explicit-only"
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

`contextProviders.providers` is a map of provider settings. A workspace can
configure one GrepAI workspace provider over multiple external or repo-internal
memory roots and one CodeGraphContext code provider whose `roots` array expands
into multiple repository instances.

`contextProviders.providers.<id>.type` describes the retrieval substrate. Use
`semantic` for concept-known/location-unknown memory discovery and
`relationship` for anchor-known/relationship-unknown code discovery.

`contextProviders.providers.<id>.roots` lists the absolute or template-expanded
roots the provider indexes. For GrepAI, each root should be an object with a
stable `projectId` and a `path`, allowing external memory repos and
repo-internal `ar-memory/` roots to share one PostgreSQL-backed workspace.
Managed GrepAI defaults `mirrorRoots` to true so lifecycle commands copy those
memory roots into provider-owned `providers/grepai/index-roots/` before
launching GrepAI; this keeps GrepAI's per-project `.grepai/` config and symbol
files out of durable memory roots. For
CodeGraphContext, each root should be an object with a stable `repoId` and a
`path`; the lifecycle manager expands those entries into per-repository runtime
instances. A root may also define `cgcignorePatterns` for repo-specific
generated or vendored paths that should be written to that instance's managed
`.cgcignore`.

`contextProviders.providers.<id>.runtimeRoot` is where Agents Remember-owned
provider-family state should live. Keep runtime roots under
`ar-coordination/providers/`. GrepAI writes workspace config, logs, state,
cache, and mirrored index roots under `providers/grepai/`; CodeGraphContext uses
`runtimeRoot` as the shared provider root and derives per-repository instance
roots from `instanceRootTemplate`.
The `providers/` tree is disposable install/runtime scaffolding and may be
deleted and recreated during reinstall; durable provider databases such as
GrepAI PostgreSQL and CGC FalkorDB belong under `provider-data/`, not under
`providers/`.
After recreating that scaffold, the runtime installer runs dependency install
commands for enabled providers from this live settings file unless
`--skip-provider-deps` is passed.
Installer, benchmark preparation, and C-09 worktree preparation all use
`scripts/provider-setup.py` as the shared orchestration entrypoint. Benchmark
and worktree preparation should seed CGC with bundle export/import plus path
rewrite when a source coordinator is available, instead of each caller
implementing its own provider install, backend, and refresh sequence.
Lifecycle commands default to `<coordination_root>/system/settings.json`; use
`--from-settings` only as a debug override for an alternate settings file.
Use `watchers start`, `watchers status`, and `watchers shutdown-all` for the
normal coordinator-level watcher workflow across every enabled provider.
CGC-specific commands can still fan out over all configured CGC roots:
`cgc start` and `cgc start-all` start every configured repo watcher; add
`--repo-id <repoId>` to `cgc start` only when starting one repo. `cgc stop`,
`cgc stop-all`, and `cgc shutdown-all` stop every configured repo watcher; add
`--repo-id <repoId>` to `cgc stop` only when stopping one repo.

`contextProviders.providers.<id>.instanceRootTemplate` maps a CodeGraphContext
root entry to its per-repository runtime root. The lifecycle manager expands it
with values such as `repoId` and `runtimeRoot`.

`contextProviders.providers.<id>.venvRoot` points to the provider-type virtual
environment. Prefer one venv per provider type, such as
`providers/_venvs/codegraphcontext`, instead of one global provider venv.

`contextProviders.providers.<id>.requirementsFile` points to the pinned
dependency file used to install or repair the provider environment.

`contextProviders.providers.<id>.patchesRoot` points to version-checked patches
that the lifecycle manager must apply and verify before using the provider.

`contextProviders.providers.<id>.stateFileTemplate` maps each expanded provider
instance to its lifecycle state file. State should include provider version,
requirements hash, applied patch identifiers, last doctor result, last refresh,
process metadata, resolved backend ports, browser URL, and containment status.

`contextProviders.providers.<id>.backend` describes a shared managed backend.
For CodeGraphContext, the managed backend is one lifecycle-owned FalkorDB Docker
DBMS per coordination root. The backend configuration controls the pinned image,
image lock file, persistent data root, container name, loopback-only port
bindings, and browser UI binding. Resolved host ports and browser URLs are
runtime state, not hand-maintained settings.

`contextProviders.providers.<id>.processEnvTemplate` lists process environment
variables the lifecycle manager applies when running provider commands. It is a
template, not a persisted `.env` file. For CodeGraphContext, expand it per root
with `instanceRoot`, resolved FalkorDB host/port, and a repo-scoped graph name.
Use `repoGraphId` when a graph-name-safe id is needed instead of the path-facing
`repoId`.
On Windows, the lifecycle manager should also route `HOME`, `USERPROFILE`,
`APPDATA`, and `LOCALAPPDATA` under the instance root so provider config and
logs stay out of user-global locations. Do not set `HOME` or `USERPROFILE`
directly to `<instanceRoot>` for CGC, because CGC would then treat
`<instanceRoot>/.codegraphcontext` as its global config directory instead of the
local repo context.

For CodeGraphContext, keep CGC configuration, ignore rules, logs, run files, and
process state under `<instanceRoot>/.codegraphcontext/`. CGC versions that
create `.cgcignore` in the indexed source repo must be patched, fixed upstream,
or explicitly accepted as a repo-local config choice before managed use.
The managed `.cgcignore` should also inherit the indexed repository's top-level
`.gitignore` patterns so ignored folders such as `samples/`, `tools/`, or
temporary working directories are not parsed by CGC.
When CGC runs on Windows, a hard refresh must also delete existing repository
children using both slash and backslash path prefixes before rebuilding. Without
that version-checked patch, `cgc index --force` can leave stale File nodes for
previously indexed ignored folders even though the new discovery pass excludes
them.

Some CGC variables are process-only runtime controls. The lifecycle manager may
apply them from `processEnvTemplate`, but should not write them into
`<instanceRoot>/.codegraphcontext/.env` when the installed CGC version rejects
them as persisted config. Persist only keys that the installed CGC version
accepts in its own config file.

`contextProviders.providers.<id>.watch` describes how the provider should be
kept fresh. `background` means the provider has its own background watcher.
`managed-foreground` means Agents Remember should supervise the long-running
process.

`contextProviders.providers.<id>.freshness.refreshAfter` lists workflow events
after which the provider may need a refresh or health check.

`contextProviders.providers.<id>.freshness.branchSwitch` describes branch-switch
handling. `watcher-first` means the watcher should be started or verified before
branch changes when CodeGraphContext coverage matters.

Provider reinstall/update is non-destructive by default. Reinstall may recreate
scaffolding, virtual environments, copied requirements, patches, containers, and
missing runtime directories, but deleting FalkorDB data, graph namespaces, or
repository indexes requires an explicit destructive lifecycle command.

`contextProviders.policy.discoveryOnly` means provider output is candidate
routing evidence only.

`contextProviders.policy.sourceProofRequired` means source files, verified
onboarding, drift checks, branch validity, and approved memory promotion remain
the proof layer.
