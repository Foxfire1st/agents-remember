#!/usr/bin/env python3
"""Sample the installed dashboard's own load for MIK-R81 rules 2 and 4.

Only reads: ``GET /api/diagnostics/background`` on the installed dashboard and
``/proc/loadavg`` on this machine. It never starts, stops or restarts the
dashboard or the host, and it sends no request to the host.

Usage (run while agents work, then again for a quiet window)::

    python scripts/measure-background.py --base-url http://127.0.0.1:8099 \\
        --duration-seconds 600 --out /tmp/mik-r81-background-busy.jsonl

Output: one JSON object per line (``sample`` rows plus a final ``summary`` row)
with every number beside the machine's 1-minute load average, so the quiet
budget's "load average under 2.0" is checkable from the same rows.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

SAMPLE_TIMEOUT_SECONDS = 10


def read_loadavg() -> float | None:
    try:
        return float(Path("/proc/loadavg").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def read_background(base_url: str) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(
            f"{base_url}/api/diagnostics/background", timeout=SAMPLE_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def summarize(samples: list[dict[str, object]]) -> dict[str, object]:
    ok = [sample for sample in samples if sample.get("background") is not None]
    owners: dict[str, dict[str, float]] = {}
    wakes_by_reason: dict[str, int] = {}
    for sample in ok:
        background = sample["background"]
        assert isinstance(background, dict)
        for owner, row in (background.get("owners") or {}).items():
            assert isinstance(row, dict)
            slot = owners.setdefault(str(owner), {"cpuSeconds": 0.0, "units": 0.0})
            slot["cpuSeconds"] += float(row.get("cpuSecondsLast60s") or 0.0)
            slot["units"] += float(row.get("units") or 0)
        for reason, count in ((background.get("wakes") or {}).get("byReason") or {}).items():
            wakes_by_reason[str(reason)] = wakes_by_reason.get(str(reason), 0) + int(count)
    span = 0.0
    if len(ok) > 1:
        first, last = ok[0]["t"], ok[-1]["t"]
        if isinstance(first, (int, float)) and isinstance(last, (int, float)):
            span = last - first
    return {
        "type": "summary",
        "samples": len(samples),
        "answered": len(ok),
        "spanSeconds": span,
        "ownersCpuSecondsLast60sSummed": {
            owner: round(row["cpuSeconds"], 3) for owner, row in sorted(owners.items())
        },
        "wakesByReason": wakes_by_reason,
        "loadavg1": [sample["loadavg1"] for sample in samples],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8099")
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    samples: list[dict[str, object]] = []
    deadline = time.time() + args.duration_seconds
    while True:
        samples.append(
            {
                "type": "sample",
                "t": time.time(),
                "loadavg1": read_loadavg(),
                "background": read_background(args.base_url),
            }
        )
        if time.time() >= deadline:
            break
        time.sleep(args.interval_seconds)
    samples.append(summarize(samples))
    Path(args.out).write_text(
        "".join(json.dumps(sample) + "\n" for sample in samples), encoding="utf-8"
    )
    print(f"wrote {len(samples)} rows to {args.out}")


if __name__ == "__main__":
    main()
