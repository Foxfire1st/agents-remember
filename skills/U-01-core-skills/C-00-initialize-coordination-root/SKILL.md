---
name: c-00-initialize-coordination-root
description: "Initialize the Agents Remember memory and coordination folders for a fresh clone or incomplete setup. Defaults to repo-local ar-memory durable memory plus local ar-coordination state; use external-memory scaffolding only when the developer explicitly asks for it."
---

# C-00 Initialize Coordination Root

Create the minimal `ar-memory/` durable-memory scaffold and `ar-coordination/` coordination scaffold expected by `agents-remember-md/AGENTS.md`.

This skill is for first-run setup and repair of missing memory or coordination infrastructure. By default it creates the code repository's internal `ar-memory/` folder and a local coordination `ar-coordination/` folder. It does not create repo onboarding files under `onboarding/`; use `C-03-repo-bootstrap` after this scaffold exists.

Use `C-08-ar-coordination-context-resolver` when an agent needs to inspect an existing repository's active coordination context. This skill creates or repairs scaffold files; it does not replace C-08 as the normal resolver.

## Inputs

- `code_repository_root`: root directory of the code repository being initialized. Default to the repository the developer asked to work on.
- `topology`: `internal` by default. Use `external` only when the developer explicitly asks for external-memory scaffolding.
- `agents_repo`: path to the `agents-remember-md` checkout. Needed only for explicit external-memory scaffolding that resolves `.env` or the built-in `../ar-coordination` default.
- `coordination_root`: optional override for explicit external-memory scaffolding. Do not use it for default internal setup unless the developer explicitly provides a path.
- `mode`: `create-missing` by default. Use `repair` only when the user explicitly asks to fix existing scaffold files.

## Safety Rules

1. Never overwrite an existing coordination file without explicit user approval.
2. Create missing directories and files only.
3. Keep starter files generic; do not invent project-specific tools, docs, sources, or onboarding.
4. If the resolved memory root or coordination root points outside the intended workspace, state the resolved absolute path before writing.
5. Default internal scaffolding must not create or select an external memory repo.
6. If `.env` is absent, do not create it unless the user explicitly asks for external-memory configuration.

## Procedure

### 1. Resolve The Root

Default internal scaffolding:

1. Resolve `code_repository_root`.
2. Set the memory root to `<code_repository_root>/ar-memory`.
3. Set the local coordination root to `<code_repository_root>/ar-coordination` unless the developer explicitly provided another coordination root.
4. Do not resolve or create an external `AR_COORDINATION_ROOT`.

Explicit external-memory scaffolding:

1. Use `coordination_root` when the developer provided one.
2. Otherwise read `<agents_repo>/.env` if it exists; do not read `.env.example` at runtime.
3. Parse `AR_COORDINATION_ROOT=<path>`.
4. Resolve relative paths from the file that declared the value.
5. If `.env` is absent or no value is declared, use `../ar-coordination` relative to `<agents_repo>` and state that default.

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

Check for these paths under the resolved coordination root:

```text
system/
memory-repos/
tasks/
notes/
worktrees/
```

Report which are present and which are missing. If everything exists, stop with a clean summary.

### 3. Create Missing Directories

Create only missing directories:

```text
<memory-root>/
  system/
  onboarding/
  docs/

<coordination-root>/
  system/
  memory-repos/
  tasks/
  notes/
  worktrees/
```

`notes/` is optional scratch space, but creating it keeps the common local layout consistent.

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
        "paths": ["vendor/**", "node_modules/**", "dist/**", "build/**"],
        "fileTypes": [".png", ".jpg", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": []
  }
}
```

`onboarding.storage` decides where eligible onboarding artifacts live. `onboarding.pathRules` decides which source paths and file types are eligible for onboarding.

For explicit external-memory scaffolding, use the same file path under the per-repo memory repo:

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
        "paths": ["vendor/**", "node_modules/**", "dist/**", "build/**"],
        "fileTypes": [".png", ".jpg", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": []
  }
}
```

Place this file in `ar-coordination/memory-repos/ar-<repo-name>/system/settings.json` for external memory repos. The coordinator may have its own local `system/settings.json` for path hints, but cross-repo policy belongs in the committed memory layer.

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
- resolved coordination root
- directories created
- files created
- files left untouched
- next suggested skill, usually `C-03-repo-bootstrap` to create or refresh onboarding under the resolved onboarding root

## Common Outcomes

### Fresh Clone

Expected result: create the full memory and coordination scaffold, then tell the user the repo is ready for repo bootstrap.

### Partial Scaffold

Expected result: create only missing files. Preserve existing `docs/`, `tasks/`, and onboarding content.

### Existing Complete Scaffold

Expected result: make no changes and report that the memory and coordination roots are already initialized.
