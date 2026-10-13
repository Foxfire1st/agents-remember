"""Caller-readable reports for the naming reads (MIK-R109 rules 2 and 3).

A read names where a thing is; when the whole list behind an answer is what
the caller works through next — a diff's comparison, a queue's entries — the
list is a file of its own and not a log (MIK-R108 rule 3): under
``<enclosure>/reports/`` for a caller whose log lies in an enclosure, in the
detail folder of the day otherwise. The answer names it. The report is read
with the caller's own tools, so it may hold what an answer never does.

The directory resolves through the same roots as the log line
(:func:`resolve_log_roots`), so a caller that logs to an enclosure reports
to it, and every other caller to the day's detail folder. A report that
cannot be written refuses the call with the reason, and nothing partial is
left: the file is written beside its final name and renamed over it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.application.tool_answer_log import (
    current_tool_call,
    detail_dir_for,
    detail_file_for,
    resolve_log_roots,
)

REPORTS_DIRNAME = "reports"

__all__ = ["REPORTS_DIRNAME", "ReportWriteError", "resolve_reports_dir", "write_report"]


class ReportWriteError(Exception):
    """One report that cannot be written, with the reason the refusal states."""

    def __init__(self, reason: str, residual: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        # The partial file the failed write may have left behind, when
        # cleanup itself failed: the refusal must name it instead of
        # asserting nothing partial was left.
        self.residual = residual


def resolve_reports_dir(
    config: Any | None = None,
    tool_name: str | None = None,
    *,
    coordination_root: Path | str | None = None,
) -> Path | None:
    """Where one call's caller-readable report goes, or ``None``.

    The enclosure's ``reports/`` directory when the ambient lifecycle binds
    the caller to an enclosure that still exists, else the day's detail
    folder under the resolved log root. When no configured root is known,
    ``coordination_root`` stands in with the default log layout beneath it
    (a test server without dispatch configuration). ``None`` when no root is
    known at all: the caller then refuses the report.
    """

    if config is None:
        call = current_tool_call()
        config = call.config if call is not None else None
    dest = resolve_log_roots(config, tool_name)
    if dest.enclosure_dir is not None:
        return dest.enclosure_dir.parent / REPORTS_DIRNAME
    if dest.coord_log_dir is not None:
        return detail_dir_for(dest.coord_log_dir)
    if coordination_root is not None:
        return detail_dir_for(Path(coordination_root) / "logs" / "mcp")
    return None


_REPORT_MARGIN_FRACTION = 32
"""Shared-writer headroom: one thirty-second of the folder budget.

