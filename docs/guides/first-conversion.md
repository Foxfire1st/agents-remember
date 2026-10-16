# Make the First Conversion

Use this guide when memory is still in the legacy format (no `knowledge/layout.json`).
Every ordinary memory operation refuses such memory with the code `legacy-format` and names
this procedure (MIK-R46 rules 1, 2 and 6). Reading still works: `read_ar_files` returns
old-format cards as they are, marked `legacy-format`, so an unconverted repository can be
looked at before it converts.

Pick exactly one of the two cases below. Both end on converted memory, after which the same
operation is repeated and runs the ordinary converted route with its validator and gate.

## Case A — Memory with no attributed baseline

The memory root has no commit that a leaf could start from (it was never ledgered).

1. Run the conversion on the memory root itself, paired with the code repository it
   documents:
   `agents-remember knowledge-convert MEMORY_ROOT --code CODE_REPOSITORY`
   (`MEMORY_ROOT` is the memory working tree; `--code` is the paired code repository).
   The conversion validates the whole converted tree before it writes anything; a
   conversion that fails validation writes nothing and names the failing files.
2. Adopt the baseline, which validates the tree (MIK-R44 rule 7). Before the conversion,
   `memory_baseline_status` answers the state `legacy-format` with this step.

`memory_init` on an existing unconverted root still writes no marker; its result names
this procedure.

## Case B — Memory with an official line

The memory repository has an official line (a branch leaves start from).

1. Start one new leaf from that line. `worktree_start` works on unconverted memory; its
   result says `memoryFormat: "legacy-format"` with this procedure.
2. Run `agents-remember knowledge-convert` on the leaf's memory worktree with the leaf's
   code worktree, as the leaf's first memory change, naming the paired code commit
   explicitly (`--code-commit`; the default is the code worktree's HEAD):
   `agents-remember knowledge-convert MEMORY_WORKTREE --code CODE_WORKTREE --code-commit CODE_COMMIT`.
   The conversion validates the whole converted tree before it writes anything.
3. Close the leaf out and land it like any leaf: its candidate is converted, so the
   validator and the invariant gate run against the converted base (MIK-R24 rule 7), and
   the conversion itself counts as no change. The gate judges the leaf's own code
   change exactly as on any converted leaf: when the change touches files the
   converted cards do not cover, the preview names the findings (for example the
   file's `onboarding:<path>` trace and the root `onboarding:overview`), and the
   leaf answers each one -- a counted card change, or a `no_impact` row through
   the knowledge writer with a reason -- then previews again. Only a passing
   preview is applied. The landing moves the leaf's source
   branches (the series line), not the base it started from; read back the landed
   memory commit (message plus `Code-Commit` trailer) and the marker at its tip.
   A standalone `knowledge-validate` of the landed tip against the unconverted
   base it started from is refused by rule `R22.6-base-converted` (the
   standalone route binds no base converter; the gate route does, and it
   already passed): validate ordinary follow-up work against the landed,
   converted tip instead.
4. Every other open line of that repository crosses at its next `worktree_sync` with
   `merge-memory` (MIK-R24 rule 8).

## Authored migration prose after conversion

The converter preserves source identities and exported wording; it is not a style-rewriting pass.
For subsequent authored invariant revisions, family guarantees and history reasons, use the
[shared STE-inspired writing profile](../../skills/l-01-agent-lifecycles/reference/shared-writing-profile.md).
The migration procedure must link that profile before assessment waves start. Compare each proposed
rewrite's promise and semantic scope with its exact baseline and obtain independent meaning review
before publication through the normal writer. Keep producer statements, source/evidence identities,
closed history and original revisions inspectable. Do not bulk-rewrite frozen records or treat
successful conversion, a shorter sentence or a vocabulary check as proof of equivalent meaning.

## The converter stays

The conversion command, its read-only reader of the legacy database, and
conversion-format version 1 are permanent parts of the product: the build cannot know
that no unconverted line is left, because such lines live in other clones and on other
machines. Removing any of the three is a breaking change that needs a developer decision
of its own (MIK-R46 rule 7; the rejected alternative is "retire when this master
lands"). `knowledge-convert --help` states the same.

## See also

- `adopt-existing-memory.md`: convert first, then adopt.
- The `c-13-install-and-onboard` skill, which describes this same procedure.
