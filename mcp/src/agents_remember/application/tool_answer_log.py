"""The daily tool-call log (MIK-R108 rule 3).

Every call of a tool appends one line to ``ar-tools-<YYYY-MM-DD>.jsonl`` (the
day is the UTC day); a refused call and a call that raised are written like
any other. When the whole result is larger than the answer, it is written,
indented, to a detail file beside the log, and the answer's ``log`` names
that file.

Where the line goes: for a call by a role agent that is bound to a task whose
enclosure exists, ``<enclosure>/logs/`` (outside both worktrees and outside
Git); for every other call, the transcript root -- the folder the setting
``transcriptRoot`` names, ``<coordination root>/logs/mcp/`` when the setting
is absent. A process without a coordination root (a test) writes no log and
does not fail. Each log folder has a budget in bytes (rule 3); the server
that writes owns both removals, and a dropped write never fails the call.

A log write that fails does not fail the call: the helpers return the
one-sentence ``logError`` instead of raising.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.application.agent_binding import read_agent_binding
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.runtime_config import TOOL_LOG_KEEP_DAYS_DEFAULT
from agents_remember.kernel.primitives.tool_reports import redact_secrets
from agents_remember.models.tools.tool_answer import (
    LOG_ERROR_BUDGET_DETAIL,
    LOG_ERROR_BUDGET_LINE,
    LOG_ERROR_DETAIL_TOO_LARGE,
    LOG_ERROR_DETAIL_UNSERIALIZABLE,
    LOG_ERROR_DETAIL_UNWRITABLE,
    LOG_ERROR_FOLDER_UNWRITABLE,
    LOG_ERROR_LINE_UNSERIALIZABLE,
    LOG_ERROR_LINE_UNWRITABLE,
)

# Made with the enclosure. It lies outside both worktrees and outside Git.
ENCLOSURE_LOGS_DIRNAME = "logs"
TOOL_LOG_BASENAME_PREFIX = "ar-tools-"
# Rule 3: keeping. In the coordination root, the first call of a day removes
# the log files and detail folders older than keepDays (14 when absent). In
# an enclosure, no age-based removal happens before cleanup. The default
# lives with the settings that parse it
# (kernel/primitives/runtime_config.py).
# Rule 3: a value over 200 characters is replaced by its length and digest.
TOOL_LOG_LONG_VALUE_MAX = 200
# Rule 3: a budget in bytes for everything in a log folder (D1-D4). The log
# is a working record and not the survival layer: where the two conflict,
# the record is dropped. ``toolLog.maxBytes`` bounds the transcript-root
# folder (256 MiB when absent); ``toolLog.enclosureMaxBytes`` bounds the
# ``logs/`` of one enclosure (64 MiB when absent). The defaults live with
# the settings that parse them (kernel/primitives/runtime_config.py).
TOOL_LOG_MAX_BYTES_DEFAULT = 268435456
TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT = 67108864
# Rule 3: a detail file has at most this many bytes. A larger result is not
# written; its line holds its size and its digest.
TOOL_LOG_DETAIL_MAX_BYTES = 8388608
# Rule 3: a write does not list the folder. The server takes the size of the
# folder from a listing it makes at most once in this many seconds, and
# from its own writes since, and it lists a finished day once.
TOOL_LOG_SIZE_CHECK_SECONDS = 60
# Rule 3: the removal by age takes whole days, at most this many in one call;
# what is left goes at the next call.
TOOL_LOG_SWEEP_MAX_DAYS = 31
# The files this leaf owns in a log folder, grouped by UTC day.
_DAY_NAME_RE = re.compile(r"^ar-tools-(\d{4}-\d{2}-\d{2})(\.jsonl)?$")
# Rule 8 sizes are answer tokens; the log line carries the whole-result bytes
# beside them so the two never mix.

# The leaf names the fields it leaves out (amended rule 3): no credential
# in a log. Keys that name a credential are dropped; the value of a
# variable whose name names a credential is left out wherever it occurs in
# an argument, from six characters on — embedded, nested and path-looking
# alike. Every other environment value is ordinary text and is written, so
# home-folder paths stay whole. The test suite proves omission with
# credential keys, credential-variable values (short, slash-bearing and
# path-looking) and nested arguments, and proves home paths whole.
OMITTED_KEY_SUBSTRINGS = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "auth_token",
    "authtoken",
    "access_token",
    "accesstoken",
    "refresh_token",
    "private_key",
    "privatekey",
    "credential",
    "session_token",
    "cookie",
)
# Credential names matched whole or as a hyphen/underscore-suffixed tail, so
# bare "token" never silences "tokens", "tokenizer" or "answerTokens" while
# "token", "auth" and "authorization" spellings are still omitted.
CREDENTIAL_KEY_NAMES = frozenset({"token", "authorization", "auth", "bearer"})


def _is_credential_key(lowered_key: str) -> bool:
    """Whether a mapping key names a credential (rule 3)."""
    if any(part in lowered_key for part in OMITTED_KEY_SUBSTRINGS):
        return True
    if lowered_key in CREDENTIAL_KEY_NAMES:
        return True
    return any(
        lowered_key.endswith(sep + name) for sep in ("_", "-") for name in CREDENTIAL_KEY_NAMES
    )


@dataclass(frozen=True)
class ToolCallContext:
    """One in-flight MCP tool call, installed by the server dispatch."""

    tool_name: str
    arguments: Mapping[str, Any]
    started_perf: float
    config: Any | None = None


_CURRENT_TOOL_CALL: ContextVar[ToolCallContext | None] = ContextVar(
    "agents_remember_tool_call", default=None
)


def current_tool_call() -> ToolCallContext | None:
    """Return the in-flight tool call, or None outside the MCP dispatch."""
    return _CURRENT_TOOL_CALL.get()


@contextmanager
def tool_call_context(
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    config: Any | None = None,
) -> Iterator[ToolCallContext]:
    """Install one tool-call context (the server dispatch; tests)."""
    token = _CURRENT_TOOL_CALL.set(
        ToolCallContext(
            tool_name=tool_name,
            arguments=dict(arguments or {}),
            started_perf=time.perf_counter(),
            config=config,
        )
    )
    try:
        yield _CURRENT_TOOL_CALL.get()  # type: ignore[misc]
    finally:
        _CURRENT_TOOL_CALL.reset(token)


@dataclass(frozen=True)
class ToolCaller:
    """Who made the call, where the server knows it (rule 3)."""

    agent_id: str | None = None
    role: str | None = None
    task: str | None = None


def read_tool_caller() -> ToolCaller:
    """Read the caller from the hosted agent binding."""
    agent_id: str | None = None
    role: str | None = None
    task: str | None = None
    try:
        binding = read_agent_binding()
    except Exception:
        binding = None
    if binding is not None:
        agent_id = binding.agent_id
        role = binding.role
        task = (
            binding.task_ref.path
            if binding.task_ref is not None
            else (binding.master_ref.path if binding.master_ref is not None else None)
        )
    return ToolCaller(agent_id=agent_id, role=role, task=task)


def _bound_enclosure_contract(config: Any | None) -> Path | None:
    """Read the canonical enclosure binding of the caller's task, without a session."""
    if config is None:
        return None
    try:
        from agents_remember.tasks.document_refs import TaskDocumentTopology  # noqa: PLC0415

        binding = read_agent_binding()
        if binding is None or binding.task_ref is None:
            return None
        resolved = TaskDocumentTopology(Path(config.coordination_root)).resolve(binding.task_ref)
        enclosures = resolved.document.enclosures
        if len(enclosures) != 1 or enclosures[0].leafId != resolved.document.id:
            return None
        return Path(enclosures[0].enclosurePath)
    except Exception:
        return None


