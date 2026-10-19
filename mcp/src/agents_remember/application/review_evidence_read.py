"""Bounded investigation reads at a comparison's exact trees (MIK-R102 rules 3, 4, 5, 7, 8, 12).

The operand is the comparison's own tree, never something the caller can choose:

* a side resolves through the comparison record; a memory base with a conversion applied resolves to
  the **effective converted tree**, with the stored tree named beside it (rules 1, 3, 5);
* a symbolic revision, a branch, a URL, an absolute path, a ``..`` segment or Git pathspec magic is
  refused by name rather than repaired (rule 3);
* the bytes are read through a bounded ``git cat-file --batch`` window, so the declared byte bound
  limits the work actually done instead of truncating a fully materialized blob (rule 4);
* a line range is reached by a bounded scan that reports how far it got and where the next read
  starts, so a continuation always makes progress and an unexamined remainder is never called empty
  (rules 4, 12); a read that reaches the object's end inside the requested range ends at its last
  line and is complete, and a remainder is counted only where the read measured it;
* relationships are read through the landed owners' own schemas -- family members, record links and
  supersession, sidecar realizations and proofs, history rows -- with their direction, revision,
  origin leaf and attempt, and a lookup that failed is not a measured absence (rule 5);
* a claim basis is resolved against the packet that cites it, so a recorded authority rests on an
  object the packet actually holds (rules 7, 8).

Nothing here writes, starts a process of the compared repository, runs a test or launches a role.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.review_evidence_packet import comparison_reference
from agents_remember.kernel.git_command import git_environment, run_git
from agents_remember.models.knowledge.review_evidence import (
    EvidenceLimitMeasurement,
    EvidenceLimits,
)
from agents_remember.models.knowledge.review_evidence_read import (
    EVIDENCE_SIDES,
    EvidenceReadReceipt,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "DEFAULT_READ_LIMITS",
    "MAX_PAGE_LINES",
    "MAX_READ_BYTES",
    "SideOperand",
    "measured_limits",
    "read_bound_source",
    "side_operand",
]

# The shipped bounds. They are a *ceiling per read*, not a claim of performance: a packet that uses
# them without measuring records them as an approval gap (rule 4), which is why
# :func:`measured_limits` exists for the surface that measures real inputs.
MAX_READ_BYTES = 256 * 1024
MAX_PAGE_LINES = 400
MAX_PAGES = 4
MAX_PATHS = 64
MAX_RECORDS = 200
MAX_DEPTH = 2
# The scan that reaches a requested line is bounded separately, and its own bound is what a
# continuation reports when the line was not reached.
MAX_SCAN_BYTES = 4 * MAX_READ_BYTES
_CHUNK = 64 * 1024
_MAX_LISTED_PATHS = 5000

DEFAULT_READ_LIMITS = EvidenceLimits(
    bytes_per_read=MAX_READ_BYTES,
    paths_per_packet=MAX_PATHS,
    records_per_collection=MAX_RECORDS,
    relationship_depth=MAX_DEPTH,
    pages_per_read=MAX_PAGES,
)

_SUBMODULE_MODE = "160000"
_SYMLINK_MODE = "120000"
_TREE_MODE = "040000"
_BINARY_SNIFF_BYTES = 8000
_RECORD_FILE = re.compile(r"^(?P<id>[A-Z]{2,4}-[A-Z0-9]{4,})-[^/]*\.json$")
_PATH_ADDRESS = re.compile(
    r"^(?P<side>code-base|code-candidate|memory-base|memory-candidate):"
    r"(?P<path>[^#]+?)(?:#L(?P<start>[1-9][0-9]*)(?:-L?(?P<end>[1-9][0-9]*))?)?$"
)
_URL_PREFIXES = ("http://", "https://", "file://", "ssh://", "git://")


@dataclass(frozen=True)
class SideOperand:
    """The repository and *effective* tree one side names, and the stored tree beside it (rule 1)."""

    side: str
    repository: Path
    tree: str
    stored_tree: str | None = None

    @property
    def converted(self) -> bool:
        """Whether this operand is a converted base, whose stored tree is a different object."""

        return self.stored_tree is not None and self.stored_tree != self.tree


@dataclass(frozen=True)
class EvidenceAddress:
    """One claim reference resolved to the operands the same read takes (rule 8)."""

    side: str
    path: str
    start_line: int | None = None
    end_line: int | None = None


def measured_limits(
    measurement: EvidenceLimitMeasurement,
    *,
    limits: EvidenceLimits = DEFAULT_READ_LIMITS,
) -> EvidenceLimits:
    """The same bounds with the measurement that earns them recorded (rule 4, evidence 8)."""

    return limits.model_copy(update={"measurement": measurement})


def side_operand(
    record: ReviewTreeComparisonRecord, side: str
) -> SideOperand | EvidenceReadReceipt:
    """The effective operand one side names, or the refusal that side earns (rules 1, 3, 5)."""

    if side not in EVIDENCE_SIDES:
        return _refused(
            record,
            side,
            "unknown-side",
            (
                f"the requested side {side!r} is not one of this comparison's four operands "
                f"({', '.join(EVIDENCE_SIDES)}); a branch, a working checkout and another "
                "comparison are not alternate operands"
            ),
        )
    chosen = {
        "code-base": record.code_base,
        "code-candidate": record.code_candidate,
        "memory-base": record.memory_base,
        "memory-candidate": record.memory_candidate,
    }[side]
    if side == "memory-base" and record.converted_base is not None:
        # The comparison compared the converted tree (rule 1); the stored tree stays named.
        return SideOperand(
            side=side,
            repository=Path(chosen.repository),
            tree=record.converted_base.tree,
            stored_tree=chosen.tree,
        )
    return SideOperand(side=side, repository=Path(chosen.repository), tree=chosen.tree)


# --- bounded blob access ------------------------------------------------------------------------


class _Blob:
    """One blob read through a bounded ``cat-file --batch`` stream, never materialized whole."""

    def __init__(self, process: subprocess.Popen[bytes], size: int) -> None:
        self._process = process
        self._cursor = 0
        self._last_byte: bytes = b""
        # The last chunk read, so a scan that landed inside it can be read again without rewinding
        # the stream: one chunk is all the slack a line position ever needs.
        self._buffer: bytes = b""
        self._buffer_start = 0
        # Every byte this read pulled from the stream, so a resumed scan's discarded prefix is
        # counted as work rather than reported as a 16-byte read.
        self.consumed = 0
        self.read_calls = 0
        self.size = size

    @classmethod
    def open(cls, repository: Path, object_id: str) -> _Blob | str:
        """Ask the repository for one blob's length, keeping the stream for bounded reads."""

        try:
            process = subprocess.Popen(
                ["git", "cat-file", "--batch"],
                cwd=repository,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=git_environment(),
            )
        except OSError as failure:
            return f"Git could not be run for object {object_id} ({failure})"
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(f"{object_id}\n".encode())
        process.stdin.flush()
        header = process.stdout.readline()
        fields = header.split()
        if len(fields) != 3 or fields[1] != b"blob":
            _dispose(process)
            return (
                f"the object {object_id} is not a blob this repository can read "
                f"({header.decode('utf-8', 'replace').strip() or 'no header'})"
            )
        try:
            size = int(fields[2])
        except ValueError:
            _dispose(process)
            return f"the object {object_id} reported no readable size"
        return cls(process, size)

    def read_at(self, offset: int, length: int) -> bytes | str:
        """A bounded window at ``offset``; the cursor only moves forward within one blob."""

        if offset < self._cursor:
            if self._buffer and self._buffer_start <= offset:
                start = offset - self._buffer_start
                return self._buffer[start : start + length]
            return "a bounded read cannot rewind inside one blob"
        try:
            while self._cursor < offset:
                step = min(_CHUNK, offset - self._cursor)
                discard = self._read(step)
                if isinstance(discard, str):
                    return discard
                if not discard:
                    return b""
            return self._read(min(length, self.size - offset))
        except OSError as failure:
            return f"the blob stream failed ({failure})"

    @property
    def ends_with_newline(self) -> bool:
        """Whether the object's last byte is a newline; the line count says how many exist."""

        return self._last_byte == b"\n"

    def _read(self, length: int) -> bytes:
        assert self._process.stdout is not None
        payload = self._process.stdout.read(length)
        self.read_calls += 1
        self.consumed += len(payload)
        if payload:
            self._buffer, self._buffer_start = payload, self._cursor
            self._last_byte = payload[-1:]
        self._cursor += len(payload)
        return payload

    def close(self) -> None:
        """End the stream and both pipes, so no descriptor outlives the read."""

        _dispose(self._process)


