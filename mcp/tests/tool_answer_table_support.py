"""The tool-answer table's rows, roster, validators and shared case helpers.

MIK-R108 rules 3, 4, 6, 7 and 8: one table with a row for every registered
tool and every operation of a tool that has operations, the authored roster
that proves no row is synthesized, and the guard every shaped row must pass.
Moved text-identical out of test_tool_answer_table.py (MIK-R196 rule 10) so
no test module carries the whole table; the cases stay in the sibling
test_tool_answer_table*.py modules that import this one."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.application.task_docs.task_doc_tools import VALID_OPERATIONS
from agents_remember.kernel.primitives.runtime_config import (
    TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT,
    TOOL_LOG_KEEP_DAYS_DEFAULT,
    TOOL_LOG_MAX_BYTES_DEFAULT,
    McpRuntimeConfig,
    parse_tool_log_settings,
)
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    INTERIM_LEAVES,
    L111_SHAPED_TOOLS,
    TOOL_ANSWER_EXCEPTION_MAX_TOKENS,
    TOOL_ANSWER_EXCEPTIONS,
    bound_for,
    is_valid_tool_code,
)
from tool_answer_l110_rows import L110_ROW_DATA
from tool_answer_l111_test_support import ToolAnswerRow, _apply_l111_shaped_rows

# Answer keys the row framework itself adds around row fields: a row case
# may carry these without naming them as fields.
ROW_CASE_FRAMEWORK_KEYS = frozenset(
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


# The authored roster (rules 6-7): every registered tool named under its
# exact rule-7 leaf. A newly registered tool has no entry here, so the
# roster check below fails instead of silently manufacturing its row. Tools
# of L111 and L100 stand below as interim rows until their leaf lands and
# shapes them; L109 has landed (MIK-R109 rules 1-4), so its four
# rebuilt reads hold final rows in L109_SHAPED_ROWS below and no entry
# names L109; L110's ten retained memory tools have landed (their rows are
# shaped in tool_answer_l110_rows.py and the implementation names no interim
# leaf for them, so they stand outside this roster); L112 has landed
# (MIK-R112 rule 9), so its three retained tools hold final rows in
# L112_FINAL_ROWS and no entry names L112. No entry names a removed tool.
AUTHORED_INTERIM_OWNERS: dict[str, str] = {
    # L100: native tools stand as interim rows until their leaf lands.
    "retire_child": "L100",
    "role_start_set": "L100",
    # L111: the task and landing tools (worktrees.py, closeout.py,
    # role_agents.py) plus the four named tools.
    "worktree_start": "L111",
    "worktree_attach": "L111",
    "worktree_status": "L111",
    "worktree_sync": "L111",
    "worktree_pause": "L111",
    "worktree_closeout_preview": "L111",
    "worktree_closeout_apply": "L111",
    "worktree_integrate": "L111",
    "worktree_checkpoint_landing": "L111",
    "worktree_record_landing": "L111",
    "worktree_operation_control": "L111",
    "worktree_cleanup": "L111",
    "worktree_abandon": "L111",
    # MIK-R82's adoption tool arrived with no row; it is a worktree tool, so
    # it stands interim under L111 like its siblings.
    "worktree_enclosure_adopt": "L111",
    "direct_landing": "L111",
    "task_doc": "L111",
    "lifecycle_finalize_task": "L111",
    "closeout_queue": "L111",
    "task_reopen": "L111",
    "role_start": "L111",
    "role_message": "L111",
}


def _l112_shaped_row(
    tool: str,
    fields: dict[str, str],
    bound: int,
    ordinary_case: dict[str, Any],
    largest_case: dict[str, Any],
) -> ToolAnswerRow:
    """One MIK-R112 rule-9 final row: named fields with caller-step uses,
    the tool's own bound set from its largest case, and measured payloads."""
    return ToolAnswerRow(
        tool=tool,
        operations=(None,),
        fields=frozenset(fields),
        uses=dict(fields),
        bound=bound,
        interim_leaf=None,
        ordinary_case=ordinary_case,
        largest_case=largest_case,
    )