@dataclass(frozen=True)
class ResolvedLogDestination:
    """Where one call's log line goes, and under which limits (rule 3)."""

    enclosure_dir: Path | None
    coord_log_dir: Path | None
    keep_days: int
    max_bytes: int
    enclosure_max_bytes: int


@dataclass
class _FolderBudgetState:
    """One log folder's size census, owned by the server that writes it.

    ``bytes_at_check`` is the folder size at the last listing;
    ``own_by_day`` is what this server wrote since, per UTC day;
    ``day_sizes`` caches finished days this server listed once;
    ``dropped`` counts writes dropped since the last written line;
    ``sweep_pending`` says a capped age sweep left old days behind.
    """

    checked_at: float = 0.0
    bytes_at_check: int = 0
    own_by_day: dict[str, int] = field(default_factory=dict)
    day_sizes: dict[str, int] = field(default_factory=dict)
    dropped: int = 0
    ever_checked: bool = False
    sweep_pending: bool = False


_FOLDER_BUDGETS: dict[str, _FolderBudgetState] = {}


def _budget_state(log_dir: Path, state: _FolderBudgetState | None = None) -> _FolderBudgetState:
    """Return the budget state for one folder (one server's own)."""
    if state is not None:
        return state
    key = log_dir.as_posix()
    return _FOLDER_BUDGETS.setdefault(key, _FolderBudgetState())


def reset_log_budgets() -> None:
    """Forget every folder census (tests)."""
    _FOLDER_BUDGETS.clear()


# Rule 3: a call that removes an enclosure or ends its task (cleanup,
# abandon, finalize) never writes into that enclosure, whoever makes it.
# These operations always log to the coordination root, even when the
# caller still names a live enclosure.
TERMINATING_TOOL_NAMES = frozenset(
    {"worktree_cleanup", "worktree_abandon", "lifecycle_finalize_task"}
)


