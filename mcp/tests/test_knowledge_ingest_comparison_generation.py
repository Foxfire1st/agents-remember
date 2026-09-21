"""The review's original baseline survives repeated ingest, and a rebase is an explicit generation.

The defect these cases seal (ICR-R18, A21): with ONE path used for both ``--baseline`` and
``--publish-to``, two successive successful writes each reported ``changed`` and published -- and the
second one re-placed the review's before half from the bytes it captured, which by then *were* the
first run's publication. The comparison then opened on a dataset that already contained the
addition, so the review showed it present on both sides with an empty delta. The prior refused-rerun
repair did not cover the successful-update path, and capturing the baseline earlier cannot cover it
either: the bytes are a different dataset by then, not a later read of the same one.

The module measures the successful path a comparison's before side has to survive, each through the
shipped CLI on a real enclosure:

* **two successful writes over one shared path** keep the dataset the comparison was opened on, while
  the second write still lands (its publication identity and the entry it committed both move);
* **an exact retry and a refused changed retry** leave that dataset byte-identical;
* **a deliberate rebase** begins a new generation whose record names the generation it replaced *and*
  that generation's exact dataset identity -- and whose id a reader can recompute from those facts.

Every way a placement *fails* -- a baseline that is unavailable, a rebase whose two legs fail
separately, a leg that fails after its bytes landed, and the invocation refusals -- is measured beside
this module in ``test_knowledge_ingest_failure_windows.py``, which imports the journey fixtures below
rather than copying them. This module owns those fixtures because the successful journey is what they
were written for.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from agents_remember.application.knowledge_baseline_generation import (
    generation_identity,
    read_baseline_generation,
)
from agents_remember.application.knowledge_curator_ingest import (
    IngestPublication,
    IngestSelection,
    ingest_curator_list,
)
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from test_knowledge_curator_ingest_list import (
    AUTHORIZATION,
    CODE_FILE,
    CODE_OTHER_SYMBOL,
    CODE_SYMBOL,
    SourcePair,
    _cli_json,
    _cycle01_hand_off,
    _cycle01_publish_baseline,
    _cycle01_sibling_contract,
    _fork_ingest_argv,
    _one_entry_list,
    _private_pair,
    _review_before_half,
    symbol,
    target,
)

pytestmark = pytest.mark.evidence_unit


def _digest(path: Path) -> str:
    """The bytes at one path, as the identity this module's cases compare before and after."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity_of(path: Path) -> SnapshotIdentity:
    """One dataset's own logical identity, read through the shipped reader."""

    return dataset_identity(path)


def _publish_a_later_line(
    private: SourcePair, tmp_path: Path, destination: Path, expected: SnapshotIdentity | None
) -> SnapshotIdentity:
    """Publish one more real dataset on the repository's line, through the shipped publication owner.

    The line a later task forks from moves on independently of any one leaf, and this is that act:
    one more obligation published to one dataset path. It is driven through the application owner
    rather than the CLI because the CLI is the surface these cases measure -- a run of it would fill
    *this* leaf's before half on the way, which is exactly the state each case has to set up
    deliberately.
    """

    report = cast("Any", ingest_curator_list)(
        _cycle01_sibling_contract(private, tmp_path, "later"),
        [
            _cycle01_hand_off(
                "LATER",
                "The obligation LATER records, published after this leaf forked.",
                [target(CODE_FILE, locator=symbol(CODE_OTHER_SYMBOL), route="pkg")],
            )
        ],
        IngestSelection(
            candidate_directory=tmp_path / "candidate-later",
            authorization_ref=AUTHORIZATION,
            dry_run=False,
            publication=IngestPublication(
                destination_path=destination, expected_destination=expected
            ),
        ),
    )
    assert report.batch_state == "changed", report.batch_refusal
    assert report.publication is not None and report.publication.identity is not None
    return report.publication.identity