_CASE_ROOT = "/tmp/ar-answer-cases/coord"


# The ordinary case the operation produces: a dry run on a fresh root with an
# ok watcher stop. Produced by run_runtime_install + the row projection
# (compact_runtime_install_payload), paths normalized to _CASE_ROOT; the
# producer test below pins it byte-for-byte. Measured 251 tokens against the
# row bound 600. No host settings file exists, so the host step reports its
# error shape and ok is false.
_INSTALL_ORDINARY_CASE = {
    "coordinationRoot": "/tmp/ar-answer-cases/coord",
    "dryRun": True,
    "experiment": {
        "delivery": {
            "mode": "legacy",
            "reason": "no experiment was selected for this run; the installation "
            "is the unmodified one",
        },
        "record": None,
        "selection": {
            "experiment": None,
            "reason": "no experiment was selected for this run; the installation "
            "is the unmodified one",
            "requested": "",
            "selected": False,
            "source": "unselected",
        },
    },
    "host": {
        "changed": False,
        "configured": False,
        "message": "No host configured: /tmp/ar-answer-cases/coord/mcp.settings.json has no "
        "paseoRuntime block; a Bootstrap role_start establishes that block in this "
        "authority file.",
        "node": None,
        "ok": True,
        "settingsPath": "/tmp/ar-answer-cases/coord/mcp.settings.json",
    },
    "includeBenchmarks": False,
    "installProviderDeps": True,
    "ok": True,
    "providerWatcherRebind": {
        "attempted": True,
        "ok": None,
        "phases": [{"action": "stop", "ok": True, "phase": "pre-provider-refresh-stop"}],
    },
    "reportPath": "/tmp/ar-answer-cases/coord/report.json",
    "summary": {
        "copiedFiles": 18,
        "createdDirs": 44,
        "dependencyRuns": 0,
        "removedPaths": 0,
        "replacedLinks": 0,
        "unchangedFiles": 0,
        "unusedProviderKeys": [],
    },
}


# The largest case the operation produces: the same dry run with a failed
# watcher stop, which records the stop, the failure message and the recovery
# action. Post-retirement the rebind holds the single stop phase only. Its
# root holds a store of a removed part and a gate log (MIK-R113 rule 5), so
# the answer counts them and names the command and the way out of a stored
# gate; their `logs/` folder is there before the run, so one folder fewer is
# created. Measured 453 tokens; the row bound 600 sits between it and twice it.
_INSTALL_LARGEST_CASE = {
    "coordinationRoot": "/tmp/ar-answer-cases/coord",
    "dryRun": True,
    "experiment": {
        "delivery": {
            "mode": "legacy",
            "reason": "no experiment was selected for this run; the installation "
            "is the unmodified one",
        },
        "record": None,
        "selection": {
            "experiment": None,
            "reason": "no experiment was selected for this run; the installation "
            "is the unmodified one",
            "requested": "",
            "selected": False,
            "source": "unselected",
        },
    },
    "host": {
        "changed": False,
        "configured": False,
        "message": "No host configured: /tmp/ar-answer-cases/coord/mcp.settings.json has no "
        "paseoRuntime block; a Bootstrap role_start establishes that block in this "
        "authority file.",
        "node": None,
        "ok": True,
        "settingsPath": "/tmp/ar-answer-cases/coord/mcp.settings.json",
    },
    "includeBenchmarks": False,
    "installProviderDeps": True,
    "messages": [
        "Provider watcher stop failed before runtime provider refresh; the install continued "
        'without watcher recovery. Failed stop: {"provider": "watchers", "action": "stop", '
        '"partial": false, "recoveryActions…'
    ],
    "ok": False,
    "providerWatcherRebind": {
        "attempted": True,
        "ok": False,
        "phases": [{"action": "stop", "ok": False, "phase": "pre-provider-refresh-stop"}],
    },
    "recoveryActions": [
        {
            "action": "restart",
            "provider": "watchers",
            "recoveryAction": "Stop the named leftover process and rerun the install; no "
            "product path restarts provider watchers. Only the "
            "providers-remove command deletes leftover provider "
            "runtime folders, and server_info reports enabled provider "
            "keys once as unused.",
        }
    ],
    "removedPartStores": {
        "bytes": 6,
        "count": 2,
        "notice": "The install deleted none of them: `agents-remember "
        "removed-stores --dry-run` lists them, and without `--dry-run` "
        "the command removes them on your word",
        "storedGates": "No part of the product decides a stored gate any more: "
        "`agents-remember removed-stores` names the gate logs and "
        "removes them on your word",
    },
    "reportPath": "/tmp/ar-answer-cases/coord/report.json",
    "summary": {
        "copiedFiles": 18,
        "createdDirs": 43,
        "dependencyRuns": 0,
        "removedPaths": 0,
        "replacedLinks": 0,
        "unchangedFiles": 0,
        "unusedProviderKeys": [],
    },
}


