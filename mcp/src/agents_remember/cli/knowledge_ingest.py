"""CLI adapter: ingest an orchestrator's curator hand-off list into a leaf's candidate.

    agents-remember knowledge-ingest --contract <leaf enclosure contract>
        --list <hand-off list> --candidate-directory <dir>
        --authorization-ref <ref> [--commit]
        [--publish-to <memory dataset path> [--expected-destination <identity JSON>]]

``--contract`` is REQUIRED and is the write guard, exactly as ``memory-citations`` and
``memory-backfill`` use it: the operation reads the code and memory repositories the contract
names and writes into the candidate directory the caller supplies, so there is no argument list
that can aim a knowledge write at another leaf's line.

``--authorization-ref`` is REQUIRED for the same reason the operation requires one: an admitted
write needs an authorship envelope, and ``Authorship`` refuses a blank reference. The same
reference is the actor the envelope names, so "who authorized this" and "who authored it" stay one
recorded fact instead of two strings that can disagree.

PLANNING IS THE DEFAULT, AND PLANNING IS ALSO THE DRY RUN. Without ``--commit`` this reads the
list, resolves every target, reports every outcome and writes nothing; the report's own
``dry_run`` field says which mode produced it. ``--commit`` is the developer's commit word and the
only mode that writes rows.

PUBLICATION IS THE SECOND HALF OF THE SAME ACT, AND IT HAS ONE ARGUMENT. ``--publish-to`` names the
dataset path the committed candidate is published to, through the shipped publication owner, by the
run that already holds the admitted candidate and reads the candidate's own live identity. Without
it this command commits and stops, exactly as it did before publication was reachable from here. A
run that did not commit publishes nothing, so ``--publish-to`` without ``--commit`` is a planning
run that reports no publication. ``--expected-destination`` is the exact identity the caller
observed at that path, as a JSON object of the identity's own fields
(``repository_id``/``schema_version``/``logical_digest``); omitting it means the destination is
expected to be ABSENT, which is the first publication into a worktree.

Exit status: 0 when every entry reached a terminal outcome the report names -- committed, a
ruling, or a typed refusal -- and 2 when the invocation itself is refused (a missing or
unreadable list, a blank authorization reference, a contract this command cannot load, a malformed
expected-destination identity). A refusal per entry is a *result*, not a tool failure: the report
is the product, and a caller branching on the exit code would otherwise lose it.

This is the production caller for :func:`agents_remember.application.knowledge_curator_ingest.
ingest_curator_list`. The mounted ``knowledge_change`` tool does NOT write and says so; the write
plane's reachable entry points are this subcommand and the operation it calls.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_curator_ingest import (
    EntryOutcome,
    IngestPublication,
    IngestReport,
    IngestSelection,
    ingest_curator_list,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity

EXIT_REPORTED = 0
EXIT_REFUSED = 2


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--contract",
        required=True,
        help="Path to the leaf enclosure contract. Required: the ingest reads the code and memory "
        "lines that contract names and refuses any other target.",
    )
    parser.add_argument(
        "--list",
        required=True,
        dest="hand_off_list",
        help="Path to the orchestrator's curator hand-off list (revision 1's JSON shape).",
    )
    parser.add_argument(
        "--candidate-directory",
        required=True,
        help="Directory holding the leaf's draft candidate. Named by the caller: a durable "
        "convention for it belongs in the memory layer's settings, not in this operation.",
    )
    parser.add_argument(
        "--authorization-ref",
        required=True,
        help="The authorization this run is admitted under. It is also the actor the authorship "
        "envelope names, so one required reference rather than two optional ones.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write the batch. Without it this reports and writes nothing, which is the dry run.",
    )
    parser.add_argument(
        "--publish-to",
        dest="publish_to",
        default=None,
        help="Dataset path the committed candidate is published to. Omit to commit without "
        "publishing; a run that did not commit publishes nothing.",
    )
    parser.add_argument(
        "--expected-destination",
        dest="expected_destination",
        default=None,
        help="The exact identity the caller observed at --publish-to, as a JSON object of the "
        "identity's own fields (repository_id, schema_version, logical_digest). Omitting it means "
        "the destination is expected to be absent.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the whole report as JSON instead of the human-readable summary.",
    )


def _expected_destination(text: str | None) -> SnapshotIdentity | None:
    """The destination identity the caller admitted, from its JSON object; ``None`` means absent.

    A malformed value is refused by name rather than defaulted to "absent": an unstated destination
    is not an expectation, and silently reading a typo as "publish over whatever is there" is the
    one interpretation this surface must never make.
    """

    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"--expected-destination is not JSON: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError(
            "--expected-destination must be a JSON object carrying the identity's fields "
            "(repository_id, schema_version, logical_digest)"
        )
    return SnapshotIdentity.model_validate(parsed)


def _publication(args: argparse.Namespace) -> IngestPublication | None:
    """The publication this invocation selects, or ``None`` when it selected no destination."""

    if args.publish_to is None:
        return None
    return IngestPublication(
        destination_path=Path(args.publish_to),
        expected_destination=_expected_destination(args.expected_destination),
    )


def run(args: argparse.Namespace) -> int:
    """Run one ingest and print its report; the report IS the result."""

    list_path = Path(args.hand_off_list)
    if not list_path.is_file():
        print(f"the hand-off list {list_path} is not a file this command can read")
        return EXIT_REFUSED
    if not str(args.authorization_ref).strip():
        print("--authorization-ref must not be blank: an admitted write needs an authorization")
        return EXIT_REFUSED
    try:
        report = ingest_curator_list(
            args.contract,
            list_path,
            IngestSelection(
                candidate_directory=args.candidate_directory,
                authorization_ref=args.authorization_ref,
                dry_run=not args.commit,
                publication=_publication(args),
            ),
        )
    except (ValueError, OSError) as error:
        print(f"the ingest was refused before it read the list: {error}")
        return EXIT_REFUSED
    if args.as_json:
        print(json.dumps(_payload(report), indent=2, sort_keys=True))
    else:
        print(_summary(report))
    return EXIT_REPORTED


def _summary(report: IngestReport) -> str:
    """The report as a reader scans it: the mode, the counts, and one line per outcome."""

    lines = [
        f"knowledge-ingest {'dry run' if report.dry_run else 'commit'} on {report.contract_path}",
        f"  candidate: {report.candidate_directory}",
        f"  lane: {report.lane}  repository: {report.repository_id}",
        f"  code tree: {report.code_tree_id} ({report.code_tree_source} at "
        f"{report.code_base_commit})",
        f"  memory tree: {report.memory_tree_id}",
        f"  batch: {report.batch_state}"
        + (f"  refusal: {report.batch_refusal.code}" if report.batch_refusal else ""),
        "  counts: " + ", ".join(f"{name}={value}" for name, value in _counts(report).items()),
    ]
    if report.publication is not None:
        publication = report.publication
        published_to = f" -> {publication.destination_ref}" if publication.destination_ref else ""
        refusal = (
            f"  refusal: {publication.refusal.code}" if publication.refusal is not None else ""
        )
        lines.append(f"  publication: {publication.state}{published_to}{refusal}")
    for outcome in report.committed:
        lines.append(f"  committed {outcome.entry_id}: {_targets(outcome)}")
    for outcome in report.rulings:
        lines.append(f"  ruling {outcome.entry_id}: {outcome.kind}/{outcome.disposition}")
    for outcome in report.refused:
        lines.append(f"  refused {outcome.entry_id}: {outcome.refusal}")
    return "\n".join(lines)


def _targets(outcome: EntryOutcome) -> str:
    return "; ".join(
        f"{target.completed_path} [{target.locator_kind}] {target.observation}"
        for target in outcome.targets
    )


def _payload(report: IngestReport) -> dict[str, Any]:
    """The whole report as one JSON object, so a caller branches on facts and not on prose.

    Every tuple becomes a list and every dataclass a mapping; nothing is summarised away, because
    the report is the operation's product and a caller that has to re-read the database to learn
    what happened has been given a message rather than a result.
    """

    return {
        "contractPath": report.contract_path,
        "candidateDirectory": report.candidate_directory,
        "candidateReceipt": report.candidate_receipt,
        "lane": report.lane,
        "repositoryId": report.repository_id,
        "codeTreeId": report.code_tree_id,
        "memoryTreeId": report.memory_tree_id,
        "codeBaseCommit": report.code_base_commit,
        "codeTreeSource": report.code_tree_source,
        "derivedIdentities": report.derived_identities,
        "dryRun": report.dry_run,
        "entriesRead": list(report.entries_read),
        "batchState": report.batch_state,
        "batchDigestBefore": report.batch_digest_before,
        "batchDigestAfter": report.batch_digest_after,
        "batchRefusal": (
            None if report.batch_refusal is None else report.batch_refusal.model_dump(mode="json")
        ),
        "publication": (
            None if report.publication is None else report.publication.model_dump(mode="json")
        ),
        "counts": _counts(report),
        "committed": [_outcome(one) for one in report.committed],
        "rulings": [_outcome(one) for one in report.rulings],
        "refused": [_outcome(one) for one in report.refused],
    }


def _counts(report: IngestReport) -> dict[str, int]:
    return {
        "entriesRead": report.counts.entries_read,
        "rulings": report.counts.rulings,
        "targetsCompleted": report.counts.targets_completed,
        "locatorsResolved": report.counts.locators_resolved,
        "anchorsObservedExact": report.counts.anchors_observed_exact,
        "routePaths": report.counts.route_paths,
        "routesAuthored": report.counts.routes_authored,
        "routesReused": report.counts.routes_reused,
        "commandsSent": report.counts.commands_sent,
        "recordsWritten": report.counts.records_written,
    }


def _outcome(outcome: EntryOutcome) -> dict[str, Any]:
    """One entry's whole outcome, including the state the operation itself assigned it."""

    return {
        "entryId": outcome.entry_id,
        "kind": outcome.kind,
        "disposition": outcome.disposition,
        "dispositionSource": outcome.disposition_source,
        "state": outcome.state,
        "refusal": outcome.refusal,
        "routes": [
            {
                "routePath": route.route_path,
                "routeId": route.route_id,
                "state": route.state,
                "refusal": route.refusal,
            }
            for route in outcome.routes
        ],
        "targets": [
            {
                "path": target.path,
                "step": target.step,
                "completedPath": target.completed_path,
                "locatorKind": target.locator_kind,
                "locator": target.locator,
                "observation": target.observation,
                "sourceIdentity": target.source_identity,
                "routePath": target.route_path,
                "routeState": target.route_state,
                "refusal": target.refusal,
            }
            for target in outcome.targets
        ],
    }
