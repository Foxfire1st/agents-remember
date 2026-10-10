"""MIK-R131: what a read-only preview proof admits, and how it checks the record.

A test that proves a ``worktree_start`` preview (``dry_run=True``) leaves the
product's state unchanged admits the record of that call and nothing else.
Per the architect ruling ``notes/run3/L131-THE-RIVERS-EVENT-IS-ALSO-THE-RECORD-OF-THE-CALL-RULING.json``,
the record of a call is what a landed requirement makes every call write:

- the day's line file ``ar-tools-<YYYY-MM-DD>.jsonl`` in the transcript root
  (MIK-R108 rule 3);
- the day's detail folder ``ar-tools-<YYYY-MM-DD>/`` and the files in it
  (MIK-R108 rule 3);
- the file of the workspace's river ``workspace/events.jsonl`` under the
  observer root (MIK-R114 rule 1: the tool server writes one ``tool.completed``
  event per completed call);
- the modification time of the folders that hold these, where the call made
  an entry in them (the transcript root, the day's detail folder, and the
  river's workspace, observer and logs folders; the fixture creates the
  transcript root before the first call, so the proof never admits creating
  ``logs`` itself).

Strict admission boundaries (sealed review L131-R1-F01/F02/F03):

- F01: daily line/detail paths are admitted only for the actual UTC days the
  calls span (both capture days inclusive, so a legitimate midnight crossing
  stays admitted); a record-shaped path from any other day is not the call's
  record. Created record paths must have record kinds (line/detail files are
  files, detail folders are directories).
- F02: a kept directory's mtime may differ only with an admitted entry
  creation in that directory during the interval; a bare mtime touch fails.
- F03: ``unchanged_except`` is a caller-proved append carve-out for the one
  admitted observer event: the caller asserts kind/mode itself and proves the
  byte-append separately, and the skip then covers only the necessary mtime
  and digest move of that proved append.

The admitted record is checked, not left out (rule 3 holds for the river in
the same way):

- the line file and the river file grow by appending: bytes before the call
  are the beginning of bytes after it;
- each new line names the tool of one of the calls made between the two
  states, and every call has its line; each new river event is a completed
  call that names its tool, and every call has its event;
- each new file in the detail folder is one that a new line names in
  ``detail``.

The river's lockfile (``workspace/events.lock``) is layout, not record: the
product only flocks it and never writes it, the fixture pre-creates it empty,
and the proof requires it byte-identical. Every other path keeps its kind,
its mode, its modification time and its bytes, and the refs, the worktrees
and the index of each repository are unchanged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

_LINE_FILE_RE = re.compile(r"^ar-tools-(\d{4}-\d{2}-\d{2})\.jsonl$")
_DETAIL_DIR_RE = re.compile(r"^ar-tools-(\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class PreviewRecordState:
    """One observed fixture state: the whole-root snapshot plus the record bytes."""

    snapshot: dict[str, dict[str, Any]]
    line_files: dict[str, bytes]
    river_events: dict[str, bytes]
    transcript_rel: str
    river_rel: str
    capture_day: str


@dataclass(frozen=True)
class _Admission:
    """The record admission for one comparison: roots plus actual call days."""

    transcript_rel: str
    river_rel: str
    days: frozenset[str]


#: The workspace-river event file where the stock fixture layout resolves it.
RIVER_REL_DEFAULT = "ar-coordination/logs/observer/workspace/events.jsonl"

#: Record kinds a created path must have: files stay files, folders stay folders.
_CREATED_KINDS = {
    "line-file": "file",
    "detail-dir": "directory",
    "detail-file": "file",
    "river-file": "file",
}


def transcript_rel_of(world: Any) -> str:
    """Return the transcript root relative to the fixture root, as snapshot paths name it."""
    return Path(world.config.transcript_root).relative_to(world.root).as_posix()


def river_rel_of(world: Any) -> str:
    """Return the workspace-river event file relative to the fixture root.

    The observer root sits beside the transcript root under the coordination
    root's ``logs``; the river file is ``workspace/events.jsonl`` under it.
    """
    coord = Path(world.config.transcript_root).parent.parent
    return (
        (coord / "logs" / "observer" / "workspace" / "events.jsonl")
        .relative_to(world.root)
        .as_posix()
    )


def _capture_day() -> str:
    """Today's UTC day: one identity bound of the call-day range."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def admitted_call_days(before: PreviewRecordState, after: PreviewRecordState) -> frozenset[str]:
    """The UTC days the calls between two captures may belong to, inclusive on both ends.

    F01: daily record admission binds to these actual call-day identities, so a
    record-shaped path from any other day is not the call's record. Both ends
    are included so a legitimate midnight crossing stays admitted.
    """
    start = min(before.capture_day, after.capture_day)
    end = max(before.capture_day, after.capture_day)
    days: set[str] = set()
    day = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while day <= last:
        days.add(day.isoformat())
        day += timedelta(days=1)
    return frozenset(days)