_INSTALL_FIELDS = {
    "ok": "stop when the run failed; continue to provider cleanup when it passed",
    "coordinationRoot": "confirm the run targeted this coordination root",
    "dryRun": "confirm the run wrote nothing before treating it as a preview",
    "experiment": "confirm which experiment selection the run applied",
    "host": "check the host settings step before starting agents on it",
    "includeBenchmarks": "confirm the knobs that ran before rerunning with different ones",
    "installProviderDeps": "confirm the knobs that ran before rerunning with different ones",
    "summary": "read how many paths the run created, copied, replaced, or removed",
    "messages": "read the step notes the summary counts omit",
    "reportPath": "open the full install report",
    "providerWatcherRebind": "see whether watcher recovery was attempted and which phases ran",
    "recoveryActions": "perform the recovery actions the run lists",
    "removedPartStores": "tell the user that stores of removed parts remain and which command lists or removes them",
    "log": "open the detail file holding the whole install result",
}


# MIK-R112 rule 9 final rows: every tool rule 7 gives L112 that is not
# removed. server_info is an exception passthrough (the rule-4 table bounds
# it at 4,000 with its reason); runtime_install holds a shaped row with
# fields, caller-step uses, and measured cases.
L112_FINAL_ROWS: dict[str, ToolAnswerRow] = {
    "server_info": ToolAnswerRow(
        tool="server_info",
        operations=(None,),
        fields=None,
        uses={},
        bound=bound_for("server_info"),
        interim_leaf=None,
        ordinary_case=None,
        largest_case=None,
    ),
    "runtime_install": _l112_shaped_row(
        "runtime_install",
        _INSTALL_FIELDS,
        bound_for("runtime_install"),
        _INSTALL_ORDINARY_CASE,
        _INSTALL_LARGEST_CASE,
    ),
}


# L109's shaped rows (MIK-R109 rules 1-4): the first shaped rows of the
# table. Each bound is set from the largest case the tool's own limits
# allow, within twice that case and at most 4,000. The cases are recorded
# wire answers (see fixtures/l109_tool_answers/PROVENANCE.md), measured here
# by the product counter -- never claimed integers.
_L109_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "l109_tool_answers"


