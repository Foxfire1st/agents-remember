"""CLI adapter: ingest an orchestrator's curator hand-off list into a leaf's candidate.

    agents-remember knowledge-ingest --contract <leaf enclosure contract>
        --list <hand-off list> [--candidate-directory <dir>]
        --authorization-ref <ref> [--commit] [--baseline <published dataset>]
        [--publish | --publish-to <memory dataset path> [--expected-destination <identity JSON>]]

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

THE ORDINARY ROUTE HAS ONE DESTINATION, AND IT IS THE ONE THE READ SIDE DECLARES. ``--publish``
selects that destination instead of a path: the repository's published knowledge dataset at
``<this enclosure's resolved memory root>/knowledge.sqlite``, resolved through
:func:`~agents_remember.application.published_intent.published_dataset_path` for the coordination
context the ordinary read route resolves, so the location a curator writes and the location a later
task's planner selects are one spelling owned once rather than two conventions that agree today.
What the run admits is at that destination is *derived* rather than typed, because the ordinary route
has no caller-typed identity to offer and needs none: when ``--baseline`` names that same location,
the bytes this run captured from it at the top of the run are the dataset standing there, and their
identity is what the publication may replace -- that is the explicit update. Every other case is
admitted as nothing being there, and the publication owner refuses by name if the location turns out
to hold something. ``--publish`` and ``--publish-to`` are mutually exclusive, ``--expected-destination``
belongs to the caller-named path alone, and NEITHER IS IMPLIED BY ``--commit``: the commit word stays
the knowledge-batch write and acquires no publication meaning.

THE RUN READS ITS PUBLICATION BACK. When the ordinary route published, the location is read again
through :func:`~agents_remember.application.published_intent.resolve_published_intent` -- the owner
the ordinary read route itself uses, in the same scope the write was made in -- and the report carries
the dataset a reader will select: ``confirmed`` with the exact identity, ``mismatch`` naming both, or
``unavailable`` with the shipped refusal code for what was found instead. A publication the owner
refused is read back not at all: nothing was established about the destination, and the report says
that rather than inventing an identity for it.

CONTINUITY IS THE SAME DECISION'S OTHER HALF, AND IT ALSO HAS ONE ARGUMENT. ``--baseline`` names the
published dataset this task forks FROM. It is the pairing :class:`IngestSelection` documents: a
candidate the caller names without the baseline it starts from is an empty candidate holding only
this task's new entry, so the repository's existing invariants are absent from it and the next task
starts blind to knowledge the repository already recorded.

Without the argument nothing about the candidate changed -- the first task of a repository has no
prior dataset to select and still creates an empty candidate -- but the review's before half is now
established rather than left absent. A repository whose *first* knowledge is what this run writes
has a truthful before side, and it is an empty **first generation** that says so: the shipped
initialization owner creates a schema-valid empty dataset in the candidate's own namespace, and a
record beside it names the generation, the code base the run observed, and that pre-feature history
is not recorded. That is what makes the first invariant an addition instead of a comparison that
cannot open. A *selected* baseline that is missing or corrupt is a different fact and is never
answered this way: the run refuses it by name and establishes nothing, and the bytes it captured are
read as a dataset before they are written, so an unreadable fork point is never placed over the half
either.

The baseline is **read at the top of the run**, before the ingest publishes, and the review's
baseline half is made from those bytes rather than from a later read of the same path. That ordering
is deliberate and load-bearing: ``--baseline`` and ``--publish-to`` may name one path, and
publication replaces that file in place, so a copy taken afterwards would place the published
candidate in the before half and the review would compare a dataset against itself.

The half is then filled **once**. The first admitted baseline is the comparison's original one, so a
later successful run over the same shared path keeps the before side it was first handed instead of
restating it: the second run's captured bytes are the first run's *publication*, and placing them is
exactly how the addition the review exists to show comes to be present on both sides with an empty
delta. A run whose admitted baseline differs from the standing one therefore places nothing and says
so, naming the generation the half holds and the action that begins a new one. That action is
``--rebase-baseline``, and it is explicit because a deliberately new baseline is a **new comparison
generation with recorded lineage**, never an overwrite behind the identity the comparison already
had. An exact retry, a refused retry and a planning run all leave the half exactly as they found it.

Exit status: 0 when every entry reached a terminal outcome the report names -- committed, a
ruling, or a typed refusal -- and 2 when the invocation itself is refused (a missing or
unreadable list, a blank authorization reference, a contract this command cannot load, a malformed
expected-destination identity, two contradictory destination selections, a declared location that
cannot be resolved). A refusal per entry is a *result*, not a tool failure: the report
is the product, and a caller branching on the exit code would otherwise lose it.

EXIT ZERO IS NOT A PUBLICATION CLAIM, and the report is where that claim lives. A run whose entries
all committed can still have published nothing -- because it named no destination, because a
publication was refused, or because the read-back found something other than what was written -- and
each of those is stated as its own fact (``publicationRoute``, the ``publication`` result, and
``publishedIdentity``). Reading the status as proof that every entry committed, or that the
repository now holds them, is exactly the mistake these three fields exist to make impossible.

This is the production caller for :func:`agents_remember.application.knowledge_curator_ingest.
ingest_curator_list`. The mounted ``knowledge_change`` tool does NOT write and says so; the write
plane has ONE writer, and this subcommand is one of the TWO shipped entry points that reach it — the
other is ``agents-remember knowledge-bootstrap``, which the taskless repository foundation uses.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_baseline_generation import (
    BaselineRun,
    CapturedBaseline,
    fill_admitted_before_half,
)
from agents_remember.application.knowledge_curator_ingest import (
    IngestPublication,
    IngestReport,
    IngestSelection,
    ingest_curator_list,
)
from agents_remember.application.knowledge_publication_route import (
    DeclaredPublicationLocation,
    PublishedIdentityReadBack,
    admitted_destination,
    declared_publication_location,
    published_identity_read_back,
)
from agents_remember.application.knowledge_review import (
    REVIEW_BASELINE_DIRECTORY,
    REVIEW_CANDIDATE_DIRECTORY,
    REVIEW_CANDIDATE_RELATIVE_ROOT,
)
from agents_remember.cli.knowledge_ingest_report import payload, summary
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

EXIT_REPORTED = 0
EXIT_REFUSED = 2

# The two batch states that mean the candidate was actually committed. ``changed`` is a batch that
# wrote rows; ``no_change`` is a batch whose rows were already stored, which committed and changed
# nothing -- both have a candidate on disk, and a batch that refused, was never attempted or ran in
# planning mode has none.
#
# ``no_change`` is not sufficient on its own, because a batch whose every entry REFUSED also reports
# it: the batch-level state falls back to ``no_change`` when the batch never ran, so an all-refused
# run is indistinguishable from an idempotent one by state alone. The committed-entry list is what
# separates them, and it is the honest test for this caller: a run that committed no entry has no
# candidate of its own, so it must not touch the review's before half. Without that check, a refused
# re-run re-placed the baseline from the bytes it captured at the top of the run -- which, once the
# leaf had published over its own fork point, are the PUBLISHED dataset -- and the review then showed
# the addition present on both sides with an empty delta. That is the failure the capture exists to
# prevent, reaching it through the refusal path instead of the publication path.
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
        "task: with no baseline and no existing candidate the run creates an empty candidate and "
        "establishes the review's before half as an explicitly identified empty first generation, "
        "which is the cold start rather than the continuity path. A named baseline that is missing "
        "or unreadable is refused by name, and one that is unreadable is never placed in the before "
        "half: a half that already records its first generation keeps it. The half is filled ONCE: "
        "the dataset this comparison was opened on stays its before side across later successful "
        "runs, exact retries and refused retries, so a run whose baseline differs from the one "
        "standing there places nothing and names --rebase-baseline.",
    )
    parser.add_argument(
        "--rebase-baseline",
        dest="rebase_baseline",
        action="store_true",
        help="Begin an explicitly NEW comparison generation from --baseline, recording the "
        "generation it replaces and that generation's dataset identity as its lineage. Without it a "
        "baseline that differs from the one this comparison opened on is never placed: the original "
        "is kept and the report names the rebase action. It does not replace an identified first "
        "generation and does not repair a damaged half.",
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
        "publishing; a run that did not commit publishes nothing. Naming a path and --publish "
        "together is refused: the ordinary route has one destination and a caller-named path is a "
        "different selection.",
    )
    parser.add_argument(
        "--publish",
        dest="publish_declared",
        action="store_true",
        help="Publish the committed candidate to the repository's ONE declared published dataset "
        "location -- <this enclosure's resolved memory root>/knowledge.sqlite, the same location "
        "the ordinary read route declares -- and read the published identity back through that "
        "route's owner. What is admitted at the destination is derived from --baseline: when the "
        "baseline IS that location, the identity this run captured from it is the dataset being "
        "replaced (the explicit update); otherwise the destination is expected to hold nothing. "
        "Mutually exclusive with --publish-to and with --expected-destination, and never implied "
        "by --commit.",
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


@dataclass(frozen=True)
class _Destination:
    """The destination this invocation selected, and the report's own line about that selection.

    ``location`` is present only for the ordinary route, because the read-back is a read of *that*
    declared location through the read route's own owner: a caller-named path is not the repository's
    publication, so there is no declared location to read and no reader route to bind it to.
    """

    publication: IngestPublication | None
    route: str
    location: DeclaredPublicationLocation | None = None


def _destination_conflict(args: argparse.Namespace) -> str | None:
    """Why this invocation's destination argument set is not one coherent selection, or ``None``.

    Stated before anything is read, because two destinations are not a narrower request: the
    ordinary route's declared location and a caller-named path are different selections, and a run
    that named both has not said which one it means. The same rule covers an argument that names
    something about a destination the run never selected:

    * ``--expected-destination`` belongs to the caller-named path alone -- the ordinary route derives
      the identity it may replace from its own admitted baseline -- so a caller-typed identity beside
      ``--publish`` would be a second, unchecked claim about the one fact the derivation establishes;
    * ``--expected-destination`` with NO destination selector at all admits what is at a place the run
      is not publishing to. It is refused by name rather than silently absorbed, exactly as
      ``--rebase-baseline`` without ``--baseline`` is: an ignored argument is one the caller believes
      was honoured, and the malformed value beside it is already refused by name.
    """

    if args.publish_declared and args.publish_to is not None:
        return (
            "--publish and --publish-to name two different destinations, so one run cannot mean "
            "both"
        )
    if args.publish_declared and args.expected_destination is not None:
        return (
            "--publish derives the destination's admitted identity from --baseline, so "
            "--expected-destination (which belongs to --publish-to) is refused beside it"
        )
    if (
        args.expected_destination is not None
        and args.publish_to is None
        and not args.publish_declared
    ):
        return (
            "--expected-destination admits what is at a destination, so it needs --publish-to or "
            "--publish: a run that names neither commits and publishes nothing"
        )
    return None


def _caller_named_destination(args: argparse.Namespace) -> _Destination:
    """The destination the caller typed, admitted exactly as it was before the ordinary route.

    Nothing about this selection narrows: the path is the caller's, the expectation is the caller's
    ``--expected-destination`` and its absence still means "the destination is expected to be
    absent". It is reported as caller-named so that a reader can tell this run from one that used
    the repository's own declared location.
    """

    destination = Path(args.publish_to)
    return _Destination(
        publication=IngestPublication(
            destination_path=destination,
            expected_destination=_expected_destination(args.expected_destination),
        ),
        route=f"caller-named: {destination}",
    )


def _declared_destination(
    contract: WorktreeContract, captured: CapturedBaseline | str | None
) -> _Destination:
    """The repository's declared published dataset location, and what this run admits is there.

    The location comes from the read side's owner and the admission from the run's own captured
    baseline; nothing here decides a path or an identity of its own. A location that cannot be
    resolved raises, which :func:`run` turns into the invocation refusal it is: an unowned
    destination is the one thing this route must never acquire by falling back to a guess.
    """

    location = declared_publication_location(contract)
    admission = admitted_destination(location.path, captured)
    return _Destination(
        publication=IngestPublication(
            destination_path=location.path,
            expected_destination=admission.identity,
        ),
        route=f"declared-location: {location.path} ({admission.detail})",
        location=location,
    )


def _selected_destination(
    args: argparse.Namespace, contract: WorktreeContract, captured: CapturedBaseline | str | None
) -> _Destination:
    """This invocation's one destination selection: named by the caller, declared, or none."""

    if args.publish_to is not None:
        return _caller_named_destination(args)
    if args.publish_declared:
        return _declared_destination(contract, captured)
    return _Destination(
        publication=None,
        route=_nothing_selected(args),
    )