def _river_folders(river_rel: str) -> set[str]:
    """The folders that hold the river file, up to ``logs``: workspace, observer, logs."""
    river = Path(river_rel)
    return {
        river.parent.as_posix(),
        river.parent.parent.as_posix(),
        river.parent.parent.parent.as_posix(),
    }


def _classify(rel: str, adm: _Admission) -> str | None:
    """Classify one snapshot-relative path: the admitted record, or None.

    F01: daily line/detail paths are admitted only for the actual call days.
    """
    if rel == adm.transcript_rel or rel in _river_folders(adm.river_rel):
        return "mtime-dir"
    if rel == adm.river_rel:
        return "river-file"
    prefix = adm.transcript_rel + "/"
    if not rel.startswith(prefix):
        return None
    rest = rel[len(prefix) :]
    head, slash, _ = rest.partition("/")
    detail_match = _DETAIL_DIR_RE.match(head)
    if detail_match is not None:
        kind = "detail-dir" if not slash else "detail-file"
        day = detail_match.group(1)
    else:
        line_match = _LINE_FILE_RE.match(rest) if not slash else None
        if line_match is None:
            return None
        kind = "line-file"
        day = line_match.group(1)
    if day not in adm.days:
        return None
    return kind


def is_call_record_path(
    rel: str,
    transcript_rel: str,
    admitted_days: frozenset[str],
    river_rel: str = RIVER_REL_DEFAULT,
) -> bool:
    """Whether one snapshot-relative path belongs to the admitted call record."""
    return _classify(rel, _Admission(transcript_rel, river_rel, admitted_days)) is not None


def capture_preview_state(world: Any) -> PreviewRecordState:
    """Snapshot the fixture world together with the current bytes of the record."""
    return attach_record(world, world.snapshot())