def _dispose(process: subprocess.Popen[bytes]) -> None:
    """Stop one ``cat-file`` child and close the pipes this read opened."""

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        process.kill()
        process.wait(timeout=5)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            with contextlib.suppress(OSError):  # a pipe the child already closed
                stream.close()


# --- one read -----------------------------------------------------------------------------------


def read_bound_source(  # noqa: PLR0913, PLR0911 - one read's own operands, bounds and states
    record: ReviewTreeComparisonRecord,
    side: str,
    path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    start_byte: int | None = None,
    resume_line: int | None = None,
    page: int = 0,
    limits: EvidenceLimits = DEFAULT_READ_LIMITS,
) -> EvidenceReadReceipt:
    """Read one path at one bound side, within the declared bounds (rules 3, 4, 12)."""

    operand = side_operand(record, side)
    if isinstance(operand, EvidenceReadReceipt):
        return operand
    refusal = _path_refusal(
        record,
        side,
        path,
        start_line=start_line,
        end_line=end_line,
        page=page,
        limits=limits,
    )
    if refusal is not None:
        return refusal
    if start_byte is not None and (start_byte < 0 or start_byte > MAX_SCAN_BYTES):
        return _refused(
            record,
            side,
            "read-beyond-limit",
            f"the byte offset {start_byte} exceeds the admitted scan bound "
            f"0..{MAX_SCAN_BYTES}; no blob bytes were read; select a nearer range",
        )
    found = _tree_entry(operand.repository, operand.tree, path)
    if isinstance(found, str):
        return _unavailable(record, operand, path, found)
    if found is None:
        return _absent_entry(record, operand, path)
    if found.mode == _TREE_MODE or found.kind == "tree":
        return _unavailable(
            record, operand, path, "this path is a tree at this endpoint rather than a file"
        )
    if found.mode == _SUBMODULE_MODE or found.kind == "commit":
        return _submodule(record, operand, path, found.object_id)
    blob = _Blob.open(operand.repository, found.object_id)
    if isinstance(blob, str):
        return _unavailable(record, operand, path, blob)
    try:
        return _blob_read(
            record,
            operand,
            path,
            entry=found,
            blob=blob,
            start_line=start_line,
            end_line=end_line,
            start_byte=start_byte or 0,
            resume_line=resume_line,
            page=page,
            limits=limits,
            symlink=found.mode == _SYMLINK_MODE,
        )
    finally:
        blob.close()