def _nothing_selected(args: argparse.Namespace) -> str:
    """What a run that named no destination reports, in the mode it actually ran in.

    The mode is read from the invocation rather than assumed, because "so the batch was committed
    without publishing" is false in a planning run: the line exists to stop a zero exit being read
    as a publication, and a line that claimed a commit this run did not make would be its own small
    fabrication.
    """

    if args.commit:
        return (
            "not-selected: this run named no destination, so the batch was committed without "
            "publishing and the repository's published dataset was not touched"
        )
    return "not-selected: this run named no destination and committed nothing, so nothing was published"


def _read_back(destination: _Destination, report: IngestReport) -> PublishedIdentityReadBack | None:
    """Read the published location back, or report that this run published nothing to read back.

    *Whether* the read-back may run is read from the run's own report, exactly as the before-half
    placement is: a publication the owner refused established nothing about the destination, and a
    run that published nothing has no location of its own to read. *What* the read-back is belongs to
    :mod:`agents_remember.application.knowledge_publication_route`, which reads the declared location
    through the owner the ordinary read route itself uses.
    """

    publication = report.publication
    if destination.location is None or publication is None or publication.identity is None:
        return None
    return published_identity_read_back(destination.location, publication.identity)


def _review_root(contract: WorktreeContract) -> Path:
    """The leaf's canonical review knowledge root, derived from the contract.

    Derived from the contract's own recorded worktree group rather than from the caller and rather
    than from the process's working directory, so the directory the Intent Reviewer resolves and the
    directory this run writes are the same path by construction rather than by two spellings
    agreeing.
    """

    return contract.worktree_group / REVIEW_CANDIDATE_RELATIVE_ROOT


