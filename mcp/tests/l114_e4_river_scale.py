"""MIK-R114 E4 live Bk measurement: concurrent writers plus a connected stream.

Two loads (20,000 and 200,000 total events) over four concurrent writer
processes against one workspace river at the real 16 MiB ceiling, with a
stream connected DURING writing and cuts (no dashboard). Writers record
``ToolEventWriter.record`` acceptance per call and stat the file after every
append; the parent drains ``read_new_events`` incrementally until the writers
join, then drains finally. All rows are young, so only the cut by size can
hold the bound.

Retained output: one result JSON per load plus this script and the exact
command line (see the successor bundle manifest). The river itself lives in a
temporary directory; the bounded capture (counts, peaks, cut observations,
newest rows, stream digest) is what is retained.

Usage:
  PYTHONPATH=<candidate>/mcp/src <venv>/python mcp/tests/l114_e4_river_scale.py \
    --out <retained-dir> [--loads 20000,200000] [--writers 4]
Prints the result JSON to stdout and writes ``e4-load-<total>.json`` per load.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.observer.event_retention import WORKSPACE_EVENT_MAX_BYTES
from agents_remember.observer.store import EventStore, workspace_base_offset
from agents_remember.observer.tool_events import ToolCaller, ToolEventWriter
from agents_remember.serving.events import read_new_events

CEILING = WORKSPACE_EVENT_MAX_BYTES
TOOL = "worktree_status"
FINALIZED = {"ok": True}


def _worker(root: str, count: int, tag: str, report: multiprocessing.Queue) -> None:
    store = EventStore(Path(root))
    writer = ToolEventWriter(store, ToolCaller(role="worker", agent_id=f"agent-{tag}"))
    path = Path(root) / "workspace" / "events.jsonl"
    peak = 0
    over = 0
    admitted = 0
    dropped = 0
    for _ in range(count):
        ok = writer.record(TOOL, FINALIZED, delivered_tokens=7)
        if ok:
            admitted += 1
        else:
            dropped += 1
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        peak = max(peak, size)
        if size > CEILING:
            over += 1
    report.put(
        {
            "tag": tag,
            "peak": peak,
            "over_ceiling_samples": over,
            "admitted": admitted,
            "dropped": dropped,
        }
    )


def _drain_once(root: Path, offsets: dict[str, int], seen: set[str]) -> tuple[dict[str, int], int]:
    """Drain one incremental pass; return the new offsets and new repeats."""
    repeats = 0
    events, offsets = read_new_events(root, offsets)
    for event in events:
        event_id = event.payload.get("id")
        if not isinstance(event_id, str):
            continue
        if event_id in seen:
            repeats += 1
        seen.add(event_id)
    return offsets, repeats


def _run_load(total: int, writers: int) -> dict:
    tmp = tempfile.TemporaryDirectory(prefix="l114-e4-")
    root = Path(tmp.name)
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    report: multiprocessing.Queue = multiprocessing.Queue()
    per_writer, remainder = divmod(total, writers)
    counts = [per_writer + (1 if i < remainder else 0) for i in range(writers)]
    processes = [
        multiprocessing.Process(target=_worker, args=(str(root), counts[i], f"w{i}", report))
        for i in range(writers)
    ]
    seen: set[str] = set()
    repeats = 0
    offsets: dict[str, int] = {}
    cuts_observed = 0
    last_base = 0
    stop_drain = threading.Event()

    def _live() -> None:
        nonlocal repeats, offsets, cuts_observed, last_base
        while not stop_drain.is_set():
            offsets, fresh = _drain_once(root, offsets, seen)
            repeats += fresh
            base = workspace_base_offset(root)
            if base > last_base:
                cuts_observed += 1
                last_base = base
            time.sleep(0.01)

    drainer = threading.Thread(target=_live, daemon=True)
    started = time.monotonic()
    drainer.start()
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    if any(process.exitcode != 0 for process in processes):
        raise RuntimeError("a writer process failed")
    stop_drain.set()
    drainer.join()
    offsets, fresh = _drain_once(root, offsets, seen)
    repeats += fresh
    seconds = time.monotonic() - started

    worker_reports = [report.get() for _ in processes]
    path = root / "workspace" / "events.jsonl"
    end_size = path.stat().st_size
    rows = path.read_text(encoding="utf-8").splitlines()
    file_ids = [json.loads(row)["id"] for row in rows]
    missed = [row_id for row_id in file_ids if row_id not in seen]
    newest = json.loads(rows[-1])
    digest = hashlib.sha256("\n".join(sorted(seen)).encode("utf-8")).hexdigest()
    others = sorted(
        str(other)
        for other in root.rglob("*.jsonl")
        if other != root / "workspace" / "events.jsonl"
    )
    peak = max(r["peak"] for r in worker_reports)
    result = {
        "load_total": total,
        "load_per_writer": counts,
        "writers": writers,
        "seconds": round(seconds, 1),
        "peak_after_append": peak,
        "over_ceiling_samples": sum(r["over_ceiling_samples"] for r in worker_reports),
        "end_size": end_size,
        "ceiling": CEILING,
        "never_over_ceiling": peak <= CEILING
        and end_size <= CEILING
        and all(r["over_ceiling_samples"] == 0 for r in worker_reports),
        "admitted": sum(r["admitted"] for r in worker_reports),
        "dropped": sum(r["dropped"] for r in worker_reports),
        "rows_retained": len(rows),
        "cuts_observed_during_stream": cuts_observed,
        "base_offset_end": workspace_base_offset(root),
        "stream_events_seen": len(seen),
        "stream_repeats": repeats,
        "retained_rows_missed_by_stream": len(missed),
        "newest_retained_row_streamed": newest["id"] in seen,
        "newest_retained_row": {
            "id": newest["id"],
            "ts": newest.get("ts"),
            "tool": newest["data"].get("tool"),
        },
        "stream_id_digest_sha256": digest,
        "second_events_files": others,
    }
    tmp.cleanup()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--loads", default="20000,200000")
    parser.add_argument("--writers", type=int, default=4)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=MCP_SRC.parent.parent,
            check=False,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=MCP_SRC.parent.parent,
            check=False,
        ).stdout.strip()
    except OSError:
        head, status = "unknown", "unknown"
    provenance = {
        "command": " ".join(sys.argv),
        "python": sys.version.split()[0],
        "mcp_src": str(MCP_SRC),
        "candidate_head": head,
        "candidate_status_lines": len(status.splitlines()) if status else 0,
        "measured_at": datetime.now(UTC).isoformat(),
    }
    for total in [int(value) for value in args.loads.split(",")]:
        result = _run_load(total, args.writers)
        result["provenance"] = provenance
        (out / f"e4-load-{total}.json").write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "load": total,
                    **{
                        k: result[k]
                        for k in (
                            "writers",
                            "seconds",
                            "peak_after_append",
                            "end_size",
                            "never_over_ceiling",
                            "admitted",
                            "dropped",
                            "rows_retained",
                            "cuts_observed_during_stream",
                            "stream_events_seen",
                            "stream_repeats",
                            "retained_rows_missed_by_stream",
                            "newest_retained_row_streamed",
                        )
                    },
                }
            )
        )


if __name__ == "__main__":
    main()
