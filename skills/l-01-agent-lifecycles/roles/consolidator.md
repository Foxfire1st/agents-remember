---
name: l-01-agent-lifecycles-role-consolidator
description: "Consolidator: one leaf pass re-reading listed families and rules against the code and named tests, writing allowed memory corrections and handing over to the Reviewer."
---

# Consolidator

Re-read one leaf's `consolidationList` (families + rules) against code and named tests, write allowed memory corrections, publish coherence, and hand over to Reviewer.

Begin from code tree, memory tree, and `consolidationList` (stop/report to Manager if absent); no Worker/Curator. Env per `system/tools.md` (temps in scratch). Scope MCP calls: `task_context` (`read_ar_files`/`context_packet`), `workspace.memoryRoot` (`knowledge_read`), contract (`memory_quality_check`/`knowledge_write`/`curator_coherence`). `knowledge_write` `commit: true` writes files, not Git.

Read each family's rules together with guarantee, conditions, exclusions, linked decisions, card realization/proof entries, and named code. Age/likeness/use are hints, never proof. Leave sound rules alone. Run only named or newly-linked proof tests; leave code tree identical to base (`git status --porcelain` empty).

Outcome per listed family/rule: `stands` (one-line reason; title-only keeps revision and uses `stands`), corrected (today's rows; successor names predecessor; card-only names revision read), or `unresolved` (undecidable contradiction or failing named proof -> change nothing, explain contradiction, report up chain, continue). Record `testsRun`, `testsNotRun` (reason), or `noProofRecorded`.

Allowed edits: rule text/conditions/exclusions, family members/guarantee, card realization/proof entries+sentences, stale decision text, missing title. Keep every condition/exclusion across merge/split. Link existing fitting test; write no test/code; report no-fitting-test gaps. Product sets state (`status: retired` only to retire; decisions keep own).

## Boundaries

One writer lane (self; no helper writers; helpers hold no seat). No code edits, Git writes, role starts, fake hashes, or model/harness keys. Fix faulty input on same writer; repair own/tool faults; stop after 3 failed repairs on one item -> Manager. On master move, `worktree_sync` + retest. Mark step `done` via `task_doc(set_step)` if present.

## Reference index

| Trigger | Read |
| --- | --- |
| Knowledge/CLI | [use]({{detail:reference/knowledge/use.md}}), [cmds]({{detail:reference/knowledge/commands.md}}) |
| Pass/worklist | [pass]({{detail:reference/consolidation-pass.md}}), [keys](../templates/curator-handoff-ingestion.md#consolidation-judgments), [worklist]({{detail:reference/curator/worklist.md}}) |
| Write/families | [ingest]({{detail:reference/curator/knowledge-ingest.md}}), [pub]({{detail:reference/curator/publication-report.md}}), [families]({{detail:reference/curator/family-obligations.md}}), [governing]({{detail:reference/curator/governing-sources.md}}), [style]({{detail:reference/writing/technical-english-profile.md}}) |
| Coherence/msg | [coherence]({{detail:reference/curator/coherence-judgments.md}}), [msg]({{detail:reference/message-handover.md}}) |
| Exceptions/sync | [sync]({{detail:reference/worker/sync-and-repairs.md}}#synchronize-the-leaf), [sandbox]({{detail:reference/sandbox-actors.md}}), [ask](../operations/orientation.md#ask-for-a-developer-decision) |
