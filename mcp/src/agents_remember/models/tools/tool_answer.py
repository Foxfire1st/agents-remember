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

from collections.abc import Callable, Iterator, Mapping
from typing import Any

# Rule 4: one constant bounds every answer, counted with the product's counter
# (``models/tokens.py``).
TOOL_ANSWER_MAX_TOKENS = 1500
# Rule 4: no bound in the exception table is above this.
TOOL_ANSWER_EXCEPTION_MAX_TOKENS = 4000
# Rule 2: a list with more entries is cut; the answer keeps the count, the
# first entries in the order the next step needs, and the location of the
# whole list (the answer-level ``log`` names the detail file).
TOOL_ANSWER_LIST_MAX = 20
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
# Rule 4: the short form of the guard has a bound of its own, for every tool
# and every row, an interim row included. A row with a lower bound does not
# lower it: the short form is what a caller gets in place of an answer that
# failed its row.
TOOL_ANSWER_OVERFLOW_MAX_TOKENS = 400
# Rule 4: steps 1 and 2 of the short form (the fixed part and the location)
# are within this many tokens, whatever the log folder is.
TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS = 250
# Rule 4: a long error is cut to this many characters first, then further
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
}

# MIK-R110 rule 6: the governed per-tool answer bounds for the landed memory
# tools. Each bound equals its shaped row's bound in the answer table, and the
# guard enforces it on every answer the tool delivers -- the table's measured
# bounds and the production bounds are one number, not two.
SHAPED_TOOL_BOUNDS: dict[tuple[str, str | None], int] = {
    ("citation_fix", None): 1500,
    ("memory_quality_check", None): 1500,
    ("knowledge_integrity_check", None): 1500,
    ("route_index_refresh", None): 400,
    ("drift_check", None): 1500,
    ("citation_migrate", None): 1500,
    ("memory_init", None): 800,
    ("memory_baseline_status", None): 600,
    ("memory_baseline_adopt", None): 700,
    ("memory_carryover_plan", None): 500,
    ("memory_carryover_apply", None): 800,
    ("curator_coherence", None): 850,
}

# Rule 7: until its leaf lands, a tool stands in the table as an interim row
# (bound 4,000, no list of fields, the name of its leaf). The guard holds it
# to the bound. This leaf builds rules 1-6 and 8 and changes the envelope;
# the answers themselves are rebuilt by L109-L112.
INTERIM_TOOL_LEAF: dict[str, str] = {
    # L100: native tools land with their own leaf; until it lands they stand
    # as interim rows here.
    "retire_child": "L100",
    "role_start_set": "L100",
    # L109: a read names where a thing is.
    "read_ar_files": "L109",
    "knowledge_read": "L109",
    "knowledge_diff": "L109",
    "context_packet": "L109",
    "resolve_context": "L109",
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
}
INTERIM_LEAVES = ("L100", "L109", "L110", "L111", "L112")


# Rule 9 (L112): the landed leaf's final rows carry their own bounds. The
# guard stays the net L108 built: it holds every answer to its row's bound
# through this lookup, and the overflow short form is unchanged.
L112_FINAL_BOUNDS = {
    "runtime_install": 600,
    "dispatch_agent": TOOL_ANSWER_MAX_TOKENS,
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
        "includeBenchmarks",
        "installProviderDeps",
        "summary",
        "messages",
        "reportPath",
        "providerWatcherRebind",
        "recoveryActions",
        "log",
    }
)


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

    Every interim row names its exact rule-7 leaf, exception rows included:
    ``role_message`` answers from ``role_agents.py`` (L111). A tool with no
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
    short["overflow"] = True
    short["answerTokens"] = answer_tokens
    return short


def _fit_location(
    short: dict[str, Any],
    log: str | None,
    count: Callable[[Mapping[str, Any]], int],
) -> None:
    """Admit the exact log location only while the form with it fits (rule 4).

    ``log`` rides when the form with it is within
    ``TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS``. A location that does not fit
    is not shortened and not replaced by another path: the answer carries
    the fixed no-path ``logError`` and no ``log``.
    """
    if log is None:
        return
    if count({**short, "log": log}) <= TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS:
        short["log"] = log
    else:
        short["logError"] = LOG_ERROR_LOCATION_OMITTED


def _fit_error(
    short: dict[str, Any],
    error: Any,
    count: Callable[[Mapping[str, Any]], int],
) -> bool:
    """Cut the error to 600 characters, then from its end until it fits.

    A count of characters does not bound a count of tokens, so after the
    character cut the error shrinks from its end until the measured form is
    within the bound. Returns whether it was cut; the detail file holds it
    whole. After the fixed part and the location the bound leaves room for
    an error of at least 100 tokens.
    """
    if not isinstance(error, str):
        short["error"] = error
        return False
    cut = len(error) > TOOL_ANSWER_ERROR_MAX_CHARS
    text = error[:TOOL_ANSWER_ERROR_MAX_CHARS] if cut else error
    short["error"] = text
    if cut:
        short["errorCut"] = True
    if count(short) <= TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
        return cut
    # The cut marker rides before the search: it is part of the measured
    # form, and adding it after the search would push the form back over.
    short["errorCut"] = True
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        short["error"] = text[:mid]
        if count(short) <= TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
            low = mid
        else:
            high = mid - 1
    short["error"] = text[:low]
    short["errorCut"] = True
    return True


