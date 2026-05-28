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
    "agents-remember-md": {
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
    "providerSeconds": 120
  }
}
```

## Memory Fields

`version` identifies the settings shape.

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

`harnessSkillRoot` is optional. By default, when the MCP settings file lives
under `<registration-root>/mcp/`, `skills_install` copies packaged skills into
`<registration-root>/skills/`. Set `harnessSkillRoot` only for non-standard
harness layouts where the registration folder and skill folder are not siblings.
When neither inference nor the override is available, the MCP server can still
run, but `skills_install` refuses to install because the target root is not
configured.

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

`timeoutCaps` contains positive integer timeout caps for MCP operations.
