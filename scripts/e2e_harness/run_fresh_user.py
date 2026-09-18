"""Entry point for the fresh-user acceptance: two clean-room fixtures, one transcript.

    python scripts/e2e_harness/run_fresh_user.py --reports <dir> [--work-root <dir>]

The run root is a fresh temporary directory unless ``--work-root`` names one, so a successor can
re-run this from the repository alone and read the same transcript shape. Nothing outside the
run root is read or written.

This entry point deliberately runs on the host rather than inside the Dagger graph. The packet
carries a **clean environment** requirement, not a Dagger-graph requirement: the fixtures are
created from nothing under the run root and no machine-local state is consulted, which is what
the certification would have bought here. The existing ``run.py`` entry point keeps its own
Dagger admission for the ambient role-chat scenario and is not modified by this module.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
# Strings, not Path objects: a non-str ``sys.path`` entry is silently ignored by the importer.
if HARNESS_DIR.as_posix() not in sys.path:
    sys.path.insert(0, HARNESS_DIR.as_posix())

from fresh_user_scenario import environment_facts, run_fresh_user_acceptance  # noqa: E402
from reporting import write_json  # noqa: E402

REPORT_DIRECTORY = "fresh-user-acceptance"


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, required=True, help="Where the transcript lands.")
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="The run root. Defaults to a fresh temporary directory that this run removes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    holder: tempfile.TemporaryDirectory[str] | None = None
    if args.work_root is None:
        holder = tempfile.TemporaryDirectory(prefix="ar-fresh-user-")
        run_root = Path(holder.name)
    else:
        args.work_root.mkdir(parents=True, exist_ok=True)
        run_root = args.work_root
    try:
        report = run_fresh_user_acceptance(run_root)
        report["environment"] = environment_facts()
        destination = args.reports / REPORT_DIRECTORY / "run.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_json(destination, report)
        failed = [one for one in report["invariants"] if one["status"] != "passed"]
        failed += [one for one in report["checkpoints"] if one["status"] != "passed"]
        print(f"transcript: {destination.as_posix()}")
        print(
            f"steps: {len(report['steps'])} "
            f"invariants: {len(report['invariants'])} "
            f"blocked: {len(report['blocked'])} "
            f"failed: {len(failed)}"
        )
        for one in failed:
            print(f"  FAILED {one.get('id') or one.get('step')}: {json.dumps(one)[:400]}")
        for one in report["blocked"]:
            print(f"  BLOCKED {one['step']}: {one['reason']}")
        return 1 if failed else 0
    finally:
        if holder is not None:
            holder.cleanup()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
