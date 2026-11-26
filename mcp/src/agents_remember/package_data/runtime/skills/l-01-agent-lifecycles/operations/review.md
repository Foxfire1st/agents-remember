<!-- ar:roles all -->
# Operation — Review

Cold reads = no round; advisory Investigators report to Architect only (Reviewer stays grade).

<!-- ar:roles architect -->
## Advisory reviews

Before commissioning/routing advisory reviews via `role_start`, read [advisory-reviews]({{detail:reference/review/advisory-reviews.md}}).

<!-- ar:roles all -->
## Outputs

Verdict: Catalog Binding, recommendation, checklist, coverage, findings (ID/loc/evidence/impact/repair), checks/limits. Baseline covers full diff (tests != acceptance); `fix-verification` = sealed IDs only; no `## Final checks`.

<!-- ar:roles reviewer -->
## Focused tests and handover

Baseline: rerun focused tests + freeze search (unrun = finding). `fix-verification`: search touched names (unrun stays open).

<!-- ar:gives producer-list to curator: list-file, freeze-identity, handoff-check-result -->
If mode is absent, ask starter via `role_message` (developer if parentless) and wait.

1. On code PASS, tell Manager to start Curator first.
2. Send verdict path directly to Worker via `role_message` (no pasted text; block names sealed report + repair stretch).
3. Once Curator exists, run `handoff-check` (`server_info`) and send Curator freeze, code-PASS verdict, list + result directly.
4. Review Curator's memory change; return findings or PASS directly.

Name leaf, candidate, path + next stretch (sibling role, leaf refs, whole IDs). Never read Worker chat; return reports missing per-finding entries without counting a round. Clean syncs open no round; review overlap/test-failure repairs only.

<!-- ar:roles all -->
## Rounds

Open leaf code rounds via `task_doc(begin_review)`, record via `record_review`/`record_route_review`; non-leaf keeps owner recording. Manager owns gate acceptance and records Architect/developer extra-round approvals. Memory review uses a separate lane (`reviewKind=memory`; own sealed reports/IDs/pass count, same limit; extra passes need Architect/developer instruction recorded by Manager); code verdict/baseline stay unchanged.

## Boundaries

After code+memory PASS & `## Final checks`: tell Manager closeout-ready (freeze, verdicts, final checks/none). Other notices: authority, ownership, recovery, extra rounds, Curator dispute.

<!-- ar:roles reviewer -->
Answer Curator's evidenced code concern directly to Curator+Worker (reopen or refute); dispute -> Manager.
<!-- ar:roles all -->
