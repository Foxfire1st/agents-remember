"""The tool-answer bound, exception table, and guard shape (MIK-R108).

A tool answer holds the outcome, what the caller needs for its next step, and
where the record of the call lies -- nothing else. The whole result a function
behind a tool computes and the answer that leaves the server for an agent are
two things: the application boundary (``application/tool_response.py``) makes
the answer from the whole result, while the dashboard and the command line keep
getting the whole result from the same functions.

This module is pure (no filesystem, no clock): the bound, the exception table,
the envelope strip, the list cap, and the overflow short form. The daily log
that records every call lives in ``application/tool_answer_log.py``.

Rule references below are MIK-R108@v1 rules.
"""

from __future__ import annotations

from collections.abc import Callable, Container, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from agents_remember.models.tools.tool_answer_short_fields import (
    TOOL_ANSWER_LIST_MAX,
    TOOL_ANSWER_OVERFLOW_MAX_TOKENS,
    ShortFormField,
    fit_declared_short_fields,
    leading_declared_fields,
)

# Rule 4: one constant bounds every answer, counted with the product's counter
# (``models/tokens.py``).
TOOL_ANSWER_MAX_TOKENS = 1500
# Rule 4: no bound in the exception table is above this.
TOOL_ANSWER_EXCEPTION_MAX_TOKENS = 4000
# Status-answer ruling: a status answer keeps the default bound of 1,500.
# The five landed status answers whose full entry lists exceed it carry at
# most this many entries per list instead -- smaller entries first, then
# fewer entries -- with the total count and the whole-list location beside
# them, so the caller still sizes the work and opens the full rows.
TOOL_ANSWER_STATUS_ENTRY_MAX = 10
# Rule 2: answer fields that hold a location name a file the caller can open
# as written. Strings at most this long stay inline in the overflow short
# form; the guard (rule 4) keeps numbers, booleans and short strings.
TOOL_ANSWER_SHORT_STRING_MAX = 200
# Status-answer ruling: blocking-entry reasons longer than this are shortened
# at a word boundary in the answer; the full sentence stays in the whole
# result the log records.
TOOL_ANSWER_REASON_MAX_CHARS = 200
# Rule 4: steps 1 and 2 of the short form (the fixed part and the location)
# are within this many tokens, whatever the log folder is.
TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS = 250
# Rule 4: a long error or warning is cut to this many characters first, then further
# from its end until the measured form fits; the detail file holds it whole.
TOOL_ANSWER_ERROR_MAX_CHARS = 600
# Rule 4: a code is an identifier in ASCII of at most this many characters
# (at most as many tokens). A longer or non-ASCII code is a defect of the
# tool that made it and is not passed on.
TOOL_CODE_MAX_CHARS = 120
_CODE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

# Rule 4: every ``logError`` is one of these fixed sentences. A sentence
# holds no path and no text of an exception; the detail file and the log
# line carry the locations and the sizes instead.
LOG_ERROR_LOCATION_OMITTED = "the result is kept: today's line of the tool log names its file."
LOG_ERROR_DETAIL_TOO_LARGE = (
    "the whole result was not recorded: it is larger than the detail bound."
)
LOG_ERROR_BUDGET_DETAIL = "the whole result was not recorded: the log folder is over its budget."
LOG_ERROR_DETAIL_UNWRITABLE = (
    "the whole result was not recorded: the detail file cannot be written."
)
LOG_ERROR_DETAIL_UNSERIALIZABLE = "the whole result was not recorded: it is not serializable."
LOG_ERROR_BUDGET_LINE = "the tool call was not recorded: the log folder is over its budget."
LOG_ERROR_LINE_UNWRITABLE = "the tool call was not recorded: the log file cannot be written."
LOG_ERROR_FOLDER_UNWRITABLE = "the tool call was not recorded: the log folder cannot be made."
LOG_ERROR_LINE_UNSERIALIZABLE = "the tool call was not recorded: the log line is not serializable."


def is_valid_tool_code(code: Any) -> bool:
    """Whether a refusal code may be passed on to the caller (rule 4)."""
    return (
        isinstance(code, str)
        and 1 <= len(code) <= TOOL_CODE_MAX_CHARS
        and all(char in _CODE_CHARS for char in code)
    )


