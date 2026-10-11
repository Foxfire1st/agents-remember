"""MIK-R110's landed row data: fields, uses, bounds, and production paths.

One entry per owned tool: the answer fields the tool's status projection
carries, the caller step each field serves, the governed bound the guard
enforces (equal to the production bound in
``models.tools.tool_answer.SHAPED_TOOL_BOUNDS``), and the real tool paths the
row's ordinary and largest outputs are derived through. The derivation itself
lives in the three ``test_tool_answer_rows_*`` modules, which run those paths
on fixtures and prove the outputs fit the rows; this module holds no answer
payloads, so no hand-authored dictionary can drift from production.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.application.tool_response import complete_tool_response
from agents_remember.kernel.primitives.runtime_config import (
    TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT,
    TOOL_LOG_KEEP_DAYS_DEFAULT,
    TOOL_LOG_MAX_BYTES_DEFAULT,
    McpRuntimeConfig,
    parse_tool_log_settings,
)
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    ENVELOPE_STRIPPED_FIELDS,
    bound_for,
    is_valid_tool_code,
    strip_envelope,
    truncate_lists,
)
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    observer_root,
)
from agents_remember.observer.ambient import reset_ambient

# MIK-R110 rule 3: ``curator_coherence`` answers with a status -- the state,
# the counts and what blocks -- while the candidate set, the recorded lists
# and the refusal expectations live in the files and refusal fields the row
# names.
COHERENCE_ROW_FIELDS = frozenset(
    {
        "action",
        "state",
        "summary",
        "contractPath",
        "canonicalPath",
        "candidateCount",
        "candidatesByKind",
        "candidateSetDigest",
        "candidatesPath",
        "candidatesRenderingPath",
        "currentnessStatus",
        "nextAction",
        "recordPath",
        "reportPath",
        "publishedJudgmentCount",
        "assessmentCount",
        "recordDigest",
        "reportDigest",
        "snapshotPath",
        "judgmentsFileSha256",
        "status",
        "detail",
        "expected",
        "observed",
    }
)
COHERENCE_ROW_USES = {
    "action": "route the answer to the prepare, publish, validate or status step that follows",
    "state": "branch the curator's next step on prepared, published, valid, current, stale or refused",
    "summary": "read what happened in one sentence",
    "contractPath": "name the leaf enclosure the answer is about",
    "canonicalPath": "open the live authority the answer judges",
    "candidateCount": "size the judgment work",
    "candidatesByKind": "split the judgment work by candidate kind",
    "candidateSetDigest": "confirm the file read is the set prepare wrote",
    "candidatesPath": "read the candidate set from the file",
    "candidatesRenderingPath": "read the set in parts as text",
    "currentnessStatus": "name what blocks on a stale or unready source",
    "nextAction": "run the one call that unblocks the leaf",
    "recordPath": "open the published generation record",
    "reportPath": "open the published generation report",
    "publishedJudgmentCount": "confirm every candidate received exactly one judgment",
    "assessmentCount": "confirm the bound assessments published with the record",
    "recordDigest": "name the published generation",
    "reportDigest": "verify the published report bytes",
    "snapshotPath": "open the frozen delivery-attempt snapshot",
    "judgmentsFileSha256": "confirm the publish read the intended judgments file",
    "status": "branch on the machine-readable refusal identity",
    "detail": "read why the call was refused",
    "expected": "see which expectation the publish carried",
    "observed": "see what the leaf holds instead",
}
COHERENCE_ROW_BOUND = 850
COHERENCE_PRODUCTION_PATHS = ("status", "prepare", "publish", "validate")

# MIK-R110 rules 1-2: ``citation_fix`` answers with a status -- the outcome
# and the document and repair counts on success (never the root paths); the
# bounded actionable entries beside them on failure. The rewritten sidecars,
# the authoring report, the source index, and the repair rows stay in the
# whole result the log records.
CITATION_FIX_ROW_FIELDS = frozenset(
    {
        "ok",
        "dryRun",
        "repoId",
        "onboardingRoot",
        "documentCount",
        "rewrittenCount",
        "staleCount",
        "unreadableCount",
        "refusedCount",
        "findingsRemaining",
        "nextAction",
        "stale",
        "unreadableSidecars",
        "refused",
        "declined",
        "refusedDocuments",
    }
)
CITATION_FIX_ROW_USES = {
    "ok": "branch on whether the citations are clean",
    "dryRun": "tell a preview (nothing written) from a run that wrote",
    "repoId": "confirm the answer is about the admitted repository",
    "onboardingRoot": "confirm the run read this leaf's memory worktree",
    "documentCount": "confirm the call covered every card of the change",
    "rewrittenCount": "read how many citations were rewritten without opening the lists",
    "staleCount": "read how many citations stay stale without opening the lists",
    "unreadableCount": "read how many sidecars could not be read",
    "refusedCount": "read how many documents refused without opening the entries",
    "findingsRemaining": "read the re-measured remainder the ok flag is read off",
    "stale": "work the stale anchors, first entries first",
    "unreadableSidecars": "open the sidecars that could not be read",
    "refused": "work the refused authoring rows, first entries first",
    "declined": "work the declined claims, first entries first",
    "refusedDocuments": "rename the refused documents to cards that exist and rerun",
    "nextAction": "rerun the tool after working the entries",
}
CITATION_FIX_ROW_BOUND = 1500
CITATION_FIX_PRODUCTION_PATHS = ("converted", "unconverted")

# MIK-R110 rule 4: ``memory_quality_check`` answers with a status -- the
# outcome, the finding counts by kind, the blocking findings up to the list
# bound, and the locations of the checklist, census and coherence record. The
# census, the samples and the catalogue stay in the files the run wrote. A
# refusal keeps its reason as a code with the next action; a taken-over
# coherence refusal keeps that check's code and next action.
QUALITY_ROW_FIELDS = frozenset(
    {
        "status",
        "repoId",
        "onboardingRoot",
        "checklistStatus",
        "qualityChecklistStatus",
        "coherenceStatus",
        "closeoutReady",
        "curatorActionableCount",
        "coherenceRecordDigest",
        "coherenceCanonicalPath",
        "reportPath",
        "attestationPath",
        "state",
        "code",
        "guidance",
        "error",
        "detail",
        "pairField",
        "nextAction",
        "nextArgs",
        "findingCountsByKind",
        "blockingCount",
        "blocking",
        "censusPath",
        "runId",
    }
)
QUALITY_ROW_USES = {
    "status": "branch the repair loop on passed, failed, running, refused or missing",
    "repoId": "confirm the answer is about the admitted repository",
    "onboardingRoot": "confirm the check read this leaf's memory worktree",
    "checklistStatus": "gate closeout on ready-for-closeout",
    "qualityChecklistStatus": "read the raw checklist outcome under the coherence join",
    "coherenceStatus": "read why the coherence check refuses",
    "closeoutReady": "branch closeout on the one flag it needs",
    "curatorActionableCount": "size the curator's repair loop",
    "coherenceRecordDigest": "name the coherence authority the readiness join judged",
    "coherenceCanonicalPath": "open the live coherence authority",
    "reportPath": "open the checklist report holding every finding",
    "attestationPath": "open the structured checklist attestation",
    "state": "read the run envelope state on start and poll answers",
    "code": "branch a refusal on its machine-readable identity",
    "guidance": "read what the state means in one paragraph",
    "error": "read why a run failed in one sentence",
    "detail": "read the refusal's detail",
    "pairField": "read which pair field the scope refusal names",
    "nextAction": "run the one call that unblocks the leaf",
    "nextArgs": "pass the unblocking call its arguments",
    "findingCountsByKind": "split the repair work by finding kind",
    "blockingCount": "read how many findings block without opening the list",
    "blocking": "work the blocking findings in repair order, first entries first",
    "censusPath": "open the structural census holding the sample and catalogue",
    "runId": "poll the started run that this answer names",
}
QUALITY_ROW_BOUND = 1500
QUALITY_PRODUCTION_PATHS = ("sync", "start", "poll")

# MIK-R110 rule 5: ``route_index_refresh`` answers with the outcome, the route
# counts, and the locations; a stale run additionally carries the stale
# indexes up to the list bound. The successfully written indexes stay in the
# whole result the log records.
ROUTE_ROW_FIELDS = frozenset(
    {
        "ok",
        "repoId",
        "onboardingRoot",
        "dryRun",
        "routes",
        "written",
        "unchanged",
        "refreshedCount",
        "unchangedCount",
        "staleCount",
        "staleIndexes",
    }
)
ROUTE_ROW_USES = {
    "ok": "branch on whether the indexes are current",
    "repoId": "confirm the answer is about the admitted repository",
    "onboardingRoot": "confirm the indexes were written into this leaf's memory worktree",
    "dryRun": "tell a preview (nothing written) from a run that wrote",
    "routes": "read how many routes the survey covered",
    "written": "read how many indexes were rewritten",
    "unchanged": "read how many indexes were already current",
    "refreshedCount": "read the refreshed count without counting entries",
    "unchangedCount": "read the unchanged count without counting entries",
    "staleCount": "read how many indexes stay stale without counting entries",
    "staleIndexes": "refresh the indexes that are still stale, first entries first",
}
ROUTE_ROW_BOUND = 400
ROUTE_PRODUCTION_PATHS = ("applied", "dry-run")

# MIK-R110 rule 6: ``knowledge_integrity_check`` answers with the outcome, the
# comparison bases, the validation outcome with its counts by rule and first
# findings, and the leaf's persisted worklist state.
INTEGRITY_ROW_FIELDS = frozenset(
    {
        "state",
        "memoryRoot",
        "codeRoot",
        "bases",
        "validation",
        "refusalCode",
        "refusalDetail",
        "worklistState",
        "worklist",
    }
)
INTEGRITY_ROW_USES = {
    "state": "branch the caller on reported or refused",
    "memoryRoot": "confirm which memory tree was validated",
    "codeRoot": "confirm which code checkout anchors were checked against",
    "bases": "read the comparison bases the run judged against",
    "validation": "read the outcome, the counts by rule, and the first findings",
    "refusalCode": "branch a refused call on its machine-readable identity",
    "refusalDetail": "read why the call was refused",
    "worklistState": "read whether the leaf has a persisted change-to-knowledge worklist",
    "worklist": "work the leaf's persisted worklist items",
}
INTEGRITY_ROW_BOUND = 1500
INTEGRITY_PRODUCTION_PATHS = ("reported", "refused")

# MIK-R110 rule 7: ``drift_check`` keeps its report (the drift report and
# snapshot files) and answers with the outcome, the counts and the first
# entries up to the list bound.
DRIFT_ROW_FIELDS = frozenset(
    {
        "onboardingRoot",
        "status",
        "error",
        "basis",
        "memoryRoot",
        "codeRoot",
        "sidecarCount",
        "count",
        "states",
        "actionableCount",
        "actionableSample",
        "violationCount",
        "violationSample",
        "reportOnlyCount",
        "unreadableSidecars",
        "reportPath",
    }
)
DRIFT_ROW_USES = {
    "onboardingRoot": "confirm the check read this leaf's memory worktree",
    "status": "branch the caller on checked, notChecked or error",
    "error": "read why the measurement failed",
    "basis": "read whether anchors were judged converted or legacy",
    "memoryRoot": "confirm which memory tree was measured",
    "codeRoot": "confirm which code tree anchors were judged against",
    "sidecarCount": "read how many sidecars the converted measurement covered",
    "count": "read how many rows the measurement classified",
    "states": "read the current/stale split without opening the rows",
    "actionableCount": "size the curator's drift work",
    "actionableSample": "refresh the stale anchors through the onboarding gate, first entries first",
    "violationCount": "read how many refusing validator findings block",
    "violationSample": "work the refusing findings by their rule and message, first entries first",
    "reportOnlyCount": "read how many findings only report",
    "unreadableSidecars": "open the sidecars that could not be read",
    "reportPath": "open the drift report holding every row",
}
DRIFT_ROW_BOUND = 1500
DRIFT_PRODUCTION_PATHS = ("clean", "flagged")

# MIK-R110 rule 7: ``citation_migrate`` keeps its shape and answers within
# the bound; the complete decline list is that migration's contract and lives
# in the whole result.
MIGRATE_ROW_FIELDS = frozenset(
    {
        "repoId",
        "dryRun",
        "onboardingRoot",
        "codeRoot",
        "documentsScanned",
        "supersededTables",
        "supersededRows",
        "tablesConverted",
        "rowsConverted",
        "rowsKeepingOldEvidence",
        "placeholderRowsPadded",
        "supersededProseCitations",
        "proseConverted",
        "proseNotOnOneLine",
        "documentsWritten",
        "rangesFromLiteralMatch",
        "rangesFromDeclaration",
        "rangesFromOccurrenceOnly",
        "declinedCount",
        "declinedByReason",
        "declinedParserDependent",
        "ambiguitiesAnOldRangeWouldHavePicked",
        "workOrders",
        "sourceIndex",
        "findingsRemaining",
        "findingsRemeasured",
        "state",
        "outcome",
    }
)
MIGRATE_ROW_USES = {
    "repoId": "confirm the answer is about the admitted repository",
    "dryRun": "tell a preview (nothing written) from a run that wrote",
    "onboardingRoot": "confirm the run converted this leaf's memory worktree",
    "codeRoot": "confirm which code tree anchors were resolved against",
    "documentsScanned": "read how many documents the run scanned",
    "supersededTables": "read how many superseded tables the run found",
    "supersededRows": "read how many superseded rows the run found",
    "tablesConverted": "read how many tables were converted",
    "rowsConverted": "read how many rows were converted",
    "rowsKeepingOldEvidence": "read how many rows keep their original evidence verbatim",
    "placeholderRowsPadded": "read how many placeholder rows were padded",
    "supersededProseCitations": "read how many prose citations the run found",
    "proseConverted": "read how many prose citations were converted",
    "proseNotOnOneLine": "read how many prose citations span lines and stay untouched",
    "documentsWritten": "read how many documents were rewritten (zero on a preview)",
    "rangesFromLiteralMatch": "read how many ranges came from a literal match",
    "rangesFromDeclaration": "read how many ranges came from a declaration",
    "rangesFromOccurrenceOnly": "read how many ranges came from an occurrence alone",
    "declinedCount": "read how many rows were declined without opening the work orders",
    "declinedByReason": "split the declined rows by reason",
    "declinedParserDependent": "read how many declines depend on the parser",
    "ambiguitiesAnOldRangeWouldHavePicked": "read how many ambiguities an old range would have picked",
    "workOrders": "work the declined rows, first entries first",
    "sourceIndex": "confirm the frozen source generation the run read",
    "findingsRemaining": "read the re-measured remainder the ok flag is read off",
    "findingsRemeasured": "confirm the remainder was re-measured from the tree",
    "state": "branch the caller on planned, converted or refused",
    "outcome": "read whether the run planned or converted",
}
MIGRATE_ROW_BOUND = 1500
MIGRATE_PRODUCTION_PATHS = ("converted", "declined")

# MIK-R110 rule 7: ``memory_init`` answers with what was scaffolded and where
# the knowledge foundation lives, within the bound.
INIT_ROW_FIELDS = frozenset(
    {
        "repoId",
        "dryRun",
        "memoryRoot",
        "initialBranch",
        "initialBranchSource",
        "createdDirs",
        "createdFiles",
        "layoutMarker",
        "git",
        "knowledge",
    }
)
INIT_ROW_USES = {
    "repoId": "confirm the answer is about the admitted repository",
    "dryRun": "tell a preview (nothing written) from a run that wrote",
    "memoryRoot": "open the memory root that was scaffolded",
    "initialBranch": "read the branch the memory root is founded on",
    "initialBranchSource": "read where the initial branch came from",
    "createdDirs": "read which directories were created",
    "createdFiles": "read which files were created",
    "layoutMarker": "read whether the layout marker was created or already there",
    "git": "read the git initialization outcome",
    "knowledge": "read where the knowledge foundation lives and what is there now",
}
INIT_ROW_BOUND = 800
INIT_PRODUCTION_PATHS = ("preview", "applied")

# MIK-R110 rule 7: ``memory_baseline_status`` answers with the adoption
# state, the drift counts and the ledger state, within the bound.
BASELINE_STATUS_ROW_FIELDS = frozenset(
    {
        "state",
        "code_repository_name",
        "topology",
        "code_repository_root",
        "memory_root",
        "onboarding_root",
        "ledger_path",
        "drift_report",
        "drift",
        "ledger",
    }
)
BASELINE_STATUS_ROW_USES = {
    "state": "branch adoption on ready, already-adopted, blocked-drift, blocked-invalid or unavailable",
    "code_repository_name": "confirm the answer is about the admitted repository",
    "topology": "read the repository topology the status was measured on",
    "code_repository_root": "confirm which code checkout was measured",
    "memory_root": "confirm which memory tree was measured",
    "onboarding_root": "confirm which onboarding tree was measured",
    "ledger_path": "open the memory ledger",
    "drift_report": "open the drift report the state was read off",
    "drift": "read the drift counts behind a blocked-drift state",
    "ledger": "read the ledger state behind an already-adopted state",
}
BASELINE_STATUS_ROW_BOUND = 600
BASELINE_STATUS_PRODUCTION_PATHS = ("ready", "adopted")

# MIK-R110 rule 7: ``memory_baseline_adopt`` answers with the adoption
# outcome and the ledger it wrote, within the bound.
BASELINE_ADOPT_ROW_FIELDS = frozenset(
    {
        "state",
        "code_repository_name",
        "code_repository_root",
        "memory_root",
        "ledger_path",
        "drift_report",
        "drift",
        "ledger",
        "adopted",
        "message",
        "onboarding_root",
        "topology",
    }
)
BASELINE_ADOPT_ROW_USES = {
    "state": "branch the caller on adopted or refused",
    "code_repository_name": "confirm the answer is about the admitted repository",
    "code_repository_root": "confirm which code checkout was measured",
    "memory_root": "confirm which memory tree was adopted",
    "ledger_path": "open the memory ledger the adoption wrote",
    "drift_report": "open the drift report the adoption accepted",
    "drift": "read the drift counts the adoption accepted",
    "ledger": "read the ledger state the adoption wrote",
    "adopted": "read what the adoption recorded",
    "message": "read the adoption outcome in one sentence",
    "onboarding_root": "confirm which onboarding tree was measured",
    "topology": "read the repository topology the adoption was measured on",
}
BASELINE_ADOPT_ROW_BOUND = 700
BASELINE_ADOPT_PRODUCTION_PATHS = ("adopted", "refused")

# MIK-R110 rule 7: ``memory_carryover_plan`` answers with the carryover
# decisions by kind and the report holding the full candidate records.
CARRYOVER_PLAN_ROW_FIELDS = frozenset(
    {
        "ok",
        "state",
        "code_repository_name",
        "code_repository_root",
        "official_code_ref",
        "official_code_head",
        "source_code_ref",
        "source_code_head",
        "source_memory",
        "target_memory",
        "old_base",
        "replace_existing",
        "counts",
        "decisions",
        "reportPath",
    }
)
CARRYOVER_PLAN_ROW_USES = {
    "ok": "branch on whether the plan is appliable",
    "state": "branch the caller on planned or refused",
    "code_repository_name": "confirm the answer is about the admitted repository",
    "code_repository_root": "confirm which code checkout was measured",
    "official_code_ref": "confirm which official ref the plan carries onto",
    "official_code_head": "pin the official head the plan was computed against",
    "source_code_ref": "confirm which source ref the plan carries from",
    "source_code_head": "pin the source head the plan was computed against",
    "source_memory": "confirm which memory the plan carries from",
    "target_memory": "confirm which memory the plan carries into",
    "old_base": "pin the common base the plan was computed against",
    "replace_existing": "read whether applying overwrites the target's existing files",
    "counts": "read the decision counts by kind without opening them",
    "decisions": "review the carryover decisions by kind before applying",
    "reportPath": "open the report holding the full candidate records",
}
CARRYOVER_PLAN_ROW_BOUND = 500
CARRYOVER_PLAN_PRODUCTION_PATHS = ("planned",)

# MIK-R110 rule 7: ``memory_carryover_apply`` answers with the applied
# outcome, the carried paths, and the report holding the full records.
CARRYOVER_APPLY_ROW_FIELDS = frozenset(
    {
        "ok",
        "state",
        "code_repository_name",
        "code_repository_root",
        "official_code_ref",
        "official_code_head",
        "source_code_ref",
        "source_code_head",
        "source_memory",
        "target_memory",
        "old_base",
        "replace_existing",
        "counts",
        "decisions",
        "carriedPaths",
        "entity_fingerprint_validation",
        "intent_note",
        "ledger_cache",
        "memory_content_commit",
        "route_index_refresh",
        "reportPath",
    }
)
CARRYOVER_APPLY_ROW_USES = {
    "ok": "branch on whether the carryover applied",
    "state": "branch the caller on applied or refused",
    "code_repository_name": "confirm the answer is about the admitted repository",
    "code_repository_root": "confirm which code checkout was measured",
    "official_code_ref": "confirm which official ref the apply carried onto",
    "official_code_head": "pin the official head the apply carried onto",
    "source_code_ref": "confirm which source ref the apply carried from",
    "source_code_head": "pin the source head the apply carried from",
    "source_memory": "confirm which memory the apply carried from",
    "target_memory": "confirm which memory the apply carried into",
    "old_base": "pin the common base the apply was computed against",
    "replace_existing": "read whether applying overwrote the target's existing files",
    "counts": "read the decision counts by kind without opening them",
    "decisions": "confirm which decisions applied, by kind",
    "carriedPaths": "read which source paths were carried",
    "entity_fingerprint_validation": "confirm the carried fingerprints validated",
    "intent_note": "read the intent the apply recorded",
    "ledger_cache": "open the ledger cache the apply wrote",
    "memory_content_commit": "name the memory commit the apply created",
    "route_index_refresh": "read the route refresh the apply ran",
    "reportPath": "open the report holding the full carried records",
}
CARRYOVER_APPLY_ROW_BOUND = 800
CARRYOVER_APPLY_PRODUCTION_PATHS = ("applied",)


# Row lookup for the production derivation tests: tool to its fields, uses,
# bound, and production paths.
L110_ROW_DATA = {
    "curator_coherence": (
        COHERENCE_ROW_FIELDS,
        COHERENCE_ROW_USES,
        COHERENCE_ROW_BOUND,
        COHERENCE_PRODUCTION_PATHS,
    ),
    "citation_fix": (
        CITATION_FIX_ROW_FIELDS,
        CITATION_FIX_ROW_USES,
        CITATION_FIX_ROW_BOUND,
        CITATION_FIX_PRODUCTION_PATHS,
    ),
    "memory_quality_check": (
        QUALITY_ROW_FIELDS,
        QUALITY_ROW_USES,
        QUALITY_ROW_BOUND,
        QUALITY_PRODUCTION_PATHS,
    ),
    "route_index_refresh": (
        ROUTE_ROW_FIELDS,
        ROUTE_ROW_USES,
        ROUTE_ROW_BOUND,
        ROUTE_PRODUCTION_PATHS,
    ),
    "knowledge_integrity_check": (
        INTEGRITY_ROW_FIELDS,
        INTEGRITY_ROW_USES,
        INTEGRITY_ROW_BOUND,
        INTEGRITY_PRODUCTION_PATHS,
    ),
    "drift_check": (
        DRIFT_ROW_FIELDS,
        DRIFT_ROW_USES,
        DRIFT_ROW_BOUND,
        DRIFT_PRODUCTION_PATHS,
    ),
    "citation_migrate": (
        MIGRATE_ROW_FIELDS,
        MIGRATE_ROW_USES,
        MIGRATE_ROW_BOUND,
        MIGRATE_PRODUCTION_PATHS,
    ),
    "memory_init": (
        INIT_ROW_FIELDS,
        INIT_ROW_USES,
        INIT_ROW_BOUND,
        INIT_PRODUCTION_PATHS,
    ),
    "memory_baseline_status": (
        BASELINE_STATUS_ROW_FIELDS,
        BASELINE_STATUS_ROW_USES,
        BASELINE_STATUS_ROW_BOUND,
        BASELINE_STATUS_PRODUCTION_PATHS,
    ),
    "memory_baseline_adopt": (
        BASELINE_ADOPT_ROW_FIELDS,
        BASELINE_ADOPT_ROW_USES,
        BASELINE_ADOPT_ROW_BOUND,
        BASELINE_ADOPT_PRODUCTION_PATHS,
    ),
    "memory_carryover_plan": (
        CARRYOVER_PLAN_ROW_FIELDS,
        CARRYOVER_PLAN_ROW_USES,
        CARRYOVER_PLAN_ROW_BOUND,
        CARRYOVER_PLAN_PRODUCTION_PATHS,
    ),
    "memory_carryover_apply": (
        CARRYOVER_APPLY_ROW_FIELDS,
        CARRYOVER_APPLY_ROW_USES,
        CARRYOVER_APPLY_ROW_BOUND,
        CARRYOVER_APPLY_PRODUCTION_PATHS,
    ),
}

# Framework keys the delivery boundary may attach beside a row's fields.
_PRODUCTION_FRAMEWORK_KEYS = frozenset(
    {
        "ok",
        "code",
        "overflow",
        "answerTokens",
        "log",
        "logError",
        "resultKept",
        "error",
        "errorCut",
        "droppedFields",
        "next",
    }
)


def assert_production_answer(case: object, tool: str, answer: object) -> int:
    """Prove one real tool output fits its row and survives the guard uncut.

    Returns the measured token size. Asserts: every key is a row field or a
    delivery-framework key; every row field present has a stated next-step
    use; a carried refusal code is valid; a not-ok answer keeps its recovery
    action; the guard does not cut it; it measures within the row bound; and
    the row bound equals the production bound the guard enforces.
    """

    assert isinstance(answer, dict), f"{tool}: production output is not a mapping"
    # The delivery boundary strips the envelope before the caller sees the
    # answer, so the row proves the caller-facing fields.
    answer = {k: v for k, v in answer.items() if k not in ENVELOPE_STRIPPED_FIELDS}
    fields, uses, bound, _paths = L110_ROW_DATA[tool]
    for key in sorted(answer):
        assert key in fields or key in _PRODUCTION_FRAMEWORK_KEYS, (
            f"{tool}: production key {key!r} outside its row"
        )
    for key in sorted(answer):
        if key in fields:
            assert isinstance(uses.get(key), str) and uses[key], (
                f"{tool}: field {key!r} has no stated next-step use"
            )
    code = answer.get("code")
    if "code" in answer:
        assert is_valid_tool_code(code), f"{tool}: production code {code!r} is invalid"
    if answer.get("ok") is False and "nextAction" in fields:
        # Reason/action preservation: rows that carry a recovery action keep
        # it on every not-ok answer (quality and citation do; the other tools
        # work their bounded entries instead).
        assert answer.get("nextAction"), f"{tool}: not-ok answer names no next action"
    assert bound_for(tool) == bound, (
        f"{tool}: row bound {bound} differs from the production bound {bound_for(tool)}"
    )
    measured = count_response_tokens(dict(answer))
    assert measured <= bound, f"{tool}: production output measures {measured} over {bound}"
    return measured


def assert_answer_survives_the_guard(tool: str, whole: dict, answer: dict) -> dict:
    """Prove one projected answer passes the delivery guard uncut.

    Runs the real ``complete_tool_response`` boundary with the whole result
    beside the projection and returns the delivered mapping.
    """

    delivered = complete_tool_response(tool, dict(whole), answer=dict(answer))
    assert delivered.get("overflow") is not True, f"{tool}: the guard cut a normal case"
    for key in answer:
        assert key in delivered, f"{tool}: delivered answer lost projected key {key!r}"
    return delivered


def delivery_shaped(whole: dict) -> dict:
    """Shape one whole result the way delivery does: strip, then cut lists.

    Tools without their own status projection deliver the whole result
    through the generic shaping; the row proves that caller-facing form,
    where every list is already cut to the list bound.
    """

    return truncate_lists(strip_envelope(dict(whole)))


def _big_blob() -> str:
    # Varied content: long runs of one character compress well under
    # o200k_base, so the overflow cases use varied text instead.
    return "".join(f"{index:08d}-abcdefgh;" for index in range(3000))


def _stub_config(
    coord_root: Path,
    *,
    keep_days: int = TOOL_LOG_KEEP_DAYS_DEFAULT,
    max_bytes: int = TOOL_LOG_MAX_BYTES_DEFAULT,
    enclosure_max_bytes: int = TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT,
    transcript_root: Path | None = None,
):
    return McpRuntimeConfig(
        config_path=coord_root / "settings.json",
        coordination_root=coord_root,
        workspace_root=coord_root,
        transcript_root=transcript_root or (coord_root / "logs" / "mcp"),
        tool_log=parse_tool_log_settings(
            {
                "keepDays": keep_days,
                "maxBytes": max_bytes,
                "enclosureMaxBytes": enclosure_max_bytes,
            }
        ),
    )


def _install_ambient(coord: Path):
    """Install a fresh fleeting ambient lifecycle for one test (rule 3)."""

    reset_ambient()
    amb = AmbientLifecycle(EventStore(observer_root(_stub_config(coord))))
    amb.start(fleeting=True)
    install_ambient(amb)
    return amb


def _server_info_case() -> dict[str, Any]:
    # The smallest valid server_info payload (MIK-R112: `ping` is removed):
    # the fields its response model requires, including the envelope
    # `servingBuild` the delivered answer strips.
    return {
        "ok": True,
        "server": "Agents Remember",
        "version": "1",
        "transport": "stdio",
        "configPath": "c",
        "coordinationRoot": "r",
        "workspaceRoot": "w",
        "transcriptRoot": "t",
        "servingBuild": {"version": "1", "bootedAt": "2026-10-10T00:00:00+00:00"},
    }