def _candidate_directory(args: argparse.Namespace, review_root: Path) -> Path:
    """The candidate directory this run writes into: the caller's, or the leaf's canonical one.

    Naming ``--candidate-directory`` still wins: a caller that wants a scratch draft gets one, and
    that draft is then simply not the leaf's review candidate.
    """

    if args.candidate_directory is not None:
        return Path(args.candidate_directory)
    return review_root / REVIEW_CANDIDATE_DIRECTORY


def _capture_baseline(args: argparse.Namespace) -> CapturedBaseline | str | None:
    """Read the admitted baseline before anything can publish over it, or say why it was not read.

    The read happens here, at the top of :func:`run`, and the bytes are carried rather than the path:
    publication replaces the dataset this path names, so a later read of the same path is a read of
    the after state. Absence and unreadability are returned as reasons rather than raised, because
    both are facts about the invocation that belong in the report beside the outcome that used the
    baseline, exactly as the rest of this command reports them. The value itself is the application
    owner's, because "the bytes the run was handed, read before it could move them" is a fact about
    the comparison and not about this argument list.
    """

    if args.baseline is None:
        return None
    source = Path(args.baseline)
    if not source.is_file():
        return f"not-captured: the named baseline dataset {source} is not a file"
    try:
        return CapturedBaseline(origin=source, payload=source.read_bytes())
    except OSError as error:
        return f"not-captured: the named baseline dataset {source} could not be read ({error})"


