# Use External Memory

External memory is the supported topology: durable memory lives in its own repo per code repository. Repo-local internal memory under `<repo>/ar-memory/` was removed from the product and is refused rather than migrated.

## When External Memory Helps

External memory is useful when:

- code and memory should be reviewed or permissioned separately
- several code repositories share one coordination root
- long-running branches need matching code and memory versions
- `c-12-closeout` worktree closeout should record code-memory ledger mappings

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

Then ask the agent to run `c-00-initialize-memory-repo` for the target repository. It creates the external memory repo; there is no other mode to choose.

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

The `c-08-ar-coordination-context-resolver` skill resolves the repository to its memory repo:

```text
<ar-coordination>/memory-repos/ar-<repo>/
```

A repository that still carries the removed repo-local `<repo>/ar-memory/` root is refused with
that exact path and the route to re-point it. Resolution is per target repository.

## Closeout

External-memory changes need code and memory to stay mapped. `c-12-closeout` handles that sequence for both direct edits in the current checkout and worktree-backed tasks:

1. for Agents Remember source changes, run the leaf change-set-scoped quality contract (`--targeted`); the full wrapper runs once per master at the master integration gate
2. commit code
3. refresh onboarding metadata against the code commit
4. commit memory content
5. update `memory.md`

Do not manually update the ledger unless you are deliberately repairing memory history.