def _blob_read(  # noqa: PLR0913, PLR0911 - one blob states its own operands and its states
    record: ReviewTreeComparisonRecord,
    operand: SideOperand,
    path: str,
    *,
    entry: _Entry,
    blob: _Blob,
    start_line: int | None,
    end_line: int | None,
    start_byte: int,
    resume_line: int | None,
    page: int,
    limits: EvidenceLimits,
    symlink: bool,
) -> EvidenceReadReceipt:
    """One blob as a bounded, range-addressed window of text (rules 3, 4, 12)."""

    base = _base_fields(record, operand, path, entry.object_id, blob.size, page)
    located = _located_start(
        record,
        operand,
        path,
        blob=blob,
        requested_start=start_line or 1,
        start_byte=start_byte,
        resume_line=resume_line,
        base=base,
    )
    if isinstance(located, EvidenceReadReceipt):
        return located
    offset, scanned = located
    window = blob.read_at(offset, limits.bytes_per_read)
    if isinstance(window, str):
        return _unavailable(record, operand, path, window)
    if symlink:
        return _symlink_receipt(base, window, start_byte, limits).model_copy(
            update={"examined_bytes": blob.consumed}
        )
    if blob.size == 0:
        return _measured_empty(base)
    if b"\x00" in window[:_BINARY_SNIFF_BYTES]:
        return _binary(base, blob.size)
    letter = _TextWindow.open(blob, offset, window, limits)
    if letter is None:
        return _binary(base, blob.size)
    if letter.end == offset and window:
        return _refused(
            record,
            operand.side,
            "read-beyond-limit",
            "the byte window holds no complete UTF-8 character; increase bytes_per_read",
        )
    return _window_receipt(
        blob=blob,
        window=letter,
        start_line=start_line,
        end_line=end_line,
        limits=limits,
        scanned=scanned,
        base=base,
    )


