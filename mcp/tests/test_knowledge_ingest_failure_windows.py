"""Every way a placement can fail without losing the baseline a comparison was opened on.

The defect these cases seal (ICR-R18, A21): with one path used for both ``--baseline`` and
``--publish-to``, the before half was re-filled from whatever the latest run captured. That is only
half the failure surface. The other half is what a *refused* placement does to a half that already
holds the comparison's original baseline -- the state every leaf that ingested before the generation
record existed is in -- and what the two legs of one placement do to it when they fail separately.

Five windows are measured, each through the shipped CLI on a real enclosure, and none of them may
leave the half holding bytes that no record describes as though they were the original:

* an **adopted** half (a dataset placed before generation records existed) is kept, not treated as an
  empty slot a later run may fill;
* a **record that disagrees with its bytes**, and a **record path occupied** by something that is not a
  record file, are both named damage -- never "this half records no generation";
* a **rebase whose record leg fails** leaves the half byte-identical (the record is durable before the
  bytes it names, so nothing was replaced);
* a **rebase whose dataset leg fails after the record landed** leaves the previous bytes beside a record
  that disagrees with them: the named damage a reader acts on;
* a leg whose **directory flush fails after its bytes landed** is reported as "did not report success",
  because it has not measured that they are absent -- the report's appended read-back, with the dataset
  identity, is what states what actually landed.

Refusals that name an invocation nothing can honour (``--rebase-baseline`` without ``--baseline``) and
the write site's own "only bytes that read as a dataset" precondition belong to the same surface and sit
here too. The successful path -- repeated ingest, retries and a deliberate rebase -- is measured in
``test_knowledge_ingest_comparison_generation.py``, whose journey fixtures this module imports rather
than copying.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from agents_remember.application import knowledge_baseline_generation
from agents_remember.application.knowledge_baseline_generation import (
    BASELINE_GENERATION_NAME,
    BaselineGeneration,
    BaselineRun,
    CapturedBaseline,
    baseline_generation_path,
    fill_admitted_before_half,
    generation_identity,
    place_original_baseline,
    read_admitted_baseline,
    read_baseline_generation,
    read_standing_generation,
    write_baseline_generation,
)
from agents_remember.cli.__main__ import main
from agents_remember.kernel import atomic_write as kernel_atomic_write
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.snapshot import CANDIDATE_DATABASE_NAME
from snapshot_lifecycle_test_support import build_case, create
from test_knowledge_curator_ingest_list import (
    AUTHORIZATION,
    CODE_OTHER_SYMBOL,
    CODE_SYMBOL,
    SourcePair,
    _cli_json,
    _cycle01_publish_baseline,
    _cycle01_sibling_contract,
    _fork_ingest_argv,
    _one_entry_list,
    _private_pair,
    _review_before_half,
)
from test_knowledge_ingest_comparison_generation import (
    _digest,
    _identity_of,
    _publish_a_later_line,
)

pytestmark = pytest.mark.evidence_unit


def _adopted_half(
    tmp_path: Path, name: str
) -> tuple[Path, Path, SourcePair, Path, SnapshotIdentity]:
    """One half placed before generation records existed, and a published dataset it is a copy of.

    The returned tuple is the enclosure, the half's dataset path, the fixture pair, the *published*
    dataset the half's bytes are a copy of, and that dataset's identity. A half in this state is what
    every leaf that ingested before this record existed is holding -- no record beside the bytes -- so
    every failure case below starts here rather than from a recorded generation.
    """

    private = _private_pair(tmp_path / name)
    published = private.memory_root / "knowledge.sqlite"
    published_identity = _cycle01_publish_baseline(private, tmp_path, published)
    contract = _cycle01_sibling_contract(private, tmp_path, "fork")
    half = _review_before_half(contract)
    half.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(published, half)
    assert read_standing_generation(half.parent).state == "adopted", half
    assert read_baseline_generation(half.parent) is None, "the case needs a half with no record"
    return contract, half, private, published, published_identity


def _fail_the_directory_flush(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Make the kernel owner's post-rename flush fail for one directory, and for nothing else.

    ``atomic_write_bytes`` replaces the destination and *then* flushes the directory that names it, so
    a failure raised at this point arrives after the bytes are already on disk: the leg never returned
    success and the bytes are there anyway. That is the window the leg wording has to describe
    honestly -- the call did not report success, and what landed is read back rather than assumed.
    """

    real_flush = kernel_atomic_write._fsync_directory

    def flush(directory_of: Path) -> None:
        if Path(directory_of) == directory:
            raise OSError("the directory flush after the rename failed")
        real_flush(directory_of)

    monkeypatch.setattr(kernel_atomic_write, "_fsync_directory", flush)