# Rule 2, third point: the envelope this leaf removes from every answer. The
# count goes to the log line instead; ``operation`` repeats the tool's name,
# which the protocol already tells the caller.
ENVELOPE_STRIPPED_FIELDS = ("tokens", "tokenizer", "tokenCountExact", "operation")

# Rule 4: the exception table. Tool (and operation), bound, reason. No bound
# is above TOOL_ANSWER_EXCEPTION_MAX_TOKENS; the test suite proves it.
# Its first rows are ``role_message`` with ``wait`` (with wait the answer is
# another agent's reply cut at the message limit, the whole reply filed beside
# the message and named by the answer -- MIK-R117 rule 6) and ``server_info``
# (the designated place for roots, build and identity -- rule 2, second point). A status answer keeps the default
# bound of 1,500: no landed memory tool stands here, by the Architect's
# status-answer ruling (an exception row comes to the Architect first).
TOOL_ANSWER_EXCEPTIONS: dict[tuple[str, str | None], tuple[int, str]] = {
    ("role_message", None): (
        4000,
        "the answer is another agent's reply, not a shaped result; with wait a reply "
        "longer than the message limit is cut in the answer and filed whole beside "
        "the message, named by the answer",
    ),
    ("server_info", None): (
        4000,
        "the designated place for roots, build and identity (rule 2)",
    ),
    # L109: each bound is set from the largest case of its row, within twice
    # that case and at most 4,000. read_ar_files' short answers of briefs are
    # fitted inside its bound before the answer leaves.
    ("read_ar_files", None): (
        2400,
        "twenty named paths with their cards, counts and overviews",
    ),
    ("knowledge_read", None): (
        4000,
        "the largest family-complete page in named rows with short currentness",
    ),
    ("knowledge_diff", None): (
        1500,
        "the compared trees with counts and up to twenty named records",
    ),
    ("context_packet", None): (
        360,
        "roots, task, action lines and short values under 600 tokens",
    ),
}

# MIK-R110 rule 6: the governed per-tool answer bounds for the landed memory
# tools. Each bound equals its shaped row's bound in the answer table, and the
# guard enforces it on every answer the tool delivers -- the table's measured
# bounds and the production bounds are one number, not two.
SHAPED_TOOL_BOUNDS: dict[tuple[str, str | None], int] = {
    ("citation_fix", None): 1500,
    # MIK-R198 rule 6a: the note-only revision answers the one reference it revised, its two
    # digests and its refusal vocabulary; the sidecar's rows stay in the log.
    ("citation_note_revision", None): 1200,
    # MIK-R196 rule 11: the settings answer carries the state, the two key lists, the harness of
    # every role and its sources; its largest measured answer is 572 chars.
    ("user_settings_init", None): 900,
    ("memory_quality_check", None): 1500,
    # MIK-R73 rule 4: the answer carries every family's routes beside the findings, so this
    # tool's own bound is the one that grew; every other tool keeps the default.
    ("knowledge_integrity_check", None): 1700,
    # MIK-R73: the writer answers with its outcome, its counts and the first
    # problems it found; the whole report is the file the answer names.
    ("knowledge_write", None): 1500,
    ("route_index_refresh", None): 400,
    ("drift_check", None): 1500,
    ("citation_migrate", None): 1500,
    ("memory_init", None): 800,
    ("memory_baseline_status", None): 600,
    ("memory_baseline_adopt", None): 700,
    ("memory_carryover_plan", None): 500,
    ("memory_carryover_apply", None): 800,
    ("curator_coherence", None): 850,
    # MIK-R111 has landed: its tools carry governed shaped bounds here,
    # measured in freeze-manifest-a06.md's evidence (see the note below).
    ("task_doc", None): TOOL_ANSWER_MAX_TOKENS,
    ("worktree_start", None): 600,
    ("worktree_attach", None): 500,
    ("worktree_status", None): 600,
    ("worktree_sync", None): 400,
    ("worktree_pause", None): 500,
    ("worktree_closeout_preview", None): 1000,
    ("worktree_closeout_apply", None): 600,
    ("worktree_integrate", None): 500,
    ("worktree_checkpoint_landing", None): 600,
    ("worktree_record_landing", None): 700,
    ("worktree_operation_control", None): 500,
    ("worktree_cleanup", None): 500,
    ("worktree_abandon", None): 600,
    ("direct_landing", None): 500,
    ("lifecycle_finalize_task", None): 600,
    ("closeout_queue", None): 500,
    ("task_reopen", None): 900,
    ("role_start", None): 600,
    ("role_message", None): 150,
    # A waited reply rides the exception bound (MIK-R111 rule 5): over it the
    # reply goes to the detail file and the answer keeps its beginning. The
    # wait row documents that guard, not the shaped bound of plain answers.
    ("role_message", "wait"): 4000,
    ("direct_execution_grant", None): 150,
    ("worktree_enclosure_adopt", None): 900,
}

