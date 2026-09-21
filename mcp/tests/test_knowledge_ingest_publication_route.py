"""The ordinary authoring and publication route, driven through the real command line (ICR-R20@v1).

The ingest exists, it writes, and its publication owner is real. What these cases measure is the
*route*: whether the curator's ordinary run reaches that owner at the repository's one published
location, what the run admits is standing there, and whether a reader then selects the dataset the
write reported. Every case below drives ``agents-remember knowledge-ingest`` through the umbrella
``main`` -- the surface the canonical curator instructions carry -- over a production-shaped
enclosure: a real code line with its own work branch, a real external memory repository, a real Git
memory **worktree** cut from it, and a contract recording all of them under a real coordination
root. The declared location is then resolved by the read route's own owner rather than restated
here, so the fixture cannot quietly agree with the implementation about a path neither of them
should be computing.

Six user operations, and one refusal family that guards them:

* **the ordinary first publication** -- the location the ordinary read route declares receives the
  committed candidate, and the route reads that location back through the owner a later task's
  planner uses: the reader sees what the writer wrote, at one exact identity;
* **a custom candidate** -- a scratch ``--candidate-directory`` must be the candidate that is
  reviewed AND published, not a conventional path guessed later, so the leaf's canonical review
  candidate directory is never created on that path;
* **a partial refusal** -- one list with a committed entry and a refused one names each, publishes
  exactly what committed, and manufactures no full completion;
* **an explicit update** -- a second run that forks from the published dataset and republishes onto
  it replaces exactly the identity it admitted, and the earlier truth survives inside the successor
  (the continuity a next task inherits);
* **an exact retry** -- the same list again changes nothing, publishes ``no_change``, and still reads
  its identity back confirmed;
* **no destination named** -- a run that commits and publishes nothing SAYS so, because exit zero is
  not a publication claim;
* **a refused publication, two contradictory selections and an unresolvable location** -- the
  destination is left untouched and nothing is read back as a success; naming two destinations, a
  caller-typed identity beside the declared route, or a repository with no memory layer to resolve a
  declared location from, are each refused by name before anything is read.

The mounted ``knowledge_read`` tool answers the last question in each case: the published dataset is
read through the same view surface a reviewer uses, at the snapshot the publication reported.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_review import (
    REVIEW_CANDIDATE_DIRECTORY,
    REVIEW_CANDIDATE_RELATIVE_ROOT,
)
from agents_remember.application.published_intent import (
    PublishedIntentSelection,
    published_dataset_path,
    resolve_published_intent,
)
from agents_remember.cli.__main__ import main
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.worktrees.modules.context import contract_context
from agents_remember.worktrees.worktree_contract import load_contract
from test_knowledge_curator_ingest_list import (
    _CODE_TEXT,
    AUTHORIZATION,
    CODE_FILE,
    CODE_OTHER_SYMBOL,
    CODE_SYMBOL,
    GONE_PATH,
    MEMORY_CARD,
    _cli_json,
    _commit,
    _cycle01_candidate_revisions,
    _cycle01_hand_off,
    _cycle01_revisions_of,
    _git,
    _one_entry_list,
    _write_files,
    entry,
    symbol,
    target,
)

pytestmark = pytest.mark.evidence_unit

REPO_NAME = "agents-remember"
LEAF_ID = "260921-ICR-L20-ROUTE"
WORK_BRANCH = "ar/260921-icr-l20-route"
WORKTREE_GROUP = "260921-icr-l20-ar"
MEMORY_WORKTREE_NAME = "memory-260921-icr-l20"
LEAF_FILE = "pkg/added_by_the_leaf.py"


@dataclass(frozen=True)
class OrdinaryEnclosure:
    """One production-shaped leaf enclosure, with the four roots the contract really records.

    ``memory_repo`` is the canonical **external** memory repository and ``memory_worktree`` is a real
    Git worktree of it on this leaf's work branch. That pair is the whole point of the fixture: the
    coordination context resolves the first as the memory layer and the contract's own worktree as
    the effective memory root, so the declared location this route publishes to is the memory line
    the task can actually commit, not a scratch directory that merely looks like one.
    """

    root: Path
    coordination_root: Path
    code_root: Path
    memory_repo: Path
    memory_worktree: Path
    contract_path: Path
    code_commit: str
    memory_commit: str


def _ordinary_enclosure(root: Path) -> OrdinaryEnclosure:
    """Build the enclosure a leaf really runs in, then the contract that records it."""

    coordination_root = root / "coordination"
    code_root = root / "code"
    memory_repo = coordination_root / "memory-repos" / f"ar-{REPO_NAME}"
    worktree_group = coordination_root / "worktrees" / REPO_NAME / WORKTREE_GROUP
    memory_worktree = worktree_group / MEMORY_WORKTREE_NAME
    task_root = coordination_root / "tasks" / REPO_NAME / "260921_icr_l20"
    task_root.mkdir(parents=True, exist_ok=True)
    _write_files(code_root, {CODE_FILE: _CODE_TEXT})
    _write_files(
        memory_repo,
        {
            f"onboarding/{MEMORY_CARD}": "# pkg module\n\nThe card for the module.\n",
            "onboarding/overview.md": "# onboarding overview\n",
        },
    )
    _git(code_root, ["init", "-q", "--initial-branch=main"])
    _git(memory_repo, ["init", "-q", "--initial-branch=main"])
    code_commit = _commit(code_root, "the recorded base")
    memory_commit = _commit(memory_repo, "the memory tree")
    # The leaf's own line: one more commit on its work branch, because a leaf always stands on work
    # the base commit does not hold and the ingest resolves its citations against that line.
    _git(code_root, ["checkout", "-q", "-b", WORK_BRANCH])
    _write_files(code_root, {LEAF_FILE: "# added by the leaf\n"})
    _commit(code_root, "the leaf's own work")
    worktree_group.mkdir(parents=True, exist_ok=True)
    _git(memory_repo, ["worktree", "add", "-q", "-b", WORK_BRANCH, str(memory_worktree)])
    enclosure = OrdinaryEnclosure(
        root=root,
        coordination_root=coordination_root,
        code_root=code_root,
        memory_repo=memory_repo,
        memory_worktree=memory_worktree,
        contract_path=root / "series-contract.md",
        code_commit=code_commit,
        memory_commit=memory_commit,
    )
    enclosure.contract_path.write_text(_contract_text(enclosure), encoding="utf-8")
    return enclosure


def _contract_text(enclosure: OrdinaryEnclosure) -> str:
    """One leaf contract carrying exactly the cells this enclosure really has."""

    coordination_root = enclosure.coordination_root
    task_root = coordination_root / "tasks" / REPO_NAME / "260921_icr_l20"
    worktree_group = enclosure.memory_worktree.parent
    code_root = enclosure.code_root
    memory_repo = enclosure.memory_repo
    memory_worktree = enclosure.memory_worktree
    code_commit = enclosure.code_commit
    memory_commit = enclosure.memory_commit
    return (
        "---\n"
        "schema: ar-series-contract/v1\n"
        "schemaVersion: 1.0\n"
        "kind: leaf\n"
        "task_id: 260921_ICR-L20\n"
        "task_name: ordinary_publication_route\n"
        f"repo_name: {REPO_NAME}\n"
        "workflow_kind: light-task\n"
        "memory_mode: external\n"
        "\n"
        "coordination:\n"
        f"  root: {coordination_root}\n"
        f"  task_root: {task_root}\n"
        f"  task_artifact: {task_root / 'task.md'}\n"
        f"  worktree_group: {worktree_group}\n"
        f"  leaf_id: {LEAF_ID}\n"
        "  parent_task_name: ordinary_publication_route\n"
        "\n"
        "code:\n"
        f"  repo_path: {code_root}\n"
        "  source_branch: main\n"
        f"  work_branch: {WORK_BRANCH}\n"
        f"  base_commit: {code_commit}\n"
        f"  worktree: {code_root}\n"
        "\n"
        "memory:\n"
        "  mode: external\n"
        f"  repo_path: {memory_repo}\n"
        "  source_branch: main\n"
        f"  work_branch: {WORK_BRANCH}\n"
        f"  base_commit: {memory_commit}\n"
        f"  worktree: {memory_worktree}\n"
        f"  ledger: {memory_repo / 'memory.md'}\n"
        "---\n"
    )


def _ordinary_argv(
    enclosure: OrdinaryEnclosure, listed: Path, candidate: Path, *extra: str
) -> list[str]:
    """The shipped invocation the canonical curator instructions carry, aimed at this enclosure."""

    return [
        "knowledge-ingest",
        "--contract",
        str(enclosure.contract_path),
        "--list",
        str(listed),
        "--candidate-directory",
        str(candidate),
        "--authorization-ref",
        AUTHORIZATION,
        *extra,
    ]


def _declared_location(enclosure: OrdinaryEnclosure) -> Path:
    """The location this route must publish to, resolved by the READ route's own owner.

    Restating ``<memory worktree>/knowledge.sqlite`` here would let a fixture agree with the
    implementation about a path while the read route selected nothing of the sort, which is exactly
    the drift the declared location exists to prevent.
    """

    return published_dataset_path(contract_context(load_contract(enclosure.contract_path)))


def _labels(database: Path) -> set[str]:
    """Every obligation label one dataset actually holds, read from the file itself."""

    connection = open_read_only_database(database)
    try:
        return {str(row[0]) for row in connection.execute("SELECT display_label FROM invariant")}
    finally:
        connection.close()


def _statements(database: Path, repository_id: str) -> list[str]:
    """The statements the MOUNTED read surface answers with, at the dataset's own snapshot."""

    view = knowledge_read_payload(
        ReadToolRequest(
            database_path=str(database),
            repository_id=repository_id,
            view="invariant",
        )
    )
    assert view["state"] == "view", view
    return [
        str(row["statement"]) for row in view["payload"]["rows"] if row["fact_kind"] == "statement"
    ]