def _located_start(  # noqa: PLR0913 - one read's own operands and its located byte
    record: ReviewTreeComparisonRecord,
    operand: SideOperand,
    path: str,
    *,
    blob: _Blob,
    requested_start: int,
    start_byte: int,
    resume_line: int | None,
    base: dict[str, Any],
) -> tuple[int, int] | EvidenceReadReceipt:
    """Where the requested line starts in the blob, or the receipt that ends the read (rules 3, 12).

    A caller that starts at the object's end asks for a line no byte can hold, so it gets the
    measured range absence rather than an empty line; a scan that could not reach the requested
    line reports what it measured instead of certifying a range it never read.
    """

    offset, scanned = start_byte, 0
    ended = _object_end_absence(blob, offset, requested_start, base)
    if ended is not None:
        return ended
    if requested_start <= 1:
        return offset, scanned
    reached = _scan_to_line(
        blob,
        requested_start,
        MAX_SCAN_BYTES - start_byte,
        start=offset,
        start_line=resume_line if offset > 0 and resume_line else 1,
    )
    if isinstance(reached, str):
        return _unavailable(record, operand, path, reached)
    offset, scanned, position, line_count, at_end = reached
    if position is not None:
        return offset, scanned
    return _scan_cut(
        line_count,
        offset,
        blob.consumed,
        requested_start,
        base,
        start=start_byte,
        at_end=at_end,
    )


def _symlink_receipt(
    base: dict[str, Any],
    window: bytes,
    start_byte: int,
    limits: EvidenceLimits,
) -> EvidenceReadReceipt:
    payload = window[: limits.bytes_per_read]
    end = start_byte + len(payload)
    return EvidenceReadReceipt(
        state="symlink",
        returned_bytes=len(payload),
        text=payload.decode("utf-8", "replace"),
        next_byte=end,
        detail=(
            "this endpoint's entry is a symlink: the target recorded in the tree is shown, and the "
            "link is not followed"
        ),
        **base,
    )


# --- the text window ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _TextWindow:
    """The decoded bytes of one window and where it starts and ends inside the object."""

    text: str
    offset: int
    end: int
    cut: bool
    examined: int

    @classmethod
    def open(
        cls, blob: _Blob, offset: int, window: bytes, limits: EvidenceLimits
    ) -> _TextWindow | None:
        """Decode a window, trimming a trailing partial character; ``None`` when it is not text."""

        payload = window
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            trimmed = _trim_to_boundary(payload)
            if trimmed is None:
                return None
            payload = trimmed
            text = payload.decode("utf-8")
        if b"\x00" in payload[:_BINARY_SNIFF_BYTES]:
            return None
        wanted = min(limits.bytes_per_read, blob.size - offset)
        return cls(
            text=text,
            offset=offset,
            end=offset + len(payload),
            cut=len(payload) < wanted,
            examined=len(window),
        )


def _trim_to_boundary(payload: bytes) -> bytes | None:
    """The longest prefix of ``payload`` that ends on a UTF-8 character boundary."""

    for back in range(1, min(5, len(payload) + 1)):
        candidate = payload[: len(payload) - back]
        try:
            candidate.decode("utf-8")
        except UnicodeDecodeError:
            continue
        return candidate
    return None


