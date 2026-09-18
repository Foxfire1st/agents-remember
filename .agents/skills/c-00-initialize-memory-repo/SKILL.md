---
name: c-00-initialize-memory-repo
description: "Initialize or repair the Agents Remember memory root for a target repository. Creates the external memory repo at `<coordination-root>/memory-repos/ar-<code-repository-name>`; repo-local internal memory was removed and is refused."
---

# c-00-initialize-memory-repo Initialize Memory Repo

Create the minimal memory root required before onboarding or task workflows can use Agents Remember for a target repository.

This skill initializes durable repo memory through MCP `memory_init`. It does
not install the coordinator runtime, expose harness skills, create task
worktrees, or generate onboarding content. Request MCP `runtime_install` only
when the coordinator runtime scaffold is missing or stale. Harness skill
exposure for first-run setup comes from the copied starter package, not from
this skill. Use `c-03-repo-bootstrap` after the memory scaffold exists when the
developer wants onboarding content generated.

Use `c-08-ar-coordination-context-resolver` to inspect an existing repository's active context. This skill creates or repairs missing memory scaffolding; it does not replace the `c-08-ar-coordination-context-resolver` skill as the normal resolver.

## Inputs

- `repo_id`: configured MCP repository ID whose memory is being initialized. This is the normal installed runtime input.
- `code_repository_root`: root directory of the code repository whose memory is being initialized. Use this only for conceptual review or source-debugging; normal installed runtime calls identify the configured repository by `repo_id`.
- `topology`: `external`. It is the only supported topology; repo-local internal memory was removed from the product.
- `coordination_root`: supplied by MCP settings for normal installed runtime calls.
- `mode`: `create-missing` by default. Use `repair` only when the developer explicitly asks to fix existing memory scaffold files.
- `initial_branch`: which code branch the memory repo is founded on. Optional; it defaults to the
  code repository's currently checked-out branch, and the call refuses rather than inventing a
  name when neither is available.

## MCP Tools

Use the Agents Remember MCP setup tools as the normal installed runtime entry
points:

> **Preview first.** These setup tools now **apply by default** (`dry_run`
> defaults to `false`). For any effectful run, call once with `dry_run=true` to
> inspect the plan, confirm it, then run the real apply (omit `dry_run`).

```text
memory_init(repo_id="<repo-id>", dry_run=true, initialize_git=true)   # preview
memory_init(repo_id="<repo-id>", initialize_git=true, initial_branch="<foundation branch>")   # apply
runtime_install(include_benchmarks=false, install_provider_deps=false)
```

Use `memory_init` for creating or repairing the configured memory root for a
repo. Use `runtime_install` only when the coordinator runtime scaffold itself is
missing or stale. Do not use `skills_install` as part of package-based first-run
setup; copied starter packages already carry the harness skills. `skills_install`
remains available only for manual maintenance or non-package installs. The skill
tree is instruction-only; installed and development workflows use the
MCP/package route.

## Safety Rules

1. Never overwrite an existing memory file without explicit user approval.
2. Create missing directories and files only.
3. Keep starter files generic; do not invent project-specific tools, docs, sources, or onboarding.
4. If the resolved memory root or coordination root points outside the intended workspace, state the resolved absolute path before writing.
5. Never create a memory root inside the code repository. A repo-local `ar-memory/` root was removed from the product and is refused by name; scaffolding must not recreate it.
6. Memory setup must not install coordinator runtime files; if the coordinator runtime is missing, tell the developer to request MCP `runtime_install` first.
7. Do not create onboarding content. An empty `onboarding/` directory is enough for the `c-08-ar-coordination-context-resolver` skill to resolve the memory root; the `c-03-repo-bootstrap` skill owns onboarding generation.

## Procedure

### 0. Ask The Foundation-Branch Question, And Say What This Is

Before creating anything, ask the one question that changes what the memory repo is:

> **Which code branch should the memory be built on?**

Default it to the branch the code repository currently has checked out. Give the developer the
absolute path their memory is about to appear at — there is exactly one, and it is
`<coordination-root>/memory-repos/ar-<their-repository-name>` — rather than describing it as
"beside the coordination root", because they are about to get a second Git repository on disk and
are entitled to know where. Pass the answer to `memory_init` as the `initial_branch` argument: the
memory repository's own first branch is given that name, and the name is recorded so later steps
compare against it.

