"""The tool-answer table and its tests (MIK-R108 rules 3, 4, 6, 7, 8).

Rule 6: the test suite has one table with a row for every registered tool,
and for every operation of a tool that has operations. A row holds the fields
the answer may hold and the bound. Rows whose leaf proves cases through live
tool runs name those real paths instead of carrying static payloads; rows
with static payloads hold an ordinary case and the largest case the tool's
own limits allow. The test fails when a registered tool has no row, when an
answer holds a field outside its row, or when a case is larger than the
bound. A leaf of this master that lands later and adds or changes a tool
adds or changes the row.

This leaf (L108) builds rules 1-6 and 8 and changes the envelope of every
answer. Until its leaf lands, a tool stands in the table as an interim row
(rule 7): the bound 4,000, no list of fields, and the name of its leaf. The
guard holds it to the bound.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import agents_remember.application.tool_answer_log as tool_log_module
from agents_remember.application.runtime.install import (
    RuntimeInstallRequest,
    run_runtime_install,
)
from agents_remember.application.task_docs.task_doc_tools import VALID_OPERATIONS
from agents_remember.application.tool_answer_log import (
    ENCLOSURE_LOGS_DIRNAME,
    TOOL_LOG_BASENAME_PREFIX,
    LineRequest,
    ToolCaller,
    ToolCallRecord,
    append_call_line,
    append_jsonl_line,
    detail_dir_for,
    ensure_enclosure_logs_dir,
    log_file_for,
    prune_old_tool_logs,
    record_tool_call,
    reset_log_budgets,
    resolve_log_roots,
    shorten_long_arguments,
    tool_call_context,
)
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.cli import tool_log_stats
from agents_remember.install import provider_watchers as install_provider_watchers
from agents_remember.kernel.primitives.runtime_config import (
    TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT,
    TOOL_LOG_KEEP_DAYS_DEFAULT,
    TOOL_LOG_MAX_BYTES_DEFAULT,
    ConfigError,
    McpRuntimeConfig,
    parse_tool_log_settings,
)
from agents_remember.mcp.tools.core import compact_runtime_install_payload
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    ENVELOPE_STRIPPED_FIELDS,
    INTERIM_LEAVES,
    INTERIM_TOOL_LEAF,
    L111_SHAPED_TOOLS,
    L112_FINAL_BOUNDS,
    LOG_ERROR_LOCATION_OMITTED,
    RUNTIME_INSTALL_ANSWER_FIELDS,
    TOOL_ANSWER_ERROR_MAX_CHARS,
    TOOL_ANSWER_EXCEPTION_MAX_TOKENS,
    TOOL_ANSWER_EXCEPTIONS,
    TOOL_ANSWER_LIST_MAX,
    TOOL_ANSWER_MAX_TOKENS,
    TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS,
    TOOL_ANSWER_OVERFLOW_MAX_TOKENS,
    TOOL_CODE_MAX_CHARS,
    bound_for,
    fields_outside_row,
    interim_leaf_for,
    is_valid_tool_code,
    next_from_next_step,
    shaped_bound_for,
    short_form,
    strip_envelope,
    truncate_lists,
)
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    new_ulid,
    observer_root,
)
from agents_remember.observer.ambient import reset_ambient
from agents_remember.worktrees.modules import cleanup as cleanup_module
from agents_remember.worktrees.modules.closeout import closeout_changed_paths
from agents_remember.worktrees.modules.git import worktree_dirty
from agents_remember.worktrees.worktree_contract import load_contract
from tool_answer_l110_rows import L110_ROW_DATA
from tool_answer_l111_test_support import ToolAnswerRow, _apply_l111_shaped_rows
from tool_entry_point_world import EntryPointWorld

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
    "coordinationRoot": f"{_CASE_ROOT}",
    "dryRun": True,
    "experiment": {
        "delivery": {
            "mode": "legacy",
            "reason": "no experiment was selected for this run; the installation is the unmodified one",
        },
        "record": None,
        "selection": {
            "experiment": None,
            "reason": "no experiment was selected for this run; the installation is the unmodified one",
            "requested": "",
            "selected": False,
            "source": "unselected",
        },
    },
    "host": {
        "changed": False,
        "error": {
            "code": "settings_invalid",
            "detail": None,
            "message": f"MCP settings file does not exist: {_CASE_ROOT}/mcp.settings.json",
            "step": "host",
        },
        "ok": False,
        # MIK-R71: the host step reads the user settings path (XDG), not the
        # removed coord/system location; the producer normalizes its prefix.
        "settingsPath": f"{_CASE_ROOT}/agents-remember/settings.json",
    },
    "includeBenchmarks": False,
    "installProviderDeps": True,
    "ok": False,
    "providerWatcherRebind": {
        "attempted": True,
        "ok": None,
        "phases": [{"action": "stop", "ok": True, "phase": "pre-provider-refresh-stop"}],
    },
    "reportPath": f"{_CASE_ROOT}/report.json",
    "summary": {
        "copiedFiles": 107,
        "createdDirs": 158,
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
    "coordinationRoot": f"{_CASE_ROOT}",
    "dryRun": True,
    "experiment": {
        "delivery": {
            "mode": "legacy",
            "reason": "no experiment was selected for this run; the installation is the unmodified one",
        },
        "record": None,
        "selection": {
            "experiment": None,
            "reason": "no experiment was selected for this run; the installation is the unmodified one",
            "requested": "",
            "selected": False,
            "source": "unselected",
        },
    },
    "host": {
        "changed": False,
        "error": {
            "code": "settings_invalid",
            "detail": None,
            "message": f"MCP settings file does not exist: {_CASE_ROOT}/mcp.settings.json",
            "step": "host",
        },
        "ok": False,
        "settingsPath": f"{_CASE_ROOT}/agents-remember/settings.json",
    },
    "includeBenchmarks": False,
    "installProviderDeps": True,
    "messages": [
        'Provider watcher stop failed before runtime provider refresh; the install continued without watcher recovery. Failed stop: {"provider": "watchers", "action": "stop", "partial": false, "recoveryActions\u2026'
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
            "recoveryAction": "Stop the named leftover process and rerun the install; no product path restarts provider watchers. Only the providers-remove command deletes leftover provider runtime folders, and server_info reports enabled provider keys once as unused.",
        }
    ],
    "removedPartStores": {
        "bytes": 6,
        "count": 2,
        "notice": "The install deleted none of them: `agents-remember removed-stores --dry-run` lists them, and without `--dry-run` the command removes them on your word",
        "storedGates": "No part of the product decides a stored gate any more: `agents-remember removed-stores` names the gate logs and removes them on your word",
    },
    "reportPath": f"{_CASE_ROOT}/report.json",
    "summary": {
        "copiedFiles": 107,
        "createdDirs": 157,
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
                "files",
                "overviews",
                "statesProblem",
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
            "files": "the per-path naming the caller opens and acts on; action entries flag title fallback when no section scored",
            "overviews": "the overview files the caller opens once for the batch",
            "statesProblem": "the caller learns why no state could be measured",
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


# L109's largest cases are operation-produced, not recorded: the producers
# below rebuild each largest answer from a hermetic fixture and pin it
# byte-for-byte against its file (L109_REGEN=1 rewrites the files).
# Only volatile bytes are sanitized: the scratch prefixes read /world
# (the entry-point world) and /gen (other fixture roots), day folders read
# ar-tools-2000-01-01, and detail timestamps read zero. Git commits use
# fixed author/committer dates, so tree and commit identities are stable
# and stay in the pin. Every other byte is the produced answer.
_L109_FIXED_GIT_ENV = {
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_AUTHOR_NAME": "l109 fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "l109 fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


def _l109_git(root: Path, *args: str) -> str:
    import subprocess  # noqa: PLC0415

    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        **_L109_FIXED_GIT_ENV,
    }
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _l109_commit_all(root: Path, message: str) -> str:
    _l109_git(root, "add", "-A")
    _l109_git(root, "commit", "-q", "-m", message)
    return _l109_git(root, "rev-parse", "HEAD")


def _sanitize_l109_answer(answer: dict[str, Any], prefixes: dict[str, str]) -> str:
    import re  # noqa: PLC0415

    raw = json.dumps(answer, indent=1, sort_keys=True) + chr(10)
    for prefix in sorted(prefixes, key=len, reverse=True):
        raw = raw.replace(prefix, prefixes[prefix])
    raw = re.sub(r"ar-tools-\d{4}-\d{2}-\d{2}", "ar-tools-2000-01-01", raw)
    raw = re.sub(r"(?<=/)(\d{12})(?=-)", "000000000000", raw)
    # The tool-log detail file carries a random 8-hex suffix per call; the
    # pin names the stable stem (day, tool) and zeroes the random part.
    raw = re.sub(
        r"-(read_ar_files|knowledge_read|knowledge_diff|context_packet|read-ar-files|knowledge-read|knowledge-diff|context-packet)-[0-9a-f]{8}\.json",
        lambda match: f"-{match.group(1)}-00000000.json",
        raw,
    )
    return raw


def _write_l109_largest_pair(code: Path, memory: Path, onb: Path, index: int, rel: str) -> str:
    """One governed file with its card, sidecar and invariant in the largest case."""

    import hashlib  # noqa: PLC0415

    from agents_remember.models.knowledge_files import (  # noqa: PLC0415
        canonical_text,
        derived_record_id,
    )

    inv = derived_record_id("invariant", f"l109-largest-{index}")
    rlz = f"RLZ-{index:06d}"
    blob = _l109_git(code, "hash-object", rel)
    digest = "sha256:" + hashlib.sha256((code / rel).read_bytes()).hexdigest()
    anchor = {"locator": {"kind": "file"}, "blob": blob, "content": digest}
    card = onb / f"{rel}.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(f"# Mod {index}\n\n## Records\n\n- holds [1]\n", encoding="utf-8")
    (onb / f"{rel}.json").write_text(
        canonical_text(
            {
                "schema": "ar-onboarding-file/v1",
                "path": rel,
                "references": {"1": {"targets": [{"kind": "code", "anchor": anchor}]}},
                "realizes": [
                    {
                        "id": rlz,
                        "invariant": inv,
                        "anchor": anchor,
                        "role": "primary-authority",
                        "rationale": f"Mod {index} lands both halves.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    inv_path = memory / "knowledge" / "invariants" / f"{inv}-mod-{index}.json"
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(
        canonical_text(
            {
                "schema": "ar-invariant/v1",
                "id": inv,
                "revision": 1,
                "status": "accepted",
                "statement": f"Mod {index} lands as one pair.",
                "applicability": f"Every mod {index}.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
                "origin": {
                    "task": "260928-MIK",
                    "leaf": "260928-MIK-L109",
                    "legacyId": f"legacy-largest-{index}",
                },
            }
        ),
        encoding="utf-8",
    )
    return inv


def _produce_l109_read_ar_files_largest() -> tuple[str, dict[str, bool]]:
    from agents_remember.models.knowledge_files import (  # noqa: PLC0415
        canonical_text,
        derived_record_id,
    )
    from agents_remember.worktrees.worktree_contract import (  # noqa: PLC0415
        load_contract,
    )
    from test_read_ar_files import (  # noqa: PLC0415
        _write_overview,
        _write_route_index,
    )

    world = EntryPointWorld()
    world.build(keep_lifecycle=True)
    try:
        # The packet's canonical journey: a leaf Worker names twenty files
        # in its own worktrees through task scope, so cards and overviews
        # arrive worktree-relative and the answer holds its bound on any
        # root length. The fixture repos stay empty; the leaf worktrees
        # carry the governed files and their cards.
        contract = load_contract(world.contract)
        code, memory = contract.code_worktree, contract.memory_worktree
        assert memory is not None, contract
        group = code.parent
        for name in ("user.email", "user.name"):
            _l109_git(code, "config", name, "l109-fixture@example.invalid")
            _l109_git(memory, "config", name, "l109-fixture@example.invalid")
        files = [f"pkg/mod{index}.py" for index in range(10)] + [
            f"pkg/sub/deep{index}.py" for index in range(10)
        ]
        for index, rel in enumerate(files):
            path = code / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"def mod{index}():{chr(10)}    return {index}{chr(10)}",
                encoding="utf-8",
            )
        onb = memory / "onboarding"
        (memory / "knowledge").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge" / "layout.json").write_text(
            canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}),
            encoding="utf-8",
        )
        _write_overview(onb, "", "# Repo overview\nroot text\n")
        _write_overview(onb, "pkg", "# pkg overview\npkg text\n")
        _write_overview(onb, "pkg/sub", "# sub overview\nsub text\n")
        members = []
        for index, rel in enumerate(files):
            members.append(_write_l109_largest_pair(code, memory, onb, index, rel))
        fam = derived_record_id("family", "l109-largest-family")
        (memory / "knowledge" / "families").mkdir(parents=True, exist_ok=True)
        (memory / "knowledge" / "families" / f"{fam}-largest.json").write_text(
            canonical_text(
                {
                    "schema": "ar-family/v1",
                    "id": fam,
                    "revision": 1,
                    "status": "accepted",
                    "title": "Largest",
                    "guarantee": "Twenty governed files land together.",
                    "members": members,
                    "routes": ["pkg"],
                    "admission": "legacy-unassessed",
                    "origin": {
                        "task": "260928-MIK",
                        "leaf": "260928-MIK-L109",
                        "legacyId": "legacy-largest-family",
                    },
                }
            ),
            encoding="utf-8",
        )
        _write_route_index(onb, "pkg", covered=list(files))
        _l109_commit_all(code, "twenty governed files")
        _l109_commit_all(memory, "twenty governing cards")
        task_context = {
            "task_document_ref": {"repository": "repo-a", "path": "m/1_leaf.json"},
            "contract_path": world.contract.as_posix(),
        }
        outcome, out = world.call(
            "read_ar_files",
            {"repo_id": "repo-a", "files": list(files), "task_context": task_context},
        )
        assert outcome == "RETURNED", out
        assert isinstance(out, dict)
        rows = out.get("files", [])
        assert isinstance(rows, list) and len(rows) == 20, out
        for row in rows:
            assert isinstance(row, dict), out
            card = row.get("card")
            assert isinstance(card, str) and not Path(card).is_absolute(), row
            assert (group / card).is_file(), row
        for location in out.get("overviews", []):
            assert not Path(str(location)).is_absolute(), out
            assert (group / str(location)).is_file(), out
        text = _sanitize_l109_answer(out, {world.coord.as_posix(): "/world"})
        states = {str(row.get("cardState")): True for row in rows if isinstance(row, dict)}
        return text, states
    finally:
        world.close()


def _produce_l109_knowledge_read_largest() -> str:
    import test_knowledge_paging as paging  # noqa: PLC0415
    from knowledge_index_test_support import (  # noqa: PLC0415
        init_repository,
    )

    tmp = Path(tempfile.mkdtemp())
    mem = tmp / "memory"
    init_repository(mem)
    paging.write_family_tree(mem, paging._statements(18, words=20), directory=paging._DEEP)
    _l109_commit_all(mem, "deep family")
    world = EntryPointWorld()
    world.build(keep_lifecycle=True)
    try:
        outcome, first = world.call(
            "knowledge_read",
            {
                "memoryRoot": str(mem),
                "view": "source_context",
                "sourcePath": paging._member_path(1, paging._DEEP),
            },
        )
        assert outcome == "RETURNED", first
        assert isinstance(first, dict)
        biggest = first
        biggest_tokens = count_response_tokens(first)
        continuation = first.get("continuation")
        while continuation is not None:
            outcome, response = world.call(
                "knowledge_read",
                {
                    "memoryRoot": str(mem),
                    "view": "source_context",
                    "sourcePath": paging._member_path(1, paging._DEEP),
                    "continuation": continuation,
                },
            )
            assert outcome == "RETURNED", response
            assert isinstance(response, dict)
            measured = count_response_tokens(response)
            if measured > biggest_tokens:
                biggest, biggest_tokens = response, measured
            continuation = response.get("continuation")
        return _sanitize_l109_answer(
            biggest, {world.coord.as_posix(): "/world", tmp.as_posix(): "/gen"}
        )
    finally:
        world.close()


def _produce_l109_knowledge_diff_largest() -> str:
    from agents_remember.models.knowledge_files import (  # noqa: PLC0415
        canonical_text,
        derived_record_id,
    )
    from agents_remember.models.knowledge_files.documents import (  # noqa: PLC0415
        LAYOUT_MARKER_PATH,
        record_path,
    )
    from knowledge_index_test_support import (  # noqa: PLC0415
        init_repository,
        write_document,
    )

    tmp = Path(tempfile.mkdtemp())
    mem = tmp / "memory"
    init_repository(mem)
    write_document(mem, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "1"})
    for index in range(50):
        inv = derived_record_id("invariant", f"l109-diff-{index}")
        write_document(
            mem,
            record_path("invariant", inv, f"rec-{index}"),
            {
                "schema": "ar-invariant/v1",
                "id": inv,
                "revision": 1,
                "status": "accepted",
                "statement": f"Record {index} holds.",
                "applicability": "Always.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
                "origin": {
                    "task": "260928-MIK",
                    "leaf": "260928-MIK-L109",
                    "legacyId": f"legacy-diff-{index}",
                },
            },
        )
    _l109_commit_all(mem, "base fifty")
    for index in range(35):
        inv = derived_record_id("invariant", f"l109-diff-{index}")
        path = mem / record_path("invariant", inv, f"rec-{index}")
        document = json.loads(path.read_text(encoding="utf-8"))
        document["statement"] = f"Record {index} holds, changed."
        path.write_text(canonical_text(document), encoding="utf-8")
    for index in range(50, 80):
        inv = derived_record_id("invariant", f"l109-diff-{index}")
        write_document(
            mem,
            record_path("invariant", inv, f"rec-{index}"),
            {
                "schema": "ar-invariant/v1",
                "id": inv,
                "revision": 1,
                "status": "accepted",
                "statement": f"Record {index} holds, added.",
                "applicability": "Always.",
                "conditions": [],
                "exclusions": [],
                "supersedes": [],
                "admission": "legacy-unassessed",
                "origin": {
                    "task": "260928-MIK",
                    "leaf": "260928-MIK-L109",
                    "legacyId": f"legacy-diff-{index}",
                },
            },
        )
    for index in range(40, 45):
        inv = derived_record_id("invariant", f"l109-diff-{index}")
        (mem / record_path("invariant", inv, f"rec-{index}")).unlink()
    _l109_commit_all(mem, "change one hundred")
    world = EntryPointWorld()
    world.build(keep_lifecycle=True)
    try:
        outcome, out = world.call(
            "knowledge_diff",
            {
                "memoryRoot": str(mem),
                "beforeRevision": "HEAD~1",
                "afterRevision": "HEAD",
            },
        )
        assert outcome == "RETURNED", out
        assert isinstance(out, dict)
        return _sanitize_l109_answer(
            out, {world.coord.as_posix(): "/world", tmp.as_posix(): "/gen"}
        )
    finally:
        world.close()


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


class ToolAnswerTableTests(unittest.TestCase):
    def test_every_registered_tool_has_an_authored_row_and_no_extras(self) -> None:
        check_tool_answer_roster(set(TOOL_RESPONSE_MODELS), authored_row_owners())
        rowed = {tool for tool, _operation in TOOL_ANSWER_TABLE}
        self.assertEqual(rowed, set(TOOL_RESPONSE_MODELS))

    def test_missing_authored_row_fails(self) -> None:
        reduced = {
            tool: leaf for tool, leaf in authored_row_owners().items() if tool != "server_info"
        }
        with self.assertRaises(AssertionError):
            check_tool_answer_roster(set(TOOL_RESPONSE_MODELS), reduced)

    def test_task_doc_row_covers_every_operation(self) -> None:
        rowed = {operation for tool, operation in TOOL_ANSWER_TABLE if tool == "task_doc"}
        self.assertEqual(rowed, set(VALID_OPERATIONS))

    def test_every_row_passes_the_gates(self) -> None:
        failures = [
            failure
            for key, row in TOOL_ANSWER_TABLE.items()
            for failure in validate_tool_answer_row(key, row)
        ]
        self.assertEqual(failures, [])

    def test_no_bound_above_the_exception_max(self) -> None:
        for key, row in TOOL_ANSWER_TABLE.items():
            self.assertLessEqual(
                row.bound, TOOL_ANSWER_EXCEPTION_MAX_TOKENS, f"row {key} over 4,000"
            )

    def test_exception_table_first_rows(self) -> None:
        self.assertIn(("role_message", None), TOOL_ANSWER_EXCEPTIONS)
        self.assertIn(("server_info", None), TOOL_ANSWER_EXCEPTIONS)
        self.assertEqual(bound_for("role_message"), TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        self.assertEqual(bound_for("server_info"), TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        for key, (bound, _reason) in TOOL_ANSWER_EXCEPTIONS.items():
            self.assertLessEqual(bound, TOOL_ANSWER_EXCEPTION_MAX_TOKENS, f"{key}")

    def test_interim_rows_hold_the_bound_and_name_a_leaf(self) -> None:
        for key, row in TOOL_ANSWER_TABLE.items():
            if key in TOOL_ANSWER_EXCEPTIONS or row.fields is not None:
                continue
            if row.fields is not None:
                continue  # landed shaped rows (L110, L111, L112 finals) hold their own bound
            self.assertEqual(row.bound, TOOL_ANSWER_EXCEPTION_MAX_TOKENS, f"row {key}")
            self.assertIsNone(row.fields, f"row {key}")
            self.assertIn(row.interim_leaf, INTERIM_LEAVES, f"row {key}")

    def test_curator_coherence_row_is_shaped(self) -> None:
        # MIK-R110 rule 3: the first row whose leaf landed is shaped, with fields, uses,
        # named production paths and no interim owner.
        row = TOOL_ANSWER_TABLE[("curator_coherence", None)]
        self.assertIsNone(row.interim_leaf)
        self.assertIsNotNone(row.fields)
        self.assertEqual(
            [failure for failure in validate_tool_answer_row(("curator_coherence", None), row)],
            [],
        )

    def test_each_later_leaf_still_owns_rows(self) -> None:
        # A leaf does not land while a row names it: only L100 still
        # owns interim rows. L109 has landed (MIK-R109 rules 1-4), L110 has
        # landed (MIK-R110 rule 6) and L112 has landed (MIK-R112 rule 9); each
        # owns none, and the next tests say so for each. L111's leaf is still
        # open, but every roster row it names is shaped by its own applier, so
        # no row names it either (the shaped-rows tests say so).
        owners = {row.interim_leaf for row in TOOL_ANSWER_TABLE.values()}
        self.assertIn("L100", owners, "L100 owns no row")
        for leaf in ("L109", "L110", "L111", "L112"):
            self.assertNotIn(leaf, owners, f"{leaf} still owns a row")

    def test_no_row_names_l112(self) -> None:
        # MIK-R112 rule 9: L112 has landed. No interim row names it -- not
        # in the authored roster, not in the built table, and not in the
        # implementation's interim map.
        self.assertNotIn("L112", set(AUTHORED_INTERIM_OWNERS.values()))
        self.assertNotIn("L112", {row.interim_leaf for row in TOOL_ANSWER_TABLE.values()})
        self.assertNotIn("L112", set(INTERIM_TOOL_LEAF.values()))

    def test_no_built_row_names_the_landed_l110(self) -> None:
        # MIK-R110 rule 6: L110 has landed. No row names it -- not in the
        # authored roster, not in the built table (interim or shaped), and
        # not in the implementation's interim map.
        self.assertNotIn("L110", set(AUTHORED_INTERIM_OWNERS.values()))
        named = sorted(
            f"{tool}:{operation}"
            for (tool, operation), row in TOOL_ANSWER_TABLE.items()
            if row.interim_leaf == "L110"
        )
        self.assertEqual(named, [])

    def test_no_row_names_l109(self) -> None:
        # MIK-R109 rules 1-4: L109 has landed. No interim row names it -- not
        # in the authored roster, not in the built table, and not in the
        # implementation's interim map -- and its four rebuilt reads hold
        # final shaped rows in the table with fields, uses and both cases.
        self.assertNotIn("L109", set(AUTHORED_INTERIM_OWNERS.values()))
        self.assertNotIn("L109", {row.interim_leaf for row in TOOL_ANSWER_TABLE.values()})
        self.assertNotIn("L109", set(INTERIM_TOOL_LEAF.values()))
        self.assertNotIn("L109", INTERIM_LEAVES)
        for tool in (
            "read_ar_files",
            "knowledge_read",
            "knowledge_diff",
            "context_packet",
        ):
            with self.subTest(tool=tool):
                row = TOOL_ANSWER_TABLE[(tool, None)]
                self.assertIsNone(row.interim_leaf, f"{tool} still names an owner")
                self.assertIsNotNone(row.fields, f"{tool} has no shaped fields")
                self.assertTrue(row.uses, f"{tool} has no field uses")
                self.assertIsNotNone(row.ordinary_case, f"{tool} has no ordinary case")
                self.assertIsNotNone(row.largest_case, f"{tool} has no largest case")

    def test_l109_shaped_rows_are_the_table_rows(self) -> None:
        # L109 has landed, so the shaped rows below (fields, bounds,
        # operation-produced cases) are the table rows, not side evidence.
        # Each holds the row's field contract, a measured bound, and both
        # cases; the largest case carries no overflow fallback.
        for tool in (
            "read_ar_files",
            "knowledge_read",
            "knowledge_diff",
            "context_packet",
        ):
            with self.subTest(tool=tool):
                row = TOOL_ANSWER_TABLE[(tool, None)]
                self.assertIs(L109_SHAPED_ROWS[(tool, None)], row)
                failures = validate_row_case(
                    (tool, None), row, "largest case", dict(row.largest_case or {})
                )
                self.assertEqual(failures, [], f"{tool} largest case fails its gates")
                largest = dict(row.largest_case or {})
                self.assertNotEqual(largest.get("overflow"), True, f"{tool} largest case overflows")

    def test_field_allowlist_mechanism(self) -> None:
        self.assertEqual(fields_outside_row({"ok": True, "x": 1}, None), set())
        self.assertEqual(
            fields_outside_row({"ok": True, "next": {}, "log": "f", "state": "s"}, {"state"}),
            set(),
        )
        self.assertEqual(
            fields_outside_row({"ok": True, "state": "s", "rogue": 1}, {"state"}),
            {"rogue"},
        )


class ToolAnswerRowGateTests(unittest.TestCase):
    """Negative controls: each rule-6 failure mode fails its gate."""

    def _shaped_row(self, **overrides: Any) -> ToolAnswerRow:
        largest = {"ok": True, "state": "y" * 300}
        largest_measured = count_response_tokens(largest)
        params: dict[str, Any] = {
            "tool": "shaped",
            "operations": (None,),
            "fields": frozenset({"state"}),
            "uses": {"state": "resume after poll"},
            "bound": largest_measured + largest_measured // 2,
            "interim_leaf": None,
            "ordinary_case": {"ok": True, "state": "started"},
            "largest_case": largest,
        }
        params.update(overrides)
        return ToolAnswerRow(**params)

    def test_missing_row_fails(self) -> None:
        reduced = {t: leaf for t, leaf in AUTHORED_INTERIM_OWNERS.items() if t != "task_doc"}
        with self.assertRaises(AssertionError):
            check_tool_answer_roster(set(TOOL_RESPONSE_MODELS), reduced)

    def test_valid_shaped_row_passes(self) -> None:
        # The probe's legitimate shaped row: no interim owner, a nonempty
        # use, a bound, and measured ordinary/largest case payloads.
        failures = validate_tool_answer_row(("shaped", None), self._shaped_row())
        self.assertEqual(failures, [])

    def test_field_outside_row_fails(self) -> None:
        self.assertEqual(
            fields_outside_row({"ok": True, "state": "s", "rogue": 1}, frozenset({"state"})),
            {"rogue"},
        )

    def test_field_without_stated_use_fails(self) -> None:
        row = self._shaped_row(uses={})
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("no stated use" in failure for failure in failures))

    def test_empty_use_fails(self) -> None:
        row = self._shaped_row(uses={"state": ""})
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("no stated use" in failure for failure in failures))

    def test_use_for_unknown_field_fails(self) -> None:
        row = self._shaped_row(uses={"state": "resume after poll", "ghost": "resume"})
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("unknown field" in failure for failure in failures))

    def test_shaped_row_with_interim_leaf_fails(self) -> None:
        row = self._shaped_row(interim_leaf="L112")
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("interim leaf" in failure for failure in failures))

    def test_interim_row_without_leaf_fails(self) -> None:
        row = ToolAnswerRow(
            tool="shaped",
            operations=(None,),
            fields=None,
            uses={},
            bound=4000,
            interim_leaf=None,
            ordinary_case=None,
            largest_case=None,
        )
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("no owning leaf" in failure for failure in failures))

    def test_case_over_the_row_bound_fails(self) -> None:
        row = self._shaped_row(
            bound=100,
            ordinary_case={"ok": True, "state": "s"},
            largest_case={"ok": True, "blob": _big_blob()},
        )
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("over the row bound" in failure for failure in failures))

    def test_bound_over_twice_the_largest_case_fails(self) -> None:
        row = self._shaped_row(
            bound=4000,
            ordinary_case={"ok": True, "state": "s"},
            largest_case={"ok": True, "state": "s"},
        )
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("twice the largest" in failure for failure in failures))

    def test_shaped_row_without_case_payloads_fails(self) -> None:
        row = self._shaped_row(ordinary_case=None, largest_case=None)
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertTrue(any("without case payloads" in failure for failure in failures))

    def test_production_proven_row_without_case_payloads_passes(self) -> None:
        # A shaped row that names the real tool paths its cases are derived
        # through needs no static payloads; the derivation tests prove it live.
        row = self._shaped_row(ordinary_case=None, largest_case=None, production_paths=("sync",))
        failures = validate_tool_answer_row(("shaped", None), row)
        self.assertEqual(failures, [])

    def test_invalid_code_case_fails_the_row(self) -> None:
        row = self._shaped_row()
        failures = validate_row_case(
            ("shaped", None), row, "largest case", {"ok": False, "code": "y" * 121}
        )
        self.assertTrue(any("invalid code" in failure for failure in failures))

    def test_guard_hit_case_fails_the_row(self) -> None:
        row = self._shaped_row()
        failures = validate_row_case(
            ("shaped", None), row, "largest case", {"ok": True, "blob": _big_blob()}
        )
        self.assertTrue(any("over the row bound" in failure for failure in failures))

    def test_case_field_outside_row_fails(self) -> None:
        row = self._shaped_row()
        failures = validate_row_case(
            ("shaped", None), row, "ordinary case", {"ok": True, "rogue": 1}
        )
        self.assertTrue(any("outside its row" in failure for failure in failures))

    def test_invalid_code_is_replaced(self) -> None:
        self.assertFalse(is_valid_tool_code("y" * 121))
        whole = {"ok": False, "code": "y" * 121, "error": "the call failed."}
        short, _dropped, _cut = short_form(
            whole,
            answer_tokens=10,
            log=None,
            log_error=None,
            count=count_response_tokens,
        )
        self.assertEqual(short["code"], "invalid-code")

    def test_interim_row_case_hitting_the_guard_gets_the_short_form(self) -> None:
        big = {"ok": True, "code": "started", "blob": _big_blob(), "count": 7}
        tokens = count_response_tokens(big)
        self.assertGreater(tokens, TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        short, _dropped, _cut = short_form(
            big,
            answer_tokens=tokens,
            log=None,
            log_error=None,
            count=count_response_tokens,
        )
        self.assertTrue(short["overflow"])
        self.assertEqual(short["code"], "started")
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)


class ToolAnswerOwnershipTests(unittest.TestCase):
    """Every current interim row names its exact rule-7 leaf; landed finals name none."""

    def test_implementation_matches_the_authored_roster(self) -> None:
        owners = authored_row_owners()
        mismatches = [
            tool for tool in TOOL_RESPONSE_MODELS if interim_leaf_for(tool) != owners.get(tool)
        ]
        self.assertEqual(mismatches, [])
        for tool in L110_ROW_DATA:
            self.assertIsNone(interim_leaf_for(tool), tool)
            _fields, _uses, bound, _paths = L110_ROW_DATA[tool]
            self.assertEqual(shaped_bound_for(tool), bound, tool)

    def test_removed_tools_have_no_row_and_no_owner(self) -> None:
        for tool in (
            "cgc_symbol_search",
            "cgc_callers",
            "cgc_callees",
            "cgc_dependencies",
            "cgc_complexity",
            "cgc_visualize",
            "grepai_search",
            "grepai_trace",
            "provider_status",
            "provider_diagnostics",
            "provider_watchers",
            "message_parent",
            "message_child",
            "rename_child",
            "rename_self",
            "lifecycle_start",
            "lifecycle_resume",
            "lifecycle_turn_end_notification",
            "lifecycle_end",
            "switch_lifecycle",
            "lifecycle_phase",
            "lifecycle_gate",
            "gate_decide",
            "gate_list",
            "ping",
            "role_capsule_compile",
            "skill_catalog_list",
            "skill_catalog_read",
            "codex_benchmark_prepare",
            "codex_benchmark_run",
            "skills_install",
            "citation_migrate",
            "drift_check",
        ):
            with self.subTest(tool=tool):
                self.assertNotIn((tool, None), TOOL_ANSWER_TABLE)
                self.assertNotIn(tool, authored_row_owners())
                self.assertIsNone(interim_leaf_for(tool))

    def test_exception_rows_name_their_leaf(self) -> None:
        self.assertEqual(interim_leaf_for("role_message"), "L111")
        # server_info is a landed L112 exception passthrough: bounded by the
        # rule-4 table, with no interim owner.
        self.assertIsNone(interim_leaf_for("server_info"))


class ToolAnswerGuardTests(unittest.TestCase):
    def test_small_answer_passes_through_unchanged(self) -> None:
        answer = {"ok": True, "state": "started"}
        tokens = count_response_tokens(answer)
        self.assertLessEqual(tokens, TOOL_ANSWER_MAX_TOKENS)
        self.assertEqual(bound_for("worktree_start"), 600)

    def test_over_bound_answer_gets_the_short_form(self) -> None:
        big = {"ok": True, "blob": _big_blob(), "count": 7, "verbose": "y" * 500}
        tokens = count_response_tokens(big)
        self.assertGreater(tokens, TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        short, _dropped, error_cut = short_form(
            big,
            answer_tokens=tokens,
            log="/logs/ar-tools/d.json",
            count=count_response_tokens,
        )
        self.assertTrue(short["ok"])
        self.assertTrue(short["overflow"])
        self.assertEqual(short["answerTokens"], tokens)
        self.assertEqual(short["log"], "/logs/ar-tools/d.json")
        self.assertEqual(short["count"], 7)
        self.assertNotIn("blob", short)
        self.assertNotIn("verbose", short)
        self.assertFalse(error_cut)
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_refusal_keeps_code_and_error_in_the_short_form(self) -> None:
        refusal = {
            "ok": False,
            "code": "start-reservation-conflict",
            "error": "recover this reserved start instead of creating another enclosure.",
            "blob": _big_blob(),
        }
        short, _dropped, error_cut = short_form(
            refusal, answer_tokens=9999, log="/logs/d.json", count=count_response_tokens
        )
        self.assertEqual(short["code"], "start-reservation-conflict")
        self.assertIn("recover this reserved start", short["error"])
        self.assertFalse(error_cut)
        self.assertNotIn("errorCut", short)

    def test_short_form_is_measured_five_hundred_short_fields(self) -> None:
        # Rule 4, E2: an answer with 500 short top-level fields gives a short
        # form within 400 tokens, with the exact code and a dropped count.
        answer = {"ok": False, "code": "start-blocked"}
        answer.update({f"f{index:03d}": f"value-{index}" for index in range(500)})
        tokens = count_response_tokens(answer)
        self.assertGreater(tokens, TOOL_ANSWER_MAX_TOKENS)
        short, dropped, error_cut = short_form(
            answer,
            answer_tokens=tokens,
            log="/logs/d.json",
            count=count_response_tokens,
        )
        self.assertEqual(short["code"], "start-blocked")
        self.assertFalse(error_cut)
        self.assertGreater(dropped, 0)
        self.assertEqual(short["droppedFields"], dropped)
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_short_form_is_measured_fifty_thousand_char_error(self) -> None:
        # Rule 4, E2: a 50,000-character error is cut to 600 with errorCut,
        # the code is exact, and the form is within 400 tokens.
        error = "".join(f"sentence {index} says what to do next. " for index in range(1500))
        self.assertGreater(len(error), 50000)
        refusal = {"ok": False, "code": "start-blocked", "error": error}
        short, _dropped, error_cut = short_form(
            refusal,
            answer_tokens=99999,
            log="/logs/d.json",
            count=count_response_tokens,
        )
        self.assertEqual(short["code"], "start-blocked")
        self.assertTrue(error_cut)
        self.assertTrue(short["errorCut"])
        self.assertEqual(len(short["error"]), TOOL_ANSWER_ERROR_MAX_CHARS)
        self.assertEqual(short["error"], error[:TOOL_ANSWER_ERROR_MAX_CHARS])
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_short_form_fixed_part_and_location_fit_with_longest_code(self) -> None:
        # Rule 4: steps 1 and 2 are within 250 tokens, shown with the
        # longest refusal codes of the product and an enclosure location.
        self.assertEqual(TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS, 250)
        self.assertEqual(TOOL_CODE_MAX_CHARS, 120)
        for code in (
            "closeout-candidate-changed-during-admission",
            "certification-selection-observation-moved",
        ):
            self.assertTrue(is_valid_tool_code(code))
            self.assertLessEqual(len(code), TOOL_CODE_MAX_CHARS)
            short, _dropped, _cut = short_form(
                {"ok": False, "code": code, "error": "what to do. " * 40},
                answer_tokens=99999,
                log=(
                    "/coord/worktrees/agents-remember/108_x/logs/ar-tools-2026-10-08/"
                    "160323398195-worktree_closeout_preview-717bc84c.json"
                ),
                count=count_response_tokens,
            )
            self.assertEqual(short["code"], code)
            self.assertIn("log", short)
            fixed = {key: short[key] for key in short if key != "error"}
            self.assertLessEqual(
                count_response_tokens(fixed), TOOL_ANSWER_OVERFLOW_FIXED_MAX_TOKENS
            )
            self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_location_over_the_fixed_bound_is_omitted_not_substituted(self) -> None:
        # Rule 4: a location that does not fit is not shortened and not
        # replaced by another path: the fixed no-path logError rides, with
        # no log.
        log = "/coord/" + "x" * 2000 + "/ar-tools-2026-10-08/1-ping-abcdef12.json"
        short, _dropped, _cut = short_form(
            {"ok": False, "code": "start-blocked", "error": "what to do."},
            answer_tokens=99999,
            log=log,
            count=count_response_tokens,
        )
        self.assertNotIn("log", short)
        self.assertEqual(short["logError"], LOG_ERROR_LOCATION_OMITTED)
        self.assertNotIn("resultKept", short)
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_short_form_is_measured_six_hundred_multi_token_char_error(self) -> None:
        # Rule 4, E2: an error of 600 characters that are several tokens
        # each is cut from its end until the measured form fits 400.
        error = "汉" * TOOL_ANSWER_ERROR_MAX_CHARS
        self.assertEqual(len(error), 600)
        refusal = {"ok": False, "code": "start-blocked", "error": error}
        short, _dropped, error_cut = short_form(
            refusal,
            answer_tokens=99999,
            log="/logs/d.json",
            count=count_response_tokens,
        )
        self.assertEqual(short["code"], "start-blocked")
        self.assertTrue(error_cut)
        self.assertTrue(short["errorCut"])
        self.assertLessEqual(len(short["error"]), 600)
        self.assertLess(
            count_response_tokens({"error": short["error"]}),
            count_response_tokens({"error": error}),
        )
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_invalid_code_is_not_passed_on(self) -> None:
        # Rule 4: a longer or non-ASCII code is a defect of the tool that
        # made it: the answer keeps its ok and carries invalid-code.
        for bad in ("x" * 121, "codedé:broken idea"):
            self.assertFalse(is_valid_tool_code(bad))
            short, _dropped, _cut = short_form(
                {"ok": False, "code": bad, "error": "what to do."},
                answer_tokens=9999,
                log="/logs/d.json",
                count=count_response_tokens,
            )
            self.assertFalse(short["ok"] is None)
            self.assertEqual(short["code"], "invalid-code")
            self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
        self.assertFalse(is_valid_tool_code(""))
        self.assertFalse(is_valid_tool_code(None))
        self.assertTrue(is_valid_tool_code("start-blocked"))

    def test_invalid_code_keeps_original_whole_in_detail(self) -> None:
        # Rule 4: the original answer is whole in the detail file.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            config = _stub_config(coord)
            bad = "y" * 121
            payload = {
                "ok": False,
                "code": bad,
                "error": "what to do.",
                "blob": _big_blob(),
            }
            with tool_call_context("memory_quality_check", {"query": "q"}, config=config):
                answer = complete_tool_response("memory_quality_check", dict(payload))
            self.assertEqual(answer["code"], "invalid-code")
            self.assertIn("log", answer)
            detail = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
            self.assertEqual(detail["code"], bad)

    def test_surveyed_product_codes_are_valid(self) -> None:
        # Rule 4, rule 6: the table test fails for a row whose cases
        # produce a malformed code. All rows are interim (no cases yet), so
        # this pins the surveyed refusal codes of the product: every one is
        # an ASCII identifier within the bound.
        surveyed = [
            "certification-selection-observation-moved",
            "prepared-memory-curator-refresh-required",
            "prior-red-gate-five-inputs-unreadable",
            "prior-red-gate-five-terminal-invalid",
            "candidate-source-observation-refused",
            "closeout-candidate-changed-during-admission",
            "entity_fingerprint_without_inventory",
            "start-blocked",
            "raised",
        ]
        for code in surveyed:
            self.assertTrue(is_valid_tool_code(code), f"surveyed code rejected: {code}")
            self.assertLessEqual(len(code), TOOL_CODE_MAX_CHARS)

    def test_short_form_bound_ignores_a_lower_row_bound(self) -> None:
        # Rule 4: a row with a lower bound does not lower the short form's
        # own bound of 400 tokens.
        self.assertEqual(TOOL_ANSWER_OVERFLOW_MAX_TOKENS, 400)
        answer: dict[str, Any] = {"ok": True}
        answer.update({f"f{index:03d}": f"value-{index}" for index in range(250)})
        tokens = count_response_tokens(answer)
        self.assertGreater(tokens, TOOL_ANSWER_MAX_TOKENS)
        short, _dropped, _cut = short_form(
            answer,
            answer_tokens=tokens,
            log="/logs/d.json",
            count=count_response_tokens,
        )
        self.assertLessEqual(count_response_tokens(short), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)

    def test_both_on_a_shaped_row_through_the_choke(self) -> None:
        # Rule 4, E2: 500 short fields and a 50,000-character error on a
        # landed shaped row (MIK-R110: memory_quality_check carries the
        # governed 1,500 bound): the measured short form keeps the exact
        # code with droppedFields and errorCut.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            config = _stub_config(coord)
            answer_in = {"ok": False, "code": "start-blocked"}
            answer_in.update({f"f{index:03d}": f"value-{index}" for index in range(500)})
            answer_in["error"] = "what to do next. " * 3000
            self.assertEqual(bound_for("memory_quality_check"), 1500)
            with tool_call_context("memory_quality_check", {"query": "q"}, config=config):
                answer = complete_tool_response("memory_quality_check", answer_in)
            self.assertTrue(answer["overflow"])
            self.assertEqual(answer["code"], "start-blocked")
            self.assertTrue(answer["errorCut"])
            self.assertGreater(answer.get("droppedFields", 0), 0)
            self.assertLessEqual(count_response_tokens(answer), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            self.assertIn("log", answer)

    def test_failed_detail_write_gives_log_error_and_result_kept_false(self) -> None:
        short, _dropped, _cut = short_form(
            {"ok": True, "blob": _big_blob()},
            answer_tokens=9999,
            log_error="the whole result was not recorded: no space.",
            count=count_response_tokens,
        )
        self.assertIn("not recorded", short["logError"])
        self.assertFalse(short["resultKept"])
        self.assertNotIn("log", short)

    def test_envelope_stripped_from_every_answer(self) -> None:
        answer = complete_tool_response(
            "server_info",
            _server_info_case(),
        )
        for field in ENVELOPE_STRIPPED_FIELDS:
            self.assertNotIn(field, answer)
        self.assertTrue(answer["ok"])
        self.assertNotIn("nextStep", answer)
        self.assertNotIn("log", answer)

    def test_over_bound_tool_answer_through_the_choke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            config = _stub_config(coord)
            with tool_call_context("memory_quality_check", {"query": "q"}, config=config):
                answer = complete_tool_response(
                    "memory_quality_check", {"ok": True, "blob": _big_blob()}
                )
            self.assertTrue(answer["overflow"])
            self.assertNotIn("blob", answer)
            self.assertIn("log", answer)
            # The short form is measured: what the caller got is within 400.
            self.assertLessEqual(count_response_tokens(answer), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            detail = json.loads(Path(answer["log"]).read_text(encoding="utf-8"))
            self.assertEqual(detail["blob"], _big_blob())
            self.assertIn("tokens", detail)
            # The line holds the tokens of the delivered answer, with the cut flag.
            log_dir = coord / "logs" / "mcp"
            lines = [
                json.loads(line)
                for file in sorted(log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0]["guardCut"])
            self.assertEqual(lines[0]["answerTokens"], count_response_tokens(answer))

    def test_over_budget_root_path_through_the_choke(self) -> None:
        # Rule 4, E2: a transcript root whose path alone is over the bound.
        # The answer is within the bound and carries the fixed logError, the
        # detail file exists in that folder, and the line names it. The path
        # is neither cut nor substituted nor capped.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            deep = coord
            for index in range(10):
                deep = deep / f"level-{index:02d}-{'y' * 180}"
            deep.mkdir(parents=True)
            self.assertGreater(len(deep.as_posix()), 1900)
            config = _stub_config(coord, transcript_root=deep)
            with tool_call_context("memory_quality_check", {"query": "q"}, config=config):
                answer = complete_tool_response(
                    "memory_quality_check", {"ok": True, "blob": _big_blob()}
                )
            self.assertTrue(answer["overflow"])
            self.assertNotIn("log", answer)
            self.assertEqual(answer["logError"], LOG_ERROR_LOCATION_OMITTED)
            self.assertLessEqual(count_response_tokens(answer), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            details = list(deep.glob("ar-tools-*/*.json"))
            self.assertEqual(len(details), 1)
            self.assertEqual(details[0].parent.parent, deep)
            stored = json.loads(details[0].read_text(encoding="utf-8"))
            self.assertEqual(stored["blob"], _big_blob())
            lines = [
                json.loads(line)
                for file in sorted(deep.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["detail"], details[0].name)

    def test_list_over_twenty_keeps_count_and_first_entries(self) -> None:
        answer = truncate_lists({"items": list(range(25)), "ok": True})
        self.assertEqual(answer["items"]["count"], 25)
        self.assertEqual(answer["items"]["items"], list(range(20)))
        self.assertTrue(answer["items"]["truncated"])
        self.assertEqual(truncate_lists({"items": list(range(20))}), {"items": list(range(20))})
        self.assertEqual(TOOL_ANSWER_LIST_MAX, 20)

    def test_nested_lists_are_cut_through_retained_items(self) -> None:
        answer = truncate_lists({"outer": [{"inner": list(range(25))}]})
        (kept,) = answer["outer"]
        self.assertEqual(kept["inner"]["count"], 25)
        self.assertEqual(kept["inner"]["items"], list(range(20)))
        self.assertTrue(kept["inner"]["truncated"])
        wide = truncate_lists({"outer": [[1] * 25 for _ in range(3)]})
        for kept_list in wide["outer"]:
            self.assertEqual(kept_list["count"], 25)
            self.assertTrue(kept_list["truncated"])
        deep = truncate_lists({"outer": list(range(25))})
        self.assertEqual(deep["outer"]["count"], 25)
        self.assertEqual(len(deep["outer"]["items"]), 20)

    def test_next_carries_the_one_following_call(self) -> None:
        self.assertIsNone(next_from_next_step(None))
        self.assertEqual(
            next_from_next_step(
                {
                    "summary": "continue",
                    "nextOperation": "continue_work",
                    "nextTool": "worktree_status",
                    "nextArgs": {"repo_id": "agents-remember"},
                }
            ),
            {
                "summary": "continue",
                "tool": "worktree_status",
                "args": {"repo_id": "agents-remember"},
            },
        )


class ToolAnswerLogTests(unittest.TestCase):
    def test_strip_envelope_removes_exactly_the_four(self) -> None:
        stripped = strip_envelope(
            {
                "ok": True,
                "tokens": 5,
                "tokenizer": "tiktoken:o200k_base",
                "tokenCountExact": True,
                "operation": "ping",
                "code": "refused",
                "error": "one sentence.",
                "state": "s",
            }
        )
        self.assertEqual(
            stripped,
            {"ok": True, "code": "refused", "error": "one sentence.", "state": "s"},
        )

    def _record(
        self,
        coord: Path,
        *,
        ok: bool = True,
        code: str | None = None,
        arguments: dict[str, Any] | None = None,
    ):
        reset_log_budgets()
        dest = resolve_log_roots(_stub_config(coord))
        self.assertIsNone(dest.enclosure_dir)
        self.assertIsNotNone(dest.coord_log_dir)
        assert dest.coord_log_dir is not None
        return record_tool_call(
            ToolCallRecord(
                tool_name="ping",
                operation="ping",
                arguments=arguments or {},
                caller=ToolCaller(agent_id="a1", role="worker", task="t", lifecycle_id="l1"),
                ok=ok,
                code=code,
                duration_ms=1.5,
                answer_tokens=42,
                whole_result={"ok": ok, "detail": [1, 2]},
                result_bytes=18,
                guard_cut=False,
                needs_detail=True,
                enclosure_dir=dest.enclosure_dir,
                coord_log_dir=dest.coord_log_dir,
            )
        )

    def _read_lines(self, coord: Path) -> list[dict[str, Any]]:
        log_dir = coord / "logs" / "mcp"
        files = sorted(log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
        self.assertEqual(len(files), 1)
        return [
            json.loads(line)
            for line in files[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_success_line_holds_the_rule_3_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            outcome = self._record(coord, arguments={"repo_id": "agents-remember"})
            self.assertIsNone(outcome.log_error)
            self.assertIsNotNone(outcome.log)
            (line,) = self._read_lines(coord)
            self.assertEqual(line["tool"], "ping")
            self.assertEqual(line["operation"], "ping")
            self.assertEqual(line["caller"]["agentId"], "a1")
            self.assertEqual(line["caller"]["role"], "worker")
            self.assertTrue(line["ok"])
            self.assertEqual(line["answerTokens"], 42)
            self.assertEqual(line["resultBytes"], 18)
            self.assertFalse(line["guardCut"])
            self.assertEqual(line["arguments"], {"repo_id": "agents-remember"})
            self.assertEqual(line["dropped"], 0)
            self.assertTrue(line["detail"].endswith(".json"))
            self.assertIsNotNone(outcome.log)
            assert outcome.log is not None
            stored = json.loads(Path(outcome.log).read_text(encoding="utf-8"))
            self.assertEqual(stored["detail"], [1, 2])

    def test_refused_and_raised_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            self._record(coord, ok=False, code="start-blocked")
            lines = self._read_lines(coord)
            self.assertFalse(lines[-1]["ok"])
            self.assertEqual(lines[-1]["code"], "start-blocked")
            outcome = record_tool_call(
                ToolCallRecord(
                    tool_name="task_doc",
                    operation="task_doc",
                    arguments={},
                    caller=ToolCaller(),
                    ok=False,
                    code="raised",
                    duration_ms=0.5,
                    answer_tokens=0,
                    whole_result={"ok": False, "code": "raised", "error": "boom."},
                    result_bytes=0,
                    guard_cut=False,
                    needs_detail=True,
                    enclosure_dir=None,
                    coord_log_dir=coord / "logs" / "mcp",
                )
            )
            self.assertIsNone(outcome.log_error)
            lines = self._read_lines(coord)
            self.assertEqual(lines[-1]["code"], "raised")

    def test_long_argument_replaced_by_length_and_digest(self) -> None:
        shortened = shorten_long_arguments({"small": "x", "big": "y" * 500})
        self.assertEqual(shortened["small"], "x")
        self.assertEqual(len(shortened["big"]), 2)
        self.assertEqual(shortened["big"]["length"], 500)
        self.assertEqual(
            shortened["big"]["sha256"], hashlib.sha256(("y" * 500).encode()).hexdigest()
        )

    def test_no_credential_and_no_env_value_in_a_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SECRET_VALUE"] = "s3cr3t-env-value-xyz"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "apiKey": "sk-live-123",
                        "password": "hunter2",
                        "note": "s3cr3t-env-value-xyz",
                        "plain": "fine",
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SECRET_VALUE"]
        self.assertEqual(line["arguments"]["apiKey"], "<omitted>")
        self.assertEqual(line["arguments"]["password"], "<omitted>")
        self.assertEqual(line["arguments"]["note"], "<env>")
        self.assertEqual(line["arguments"]["plain"], "fine")

    def test_embedded_env_secret_and_credential_spellings_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SECRET_VALUE"] = "s3cr3t-env-value-xyz"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "query": "find s3cr3t-env-value-xyz in the docs",
                        "nested": {"deep": ["pair s3cr3t-env-value-xyz here"]},
                        "token": "tok-abc-123",
                        "Authorization": "Bearer xyz",
                        "auth": "shh",
                        "tokens": 5,
                        "tokenizer": "o200k_base",
                        "plain": "fine",
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SECRET_VALUE"]
        self.assertEqual(line["arguments"]["query"], "find <env> in the docs")
        self.assertEqual(line["arguments"]["nested"], {"deep": ["pair <env> here"]})
        self.assertEqual(line["arguments"]["token"], "<omitted>")
        self.assertEqual(line["arguments"]["Authorization"], "<omitted>")
        self.assertEqual(line["arguments"]["auth"], "<omitted>")
        self.assertEqual(line["arguments"]["tokens"], 5)
        self.assertEqual(line["arguments"]["tokenizer"], "o200k_base")
        self.assertEqual(line["arguments"]["plain"], "fine")
        text = json.dumps(line)
        self.assertNotIn("s3cr3t-env-value-xyz", text)
        self.assertNotIn("tok-abc-123", text)
        self.assertNotIn("Bearer xyz", text)

    def test_short_credential_variable_value_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SHORT_CREDENTIAL"] = "S4719x"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "query": "find S4719x here",
                        "nested": {"text": "nested S4719x value"},
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SHORT_CREDENTIAL"]
        self.assertEqual(line["arguments"]["query"], "find <env> here")
        self.assertEqual(line["arguments"]["nested"], {"text": "nested <env> value"})
        self.assertNotIn("S4719x", json.dumps(line))

    def test_ordinary_env_value_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_NOTE"] = "ordinary-note-value"
            try:
                coord = Path(tmp)
                self._record(coord, arguments={"note": "holds ordinary-note-value inside"})
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_NOTE"]
        self.assertEqual(line["arguments"]["note"], "holds ordinary-note-value inside")

    def test_home_folder_path_stays_whole(self) -> None:
        """Ordinary home-folder text is preserved whole inside its length domain (rule 3)."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_DATADIR"] = tmp
            try:
                coord = Path(tmp)
                short_path = "group/series-contract.md"
                self._record(coord, arguments={"contract_path": short_path, "repo_id": "r"})
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_DATADIR"]
        self.assertEqual(line["arguments"]["contract_path"], short_path)

    def test_home_folder_path_follows_the_length_rule(self) -> None:
        """The real tmp-rooted path is whole at or under 200 chars, else length+digest (rule 3)."""
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_DATADIR"] = tmp
            try:
                coord = Path(tmp)
                contract_path = (coord / "group" / "series-contract.md").as_posix()
                self._record(coord, arguments={"contract_path": contract_path, "repo_id": "r"})
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_DATADIR"]
        if len(contract_path) > 200:
            self.assertEqual(
                line["arguments"]["contract_path"],
                {
                    "length": len(contract_path),
                    "sha256": hashlib.sha256(contract_path.encode("utf-8")).hexdigest(),
                },
            )
        else:
            self.assertEqual(line["arguments"]["contract_path"], contract_path)

    def test_overlong_path_shortens_to_length_and_digest(self) -> None:
        """A value over 200 chars is replaced by its length and digest (rule 3)."""
        long_path = "home/" + "d" * 250 + "/series-contract.md"
        assert len(long_path) > 200
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            self._record(coord, arguments={"contract_path": long_path, "repo_id": "r"})
            (line,) = self._read_lines(coord)
        self.assertEqual(
            line["arguments"]["contract_path"],
            {
                "length": len(long_path),
                "sha256": hashlib.sha256(long_path.encode("utf-8")).hexdigest(),
            },
        )

    def test_slash_bearing_env_credential_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SECRET_SLASH"] = "reviewSynthetic/SlashCredential4719"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "query": "prefix reviewSynthetic/SlashCredential4719 suffix",
                        "nested": {"text": "prefix reviewSynthetic/SlashCredential4719"},
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SECRET_SLASH"]
        self.assertEqual(line["arguments"]["query"], "prefix <env> suffix")
        self.assertEqual(line["arguments"]["nested"], {"text": "prefix <env>"})
        self.assertNotIn("reviewSynthetic/SlashCredential4719", json.dumps(line))

    def test_path_looking_credential_values_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AR_L108_TEST_SECRET_SLASH"] = "reviewSynthetic/SlashCredential4719"
            try:
                coord = Path(tmp)
                self._record(
                    coord,
                    arguments={
                        "query": "reviewSynthetic/SlashCredential4719/report",
                        "nested": {"text": "/tmp/reviewSynthetic/SlashCredential4719/report"},
                        "safe": "report without the value",
                    },
                )
                (line,) = self._read_lines(coord)
            finally:
                del os.environ["AR_L108_TEST_SECRET_SLASH"]
        self.assertEqual(line["arguments"]["query"], "<env>/report")
        self.assertEqual(line["arguments"]["nested"], {"text": "/tmp/<env>/report"})
        self.assertEqual(line["arguments"]["safe"], "report without the value")
        self.assertNotIn("reviewSynthetic/SlashCredential4719", json.dumps(line))
        # A diagnostic holding no protected value passes through untouched.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            self._record(coord, arguments={"repo_id": "r", "note": "plain diagnostic"})
            (plain,) = self._read_lines(coord)
        self.assertEqual(plain["arguments"]["note"], "plain diagnostic")

    def test_prune_touches_only_old_tool_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            old_log = folder / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01.jsonl"
            old_log.write_text("{}\n", encoding="utf-8")
            old_detail = folder / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01"
            old_detail.mkdir()
            fresh_log = folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl"
            fresh_log.write_text("{}\n", encoding="utf-8")
            other = folder / "other.jsonl"
            other.write_text("{}\n", encoding="utf-8")
            old = 946684800.0
            os.utime(old_log, (old, old))
            os.utime(old_detail, (old, old))
            removed = prune_old_tool_logs(folder, 14, today="2026-10-08")
            self.assertEqual(removed, 1)
            self.assertFalse(old_log.exists())
            self.assertFalse(old_detail.exists())
            self.assertTrue(fresh_log.exists())
            self.assertTrue(other.exists())

    def test_day_change_and_keep_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            log_dir = coord / "logs" / "mcp"
            log_dir.mkdir(parents=True)
            stale_log = log_dir / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01.jsonl"
            stale_log.write_text("{}\n", encoding="utf-8")
            stale_detail = log_dir / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01"
            stale_detail.mkdir()
            (stale_detail / "x.json").write_text("{}", encoding="utf-8")
            old = 946684800.0  # 2000-01-01
            os.utime(stale_log, (old, old))
            os.utime(stale_detail / "x.json", (old, old))
            os.utime(stale_detail, (old, old))
            keep = log_dir / "keep.txt"
            keep.write_text("x", encoding="utf-8")
            self._record(coord)
            self.assertFalse(stale_log.exists())
            self.assertFalse(stale_detail.exists())
            self.assertTrue(keep.exists())

    def test_enclosure_keeps_everything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            enclosure = Path(tmp) / "enclosure" / ENCLOSURE_LOGS_DIRNAME
            made = ensure_enclosure_logs_dir(Path(tmp) / "enclosure")
            self.assertIsNotNone(made)
            assert made is not None
            self.assertTrue(made.is_dir())
            stale = enclosure / f"{TOOL_LOG_BASENAME_PREFIX}2000-01-01.jsonl"
            stale.write_text("{}\n", encoding="utf-8")
            outcome = record_tool_call(
                ToolCallRecord(
                    tool_name="ping",
                    operation="ping",
                    arguments={},
                    caller=ToolCaller(),
                    ok=True,
                    code=None,
                    duration_ms=0.1,
                    answer_tokens=1,
                    whole_result={"ok": True},
                    result_bytes=2,
                    guard_cut=False,
                    needs_detail=True,
                    enclosure_dir=enclosure,
                    coord_log_dir=None,
                    keep_days=1,
                )
            )
            self.assertIsNone(outcome.log_error)
            self.assertTrue(stale.exists())

    def test_failing_log_write_does_not_fail_the_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("file, not a dir", encoding="utf-8")
            outcome = record_tool_call(
                ToolCallRecord(
                    tool_name="ping",
                    operation="ping",
                    arguments={},
                    caller=ToolCaller(),
                    ok=True,
                    code=None,
                    duration_ms=0.1,
                    answer_tokens=1,
                    whole_result={"ok": True},
                    result_bytes=2,
                    guard_cut=False,
                    needs_detail=True,
                    enclosure_dir=None,
                    coord_log_dir=blocker / "logs",
                )
            )
            self.assertIsNotNone(outcome.log_error)
            self.assertIn("not recorded", outcome.log_error or "")
            answer = complete_tool_response("server_info", _server_info_case())
            self.assertTrue(answer["ok"])

    def test_no_coordination_root_writes_no_log_and_does_not_fail(self) -> None:
        dest = resolve_log_roots(None)
        self.assertIsNone(dest.enclosure_dir)
        self.assertIsNone(dest.coord_log_dir)

    def test_custom_absolute_transcript_root_holds_line_detail_and_location(
        self,
    ) -> None:
        # Rule 3, E3: a transcriptRoot set to another absolute folder holds
        # the line, the detail file and the location in the answer.
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            custom = coord / "elsewhere" / "tool-logs"
            config = _stub_config(coord, transcript_root=custom)
            dest = resolve_log_roots(config)
            self.assertEqual(dest.coord_log_dir, custom)
            with tool_call_context("server_info", {}, config=config):
                answer = complete_tool_response(
                    "server_info",
                    _server_info_case(),
                )
            self.assertTrue(answer["log"].startswith(custom.as_posix()))
            detail = Path(answer["log"])
            self.assertTrue(detail.is_file())
            self.assertEqual(detail.parent.parent, custom)
            lines = [
                json.loads(line)
                for file in sorted(custom.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["detail"], detail.name)
            self.assertFalse((coord / "logs").exists())

    def test_append_is_one_whole_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.jsonl"
            self.assertIsNone(append_jsonl_line(path, {"a": 1}))
            self.assertIsNone(append_jsonl_line(path, {"b": 2}))
            self.assertEqual(
                path.read_text(encoding="utf-8").splitlines(),
                ["", '{"a": 1}', '{"b": 2}'],
            )


def _install_ambient(coord: Path) -> Any:
    """Install an ambient that adopted a lifecycle for one test (rule 3)."""
    reset_ambient()
    amb = AmbientLifecycle(EventStore(observer_root(_stub_config(coord))))
    amb.attach(new_ulid(), enclosure=str(coord / "contract.json"))
    install_ambient(amb)
    return amb


class ToolAnswerFinalMeasureTests(unittest.TestCase):
    """The guard measures the final delivered mapping (rule 4, rule 3).

    Attaching the log location can itself take an exact-edge answer over its
    bound; a late line failure must not push a delivered answer back over
    it. The line and the observer both carry the delivered count.
    """

    def _complete(
        self, coord: Path, tool: str, whole: dict[str, Any]
    ) -> tuple[dict[str, Any], list[Any]]:
        reset_log_budgets()
        cfg = _stub_config(coord)
        amb = _install_ambient(coord)
        seen: list[Any] = []
        original = amb.emit_tool
        amb.emit_tool = lambda *args, **kwargs: seen.append((args, kwargs))  # type: ignore[method-assign]
        try:
            with tool_call_context(tool, {}, config=cfg):
                answer = complete_tool_response(tool, dict(whole), config=cfg)
        finally:
            amb.emit_tool = original  # type: ignore[method-assign]
            reset_ambient()
        return answer, seen

    def _read_line(self, coord: Path) -> dict[str, Any]:
        files = sorted((coord / "logs" / "mcp").glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])

    def test_exact_edge_answer_guarded_by_its_log_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            bound = bound_for("memory_quality_check")
            whole = {"ok": True, "blob": ""}
            size = 0
            while not bound - 40 <= count_response_tokens(whole) <= bound - 5:
                size += 64
                whole["blob"] = "x" * size
            self.assertLessEqual(count_response_tokens(whole), bound)
            answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertTrue(answer.get("overflow"))
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertTrue(line["guardCut"])
            self.assertEqual(seen[-1][1]["answer_tokens"], delivered)

    def test_ordinary_answer_observer_matches_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            whole = _server_info_case()
            answer, seen = self._complete(coord, "server_info", whole)
            delivered = count_response_tokens(answer)
            self.assertNotIn("code", answer)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertEqual(seen[-1][1]["answer_tokens"], delivered)

    def test_two_late_failures_keep_first_error_and_recount(self) -> None:
        real_write = tool_log_module.os.write
        real_evict = tool_log_module._evict_older_days
        line_failures: list[int] = []
        evict_calls: list[int] = []

        def flaky_write(fd: int, data: Any) -> int:
            raw = bytes(data)
            if b'"answerTokens"' in raw and not line_failures:
                line_failures.append(1)
                raise OSError("forced line failure")
            return real_write(fd, data)

        def flaky_evict(*args: Any, **kwargs: Any) -> bool:
            evict_calls.append(1)
            if len(evict_calls) == 3:
                return False
            return real_evict(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            whole = {"ok": True, "blob": _big_blob()}
            with (
                mock.patch.object(tool_log_module.os, "write", side_effect=flaky_write),
                mock.patch.object(tool_log_module, "_evict_older_days", side_effect=flaky_evict),
            ):
                answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertEqual(line_failures, [1], "the line failure never fired")
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            # The first sentence stays; the retry failure only counts as
            # dropped. The delivered answer, not the pre-cut count, is observed.
            self.assertEqual(
                answer.get("logError"),
                "the tool call was not recorded: the log file cannot be written.",
            )
            self.assertEqual(seen[-1][1]["answer_tokens"], delivered)

    def test_ordinary_exact_edge_with_line_failure_is_reguarded(self) -> None:
        real_write = tool_log_module.os.write
        failed: list[int] = []

        def flaky_once(fd: int, data: Any) -> int:
            raw = bytes(data)
            if b'"answerTokens"' in raw and not failed:
                failed.append(1)
                raise OSError("forced line failure")
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            # Learn the attached location's size on a passing run, then find
            # the largest blob whose final mapping still fits the bound: the
            # predicate is monotone, so binary search always terminates and
            # never skips past the edge.
            probe, _ = self._complete(coord, "memory_quality_check", {"ok": True, "blob": ""})
            assert probe["log"] is not None
            location_tokens = count_response_tokens({"log": probe["log"]})
            bound = bound_for("memory_quality_check")
            low, high = 0, 200000
            while low < high:
                mid = (low + high + 1) // 2
                final = count_response_tokens({"ok": True, "blob": "x" * mid}) + location_tokens
                if final <= bound:
                    low = mid
                else:
                    high = mid - 1
            whole: dict[str, Any] = {"ok": True, "blob": "x" * low}
            final_before_failure = count_response_tokens(whole) + location_tokens
            # Near the edge: the late failure is what pushes it over.
            self.assertLessEqual(final_before_failure, bound)
            self.assertGreater(final_before_failure, bound - 40)
            reset_log_budgets()
            with mock.patch.object(tool_log_module.os, "write", side_effect=flaky_once):
                answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertEqual(failed, [1], "the line failure never fired")
            self.assertTrue(answer.get("overflow"))
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertTrue(line["guardCut"])
            self.assertEqual(seen[-1][1]["answer_tokens"], delivered)

    def test_full_short_form_fits_including_bookkeeping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            error = "".join(f"token-{index}-failure; " for index in range(40))
            whole: dict[str, Any] = {"ok": False, "error": error, "blob": _big_blob()}
            for index in range(500):
                whole[f"f{index:03d}"] = f"scalar-value-{index}"
            answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertTrue(answer.get("overflow"))
            self.assertTrue(answer.get("errorCut"))
            # The dropped count itself is part of the measured form: with no
            # scalar retained to shed, the error shrinks until everything fits.
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            kept_scalars = [key for key in answer if key.startswith("f")]
            self.assertEqual(answer.get("droppedFields", 0) + len(kept_scalars), 500)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertEqual(seen[-1][1]["answer_tokens"], delivered)

    def test_cut_answer_late_failure_reguards_at_the_short_ceiling(self) -> None:
        real_write = tool_log_module.os.write
        failed: list[int] = []

        def flaky_once(fd: int, data: Any) -> int:
            raw = bytes(data)
            if b'"answerTokens"' in raw and not failed:
                failed.append(1)
                raise OSError("forced line failure")
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            error = "".join(f"token-{index}-failure; " for index in range(40))
            whole: dict[str, Any] = {"ok": False, "error": error}
            for index in range(500):
                whole[f"f{index:03d}"] = f"scalar-value-{index}"
            reset_log_budgets()
            with mock.patch.object(tool_log_module.os, "write", side_effect=flaky_once):
                answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertEqual(failed, [1], "the line failure never fired")
            self.assertTrue(answer.get("overflow"))
            # The re-guard ceiling is the short-form ceiling even though the
            # tool bound is higher: the late sentence re-triggers the cut.
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertTrue(line["guardCut"])
            self.assertEqual(seen[-1][1]["answer_tokens"], delivered)

    def test_line_failure_overflow_stays_in_bound_and_counts_agree(self) -> None:
        real_write = os.write
        failed: list[int] = []

        def flaky(fd: int, data: Any) -> int:
            raw = bytes(data)
            # Only the log line carries answerTokens; the detail whole does
            # not. Fail the line's first attempt, then let the retry land.
            if b'"answerTokens"' in raw and not failed:
                failed.append(1)
                raise OSError("forced line failure")
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            whole = {"ok": True, "blob": _big_blob()}
            with mock.patch.object(tool_log_module.os, "write", side_effect=flaky):
                answer, seen = self._complete(coord, "memory_quality_check", whole)
            delivered = count_response_tokens(answer)
            self.assertEqual(failed, [1], "the line failure never fired")
            self.assertLessEqual(delivered, TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            self.assertIsNotNone(answer.get("logError"))
            (line,) = [self._read_line(coord)]
            self.assertEqual(line["answerTokens"], delivered)
            self.assertEqual(seen[-1][1]["answer_tokens"], delivered)


class ToolPartialWriteTests(unittest.TestCase):
    """A short write is detected, never claimed as a whole record (rule 3)."""

    def test_short_write_is_recovered(self) -> None:
        real_write = os.write
        calls = []

        def partial_once(fd: int, data: Any) -> int:
            calls.append(1)
            if len(calls) == 1:
                return real_write(fd, bytes(data[:10]))
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ar-tools-2026-10-08.jsonl"
            with mock.patch.object(tool_log_module.os, "write", side_effect=partial_once):
                error = append_jsonl_line(path, {"ok": True, "tool": "ping"})
            self.assertIsNone(error)
            (parsed,) = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(parsed, {"ok": True, "tool": "ping"})

    def test_partial_then_stalled_prefix_does_not_join_next_record(self) -> None:
        real_write = tool_log_module.os.write
        calls: list[int] = []

        def ten_then_stuck(fd: int, data: Any) -> int:
            calls.append(1)
            if len(calls) == 1:
                return real_write(fd, bytes(data[:10]))
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            reset_log_budgets()
            dest = resolve_log_roots(_stub_config(coord))
            assert dest.coord_log_dir is not None
            record = ToolCallRecord(
                tool_name="second",
                operation="second",
                arguments={},
                caller=ToolCaller(),
                ok=True,
                code=None,
                duration_ms=1.0,
                answer_tokens=10,
                whole_result={"ok": True},
                result_bytes=12,
                guard_cut=False,
                needs_detail=False,
                enclosure_dir=None,
                coord_log_dir=dest.coord_log_dir,
            )
            with mock.patch.object(tool_log_module.os, "write", side_effect=ten_then_stuck):
                outcome = record_tool_call(record)
            self.assertIsNotNone(outcome.log_error)
            outcome = record_tool_call(record)
            self.assertIsNone(outcome.log_error)
            raw = next(dest.coord_log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl")).read_text(
                encoding="utf-8"
            )
            parsed: list[dict[str, Any]] = []
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            # The stalled prefix stands alone; the valid record parses.
            self.assertEqual(len(parsed), 1)
            self.assertEqual(parsed[0]["tool"], "second")
            self.assertEqual(parsed[0]["dropped"], 1)

    def test_concurrent_writers_never_interleave_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "storm.jsonl"
            errors: list[BaseException] = []

            def worker(index: int) -> None:
                try:
                    failure = append_jsonl_line(path, {"tool": f"w{index}", "n": index})
                    assert failure is None, failure
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            self.assertEqual(errors, [])
            parsed = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.assertEqual(len(parsed), 8)
            self.assertEqual(
                sorted(entry["tool"] for entry in parsed),
                [f"w{index}" for index in range(8)],
            )

    def test_stalled_writer_leaves_concurrent_neighbor_parseable(self) -> None:
        real_write = tool_log_module.os.write

        first_holder: list[threading.Thread] = []
        a_armed = [True]

        def ten_then_stuck(fd: int, data: Any) -> int:
            if first_holder and threading.current_thread() is first_holder[0]:
                if a_armed[0]:
                    a_armed[0] = False
                    return real_write(fd, bytes(data[:10]))
                return 0
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "neighbors.jsonl"
            results: dict[str, Any] = {}

            def stalled() -> None:
                results["stalled"] = append_jsonl_line(path, {"tool": "first", "pad": "x" * 200})

            def whole() -> None:
                results["whole"] = append_jsonl_line(path, {"tool": "second"})

            first, second = (
                threading.Thread(target=stalled),
                threading.Thread(target=whole),
            )
            first_holder.append(first)
            with mock.patch.object(tool_log_module.os, "write", side_effect=ten_then_stuck):
                first.start()
                second.start()
                first.join(timeout=60)
                second.join(timeout=60)
            self.assertIsNotNone(results["stalled"])
            self.assertIsNone(results["whole"])
            parsed = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self.assertEqual([entry["tool"] for entry in parsed], ["second"])

    def test_concurrent_stall_is_counted_on_the_next_persisted_line(self) -> None:
        real_write = tool_log_module.os.write
        first_holder: list[threading.Thread] = []
        a_armed = [True]

        def ten_then_stuck(fd: int, data: Any) -> int:
            if first_holder and threading.current_thread() is first_holder[0]:
                if a_armed[0]:
                    a_armed[0] = False
                    return real_write(fd, bytes(data[:10]))
                return 0
            return real_write(fd, data)

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            reset_log_budgets()

            def budgeted(tool: str) -> Any:
                return append_call_line(
                    LineRequest(
                        log_dir=folder,
                        budget_bytes=10_000_000,
                        line={
                            "tool": tool,
                            "operation": tool,
                            "answer_tokens": 10,
                            "ok": True,
                        },
                    ),
                    day="2026-10-08",
                )

            results: dict[str, Any] = {}
            first = threading.Thread(
                target=lambda: results.setdefault("stalled", budgeted("first"))
            )
            second = threading.Thread(
                target=lambda: results.setdefault("whole", budgeted("second"))
            )
            first_holder.append(first)
            with mock.patch.object(tool_log_module.os, "write", side_effect=ten_then_stuck):
                first.start()
                second.start()
                first.join(timeout=60)
                second.join(timeout=60)
            self.assertIsNotNone(results["stalled"])
            self.assertIsNone(results["whole"])
            third = budgeted("third")
            self.assertIsNone(third)
            lines = []
            for line in (
                (folder / "ar-tools-2026-10-08.jsonl").read_text(encoding="utf-8").splitlines()
            ):
                if not line.strip():
                    continue
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            by_tool = {entry["tool"]: entry for entry in lines if isinstance(entry, dict)}
            # The stalled write is counted on the next applicable persisted
            # line no matter which writer won the race.
            self.assertIn(by_tool["second"]["dropped"], (0, 1))
            accounted = by_tool["second"]["dropped"] + by_tool["third"]["dropped"]
            self.assertEqual(accounted, 1)
            self.assertEqual(sorted(by_tool), ["second", "third"])

    def test_stuck_write_is_dropped_and_counted(self) -> None:
        def stuck(_fd: int, _data: Any) -> int:
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            reset_log_budgets()
            dest = resolve_log_roots(_stub_config(coord))
            assert dest.coord_log_dir is not None
            record = ToolCallRecord(
                tool_name="ping",
                operation="ping",
                arguments={},
                caller=ToolCaller(),
                ok=True,
                code=None,
                duration_ms=1.0,
                answer_tokens=10,
                whole_result={"ok": True},
                result_bytes=12,
                guard_cut=False,
                needs_detail=False,
                enclosure_dir=None,
                coord_log_dir=dest.coord_log_dir,
            )
            with mock.patch.object(tool_log_module.os, "write", side_effect=stuck):
                outcome = record_tool_call(record)
            self.assertIsNotNone(outcome.log_error)
            log_dir = dest.coord_log_dir
            files = sorted(log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
            self.assertTrue(all(not f.read_text(encoding="utf-8").strip() for f in files))
            outcome = record_tool_call(record)
            self.assertIsNone(outcome.log_error)
            lines = [
                json.loads(line)
                for file in sorted(log_dir.glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["dropped"], 1)


class ToolRaisedCallLoggingTests(unittest.TestCase):
    """Raised calls log through the production entry and still raise (rule 3)."""

    def _today_lines(self, world: EntryPointWorld) -> list[dict[str, Any]]:
        files = sorted((world.coord / "logs" / "mcp").glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl"))
        self.assertEqual(len(files), 1)
        return [
            json.loads(line)
            for line in files[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_unknown_tool_refusal_is_logged(self) -> None:
        world = EntryPointWorld()
        try:
            status, _ = world.call("no_such_tool_xyz", {})
            self.assertTrue(status.startswith("RAISED"), status)
            (line,) = self._today_lines(world)
            self.assertEqual(line["tool"], "no_such_tool_xyz")
            self.assertFalse(line["ok"])
            self.assertEqual(line["code"], "raised")
            self.assertTrue(line["detail"].endswith(".json"))
        finally:
            world.close()
            reset_ambient()

    def test_strict_argument_refusal_is_logged(self) -> None:
        world = EntryPointWorld()
        try:
            status, _ = world.call("task_doc", {"repo_id": 123, "operation": "get"})
            self.assertTrue(status.startswith("RAISED"), status)
            (line,) = self._today_lines(world)
            self.assertEqual(line["tool"], "task_doc")
            self.assertFalse(line["ok"])
            self.assertEqual(line["code"], "raised")
            self.assertTrue(line["detail"].endswith(".json"))
        finally:
            world.close()
            reset_ambient()


class ToolTerminatingRouteTests(unittest.TestCase):
    """Cleanup, abandon and finalize never log into an enclosure (rule 3)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.world.build()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()
        reset_ambient()

    def _resolve(self, tool: str) -> Any:
        amb = _install_ambient(self.world.coord)
        state = amb.current
        assert state is not None
        amb.current = replace(state, enclosure=str(self.world.contract))
        try:
            return resolve_log_roots(_stub_config(self.world.coord), tool)
        finally:
            reset_ambient()

    def test_terminating_operations_log_to_the_root(self) -> None:
        for tool in ("worktree_cleanup", "worktree_abandon", "lifecycle_finalize_task"):
            dest = self._resolve(tool)
            self.assertIsNone(dest.enclosure_dir, tool)
            self.assertIsNotNone(dest.coord_log_dir, tool)

    def test_ordinary_tool_uses_the_live_enclosure(self) -> None:
        dest = self._resolve("ping")
        self.assertIsNotNone(dest.enclosure_dir)
        self.assertIsNotNone(dest.coord_log_dir)
        self.assertTrue(Path(str(dest.enclosure_dir)).is_dir())

    def test_removed_group_falls_back_to_the_root(self) -> None:
        amb = _install_ambient(self.world.coord)
        state = amb.current
        assert state is not None
        amb.current = replace(state, enclosure=str(self.world.coord / "no-such-contract.md"))
        try:
            dest = resolve_log_roots(_stub_config(self.world.coord), "ping")
        finally:
            reset_ambient()
        self.assertIsNone(dest.enclosure_dir)
        self.assertIsNotNone(dest.coord_log_dir)


class ToolLogSettingsTests(unittest.TestCase):
    def test_defaults_and_parsing(self) -> None:
        self.assertEqual(parse_tool_log_settings(None).keep_days, TOOL_LOG_KEEP_DAYS_DEFAULT)
        self.assertEqual(TOOL_LOG_KEEP_DAYS_DEFAULT, 14)
        self.assertEqual(parse_tool_log_settings({}).keep_days, 14)
        self.assertEqual(parse_tool_log_settings({"keepDays": 30}).keep_days, 30)
        self.assertEqual(parse_tool_log_settings({}).max_bytes, TOOL_LOG_MAX_BYTES_DEFAULT)
        self.assertEqual(TOOL_LOG_MAX_BYTES_DEFAULT, 268435456)
        self.assertEqual(
            parse_tool_log_settings({}).enclosure_max_bytes,
            TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT,
        )
        self.assertEqual(TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT, 67108864)
        parsed = parse_tool_log_settings(
            {"keepDays": 30, "maxBytes": 1024, "enclosureMaxBytes": 2048}
        )
        self.assertEqual(
            (parsed.keep_days, parsed.max_bytes, parsed.enclosure_max_bytes),
            (30, 1024, 2048),
        )

    def test_bad_values_refuse(self) -> None:
        for raw in (
            "x",
            [],
            {"keepDays": 0},
            {"keepDays": -3},
            {"keepDays": True},
            {"keepDays": "14"},
            {"maxBytes": 0},
            {"maxBytes": -1},
            {"maxBytes": True},
            {"maxBytes": "1024"},
            {"enclosureMaxBytes": 0},
            {"enclosureMaxBytes": False},
            {"enclosureMaxBytes": "2048"},
            {"bogus": 1},
        ):
            with self.assertRaises(ConfigError, msg=repr(raw)):
                parse_tool_log_settings(raw)


class ToolLogStatsCommandTests(unittest.TestCase):
    def _write_log(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        lines = [
            {
                "tool": "ping",
                "operation": "ping",
                "answerTokens": 10,
                "guardCut": False,
            },
            {
                "tool": "ping",
                "operation": "ping",
                "answerTokens": 30,
                "guardCut": False,
            },
            {"tool": "ping", "operation": "ping", "answerTokens": 20, "guardCut": True},
            {
                "tool": "task_doc",
                "operation": "get",
                "answerTokens": 100,
                "guardCut": False,
            },
        ]
        (folder / f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl").write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
        )

    def test_collect_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write_log(folder)
            rows = tool_log_stats.collect_rows(folder)
            by_tool = {(row["tool"], row["operation"]): row for row in rows}
            ping = by_tool[("ping", "ping")]
            self.assertEqual(ping["calls"], 3)
            self.assertEqual(ping["median"], 20)
            self.assertEqual(ping["largest"], 30)
            self.assertEqual(ping["sum"], 60)
            self.assertEqual(ping["guardCuts"], 1)
            self.assertEqual(by_tool[("task_doc", "get")]["calls"], 1)

    def test_day_span_and_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self._write_log(folder)
            self.assertEqual(
                tool_log_stats.run(
                    argparse.Namespace(log_folder=folder, since=None, until=None, json=True)
                ),
                0,
            )
            self.assertEqual(
                tool_log_stats.run(
                    argparse.Namespace(log_folder=folder, since="2026-10-09", until=None, json=True)
                ),
                0,
            )
            self.assertEqual(
                tool_log_stats.run(
                    argparse.Namespace(
                        log_folder=Path(tmp) / "missing",
                        since=None,
                        until=None,
                        json=False,
                    )
                ),
                2,
            )
            self.assertEqual(
                tool_log_stats.run(
                    argparse.Namespace(log_folder=folder, since="not-a-day", until=None, json=False)
                ),
                2,
            )


class ToolAnswerEnclosureTests(unittest.TestCase):
    """The enclosure half of Expected Evidence 4, on a real enclosure.

    One hermetic EntryPointWorld (scratch coordination root, real code and
    memory repositories, one real leaf enclosure opened through the real
    worktree_start) proves: ``logs/`` exists after the enclosure is made,
    both worktrees show a clean status with files in it, the closeout
    changed-paths source lists no logs, and the real cleanup path removes
    ``logs/`` with the enclosure.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.world = EntryPointWorld()
        cls.world.build()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.world.close()
        reset_ambient()

    def test_logs_made_with_the_enclosure(self) -> None:
        contract = load_contract(self.world.contract)
        logs = Path(contract.worktree_group) / ENCLOSURE_LOGS_DIRNAME
        self.assertTrue(logs.is_dir(), "no logs/ beside the new enclosure's worktrees")
        self.assertEqual(
            Path(contract.worktree_group).parent,
            self.world.coord / "worktrees" / "repo-a",
        )

    def test_both_worktrees_clean_with_files_in_logs(self) -> None:
        contract = load_contract(self.world.contract)
        logs = Path(contract.worktree_group) / ENCLOSURE_LOGS_DIRNAME
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "ar-tools-2026-10-08.jsonl").write_text("{}\n", encoding="utf-8")
        self.assertIsNotNone(contract.code_worktree)
        self.assertIsNotNone(contract.memory_worktree)
        assert contract.code_worktree is not None
        assert contract.memory_worktree is not None
        self.assertFalse(
            worktree_dirty(Path(contract.code_worktree)),
            "enclosure logs/ dirties the code worktree",
        )
        self.assertFalse(
            worktree_dirty(Path(contract.memory_worktree)),
            "enclosure logs/ dirties the memory worktree",
        )
        self.assertFalse((Path(contract.code_worktree) / "logs").exists())
        self.assertFalse((Path(contract.memory_worktree) / "logs").exists())

    def test_closeout_changed_paths_list_no_logs(self) -> None:
        contract = load_contract(self.world.contract)
        logs = Path(contract.worktree_group) / ENCLOSURE_LOGS_DIRNAME
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "ar-tools-2026-10-08.jsonl").write_text("{}\n", encoding="utf-8")
        worklist = closeout_changed_paths(contract)
        logged = [
            path
            for tier in ("all", "working", "committed")
            for path in worklist[tier]
            if "logs" in Path(path).parts
        ]
        self.assertEqual(logged, [])

    def test_cleanup_removes_logs_with_the_enclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp) / "group"
            logs = group / ENCLOSURE_LOGS_DIRNAME
            logs.mkdir(parents=True)
            (logs / "ar-tools-2026-10-08.jsonl").write_text("{}\n", encoding="utf-8")
            removed: list[tuple[str, bool]] = []

            class FakeLifecycle:
                def remove_tree(self, path: Path, *, dry_run: bool) -> dict[str, object]:
                    removed.append((path.as_posix(), dry_run))
                    if not dry_run and path.exists():
                        shutil.rmtree(path, ignore_errors=True)
                    return {"path": path.as_posix(), "removed": True}

            services = mock.Mock()
            services.provider_lifecycle = FakeLifecycle()
            with mock.patch.object(cleanup_module, "worktree_services", return_value=services):
                directories = cleanup_module._removed_directories(
                    SimpleNamespace(worktree_group=group, kind="leaf", task_root=Path(tmp)),
                    dry_run=False,
                )
            self.assertIn(
                (logs.as_posix(), False),
                removed,
                "cleanup never offered the enclosure logs/ to removal",
            )
            self.assertIn("logs", directories)
            self.assertFalse(logs.exists(), "cleanup left the enclosure logs/ behind")


class ToolAnswerReaderChangeTests(unittest.TestCase):
    def test_log_file_and_detail_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_file = log_file_for(root, "2026-10-08")
            self.assertEqual(log_file.name, f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08.jsonl")
            self.assertEqual(
                detail_dir_for(root, "2026-10-08").name,
                f"{TOOL_LOG_BASENAME_PREFIX}2026-10-08",
            )

    def test_enclosure_logs_dir_made_with_the_enclosure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            group = Path(tmp) / "worktree-group"
            made = ensure_enclosure_logs_dir(group)
            self.assertIsNotNone(made)
            assert made is not None
            self.assertEqual(made.name, ENCLOSURE_LOGS_DIRNAME)
            code_tree = group / "code-worktree"
            code_tree.mkdir()
            self.assertFalse((code_tree / ENCLOSURE_LOGS_DIRNAME).exists())


_PACKAGE_DATA = Path(__file__).resolve().parents[1] / "src" / "agents_remember" / "package_data"


def _produced_install_compact(*, stop_ok: bool) -> tuple[dict[str, Any], str]:
    """Run the real install and project it: the operation-produced answer.

    A disposable dry run from the packaged tree with the watcher stop mocked
    at the lifecycle boundary (ok or failed with a fixed process); returns
    the projected compact and the coordination root for normalization. The
    run with the failed stop finds a store of a removed part and a gate log
    in its root, so that it is the largest answer the operation gives.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        coord = (root / "coord").resolve()
        coord.mkdir(parents=True, exist_ok=True)
        if not stop_ok:
            workspace = coord / "logs" / "observer" / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "operator-inbox.jsonl").write_text("{}\n", encoding="utf-8")
            (workspace / "gates.jsonl").write_text("{}\n", encoding="utf-8")
        config = McpRuntimeConfig(
            config_path=coord / "mcp.settings.json",
            coordination_root=coord,
            workspace_root=(root / "ws").resolve(),
            transcript_root=coord / "logs",
            repositories={},
        )
        request = RuntimeInstallRequest(dry_run=True, source_root=_PACKAGE_DATA.resolve())
        if stop_ok:
            stop_result: dict[str, Any] = {
                "provider": "watchers",
                "action": "stop",
                "partial": False,
                "recoveryActions": [],
                "ok": True,
            }
        else:
            stop_result = {
                "provider": "watchers",
                "action": "stop",
                "partial": False,
                "recoveryActions": [],
                "ok": False,
                "process": {"pid": 1234, "command": "cgc watch"},
            }

        def stop_watcher(args: Any, action: str) -> dict[str, Any]:
            return {"provider": "watchers", **stop_result, "action": action}

        with mock.patch.object(
            install_provider_watchers.lifecycle,
            "watchers_run",
            side_effect=stop_watcher,
        ):
            full = run_runtime_install(config, request)
        compact = compact_runtime_install_payload(full, f"{_CASE_ROOT}/report.json")
        raw = json.dumps(compact).replace(coord.as_posix(), _CASE_ROOT)
        # MIK-R71: the host step reports the user settings path under
        # XDG_CONFIG_HOME, which varies per run; normalize its prefix too.
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if xdg:
            raw = raw.replace(xdg, _CASE_ROOT)
        normalized = json.loads(raw)
        return normalized, coord.as_posix()


class L109LargestProductionTests(unittest.TestCase):
    """MIK-R109 F01: the three largest cases are operation-produced and pinned.

    Each producer rebuilds its largest answer from a hermetic fixture and
    the test pins it byte-for-byte against its file; L109_REGEN=1 rewrites
    the files instead of asserting. The pins carry no overflow fallback:
    rowsOnPage counts the delivered rows, decisionRecordCount the named
    records beside their total, and every pin measures within its row bound.
    """

    def _pinned(self, name: str, produced: str) -> None:
        path = _L109_FIXTURES / f"{name}.json"
        if os.environ.get("L109_REGEN") == "1":
            path.write_text(produced, encoding="utf-8")
        self.assertEqual(
            produced,
            path.read_text(encoding="utf-8"),
            f"{name} is not operation-produced",
        )

    def test_read_ar_files_largest_is_operation_produced(self) -> None:
        produced, states = _produce_l109_read_ar_files_largest()
        self.assertEqual(states, {"current": True}, states)
        case = json.loads(produced)
        self.assertEqual(case["state"], "named", case)
        self.assertEqual(len(case["files"]), 20, case)
        self.assertNotEqual(case.get("overflow"), True, case)
        self.assertLessEqual(count_response_tokens(case), bound_for("read_ar_files"))
        self._pinned("read_ar_files_largest", produced)

    def test_knowledge_read_largest_is_operation_produced(self) -> None:
        produced = _produce_l109_knowledge_read_largest()
        case = json.loads(produced)
        self.assertEqual(case["state"], "page", case)
        self.assertNotEqual(case.get("overflow"), True, case)
        rows = case["payload"]["rows"]
        self.assertEqual(case["page"]["rowsOnPage"], len(rows), case)
        self.assertLessEqual(count_response_tokens(case), bound_for("knowledge_read"))
        self._pinned("knowledge_read_largest", produced)

    def test_knowledge_diff_largest_is_operation_produced(self) -> None:
        produced = _produce_l109_knowledge_diff_largest()
        case = json.loads(produced)
        self.assertEqual(case["state"], "compared", case)
        self.assertNotEqual(case.get("overflow"), True, case)
        self.assertLessEqual(len(case["decisionRecords"]), TOOL_ANSWER_LIST_MAX, case)
        self.assertLessEqual(count_response_tokens(case), bound_for("knowledge_diff"))
        self._pinned("knowledge_diff_largest", produced)


class L112FinalRowProductionTests(unittest.TestCase):
    """MIK-R112 F11: the two final rows govern their production answers.

    The row bounds come from the production lookup (bound_for), the row
    fields match the production projection, the cases are operation-produced
    and pinned byte-for-byte, over-bound answers overflow through the
    unchanged L108 guard, and L109-L111 ownership is untouched.
    """

    def test_final_bounds_live_in_the_production_lookup(self) -> None:
        self.assertEqual(L112_FINAL_BOUNDS, {"runtime_install": 600})
        for tool, row in L112_FINAL_ROWS.items():
            if tool in L112_FINAL_BOUNDS:
                self.assertEqual(row.bound, bound_for(tool))

    def test_row_fields_match_the_production_projection(self) -> None:
        self.assertEqual(set(_INSTALL_FIELDS), set(RUNTIME_INSTALL_ANSWER_FIELDS))

    def test_l109_l111_ownership_is_unchanged(self) -> None:
        self.assertNotIn("L112", set(INTERIM_TOOL_LEAF.values()))
        self.assertNotIn("runtime_install", INTERIM_TOOL_LEAF)

    def test_ordinary_install_case_is_operation_produced(self) -> None:
        produced, _ = _produced_install_compact(stop_ok=True)
        self.assertEqual(produced, _INSTALL_ORDINARY_CASE)
        self.assertLessEqual(
            count_response_tokens(dict(_INSTALL_ORDINARY_CASE)),
            bound_for("runtime_install"),
        )

    def test_largest_install_case_is_operation_produced(self) -> None:
        produced, _ = _produced_install_compact(stop_ok=False)
        self.assertEqual(produced, _INSTALL_LARGEST_CASE)
        self.assertLessEqual(
            count_response_tokens(dict(_INSTALL_LARGEST_CASE)),
            bound_for("runtime_install"),
        )

    def test_projection_forwards_no_undeclared_fields(self) -> None:
        for stop_ok in (True, False):
            produced, _ = _produced_install_compact(stop_ok=stop_ok)
            self.assertNotIn("operation", produced)
            self.assertEqual(set(produced) - set(RUNTIME_INSTALL_ANSWER_FIELDS), set())
            for message in produced.get("messages", []):
                self.assertLessEqual(len(message), 201)

    def test_row_bound_governs_install_overflow(self) -> None:
        # Over the 600 row bound but under the 4,000 net: only the row bound
        # overflows this answer, through the unchanged L108 guard.
        whole = dict(_INSTALL_LARGEST_CASE)
        whole["messages"] = [f"note {index:04d}-abcdefgh;" * 8 for index in range(5)]
        measured = count_response_tokens(whole)
        self.assertGreater(measured, bound_for("runtime_install"))
        self.assertLess(measured, TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            config = _stub_config(coord)
            with tool_call_context("runtime_install", {"dry_run": True}, config=config):
                answer = complete_tool_response("runtime_install", whole)
            self.assertTrue(answer.get("overflow"))
            self.assertLessEqual(count_response_tokens(answer), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            lines = [
                json.loads(line)
                for file in sorted(
                    (coord / "logs" / "mcp").glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl")
                )
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0]["guardCut"])


if __name__ == "__main__":
    unittest.main()
