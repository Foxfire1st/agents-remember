"""CLI adapter: the one-time conversion of a memory tree to derived states.

    agents-remember knowledge-convert-states --memory-root MEMORY --task-root TASKS/<repository>
                                             [--apply] [--report FILE]

Without ``--apply`` it writes nothing into the memory and reports what the conversion would change;
with ``--apply`` it writes the changes. The conversion itself is
:mod:`agents_remember.memory.conversion.state_conversion`. It changes the memory under
``--memory-root`` and reads the task documents under ``--task-root``. The report (counts, changed files, and
unresolved owners) is written before any memory file. With ``--report`` the report and memory
files share one transaction, and the console gets a summary. Otherwise the full report is
flushed to stdout, and the exit status says whether any requested writes succeeded.

Exit status: 0 planned or applied; 2 the input could not be read or converted, or the write failed
and every file was restored (nothing changed); 3 the write failed and the files the message names
could not be restored.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agents_remember.errors import StateConversionError
from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.memory.conversion.state_conversion_run import (
    StateConversionRequest,
    StateConversionRun,
    clock_now,
    prepare_state_conversion,
)
from agents_remember.memory.conversion.state_files import PartialStateWrite


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--memory-root", required=True, type=Path, help="The memory working tree to convert."
    )
    parser.add_argument(
        "--task-root",
        required=True,
        type=Path,
        help="The repository's task folder, tasks/<repository>, whose task documents are read.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write the changes (default: report them only)."
    )
    parser.add_argument(
        "--report", type=Path, help="Write the full JSON report here instead of stdout."
    )


def run(args: argparse.Namespace) -> int:
    request = StateConversionRequest(
        memory_root=args.memory_root, task_root=args.task_root, apply=bool(args.apply)
    )
    try:
        _write_result(prepare_state_conversion(request, at=clock_now()), args.report)
    except PartialStateWrite as error:
        print(f"knowledge-convert-states FAILED, PARTLY WRITTEN: {error}", file=sys.stderr)
        return 3
    except (StateConversionError, ValueError, OSError) as error:
        print(f"knowledge-convert-states REFUSED: {error}", file=sys.stderr)
        return 2
    return 0


def _write_result(result: StateConversionRun, destination: Path | None) -> None:
    report = result.report_document()
    if destination is None:
        report["report"] = None
        if sys.stdout is None:
            raise OSError("stdout is closed; use --report with a writable file path")
        sys.stdout.write(_json_text(report))
        sys.stdout.flush()
        if result.request.apply:
            result.apply()
        return

    report_file = result.file_report(destination, _json_text(report).encode("ascii"))
    if result.request.apply:
        result.apply(report=report_file)
    else:
        atomic_write_bytes(report_file.path, report_file.payload)
    summary = {key: value for key, value in report.items() if key != "changedFiles"}
    summary["report"] = destination.as_posix()
    try:
        print(_json_text(summary), end="", flush=True)
    except OSError as error:
        print(
            f"Report saved to {destination}. The console summary failed: {error}", file=sys.stderr
        )


def _json_text(document: dict[str, Any]) -> str:
    # Parsed task JSON can include escaped lone surrogates. ASCII JSON preserves them on output.
    return json.dumps(document, indent=2, ensure_ascii=True) + "\n"