Then give the short version to a developer who skipped the README, in plain language and with no
undefined product vocabulary, **before** asking them to confirm anything. Four points:

1. **What this memory is.** A second Git repository, separate from the code and created for you at
   that absolute path. It holds what agents have worked out about *this* code repository: written
   explanations of how the code works, and the decisions behind it. It is not a copy of the code.
   It sits outside the code repository on purpose, so it can be versioned and shared on its own.
2. **What this setup creates.** Inside that new repository: a directory named `system` for its own
   settings and notes, a directory named `onboarding` for the written explanations, and a directory
   named `docs` for local reference material. "Onboarding" here is just the name of the explanations
   directory — it is not a setup procedure, and nothing in it is generated by scanning your code
   unless you later ask for that.
3. **What the ledger is.** One file, named `memory.md`, created at the top of the memory repository.
   It is a short table pairing a commit in *your* code repository with the memory content that was
   written about that commit, so a later reader can tell which memory describes which state of the
   code. It is produced automatically, never hand-written, and it does not exist until the first
   snapshot of memory content has been committed — that commit is called the memory baseline, and
   making it is the "adopt" step you will be asked about.
4. **Why the branch choice matters.** The memory is created and checked against the state of the
   branch you name; that name is recorded, and later steps compare themselves to it. Choosing a
   different branch afterwards means moving the memory over deliberately — a separate procedure,
   not a setting you flip — so name the branch your work actually lives on.

Use the developer's own words for their repository and their team. Before sending this, read it once
as somebody who has never seen the product and strike every term you cannot define in the same
sentence: if a word only makes sense to someone who already read the README, it is not doing its
job.

### 1. Resolve The Memory Root

`external` is the only supported topology, so there is one procedure:

1. Resolve `code_repository_root`.
2. Resolve `coordination_root` from the developer-provided path or the installed runtime root.
3. Verify the coordinator runtime exists by checking for:

```text
<coordination-root>/AGENTS.md
<coordination-root>/skills/
<coordination-root>/tasks/
<coordination-root>/memory-repos/
```

4. If the runtime is missing, stop and ask the developer to request (preview
   first with `runtime_install(dry_run=true)`):

```text
runtime_install()
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
.git/
```

Also check the code repository for a repo-local `ar-memory/` directory. That layout was
removed from the product: report it to the developer with its exact path and the route below
instead of reading, writing, or migrating it yourself.

**Route out of the removed layout.** Re-point the repository to the external memory root
(`<coordination-root>/memory-repos/ar-<code-repository-name>`) and record `memory_mode: external`
on its worktree contracts, or re-initialize the memory root with this skill. Nothing is migrated
automatically, and no existing memory root is rewritten or deleted.

Report which are present and which are missing. If everything exists, stop with a clean summary.

### 3. Create Missing Directories

Create only missing directories:

```text
<memory-root>/
  system/
  onboarding/
  docs/
```

Ensure `<coordination-root>/memory-repos/` exists before creating the per-repo memory root. Do not create or modify coordinator runtime directories such as `skills/`, `tasks/`, `worktrees/`, `notes/`, or `temp/`.

When creating the memory Git repository, add `docs/.gitkeep` if `docs/` would otherwise be empty so the scaffold can be committed.

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

- resolved topology (`external`)
- resolved memory root
- resolved coordination root
- whether the memory Git repository was initialized
- any repo-local `ar-memory/` root found and reported, with the route recorded above
- directories created
- files created
- files left untouched
- next suggested skill, usually `c-03-repo-bootstrap` when the developer wants onboarding content under the resolved onboarding root

## Common Outcomes

### Fresh Memory Repo

Expected result: create `<coordination-root>/memory-repos/ar-<repo-name>/`, initialize Git if needed, add `system/`, `onboarding/`, and `docs/`, then tell the developer the repo is ready for optional repo bootstrap.

### Repo-Local `ar-memory/` Found

Expected result: create nothing inside the code repository. Report the exact `ar-memory/` path, state that repo-local internal memory was removed from the product, and give the developer the route: re-point to the external memory root and record `memory_mode: external` on the worktree contracts, or re-initialize with this skill. Do not delete or rewrite anything.

### Partial Memory Scaffold

Expected result: create only missing files or directories. Preserve existing `docs/`, `system/`, and onboarding content.

### Existing Complete Memory Scaffold

Expected result: make no changes and report that the memory root is already initialized.