def _window_receipt(  # noqa: PLR0913 - one window states its own bounds and facts
    *,
    blob: _Blob,
    window: _TextWindow,
    start_line: int | None,
    end_line: int | None,
    limits: EvidenceLimits,
    scanned: int = 0,
    base: dict[str, Any],
) -> EvidenceReadReceipt:
    """Turn one decoded window into lines, with an honest remainder and a progressing continuation."""

    requested_start = start_line or 1
    lines = window.text.split("\n")
    if window.text.endswith("\n"):
        lines = lines[:-1]
    complete = len(lines)
    line_open = not window.text.endswith("\n") and window.end < blob.size
    if line_open and lines:
        complete -= 1
    take, next_byte, line_cut = _take_window(
        window, lines, complete, requested_start=requested_start, end_line=end_line, limits=limits
    )
    returned_end = requested_start + len(take) - 1 if take else requested_start - 1
    remaining, state, object_last_line = _remainder(
        returned_end,
        end_line,
        bool(take),
        measured_through=requested_start + complete - 1 if complete else requested_start - 1,
        reached_end=window.end >= blob.size,
        line_cut=line_cut,
    )
    return _receipt_from_window(
        window=window,
        take=take,
        requested_start=requested_start,
        returned_end=returned_end,
        next_byte=next_byte,
        remaining=remaining,
        state=state,
        scanned=scanned,
        end_line=end_line,
        size=blob.size,
        consumed=blob.consumed,
        line_cut=line_cut,
        object_last_line=object_last_line,
        base=base,
    )


def _receipt_from_window(  # noqa: PLR0913 - one window's measured facts, stated once
    *,
    window: _TextWindow,
    take: list[str],
    requested_start: int,
    returned_end: int,
    next_byte: int | None,
    remaining: int | None,
    state: str,
    scanned: int,
    end_line: int | None,
    size: int,
    base: dict[str, Any],
    consumed: int = 0,
    line_cut: bool = False,
    object_last_line: int | None = None,
) -> EvidenceReadReceipt:
    truncated = state == "truncated"
    return EvidenceReadReceipt(
        state=state,  # type: ignore[arg-type]
        requested_range=(requested_start, end_line or requested_start),
        returned_range=(requested_start, returned_end) if take else None,
        start_byte=window.offset,
        next_byte=next_byte,
        # The stream's own consumption is the floor: a resumed read's discarded prefix is work.
        examined_bytes=max(scanned + window.examined, consumed),
        returned_bytes=len("\n".join(take).encode()),
        continuation=(
            f"start_byte={next_byte};resume_line={returned_end if line_cut else returned_end + 1};"
            f"start_line={returned_end if line_cut else returned_end + 1}"
            + (f";end_line={end_line}" if end_line is not None else "")
            if truncated and next_byte is not None
            else None
        ),
        remaining_state=(
            "known" if remaining is not None else ("unknown" if truncated else "none")
        ),
        remaining_lines=remaining,
        limit=(
            "page_lines"
            if truncated and not line_cut and len(take) >= MAX_PAGE_LINES
            else "bytes_per_read"
            if truncated
            else None
        ),
        text="\n".join(take),
        detail=_window_detail(
            requested_start,
            end_line,
            returned_end,
            window_length=len(window.text),
            size=size,
            remaining=remaining,
            truncated=truncated,
            object_last_line=object_last_line,
        ),
        **base,
    )


def _window_detail(  # noqa: PLR0913 - one window's measured facts, each its own field
    requested_start: int,
    end_line: int | None,
    returned_end: int,
    *,
    window_length: int,
    size: int,
    remaining: int | None,
    truncated: bool,
    object_last_line: int | None = None,
) -> str:
    detail = (
        f"requested {requested_start}-{end_line or 'end of object'}; returned "
        f"{requested_start}-{returned_end} from a {window_length}-character window of a {size}-byte "
        "object"
    )
    if object_last_line is not None:
        return (
            detail + f"; the object's last line is line {object_last_line} and the requested range "
            "runs past it"
        )
    if remaining is not None:
        return detail + f"; {remaining} line(s) remain in the requested range"
    if truncated:
        return detail + "; the object holds unexamined bytes and their line count was not measured"
    return detail + "; the requested source is complete"


