# Curation exceptions, citations, and recovery

Curators, Reviewers and Managers read this file when handling report-only or repository-foundation assignments, quality-check card cuts and external citations, or recovery from memory-tool failures, evidenced code concerns and sync conflicts.

## Report-only assignments and foundation reports

An explicitly report-only assignment writes only its requested report and bound upstream message. It runs no onboarding writes, indexes, `memory_quality_check`, coherence or task comparison, and claims no normal curation pass complete. Normal authoring keeps full quality-check and coherence obligations.

For repository foundation, report the pre-authoring state read, examined and unexamined source areas, each record's outcome, wave-written files or no-write state, remaining or carried work and unreached partial-run areas.

## Quality-check card cuts and external citations

For each quality-check card cut, cite source evidence proving the removed text untrue; never cut text merely to clear a finding. Evidence and working files belong in the task record or scratch root, never the memory worktree. During memory review, the Reviewer checks `cutCards` and `addedFiles` per [Omission repairs, whole-set findings, and memory changes](../review/rounds-and-late-findings.md#omission-repairs-whole-set-findings-and-memory-changes).

Cite an external document supporting a card as an `external` sidecar reference, never as a repository path with a Git blob. Name the document in the handover evidence for the record's `origin`, so each backed record identifies its supporting document.

## Dependent-document closeout check and requested report fields

For Manager closeout, check enabled repositories before asking about updates, and run `knowledge_read(memoryRoot=<leaf memory>, repositoryRoot=<leaf code>, codeTreeId="working-tree", view="dependent", staleOnly=true)`; match candidates before and after, finish all pages, confirm or correct rows, and record the pair, input, `memoryTree.treeKey`, final `counts.total` and receipt. Product sets lifecycle statuses (`proposed`/`reviewed`/`consolidated`; `knowledge_write` refuses them); write `status: retired` only to retire records (decisions keep their own status). On `checklistStatus=coherence-required`, run `curator_coherence` `prepare`->`publish` (1 record per candidate, or empty when no change)->`validate` before closeout and never re-validate post-closeout trees.

Requested curation reports cover changed paths, reconciliation/repairs/answers, commands and results, writer state/IDs/paths/refusals, family coverage (updated plus unchanged with reasons; mark unexamined), worklist count and host/helper support (delegated work and how it was verified; state explicitly when none was used).

## Memory-tool failures, code concerns, and sync conflicts

- **Bounded repairs and tool failures**: Apply bounded encountered-memory repairs—whoever left it, beyond the quality check's findings—without a broad sweep, report every repair, and rerun complete checks and the independent fact-check before handover. When one memory tool fails, continue independent work through other admitted memory tools; existing refusal stops still bind dependent work, and no provider, permission, scope or tool boundary changes. Keep partial and refused handovers partial (`planned` and `refused` wrote nothing), correct faulty input through the same tool, and still repair validator and gate findings.
- **Stops, unfinished turns, and step marking**: Stop for an open permission prompt, a standing refusal outside covered-write recovery, a question about what the requirement means, or an effect outside the assigned worktree, scratch folder, and task. If you have a parent and remain unfinished at turn end, tell that parent what and whom you await via `role_message`. Mark only your own completed step; if no step names your part, report that and mark nothing. Incomplete, blocked, progress and escalation reports never mark unfinished work done.
- **Unsatisfied checks and rejected requirements**: Report rejected or Worker-blocked requirements as contradictions, never ruled onboarding intent. Report unsatisfied checks as blocked. Report dirty-source drift, missing onboarding and other findings exactly as returned, reading the full result and its file rather than only `ok`.
- **Code questions and evidenced code concerns**: When a comparison side is missing or too ambiguous to establish, ask the Worker directly about code or the Reviewer about its verdict; ask the Manager only about ownership or authority. Send evidenced code concerns to both Worker and Reviewer, including after passing code review, showing the requirement or report mismatch. Repair no code and author no knowledge claiming behavior the code lacks. The Reviewer records a finding and reopens the point, or explains its rejection; the Curator records the answer, reports both the finding and the answer, and sends unresolved disagreement to the Manager.
- **Sync conflicts and cross-leaf contacts**: Unresolved transaction conflicts belong to the Manager, and completed curation does not decide whether the leaf lands. Memory conflicts in the Worker's supported leaf sync go directly to the Curator while transaction steps remain with the Manager; follow [Synchronize the leaf](../worker/sync-and-repairs.md#synchronize-the-leaf) for the content-repair boundary and the Manager and Worker continuation sequence. Contact another leaf only for a dependency named by a requirement or the Manager; route other cross-leaf matters through the Manager.