def _placement_refusal(report: IngestReport) -> str | None:
    """Why this run may put nothing in the review's before half, or ``None`` when it may.

    Each condition states only what it established, and the two that concern the batch are separate
    on purpose. The state alone cannot carry the second: a batch whose every entry REFUSED also
    reports ``no_change``, because the batch-level state falls back to it when the batch never ran,
    so the committed-entry list is what separates an all-refused run from an idempotent one. One
    sentence serving both conditions read "committed no entry (replayed, committed 1)", contradicting
    itself in a single line about the one thing this gate exists to make trustworthy.

    Both ways of filling the before half are gated here rather than in each of them, because the
    question is the same one: the half belongs to the run that committed, and a planning run, a
    refused batch and a batch that committed nothing all leave it exactly as they found it.
    """

    if report.dry_run:
        return "not-placed: planning run (the before half is filled by the run that commits)"
    if report.batch_state not in COMMITTED_BATCH_STATES:
        return f"not-placed: the batch did not commit ({report.batch_state})"
    if not report.committed:
        return (
            "not-placed: the batch committed no entry "
            f"({report.batch_state}, refused {len(report.refused)})"
        )
    return None


def _place_review_baseline(
    args: argparse.Namespace,
    contract: WorktreeContract,
    report: IngestReport,
    captured: CapturedBaseline | str | None,
) -> str | None:
    """Fill the review's before half through its owner, or say why this run filled nothing.

    Two questions are answered in two places on purpose, and the split is the same one the rest of
    this command follows. *Whether* this run may fill anything at all is read from the run's own
    report, because a planning run and a batch that committed nothing are facts only the report
    holds. *What the half then is* -- its generation, its lineage, and whether a baseline the run was
    handed may replace the one standing there -- belongs to
    :mod:`agents_remember.application.knowledge_baseline_generation`, which composes the before-half
    layout owner and the first-generation owner. This function carries the run's facts across that
    seam and nothing else, so the CLI stays a surface rather than a second placement authority.

    The half is derived from the contract's own recorded worktree group, exactly as the review
    resolves it, so the directory this run fills and the directory the comparison opens are one path
    by construction rather than by two spellings agreeing.
    """

    refusal = _placement_refusal(report)
    if refusal is not None:
        return refusal
    if isinstance(captured, str):
        return captured
    return fill_admitted_before_half(
        half=_review_root(contract) / REVIEW_BASELINE_DIRECTORY,
        candidate_directory=Path(report.candidate_directory),
        captured=captured,
        run=BaselineRun(
            leaf_id=contract.leaf_id,
            contract_path=str(args.contract),
            authorization_ref=args.authorization_ref,
            code_base_commit=report.code_base_commit or None,
        ),
        rebase=args.rebase_baseline,
    )