def _take_window(  # noqa: PLR0913 - one window, its lines and its three bounds
    window: _TextWindow,
    lines: list[str],
    complete: int,
    *,
    requested_start: int,
    end_line: int | None,
    limits: EvidenceLimits,
) -> tuple[list[str], int | None, bool]:
    """The lines this read returns, the byte the next read starts at, and whether a line was cut.

    A line longer than the admitted window is returned as the prefix this read examined, marked as
    cut, so the receipt says *truncated* with a continuation inside the same line rather than
    certifying a prefix as the whole requested line.
    """

    take = lines[:complete][:MAX_PAGE_LINES]
    if end_line is not None:
        take = take[: max(0, end_line - requested_start + 1)]
    consumed = len("\n".join(take).encode())
    if take and (len(take) < len(lines) or window.text.endswith("\n")):
        consumed += 1
    next_byte = window.offset + consumed
    if not take and lines:
        prefix = window.text[: limits.bytes_per_read]
        return [prefix], window.offset + len(prefix.encode()), True
    return take, next_byte, False


def _remainder(  # noqa: PLR0913 - one window's returned range, bounds and cut
    returned_end: int,
    end_line: int | None,
    returned: bool,
    *,
    measured_through: int,
    reached_end: bool,
    line_cut: bool = False,
) -> tuple[int | None, str, int | None]:
    """The state, the remainder when it was measured, and the object's last line when it ended.

    A remaining count is exact only where the read measured it: the examined bytes hold the
    requested range's end, or the object's end. The distance from the returned range to a
    requested end line that was never read is not a count of lines that exist. A read that
    reached the object's end before its requested end line, having returned every line from its
    start to that end, is complete; ``object_last_line`` names that end for the detail.
    """

    if line_cut:
        return None, "truncated", None
    if not returned:
        return None, "absent", None
    if end_line is not None and returned_end >= end_line:
        # The requested range was returned whole; bytes after it are not part of the request.
        return 0, "present", None
    if end_line is not None and measured_through >= end_line:
        # The requested end line was read, so the rest of the request is exactly countable.
        return end_line - returned_end, "truncated", None
    if not reached_end:
        return None, "truncated", None
    return _measured_object_end(returned_end, end_line, measured_through)


def _measured_object_end(
    returned_end: int, end_line: int | None, measured_through: int
) -> tuple[int | None, str, int | None]:
    """The remainder of a read whose examined bytes hold the object's end (rules 1, 2).

    Every line that exists was measured, so a page bound that held lines back leaves an exact
    count, and a read that returned them all is complete even though its requested range runs
    past the end. ``object_last_line`` names that end for the detail.
    """

    if returned_end < measured_through:
        return measured_through - returned_end, "truncated", None
    return 0, "present", measured_through if end_line is not None else None


def _scan_to_line(
    blob: _Blob, target: int, budget: int, *, start: int = 0, start_line: int = 1
) -> tuple[int, int, str | None, int | None, bool] | str:
    """Scan forward from ``start`` to the byte where ``target`` starts, within ``budget`` bytes.

    ``start_line`` is the line the byte ``start`` begins, so a continuation that already sits on the
    requested line is reached at once. Only a scan that began at byte zero knows the object's full
    line count; a resumed one reports how far it got.
    """

    if start_line >= target:
        return start, 0, "found", None, False
    offset = start
    line = start_line
    scanned = 0
    while line < target and offset < blob.size and scanned < budget:
        chunk = blob.read_at(offset, min(_CHUNK, budget - scanned))
        if isinstance(chunk, str):
            return chunk
        if not chunk:
            return offset, scanned, None, line, True
        remaining = target - line
        count = chunk.count(b"\n")
        if count >= remaining:
            index = -1
            for _ in range(remaining):
                index = chunk.index(b"\n", index + 1)
            position = offset + index + 1
            if position < blob.size:
                return position, scanned + len(chunk), "found", None, False
            # The nth newline was the object's last byte: no line begins at that offset.
            line += count
            scanned += len(chunk)
            offset = position
            break
        line += count
        scanned += len(chunk)
        offset += len(chunk)
    if offset >= blob.size:
        held = line - 1 if blob.ends_with_newline else line
        return offset, scanned, None, held, True
    return offset, scanned, None, line, False


def _decode_at(blob: _Blob, offset: int, limits: EvidenceLimits) -> _TextWindow | None:
    """Decode one window at a byte offset that the same blob stream has reached."""

    window = blob.read_at(offset, limits.bytes_per_read)
    if isinstance(window, str) or not window:
        return None
    return _TextWindow.open(blob, offset, window, limits)


