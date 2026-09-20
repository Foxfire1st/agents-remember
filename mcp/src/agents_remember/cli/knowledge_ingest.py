"""CLI adapter: ingest an orchestrator's curator hand-off list into a leaf's candidate.

    agents-remember knowledge-ingest --contract <leaf enclosure contract>
        --list <hand-off list> [--candidate-directory <dir>]
        --authorization-ref <ref> [--commit] [--baseline <published dataset>]
        [--publish-to <memory dataset path> [--expected-destination <identity JSON>]]

``--contract`` is REQUIRED and is the write guard, exactly as ``memory-citations`` and
``memory-backfill`` use it: the operation reads the code and memory repositories the contract
names and writes into the candidate directory the caller supplies, so there is no argument list
that can aim a knowledge write at another leaf's line.

``--candidate-directory`` is OPTIONAL and defaults to the leaf's canonical review candidate root,
``<worktree-group>/provider-runtime/dev-ar-coordination/knowledge/candidate``, derived from the
contract's own recorded worktree group. That default is what connects the write side to the read
side: the Intent Reviewer resolves its candidate from the same root through the same published
names, so an ingest that names no directory authors the candidate the review then opens. Naming a
directory still writes there -- a scratch draft stays possible -- and that draft is then not the
leaf's review candidate, which the report echoes either way.

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

CONTINUITY IS THE SAME DECISION'S OTHER HALF, AND IT ALSO HAS ONE ARGUMENT. ``--baseline`` names the
published dataset this task forks FROM. It is the pairing :class:`IngestSelection` documents: a
candidate the caller names without the baseline it starts from is an empty candidate holding only
this task's new entry, so the repository's existing invariants are absent from it and the next task
starts blind to knowledge the repository already recorded. Without the argument nothing changed --
the first task of a repository has no prior dataset to select and still creates an empty candidate.

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
import shutil
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_curator_ingest import (
    EntryOutcome,
    IngestPublication,
    IngestReport,
    IngestSelection,
    ingest_curator_list,
)
from agents_remember.application.knowledge_review import (
    REVIEW_BASELINE_DIRECTORY,
    REVIEW_CANDIDATE_DIRECTORY,
    REVIEW_CANDIDATE_RELATIVE_ROOT,
)
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.snapshot import CANDIDATE_DATABASE_NAME
from agents_remember.worktrees.worktree_contract import load_contract

EXIT_REPORTED = 0
EXIT_REFUSED = 2

# The two batch states that mean the candidate was actually committed. ``changed`` is a batch that
# wrote rows; ``no_change`` is a batch whose rows were already stored, which committed and changed
# nothing -- both have a candidate on disk, and a batch that refused, was never attempted or ran in
# planning mode has none.
COMMITTED_BATCH_STATES = frozenset({"changed", "no_change"})


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
        default=None,
        help="Directory holding the leaf's draft candidate. Omit to use the leaf's canonical "
        "review candidate root -- <worktree-group>/provider-runtime/dev-ar-coordination/knowledge/"
        "candidate -- which is the directory the Intent Reviewer resolves for this leaf. Naming "
        "another directory ingests into it without placing it where the review looks.",
    )
    parser.add_argument(
        "--authorization-ref",
        required=True,
        help="The authorization this run is admitted under. It is also the actor the authorship "
        "envelope names, so one required reference rather than two optional ones.",
    )
    parser.add_argument(
        "--baseline",
        dest="baseline",
        default=None,
        help="Path to the published dataset this task forks FROM. Omit for a repository's first "
        "task: with no baseline and no existing candidate the run creates an empty candidate, "
        "which is the cold start rather than the continuity path.",
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


def _review_root(args: argparse.Namespace) -> Path:
    """The leaf's canonical review knowledge root, derived from the contract.

    Derived from the contract's own recorded worktree group rather than from the caller and rather
    than from the process's working directory, so the directory the Intent Reviewer resolves and the
    directory this run writes are the same path by construction rather than by two spellings
    agreeing.
    """

    return load_contract(Path(args.contract)).worktree_group / REVIEW_CANDIDATE_RELATIVE_ROOT


def _candidate_directory(args: argparse.Namespace, review_root: Path) -> Path:
    """The candidate directory this run writes into: the caller's, or the leaf's canonical one.

    Naming ``--candidate-directory`` still wins: a caller that wants a scratch draft gets one, and
    that draft is then simply not the leaf's review candidate.
    """

    if args.candidate_directory is not None:
        return Path(args.candidate_directory)
    return review_root / REVIEW_CANDIDATE_DIRECTORY


def _place_review_baseline(
    args: argparse.Namespace, review_root: Path, report: IngestReport
) -> str | None:
    """Put the dataset this task forks from into the review's baseline half, or say why not.

    ``--baseline`` is the one production input that names the fork-point dataset, and the ruling this
    function implements is that the run which authors the candidate is the run that places the
    baseline: same run, same explicit caller input, no new owner and no recorded contract. The bytes
    are **copied**, not moved or linked, because the review's own recorded decision is that both
    halves sit inside the leaf's disposable local root "so a review reads no candidate out of the
    live coordination tree" -- the published dataset keeps serving its own lane.

    Nothing is invented on the ways out: a planning run and a batch that did not commit place
    nothing, a caller that named no baseline leaves the half absent (where ``candidate_dataset_absent``
    is then the truthful answer), and a destination already holding an identical dataset is left
    alone rather than rewritten.
    """

    if args.baseline is None:
        return None
    if report.dry_run:
        return "not-placed: planning run (the baseline is placed by the run that commits)"
    if report.batch_state not in COMMITTED_BATCH_STATES:
        return f"not-placed: the batch did not commit ({report.batch_state})"
    source = Path(args.baseline)
    if not source.is_file():
        return f"not-placed: the named baseline dataset {source} is not a file"
    destination = review_root / REVIEW_BASELINE_DIRECTORY / CANDIDATE_DATABASE_NAME
    if destination.is_file() and destination.read_bytes() == source.read_bytes():
        return f"present: {destination}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return f"placed: {destination}"


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
        review_root = _review_root(args)
        candidate_directory = _candidate_directory(args, review_root)
    except (ValueError, OSError) as error:
        print(f"the ingest was refused before it read the list: {error}")
        return EXIT_REFUSED
    try:
        report = ingest_curator_list(
            args.contract,
            list_path,
            IngestSelection(
                candidate_directory=candidate_directory,
                authorization_ref=args.authorization_ref,
                dry_run=not args.commit,
                baseline=None if args.baseline is None else Path(args.baseline),
                publication=_publication(args),
            ),
        )
    except (ValueError, OSError) as error:
        print(f"the ingest was refused before it read the list: {error}")
        return EXIT_REFUSED
    review_baseline = _place_review_baseline(args, review_root, report)
    if args.as_json:
        print(json.dumps(_payload(report, review_baseline), indent=2, sort_keys=True))
    else:
        print(_summary(report, review_baseline))
    return EXIT_REPORTED


def _summary(report: IngestReport, review_baseline: str | None) -> str:
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
    if review_baseline is not None:
        lines.append(f"  review baseline: {review_baseline}")
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


def _payload(report: IngestReport, review_baseline: str | None) -> dict[str, Any]:
    """The whole report as one JSON object, so a caller branches on facts and not on prose.

    Every tuple becomes a list and every dataclass a mapping; nothing is summarised away, because
    the report is the operation's product and a caller that has to re-read the database to learn
    what happened has been given a message rather than a result. ``reviewBaseline`` is the report's
    own line about the review handoff: what this run placed in the baseline half, or why it placed
    nothing. It is a string and not a boolean because "not placed" has several reasons and a caller
    that has to guess which one is being given a message rather than a result.
    """

    return {
        "contractPath": report.contract_path,
        "candidateDirectory": report.candidate_directory,
        "reviewBaseline": review_baseline,
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