# Rule 7: until its leaf lands, a tool stands in the table as an interim row
# (bound 4,000, no list of fields, the name of its leaf). The guard holds it
# to the bound. L109 has landed: its four rebuilt reads hold final rows in
# TOOL_ANSWER_EXCEPTIONS above. The rows below name the leaves still to land.
INTERIM_TOOL_LEAF: dict[str, str] = {
    # L100: native tools land with their own leaf; until it lands they stand
    # as interim rows here.
    "retire_child": "L100",
    "role_start_set": "L100",
    # L111: the task and landing tools answer with the state and the next step.
    "worktree_start": "L111",
    "worktree_attach": "L111",
    "worktree_status": "L111",
    "worktree_sync": "L111",
    "worktree_pause": "L111",
    "worktree_closeout_preview": "L111",
    "worktree_closeout_apply": "L111",
    "worktree_enclosure_adopt": "L111",
    "worktree_integrate": "L111",
    "worktree_checkpoint_landing": "L111",
    "worktree_record_landing": "L111",
    "worktree_operation_control": "L111",
    "worktree_cleanup": "L111",
    "worktree_abandon": "L111",
    "direct_landing": "L111",
    "task_doc": "L111",
    "lifecycle_finalize_task": "L111",
    "closeout_queue": "L111",
    "task_reopen": "L111",
    "role_start": "L111",
    "role_message": "L111",
    # L183: the review page's explainer control (explainer_control.py) answers with the
    # environment's state and the next step, and stands interim until its leaf lands.
    "explainer_control": "L183",
}
INTERIM_LEAVES = ("L100", "L110", "L111", "L112", "L183")


# Rule 9 (L112): the landed leaf's final rows carry their own bounds. The
# guard stays the net L108 built: it holds every answer to its row's bound
# through this lookup, and the overflow short form is unchanged.
L112_FINAL_BOUNDS = {
    "runtime_install": 600,
}


# Rule 9 (L112): the final answer projection for runtime_install. The
# delivered answer holds exactly these fields: the outcome, what the caller
# needs for its next step, and where the record lies. The whole result keeps
# everything (``operation``, untruncated runs) for the report and the log.
RUNTIME_INSTALL_ANSWER_FIELDS = frozenset(
    {
        "ok",
        "coordinationRoot",
        "dryRun",
        "experiment",
        "host",
        "runningServers",
        "dashboard",
        "includeBenchmarks",
        "installProviderDeps",
        "summary",
        "messages",
        "reportPath",
        "providerWatcherRebind",
        "recoveryActions",
        "removedPartStores",
        "sharedHeldStores",
        "log",
    }
)

# MIK-R111: these tools have shaped rows in the rule-6 table and name no
# interim leaf. ``task_doc`` carries its shaped bound; the rest keep the
# interim bound as their limit.
L111_SHAPED_TOOLS = frozenset(
    {
        "worktree_start",
        "worktree_attach",
        "worktree_status",
        "worktree_sync",
        "worktree_pause",
        "worktree_closeout_preview",
        "worktree_closeout_apply",
        "worktree_integrate",
        "worktree_checkpoint_landing",
        "worktree_record_landing",
        "worktree_operation_control",
        "worktree_cleanup",
        "worktree_abandon",
        "direct_landing",
        "task_doc",
        "lifecycle_finalize_task",
        "closeout_queue",
        "task_reopen",
        "role_start",
        "role_message",
        "direct_execution_grant",
        "worktree_enclosure_adopt",
    }
)