def resolve_log_roots(config: Any | None, tool_name: str | None = None) -> ResolvedLogDestination:
    """Resolve where one call's log line goes, and under which limits.

    The enclosure directory is returned only when the caller binding names
    a contract path whose file still exists and whose worktree group still
    exists: a leaf agent before its enclosure exists or after it was removed
    logs to the coordination root like every other caller. A terminating
    operation (cleanup, abandon, finalize) never logs into an enclosure. The
    coordination log directory is None when no coordination root is known (a
    test): the caller then writes no log and does not fail. The folder is the
    resolved ``transcript_root`` -- a custom absolute ``transcriptRoot``
    included -- and ``<coordination root>/logs/mcp/`` only when the setting
    is absent.
    """
    keep_days = TOOL_LOG_KEEP_DAYS_DEFAULT
    max_bytes = TOOL_LOG_MAX_BYTES_DEFAULT
    enclosure_max_bytes = TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT
    coordination_root: Path | None = None
    transcript_root: Path | None = None
    if config is not None:
        coordination_root = Path(config.coordination_root)
        # Minimal fixture configs name only the coordination root; a real
        # serving config always carries transcript_root. Absent it, the
        # default folder below applies (MIK-R108 rule 3).
        transcript_root = getattr(config, "transcript_root", None)
        transcript_root = Path(transcript_root) if transcript_root is not None else None
        tool_log = getattr(config, "tool_log", None)
        keep_days = int(getattr(tool_log, "keep_days", TOOL_LOG_KEEP_DAYS_DEFAULT))
        max_bytes = int(getattr(tool_log, "max_bytes", TOOL_LOG_MAX_BYTES_DEFAULT))
        enclosure_max_bytes = int(
            getattr(tool_log, "enclosure_max_bytes", TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT)
        )
    enclosure_dir: Path | None = None
    contract_path = _bound_enclosure_contract(config)
    if (
        tool_name not in TERMINATING_TOOL_NAMES
        and contract_path is not None
        and contract_path.is_file()
    ):
        try:
            # Worktrees-layer import stays lazy so the application layer
            # never hard-depends on it at import time.
            from agents_remember.worktrees.worktree_contract import load_contract  # noqa: PLC0415

            contract = load_contract(contract_path)
        except Exception:
            contract = None
        if contract is not None:
            group = Path(contract.worktree_group)
            if group.is_dir():
                enclosure_dir = group / ENCLOSURE_LOGS_DIRNAME
                if coordination_root is None:
                    coordination_root = Path(contract.coordination_root)
    if coordination_root is None:
        return ResolvedLogDestination(
            enclosure_dir=enclosure_dir,
            coord_log_dir=None,
            keep_days=keep_days,
            max_bytes=max_bytes,
            enclosure_max_bytes=enclosure_max_bytes,
        )
    log_dir = (
        transcript_root if transcript_root is not None else (coordination_root / "logs" / "mcp")
    )
    return ResolvedLogDestination(
        enclosure_dir=enclosure_dir,
        coord_log_dir=log_dir,
        keep_days=keep_days,
        max_bytes=max_bytes,
        enclosure_max_bytes=enclosure_max_bytes,
    )


def ensure_enclosure_logs_dir(worktree_group: Path) -> Path | None:
    """Make ``<enclosure>/logs/``; None when it cannot be made.

    Called when an enclosure is made and again on every logged call (an
    enclosure's ``logs/`` removed by hand is made again by the next call).
    Lies outside both worktrees and outside Git by construction.
    """
    path = worktree_group / ENCLOSURE_LOGS_DIRNAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path if path.is_dir() else None


def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def log_file_for(log_dir: Path, day: str | None = None) -> Path:
    """The daily log file in one log folder."""
    return log_dir / f"{TOOL_LOG_BASENAME_PREFIX}{day or _utc_day()}.jsonl"


def detail_dir_for(log_dir: Path, day: str | None = None) -> Path:
    """The detail folder of one day, beside its log."""
    return log_dir / f"{TOOL_LOG_BASENAME_PREFIX}{day or _utc_day()}"


def _short_id() -> str:
    return hashlib.sha256(os.urandom(16)).hexdigest()[:8]


def detail_file_for(detail_dir: Path, tool_name: str, detail_id: str | None = None) -> Path:
    """One detail file: ``<time>-<tool>-<short id>.json`` beside the log."""
    stamp = datetime.now(UTC).strftime("%H%M%S%f")
    safe_tool = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in tool_name)
    return detail_dir / f"{stamp}-{safe_tool}-{detail_id or _short_id()}.json"


# Eight words name a credential wherever they occur in a variable name.
CREDENTIAL_NAME_WORDS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "auth",
    "cookie",
)