def _object_end_absence(
    blob: _Blob, offset: int, requested_start: int, base: dict[str, Any]
) -> EvidenceReadReceipt | None:
    """The measured range absence for a read that starts at or past the object's end (rule 3).

    No line begins at or after that byte, so the answer is an absence about the requested range,
    never an empty line and never a continuation that reads no byte.
    """

    if blob.size == 0 or offset < blob.size:
        return None
    return _scan_cut(
        None,
        offset,
        blob.consumed,
        requested_start,
        base,
        start=offset,
        at_end=True,
    )


def _scan_cut(  # noqa: PLR0913 - one scan's own bounds and its measured result
    line_count: int | None,
    offset: int,
    scanned: int,
    requested_start: int,
    base: dict[str, Any],
    *,
    start: int = 0,
    at_end: bool = False,
) -> EvidenceReadReceipt:
    """The requested line was not reached: a measured absence past the end, or a scan that stopped."""

    if not at_end and offset <= start:
        return EvidenceReadReceipt(
            state="refused",
            refusal_code="read-beyond-limit",
            examined_bytes=scanned,
            detail=f"line {requested_start} was not reached within the admitted "
            f"{MAX_SCAN_BYTES} scan bytes; select a nearer range",
            **base,
        )

    if at_end:
        held = (
            f"the object holds {line_count} line(s)"
            if start == 0 and line_count is not None
            else f"the object ends before any line {requested_start} at or after byte {start}"
        )
        return EvidenceReadReceipt(
            state="absent",
            requested_range=(requested_start, requested_start),
            examined_bytes=scanned,
            next_byte=offset,
            detail=f"{held}, so line {requested_start} lies past its end: a measured range "
            "absence, not an empty file",
            **base,
        )
    return EvidenceReadReceipt(
        state="truncated",
        requested_range=(requested_start, requested_start),
        start_byte=offset,
        next_byte=offset,
        examined_bytes=scanned,
        continuation=f"start_byte={offset};resume_line={line_count};start_line={requested_start}",
        remaining_state="unknown",
        limit="scan_bytes",
        detail=(
            f"reaching line {requested_start} needs a scan longer than the admitted "
            f"{MAX_SCAN_BYTES} byte(s); {scanned} byte(s) were examined and the continuation "
            "resumes the scan at the reported byte"
        ),
        **base,
    )


# --- entry states -------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    """One ``ls-tree`` record: Git's mode and type for the path, and the object it names."""

    mode: str
    kind: str
    object_id: str


def _base_fields(  # noqa: PLR0913, PLR0917 - the six facts every receipt of one read states
    record: ReviewTreeComparisonRecord,
    operand: SideOperand,
    path: str,
    object_id: str,
    size: int,
    page: int,
) -> dict[str, Any]:
    return {
        "side": operand.side,
        "comparison_number": record.number,
        "comparison_reference": comparison_reference(record),
        "repository": str(operand.repository),
        "tree": operand.tree,
        "stored_tree": operand.stored_tree,
        "path": path,
        "object_id": object_id,
        "object_size": size,
        "page": page,
    }


def _measured_empty(base: dict[str, Any]) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="present",
        text="",
        detail=(
            "measured empty: the tree holds an entry at this path and its object is zero bytes; "
            "this is not a missing path and no other tree was read"
        ),
        **base,
    )


def _binary(base: dict[str, Any], size: int) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="binary",
        detail=(
            f"this endpoint holds {size} byte(s) that are not lossless UTF-8 text, so no text form "
            "of the content exists here; the exact object identity and size are stated instead"
        ),
        **base,
    )


def _submodule(
    record: ReviewTreeComparisonRecord,
    operand: SideOperand,
    path: str,
    object_id: str,
) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="submodule",
        side=operand.side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        repository=str(operand.repository),
        tree=operand.tree,
        stored_tree=operand.stored_tree,
        path=path,
        object_id=object_id,
        detail=(
            f"this endpoint's entry is a submodule pointer to {object_id}: the entry has no file "
            "bytes of its own and its working tree is not read"
        ),
    )