# MIK-R111: the shaped bound of a landed tool. A ``task_doc`` write answers
# within 100 tokens ordinary; the bound holds the largest dry run (the diff
# alone may ride 1,000 tokens) at the rule-4 default for shaped answers.
# Every other bound below is a genuine measured maximum, not a wholesale
# ceiling: each was measured on a real fixture through the production MCP
# path (success, drift, thirty-path previews, diverged sync, dry refusals),
# plus a 300-token root-variance margin calibrated from the observed
# deep-root vs short-root deltas (worst +284), rounded up to the next 100.
# Measured maxima (short roots): start 260, status 273, preview 653,
# integrate 187, cleanup 142, checkpoint 277, abandon 232, pause 172,
# attach 190, control 122, record 366, sync 81, apply 236, finalize 259,
# reopen 534, adopt 529, direct 141. The role_start missing-file answer measured
# 236 tokens at a short root and 398 at a deep root, including the log path.
# The short-root measurement plus 300 rounds up to 600. Role_message has a
# shaped row of 150 and takes the 4000-token exception above.
# ``task_doc`` keeps the rule-4 default: its genuine ceiling is the
# 1,000-token dry-diff cap plus answer overhead. ``closeout_queue`` answers
# live: status measures 82 short-root (118 undeclared) and 167 under a
# 200-character root with two declared masters, so 500 covers the genuine
# class with root-variance margin; the 256-problem model ceiling stays under
# the guard alone.


def bound_for(tool_name: str, operation: str | None = None) -> int:
    """Return the answer bound in tokens for one tool (and operation).

    Exceptions first, then the governed shaped bound for tools whose leaf has
    landed (MIK-R110 rule 6: this leaf), then the interim 4,000 for tools
    whose leaf has not landed yet (rules 4 and 7), then L112's final rows
    (rule 9), then the 4,000 net for anything without an entry: the guard
    stays the net L108 built.
    """
    for key in ((tool_name, operation), (tool_name, None)):
        if key in TOOL_ANSWER_EXCEPTIONS:
            return TOOL_ANSWER_EXCEPTIONS[key][0]
    if (tool_name, operation) in SHAPED_TOOL_BOUNDS:
        return SHAPED_TOOL_BOUNDS[(tool_name, operation)]
    if (tool_name, None) in SHAPED_TOOL_BOUNDS:
        return SHAPED_TOOL_BOUNDS[(tool_name, None)]
    if tool_name in INTERIM_TOOL_LEAF:
        return TOOL_ANSWER_EXCEPTION_MAX_TOKENS
    if tool_name in L112_FINAL_BOUNDS:
        return L112_FINAL_BOUNDS[tool_name]
    return TOOL_ANSWER_EXCEPTION_MAX_TOKENS


def shaped_bound_for(tool_name: str, operation: str | None = None) -> int | None:
    """Return the governed shaped bound for one landed tool, else None."""
    if (tool_name, operation) in SHAPED_TOOL_BOUNDS:
        return SHAPED_TOOL_BOUNDS[(tool_name, operation)]
    return SHAPED_TOOL_BOUNDS.get((tool_name, None))


def interim_leaf_for(tool_name: str) -> str | None:
    """Return the leaf that owns one tool's answer, while it is interim.

    Every interim row names its exact rule-7 leaf. A tool with no
    interim row -- a landed leaf's final tool, or an exception passthrough
    like ``server_info`` -- has no owning leaf. A tool with a governed shaped
    bound has landed and owns its answer itself, so it names no leaf either.
    """
    if tool_name in INTERIM_TOOL_LEAF:
        return INTERIM_TOOL_LEAF[tool_name]
    if (tool_name, None) in SHAPED_TOOL_BOUNDS or tool_name in (
        name for name, _op in SHAPED_TOOL_BOUNDS
    ):
        return None
    if (tool_name, None) in TOOL_ANSWER_EXCEPTIONS or tool_name in (
        name for name, _op in TOOL_ANSWER_EXCEPTIONS
    ):
        return None
    return None


def strip_envelope(answer: Mapping[str, Any]) -> dict[str, Any]:
    """Remove the four envelope fields from one answer (rule 2).

    ``code`` and ``error`` are never envelope: they are the refusal, and the
    guard never drops them.
    """
    return {key: value for key, value in answer.items() if key not in ENVELOPE_STRIPPED_FIELDS}


def _is_short_scalar(value: Any) -> bool:
    """Whether a value survives into the overflow short form (rule 4)."""
    if isinstance(value, bool | int | float):
        return True
    return isinstance(value, str) and len(value) <= TOOL_ANSWER_SHORT_STRING_MAX


