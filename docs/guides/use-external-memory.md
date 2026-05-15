# Use External Memory

Most repositories should start with internal memory under `<repo>/ar-memory/`. Use external memory when you intentionally want durable memory in a separate repository.

## When External Memory Helps

External memory is useful when:

- code and memory should be reviewed or permissioned separately
- several code repositories share one coordination root
- long-running branches need matching code and memory versions
- C-09 worktree closeout should record code-memory ledger mappings

## Layout

```text
projects/
  agents-remember-md/
  ar-coordination/
    AGENTS.md
    scripts/
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

Install the runtime first:

```bash
python3 agents-remember-md/installer/install-runtime.py ./ar-coordination
```

Then ask the agent to run `C-00-initialize-memory-repo` in external-memory mode for the target repository. External mode should be explicit; C-00 defaults to internal memory.

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

C-08 checks internal memory first, then external memory:

```text
<repo>/ar-memory/
<ar-coordination>/memory-repos/ar-<repo>/
```

An external memory repo does not force sibling repositories into external mode. Resolution is per target repository.

## Closeout

External-memory changes need code and memory to stay mapped. C-09 handles that sequence for worktree-backed tasks and direct closeout:

1. commit code
2. refresh onboarding metadata against the code commit
3. commit memory content
4. update `memory.md`

Do not manually update the ledger unless you are deliberately repairing memory history.