def _opened_comparison(
    tmp_path: Path,
    name: str,
    entry_id: str,
    code_symbol: str,
    capsys: pytest.CaptureFixture[str],
) -> tuple[SourcePair, Path, Path, Path, Path, dict[str, Any]]:
    """One leaf whose comparison is opened: the fork point is placed, and one write has landed.

    The returned tuple is the fixture pair, the shared baseline/publication path, the enclosure, the
    candidate directory, the review's before half, and the report of the run that opened the
    comparison. Every case below starts from this state because it is the state the defect appears
    in -- the comparison is open, the leaf's own line has published over the fork point it forked
    from, and the next run is handed those published bytes as its baseline.
    """

    private = _private_pair(tmp_path / name)
    published = private.memory_root / "knowledge.sqlite"
    published_identity = _cycle01_publish_baseline(private, tmp_path, published)
    fork = tmp_path / "leaf.sqlite"
    shutil.copyfile(published, fork)
    contract = _cycle01_sibling_contract(private, tmp_path, "fork")
    candidate = tmp_path / "candidate"
    listed = _one_entry_list(tmp_path, "one", entry_id, code_symbol)
    opened = _cli_json(
        _fork_ingest_argv(
            contract, listed, candidate, fork, published_identity.model_dump(mode="json")
        ),
        capsys,
    )
    assert opened["batchState"] == "changed", opened
    assert opened["publication"]["state"] == "published", opened["publication"]
    return private, fork, contract, candidate, _review_before_half(contract), opened


def test_a_second_successful_ingest_over_one_path_keeps_the_original_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two successful writes over one shared path still compare against the original task fork.

    The user operation is the ordinary one: the curator authors the next obligation for a leaf whose
    line already published once, naming the same path for ``--baseline`` and ``--publish-to`` because
    that path *is* the leaf's line. Both runs legitimately succeed and both publish -- and the
    comparison the reviewer opens afterwards must still be against the fork point this task started
    from, not against the publication the first run left at that path.

    Four facts are measured, and the fourth is the defect: the second write lands (its entry commits
    and the published identity moves), the before half is byte-identical to the fork point, its
    dataset identity is still the original baseline's while the shared path holds the update, and the
    report says which generation stands there and how a deliberate rebase would replace it.
    """

    _private, fork, contract, candidate, half, opened = _opened_comparison(
        tmp_path, "repeated-success", "E-ONE", CODE_SYMBOL, capsys
    )
    original = _digest(half)
    assert (
        _identity_of(half).logical_digest
        == opened["publication"]["previous_identity"]["logical_digest"]
    ), "the half no longer holds the dataset the first run forked from"

    second = _one_entry_list(tmp_path, "two", "E-TWO", CODE_OTHER_SYMBOL)
    report = _cli_json(
        _fork_ingest_argv(contract, second, candidate, fork, opened["publication"]["identity"]),
        capsys,
    )

    # The update still lands: the entry commits and the shared path holds a new dataset identity.
    assert report["batchState"] == "changed", report
    assert [one["entryId"] for one in report["committed"]] == ["E-TWO"], report["committed"]
    assert report["publication"]["state"] == "published", report["publication"]
    assert (
        report["publication"]["identity"]["logical_digest"]
        != (opened["publication"]["identity"]["logical_digest"])
    ), "the second write did not actually change the published dataset"

    # THE REGRESSION: the comparison's original baseline is still what the review opens on.
    assert report["reviewBaseline"].startswith("not-placed:"), report["reviewBaseline"]
    assert _digest(half) == original, (
        "the second successful run replaced the review's before half with its own publication, so "
        "the review now shows the addition present on both sides with an empty delta"
    )
    assert (
        _identity_of(half).logical_digest != report["publication"]["identity"]["logical_digest"]
    ), "the half holds the dataset this run published instead of the one the comparison opened on"
    record = read_baseline_generation(half.parent)
    assert record is not None and record.generation_index == 1, record
    assert record.logical_digest == _identity_of(half).logical_digest, record
    assert record.generation_id in report["reviewBaseline"], report["reviewBaseline"]
    assert str(half) in report["reviewBaseline"], report["reviewBaseline"]
    assert "--rebase-baseline" in report["reviewBaseline"], report["reviewBaseline"]
    assert str(fork) in report["reviewBaseline"], report["reviewBaseline"]


def test_an_exact_retry_and_a_refused_changed_retry_keep_the_original_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The retry paths of one comparison leave its before side byte-identical.

    Two retries are the boundary the packet names. The exact retry repeats the operation the first
    run already performed, so it writes no new row and must restate nothing. The changed retry wears
    the same entry id with a different statement, which the write plane correctly refuses as a
    conflict -- and the refusal must not become a second way to move the before side either.

    Both are measured through the CLI, on the same half, with the byte identity taken before each
    run: the exact retry reports a replay with the entry it already holds, the changed retry reports
    the typed refusal, and neither changes the dataset the comparison opens on.
    """

    _private, fork, contract, candidate, half, opened = _opened_comparison(
        tmp_path, "retries", "E-ONE", CODE_SYMBOL, capsys
    )
    original = _digest(half)
    listed = _one_entry_list(tmp_path, "one", "E-ONE", CODE_SYMBOL)

    exact = _cli_json(
        _fork_ingest_argv(contract, listed, candidate, fork, opened["publication"]["identity"]),
        capsys,
    )
    assert exact["batchState"] == "replayed", exact
    assert [one["entryId"] for one in exact["committed"]] == ["E-ONE"], exact["committed"]
    assert exact["reviewBaseline"].startswith("not-placed:"), exact["reviewBaseline"]
    assert _digest(half) == original, "an exact retry moved the comparison's original baseline"

    changed = _one_entry_list(tmp_path, "changed", "E-ONE", CODE_SYMBOL)
    authored = json.loads(changed.read_text(encoding="utf-8"))
    authored[0]["statement"] = "The obligation E-ONE records, corrected after review."
    changed.write_text(json.dumps(authored), encoding="utf-8")
    refused = _cli_json(
        _fork_ingest_argv(contract, changed, candidate, fork, exact["publication"]["identity"]),
        capsys,
    )
    assert refused["batchState"] == "no_change", refused
    assert refused["committed"] == [], refused
    assert [one["refusal"].split(":")[0] for one in refused["refused"]] == [
        "allocation_content_conflict"
    ], refused["refused"]
    assert refused["publication"] is None, refused["publication"]
    assert refused["reviewBaseline"].startswith("not-placed:"), refused["reviewBaseline"]
    assert _digest(half) == original, (
        "a refused changed retry replaced the comparison's original baseline with the dataset this "
        "leaf published"
    )


