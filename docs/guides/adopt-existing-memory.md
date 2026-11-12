# Adopt Existing Memory

Use this guide when a memory repository exists and its content shall become the first baseline.
The baseline is one commit of the memory repository's content. The message of that commit records
which commit of the code the content describes.

The Bootstrap role agent follows this procedure in a first setup. A memory repository that
`memory_init` just created is adopted in the same way.

## Purpose

Baseline adoption commits the content of the memory repository and records the code commit that
the content describes. The record is the trailer `Code-Commit:` in the message of the memory
commit. Adoption repairs no card. The agent of your harness, or the Bootstrap role agent, makes the
call at your request; the call records that you accept the content as the description of that
code commit.

Only a reachable commit with the trailer decides whether a baseline exists. The file
`<memory-root>/memory.md` is a cache: Agents Remember computes it from the trailers and never
commits it. A missing, old or malformed `memory.md` says nothing about the baseline.

## Status Check

Ask the agent of your harness for the state:

```text
memory_baseline_status(repo_id="<repo-id>")
```

The call changes nothing. The field `state` of its answer has one of these values:

| State | Meaning | What you do |
| --- | --- | --- |
| `ready` | No reachable memory commit carries a `Code-Commit:` trailer, and no check blocks. | Adopt. |
| `already-adopted` | A reachable memory commit carries a `Code-Commit:` trailer. | Nothing. Adoption is valid only before such a commit exists. |
| `unavailable` | The memory repository has a commit, and its history cannot be read. | Restore a readable Git history. |
| `blocked-legacy-cards` | A card under `onboarding/` holds a metadata row `doc_type`, `lastVerifiedCommitHash` or `lastVerifiedCommitDate`, or the heading of an append-only change history. The field `legacyCards` lists those cards. | Correct the listed cards. `accept_drift` does not lift this state. |
| `blocked-invalid` | The [knowledge validator](../concepts.md#validator) refuses the memory tree. The block `drift` gives `violationCount` and `violationSample`. | Correct the files that the sample names. |
| `blocked-drift` | Reference anchors of cards are stale. The block `drift` gives `actionableCount` and `actionableSample`. | Repair the anchors; see [Repair Before Adoption](#repair-before-adoption). |
| `legacy-format` | The memory repository has no layout marker `knowledge/layout.json`. The call measures nothing else. | Convert it with `agents-remember knowledge-convert`; see [Onboard An Existing Repository](onboard-existing-repo.md#2-create-the-memory-repository). |

The answer names `legacy-format` before every other state. When more than one other condition
holds, it names the first of `already-adopted`, `unavailable`, `blocked-legacy-cards`,
`blocked-invalid` and `blocked-drift`, in this order. The answer carries `ok: true` for `ready`
and `already-adopted`, and `ok: false` for every other state.

## Repair Before Adoption

A stale anchor cites a place in the code repository that is absent, or whose content differs from
the content that the card recorded. Two repairs exist:

- `citation_fix` records an anchor again when the cited content is unchanged and only its
  position moved. Inside a task, a call names the task's
  [worktree contract](../concepts.md#worktree-contract) in `contract_path`. While the memory
  repository has no baseline, the tool also accepts a call without `contract_path` and then works
  on the memory repository itself. Name the cards in `documents`, each as a path relative to
  `<memory-root>/onboarding/`; a call without it works on every card. Preview with
  `dry_run=true`.
- For every other stale anchor, a person or a Curator reads the source and corrects the card.

The argument `accept_drift=true` of `memory_baseline_adopt` lifts the state `blocked-drift` and
nothing else. It does not make a stale anchor acceptable: the validation of the next section
still refuses a stale anchor into a code or test file, with the state `blocked-invalid` and the
rule `R198.1-reference-anchor`, the rule of the validator that requires each such anchor to name <!-- docs-check: identifier: the product prints this code -->
the file's blob in the code commit and to find its recorded content there.

To see the anchors before you decide, call `context_packet(repo_id="<repo-id>", include_drift=true)`.
[Memory Quality Control](../reference/drift-c02.md#measuring-drift) describes the result.

## Adopt

Preview, then adopt:

```text
memory_baseline_adopt(repo_id="<repo-id>", dry_run=true)
memory_baseline_adopt(repo_id="<repo-id>")
```

| Argument | Meaning |
| --- | --- |
| `repo_id` | Required. The id of the code repository in the settings file. |
| `dry_run` | `true` validates what an adoption would commit and changes nothing. The default is `false`. |
| `accept_drift` | `true` lifts the state `blocked-drift`. The default is `false`. |
| `source_branch` | The branch of the code repository whose newest commit the baseline records. Without the argument, the call takes the branch that the code repository has checked out. |
| `work_branch` | The branch of the memory repository that receives the commit. Without the argument, the call takes the name of the source branch. |

The memory branch must be the default branch that `memory_init` recorded, and the memory
repository must have it checked out. The call refuses any other branch. It creates no branch and
switches to none.

The call fails with an error, and changes nothing, when the memory root does not exist, is not a
Git repository, or holds none of the folders `onboarding/`, `docs/` and `system/`.

The adoption takes these steps:

1. It stages the whole working tree of the memory repository. It leaves out the file `memory.md`
   and a folder `bootstrap/` at the root of the memory repository. Agents Remember creates no
   such folder and leaves it out of every memory commit.
2. The knowledge validator checks the staged tree against the code commit. A violation refuses
   the adoption with the state `blocked-invalid`.
3. The pre-commit hook of the memory repository runs on the staged tree. A failing hook refuses
   the adoption.
4. The call commits exactly the validated tree. The subject of the message is
   `[adopt-<repo-id>-memory-baseline] Adopt external memory content`, and the message carries the
   `Code-Commit:` trailer.
5. The call computes `<memory-root>/memory.md`.

When the working tree holds nothing to commit, the call validates the tree of the newest commit
and creates no commit.

The field `state` of the answer has one of these values:

| State | Meaning |
| --- | --- |
| `would-adopt` | A dry run, and the validation of the staged tree passed. |
| `adopted` | The adoption passed. The block `bootstrap` names in `memoryContentCommit` the new commit, or the newest commit when the working tree held nothing to commit. |
| `already-adopted` | A baseline exists. Nothing changed. |
| `blocked-legacy-cards`, `blocked-invalid`, `blocked-drift`, `unavailable`, `legacy-format` | As in the status check. Nothing changed. |
| `refused` | The memory repository has no recorded default branch. The answer carries `status: "memory-baseline-precondition-unavailable"` and names `memory_init` as the next tool. |

A refusal and a dry run leave the index, the branches and every file as they were.

Advice: read `memory_baseline_status` before and after the adoption, and keep both answers.
After an adoption that created the commit, the second answer reads `already-adopted`.

## After Adoption

Later work on the memory runs in tasks. A task has a code worktree and a memory worktree in its
enclosure, and its closeout commits both as a pair; see
[Use The Memory Repository](use-external-memory.md#closeout).

A baseline is not the repository's [knowledge foundation](../concepts.md#knowledge-foundation).
The answer of `memory_init` and the report of Bootstrap say whether the repository has recorded
knowledge. An Architect plans the knowledge foundation as a task; see
[Onboard An Existing Repository](onboard-existing-repo.md#4-start-the-knowledge-foundation).