def _env_values() -> set[str]:
    """Credential-variable values worth redacting (amended rule 3).

    No credential in a log: the searched set holds only the values of
    variables whose name names a credential (the eight words above, any
    case), from six characters on. Every other environment value is
    ordinary text and is written, so paths under the home folder stay
    whole. Credential-naming argument keys are dropped separately.
    """
    values: set[str] = set()
    for name, value in os.environ.items():
        lowered = name.lower()
        if len(value) >= 6 and any(word in lowered for word in CREDENTIAL_NAME_WORDS):
            values.add(value)
    return values


def _scrub_substrings(text: str, env_values: set[str]) -> str | None:
    """Replace embedded credential-variable values in text.

    Every searched value (a credential-named variable's value, six
    characters on) is scrubbed wherever it occurs, including inside
    path-looking query and argument values: no path spelling authorizes
    writing a credential to a log. Ordinary values never enter the searched
    set. Returns the scrubbed text, or None when nothing matched.
    """
    redacted_text = text
    hit = False
    for secret in sorted(env_values, key=len, reverse=True):
        if not secret:
            continue
        if secret in redacted_text:
            redacted_text = redacted_text.replace(secret, "<env>")
            hit = True
    return redacted_text if hit else None


def _redact_mapping(
    value: Mapping[str, Any], env_values: set[str], *, substrings: bool
) -> dict[str, Any]:
    """Omit credential keys from one mapping, redacting the rest."""
    redacted: dict[str, Any] = {}
    for key, item in value.items():
        if _is_credential_key(str(key).lower()):
            redacted[key] = "<omitted>"
        else:
            redacted[key] = _redact_value(item, env_values, substrings=substrings)
    return redacted


def _redact_text(value: str, env_values: set[str], *, substrings: bool) -> Any:
    """Omit one string value: whole-match, substring scrub, or shared rules."""
    if value in env_values:
        return "<env>"
    if substrings:
        scrubbed = _scrub_substrings(value, env_values)
        if scrubbed is not None:
            return scrubbed
    return redact_secrets(value)


def _redact_value(value: Any, env_values: set[str], *, substrings: bool = False) -> Any:
    """Omit credentials and environment values from logged data (rule 3).

    Mapping keys that name a credential are always dropped. A remaining
    value that equals an environment value is always replaced. Longer
    strings are additionally scrubbed for embedded credential-shaped
    environment values only where ``substrings`` is set (call arguments).
    Whole results kept in detail files compare whole-string only, preserving
    result fidelity (a refusal naming a path stays readable).
    """
    if isinstance(value, str):
        return _redact_text(value, env_values, substrings=substrings)
    if isinstance(value, Mapping):
        return _redact_mapping(value, env_values, substrings=substrings)
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, env_values, substrings=substrings) for item in value]
    return value