def truncate_lists(value: Any, limit: int = TOOL_ANSWER_LIST_MAX) -> Any:
    """Cut every list longer than ``limit`` to its count plus first entries.

    The answer keeps the count and the first entries in the order the next
    step needs; the location of the whole list is the answer-level ``log``,
    which names the detail file holding the whole result. Mappings and the
    retained items of lists are walked, so a list nested in a kept entry is
    cut the same way; anything else passes through.
    """
    if isinstance(value, list):
        if len(value) > limit:
            return {
                "count": len(value),
                "items": [truncate_lists(item, limit) for item in value[:limit]],
                "truncated": True,
            }
        return [truncate_lists(item, limit) for item in value]
    if isinstance(value, dict):
        return {key: truncate_lists(item, limit) for key, item in value.items()}
    return value


def next_from_next_step(next_step: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Translate the lifecycle next-step hint into the answer's ``next``.

    Rule 1: the one call or action that follows, with its arguments, where
    there is exactly one. The producers keep writing ``nextStep`` onto the
    validated response; the answer carries it as ``next``.
    """
    if not next_step:
        return None
    summary = next_step.get("summary")
    tool = next_step.get("nextTool")
    args = next_step.get("nextArgs")
    next_value: dict[str, Any] = {}
    if isinstance(summary, str) and summary:
        next_value["summary"] = summary
    if isinstance(tool, str) and tool:
        next_value["tool"] = tool
    if isinstance(args, dict) and args:
        next_value["args"] = args
    return next_value or None


def _fixed_short_part(
    answer: Mapping[str, Any],
    *,
    answer_tokens: int,
) -> dict[str, Any]:
    """Build the short form's fixed part, always whole (rule 4).

    A valid ``code`` is never dropped and never cut. A longer or non-ASCII
    code is a defect of the tool that made it: the answer keeps its ``ok``
    and carries ``code: "invalid-code"`` instead.
    """
    short: dict[str, Any] = {}
    if "ok" in answer:
        short["ok"] = answer["ok"]
    if "code" in answer:
        short["code"] = answer["code"] if is_valid_tool_code(answer["code"]) else "invalid-code"
    if "buildCurrentness" in answer:
        short["buildCurrentness"] = answer["buildCurrentness"]
    if isinstance(answer.get("runningOperation"), str):
        # The status answer puts a queued or running direct landing in one compact
        # sentence. Keep that line with the fixed fields of the short form so the
        # caller can identify the live generation even when reading the full result
        # requires opening its log.
        short["runningOperation"] = answer["runningOperation"][:TOOL_ANSWER_SHORT_STRING_MAX]
    short["overflow"] = True
    short["answerTokens"] = answer_tokens
    return short


@dataclass(frozen=True)
class ShortFormLocation:
    log: str | None = None
    log_error: str | None = None


_NO_SHORT_FORM_LOCATION = ShortFormLocation()


def _set_short_location(short: dict[str, Any], location: ShortFormLocation) -> None:
    """Keep the full result's precise location and any reported logging failure."""
    if location.log is not None:
        short["log"] = location.log
    if location.log_error is not None:
        short["logError"] = location.log_error
    if location.log is None:
        short["resultKept"] = False


def _cut_hint(log: str | None, log_error: str | None) -> dict[str, Any]:
    """The last field of every cut answer: that it was cut, and where the whole result is.

    Where the result was recorded, the hint names its file. Otherwise it says why no file
    holds the result, so a reader is never sent to an empty location.
    """
    if log is not None:
        return {
            "cut": True,
            "log": log,
            "readIt": "the whole result is in this file; read it there",
        }
    return {
        "cut": True,
        "log": None,
        "readIt": "no file holds the whole result",
        "why": log_error or LOG_ERROR_BUDGET_DETAIL,
    }


def _fit_short_text(
    short: dict[str, Any],
    field: str,
    value: Any,
    count: Callable[[Mapping[str, Any]], int],
) -> bool:
    """Cut an error or warning to 600 characters, then to a measured prefix."""

    if not isinstance(value, str):
        short[field] = value
        return False
    marker = f"{field}Cut"
    cut = len(value) > TOOL_ANSWER_ERROR_MAX_CHARS
    text = value[:TOOL_ANSWER_ERROR_MAX_CHARS]
    short[field] = text
    if cut:
        short[marker] = True
    if count(short) <= TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
        return cut
    # The cut marker rides before the search: it is part of the measured
    # form, and adding it after the search would push the form back over.
    short[marker] = True
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        short[field] = text[:mid]
        if count(short) <= TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
            low = mid
        else:
            high = mid - 1
    short[field] = text[:low]
    return True


def _fit_warning(
    short: dict[str, Any], warning: str, count: Callable[[Mapping[str, Any]], int]
) -> None:
    """Keep a warning prefix or just its marker when no text fits."""

    if _fit_short_text(short, "warning", warning, count) and not short["warning"]:
        del short["warning"]


def _eligible_short_scalars(
    answer: Mapping[str, Any], reserved: Container[str]
) -> Iterator[tuple[str, Any]]:
    """Yield the short-form scalar candidates in answer order (rule 4)."""
    for key, value in answer.items():
        if key in reserved or key in (
            "log",
            "logError",
            "resultKept",
            "overflow",
            "answerTokens",
            "errorCut",
            "warning",
            "warningCut",
            "droppedFields",
        ):
            continue
        if _is_short_scalar(value):
            yield key, value


def _fit_scalars(
    short: dict[str, Any],
    items: Iterator[tuple[str, Any]],
    count: Callable[[Mapping[str, Any]], int],
) -> tuple[int, list[str]]:
    """Keep candidates only while the form stays within its bound."""
    dropped = 0
    kept: list[str] = []
    for key, value in items:
        short[key] = value
        if count(short) > TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
            del short[key]
            dropped += 1
        else:
            kept.append(key)
    return dropped, kept


def _document_notice(answer: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep the landing's debt or failed-computation notice when its detailed lists overflow."""

    notice = answer.get("staleDocuments")
    if not isinstance(notice, Mapping):
        return None
    result = {key: notice[key] for key in ("count", "state") if key in notice}
    result["read"] = {"view": "dependent", "staleOnly": True}
    return result


def short_form(
    answer: Mapping[str, Any],
    *,
    answer_tokens: int,
    location: ShortFormLocation = _NO_SHORT_FORM_LOCATION,
    count: Callable[[Mapping[str, Any]], int] = lambda _payload: 0,
    declared_fields: tuple[ShortFormField, ...] = (),
) -> tuple[dict[str, Any], int, bool]:
    """Build the overflow short form for an answer over its bound (rule 4).

    The guard first reserves the required envelope and measures the selected
    fields against ``TOOL_ANSWER_OVERFLOW_MAX_TOKENS``:

    1. The fixed part: ``ok``, ``code`` where present, ``overflow: true``
       and ``answerTokens``. It is never reduced. A valid ``code`` is never
       dropped and never cut; a longer or non-ASCII one is a defect of the
       tool that made it, and the answer carries ``code: "invalid-code"``.
    2. The exact location where the complete result was recorded. If there
       is no recording, the answer carries ``resultKept: false`` and says why
       in its cut hint.
    3. The tool's declared whole fields, in priority order. A fitting omission
       reserves space before the next field is considered. Delivered outcomes
       and next actions appear before the locator.
    4. ``error`` where present. It is cut to at most
       ``TOOL_ANSWER_ERROR_MAX_CHARS`` characters and then further, from its
       end, until the measured form is within the bound.
       ``errorCut: true`` says that it was cut, and the detail file holds it
       whole.
    5. ``warning`` where present, with the same character cap as ``error``.
       Fit it after the error and with the locator and final cut hint counted.
       ``warningCut: true`` marks a cut and stays even when no warning text fits.
    6. The other top-level fields that are numbers, booleans or strings of up to
       200 characters, in the order of the answer, each only while the form
       stays within its bound.

    ``error`` is never dropped. ``droppedFields`` holds the count of eligible
    fields left out, plus any count the incoming mapping already carried,
    and is part of the measured form: when it does not fit, further fields
    are left out until it does. If needed, the warning shrinks before the error.
    A supplied ``logError`` stays beside ``log``. A previous ``warningCut``
    marker remains on a refit.
    Returns the short form, the dropped count, and whether the error was cut.
    ``count`` measures tokens with the product's counter (``models/tokens.py``);
    the module stays pure by taking it as an argument.
    """
    fixed = _fixed_short_part(answer, answer_tokens=answer_tokens)
    short = fixed.copy()
    prior_dropped_count = _prior_dropped_fields(answer, declared_fields)
    prior_error_cut = answer.get("errorCut") is True
    _set_short_location(short, location)
    if (notice := _document_notice(answer)) is not None:
        short["staleDocuments"] = notice
    hint = _cut_hint(location.log, location.log_error)
    dropped_budget = prior_dropped_count + len(answer) + len(declared_fields)
    declared_dropped = fit_declared_short_fields(
        short,
        declared_fields,
        hint,
        count,
        dropped_budget,
    )
    error_cut = prior_error_cut
    if "error" in answer:
        error_cut = _fit_short_text(short, "error", answer["error"], count) or prior_error_cut
    dropped = prior_dropped_count + declared_dropped
    if answer.get("warningCut") is True:
        short["warningCut"] = True
    if isinstance(warning := answer.get("warning"), str):
        _fit_warning(short, warning, lambda form: count(_with_cut_hint(form, hint, dropped)))
    reserved = set(short).union(field.root for field in declared_fields)
    scalar_dropped, kept = _fit_scalars(short, _eligible_short_scalars(answer, reserved), count)
    dropped, error_cut = _fit_the_cut_hint(
        short, _CutFitting(kept, dropped + scalar_dropped, hint, count, error_cut)
    )
    short["cut"] = hint
    # Token accounting uses key-sorted JSON. Put fitted outcome and action
    # fields ahead of the reserved location for the actual transport order.
    return fixed | leading_declared_fields(short, declared_fields) | short, dropped, error_cut


def _prior_dropped_fields(
    answer: Mapping[str, Any], declared_fields: tuple[ShortFormField, ...]
) -> int:
    dropped = answer.get("droppedFields")
    if not isinstance(dropped, int) or dropped <= 0:
        return 0
    if "cut" in answer:
        # A late logging failure re-fits the declared fields from the whole
        # result, including those the previous pass counted as dropped.
        dropped -= sum(not field.present_in(answer) for field in declared_fields)
    return max(0, dropped)


@dataclass(frozen=True)
class _CutFitting:
    """What the measurement of a cut form needs besides the form itself."""

    kept: list[str]
    dropped: int
    hint: Mapping[str, Any]
    count: Callable[[Mapping[str, Any]], int]
    error_cut: bool


def _with_cut_hint(
    form: Mapping[str, Any], hint: Mapping[str, Any], dropped: int
) -> dict[str, Any]:
    measured = {**form, "cut": hint}
    if dropped:
        measured["droppedFields"] = dropped
    return measured


def _fit_the_cut_hint(short: dict[str, Any], fitting: _CutFitting) -> tuple[int, bool]:
    """Measure the form with its hint (and its ``droppedFields``) and shed until it fits.

    Shed scalars, warning text and error text in that order. Keep the hint
    even above the bound so the reader can find the result.
    """

    kept, dropped, hint, count, error_cut = (
        fitting.kept,
        fitting.dropped,
        fitting.hint,
        fitting.count,
        fitting.error_cut,
    )

    def measured(form: Mapping[str, Any] = short) -> int:
        return count(_with_cut_hint(form, hint, dropped))

    while kept and measured() > TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
        del short[kept.pop()]
        dropped += 1
    if "warning" in short and measured() > TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
        _fit_warning(short, short["warning"], measured)
    if "error" in short and measured() > TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
        _fit_short_text(short, "error", short["error"], measured)
        error_cut = True
    if dropped:
        short["droppedFields"] = dropped
    return dropped, error_cut


# Rule 6: the framework keys every answer may carry beside its row's result
# fields. A leaf that lands later and adds or changes a tool adds or changes
# that tool's row; this set stays the framework's. ``nextStep`` is not among
# them: producers keep writing it onto the validated response, and the answer
# carries it as ``next``.
FRAMEWORK_ANSWER_KEYS = frozenset(
    {
        "ok",
        "code",
        "error",
        "next",
        "log",
        "logError",
        "resultKept",
        "overflow",
        "answerTokens",
        "droppedFields",
        "errorCut",
        "warningCut",
        "buildCurrentness",
        "runningOperation",
        "cut",
    }
)


def fields_outside_row(
    answer: Mapping[str, Any], allowed_fields: set[str] | frozenset[str] | None
) -> set[str]:
    """Return the answer keys no row allows (rule 6).

    ``allowed_fields=None`` is an interim row (rule 7): no list of fields,
    so nothing is outside it. Framework keys (``ok``, ``next``, ``log``,
    the overflow short-form keys) are never outside.
    The table test fails for a missing row, for a field outside its row,
    and for a case over the bound.
    """
    if allowed_fields is None:
        return set()
    return set(answer) - allowed_fields - FRAMEWORK_ANSWER_KEYS