def _absent_entry(
    record: ReviewTreeComparisonRecord, operand: SideOperand, path: str
) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="absent",
        side=operand.side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        repository=str(operand.repository),
        tree=operand.tree,
        stored_tree=operand.stored_tree,
        path=path,
        detail=(
            "the requested tree holds no entry at this path: a measured fact about this side, not "
            "an empty file and not a failed read"
        ),
    )


def _unavailable(
    record: ReviewTreeComparisonRecord,
    operand: SideOperand,
    path: str,
    detail: str,
) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="unavailable",
        side=operand.side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        repository=str(operand.repository),
        tree=operand.tree,
        stored_tree=operand.stored_tree,
        path=path,
        detail=detail,
    )


def _refused(
    record: ReviewTreeComparisonRecord, side: str, code: str, detail: str
) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="refused",
        side=side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        detail=detail,
        refusal_code=code,
    )


# --- the requested operand ----------------------------------------------------------------------


def _path_refusal(  # noqa: PLR0913 - the read's own operands and bounds, checked once
    record: ReviewTreeComparisonRecord,
    side: str,
    path: str,
    *,
    start_line: int | None,
    end_line: int | None,
    page: int,
    limits: EvidenceLimits,
) -> EvidenceReadReceipt | None:
    """Why this read's operand or bound is inadmissible, or ``None`` when it is admissible."""

    reason = _path_problem(path)
    if reason is not None:
        return _refused(record, side, reason[0], reason[1])
    if page < 0 or page >= limits.pages_per_read:
        return _refused(
            record,
            side,
            "read-beyond-limit",
            f"page {page} is beyond the declared bound of {limits.pages_per_read} page(s) per read",
        )
    for bound in (start_line, end_line):
        if bound is not None and bound < 1:
            return _refused(
                record, side, "range-invalid", "a line range is one-based and addresses no line 0"
            )
    if start_line is not None and end_line is not None and end_line < start_line:
        return _refused(record, side, "range-invalid", "the requested range ends before it starts")
    return None


def _path_problem(path: str) -> tuple[str, str] | None:
    """The refusal code and sentence one path spelling earns, or ``None`` when it is an address."""

    if not path or "\x00" in path:
        return ("path-escape", "the requested path is empty or holds a NUL byte")
    if path.lower().startswith(_URL_PREFIXES):
        return (
            "arbitrary-url",
            "the requested operand is a URL: this surface reads the comparison's own Git trees and "
            "fetches no external material",
        )
    if path.startswith("/") or path.startswith(":") or ".." in path.split("/"):
        return (
            "path-escape",
            (
                f"the requested path {path!r} is not confined to the tree: an absolute path, a '..' "
                "segment and a leading ':' (Git pathspec magic) are all refused"
            ),
        )
    if re.match(r"^[A-Za-z][\w./-]*:", path):
        return (
            "symbolic-revision",
            (
                f"the requested operand {path!r} is a revision:path spelling; a symbolic HEAD, a "
                "branch name and a working checkout are not alternate operands"
            ),
        )
    return None


def _tree_entry(repository: Path, tree: str, path: str) -> _Entry | str | None:
    """One path's entry in one tree, the sentence saying why it could not be read, or ``None``."""

    try:
        result = run_git(repository, ["ls-tree", "-z", tree, "--", f":(literal){path}"])
    except OSError as failure:
        return (
            f"the entry at this path could not be looked up in tree {tree} because Git could not be "
            f"run ({failure}), so this side's content is unknown rather than absent"
        )
    if result.returncode != 0:
        return (
            f"the entry at this path could not be looked up in tree {tree} (git ls-tree exited "
            f"{result.returncode}): the tree may be gone, and no other tree was substituted"
        )
    record = result.stdout.split("\0", 1)[0]
    if not record:
        return None
    header, _separator, named = record.partition("\t")
    fields = header.split()
    if len(fields) < 3 or named != path:
        return (
            "the tree lookup answered with a record this surface does not read as the requested "
            "path's entry, so this side's content is unknown rather than another entry's bytes"
        )
    return _Entry(mode=fields[0], kind=fields[1], object_id=fields[2])
