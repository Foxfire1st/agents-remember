# Onboard An Existing Repo

You do not need full coverage before Agents Remember becomes useful. Start small, then let onboarding grow as work touches new files.

## 1. Install The Runtime

Configure the Agents Remember MCP server, then request:

```text
runtime_install()
```

Skills are served by the tool server from its packaged copy; do not install a skill folder in a
project or in the coordination root. Follow the relevant [install guide](../README.md#install-guides)
for the registration.

## 2. Initialize Memory

External memory is the only supported topology, and it creates one memory repo per code repository:

```text
<coordination-root>/memory-repos/ar-<repo>/
  memory.md
  onboarding/
  docs/
  system/
    settings.md
    settings.json
    sources.md
    tools.md
```

`memory_init` takes the configured `repo_id` for a normal installed run, or the code repository
root for source debugging. Ask for the `initial_branch` first: default it to the branch the code
repository has checked out and give the developer the absolute memory path. When neither the
argument nor the checked-out branch exists, the call refuses rather than inventing a name. Pass
the answer as `initial_branch`; the memory repository's own first branch is given that name and
recorded for later comparisons.

Preview, then apply:

```text
memory_init(repo_id="<repo-id>", dry_run=true, initialize_git=true)   # preview
memory_init(repo_id="<repo-id>", initialize_git=true, initial_branch="<foundation branch>")
```

The default mode creates what is missing. Use `repair` only when the developer explicitly asks
to fix existing scaffold files. Never overwrite an existing memory file without that approval,
and create only the directories and files that are absent.

Inspect the memory root before changing anything:

```text
system/settings.md
system/settings.json
system/sources.md
system/tools.md
onboarding/
docs/
.git/
```

Also check the code repository for a repo-local `ar-memory/` directory. That layout was removed
from the product and is refused by name; report its exact path and never recreate it. Never
create a memory root inside the code repository. If the resolved memory or coordination root
points outside the intended workspace, state the absolute path before writing.

Create only the missing `system/`, `onboarding/` and `docs/` folders. Ensure
`<coordination-root>/memory-repos/` exists first. Do not create or modify coordinator runtime
directories such as `tasks/`, `worktrees/`, `notes/` or `temp/`, and do not create a `skills/`
directory: the runtime carries none, and the tool server serves the skills. When Git would
otherwise commit an empty directory, add `docs/.gitkeep`.

Create only absent starter files, with generic content:

- `system/settings.md` names the memory root's purpose and its scaffold table, and points
  machine-readable storage, path rules and cross-repository settings at `system/settings.json`.
  It must not duplicate active `pathRules` as an authoritative machine source.
- `system/settings.json` holds the storage mode, path rules and cross-repository allow list; see
  [Path Rules](../reference/path-rules.md).
- `system/sources.md` registers the domain documentation and external references later research
  may use, and says to record what was checked when no relevant source exists.
- `system/tools.md` holds the repository's own checks, commands and runtime notes.

Expected outcomes:

- **Fresh memory repo:** the memory root exists at the absolute path, Git is initialized when
  requested, `system/`, `onboarding/` and `docs/` exist, and the developer is told the memory is
  ready for the optional knowledge foundation.
- **Partial scaffold:** only missing directories and files are created; existing `docs/`,
  `system/` and onboarding content are preserved.
- **Complete scaffold:** nothing changes, and the report says the memory root is already
  initialized.

Report the resolved topology and memory root, the coordination root, whether Git was
initialized, any repo-local `ar-memory/` root found, the directories and files created, the files
left untouched, and the next step. The next step is not a bootstrap skill: ask the Architect for
the repository's first knowledge foundation.

To use an existing memory repo instead, clone or check it out at the resolved memory location
and adopt it as the first Git-attributed baseline; see
[Adopt Existing Memory](adopt-existing-memory.md). Its onboarding already exists. Do not skip the
knowledge state: report whether the repository has a recorded foundation, because an existing
memory can hold onboarding and none.

## 3. Configure Path Eligibility

Review `<coordination-root>/memory-repos/ar-<repo>/system/settings.json`.

Start with a small eligible surface:

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

See [Path Rules](../reference/path-rules.md) for the fuller exclusion baseline. When the resolved
rules lack the common excludes, record that during source review and repair `settings.json`
rather than filtering inside an agent.

## 4. Start The Knowledge Foundation

Ask the developer to start an Architect and give it this repository. The Architect opens a
registered foundation task under a master; its Curator reads the current knowledge state,
inventories the agreed sources, and authors the first invariants, families and realizations
through the curator writer. The foundation needs no onboarding file to start.

An Investigator may research the repository for that foundation; its report path is recorded in
the foundation task document so the Curator finds the evidence. Research produces evidence and
writes no memory.

Onboarding cards are separate: the Curator authors them inside a task when work touches a file,
following [the card procedure](../../skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md).
There is no separate onboarding-bootstrap skill.

## 5. Let Coverage Grow From Work

When a task touches a file, the agent should:

1. resolve context with `context_packet` using the [tool reference](../../skills/l-01-agent-lifecycles/reference/knowledge/commands.md)
2. check drift with the [memory-quality tools](../../skills/l-01-agent-lifecycles/reference/knowledge/commands.md)
3. read or create the relevant onboarding
4. implement approved work
5. have the Curator refresh onboarding through the [card procedure](../../skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md)

This avoids a giant up-front documentation project. The first task on a file pays the onboarding cost; later tasks benefit.