def shorten_long_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Replace an argument value over 200 chars by its length and digest."""
    env_values = _env_values()
    shortened: dict[str, Any] = {}
    for key, raw in arguments.items():
        if _is_credential_key(str(key).lower()):
            shortened[key] = "<omitted>"
            continue
        redacted = _redact_value(raw, env_values, substrings=True)
        text: str | None = None
        if isinstance(redacted, str):
            text = redacted
        else:
            try:
                text = json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
            except (TypeError, ValueError):
                text = None
        if text is not None and len(text) > TOOL_LOG_LONG_VALUE_MAX:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            shortened[key] = {"length": len(text), "sha256": digest}
        else:
            shortened[key] = redacted
    return shortened


def _write_all(fd: int, data: bytes) -> bool:
    """Write every byte or report failure (rule 3).

    A short write must never be reported as a complete record: the writer
    loops until everything is written, and a write that cannot progress (or
    fails) reports failure so the line is dropped safely instead of leaving
    a partial JSONL record beside whole ones.
    """
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except OSError:
            return False
        if written <= 0:
            return False
        view = view[written:]
    return True


try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

_APPEND_THREAD_LOCK = threading.RLock()


def _append_bytes(path: Path, data: bytes) -> bool:
    """Append whole bytes under whole-record exclusion (rule 3).

    One process-wide threading lock serializes writers in this process and
    an exclusive flock on the open file serializes writers across
    processes; both are held for the entire recovery loop, so a partial
    retry cannot interleave with another record. Neighbors stay
    independently parseable, and a stalled writer reports failure
    truthfully instead of claiming a complete record. The parent directory
    must already exist.
    """
    with _APPEND_THREAD_LOCK:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        except OSError:
            return False
        try:
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except OSError:
                    return False
            return _write_all(fd, data)
        finally:
            with suppress(OSError):
                os.close(fd)


def append_jsonl_line(path: Path, line: Mapping[str, Any]) -> str | None:
    """Append one record; a stalled prefix never joins the next record.

    Returns None on success, the one-sentence logError otherwise. The payload
    is serialized before the file is touched, so a serialization failure is
    also a logError, never a partial line. Every record starts with its own
    newline and carries no trailing one, and the whole record is appended
    under whole-record exclusion: readers skip blank and malformed lines, so
    a prefix left by a stalled write stands alone and neighboring records
    still parse independently.
    """
    try:
        text = "\n" + json.dumps(line, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return LOG_ERROR_LINE_UNSERIALIZABLE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return LOG_ERROR_FOLDER_UNWRITABLE
    if not _append_bytes(path, text.encode("utf-8")):
        return LOG_ERROR_LINE_UNWRITABLE
    return None


def write_detail_file(path: Path, whole_result: Mapping[str, Any]) -> str | None:
    """Write the whole result, indented, to one detail file beside the log."""
    text, failure = _serialize_detail(whole_result)
    if failure is not None or text is None:
        return LOG_ERROR_DETAIL_UNSERIALIZABLE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return LOG_ERROR_FOLDER_UNWRITABLE
    try:
        atomic_write_text(path, text)
    except Exception:
        return LOG_ERROR_DETAIL_UNWRITABLE
    return None


def _day_name(entry_name: str) -> str | None:
    """Return the UTC day of a file this leaf owns, else None."""
    match = _DAY_NAME_RE.match(entry_name)
    return match.group(1) if match else None


def _day_entries(log_dir: Path) -> dict[str, list[Path]]:
    """Group the files this leaf owns in one log folder by UTC day."""
    days: dict[str, list[Path]] = {}
    try:
        entries = list(log_dir.iterdir())
    except OSError:
        return days
    for entry in entries:
        day = _day_name(entry.name)
        if day is None:
            continue
        days.setdefault(day, []).append(entry)
    return days


def _path_bytes(path: Path) -> int:
    """Count one path's bytes without following symlinks."""
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            total = 0
            for root, _dirs, files in os.walk(path):
                for name in files:
                    try:
                        total += (Path(root) / name).stat().st_size
                    except OSError:
                        continue
            return total
    except OSError:
        return 0
    return 0


