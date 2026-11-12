# Use The Memory Repository

The memory of a code repository lies in a Git repository of its own, under the coordination
root. This guide shows where that memory lies, how the product finds it, and how a closeout
keeps the code and the memory in step.

## What The Separation Gives

- The code and the memory have separate Git histories. You review each one on its own and give
  each one its own permissions.
- One coordination root holds the memory repositories of several code repositories.
- Each memory commit names the code commit that it describes.

## Layout

```text
<workspace-root>/
  my-repo/                  the code repository
<coordination-root>/
  memory-repos/
    ar-my-repo/             the memory repository of my-repo
      docs/
      knowledge/
      onboarding/
      system/
```

Each code repository has exactly one memory repository. Its path follows from the id of the code
repository in the settings file:

```text
<coordination-root>/memory-repos/ar-<repo-id>/
```

[Runtime Layout](../reference/runtime-layout.md) lists the other folders of the coordination
root.

## Set Up

Install Agents Remember as [Getting started](../getting-started.md) describes. Its step 4, the
[Bootstrap call](../concepts.md#bootstrap-call), starts the Bootstrap role agent, which then
creates the memory repository in a chat with you; see
[Set up a repository with Bootstrap](../getting-started.md#set-up-a-repository-with-bootstrap).

To create it yourself, ask the agent of your harness to call `memory_init`:

```text
memory_init(repo_id="<repo-id>", dry_run=true)
memory_init(repo_id="<repo-id>", initial_branch="<branch>")
```

The call creates the folders, the files `settings.md`, `sources.md` and `tools.md` under `system/`
and the layout marker `knowledge/layout.json`, and it makes the folder a Git repository.
It writes no card and no knowledge record, and it overwrites no file that exists.
When the memory path already holds a Git repository without the marker, the call writes no marker;
[Onboard An Existing Repository](onboard-existing-repo.md#2-create-the-memory-repository) tells the
two ways that work.
[Onboard An Existing Repository](onboard-existing-repo.md) describes each argument and the answer.

For a memory repository that exists already, follow
[Adopt Existing Memory](adopt-existing-memory.md).

## Configure

The memory settings file of a repository is `<memory-root>/system/settings.json`. It holds the
path rules, the list of other repositories whose content may be read, and the landing workflow.
The path rules decide which source files are eligible for a card.

[Onboard An Existing Repository](onboard-existing-repo.md#3-choose-the-eligible-files) shows a
first file. The [settings reference](../reference/settings-json.md#memory-settings-file-keys)
describes every key, and [Path Rules](../reference/path-rules.md) describes the rules.

## Resolve

The tool `context_packet` resolves a code repository to its memory repository:

```text
context_packet(repo_id="<repo-id>")
```

The field `roots` of the answer names the two folders: `code` and `memory`. Outside a task they
are the code repository and the memory repository. For a task they are the two worktrees of the
task's enclosure, and `roots` also names the `enclosure` and the rendered `taskDocument`. `code`
is an absolute path; each other entry is relative to `code` where that form is shorter, and
absolute otherwise. [Product Context Resolution](../reference/resolver-c08.md) describes the whole
answer.

## How Code And Memory Stay Mapped

Every memory commit that a closeout or a baseline adoption makes carries a trailer in its
message:

```text
Code-Commit: <commit of the code repository>
```

The trailer is the record of which code a memory commit describes. The file
`<memory-root>/memory.md` lists these pairs, newest first. The file is a cache: Agents Remember
computes it from the trailers at each closeout and each baseline adoption, and keeps it out of
every commit. Do not edit it.

## Closeout

A closeout commits the code and the memory of one task as a pair. It needs your approval of that
commit; [Workflows](../workflows.md#closeout) says which role agent runs it and which authority
can stand in for your approval. The apply records the approval in its required argument
`intent_note`. The closeout has two calls; `<contract>` is the path of the task's
[worktree contract](../concepts.md#worktree-contract):

```text
worktree_closeout_preview(contract_path="<contract>")
worktree_closeout_apply(contract_path="<contract>", intent_note="<the reason for this commit>")
```

The preview judges the candidate and changes nothing. It answers `would-closeout` or a refusal:
`knowledge-gate-refused` (step 1 below), `closeout-gate-blocked`, `blocked`, or
`memory-opt-out-refused` when the worktree contract names a memory mode other than `external`.
The apply answers `closed`, `blocked` or `closeout-gate-blocked`, and it takes these steps in this
order:

1. **The [knowledge gate](../concepts.md#knowledge-gate) judges the pair.** The gate computes the
   [worklist](../concepts.md#worklist-and-knowledge-gate) and validates the exact code tree and
   the exact memory tree of the task. An open worklist item or a validator violation refuses the
   closeout as `knowledge-gate-refused`, before any commit.
2. **The pre-commit hook of the code repository runs.** The closeout runs the hook on exactly the
   tree it is about to commit. The closeout is refused when the hook fails, when the hook changes
   a file, or when the hook runs longer than the setting
   `orchestration.landing.commitCheckTimeoutSeconds` allows (default 900 seconds). The closeout
   reads the setting from the user settings file, from `system/settings.json` of the checkout in
   which the hook runs and, for a worktree, from that of its main checkout; a later file wins.
3. **The code commit.** The closeout commits the judged code tree on the branch of the task.
4. **The memory update.** The closeout refreshes the [route indexes](../concepts.md#route-index)
   and the fingerprints of the [entity catalog](../concepts.md#entity-catalog): for each entity a
   hash over the Git blobs of the files that define it, at the new code commit. It closes the
   history file of the task. The gate then judges the resulting memory tree against the new code
   commit.
5. **The memory commit.** The closeout runs the pre-commit hook of the memory repository on that
   tree and commits it with the `Code-Commit:` trailer of step 3.
6. **The cache.** The closeout computes `<memory-root>/memory.md` again. The file is not
   committed.

A commit that passed the hook check carries the trailer
`Commit-Check-Pass: tree=<tree> hook=<hook>`. For a repository without a pre-commit hook, `hook`
is `none`.

The closeout starts no test suite, no code-quality run, no certification and no review. The
Worker and the Reviewer run the checks of a task before the closeout.

When step 4 or step 5 refuses, the code commit exists and the memory commit does not. Repair what
the refusal names and run the closeout again: the second run completes the pair.

A task with a direct-execution grant has no enclosure. The tool `direct_landing` commits its work
with the same gate and the same hook check; see [Workflows](../workflows.md#direct-execution).

[Worktrees And Closeout](../reference/worktrees-c09.md#closeout-and-recovery) is the reference of
the closeout, and the [knowledge system](../reference/knowledge-system.md#worklist-validator-and-gate)
describes the gate.
