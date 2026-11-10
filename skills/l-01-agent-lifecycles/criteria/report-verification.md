# Criteria catalog: report verification

The Reviewer and the loop owner checking a builder round use this catalog for every report, claim and summary in the change set. This includes builder reports, owner claims and handover packets. It is standing in every review type.

Before starting a baseline or fix-verification, read the [shared catalog review rules](code-seam.md#shared-catalog-review-rules). They define standing checks, baseline exploration, fixed-list verification and criterion promotion, demotion and automation. Preserve the developer-ruling exception below.

## Standing criteria

### RV-1 — Report-vs-artifact on every claim

Open the artifact behind every claim that something was done, passed or refreshed. Verify each claim against the artifact itself; do not sample the load-bearing claims.

For a claim that the tree contains only intended changes, inspect both content and mode changes. Use `git status --short` and `git diff HEAD --numstat` for content, and `git diff HEAD --summary` for mode-only changes. An executable-bit drop on a hook or script affects behavior—Git skips non-executable hooks—and content diffs do not show it.

Run these checks in the same filesystem view as the claimed change set. A 9P/Windows client can show mode changes absent from the Windows Subsystem for Linux view.

The claim must also cover every nonignored untracked path, not just the `??` name in status. Enumerate exact paths with a NUL-delimited Git inventory, such as `git ls-files --others --exclude-standard -z`, or an equivalently path-safe application programming interface. Do not use shell word splitting or newline parsing.

For every exact path, in that same filesystem view:

- Use `lstat`-equivalent semantics. Record filesystem type, numeric mode and executable significance.
- For a regular file, record its size and inspect bounded content. Use escaped, bounded text bytes, or an explicit binary/sensitive classification with a content hash; do not dump unlimited raw content.
- For a symbolic link, record the link and its target text without following the target.
- For a directory or special object, record the type and explicitly accept, reject or route it for further inspection. Do not open it as an ordinary file.
- Record whether the object is intended in the candidate.

If an object disappears, changes identity or becomes unreadable between inventory and evidence capture, report the race or limitation and establish a consistent view again. Do not silently omit it or claim complete-tree coverage. This is a semantic review obligation, not another verifier or authority to dump arbitrary untracked bytes into a report.

Builder and owner claims need the same scrutiny.

### RV-2 — Class-completeness

This criterion is standing. For every found instance, identify its class—for example a directive surface, census count or vocabulary token. Enumerate and check every sibling surface, not just those named in the report.

### RV-4 — Decision-log completeness for scope-expanding disclosures

This criterion is standing. A scope-expanding owner supplement or consequential builder disclosure must be verifiable in the task-document decision log, not only the builder report. Cross-check every supplement and disclosure against that log.

Continuity depends on task documents and durable artifacts, not transcripts. A ruling or environment finding held only in one report is lost if that report is lost.

### RV-5 — Canonical invocation target provenance

This criterion is standing. Every test, mutation check, benchmark and evidence command must establish that its resolved source checkout and task/contract are the artifacts under review.

Even a nominally canonical command can use an editable install from another checkout, name the wrong worktree contract or refuse before producing the output later attributed to it. Verify the resolved source/import path and the task/contract used by that exact invocation. Perform mutation-test checks under the same invocation. A passing result or measurement without matching target provenance is not evidence for the change set.

### RV-6 — Check scope, carried runs and pre-existing failures

This criterion is directly required and standing. The promotion/demotion process cannot demote it; only a developer ruling can.

A report calling a check complete, or calling it the gate, must name what ran and what was left out. Compare that coverage with the gate's own definition.

A failure called pre-existing must fail on the base for the same cause, identified by message and subtest, rather than merely sharing a test name. A run carried from an earlier freeze remains valid only when the files it exercises are byte-identical to those of its last run. Changed exercised files make the carried run an evidence gap.

## Candidate criteria

Run candidates under baseline exploration. For promotion, follow the [Promotion Ratchet](code-seam.md#promotion-ratchet).

### RV-3 — Partial-fix-creates-falsehoods

This candidate has one catch. Verify claims about changed artifacts as well as the artifacts themselves. A partial fix can make previously true documentation, quoted installation instructions or counts false. Find statements about what changed and check whether they remain true.