def _eligible_short_scalars(
    answer: Mapping[str, Any], reserved: Mapping[str, Any]
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


def short_form(
    answer: Mapping[str, Any],
    *,
    answer_tokens: int,
    log: str | None = None,
    log_error: str | None = None,
    count: Callable[[Mapping[str, Any]], int] = lambda _payload: 0,
) -> tuple[dict[str, Any], int, bool]:
    """Build the overflow short form for an answer over its bound (rule 4).

    The guard builds the short form in this order and measures what it
    built, against ``TOOL_ANSWER_OVERFLOW_MAX_TOKENS``:

    1. The fixed part: ``ok``, ``code`` where present, ``overflow: true``
       and ``answerTokens``. It is never reduced. A valid ``code`` is never
       dropped and never cut; a longer or non-ASCII one is a defect of the
       tool that made it, and the answer carries ``code: "invalid-code"``.
    2. The location: ``log``, when the form with it is within
       ``TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS``. A location that does not
       fit is not shortened and not replaced by another path: the answer
       carries the fixed no-path ``logError`` and no ``log``, while the
       detail file is written all the same. When the detail file could not
       be written, the answer carries the caller's ``logError`` and
       ``resultKept: false``.
    3. ``error`` where present. It is cut to at most
       ``TOOL_ANSWER_ERROR_MAX_CHARS`` characters and then further, from its
       end, until the measured form is within the bound.
       ``errorCut: true`` says that it was cut, and the detail file holds it
       whole.
    4. The top-level fields that are numbers, booleans or strings of up to
       200 characters, in the order of the answer, each only while the form
       stays within its bound.

    ``error`` is never dropped. ``droppedFields`` holds the count of eligible
    fields left out, plus any count the incoming mapping already carried,
    and is part of the measured form: when it does not fit, further fields
    are left out until it does, and when no field is left to shed the error
    shrinks further until the form fits. A supplied ``logError`` always rides
    the form beside ``log``; a previous ``errorCut`` marker is preserved.
    Returns the short form, the dropped count, and whether the error was cut.
    ``count`` measures tokens with the product's counter (``models/tokens.py``);
    the module stays pure by taking it as an argument.
    """
    short = _fixed_short_part(answer, answer_tokens=answer_tokens)
    prior_dropped = answer.get("droppedFields")
    prior_dropped_count = (
        prior_dropped if isinstance(prior_dropped, int) and prior_dropped > 0 else 0
    )
    prior_error_cut = answer.get("errorCut") is True
    if log is None:
        if log_error is not None:
            short["logError"] = log_error
        short["resultKept"] = False
    elif log_error is not None:
        short["log"] = log
        short["logError"] = log_error
    else:
        _fit_location(short, log, count)
    error_cut = prior_error_cut
    if "error" in answer:
        error_cut = _fit_error(short, answer["error"], count) or prior_error_cut
    dropped, kept = _fit_scalars(short, _eligible_short_scalars(answer, short), count)
    dropped += prior_dropped_count
    if dropped:
        short["droppedFields"] = dropped
        while kept and count(short) > TOOL_ANSWER_OVERFLOW_MAX_TOKENS:
            del short[kept.pop()]
            dropped += 1
            short["droppedFields"] = dropped
        if count(short) > TOOL_ANSWER_OVERFLOW_MAX_TOKENS and "error" in short:
            _fit_error(short, short["error"], count)
            error_cut = True
    return short, dropped, error_cut


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
        # Conditional lifecycle banners, kept by preservation (what a tool
        # does is unchanged; the stale-agent banner is per-call state).
        "agentNotifierBanner",
        "supervisorBanner",
    }
)


def fields_outside_row(
    answer: Mapping[str, Any], allowed_fields: set[str] | frozenset[str] | None
) -> set[str]:
    """Return the answer keys no row allows (rule 6).

    ``allowed_fields=None`` is an interim row (rule 7): no list of fields,
    so nothing is outside it. Framework keys (``ok``, ``next``, ``log``,
    the overflow short-form keys, the preserved banners) are never outside.
    The table test fails for a missing row, for a field outside its row,
    and for a case over the bound.
    """
    if allowed_fields is None:
        return set()
    return set(answer) - allowed_fields - FRAMEWORK_ANSWER_KEYS