def attach_record(world: Any, snapshot: dict[str, dict[str, Any]]) -> PreviewRecordState:
    """Pair an already-taken whole-root snapshot with the current record bytes."""
    transcript_rel = transcript_rel_of(world)
    river_rel = river_rel_of(world)
    transcript = world.root / transcript_rel
    line_files: dict[str, bytes] = {}
    try:
        entries = sorted(transcript.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if entry.is_file() and _LINE_FILE_RE.match(entry.name) is not None:
            line_files[f"{transcript_rel}/{entry.name}"] = entry.read_bytes()
    river_events: dict[str, bytes] = {}
    river_path = world.root / river_rel
    if river_path.is_file():
        river_events[river_rel] = river_path.read_bytes()
    return PreviewRecordState(
        snapshot=snapshot,
        line_files=line_files,
        river_events=river_events,
        transcript_rel=transcript_rel,
        river_rel=river_rel,
        capture_day=_capture_day(),
    )


def _parse_new_lines(
    subject: str, rel: str, before_bytes: bytes, after_bytes: bytes
) -> list[dict[str, Any]]:
    """Parse the rows appended to one append-only record file; fail when earlier bytes moved."""
    if not after_bytes.startswith(before_bytes):
        raise AssertionError(
            f"{subject} {rel} does not grow by appending: "
            "its bytes before the call are not the beginning of its bytes after it"
        )
    lines: list[dict[str, Any]] = []
    for raw in after_bytes[len(before_bytes) :].split(b"\n"):
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            raise AssertionError(
                f"{subject} {rel} holds a new row that does not parse as JSON"
            ) from None
        if not isinstance(line, dict):
            raise AssertionError(f"{subject} {rel} holds a new row that is not an object")
        lines.append(line)
    return lines


def _check_kept_path(
    rel: str,
    before_entry: Any,
    after_entry: Any,
    adm: _Admission,
    unchanged_except: frozenset[str],
) -> None:
    """Assert one path present in both states differs at most as the record admits."""
    kind = _classify(rel, adm)
    if kind in ("mtime-dir", "detail-dir"):
        if (
            before_entry[0] != "directory"
            or after_entry[0] != "directory"
            or before_entry[1] != after_entry[1]
        ):
            raise AssertionError(
                f"preview changed {rel} beyond its modification time: "
                f"{before_entry!r} -> {after_entry!r}"
            )
        return
    if kind in ("line-file", "river-file"):
        if before_entry[0] != after_entry[0] or before_entry[1] != after_entry[1]:
            raise AssertionError(
                f"preview changed {rel} beyond the admitted record: "
                f"{before_entry!r} -> {after_entry!r}"
            )
        return
    if kind is not None:
        if before_entry != after_entry:
            raise AssertionError(
                f"preview changed previously written detail path {rel}: "
                f"{before_entry!r} -> {after_entry!r}"
            )
        return
    if rel not in unchanged_except and before_entry != after_entry:
        raise AssertionError(
            f"preview changed {rel} outside the admitted record: "
            f"{before_entry!r} -> {after_entry!r}"
        )


def _require_entry_causality(
    before_paths: dict[str, Any], after_paths: dict[str, Any], adm: _Admission
) -> None:
    """F02: a kept directory's mtime may differ only with an admitted entry creation.

    Every file under an admitted directory is content-checked elsewhere, so a
    bare mtime change with no new entry is a non-record touch, contrary to
    rule 2's "where the call made an entry".
    """
    added = {rel for rel in after_paths if rel not in before_paths}
    for rel, before_entry in before_paths.items():
        after_entry = after_paths.get(rel)
        if after_entry is None or after_entry == before_entry:
            continue
        if _classify(rel, adm) not in ("mtime-dir", "detail-dir"):
            continue
        children = [
            child
            for child in added
            if child.rpartition("/")[0] == rel and _classify(child, adm) is not None
        ]
        if not children:
            raise AssertionError(
                f"directory {rel} changed modification time without an admitted entry creation: "
                f"{before_entry!r} -> {after_entry!r}"
            )


def _check_paths_admit_only_record(
    before_paths: dict[str, Any],
    after_paths: dict[str, Any],
    adm: _Admission,
    unchanged_except: frozenset[str],
) -> None:
    """Assert the path sets differ at most by admitted record paths, and kept paths match."""
    for rel in before_paths:
        if rel not in after_paths:
            raise AssertionError(f"preview removed {rel}; a read-only proof admits no removal")
    for rel in after_paths:
        if rel in before_paths:
            continue
        kind = _classify(rel, adm)
        required = _CREATED_KINDS.get(kind) if kind is not None else None
        if required is None:
            raise AssertionError(
                f"preview added unexpected path {rel}; "
                "a read-only proof admits only the call days' line file, "
                "the call days' detail folder and the files in it, "
                "and the workspace river's event file"
            )
        if after_paths[rel][0] != required:
            raise AssertionError(
                f"preview added {rel} as {after_paths[rel][0]}; "
                f"the admitted record requires a {required} there"
            )
    for rel, before_entry in before_paths.items():
        after_entry = after_paths.get(rel)
        if after_entry is not None:
            _check_kept_path(rel, before_entry, after_entry, adm, unchanged_except)
    _require_entry_causality(before_paths, after_paths, adm)


def _new_log_lines(before: PreviewRecordState, after: PreviewRecordState) -> list[Any]:
    """Parse every line appended to a line file between the two states."""
    new_lines: list[Any] = []
    for rel, after_bytes in after.line_files.items():
        new_lines.extend(
            (rel, line)
            for line in _parse_new_lines(
                "tool log", rel, before.line_files.get(rel, b""), after_bytes
            )
        )
    for rel in before.line_files:
        if rel not in after.line_files:
            raise AssertionError(f"tool log {rel} vanished; a read-only proof admits no removal")
    return new_lines


def _new_river_events(before: PreviewRecordState, after: PreviewRecordState) -> list[Any]:
    """Parse every event appended to the workspace river between the two states."""
    new_events: list[Any] = []
    for rel, after_bytes in after.river_events.items():
        new_events.extend(
            (rel, event)
            for event in _parse_new_lines(
                "workspace river", rel, before.river_events.get(rel, b""), after_bytes
            )
        )
    for rel in before.river_events:
        if rel not in after.river_events:
            raise AssertionError(
                f"workspace river {rel} vanished; a read-only proof admits no removal"
            )
    return new_events


def _river_event_tool(event: dict[str, Any]) -> str | None:
    """The tool one river event names, or None when it names none."""
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    tool = data.get("tool")
    return tool if isinstance(tool, str) else None


def _check_river_checked(
    before: PreviewRecordState, after: PreviewRecordState, expected_tools: Sequence[str]
) -> None:
    """Assert the river record is checked: appends, tools, and one event per call."""
    new_events = _new_river_events(before, after)
    if len(new_events) != len(expected_tools):
        raise AssertionError(
            f"workspace river holds {len(new_events)} new events for {len(expected_tools)} calls; "
            "each new event must belong to a call that was made, and every call has its event"
        )
    got_tools = sorted(str(_river_event_tool(event)) for _, event in new_events)
    if got_tools != sorted(expected_tools):
        raise AssertionError(
            f"new river events name tools {got_tools}; the calls made were {sorted(expected_tools)}"
        )
    for _, event in new_events:
        if event.get("kind") != "tool.completed":
            raise AssertionError(
                f"new river event is not a completed call: {event!r}; "
                "each new event must be a completed call that names its tool"
            )


def _check_record_checked(
    before: PreviewRecordState,
    after: PreviewRecordState,
    adm: _Admission,
    expected_tools: Sequence[str],
) -> None:
    """Assert the admitted record is checked: appends, tools, and named details."""
    after_paths = after.snapshot["paths"]
    before_paths = before.snapshot["paths"]
    new_lines = _new_log_lines(before, after)
    if len(new_lines) != len(expected_tools):
        raise AssertionError(
            f"tool log holds {len(new_lines)} new lines for {len(expected_tools)} calls; "
            "each new line must belong to a call that was made, and every call has its line"
        )
    got_tools = sorted(str(line.get("tool")) for _, line in new_lines)
    if got_tools != sorted(expected_tools):
        raise AssertionError(
            f"new log lines name tools {got_tools}; the calls made were {sorted(expected_tools)}"
        )
    named_details: set[str] = set()
    for rel, line in new_lines:
        detail = line.get("detail")
        if detail is None:
            continue
        match = _LINE_FILE_RE.match(Path(rel).name)
        assert match is not None
        detail_rel = f"{adm.transcript_rel}/ar-tools-{match.group(1)}/{detail}"
        if detail_rel not in after_paths:
            raise AssertionError(
                f"log line in {rel} names detail file {detail} with no such file; "
                "each new line's detail must name the file the call wrote"
            )
        named_details.add(detail_rel)
    for rel in after_paths:
        if (
            rel not in before_paths
            and _classify(rel, adm) == "detail-file"
            and rel not in named_details
        ):
            raise AssertionError(
                f"detail file {rel} is named by no new log line; "
                "each new file in the detail folder must be one that a new line names"
            )


def assert_preview_read_only(
    before: PreviewRecordState,
    after: PreviewRecordState,
    *,
    expected_tools: Sequence[str],
    expected_river_tools: Sequence[str] | None = None,
    unchanged_except: frozenset[str] = frozenset(),
) -> None:
    """Assert `after` differs from `before` only by the checked record of the calls.

    `expected_tools` names the tool of each call made between the two states,
    one entry per call; `expected_river_tools` names the tool of each
    *completed* call (a call that raised has its line but no river event, and
    a call under an ambient lifecycle has its event in that lifecycle's file,
    proved separately) and defaults to `expected_tools`. Paths in
    `unchanged_except` are the caller's proved append carve-out (F03: the
    caller asserts their kind and mode itself and proves the byte-append
    separately; the skip then covers only the necessary mtime and digest move
    of that proved append); everything else outside the record must be
    byte-identical.
    """
    if before.transcript_rel != after.transcript_rel or before.river_rel != after.river_rel:
        raise AssertionError("preview moved the transcript root or the river itself")
    adm = _Admission(
        transcript_rel=after.transcript_rel,
        river_rel=after.river_rel,
        days=admitted_call_days(before, after),
    )
    _check_paths_admit_only_record(
        before.snapshot["paths"],
        after.snapshot["paths"],
        adm,
        unchanged_except,
    )
    if before.snapshot["repositories"] != after.snapshot["repositories"]:
        raise AssertionError(
            "preview changed the refs, the worktrees or the index of a repository; "
            "a read-only proof admits only the record of the call"
        )
    _check_record_checked(before, after, adm, expected_tools)
    if expected_river_tools is None:
        expected_river_tools = expected_tools
    _check_river_checked(before, after, expected_river_tools)