# The governed fixture contract (evidence-lifecycle.toml): every retained
# case file by its repository-relative literal, so the catalog's
# source-derived ownership sees the consumer. `_l109_case` reads through
# this mapping, never a bare stem.
_L109_CASE_FILES = {
    "read_ar_files_ordinary": "mcp/tests/fixtures/l109_tool_answers/read_ar_files_ordinary.json",
    "read_ar_files_largest": "mcp/tests/fixtures/l109_tool_answers/read_ar_files_largest.json",
    "knowledge_read_ordinary": "mcp/tests/fixtures/l109_tool_answers/knowledge_read_ordinary.json",
    "knowledge_read_largest": "mcp/tests/fixtures/l109_tool_answers/knowledge_read_largest.json",
    "knowledge_diff_ordinary": "mcp/tests/fixtures/l109_tool_answers/knowledge_diff_ordinary.json",
    "knowledge_diff_largest": "mcp/tests/fixtures/l109_tool_answers/knowledge_diff_largest.json",
    "context_packet_ordinary": "mcp/tests/fixtures/l109_tool_answers/context_packet_ordinary.json",
    "context_packet_largest": "mcp/tests/fixtures/l109_tool_answers/context_packet_largest.json",
}


def _l109_case(name: str) -> dict[str, Any]:
    return json.loads((_L109_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


L109_SHAPED_ROWS: dict[tuple[str, str | None], ToolAnswerRow] = {
    ("read_ar_files", None): ToolAnswerRow(
        tool="read_ar_files",
        operations=(None,),
        fields=frozenset(
            {
                "state",
                "repoId",
                "codeState",
                "codeTree",
                "memoryState",
                "worktreeScope",
                "sourceRoot",
                "memoryRoot",
                "files",
                "overviews",
                "statesProblem",
                "routeIndexStale",
                "routeIndexRefresh",
                "refusalCode",
                "refusalDetail",
            }
        ),
        uses={
            "state": "the caller tells named from refused before it opens anything",
            "repoId": "the caller checks the answer names the repository it asked about",
            "codeState": "the caller learns what code the states were measured on",
            "codeTree": "the caller pins the tree the card states hold for",
            "memoryState": "the caller learns whether cards could be named at all",
            "worktreeScope": "the caller learns whose worktrees the answer read: a leaf's or the official line",
            "sourceRoot": "the caller opens the code root the answer measured",
            "memoryRoot": "the caller opens the memory root the cards were named in",
            "files": "the per-path naming the caller opens and acts on; action entries flag title fallback when no section scored",
            "overviews": "the overview files the caller opens once for the batch",
            "statesProblem": "the caller learns why no state could be measured",
            "routeIndexStale": "the caller learns the route index missed a card the answer served",
            "routeIndexRefresh": "the operation the caller runs to bring the route index back",
            "refusalCode": "the refusal identity the caller branches on",
            "refusalDetail": "what the caller can do instead of this call",
        },
        bound=2400,
        interim_leaf=None,
        ordinary_case=_l109_case("read_ar_files_ordinary"),
        largest_case=_l109_case("read_ar_files_largest"),
    ),
    ("knowledge_read", None): ToolAnswerRow(
        tool="knowledge_read",
        operations=(None,),
        fields=frozenset(
            {
                "state",
                "view",
                "repositoryId",
                "snapshot",
                "completeWithinDeclaredScope",
                "continuation",
                "page",
                "threshold",
                "payload",
                "currentness",
                "proofs",
                "families",
                "memoryTree",
                "memoryCommit",
                "memoryDirty",
                "report",
                "indexComplete",
                "refusalCode",
                "refusalDetail",
            }
        ),
        uses={
            "state": "the caller tells a view from a page from a refusal, and how to continue each",
            "view": "the caller learns which view the rows belong to",
            "repositoryId": "the namespace the caller resolves the text IDs in",
            "snapshot": "the snapshot the caller cites the rows from",
            "completeWithinDeclaredScope": "the caller learns whether the page is complete in scope",
            "continuation": "the token the caller passes to read the next page",
            "page": "the paging position the caller resumes from",
            "threshold": "the paging bound the caller sizes its next read by",
            "payload": "the named rows the caller works through",
            "currentness": "the short-form states the caller acts on without opening files",
            "proofs": "the proof entries the caller cites for the returned claims",
            "families": "the families the caller reads next for an invariant",
            "memoryTree": "the memory root and tree key the caller resolves locations against",
            "memoryCommit": "the commit the caller knows the locations were read from",
            "memoryDirty": "whether uncommitted memory changes qualify the named locations",
            "report": "the work-through list's location, which the caller opens",
            "indexComplete": "the caller learns whether the index behind the read is complete",
            "refusalCode": "the refusal identity the caller branches on",
            "refusalDetail": "what the caller can do instead of this call",
        },
        bound=4000,
        interim_leaf=None,
        ordinary_case=_l109_case("knowledge_read_ordinary"),
        largest_case=_l109_case("knowledge_read_largest"),
    ),
    ("knowledge_diff", None): ToolAnswerRow(
        tool="knowledge_diff",
        operations=(None,),
        fields=frozenset(
            {
                "state",
                "memoryRoot",
                "beforeRevision",
                "afterRevision",
                "beforeTree",
                "afterTree",
                "counts",
                "decisionRecords",
                "decisionRecordCount",
                "report",
                "refusalCode",
                "refusalDetail",
            }
        ),
        uses={
            "state": "the caller tells a comparison from a refusal",
            "memoryRoot": "the caller learns which repository was compared",
            "beforeRevision": "the caller cites the named before side",
            "afterRevision": "the caller cites the named after side",
            "beforeTree": "the exact before tree the caller records",
            "afterTree": "the exact after tree the caller records",
            "counts": "the change counts the caller triages by",
            "decisionRecords": "the changed records the caller reviews",
            "decisionRecordCount": "how many changed records there are in total",
            "report": "the full comparison, which the caller reads",
            "refusalCode": "the refusal identity the caller branches on",
            "refusalDetail": "what the caller can do instead of this call",
        },
        bound=1500,
        interim_leaf=None,
        ordinary_case=_l109_case("knowledge_diff_ordinary"),
        largest_case=_l109_case("knowledge_diff_largest"),
    ),
    ("context_packet", None): ToolAnswerRow(
        tool="context_packet",
        operations=(None,),
        fields=frozenset(
            {
                "contextPacketVersion",
                "ok",
                "roots",
                "task",
                "actions",
                "servingBuild",
                "pair",
            }
        ),
        uses={
            "contextPacketVersion": "the caller learns which answer contract to parse",
            "ok": "the caller tells a usable answer from a failure",
            "roots": "the workplaces the caller opens",
            "task": "the task and master the caller works under",
            "actions": "the states needing action, one line each",
            "servingBuild": "the serving build commit the caller cites when reporting defects",
            "pair": "the code and memory commits identifying what the caller sees",
        },
        bound=360,
        interim_leaf=None,
        ordinary_case=_l109_case("context_packet_ordinary"),
        largest_case=_l109_case("context_packet_largest"),
    ),
}


def build_tool_answer_table() -> dict[tuple[str, str | None], ToolAnswerRow]:
    """The rule-6 table: one row per authored tool (and per operation).

    Rows come only from the authored roster above plus the landed leaves'
    final rows (L109's four rebuilt reads, L111's shaped rows, L112's three
    retained tools), never synthesized from the registry: a tool without an
    authored entry has no row. A leaf that lands later and adds or changes a
    tool edits that roster, setting ``fields`` to the answer's field list with
    every use, the case sizes, and clearing ``interim_leaf``.
    """
    table: dict[tuple[str, str | None], ToolAnswerRow] = {}
    for tool in sorted(AUTHORED_INTERIM_OWNERS):
        operations: tuple[str | None, ...] = (None,)
        if tool == "task_doc":
            operations = tuple(sorted(set(VALID_OPERATIONS)))
        for operation in operations:
            table[(tool, operation)] = ToolAnswerRow(
                tool=tool,
                operations=operations,
                fields=None,
                uses={},
                bound=bound_for(tool, operation=operation),
                interim_leaf=AUTHORED_INTERIM_OWNERS[tool],
                ordinary_case=None,
                largest_case=None,
            )
    for tool in sorted(L112_FINAL_ROWS):
        table[(tool, None)] = L112_FINAL_ROWS[tool]
    for key, shaped in _shaped_answer_rows().items():
        table[key] = shaped
    for key in sorted(L109_SHAPED_ROWS):
        table[key] = L109_SHAPED_ROWS[key]
    _apply_l111_shaped_rows(table)
    return table


def authored_row_owners() -> dict[str, str | None]:
    """Every authored row's owner: the interim leaf, or None for landed finals."""
    owners: dict[str, str | None] = dict(AUTHORED_INTERIM_OWNERS)
    for tool in L112_FINAL_ROWS:
        owners[tool] = None
    for tool in L110_SHAPED_TOOLS:
        owners[tool] = None
    # MIK-R196 rule 11: the new tool's row is authored in L110_ROW_DATA at this leaf, so it owns no
    # interim leaf and stands as a final row.
    owners["user_settings_init"] = None
    for tool, _operation in L109_SHAPED_ROWS:
        owners[tool] = None
    # L111's landed finals own no leaf; tools the interim map still names
    # keep their leaf (their table rows are interim until the applier covers
    # them, and the implementation's interim map agrees).
    for tool in L111_SHAPED_TOOLS:
        owners.setdefault(tool, None)
    return owners


# MIK-R110's ten retained landed rows: fields, uses, bounds, and the real
# tool paths their ordinary and largest cases are derived through live in
# tool_answer_l110_rows.py. The derivation and its proofs live in the three
# test_tool_answer_rows_* modules; this table carries no static payloads for
# them, so no hand-authored dictionary can drift from production.
# MIK-R112 retired drift_check and citation_migrate as registered tools, so
# they hold no table row; L110_ROW_DATA keeps their entries for the
# builder-level derivation proofs, and L110_SHAPED_TOOLS is the rowed ten.
L110_SHAPED_TOOLS: tuple[str, ...] = tuple(
    tool for tool in L110_ROW_DATA if tool not in ("drift_check", "citation_migrate")
)


def _shaped_answer_rows() -> dict[tuple[str, str | None], ToolAnswerRow]:
    """The rows whose owning leaf has landed: shaped answers with fields and uses."""

    rows: dict[tuple[str, str | None], ToolAnswerRow] = {}
    for tool in L110_SHAPED_TOOLS:
        fields, uses, bound, paths = L110_ROW_DATA[tool]
        rows[(tool, None)] = ToolAnswerRow(
            tool=tool,
            operations=(None,),
            fields=fields,
            uses=dict(uses),
            bound=bound,
            interim_leaf=None,
            ordinary_case=None,
            largest_case=None,
            production_paths=tuple(paths),
        )
    return rows


def check_tool_answer_roster(registered: set[str], owners: Mapping[str, str | None]) -> None:
    """Fail unless every registered tool has exactly one authored row."""
    missing = sorted(registered - set(owners))
    extra = sorted(set(owners) - registered)
    if missing or extra:
        raise AssertionError(f"roster mismatch: missing rows {missing}; extra rows {extra}")


def validate_tool_answer_row(key: tuple[str, str | None], row: ToolAnswerRow) -> list[str]:
    """Check one row against the rule-6 gates; return failure strings."""
    failures = _validate_row_identity(key, row)
    if row.fields is None:
        if row.interim_leaf is None:
            # Unshaped exception passthrough (rule 4): the exception table
            # already bounds it with its reason, so the rule-6 row carries
            # no fields, uses, or cases. Anything else without a leaf fails.
            if key not in TOOL_ANSWER_EXCEPTIONS:
                failures.append(f"row {key}: interim row with no owning leaf")
            if row.uses:
                failures.append(f"row {key}: interim row with stated uses")
            if row.ordinary_case is not None or row.largest_case is not None:
                failures.append(f"row {key}: interim row with case payloads")
            return failures
        if row.interim_leaf not in INTERIM_LEAVES:
            failures.append(f"row {key}: interim row with no owning leaf")
        if row.uses:
            failures.append(f"row {key}: interim row with stated uses")
        if row.ordinary_case is not None or row.largest_case is not None:
            failures.append(f"row {key}: interim row with case payloads")
        return failures
    failures.extend(_validate_shaped_row(key, row))
    return failures


def _validate_row_identity(key: tuple[str, str | None], row: ToolAnswerRow) -> list[str]:
    """Check one row's bound."""
    failures: list[str] = []
    if row.bound > TOOL_ANSWER_EXCEPTION_MAX_TOKENS:
        failures.append(f"row {key}: bound over 4,000")
    return failures


def validate_row_case(
    key: tuple[str, str | None],
    row: ToolAnswerRow,
    case_name: str,
    case: Mapping[str, Any],
) -> list[str]:
    """Check one row-case payload: measured bound, code, and field cover."""
    failures: list[str] = []
    if not isinstance(case, Mapping):
        return [f"row {key}: {case_name} is not a mapping"]
    code = case.get("code")
    if "code" in case and not (isinstance(code, str) and is_valid_tool_code(code)):
        failures.append(f"row {key}: {case_name} carries an invalid code")
    measured = count_response_tokens(dict(case))
    if measured > row.bound:
        failures.append(
            f"row {key}: {case_name} measures {measured} over the row bound {row.bound}"
        )
    allowed = set(row.fields or ()) | ROW_CASE_FRAMEWORK_KEYS
    for field in sorted(case):
        if field not in allowed:
            failures.append(f"row {key}: {case_name} holds a field outside its row")
    return failures


def _validate_shaped_row(key: tuple[str, str | None], row: ToolAnswerRow) -> list[str]:
    """Check one shaped row: no interim owner, uses, and proven cases."""
    assert row.fields is not None
    failures: list[str] = []
    if row.interim_leaf is not None:
        failures.append(f"row {key}: shaped row still names an interim leaf")
    for field in sorted(row.fields):
        use = row.uses.get(field)
        if not isinstance(use, str) or not use:
            failures.append(f"row {key}: field {field!r} has no stated use")
    for field in sorted(row.uses):
        if field not in row.fields:
            failures.append(f"row {key}: use for unknown field {field!r}")
    if row.ordinary_case is None or row.largest_case is None:
        # A production-proven row names the real tool paths its cases are
        # derived through instead of carrying static payloads; the derivation
        # tests prove fields, uses, bounds, and guard behavior live.
        if not row.production_paths:
            failures.append(f"row {key}: shaped row without case payloads")
        return failures
    failures.extend(validate_row_case(key, row, "ordinary case", row.ordinary_case))
    failures.extend(validate_row_case(key, row, "largest case", row.largest_case))
    largest_measured = count_response_tokens(dict(row.largest_case))
    if row.bound > 2 * largest_measured:
        failures.append(f"row {key}: bound over twice the largest case")
    return failures


TOOL_ANSWER_TABLE = build_tool_answer_table()


def _big_blob() -> str:
    # Varied content: long runs of one character compress well under
    # o200k_base, so the overflow cases use varied text instead.
    return "".join(f"{index:08d}-abcdefgh;" for index in range(3000))


def _server_info_case() -> dict[str, Any]:
    # The smallest call of a server (MIK-R112: `ping` is removed,
    # `server_info` answers the same and more): a valid server_info
    # payload with the fields its response model requires.
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


assert set(_L109_CASE_FILES) == {
    "read_ar_files_ordinary",
    "read_ar_files_largest",
    "knowledge_read_ordinary",
    "knowledge_read_largest",
    "knowledge_diff_ordinary",
    "knowledge_diff_largest",
    "context_packet_ordinary",
    "context_packet_largest",
}, "the fixture contract lists every row case"
