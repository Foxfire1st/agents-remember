# Separate current prose from authored history

Curators read this topic before updating current overview prose or answering changed-file history. It describes the meaning-owning body, onboarding-trace answers and invariant-history effects.

## Update the meaning-owning body

Never add an overview heading named for a leaf or date. Update the body section that owns the meaning. Add no task log. Preserve a truth when it matters to future correctness, is non-obvious and would be expensive to rediscover. Omit code narration, temporary branch facts, raw test totals, generic implementation chronology and facts obvious from code and tests.

## Answer changed-file and invariant history

Before writing card history, read [history dispositions](card-formats-and-citations.md#choose-the-history-disposition) for history meanings and family rows. Before checking citations, read [citation input](card-formats-and-citations.md#prepare-citation-input) for evidence tables, source locators and anchors. A changed file whose card is brought up to date is answered by an `onboarding_trace` row through the admitted writer. The handover names its subject, disposition and reason; the writer binds it to the source files' content.

Before answering an unexplained-hunk or unexplained-file worklist item, read the [handoff-list template's no-invariant row rules](../../templates/curator-handoff-ingestion.md#changes-with-no-invariant). They define the three answers for covered files, the delete-only exception and the separate onboarding-trace answer for uncovered files.

For invariant history, a `moved` row covers the re-anchored entry. A `changed` row names its effect; a `deleted` row retiring the invariant names only `retire`; other invariant rows name no effect.
