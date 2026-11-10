# Use External Memory

External memory is the supported topology: durable memory lives in its own repo per code repository. Repo-local internal memory under `<repo>/ar-memory/` was removed from the product and is refused rather than migrated.

## When External Memory Helps

External memory is useful when:

- code and memory should be reviewed or permissioned separately
- several code repositories share one coordination root
- long-running branches need matching code and memory versions
- the [authorized worktree closeout](../../skills/l-01-agent-lifecycles/operations/closeout.md) should record code-memory ledger mappings

## Layout

```text
projects/
  agents-remember/
  ar-coordination/
    AGENTS.md
    skills/
    memory-repos/
      ar-my-app/
        memory.md
        onboarding/
        docs/
        system/
  my-app/
```

Each selected code repository gets one memory repo:

```text
ar-coordination/memory-repos/ar-<repo-name>/
```

## Initialize

Install the runtime first through the MCP server:

```text
runtime_install()
```

Before ordinary task execution, initialize missing external memory for the selected repository through `memory_init` on `agents-remember-task`. This belongs to setup, including the Bootstrap role's permitted setup procedure; it is not a task-worktree repair.

Preview with `dry_run: true`, naming the configured repository with `repo_id`. Use `initial_branch` for the chosen foundation branch, or let the initializer inherit the code checkout's current branch. If neither supplies a branch, resolve that decision rather than guessing one. Run the agreed setup without dry run, then read its returned memory root, Git result and knowledge-foundation state. Initialization scaffolds memory; it does not author onboarding cards or knowledge records. Existing files remain intact. Follow an actual refusal or returned next action rather than treating an existing unconverted root as initialized.

## Configure

External memory uses memory-repo storage:

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
        "paths": ["node_modules/**", "vendor/**", "dist/**", "build/**", ".env", ".env.*"],
        "fileTypes": [".png", ".zip"]
      }
    }
  },
  "crossRepo": {
    "allow": []
  }
}
```

In a one-repo memory repo, unscoped path rules are fine. In shared coordinator settings, scope rules by repository path when one settings file covers more than one repo.

## Resolve

Product context resolution maps the selected repository to its memory repo; use `context_packet` under the [mounted tool contract](../../skills/l-01-agent-lifecycles/reference/knowledge/commands.md):

```text
<ar-coordination>/memory-repos/ar-<repo>/
```

A repository that still carries the removed repo-local `<repo>/ar-memory/` root is refused with
that exact path and the route to re-point it. Resolution is per target repository.

## Closeout

External-memory changes need code and memory to stay mapped. The [authorized closeout owner](../../skills/l-01-agent-lifecycles/operations/closeout.md) coordinates that sequence through the existing tools for both direct edits in the current checkout and worktree-backed tasks:

1. for Agents Remember source changes, run the leaf change-set-scoped quality contract (`--targeted`); the full wrapper runs once per master at the master integration gate
2. commit code
3. refresh onboarding metadata against the code commit
4. commit memory content
5. update `memory.md`

Do not manually update the ledger unless you are deliberately repairing memory history.
