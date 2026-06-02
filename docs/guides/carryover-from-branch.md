# Carry Memory From a Branch

Use this guide when a protected official code branch gets delayed or batched
merges, while richer onboarding has accumulated on another branch (a personal
workbench, a feature branch, or a branch handled in a reconciliation pass).

`c-11-memory-carryover-from-branch` is **not a Git merge**. It is a selective
memory reconciliation: it proves which source-branch code changes have actually
landed on the official branch, then carries only the *corresponding* onboarding
into official memory and refreshes its verification metadata to the official code
commit.

## When to use it

- Official memory is missing richer onboarding that exists on another branch, and
- the code those onboarding files describe has (at least partly) landed on the
  official branch.

If the code did **not** land on official, its memory is not carried — that is the
whole point.

## Plan, then apply

Run the plan first and review the candidate report; only then apply.

```text
memory_carryover_plan(repo_id="<repo-id>", source_memory="<source-memory-repo>",
  official_code_ref="<official-ref>", source_code_ref="<source-ref>", old_base="<base-ref-or-sha>")

memory_carryover_apply(repo_id="<repo-id>", source_memory="<source-memory-repo>",
  official_code_ref="<official-ref>", source_code_ref="<source-ref>", old_base="<base-ref-or-sha>",
  intent_note="<developer intent>")
```

`apply` mutates official memory only — it never moves code branches, and it
refreshes carried onboarding to the **official** code commit (not the source
branch commit).

## Evidence tiers

Each candidate onboarding file is classified by how strongly its code is proven to
have landed:

| Tier | Meaning | Auto-carry? |
| --- | --- | --- |
| `exact-landed-commit` | a source commit that touched the path is an ancestor of official | yes |
| `patch-id-match` | the old-base→source patch matches the old-base→official patch | yes |
| `final-content-match` | source-ref content equals official-ref content for the path | yes |
| `same-path-changed` | both branches changed the path, but equivalence not proven | review-required |
| `not-landed` | changed on source but not on official | skipped |

Only proven tiers auto-carry. `same-path-changed` is review-required by default —
another developer may have changed the same file independently.

## Output states

- `would-carryover` — dry-run plan with candidate decisions
- `carried-over` — official memory content + ledger commits were created
- `nothing-to-carryover` — no selected candidate changed official memory
- `blocked` — apply requested without approval, dirty official memory, missing
  ledger, or missing candidate data

## Boundaries

- Never carries memory for code that did not land on official.
- Never copies source-branch ledger rows into official memory.
- Refreshes carried onboarding metadata to the official commit.
- Does not auto-carry `same-path-changed` evidence.
- The `c-02-memory-quality-control` skill remains the branch-accuracy drift detector; carryover only imports memory
  whose code validity is proven or explicitly approved.

Related: [Adopt Existing Memory](adopt-existing-memory.md) (the `c-10-adopt-memory-baseline` skill) creates the
first ledgered baseline; carryover (the `c-11-memory-carryover-from-branch` skill) keeps an existing official memory
enriched as branch work lands.
