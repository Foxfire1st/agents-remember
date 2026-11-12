# Onboard An Existing Repository

This guide sets up one code repository that has no memory yet. You create the memory repository,
choose which files are eligible for cards, and start the first knowledge.

A repository needs no full coverage before Agents Remember is useful. Cards and knowledge records
grow as tasks touch files.

The Bootstrap role agent makes the calls of step 2 in a chat with you; see
[Set up a repository with Bootstrap](../getting-started.md#set-up-a-repository-with-bootstrap).
Use this guide to make the calls from a chat of your harness, or to read what Bootstrap did.

## 1. Install Agents Remember

Follow the install steps in [Getting started](../getting-started.md). This guide needs three of
their results: the settings file lists the code repository under `repositories` (step 2), your
harness has the tool server registered (step 3), and the coordination root exists (step 4, the
[Bootstrap call](../concepts.md#bootstrap-call), creates it).

## 2. Create The Memory Repository

Each code repository has one memory repository under the coordination root:

```text
<coordination-root>/memory-repos/ar-<repo-id>/
```

The tool `memory_init` creates it. Ask the agent in a chat of the harness in which you registered
the tool server to preview the call and then to make it:

```text
memory_init(repo_id="<repo-id>", dry_run=true)
memory_init(repo_id="<repo-id>", initial_branch="<branch>")
```

| Argument | Meaning |
| --- | --- |
| `repo_id` | Required. The id of the code repository in the settings file. |
| `dry_run` | `true` reports what the call would create and writes nothing. The default is `false`. |
| `initialize_git` | `true` makes the memory root a Git repository. The default is `true`. |
| `initial_branch` | The branch of the code repository that the memory describes. |

`initial_branch` gives the first branch of the memory repository its name. Without the argument,
the call takes the branch that the code repository has checked out. When neither gives a name,
the call refuses with `ok: false` and writes no file; it creates at most the empty folder of the
memory root, which `createdDirs` names. The call records the name in the Git configuration of the
memory repository, under `agents-remember.defaultBranch`. Baseline adoption and the worktree tools
compare against that name.

A call on a repository without memory creates this tree:

```text
<coordination-root>/memory-repos/ar-<repo-id>/
  .git/
  docs/
  knowledge/
    layout.json
  onboarding/
  system/
    settings.md
    sources.md
    tools.md
```

| Path | What it holds |
| --- | --- |
| `<memory-root>/onboarding/` | The cards. The call creates the folder empty. |
| `<memory-root>/knowledge/layout.json` | The layout marker. It names the format of the memory files, and the memory tools need it. |
| `<memory-root>/docs/` | Documents about the repository that you or a role agent add. The call creates the folder empty. |
| `<memory-root>/system/settings.md` | Your instructions for people and agents who work on this repository. |
| `<memory-root>/system/tools.md` | The repository's commands for tests and checks. Workers and Reviewers read it. |
| `<memory-root>/system/sources.md` | The list of domain documents and outside references that research and curation read. |

Each of the three files in `<memory-root>/system/` starts as one heading line. You fill them in.

The call creates what is missing and overwrites no file that exists. Its answer names the result:

| Field | Meaning |
| --- | --- |
| `memoryRoot` | The absolute path of the memory repository. |
| `initialBranch`, `initialBranchSource` | The branch name and its origin: `explicit`, `code-repository-current-branch` or `unresolved`. |
| `createdDirs`, `createdFiles` | The folders and files this call created. Both are empty when the tree was complete. |
| `layoutMarker` | `created` on a new memory root; `present` when the marker exists; `unconverted-existing-memory` when the memory root holds Markdown files under `onboarding/` or a knowledge database, but no marker; `existing-repository-unchanged` when the memory root is a Git repository without the marker; `not-created` when the call would have created the marker and refused first (`ok: false`). Only `created` writes a marker. A memory root in the state `unconverted-existing-memory` needs the conversion `agents-remember knowledge-convert` that this section describes below. |
| `git` | Whether the call ran `git init`, and Git's result. |
| `knowledge` | Whether the repository has recorded knowledge: `state` is `not-recorded`, `recorded`, `unusable` or `context-not-admitted`. |

The call fails with an error when the coordination root is missing. The error names
`runtime_install`, the tool that creates the coordination root. Finish step 1 first: its Bootstrap
call creates the coordination root.

To use a memory repository that exists already, clone it to the path above and follow
[Adopt Existing Memory](adopt-existing-memory.md).

When the folder at the memory path is a Git repository without the layout marker, for example a
clone of an empty repository that you created on a hosting site, `memory_init` creates the folders
and the three files under `system/` and writes no marker. `memory_baseline_status` and
`memory_baseline_adopt` then answer `ok: false` with the state `legacy-format`, and their
`nextAction` names the conversion below. Two ways lead to a memory repository that the tools
accept:

- Call `memory_init` while no folder stands at the memory path. The call creates the repository
  with the marker. Add the remote afterwards, in the memory root, with
  `git remote add origin <address>`.
- In a repository that is already cloned to the memory path, call `memory_init` and then run
  `agents-remember knowledge-convert <memory-root> --code <code-repository>`. It writes
  `knowledge/layout.json`, and `memory_baseline_status` then answers `ready`.

Neither way commits anything.

`memory_init` writes no card and no knowledge record, and it commits nothing. The first commit of
a new memory repository is its baseline: adopt it as [Adopt Existing Memory](adopt-existing-memory.md)
describes.

## 3. Choose The Eligible Files

Path rules decide which files of the code repository are eligible for cards. They stand in the
memory settings file, `<memory-root>/system/settings.json`. `memory_init` does not create that
file. Without it, the repository has no path rules of its own.

Advice: start with few files eligible for a card and add paths as tasks reach them. Create the
file with content of this shape:

```json
{
  "onboarding": {
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

[Path Rules](../reference/path-rules.md) gives a fuller list of excludes. The
[settings reference](../reference/settings-json.md#memory-settings-file-keys) describes every key
of the file, and its [example](../reference/settings-json.md#memory-repo-example) shows a complete
file. The repository carries an example file at
[`system/defaults/examples/memory-repo/settings.json`](https://github.com/Foxfire1st/agents-remember/blob/main/system/defaults/examples/memory-repo/settings.json).

## 4. Start The Knowledge Foundation

The [knowledge foundation](../concepts.md#knowledge-foundation) is the repository's first set of
knowledge records, invariants and families, with their
[realizations](../concepts.md#realization-and-proof) in the code. An Architect plans it as a
regular task.

1. Open the dashboard on the tab "Chats" and start an Architect; see
   [Start your first task](../getting-started.md#start-your-first-task).
2. Tell the Architect that you want the knowledge foundation of this repository.

The Architect opens a task for the knowledge foundation under a master. The Curator of that task
reads the state of the knowledge, lists the agreed sources, and writes the first records through
the tool `knowledge_write`. The knowledge foundation needs no card to start.

The Architect may start an Investigator to research the repository first. The Investigator
reports evidence and writes no memory. The task document of the foundation task names the report,
so the Curator finds it.

Cards are separate from the knowledge foundation. A Curator writes the card of a file inside the
task that touches the file, by the
[card procedure](https://github.com/Foxfire1st/agents-remember/blob/main/skills/l-01-agent-lifecycles/reference/curator/card-formats-and-citations.md),
the Curator's instructions on the format of a card and its numbered citations.
[The Cost Of A Repository's First Memory](cost-aware-bootstrap.md) gives advice on the cost of
this first work.

## 5. Let Coverage Grow From Work

Each task adds memory for the files it touches:

1. The role agent resolves the repository with `context_packet(repo_id="<repo-id>")`: the
   answer names the folders of the code and of the memory that the agent works in; see
   [Context resolution](../concepts.md#context-resolution).
2. It asks `read_ar_files` for the cards and records of the files it will read. The tool names
   where each one lies, and the agent reads those files beside the source.
3. The Worker builds the approved change.
4. The Curator of the task writes or updates the cards and knowledge records of the touched
   files.
5. Closeout commits the code and the memory together.

The first task on a file pays for its card. Later tasks read the card and keep it current.
[Workflows](../workflows.md) describes a task from the request to the landing, and
[Memory Quality Control](../reference/drift-c02.md) describes the checks on the cards.