def _census_bytes(
    log_dir: Path,
    state: _FolderBudgetState,
    now: float,
) -> int:
    """Take the folder size from a listing plus this server's own writes.

    A write does not list the folder: the listing is remade at most once in
    ``TOOL_LOG_SIZE_CHECK_SECONDS``; between two listings the size is the
    last listing plus what this server wrote since. The listing counts
    everything in the folder, owned days or not: the budget is for everything
    in it. Per-day sizes found during the walk are cached, so eviction reuses
    them instead of walking a finished day again. Several servers write the
    same folder, so a budget can be passed by what they write between two
    listings, and by no more.
    """
    if state.ever_checked and now - state.checked_at < TOOL_LOG_SIZE_CHECK_SECONDS:
        return state.bytes_at_check + sum(state.own_by_day.values())
    total = 0
    day_sizes: dict[str, int] = {}
    try:
        entries = list(log_dir.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        size = _path_bytes(entry)
        total += size
        day = _day_name(entry.name)
        if day is not None:
            day_sizes[day] = day_sizes.get(day, 0) + size
    state.bytes_at_check = total
    state.day_sizes = day_sizes
    state.own_by_day = {}
    state.checked_at = now
    state.ever_checked = True
    return total


def _current_bytes(state: _FolderBudgetState) -> int:
    """Fold this server's own writes since the last listing into the size."""
    return state.bytes_at_check + sum(state.own_by_day.values())


def _remove_day(paths: list[Path]) -> list[Path]:
    """Remove one whole day; return the paths still present.

    A day is its line file plus its detail folder, removed with no marker.
    Sizing stays with the caller (the census cache or a fresh walk): a
    successfully removed cached day is never walked again, while a failed
    removal's residual is measured once by the caller.
    """
    for path in paths:
        with suppress(OSError):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    return [path for path in paths if path.exists()]


def _evict_older_days(
    log_dir: Path,
    state: _FolderBudgetState,
    today: str,
    need_bytes: int,
    budget_bytes: int,
) -> bool:
    """Remove whole earlier days, oldest first, until the write fits.

    Before a write that would take a folder over its budget, the server
    removes earlier days whole, oldest first. When only the current day is
    left and the write still does not fit, the write is dropped (False). The
    folder is inventoried once per pass; finished-day sizes come from the
    census cache and are never walked again on success, and a day first
    seen here is measured once, when removed. Only bytes confirmed
    reclaimed leave the census. The caller takes the census first; this
    only subtracts what it removes.
    """
    if _current_bytes(state) + need_bytes <= budget_bytes:
        return True
    days = _day_entries(log_dir)
    for day in sorted(days):
        if day >= today:
            continue
        paths = days[day]
        cached = state.day_sizes.get(day)
        if cached is None:
            cached = sum(_path_bytes(path) for path in paths)
            state.day_sizes[day] = cached
        accounted = cached + state.own_by_day.get(day, 0)
        remaining = _remove_day(paths)
        if not remaining:
            state.bytes_at_check -= accounted
            state.own_by_day.pop(day, None)
            state.day_sizes.pop(day, None)
        else:
            # A failed deletion keeps its bytes in the census: only the
            # confirmed-gone part leaves it, and the residual is re-listed
            # once so the next write accounts for what is really there.
            residual = sum(_path_bytes(path) for path in remaining)
            state.bytes_at_check -= accounted - residual
            state.own_by_day.pop(day, None)
            state.day_sizes[day] = residual
        if _current_bytes(state) + need_bytes <= budget_bytes:
            return True
    return _current_bytes(state) + need_bytes <= budget_bytes


def prune_old_tool_logs(
    log_dir: Path,
    keep_days: int,
    *,
    today: str | None = None,
    now: float | None = None,
    max_days: int = TOOL_LOG_SWEEP_MAX_DAYS,
) -> int:
    """Remove whole days older than keepDays (coord root); return days gone.

    The removal by age takes whole days as well, at most ``max_days`` in one
    call; what is left goes at the next call. In an enclosure, no age-based
    removal happens before cleanup; this runs only for the coordination-root
    folder, on the first call of a day. A failed removal leaves the call
    unaffected: the next day's first call tries again. The walk counts
    everything in the folder and doubles as the size census for the folder
    budget; only bytes confirmed gone leave it.
    """
    day = today or _utc_day()
    moment = now if now is not None else time.time()
    state = _budget_state(log_dir)
    try:
        today_date = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return 0
    try:
        entries = list(log_dir.iterdir())
    except OSError:
        return 0
    total = 0
    days: dict[str, list[Path]] = {}
    for entry in entries:
        size = _path_bytes(entry)
        total += size
        entry_day = _day_name(entry.name)
        if entry_day is not None:
            days.setdefault(entry_day, []).append(entry)
    removed = 0
    for entry_day in sorted(days):
        try:
            age = (today_date - datetime.strptime(entry_day, "%Y-%m-%d")).days
        except ValueError:
            continue
        if age <= keep_days or removed >= max_days:
            continue
        paths = days[entry_day]
        accounted = sum(_path_bytes(path) for path in paths)
        remaining = _remove_day(paths)
        if not remaining:
            total -= accounted
            state.own_by_day.pop(entry_day, None)
            removed += 1
        else:
            residual = sum(_path_bytes(path) for path in remaining)
            total -= accounted - residual
            state.own_by_day.pop(entry_day, None)
            state.day_sizes[entry_day] = residual
    state.bytes_at_check = total
    state.day_sizes = {
        entry_day: sum(_path_bytes(path) for path in paths)
        for entry_day, paths in days.items()
        if all(path.exists() for path in paths)
    }
    state.own_by_day = {}
    state.checked_at = moment
    state.ever_checked = True
    state.sweep_pending = removed >= max_days
    return removed


@dataclass
class ToolLogOutcome:
    """What recording one call produced (never a failure of the call)."""

    log: str | None = None
    log_error: str | None = None
    answer_tokens: int = 0
    result_bytes: int = 0
    guard_cut: bool = False


@dataclass(frozen=True)
class ToolCallRecord:
    """Everything one log line (and its detail file) needs about one call."""

    tool_name: str
    operation: str | None
    arguments: Mapping[str, Any]
    caller: ToolCaller
    ok: bool
    code: str | None
    duration_ms: float
    answer_tokens: int
    whole_result: Mapping[str, Any]
    result_bytes: int
    guard_cut: bool
    needs_detail: bool
    enclosure_dir: Path | None
    coord_log_dir: Path | None
    keep_days: int = TOOL_LOG_KEEP_DAYS_DEFAULT
    max_bytes: int = TOOL_LOG_MAX_BYTES_DEFAULT
    enclosure_max_bytes: int = TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT
    result_digest: str | None = None
    detail_id: str | None = None
    day: str | None = None
    now: float | None = None


@dataclass(frozen=True)
class DetailRequest:
    """One detail file a writing server is asked to store (rule 3)."""

    log_dir: Path
    budget_bytes: int
    tool_name: str
    whole_result: Mapping[str, Any]
    result_digest: str | None = None
    detail_id: str | None = None


@dataclass(frozen=True)
class LineRequest:
    """One log line a writing server is asked to append whole (rule 3)."""

    log_dir: Path
    budget_bytes: int
    line: Mapping[str, Any]


def prepare_log_folder(
    log_dir: Path,
    keep_days: int,
    is_enclosure: bool,
    *,
    day: str | None = None,
    now: float | None = None,
) -> str | None:
    """Make the folder and sweep old days; one sentence on failure, else None.

    An enclosure's ``logs/`` removed by hand is made again by the next call.
    In the coordination root, the first call of a day removes whole days
    older than keepDays (at most ``TOOL_LOG_SWEEP_MAX_DAYS`` per call). A
    failed removal leaves the call unaffected.
    """
    today = day or _utc_day()
    if is_enclosure:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return LOG_ERROR_FOLDER_UNWRITABLE
        if not log_dir.is_dir():
            return LOG_ERROR_FOLDER_UNWRITABLE
        return None
    state = _budget_state(log_dir)
    first_of_day = not (log_dir / f"{TOOL_LOG_BASENAME_PREFIX}{today}.jsonl").exists()
    # A capped sweep leaves old days behind; the next call carries the
    # continuation even when today's log already exists. Failed removals keep
    # the specified next-day retry and are not forced here.
    if first_of_day or state.sweep_pending:
        with suppress(Exception):
            prune_old_tool_logs(log_dir, keep_days, today=today, now=now)
    return None


def prepare_call_detail(
    request: DetailRequest,
    *,
    day: str | None = None,
    now: float | None = None,
    state: _FolderBudgetState | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Write the detail file under the folder budget (rule 3).

    Returns ``(log path | None, logError | None, resultDigest | None)``. A
    result over ``TOOL_LOG_DETAIL_MAX_BYTES`` is not written; its line holds
    its size and its digest. A write that does not fit into the last day is
    dropped and counted; the caller attaches the logError to the answer with
    no ``log``.
    """
    today = day or _utc_day()
    moment = now if now is not None else time.time()
    log_dir = request.log_dir
    budget_bytes = request.budget_bytes
    folder_state = _budget_state(log_dir, state)
    _census_bytes(log_dir, folder_state, moment)
    text, failure = _serialize_detail(request.whole_result)
    if failure is not None or text is None:
        folder_state.dropped += 1
        return (
            None,
            LOG_ERROR_DETAIL_UNSERIALIZABLE,
            request.result_digest,
        )
    detail_bytes = len(text.encode("utf-8"))
    if detail_bytes > TOOL_LOG_DETAIL_MAX_BYTES:
        folder_state.dropped += 1
        return (
            None,
            LOG_ERROR_DETAIL_TOO_LARGE,
            request.result_digest,
        )
    detail_path = detail_file_for(
        detail_dir_for(log_dir, today), request.tool_name, request.detail_id
    )
    if not _evict_older_days(log_dir, folder_state, today, detail_bytes, budget_bytes):
        folder_state.dropped += 1
        return None, LOG_ERROR_BUDGET_DETAIL, request.result_digest
    try:
        detail_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(detail_path, text)
    except Exception:
        folder_state.dropped += 1
        return (
            None,
            LOG_ERROR_DETAIL_UNWRITABLE,
            request.result_digest,
        )
    folder_state.own_by_day[today] = folder_state.own_by_day.get(today, 0) + detail_bytes
    return detail_path.as_posix(), None, None


def write_diff_report(
    log_dir: Path,
    tool_name: str,
    diff: str,
    *,
    day: str | None = None,
    max_bytes: int = 1_048_576,
) -> str | None:
    """Write one whole caller diff as its own report (MIK-R111 rule 2/F01.9).

    The report sits beside the day's detail files, so day-folder eviction
    bounds it like everything else. Returns its path, or ``None`` when the
    diff is over budget or cannot be written: the caller then keeps the
    detail-file promise (or the unretained marker), never a fabricated one.
    """
    raw = diff.encode("utf-8")
    if len(raw) > max_bytes:
        return None
    try:
        target = detail_file_for(detail_dir_for(log_dir, day), tool_name, None)
        report = target.with_suffix(".diff.md")
        report.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(report, diff)
    except Exception:
        return None
    return report.as_posix()


def _call_line_dict(
    record: ToolCallRecord,
    *,
    detail_name: str | None,
    unwritten_digest: str | None,
    dropped: int,
) -> dict[str, Any]:
    """Build one log line (rule 3); the caller appends it under the budget."""
    return {
        "time": _utc_stamp(),
        "tool": record.tool_name,
        "operation": record.operation or record.tool_name,
        "caller": {
            "agentId": record.caller.agent_id,
            "role": record.caller.role,
            "task": record.caller.task,
        },
        "ok": record.ok,
        "code": record.code,
        "durationMs": round(record.duration_ms, 3),
        "answerTokens": record.answer_tokens,
        "resultBytes": record.result_bytes,
        "resultDigest": unwritten_digest,
        "guardCut": record.guard_cut,
        "detail": detail_name,
        "dropped": dropped,
        "arguments": shorten_long_arguments(record.arguments),
    }


def append_call_line(
    request: LineRequest,
    *,
    day: str | None = None,
    now: float | None = None,
    state: _FolderBudgetState | None = None,
) -> str | None:
    """Append one line whole under the folder budget (rule 3).

    A line is appended whole under whole-record exclusion: two writers
    never interleave fragments of their records, in this process or across
    processes. The line carries the count of writes dropped before it, then
    the count resets. A line that does not fit into the last day is dropped
    and counted instead. Returns None on success, the one-sentence logError
    otherwise.
    """
    today = day or _utc_day()
    moment = now if now is not None else time.time()
    log_dir = request.log_dir
    budget_bytes = request.budget_bytes
    folder_state = _budget_state(log_dir, state)
    _census_bytes(log_dir, folder_state, moment)
    # The pending/read/reset transaction rides the same exclusion as the
    # byte append: a concurrent writer must not read a stale pending count
    # before waiting, or a stalled write it never saw would lose its drop
    # accounting when the winner resets the counter.
    with _APPEND_THREAD_LOCK:
        return _append_counted_line(folder_state, request, log_dir, budget_bytes, today)


def _append_counted_line(
    folder_state: _FolderBudgetState,
    request: LineRequest,
    log_dir: Path,
    budget_bytes: int,
    today: str,
) -> str | None:
    """Write one counted line; the caller holds the append exclusion."""
    pending = folder_state.dropped
    entry = dict(request.line)
    entry["dropped"] = pending
    try:
        text = "\n" + json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        folder_state.dropped = pending + 1
        return LOG_ERROR_LINE_UNSERIALIZABLE
    need_bytes = len(text.encode("utf-8"))
    if not _evict_older_days(log_dir, folder_state, today, need_bytes, budget_bytes):
        folder_state.dropped = pending + 1
        return LOG_ERROR_BUDGET_LINE
    log_file = log_file_for(log_dir, today)
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder_state.dropped = pending + 1
        return LOG_ERROR_FOLDER_UNWRITABLE
    if not _append_bytes(log_file, text.encode("utf-8")):
        folder_state.dropped = pending + 1
        return LOG_ERROR_LINE_UNWRITABLE
    folder_state.dropped = 0
    folder_state.own_by_day[today] = folder_state.own_by_day.get(today, 0) + need_bytes
    return None


def _serialize_detail(
    whole_result: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """Serialize the whole result, indented, for one detail file."""
    try:
        text = (
            json.dumps(
                _redact_value(dict(whole_result), _env_values()),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n"
        )
    except (TypeError, ValueError):
        return None, LOG_ERROR_DETAIL_UNSERIALIZABLE
    return text, None


def record_tool_call(record: ToolCallRecord) -> ToolLogOutcome:
    """Append one log line (and its detail file) for one call.

    Never raises and never fails the call: every filesystem failure and
    every dropped-by-budget write becomes the one-sentence ``logError``.
    ``needs_detail`` says the whole result is larger than the answer; when
    the log folder cannot be written at all, no log is written and the
    outcome carries ``logError``. In an enclosure the budget is the only
    removal before cleanup, and it leaves no marker.
    """
    outcome = ToolLogOutcome(answer_tokens=record.answer_tokens, result_bytes=record.result_bytes)
    outcome.guard_cut = record.guard_cut
    log_dir = record.enclosure_dir if record.enclosure_dir is not None else record.coord_log_dir
    if log_dir is None:
        return outcome
    is_enclosure = record.enclosure_dir is not None
    budget_bytes = record.enclosure_max_bytes if is_enclosure else record.max_bytes
    folder_error = prepare_log_folder(
        log_dir,
        record.keep_days,
        is_enclosure,
        day=record.day,
        now=record.now,
    )
    if folder_error is not None:
        outcome.log_error = folder_error
        return outcome
    detail_log: str | None = None
    unwritten_digest: str | None = None
    if record.needs_detail:
        detail_log, detail_error, unwritten_digest = prepare_call_detail(
            DetailRequest(
                log_dir=log_dir,
                budget_bytes=budget_bytes,
                tool_name=record.tool_name,
                whole_result=record.whole_result,
                result_digest=record.result_digest,
                detail_id=record.detail_id,
            ),
            day=record.day,
            now=record.now,
        )
        if detail_log is not None:
            outcome.log = detail_log
        if detail_error is not None:
            outcome.log_error = detail_error
    line = _call_line_dict(
        record,
        detail_name=(Path(detail_log).name if detail_log is not None else None),
        unwritten_digest=unwritten_digest,
        dropped=0,
    )
    failure = append_call_line(
        LineRequest(log_dir=log_dir, budget_bytes=budget_bytes, line=line),
        day=record.day,
        now=record.now,
    )
    if failure is not None and outcome.log_error is None:
        outcome.log_error = failure
    return outcome