def test_a_deliberate_rebase_begins_a_recorded_generation_with_explicit_lineage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A deliberate rebase is a new generation whose record names exactly what it replaced.

    The user operation is the one a curator performs when the comparison's fork point is genuinely
    obsolete: the repository's line has published on, and the review must be re-opened against the
    new line rather than against a dataset that no longer exists anywhere but this leaf. That is a
    *deliberate* act, and the difference between it and the silent replacement the packet forbids is
    the record: which generation the half held, that generation's exact dataset identity, and a new
    generation id a reader can recompute from those facts.

    The case measures all four: the report names both generations, the half holds the new baseline's
    bytes, the record's lineage is the old generation's id *and* identity, and its own id is the one
    its recorded facts derive.
    """

    private, _fork, contract, candidate, half, _opened = _opened_comparison(
        tmp_path, "rebase", "E-ONE", CODE_SYMBOL, capsys
    )
    original = read_baseline_generation(half.parent)
    assert original is not None and original.generation_index == 1, original
    original_identity = _identity_of(half)

    later = tmp_path / "later.sqlite"
    later_identity = _publish_a_later_line(private, tmp_path, later, None)
    later_bytes = later.read_bytes()
    listed = _one_entry_list(tmp_path, "rebase", "E-REBASE", CODE_OTHER_SYMBOL)

    rebased = _cli_json(
        [
            *_fork_ingest_argv(
                contract, listed, candidate, later, later_identity.model_dump(mode="json")
            ),
            "--rebase-baseline",
        ],
        capsys,
    )

    assert rebased["batchState"] == "changed", rebased
    assert [one["entryId"] for one in rebased["committed"]] == ["E-REBASE"], rebased["committed"]
    assert rebased["publication"]["state"] == "published", rebased["publication"]
    assert rebased["reviewBaseline"].startswith("placed:"), rebased["reviewBaseline"]
    assert "rebased from generation" in rebased["reviewBaseline"], rebased["reviewBaseline"]
    assert original.generation_id in rebased["reviewBaseline"], rebased["reviewBaseline"]
    assert half.read_bytes() == later_bytes, (
        "the rebase did not place the baseline the caller deliberately named"
    )

    record = read_baseline_generation(half.parent)
    assert record is not None, "the rebase recorded no generation"
    assert record.generation_index == 2, record
    assert record.parent_generation_id == original.generation_id, record
    assert record.parent_identity == original_identity, (
        "the rebase does not record which dataset it replaced"
    )
    assert record.logical_digest == later_identity.logical_digest, record
    assert record.selected_baseline == str(later), record
    assert record.generation_id == generation_identity(
        generation_index=2,
        identity=later_identity,
        parent_generation_id=original.generation_id,
    ), "the generation id is not the one this record's own facts derive"
    assert record.generation_id in rebased["reviewBaseline"], rebased["reviewBaseline"]