@dataclass(frozen=True)
class _Invocation:
    """Everything one run resolves before it hands the list to the operation.

    The four values are one fact each and are resolved here in the order the run needs them: the
    contract names the enclosure, the review root comes from that contract, and the baseline is read
    BEFORE the destination is selected, because the ordinary route's admission IS the identity of
    those captured bytes.
    """

    contract: WorktreeContract
    candidate_directory: Path
    captured_baseline: CapturedBaseline | str | None
    destination: _Destination


def _invocation_refusal(args: argparse.Namespace) -> str | None:
    """Why this invocation is refused before anything is read, or ``None`` when it may proceed.

    Every one of these is a fact about the argument list, so they are answered before a contract, a
    list or a byte is touched. They are grouped here rather than spread through :func:`run` for the
    reason the rest of this command separates its steps: a reader asking "what does this refuse
    outright" reads one function, and ``run`` stays the sequence of what a run does.
    """

    list_path = Path(args.hand_off_list)
    if not list_path.is_file():
        return f"the hand-off list {list_path} is not a file this command can read"
    if not str(args.authorization_ref).strip():
        return "--authorization-ref must not be blank: an admitted write needs an authorization"
    conflict = _destination_conflict(args)
    if conflict is not None:
        return conflict
    if args.rebase_baseline and args.baseline is None:
        # A rebase is a transition *from* one admitted baseline to another, so a run that names the
        # action without the dataset has stated no generation to begin from. Reading it as the cold
        # start would silently do something other than what the caller asked for.
        return (
            "--rebase-baseline begins a new comparison generation from --baseline, so it needs the "
            "dataset it rebases onto"
        )
    return None