def test_a_half_that_records_no_generation_keeps_its_baseline_as_the_original(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A before half placed before generation records existed is kept, not adopted as empty.

    This is the state every half placed by any earlier run is in, and it is the one an implementation
    is most likely to get wrong: a dataset with no record beside it is not an empty slot. It is the
    dataset this comparison was opened on, so a later run whose baseline differs must name it and
    leave it exactly as it is -- the same answer a recorded generation gets, from the facts that are
    actually there rather than from a record that never existed.
    """

    private = _private_pair(tmp_path / "adopted")
    published = private.memory_root / "knowledge.sqlite"
    published_identity = _cycle01_publish_baseline(private, tmp_path, published)
    contract = _cycle01_sibling_contract(private, tmp_path, "fork")
    half = _review_before_half(contract)
    half.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(published, half)
    original = _digest(half)
    assert read_standing_generation(half.parent).state == "adopted", half
    assert read_baseline_generation(half.parent) is None, "the case no longer exercises a bare half"

    later = tmp_path / "later.sqlite"
    later_identity = _publish_a_later_line(private, tmp_path, later, None)
    listed = _one_entry_list(tmp_path, "adopted", "E-ADOPTED", CODE_SYMBOL)
    report = _cli_json(
        _fork_ingest_argv(
            contract, listed, tmp_path / "candidate", later, later_identity.model_dump(mode="json")
        ),
        capsys,
    )

    assert report["batchState"] == "changed", report
    assert [one["entryId"] for one in report["committed"]] == ["E-ADOPTED"], report["committed"]
    assert report["reviewBaseline"].startswith("not-placed:"), report["reviewBaseline"]
    assert "carries no recorded generation" in report["reviewBaseline"], report["reviewBaseline"]
    assert "--rebase-baseline" in report["reviewBaseline"], report["reviewBaseline"]
    adopted = generation_identity(
        generation_index=1, identity=published_identity, parent_generation_id=None
    )
    assert adopted in report["reviewBaseline"], report["reviewBaseline"]
    assert _digest(half) == original, (
        "a half with no generation record was treated as an empty slot and replaced"
    )


def test_a_recorded_generation_that_disagrees_with_its_bytes_is_named_not_trusted(
    tmp_path: Path,
) -> None:
    """A record naming a dataset that is not beside it is damage, and nothing is placed over it.

    The record is the half's own statement of which generation it holds, so a record that no longer
    matches its bytes cannot be read as "this comparison's baseline" -- and it must not be repaired
    by quietly writing a new dataset over the top of it either. Both halves of the answer are
    measured here without a CLI run, because the state is a property of the half rather than of any
    one invocation.
    """

    half = tmp_path / "half"
    case = build_case(tmp_path / "recorded-half")
    assert create(case).state == "created", "the fixture did not produce a real dataset"
    identity = _identity_of(case.database_path)
    half.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(case.database_path, half / CANDIDATE_DATABASE_NAME)
    write_baseline_generation(
        half,
        BaselineGeneration(
            generation_id=generation_identity(
                generation_index=1, identity=identity, parent_generation_id=None
            ),
            generation_index=1,
            selected_baseline=str(case.database_path),
            repository_id=identity.repository_id,
            schema_version=identity.schema_version,
            logical_digest="0" * 64,
            recorded_at="2026-09-21T00:00:00+00:00",
            leaf_id="260921-ICR-L18",
            contract_path=str(tmp_path / "series-contract.md"),
            authorization_ref=AUTHORIZATION,
        ),
    )

    standing = read_standing_generation(half)
    assert standing.state == "damaged", standing
    assert "logical_digest" in standing.detail, standing.detail

    captured = CapturedBaseline(origin=case.database_path, payload=case.database_path.read_bytes())
    before = _digest(half / CANDIDATE_DATABASE_NAME)
    report = fill_admitted_before_half(
        half=half,
        candidate_directory=tmp_path / "candidate",
        captured=captured,
        run=BaselineRun(
            leaf_id="260921-ICR-L18",
            contract_path=str(tmp_path / "series-contract.md"),
            authorization_ref=AUTHORIZATION,
        ),
        rebase=True,
    )
    assert report.startswith("not-placed:"), report
    assert "left the half exactly as it is" in report, report
    assert _digest(half / CANDIDATE_DATABASE_NAME) == before, report


def test_a_rebase_without_a_baseline_is_refused_before_anything_is_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--rebase-baseline`` without ``--baseline`` names no generation to begin from.

    A rebase is a transition from one admitted baseline to another, so the action without the dataset
    states nothing to transition from. Reading it as the cold start would quietly do something other
    than what the caller asked for, so it is refused by name before the enclosure or the list is
    even read -- which the absent contract path here demonstrates.
    """

    listed = tmp_path / "empty.json"
    listed.write_text("[]", encoding="utf-8")
    capsys.readouterr()

    exit_code = main(
        [
            "knowledge-ingest",
            "--contract",
            str(tmp_path / "absent-contract.md"),
            "--list",
            str(listed),
            "--authorization-ref",
            AUTHORIZATION,
            "--commit",
            "--json",
            "--rebase-baseline",
        ]
    )

    assert exit_code == 2, capsys.readouterr().out
    printed = capsys.readouterr().out
    assert "--rebase-baseline" in printed and "--baseline" in printed, printed
    assert not (tmp_path / "candidate").exists()


def test_the_write_site_publishes_only_bytes_that_read_as_a_dataset(tmp_path: Path) -> None:
    """The before half is filled with bytes that read as a dataset of this code, or not at all.

    This is the write site's own precondition rather than a repeat of the admission's: the admission
    answers for the *input path* it was handed, while this answers for the bytes that would actually
    land in the half -- the last thing standing between a corrupt fork point and a before side no
    comparison can open. It is driven at this level because the operation refuses an unreadable
    selected baseline earlier, which is the right order and also why a CLI-level case cannot reach
    this rule.

    Both directions are measured: bytes that are not a dataset are refused by name and leave nothing
    behind, and a real dataset is published with the record that names its generation, then kept as
    it is by the run that follows it.
    """

    half = tmp_path / "write-site" / "baseline"
    origin = tmp_path / "corrupt.sqlite"
    origin.write_bytes(b"this is not a database\n")
    captured = CapturedBaseline(origin=origin, payload=origin.read_bytes())
    run = BaselineRun(
        leaf_id="260921-ICR-L18",
        contract_path=str(tmp_path / "series-contract.md"),
        authorization_ref=AUTHORIZATION,
    )

    refused = read_admitted_baseline(captured)
    assert isinstance(refused, str), refused
    assert str(origin) in refused and "could not be read as a dataset" in refused, refused
    assert not half.exists(), refused

    case = build_case(tmp_path / "write-site-valid")
    assert create(case).state == "created", "the fixture did not produce a real dataset"
    placeable = CapturedBaseline(origin=case.database_path, payload=case.database_path.read_bytes())
    admitted = read_admitted_baseline(placeable)
    assert not isinstance(admitted, str), admitted
    placed = place_original_baseline(half, admitted, run=run)
    assert placed.state == "placed", placed.detail
    assert placed.record is not None and placed.record.generation_index == 1, placed.record
    destination = half / CANDIDATE_DATABASE_NAME
    assert destination.read_bytes() == placeable.payload, placed.detail
    assert read_baseline_generation(half) == placed.record, (
        "the generation the write site reported is not the one recorded beside the dataset"
    )
    assert baseline_generation_path(half).is_file(), placed.detail

    retry = fill_admitted_before_half(
        half=half,
        candidate_directory=tmp_path / "write-site-candidate",
        captured=placeable,
        run=run,
        rebase=False,
    )
    assert retry.startswith("present:"), retry
    assert destination.read_bytes() == placeable.payload, (
        "a retry restated the before side it was first handed"
    )


def test_a_rebase_whose_record_leg_fails_leaves_the_original_baseline_in_place(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The record is durable before the bytes it names, so a blocked record cannot lose the original.

    The user operation is a deliberate rebase of a half whose original baseline was never recorded,
    on a leaf where the record cannot be written (the half's own directory is not writable). The
    defect this seals is an ordering one: with the dataset landing first, that failure left the half
    holding the **replacement** bytes with no record beside them, which reads as an *adopted*
    baseline -- so the next run was told the replacement was the dataset this comparison had always
    opened on, and the one it really opened on was gone.

    Four facts are measured: the run reports the record leg and claims no generation; the half still
    holds the pre-rebase bytes, byte for byte, and its dataset identity is still the original's; no
    record appeared; and a following run handed the true original reports it as the standing baseline
    (`present:`) instead of being told the replacement is it.
    """

    contract, half, _private, published, published_identity = _adopted_half(tmp_path, "record-leg")
    original = _digest(half)
    later = tmp_path / "later.sqlite"
    later_identity = _publish_a_later_line(_private, tmp_path, later, None)
    later_bytes = later.read_bytes()
    listed = _one_entry_list(tmp_path, "record-leg", "E-RECORD", CODE_SYMBOL)

    os.chmod(half.parent, 0o555)
    try:
        refused = _cli_json(
            [
                *_fork_ingest_argv(
                    contract,
                    listed,
                    tmp_path / "candidate",
                    later,
                    later_identity.model_dump(mode="json"),
                ),
                "--rebase-baseline",
            ],
            capsys,
        )
    finally:
        os.chmod(half.parent, 0o755)

    assert refused["batchState"] == "changed", refused
    assert refused["publication"]["state"] == "published", refused["publication"]
    assert refused["reviewBaseline"].startswith("not-placed:"), refused["reviewBaseline"]
    assert "generation record leg did not report success" in refused["reviewBaseline"], refused[
        "reviewBaseline"
    ]
    assert "the half now reads adopted" in refused["reviewBaseline"], refused["reviewBaseline"]
    assert _digest(half) == original, (
        "a rebase whose record could not be written replaced the baseline the comparison was opened "
        "on, and with no record beside the replacement it would read as the original"
    )
    assert half.read_bytes() != later_bytes, refused["reviewBaseline"]
    assert _identity_of(half).logical_digest == published_identity.logical_digest
    assert read_baseline_generation(half.parent) is None, (
        "a generation was recorded after a refusal"
    )

    follow = _one_entry_list(tmp_path, "record-leg-follow", "E-FOLLOW", CODE_OTHER_SYMBOL)
    handed_the_original = _cli_json(
        _fork_ingest_argv(
            contract,
            follow,
            tmp_path / "candidate",
            published,
            published_identity.model_dump(mode="json"),
        ),
        capsys,
    )
    assert handed_the_original["reviewBaseline"].startswith("present:"), (
        "the half no longer holds the dataset the comparison was opened on, so a run handed the true "
        f"original was told something else stood there: {handed_the_original['reviewBaseline']}"
    )


def test_a_rebase_whose_dataset_leg_fails_names_the_damage_it_left(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one window a record-first rebase can leave is the previous bytes beside a foreign record.

    This is the other side of the ordering contract: the record is written first and the replacement
    bytes then fail to land. What must remain is the *previous* baseline with a record that disagrees
    with it -- the named damage a reader can act on -- and never the replacement with no record.

    The dataset leg is made to fail where it really fails, at the kernel write every placement goes
    through, and only for the half's dataset path: the run's own commit and publication still land, so
    the case measures the placement leg and nothing else. It then asserts the state the half is left
    in, the report's own account of it, and that a following run refuses to build on it.
    """

    contract, half, _private, _published, _published_identity = _adopted_half(
        tmp_path, "dataset-leg"
    )
    original = _digest(half)
    later = tmp_path / "later.sqlite"
    later_identity = _publish_a_later_line(_private, tmp_path, later, None)
    listed = _one_entry_list(tmp_path, "dataset-leg", "E-DATASET", CODE_SYMBOL)

    real_write = knowledge_baseline_generation.atomic_write_bytes

    def fail_the_dataset_leg(path: Path, payload: bytes) -> None:
        if Path(path).name == CANDIDATE_DATABASE_NAME:
            raise OSError("the dataset leg could not be replaced")
        real_write(path, payload)

    monkeypatch.setattr(knowledge_baseline_generation, "atomic_write_bytes", fail_the_dataset_leg)
    refused = _cli_json(
        [
            *_fork_ingest_argv(
                contract,
                listed,
                tmp_path / "candidate",
                later,
                later_identity.model_dump(mode="json"),
            ),
            "--rebase-baseline",
        ],
        capsys,
    )
    monkeypatch.undo()

    assert refused["batchState"] == "changed", refused
    assert refused["reviewBaseline"].startswith("not-placed:"), refused["reviewBaseline"]
    assert "dataset leg did not report success" in refused["reviewBaseline"], refused[
        "reviewBaseline"
    ]
    assert "the half now reads damaged" in refused["reviewBaseline"], refused["reviewBaseline"]
    assert _digest(half) == original, "the refused dataset leg still replaced the previous baseline"
    standing = read_standing_generation(half.parent)
    assert standing.state == "damaged", standing
    assert "logical_digest" in standing.detail, standing.detail
    record = read_baseline_generation(half.parent)
    assert record is not None and record.generation_index == 2, record
    assert record.logical_digest == later_identity.logical_digest, record
    assert record.parent_identity is not None, record

    follow = _one_entry_list(tmp_path, "dataset-leg-follow", "E-FOLLOW", CODE_OTHER_SYMBOL)
    refused_again = _cli_json(
        _fork_ingest_argv(
            contract,
            follow,
            tmp_path / "candidate",
            later,
            _identity_of(later).model_dump(mode="json"),
        ),
        capsys,
    )
    assert refused_again["reviewBaseline"].startswith("not-placed:"), refused_again[
        "reviewBaseline"
    ]
    assert "left the half exactly as it is" in refused_again["reviewBaseline"], refused_again[
        "reviewBaseline"
    ]
    assert _digest(half) == original, refused_again["reviewBaseline"]


def test_an_obstructed_generation_record_path_is_damage_and_not_an_absent_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A record path occupied by something that is not a record is damage, never "no record here".

    The generation record path is where the half's own statement of what it holds lives, so an
    obstruction at that path -- a directory left where the record belongs -- must not be read as "this
    half records no generation": that reading is the one that adopts whatever bytes are beside it as
    the comparison's original baseline, and it would let the obstruction go on being invisible while
    runs kept rebasing over it.

    Both halves of the answer are measured: the reader names it as damage, and the CLI run then places
    nothing and leaves the pre-rebase bytes exactly where they are.
    """

    contract, half, _private, _published, published_identity = _adopted_half(tmp_path, "obstructed")
    original = _digest(half)
    (half.parent / BASELINE_GENERATION_NAME).mkdir()
    later = tmp_path / "later.sqlite"
    later_identity = _publish_a_later_line(_private, tmp_path, later, None)
    listed = _one_entry_list(tmp_path, "obstructed", "E-OBSTRUCTED", CODE_SYMBOL)

    standing = read_standing_generation(half.parent)
    assert standing.state == "damaged", standing
    assert "occupied by something that is not a record file" in standing.detail, standing.detail

    refused = _cli_json(
        [
            *_fork_ingest_argv(
                contract,
                listed,
                tmp_path / "candidate",
                later,
                later_identity.model_dump(mode="json"),
            ),
            "--rebase-baseline",
        ],
        capsys,
    )
    assert refused["reviewBaseline"].startswith("not-placed:"), refused["reviewBaseline"]
    assert "occupied by something that is not a record file" in refused["reviewBaseline"], refused[
        "reviewBaseline"
    ]
    assert _digest(half) == original, (
        "an obstructed record path let the rebase replace the baseline"
    )
    assert _identity_of(half).logical_digest == published_identity.logical_digest


def test_a_record_leg_whose_flush_fails_reports_no_success_and_the_read_back_says_what_landed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leg that fails *after* its bytes landed is reported as "did not report success", not as absent.

    The write owner replaces the destination and then flushes the directory that names it, so the
    record can be on disk while the leg still refuses. A clause reading "was not written" would then
    contradict the very read-back appended beside it -- the half reads `damaged` precisely *because*
    the record landed and names bytes the dataset does not hold -- so the leg says only what it
    measured, and the read-back (with the dataset identity it still holds) carries the outcome.

    This is a rebase, so the record leg runs first: nothing replaced the dataset, and the half keeps the
    pre-rebase bytes while the new record sits beside them naming the replacement.
    """

    contract, half, _private, _published, _published_identity = _adopted_half(
        tmp_path, "record-flush"
    )
    original = _digest(half)
    later = tmp_path / "later.sqlite"
    later_identity = _publish_a_later_line(_private, tmp_path, later, None)
    listed = _one_entry_list(tmp_path, "record-flush", "E-FLUSH", CODE_SYMBOL)
    _fail_the_directory_flush(monkeypatch, half.parent)

    refused = _cli_json(
        [
            *_fork_ingest_argv(
                contract,
                listed,
                tmp_path / "candidate",
                later,
                later_identity.model_dump(mode="json"),
            ),
            "--rebase-baseline",
        ],
        capsys,
    )

    assert refused["reviewBaseline"].startswith("not-placed:"), refused["reviewBaseline"]
    assert "generation record leg did not report success" in refused["reviewBaseline"], refused[
        "reviewBaseline"
    ]
    assert "was not written" not in refused["reviewBaseline"], (
        "the leg claims the record is absent while the record is on disk: "
        f"{refused['reviewBaseline']}"
    )
    assert "the half now reads damaged" in refused["reviewBaseline"], refused["reviewBaseline"]
    # The record IS on disk -- that is exactly why the half reads damaged -- and the dataset still
    # holds the bytes the comparison was opened on.
    record = read_baseline_generation(half.parent)
    assert record is not None and record.generation_index == 2, record
    assert record.logical_digest == later_identity.logical_digest, record
    assert _digest(half) == original, "the refused record leg still replaced the previous baseline"
    identity = _identity_of(half)
    assert (
        f"{identity.repository_id}/{identity.schema_version}/{identity.logical_digest}"
        in refused["reviewBaseline"]
    ), "the read-back does not name the dataset the half still holds"


def test_a_dataset_leg_whose_flush_fails_reports_no_success_while_the_bytes_are_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first placement's dataset leg can land its bytes and still refuse; the line must not deny it.

    Same window, the other leg: an *empty* half takes the dataset first (where a dataset without its
    record is truthfully an adopted baseline), and the flush of the directory that names it fails after
    the rename. The bytes are on disk, so "was not replaced" would be false; the read-back reports the
    half as `adopted` and names the dataset identity it now holds, which is the fact a reader acts on.
    """

    contract, half, _private, published, published_identity = _adopted_half(
        tmp_path, "dataset-flush"
    )
    captured_bytes = published.read_bytes()
    half.unlink()
    assert read_standing_generation(half.parent).state == "absent", "the case needs an empty half"
    listed = _one_entry_list(tmp_path, "dataset-flush", "E-FLUSH", CODE_SYMBOL)
    _fail_the_directory_flush(monkeypatch, half.parent)

    refused = _cli_json(
        _fork_ingest_argv(
            contract,
            listed,
            tmp_path / "candidate",
            published,
            published_identity.model_dump(mode="json"),
        ),
        capsys,
    )

    assert refused["reviewBaseline"].startswith("not-placed:"), refused["reviewBaseline"]
    assert "dataset leg did not report success" in refused["reviewBaseline"], refused[
        "reviewBaseline"
    ]
    assert "was not replaced" not in refused["reviewBaseline"], (
        f"the leg claims the bytes are absent while they are on disk: {refused['reviewBaseline']}"
    )
    assert "the half now reads adopted" in refused["reviewBaseline"], refused["reviewBaseline"]
    assert half.read_bytes() == captured_bytes, (
        "the case no longer exercises a leg that failed after its bytes landed"
    )
    assert read_baseline_generation(half.parent) is None, refused["reviewBaseline"]
    assert published_identity.logical_digest in refused["reviewBaseline"], refused["reviewBaseline"]