def test_the_ordinary_route_publishes_to_the_declared_location_and_reads_it_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One ordinary run: the declared location receives the candidate and a reader selects it.

    The user operation is the curator's: author the obligation, publish it where the repository's
    knowledge lives, and know what was published. Four facts are measured, in the order the route
    establishes them: the destination the run selected is the one the READ route declares (derived
    here by that route's own owner), the publication owner reported ``published`` for it, the route's
    independent read-back confirms that exact identity, and the mounted read surface answers from
    that dataset -- so "the next task can read what this task published" is measured rather than
    inferred from a file appearing.
    """

    enclosure = _ordinary_enclosure(tmp_path / "cold-start")
    published = _declared_location(enclosure)
    listed = _one_entry_list(tmp_path, "first", "E-ROUTE", CODE_SYMBOL)

    report = _cli_json(
        _ordinary_argv(
            enclosure, listed, tmp_path / "candidate", "--commit", "--publish", "--json"
        ),
        capsys,
    )

    assert report["batchState"] == "changed", report
    assert [one["entryId"] for one in report["committed"]] == ["E-ROUTE"], report["committed"]
    assert published.is_file(), "the ordinary route published nowhere"
    assert report["publication"]["destination_ref"] == str(published), report["publication"]
    assert report["publication"]["state"] == "published", report["publication"]
    assert report["publicationRoute"].startswith(f"declared-location: {published}"), report[
        "publicationRoute"
    ]

    identity: dict[str, Any] = report["publication"]["identity"]
    read_back = report["publishedIdentity"]
    assert read_back["state"] == "confirmed", read_back
    assert read_back["datasetPath"] == str(published), read_back
    assert read_back["logicalDigest"] == identity["logical_digest"], read_back

    # The reader's own owner -- the one a taskless planner uses -- selects the dataset the writer
    # reached, in this same scope, at the identity the publication reported.
    selection = resolve_published_intent(contract_context(load_contract(enclosure.contract_path)))
    assert isinstance(selection, PublishedIntentSelection), selection
    assert selection.database_path == published
    assert selection.logical_digest == identity["logical_digest"]
    # ... and the MOUNTED read surface answers from that snapshot with the committed statement.
    assert _statements(published, str(identity["repository_id"])) == [
        "The obligation E-ROUTE records."
    ]


def test_a_custom_candidate_is_the_one_the_ordinary_route_publishes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A named scratch candidate is published, and no conventional candidate path is invented.

    ``--candidate-directory`` is what lets a curator author a draft somewhere other than the leaf's
    review root. The requirement is that the directory the caller SELECTED is the one published --
    not a conventional path guessed later at publication time. So the leaf's canonical review
    candidate directory must not exist afterwards, and the published dataset must carry the custom
    candidate's own entry.
    """

    enclosure = _ordinary_enclosure(tmp_path / "custom-candidate")
    published = _declared_location(enclosure)
    scratch = tmp_path / "scratch-draft"
    listed = _one_entry_list(tmp_path, "custom", "E-CUSTOM", CODE_OTHER_SYMBOL)

    report = _cli_json(
        _ordinary_argv(enclosure, listed, scratch, "--commit", "--publish", "--json"), capsys
    )

    assert report["candidateDirectory"] == str(scratch), report["candidateDirectory"]
    assert report["publication"]["state"] == "published", report["publication"]
    assert _labels(published) == {"E-CUSTOM"}, (
        "the published dataset is not the selected candidate's"
    )
    canonical = (
        load_contract(enclosure.contract_path).worktree_group
        / REVIEW_CANDIDATE_RELATIVE_ROOT
        / REVIEW_CANDIDATE_DIRECTORY
    )
    assert not canonical.exists(), (
        "publishing guessed the leaf's conventional candidate directory instead of the one the "
        f"caller selected: {canonical} was created"
    )
    assert report["publishedIdentity"]["state"] == "confirmed", report["publishedIdentity"]


def test_a_partial_refusal_names_each_entry_and_publishes_only_what_committed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One list, one committed entry and one refused entry: the report names both and claims neither.

    The boundary the packet states: a hand-off with both committed and refused entries names each and
    does not manufacture full completion. The failure it guards: reading a zero exit as "every entry
    committed". So the run's counts have to separate the two, the refused entry has to carry its own
    reason, and the published dataset has to hold exactly the entry that committed -- no more.
    """

    enclosure = _ordinary_enclosure(tmp_path / "partial")
    published = _declared_location(enclosure)
    listed = tmp_path / "partial.json"
    listed.write_text(
        json.dumps(
            [
                entry(
                    "E-COMMITTED",
                    targets=[target(CODE_FILE, locator=symbol(CODE_SYMBOL), route="pkg")],
                ),
                entry("E-REFUSED", targets=[target(GONE_PATH, locator={"kind": "file"})]),
            ]
        ),
        encoding="utf-8",
    )

    report = _cli_json(
        _ordinary_argv(
            enclosure, listed, tmp_path / "candidate", "--commit", "--publish", "--json"
        ),
        capsys,
    )

    assert report["batchState"] == "changed", report
    assert [one["entryId"] for one in report["committed"]] == ["E-COMMITTED"], report["committed"]
    assert [one["entryId"] for one in report["refused"]] == ["E-REFUSED"], report["refused"]
    assert report["refused"][0]["refusal"].startswith("target_path_unresolved"), report["refused"]
    # The counts are the denominator a caller reads: two entries in, one committed, one refused.
    assert report["counts"]["entriesRead"] == 2, report["counts"]
    assert report["counts"]["committed"] == 1, report["counts"]
    assert report["counts"]["refused"] == 1, report["counts"]
    # The committed half still reaches the repository, and the refused half is not fabricated into it.
    assert report["publication"]["state"] == "published", report["publication"]
    assert _labels(published) == {"E-COMMITTED"}, _labels(published)
    assert _statements(published, str(report["publication"]["identity"]["repository_id"])) == [
        "The obligation E-COMMITTED records."
    ]


def test_an_explicit_update_replaces_the_dataset_the_run_forked_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A second ordinary run forking from the published dataset updates exactly that dataset.

    The continuity operation: task B begins from what task A published, records a successor of A's
    obligation, and republishes -- so the destination holds both truths and the next task inherits
    them. What makes the update *explicit* is that the identity replaced is the one this run read
    from the baseline it named, not one the caller typed: the publication's own ``previous_identity``
    is the first run's published identity, and the route says the admitted dataset was the fork point.
    """

    enclosure = _ordinary_enclosure(tmp_path / "explicit-update")
    published = _declared_location(enclosure)
    first = _cli_json(
        _ordinary_argv(
            enclosure,
            _one_entry_list(tmp_path, "first", "E-FIRST", CODE_SYMBOL),
            tmp_path / "candidate-first",
            "--commit",
            "--publish",
            "--json",
        ),
        capsys,
    )
    prior_invariant, prior_revision = next(iter(_cycle01_candidate_revisions(published, "E-FIRST")))

    successor = tmp_path / "successor.json"
    successor.write_text(
        json.dumps(
            [
                _cycle01_hand_off(
                    "E-SUCCESSOR",
                    "The obligation now also covers the other construct.",
                    [target(CODE_FILE, locator=symbol(CODE_OTHER_SYMBOL), route="pkg")],
                    invariant_id=prior_invariant,
                    predecessor_revision_ids=[prior_revision],
                )
            ]
        ),
        encoding="utf-8",
    )
    second = _cli_json(
        _ordinary_argv(
            enclosure,
            successor,
            tmp_path / "candidate-second",
            "--commit",
            "--publish",
            "--baseline",
            str(published),
            "--json",
        ),
        capsys,
    )

    assert second["batchState"] == "changed", second
    assert second["publication"]["state"] == "published", second["publication"]
    assert (
        second["publication"]["previous_identity"]["logical_digest"]
        == first["publication"]["identity"]["logical_digest"]
    ), "the update did not replace the exact dataset this run forked from"
    assert (
        second["publication"]["identity"]["logical_digest"]
        != first["publication"]["identity"]["logical_digest"]
    ), "the second ordinary run published the same dataset it started from"
    assert "forks from" not in second["publicationRoute"], second["publicationRoute"]
    assert second["publishedIdentity"]["state"] == "confirmed", second["publishedIdentity"]

    # The successor landed under A's invariant -- it declares no new one -- and both of A's revisions
    # are on the line the next task inherits, which the mounted read answers with.
    assert _labels(published) == {"E-FIRST"}, _labels(published)
    assert len(_cycle01_revisions_of(published, prior_invariant)) == 2, (
        "the successor revision was not recorded under the invariant the producer named"
    )
    assert set(_statements(published, str(second["publication"]["identity"]["repository_id"]))) == {
        "The obligation E-FIRST records.",
        "The obligation now also covers the other construct.",
    }, "the mounted read does not answer with both revisions of the evolved obligation"
    selection = resolve_published_intent(contract_context(load_contract(enclosure.contract_path)))
    assert isinstance(selection, PublishedIntentSelection), selection
    assert selection.logical_digest == second["publication"]["identity"]["logical_digest"]


def test_an_exact_retry_republishes_no_change_and_still_reads_back_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Repeating the ordinary run writes nothing a second time and keeps the published bytes.

    An exact retry is the curator's response to an interrupted turn: the same list, the same entry
    id, the same candidate. The batch replays, the publication finds the destination already holding
    that logical dataset and reports ``no_change`` rather than replacing it, the bytes do not move,
    and the read-back still confirms the identity -- so "retry is safe" is measured on the file, not
    asserted from the exit code.
    """

    enclosure = _ordinary_enclosure(tmp_path / "exact-retry")
    published = _declared_location(enclosure)
    listed = _one_entry_list(tmp_path, "retry", "E-RETRY", CODE_SYMBOL)
    candidate = tmp_path / "candidate"
    first = _cli_json(
        _ordinary_argv(enclosure, listed, candidate, "--commit", "--publish", "--json"), capsys
    )
    bytes_before = published.read_bytes()

    again = _cli_json(
        _ordinary_argv(
            enclosure,
            listed,
            candidate,
            "--commit",
            "--publish",
            "--baseline",
            str(published),
            "--json",
        ),
        capsys,
    )

    assert again["batchState"] == "replayed", again["batchState"]
    assert [one["entryId"] for one in again["committed"]] == ["E-RETRY"], again["committed"]
    assert again["publication"]["state"] == "no_change", again["publication"]
    assert (
        again["publication"]["identity"]["logical_digest"]
        == first["publication"]["identity"]["logical_digest"]
    ), "the retry published a different dataset"
    assert published.read_bytes() == bytes_before, "the retry replaced the published bytes"
    assert again["publishedIdentity"]["state"] == "confirmed", again["publishedIdentity"]


def test_a_run_that_names_no_destination_states_that_it_published_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit zero beside a committed entry is not a publication, and the report says which it is.

    This is the packet's non-conforming example made measurable: a successful process exit read as
    proof that the repository now holds the knowledge. ``--commit`` is the knowledge-batch write and
    nothing else, so a run that named no destination commits and publishes nowhere -- and the report
    states the absence in its own field rather than leaving a caller to infer it from a null.
    """

    enclosure = _ordinary_enclosure(tmp_path / "no-destination")
    listed = _one_entry_list(tmp_path, "silent", "E-SILENT", CODE_SYMBOL)

    report = _cli_json(
        _ordinary_argv(enclosure, listed, tmp_path / "candidate", "--commit", "--json"), capsys
    )

    assert [one["entryId"] for one in report["committed"]] == ["E-SILENT"], report["committed"]
    assert report["publication"] is None, report["publication"]
    assert report["publishedIdentity"] is None, report["publishedIdentity"]
    assert report["publicationRoute"].startswith("not-selected:"), report["publicationRoute"]
    assert not _declared_location(enclosure).exists(), (
        "a run that named no destination published to the repository's declared location anyway"
    )


def test_a_refused_publication_leaves_the_destination_and_reads_nothing_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A destination the run never admitted is refused, untouched, and nothing is claimed about it.

    The second run authors into a candidate of its own and publishes with no baseline, so it admits
    nothing at the destination -- and the destination already holds task A's dataset. The publication
    owner refuses by name (``destination_stale``), the bytes are not replaced, and the route reads
    nothing back: a refused publication established no identity, and reporting one would be the
    fabricated success the packet forbids.
    """

    enclosure = _ordinary_enclosure(tmp_path / "refused-publication")
    published = _declared_location(enclosure)
    _cli_json(
        _ordinary_argv(
            enclosure,
            _one_entry_list(tmp_path, "first", "E-FIRST", CODE_SYMBOL),
            tmp_path / "candidate-first",
            "--commit",
            "--publish",
            "--json",
        ),
        capsys,
    )
    bytes_before = published.read_bytes()

    second = _cli_json(
        _ordinary_argv(
            enclosure,
            _one_entry_list(tmp_path, "second", "E-SECOND", CODE_OTHER_SYMBOL),
            tmp_path / "candidate-second",
            "--commit",
            "--publish",
            "--json",
        ),
        capsys,
    )

    assert second["batchState"] == "changed", second
    assert [one["entryId"] for one in second["committed"]] == ["E-SECOND"], second["committed"]
    assert second["publication"]["state"] == "refused", second["publication"]
    assert second["publication"]["refusal"]["code"] == "destination_stale", second["publication"]
    assert second["publishedIdentity"] is None, second["publishedIdentity"]
    assert published.read_bytes() == bytes_before, "a refused publication replaced the destination"
    assert _labels(published) == {"E-FIRST"}, _labels(published)


def test_two_destination_selections_are_refused_by_name_before_anything_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two destinations, or a typed identity beside the declared route, are invocation refusals.

    A run that named both ``--publish`` and ``--publish-to`` has not said which destination it
    means, and ``--expected-destination`` beside ``--publish`` would be a second, unchecked claim
    about the one fact the declared route derives from its own admitted baseline. Both are refused
    by name with the invocation's own exit code, before the list is read or a candidate is created.
    """

    enclosure = _ordinary_enclosure(tmp_path / "contradiction")
    listed = _one_entry_list(tmp_path, "contradiction", "E-CONTRADICTION", CODE_SYMBOL)
    candidate = tmp_path / "candidate"

    both = _ordinary_argv(
        enclosure, listed, candidate, "--commit", "--publish", "--publish-to", str(tmp_path / "x")
    )
    assert main(both) == 2
    assert "--publish and --publish-to" in capsys.readouterr().out

    typed = _ordinary_argv(
        enclosure,
        listed,
        candidate,
        "--commit",
        "--publish",
        "--expected-destination",
        json.dumps({"repository_id": "0" * 8, "schema_version": "1", "logical_digest": "0" * 64}),
    )
    assert main(typed) == 2
    assert "--expected-destination" in capsys.readouterr().out

    assert not candidate.exists(), "a refused invocation created a candidate"


def test_a_destination_selected_without_a_publication_says_so_in_the_route_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Selecting a destination is not publishing to it, and the route line does not claim otherwise.

    Two runs select the declared location and publish nothing, for two different reasons: a planning
    run (``--publish`` without ``--commit``), and a committed run whose every entry was refused, so
    the batch holds no entry to publish. Both leave ``publication`` and ``publishedIdentity`` absent
    and the location uncreated -- and the route line, which is what a curator reads to learn what
    happened, has to say which of those it was. A line that stopped at "the destination is admitted
    as holding nothing" was read as a publication claim for both, which is the one thing no field in
    this report may do.
    """

    enclosure = _ordinary_enclosure(tmp_path / "nothing-published")
    published = _declared_location(enclosure)
    listed = _one_entry_list(tmp_path, "would-publish", "E-PLANNED", CODE_SYMBOL)

    planned = _cli_json(
        _ordinary_argv(enclosure, listed, tmp_path / "planned", "--publish", "--json"), capsys
    )
    assert planned["dryRun"] is True, planned
    assert planned["publication"] is None, planned["publication"]
    assert planned["publishedIdentity"] is None, planned["publishedIdentity"]
    assert planned["publicationRoute"].startswith(f"declared-location: {published}"), planned[
        "publicationRoute"
    ]
    assert "nothing was published by this run" in planned["publicationRoute"], planned[
        "publicationRoute"
    ]
    assert "planning run" in planned["publicationRoute"], planned["publicationRoute"]
    assert "first publication" not in planned["publicationRoute"], planned["publicationRoute"]

    refused_list = tmp_path / "all-refused.json"
    refused_list.write_text(
        json.dumps(
            [entry("E-REFUSED-ONLY", targets=[target(GONE_PATH, locator={"kind": "file"})])]
        ),
        encoding="utf-8",
    )
    refused = _cli_json(
        _ordinary_argv(
            enclosure, refused_list, tmp_path / "all-refused", "--commit", "--publish", "--json"
        ),
        capsys,
    )
    assert refused["committed"] == [], refused["committed"]
    assert [one["entryId"] for one in refused["refused"]] == ["E-REFUSED-ONLY"], refused["refused"]
    assert refused["publication"] is None, refused["publication"]
    assert refused["publishedIdentity"] is None, refused["publishedIdentity"]
    assert "nothing was published by this run" in refused["publicationRoute"], refused[
        "publicationRoute"
    ]
    assert "committed no entry" in refused["publicationRoute"], refused["publicationRoute"]
    assert "first publication" not in refused["publicationRoute"], refused["publicationRoute"]
    assert not published.exists(), "a run that published nothing created the declared location"


def test_an_expected_destination_without_a_destination_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An expectation with nothing to expect at is refused, exactly as a rebase without a baseline is.

    ``--expected-destination`` admits what is at the destination the run selected. A run that selects
    none publishes nothing, so the value would be silently ignored -- and a caller whose argument was
    absorbed believes it was honoured. It is refused by name at the invocation's own exit code,
    before the list is read or a candidate is created; a well-formed identity is used here so the
    refusal is about the missing destination and not about the value.
    """

    enclosure = _ordinary_enclosure(tmp_path / "expectation-without-destination")
    listed = _one_entry_list(tmp_path, "expectation", "E-EXPECTATION", CODE_SYMBOL)
    candidate = tmp_path / "candidate"
    well_formed = json.dumps(
        {
            "repository_id": "00000000-0000-0000-0000-000000000000",
            "schema_version": "1",
            "logical_digest": "0" * 64,
        }
    )

    argv = _ordinary_argv(
        enclosure, listed, candidate, "--commit", "--expected-destination", well_formed
    )
    assert main(argv) == 2
    refusal = capsys.readouterr().out
    assert "--expected-destination" in refusal, refusal
    assert "--publish-to or --publish" in refusal, refusal
    assert not candidate.exists(), "a refused invocation created a candidate"


def test_a_declared_location_that_cannot_be_resolved_is_refused_rather_than_guessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No memory layer means no declared location, and the run says so instead of publishing anywhere.

    The ordinary route's destination is resolved, never defaulted: an enclosure whose repository has
    no memory layer -- the memory repository the read route resolves is gone -- has no declared
    publication location at all. A fallback here is exactly the failure the requirement forbids, so
    the invocation is refused by name at its own exit code and no candidate is created on the way.
    """

    enclosure = _ordinary_enclosure(tmp_path / "unresolvable-location")
    listed = _one_entry_list(tmp_path, "unresolvable", "E-UNRESOLVABLE", CODE_SYMBOL)
    candidate = tmp_path / "candidate"
    shutil.rmtree(enclosure.memory_repo)

    argv = _ordinary_argv(enclosure, listed, candidate, "--commit", "--publish", "--json")
    assert main(argv) == 2
    refusal = capsys.readouterr().out
    assert "declared published dataset location could not be resolved" in refusal, refusal
    assert str(enclosure.contract_path) in refusal, refusal
    assert not candidate.exists(), "a refused invocation created a candidate"
