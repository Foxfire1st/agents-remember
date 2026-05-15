---
name: c-00-initialize-memory-repo
description: "Initialize or repair the Agents Remember memory root for a target repository. Defaults to repo-local internal memory; creates an external memory repo only when the developer explicitly asks for external memory."
---

# C-00 Initialize Memory Repo

Create the minimal memory root required before onboarding or task workflows can use Agents Remember for a target repository.

This skill initializes durable repo memory. It does not install the coordinator runtime, create harness skill symlinks, create task worktrees, or generate onboarding content. Use `installer/install-runtime.py` for coordinator runtime installation, `runtime/scripts/install-skills.sh` for harness exposure, and `C-03-repo-bootstrap` after this scaffold exists when the developer wants onboarding content generated.

Use `C-08-ar-coordination-context-resolver` to inspect an existing repository's active context. This skill creates or repairs missing memory scaffolding; it does not replace C-08 as the normal resolver.

## Inputs

- `code_repository_root`: root directory of the code repository whose memory is being initialized. Default to the repository the developer asked to work on.
- `topology`: `internal` by default. Use `external` only when the developer explicitly asks for an external memory repo.
- `coordination_root`: required for external-memory initialization unless the installed runtime root is already obvious from the current skill location or the developer provided a path.
- `mode`: `create-missing` by default. Use `repair` only when the developer explicitly asks to fix existing memory scaffold files.

## Safety Rules

1. Never overwrite an existing memory file without explicit user approval.
2. Create missing directories and files only.
3. Keep starter files generic; do not invent project-specific tools, docs, sources, or onboarding.
4. If the resolved memory root or coordination root points outside the intended workspace, state the resolved absolute path before writing.
5. Default internal scaffolding must not create or select an external memory repo.
6. External-memory setup must not install coordinator runtime files; if the coordinator runtime is missing, tell the developer to run `installer/install-runtime.py` first.
7. Do not create onboarding content. An empty `onboarding/` directory is enough for C-08 to resolve the memory root; C-03 owns onboarding generation.

## Procedure

### 1. Resolve The Memory Root

Default internal memory:

1. Resolve `code_repository_root`.
2. Set `memory_root` to `<code_repository_root>/ar-memory`.
3. Do not resolve or create an external memory repo.

Explicit external memory:

1. Resolve `code_repository_root`.
2. Resolve `coordination_root` from the developer-provided path or the installed runtime root.
3. Verify the coordinator runtime exists by checking for:

```text
<coordination-root>/AGENTS.md
<coordination-root>/skills/
<coordination-root>/scripts/
<coordination-root>/tasks/
<coordination-root>/memory-repos/
```

4. If the runtime is missing, stop and ask the developer to run:

```bash
python3 agents-remember-md/installer/install-runtime.py <coordination-root>
```

5. Set `memory_root` to `<coordination-root>/memory-repos/ar-<code-repository-name>`.
6. Initialize the external memory root as a Git repository when it is newly created or when it exists without `.git`.

### 2. Inspect Existing State

Check for these paths under the resolved memory root:

```text
system/settings.md
system/settings.json
system/sources.md
system/tools.md
onboarding/
docs/
```

For external memory, also check:

```text
.git/
```

Report which are present and which are missing. If everything exists, stop with a clean summary.

### 3. Create Missing Directories

Create only missing directories:

```text
<memory-root>/
  system/
  onboarding/
  docs/
```

For external memory, ensure `<coordination-root>/memory-repos/` exists before creating the per-repo memory root. Do not create or modify coordinator runtime directories such as `skills/`, `scripts/`, `tasks/`, `worktrees/`, `notes/`, or `temp/`.

When creating an external memory Git repository, add `docs/.gitkeep` if `docs/` would otherwise be empty so the scaffold can be committed.

### 4. Create Missing Starter Files

Create only files that do not already exist.

#### `system/settings.md`

```md
# Settings

This memory root stores durable context for Agents Remember.

Use this Markdown file for human and agent instructions, scaffold notes, and operational context. Machine-readable storage, path-rule, and cross-repo settings live in `system/settings.json`.

Do not duplicate active `pathRules` here as the authoritative machine source when `system/settings.json` exists.

## Scaffold

| Layer         | Location               | Purpose                                                     |
| ------------- | ---------------------- | ----------------------------------------------------------- |
| instructions  | `system/settings.md`   | Human and agent guidance, path contract, and scaffold notes |
| path settings | `system/settings.json` | Machine-readable storage, pathRules, and cross-repo data    |
| sources       | `system/sources.md`    | External and domain documentation registry                  |
| tools         | `system/tools.md`      | Repo-specific commands, checks, and local tool notes        |
| onboarding    | `onboarding/`          | Durable repo and file-level code commentary                 |
| docs          | `docs/`                | Local domain docs, mirrors, and reference material          |
```

#### `system/settings.json`

For internal memory:

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
        "fileTypes": [".png", ".jpg", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": []
  }
}
```

For external memory:

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
        "fileTypes": [".png", ".jpg", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": []
  }
}
```

`onboarding.storage` decides where eligible onboarding artifacts live. `onboarding.pathRules` decides which source paths and file types are eligible for onboarding. Cross-repo policy belongs in the memory layer, not in untracked coordinator runtime files.

#### `system/sources.md`

```md
# Sources

## Domain Documentation

No domain documentation configured yet.

Add project-specific docs, local mirrors, API references, and canonical source links here before creating durable onboarding that depends on external behavior.

## External References

No external references configured yet.

## Notes

- Prefer local mirrors for reading when available.
- Link onboarding `Docs References` rows to canonical source URLs when a canonical online reference exists.
- If no relevant domain documentation exists for a task, record what was checked instead of implying the search space was complete.
```

#### `system/tools.md`

```md
# Tools

## Checks

No repo-specific checks configured yet.

Add test, lint, typecheck, build, and smoke-check commands for each onboarded repo.

## Commands

No repo-specific commands configured yet.

## Runtime Notes

Record environment setup, local service assumptions, MCP notes, and command caveats here.
```

### 5. Report Result

Summarize:

- resolved topology
- resolved memory root
- resolved coordination root when external memory is used
- whether an external Git repository was initialized
- directories created
- files created
- files left untouched
- next suggested skill, usually `C-03-repo-bootstrap` when the developer wants onboarding content under the resolved onboarding root

## Common Outcomes

### Fresh Internal Memory

Expected result: create `<code-repository-root>/ar-memory/` with `system/`, `onboarding/`, and `docs/`, then tell the developer the repo is ready for optional repo bootstrap.

### Fresh External Memory

Expected result: create `<coordination-root>/memory-repos/ar-<repo-name>/`, initialize Git if needed, add `system/`, `onboarding/`, and `docs/`, then tell the developer the repo is ready for optional repo bootstrap.

### Partial Memory Scaffold

Expected result: create only missing files or directories. Preserve existing `docs/`, `system/`, and onboarding content.

### Existing Complete Memory Scaffold

Expected result: make no changes and report that the memory root is already initialized.
