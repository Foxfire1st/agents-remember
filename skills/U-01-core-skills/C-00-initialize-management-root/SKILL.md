---
name: C-00-initialize-management-root
description: "Initialize the Agents Remember management folder for a fresh clone or incomplete setup. Defaults to a repo-local internal ar-management folder for the target repo; use shared scaffolding only when the developer explicitly asks for it."
---

# C-00 Initialize Management Root

Create the minimal `ar-management/` scaffold expected by `agents-remember-md/AGENTS.md`.

This skill is for first-run setup and repair of missing management infrastructure. By default it creates only the target repository's internal `ar-management/` folder. It does not create repo onboarding files under `onboarding/`; use `C-03-repo-bootstrap` after this scaffold exists.

## Inputs

- `target_repo`: path to the repository being initialized. Default to the repository the developer asked to work on.
- `topology`: `internal` by default. Use `shared` only when the developer explicitly asks for shared scaffolding.
- `agents_repo`: path to the `agents-remember-md` checkout. Needed only for explicit shared scaffolding that resolves `.env` or `.env.example`.
- `management_root`: optional override for explicit shared scaffolding. Do not use it for default internal setup unless the developer explicitly provides a path.
- `mode`: `create-missing` by default. Use `repair` only when the user explicitly asks to fix existing scaffold files.

## Safety Rules

1. Never overwrite an existing management file without explicit user approval.
2. Create missing directories and files only.
3. Keep starter files generic; do not invent project-specific tools, docs, sources, or onboarding.
4. If the resolved management root points outside the intended workspace, state the resolved absolute path before writing.
5. Default internal scaffolding must not create a shared root.
6. If `.env` is absent, do not create it unless the user explicitly asks for shared configuration.

## Procedure

### 1. Resolve The Root

Default internal scaffolding:

1. Resolve `target_repo`.
2. Set the management root to `<target_repo>/ar-management`.
3. Do not resolve or create a shared `AR_MANAGEMENT_ROOT`.

Explicit shared scaffolding:

1. Use `management_root` when the developer provided one.
2. Otherwise read `<agents_repo>/.env` if it exists; if it is absent, read `<agents_repo>/.env.example`.
3. Parse `AR_MANAGEMENT_ROOT=<path>`.
4. Resolve relative paths from the file that declared the value.
5. If neither file exists or no value is declared, use `../ar-management` relative to `<agents_repo>` and state that fallback.

### 2. Inspect Existing State

Check for these paths under the resolved root:

```text
system/settings.md
system/sources.md
system/tools.md
onboarding/
tasks/
docs/
notes/
```

Report which are present and which are missing. If everything exists, stop with a clean summary.

### 3. Create Missing Directories

Create only missing directories:

```text
<resolved-root>/
  system/
  onboarding/
  tasks/
  docs/
  notes/
```

`notes/` is optional scratch space, but creating it keeps the common local layout consistent.

### 4. Create Missing Starter Files

Create only files that do not already exist.

#### `system/settings.md`

````md
# Settings

This management root stores local durable context for Agents Remember.

## Topology

```yaml
management:
  topology: internal

onboarding:
  storage:
    mode: repo-sidecar
  pathRules:
    include:
      paths:
        - README.md
        - docs/**
        - src/**
      fileTypes:
        - .md
        - .py
        - .ts
        - .tsx
    exclude:
      paths:
        - vendor/**
        - node_modules/**
        - dist/**
        - build/**
      fileTypes:
        - .png
        - .jpg
        - .zip

crossRepo:
  allow: []
```
````

`onboarding.storage` decides where eligible onboarding artifacts live. `onboarding.pathRules` decides which source paths and file types are eligible for onboarding.

For explicit shared scaffolding, use the same file path under the shared root but write `management.topology: shared` and keep `pathRules` present:

```yaml
management:
  topology: shared

onboarding:
  storage:
    layout: shared-root
  pathRules:
    - path: my-app
      include:
        paths:
          - README.md
          - docs/**
          - src/**
        fileTypes:
          - .md
          - .py
          - .ts
          - .tsx
      exclude:
        paths:
          - vendor/**
          - node_modules/**
          - dist/**
          - build/**
        fileTypes:
          - .png
          - .jpg
          - .zip
```

Replace `my-app` with the repository name for each shared-managed repository. Add one scoped rule per repo when shared repositories need different eligible paths or file types.

## Scaffold

| Layer      | Location             | Purpose                                                     |
| ---------- | -------------------- | ----------------------------------------------------------- |
| settings   | `system/settings.md` | Topology, storage, path eligibility, and scaffold notes     |
| sources    | `system/sources.md`  | External and domain documentation registry                  |
| tools      | `system/tools.md`    | Repo-specific commands, checks, and local tool notes        |
| onboarding | `onboarding/`        | Durable repo and file-level code commentary                 |
| tasks      | `tasks/`             | Current task plans, decision logs, and implementation notes |
| docs       | `docs/`              | Local domain docs, mirrors, and reference material          |
| notes      | `notes/`             | Scratch observations that are not durable onboarding yet    |

````

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
````

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
- resolved management root
- directories created
- files created
- files left untouched
- next suggested skill, usually `C-03-repo-bootstrap` to create or refresh onboarding under `onboarding/`

## Common Outcomes

### Fresh Clone

Expected result: create the full scaffold and starter files, then tell the user the management root is ready for repo bootstrap.

### Partial Scaffold

Expected result: create only missing files. Preserve existing `docs/`, `tasks/`, and onboarding content.

### Existing Complete Scaffold

Expected result: make no changes and report that the management root is already initialized.
