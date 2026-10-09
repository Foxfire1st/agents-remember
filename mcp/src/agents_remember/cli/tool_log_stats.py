"""CLI adapter: per-tool answer sizes read from the tool-call log (MIK-R108 rule 8).

agents-remember tool-log-stats LOG_FOLDER [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--json]

For a log folder and a span of days, prints one line for each tool and
operation: the calls, the median, the 90th percentile, the largest and the
sum of the answer tokens, and how often the guard cut an answer.

The command only reads. Exit status: 0 when the folder is read, 2 when the
folder or a day bound cannot be read.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import date
from pathlib import Path
from typing import Any


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "log_folder", type=Path, help="The log folder holding ar-tools-<YYYY-MM-DD>.jsonl files."
    )
    parser.add_argument("--since", default=None, help="First day, YYYY-MM-DD (inclusive).")
    parser.add_argument("--until", default=None, help="Last day, YYYY-MM-DD (inclusive).")
    parser.add_argument("--json", action="store_true", help="Print the rows as JSON.")


def _parse_day(value: str, label: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} day {value!r} is not YYYY-MM-DD") from None
    return parsed.isoformat()


def _selected_log_files(
    log_folder: Path, *, since: str | None = None, until: str | None = None
) -> list[Path]:
    """The daily logs in the folder inside the day span."""
    if not log_folder.is_dir():
        raise FileNotFoundError(f"log folder does not exist: {log_folder}")
    try:
        files = sorted(log_folder.glob("ar-tools-*.jsonl"))
    except OSError as error:
        raise OSError(f"log folder cannot be listed: {log_folder} ({error})") from error
    if since is not None:
        files = [path for path in files if path.stem >= f"ar-tools-{since}"]
    if until is not None:
        files = [path for path in files if path.stem <= f"ar-tools-{until}"]
    return files


def _iter_log_lines(files: list[Path]):
    """Yield each well-formed log line dict from the selected files."""
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            if not raw.strip():
                continue
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(line, dict):
                yield line


def _summarize(
    samples: dict[tuple[str, str], list[int]],
    guard_cuts: dict[tuple[str, str], int],
) -> list[dict[str, Any]]:
    """One row per tool and operation: calls, median, p90, largest, sum, cuts."""
    rows: list[dict[str, Any]] = []
    for (tool, operation), values in sorted(samples.items()):
        ordered = sorted(values)
        rows.append(
            {
                "tool": tool,
                "operation": operation,
                "calls": len(ordered),
                "median": statistics.median(ordered),
                "p90": ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))],
                "largest": ordered[-1],
                "sum": sum(ordered),
                "guardCuts": guard_cuts.get((tool, operation), 0),
            }
        )
    return rows


def collect_rows(
    log_folder: Path, *, since: str | None = None, until: str | None = None
) -> list[dict[str, Any]]:
    """Aggregate one row per tool and operation over the selected days."""
    samples: dict[tuple[str, str], list[int]] = {}
    guard_cuts: dict[tuple[str, str], int] = {}
    for line in _iter_log_lines(_selected_log_files(log_folder, since=since, until=until)):
        key = (str(line.get("tool", "?")), str(line.get("operation", "?")))
        tokens = line.get("answerTokens")
        samples.setdefault(key, []).append(int(tokens) if isinstance(tokens, int) else 0)
        if line.get("guardCut") is True:
            guard_cuts[key] = guard_cuts.get(key, 0) + 1
    return _summarize(samples, guard_cuts)


def run(args: argparse.Namespace) -> int:
    """Entry point wired as the ``tool-log-stats`` subcommand."""
    try:
        since = _parse_day(args.since, "since") if args.since else None
        until = _parse_day(args.until, "until") if args.until else None
    except ValueError as error:
        print(f"error: {error}")
        return 2
    try:
        rows = collect_rows(Path(args.log_folder), since=since, until=until)
    except (FileNotFoundError, OSError) as error:
        print(f"error: {error}")
        return 2
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no tool calls in the selected span")
        return 0
    print(
        f"{'tool':36} {'operation':28} {'calls':>6} {'median':>7} {'p90':>7} {'largest':>8} {'sum':>10} {'cuts':>5}"
    )
    for row in rows:
        print(
            f"{row['tool']:36} {row['operation']:28} {row['calls']:6d} "
            f"{row['median']:7.0f} {row['p90']:7d} {row['largest']:8d} "
            f"{row['sum']:10d} {row['guardCuts']:5d}"
        )
    return 0