def _invocation(args: argparse.Namespace) -> _Invocation:
    """Resolve the enclosure, the candidate and the destination this run is admitted under."""

    contract = load_contract(Path(args.contract))
    captured_baseline = _capture_baseline(args)
    return _Invocation(
        contract=contract,
        candidate_directory=_candidate_directory(args, _review_root(contract)),
        captured_baseline=captured_baseline,
        destination=_selected_destination(args, contract, captured_baseline),
    )


def _publication_route(destination: _Destination, report: IngestReport) -> str:
    """The run's own line about its publication: the destination selected, and what became of it.

    The selection happens before the list is read -- the destination is part of what the operation is
    handed -- but whether anything was published is a fact only the report holds. A planning run and a
    batch that committed no entry both leave ``report.publication`` absent while a destination WAS
    selected, so a line that stopped at the selection would read as a publication claim for both. The
    line is completed here, where the report exists, by the same discipline the review-baseline line
    follows: the run's own report says what the run did, and no field overstates it.
    """

    if destination.publication is None or report.publication is not None:
        return destination.route
    return f"{destination.route}; nothing was published by this run: {_nothing_published(report)}"


def _nothing_published(report: IngestReport) -> str:
    """Why a run that selected a destination published nothing, read from its own report."""

    if report.dry_run:
        return "it was a planning run, and publication is the committed batch's second half"
    return f"the batch committed no entry ({report.batch_state}, refused {len(report.refused)})"


def _print_report(
    args: argparse.Namespace,
    report: IngestReport,
    review_baseline: str | None,
    destination: _Destination,
    published_identity: PublishedIdentityReadBack | None,
) -> None:
    """Print the report in the form the caller asked for; the report IS the result."""

    publication_route = _publication_route(destination, report)
    if args.as_json:
        print(
            json.dumps(
                payload(
                    report,
                    review_baseline,
                    publication_route=publication_route,
                    published_identity=published_identity,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(
        summary(
            report,
            review_baseline,
            publication_route=publication_route,
            published_identity=published_identity,
        )
    )


def run(args: argparse.Namespace) -> int:
    """Run one ingest and print its report; the report IS the result."""

    refusal = _invocation_refusal(args)
    if refusal is not None:
        print(refusal)
        return EXIT_REFUSED
    try:
        invocation = _invocation(args)
    except (ValueError, OSError) as error:
        print(f"the ingest was refused before it read the list: {error}")
        return EXIT_REFUSED
    try:
        report = ingest_curator_list(
            args.contract,
            Path(args.hand_off_list),
            IngestSelection(
                candidate_directory=invocation.candidate_directory,
                authorization_ref=args.authorization_ref,
                dry_run=not args.commit,
                baseline=None if args.baseline is None else Path(args.baseline),
                publication=invocation.destination.publication,
            ),
        )
    except (ValueError, OSError) as error:
        print(f"the ingest was refused before it read the list: {error}")
        return EXIT_REFUSED
    review_baseline = _place_review_baseline(
        args, invocation.contract, report, invocation.captured_baseline
    )
    published_identity = _read_back(invocation.destination, report)
    _print_report(args, report, review_baseline, invocation.destination, published_identity)
    return EXIT_REPORTED