Admission reserves the margin for concurrent tool-log growth between the
usage census and the publish: a report is admitted only when usage, its
own bytes and the margin fit. The fraction scales from test budgets to
production folders, so small budgets still admit small reports while
large folders keep headroom for a concurrent max-detail write.
"""


def write_report(
    stem: str,
    payload: Mapping[str, Any],
    *,
    config: Any | None = None,
    coordination_root: Path | str | None = None,
) -> Path:
    """Write one caller-readable report; return its path.

    ``stem`` is the tool operation (``knowledge-diff``); the file takes the
    detail-file naming beside whichever directory :func:`resolve_reports_dir`
    names. Raises :class:`ReportWriteError` with the refusal's reason,
    leaving nothing partial behind.

    Resource admission (MIK-R109 rule 3): the payload's bytes are counted
    in encoding chunks, never held as one counted string first; the count
    is admitted against the folder's byte budget, then the payload streams
    to its file in chunks. An over-budget report is refused before any
    file is created, never truncated: a caller-readable report is whole or
    absent. Usage is
    the full budget family's current bytes (the whole log root the budget
    covers, shared with the tool log -- not just the reports directory),
    plus a reserved shared-writer margin for concurrent log growth between
    admission and publish. The transcript-root budget admits day-detail
    reports; the enclosure budget admits live-enclosure reports (the
    live-enclosure policy: enclosure reports share the enclosure's budget
    family, admitted against the family's current usage plus margin).
    Usage is measured at write time, and no admission is held beyond it:
    a refused report leaves nothing behind (reclamation), so a smaller
    report still succeeds after it.
    """

    directory = resolve_reports_dir(config, stem, coordination_root=coordination_root)
    if directory is None:
        raise ReportWriteError(
            "the report cannot be written: no log folder is known for this caller, "
            "so there is no reports directory to hold it"
        )
    if config is None:
        # One config binds destination and budget: the ambient call's
        # config when the caller passed none, so admission charges the
        # same family the destination was resolved from.
        call = current_tool_call()
        config = call.config if call is not None else None
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ReportWriteError(
            f"the report cannot be written: the reports directory {directory} "
            f"cannot be made ({error})"
        ) from error
    size = _measured_size(payload)
    budget, usage, margin = _admission(config, stem, directory)
    if usage + size + margin > budget:
        raise ReportWriteError(
            f"the report cannot be written: {size} bytes would exceed the {budget}-byte "
            f"budget for {directory} (already holding {usage} bytes, "
            f"with a {margin}-byte shared-writer margin)"
        )
    path = detail_file_for(directory, stem)
    tmp = path.with_name(path.name + ".part")
    try:
        _stream_report_payload(tmp, payload)
        os.replace(tmp, path)
    except OSError as error:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_error:
            raise ReportWriteError(
                f"the report cannot be written: {path} cannot be written ({error}); "
                f"a partial file remains at {tmp} ({cleanup_error}): remove it before retrying",
                residual=str(tmp),
            ) from error
        raise ReportWriteError(
            f"the report cannot be written: {path} cannot be written ({error})"
        ) from error
    return path


def _measured_size(payload: Mapping[str, Any]) -> int:
    """The payload's serialized bytes, counted in chunks, never held whole.

    Admission needs the size before the file exists; counting the encoding
    chunks measures it without building the one counted string first.
    """

    total = 0
    for chunk in json.JSONEncoder(indent=1).iterencode(payload):
        total += len(chunk.encode())
    return total + len(b"\n")


def _stream_report_payload(tmp: Path, payload: Mapping[str, Any]) -> None:
    """Stream one payload to its partial file in chunks (bounded memory)."""

    with tmp.open("w", encoding="utf-8") as handle:
        for chunk in json.JSONEncoder(indent=1).iterencode(payload):
            handle.write(chunk)
        handle.write("\n")


def _admission(config: Any | None, stem: str, directory: Path) -> tuple[int, int, int]:
    """One report's (budget, usage, margin): full-root accounting.

    Usage is the whole budget family's current bytes -- the reports
    directory the file lands in plus the log root the budget covers,
    shared with the tool log -- never the logs alone. The margin
    reserves shared-writer headroom between this census and the publish.
    A census that hit filesystem errors refuses instead of admitting
    against an undercount: usage that cannot be measured truthfully
    admits nothing.
    """

    budget = _report_budget(config, stem, directory)
    family = _budget_family(config, stem, directory)
    usage = 0
    errors = 0
    for member in family:
        member_usage, member_errors = _directory_usage(member)
        usage += member_usage
        errors += member_errors
    if errors:
        raise ReportWriteError(
            f"the report cannot be written: the {budget}-byte budget for {directory} "
            f"cannot be checked ({errors} files could not be measured); remove "
            "unreadable files before retrying"
        )
    margin = max(budget // _REPORT_MARGIN_FRACTION, 0)
    return budget, usage, margin


def _budget_family(config: Any | None, stem: str, directory: Path) -> tuple[Path, ...]:
    """The folders whose bytes the report's budget covers.

    The enclosure family is the reports directory beside the enclosure
    logs: reports accumulate in ``reports/`` while the tool log grows in
    ``logs/``, and the budget covers both. The day-detail family is the
    log root the detail folder sits under.
    """

    from agents_remember.application.tool_answer_log import (  # noqa: PLC0415
        resolve_log_roots,
    )

    try:
        dest = resolve_log_roots(config, stem)
    except Exception:
        return (directory,)
    if dest.enclosure_dir is not None and directory.is_relative_to(dest.enclosure_dir.parent):
        if directory == dest.enclosure_dir:
            return (directory,)
        return (directory, dest.enclosure_dir)
    if dest.coord_log_dir is not None and directory.is_relative_to(dest.coord_log_dir):
        return (dest.coord_log_dir,)
    return (directory,)


def _report_budget(config: Any | None, tool_name: str, directory: Path) -> int:
    """The byte budget admitting one report in ``directory``."""

    from agents_remember.application.tool_answer_log import (  # noqa: PLC0415
        TOOL_LOG_MAX_BYTES_DEFAULT,
        resolve_log_roots,
    )

    try:
        dest = resolve_log_roots(config, tool_name)
    except Exception:
        return TOOL_LOG_MAX_BYTES_DEFAULT
    if dest.enclosure_dir is not None and directory.is_relative_to(dest.enclosure_dir.parent):
        return dest.enclosure_max_bytes
    if dest.coord_log_dir is not None and directory.is_relative_to(dest.coord_log_dir):
        return dest.max_bytes
    return TOOL_LOG_MAX_BYTES_DEFAULT


def _directory_usage(directory: Path) -> tuple[int, int]:
    """The bytes one family member already holds, with its unreadable count.

    Files that cannot be measured are counted, not silently dropped: the
    caller refuses admission when the count is nonzero rather than
    admitting against an undercount.
    """

    total = 0
    errors = 0
    if not directory.is_dir():
        # A family member that does not exist yet holds no bytes and
        # hides no error: the first write to a fresh directory admits
        # against zero, not against a refusal.
        return total, errors
    try:
        for path in directory.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                errors += 1
    except OSError:
        errors += 1
    return total, errors
