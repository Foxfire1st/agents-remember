"""CLI adapter: list, or delete, the leftover copies of the retired knowledge database.

    agents-remember knowledge-copies --coordination-root ROOT [--apply --reviewed DRY-RUN-REPORT] [--report FILE] [--json]

MIK-R26 rule 6. Without ``--apply`` this is the dry run: every SQLite file under ``ROOT`` that holds
the knowledge schema is listed with the rule that decides it, and nothing is changed. With
``--apply`` the copies rule 6 names are deleted -- those in ``provider-runtime`` directories, in
worktrees that no longer exist, and under the notes of archived tasks -- together with the receipt,
origin and lock files the database route wrote beside them. The apply is bound to a reviewed dry
run: ``--reviewed`` names the ``--report`` file of that dry run, and when the scan now differs from
it (``reviewDigest``) nothing is deleted and the command refuses with exit status 2.

Git-tracked files and derived index caches are skipped by construction, and copies under the notes
of a task that is not archived are left for that task's archive hook (D17). ``--report`` writes the
same document the command prints with ``--json``: every copy, its rule, and the counts and sizes
before and after, by rule and by task.

Exit status: 0 when the run did what its reviewed dry run listed (a companion that dry run listed as
kept is kept, and is no failure), 1 when a deletion failed or a file changed after the review, 2
when ``ROOT`` is not a coordination root or the apply is refused.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agents_remember.application.legacy_dataset_copies import plan_cleanup, run_cleanup

EXIT_DONE = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--coordination-root",
        type=Path,
        required=True,
        help="The coordination root to search (the directory that holds tasks/ and worktrees/).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the copies MIK-R26 rule 6 names. Without it nothing is changed (dry run).",
    )
    parser.add_argument(
        "--reviewed",
        type=Path,
        help="With --apply: the --report file of the dry run you reviewed; the apply deletes "
        "exactly what that dry run listed, or refuses.",
    )
    parser.add_argument("--report", type=Path, help="Also write the JSON document to this file.")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Print the JSON.")


def _megabytes(count: int) -> str:
    return f"{count / 1_000_000:.1f} MB"


def _summary(document: dict[str, Any]) -> str:
    applied = document["mode"] == "apply"
    lines = [
        f"knowledge copies under {document['coordinationRoot']} "
        f"({'deleting' if applied else 'dry run: nothing is changed'})"
    ]
    for copy in document["copies"]:
        lines.append(
            f"  [{copy['rule']}] {copy['path']} ({copy['bytes']} bytes): {copy['ruleText']}"
        )
    before = document["before"]
    after = document["after"] if applied else document["afterApply"]
    lines.append(
        f"before: {before['files']} copies, {_megabytes(before['bytes'])}; "
        f"after {'this run' if applied else 'a run with --apply'}: {after['files']} copies, "
        f"{_megabytes(after['bytes'])}"
    )
    for rule, row in before["byRule"].items():
        lines.append(f"  {rule}: {row['files']} files, {_megabytes(row['bytes'])}")
    for owner, row in before["byOwner"].items():
        lines.append(f"  task or group {owner}: {row['files']} files, {_megabytes(row['bytes'])}")
    if not applied:
        lines.append(
            f"review digest (pass its report to --apply --reviewed): {document['reviewDigest']}"
        )
    if applied:
        lines.append(f"deleted: {len(document['deleted'])} copies")
        lines.extend(f"  also removed: {path}" for path in document["removedWithThem"])
        lines.extend(
            f"  kept, as reviewed: {entry['path']}: {entry['detail']}"
            for entry in document["keptCompanions"]
        )
    lines.extend(
        f"  FAILED {failure['path']}: {failure['detail']}" for failure in document["failures"]
    )
    return "\n".join(lines)


def _reviewed_digest(path: Path | None, coordination_root: Path) -> str:
    if path is None:
        raise ValueError(
            "--apply needs --reviewed with the --report file of a dry run you reviewed"
        )
    try:
        reviewed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot read the reviewed dry run {path}: {error}") from error
    if not isinstance(reviewed, dict) or reviewed.get("mode") != "dry-run":
        raise ValueError(f"{path} is not the report of a dry run")
    if reviewed.get("coordinationRoot") != coordination_root.resolve().as_posix():
        raise ValueError(f"{path} is a dry run of another coordination root")
    digest = reviewed.get("reviewDigest")
    if not isinstance(digest, str) or not digest:
        raise ValueError(f"{path} carries no reviewDigest")
    return digest


def run(args: argparse.Namespace) -> int:
    try:
        if args.apply:
            document = run_cleanup(
                args.coordination_root, _reviewed_digest(args.reviewed, args.coordination_root)
            )
        else:
            document = plan_cleanup(args.coordination_root)
    except ValueError as error:
        print(f"knowledge-copies refuses: {error}")
        return EXIT_REFUSED
    text = json.dumps(document, indent=2, sort_keys=True)
    if args.report is not None:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text if args.as_json else _summary(document))
    return EXIT_FAILED if document["failures"] else EXIT_DONE
