# Operation — Implementation

**What it covers:** building the assigned scope inside the leaf's code worktree, and producing the
builder artifacts the next seats consume.

**When it is selected:** a leaf (or a flat/solo owner wearing the worker discipline) is building
code or docs against an approved leaf plan.

## Who carries it, and their job

| Role | Its job in this operation |
| --- | --- |
| worker | implement exactly the leaf plan; run targeted checks; write the turn report |
| architect (solo/flat only) | the same discipline when it builds hands-on at session scale |

No other seat implements. A manager, orchestrator, curator, reviewer, strategist, or designer that
finds itself implementing has crossed a boundary in its own role file.

## Required inputs

- The leaf `task_doc` and its one owned primary requirement revision (stable ID + version, approved
  packet, durable corpus ruling), plus the adjacent revisions as dependency/preservation
  constraints only.
- The code worktree the brief names — the seat's only write area besides its report path.
- The resolved memory layer's `system/coding-guidelines.md` and `system/tools.md` **before the first
  edit**. A conflict between those guidelines and the leaf plan is an escalation, never a silent
  choice.
- Retrieval evidence as the brief requires: paired `read_ar_files` reads for orientation, plus
  `grepai_search` / `cgc_*` when the leaf needs semantics or relationships.

## Normal workflow

1. **Orient (see `orientation.md`), then read before editing.** Read the files you will touch paired
   with their onboarding, and read the current bytes natively inside the worktree. **Native read is
   the edit precondition.**
2. **Implement exactly the leaf plan.** Fill small, unambiguous blanks a competent implementer would
   fill; do not fill important gaps silently.
3. **Keep the change to the named surfaces.** Nothing outside the brief's writable areas, and no
   unrelated cleanup.
4. **Produce the next seat's input as you go**: changed paths, a diff summary, the tests you ran,
   and any route/onboarding observations — marked as **observations or candidates**, never as
   current truth. The curator, not the builder, writes onboarding.
5. **Run the targeted checks** required by the leaf brief and by the **targeted-check contract** in
   `closeout.md` § The targeted-check contract, then write the turn report. The builder owns the
   checks; the owning seat consumes them as handoff evidence.

## Authority gates

- **Never `git commit`, merge, push, or integrate.** All changes stay uncommitted in both worktrees;
  the owning seat commits at closeout after reviewing the report.
- **No curator writes, no self-approval, no closeout, no task-doc bookkeeping.**
- A plan delta beyond blank-filling escalates to the owning seat — never straight to the developer,
  and never a reshape of the seat's own.
- The memory worktree is read-only context unless the brief explicitly says otherwise.
- Generated route indexes are regenerated with their owning tool from the memory worktree; a
  builder does not regenerate the official indexes.

## Failure handling

- **A red targeted check the seat cannot fix inside the leaf's scope is an escalation, not a
  workaround.** Report it; never relabel a failure or a not-run check as green.
- **A guideline conflict** (coding guidelines vs leaf plan) escalates to the owning seat.
- **A claimed requirement problem** is diagnosed and proposed, never rewritten: report it as a
  contradiction for architect/developer revision authority.
- **A blocked attempt** is reported with exactly one failure class: `implementation defect`,
  `evidence gap`, `requirement contradiction/overconstraint`, `test/tool defect`, or
  `external blocker`.

## Handoff / exit

The implementation operation exits when the change is complete, the targeted checks are run and
truthfully reported, and the durable builder artifacts exist:

- the **turn report** at the brief's report path, in the shape of `../templates/turn-report.md`,
  including the one acceptance block for the owned primary revision;
- the **Requirement Attempt Journal** records, when an exact candidate is being handed to
  independent review;
- the builder input the curator needs (changed paths, diff summary, observations).

Then stop. Ending the turn once the artifact exists is safe — see `../core/acceptance.md`.
