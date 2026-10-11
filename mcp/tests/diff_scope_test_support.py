"""The curator-stage diff fixture: one baseline tree and one curator-updated candidate tree.

The comparison is decided by a topology no single-snapshot fixture can carry, because half of its
obligations are statements about what *changed between two snapshots* rather than about what one
snapshot holds. This module therefore builds the same identity topology **twice** -- the baseline
and the candidate -- as two text trees through the rows builder, and the candidate carries exactly
one authored change of each kind the packet distinguishes. Both trees mint the same text IDs in
the same order, so the rows neither side changed project the same UUIDs on both sides and pair as
themselves, while every changed row projects a new identity or a new side:

* **a revised statement, and the two shapes it produces** -- the candidate's retry record carries a
  different statement at another revision number of the same text ID. Text records are numbered
  revisions of one file and hold no predecessor edge (MIK-R74 rule 1), so the comparison reports
  the pair as a removal beside an unrelated addition -- never as a succession -- with each side
  present outside the other's selection. The candidate's integration claim keeps citing the retry
  record, so it pairs by its own row and displays as moved, with the revised revision beside it;
* **a source change with no record change** -- the two claims at ``src/batch.py`` are selected by
  both snapshots with identical claim rows, and the candidate tree holds different bytes at that
  path than the baseline tree does. The recorded anchor did not move; the bytes at the recorded
  path are simply not the recorded ones any more;
* **a removed realization** -- the claim at ``src/synchronization.py`` exists only in the baseline.
  Its entry is gone from the candidate tree, so its before-side source context is still observable
  from the baseline's own rows -- the packet's own non-conforming example (deleting a link must
  not erase the earlier code) made measurable;
* **an added realization** -- the claim at ``src/retry_interval.py`` exists only in the candidate,
  citing the revised revision;
* **unchanged siblings on both sides** -- the family the retry obligation directly belongs to holds
  a *sibling* invariant, and both of that sibling's claims are selected by both snapshots with
  identical claim rows. The rewritten code path is one of that sibling's two locations, so those
  two items are unchanged *as records* while their source observation moved -- the pair of
  statements the response has to keep apart, measured on one item each way;
* **a path only one side's selection reaches** -- ``src/timeout.py`` is recorded in the candidate
  snapshot and the candidate's selection never reaches its claim: the
  "present-but-outside-the-selected-scope is not deletion" state;
* **an added code path with no recorded attribution** -- ``src/unmapped.py`` appears in the
  candidate tree and in no claim: the packet's visible unattributed gap.

A fourth retry revision that neither side selects is gone with the multi-revision datasets: a text
record holds exactly one revision, so a revision no tree holds is not a shape a tree can express,
and the case that needed it is deleted and named in the leaf's report. The baseline is the shared
read-scope fixture, so the two leaves cannot disagree about which records the R07 stopping rule
selects. Every identity a case asserts against is a named field of :class:`DiffFixture`.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.records import decode_authorship
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.source import FileLocator
from anchor_fixture_models import RealizationClaimDraft
from knowledge_row_codec_test_support import claim_row_digest, stored_realization_role
from knowledge_rows_test_support import (
    RowStore,
)
from read_scope_test_support import (
    AUXILIARY_PATH,
    BASE_LABEL,
    BATCH_PATH,
    CONDITIONS,
    INTEGRATION_FILE_TEXT,
    INTEGRATION_PATH,
    MISMATCH_PATH,
    RESOLUTION_PATH,
    SUBJECT_REVISION,
    SYNCHRONIZATION_PATH,
    UNPARSED_PATH,
    ExtraFamilySeed,
    ExtraInvariantSeed,
    ReadFixtureRealization,
    ReadScopeFixture,
    RealizationSeed,
    build_read_scope_fixture,
)

# The candidate's two new source paths. They exist only to carry the added realization and the
# unattributed change; the baseline tree holds neither.
SUCCESSOR_PATH = "src/retry_interval.py"
UNMAPPED_PATH = "src/unmapped.py"

RETRY_REVISED_STATEMENT = (
    "Retries share one budget across integration and synchronization and record the first refusal."
)
RETRY_REVISED_APPLICABILITY = "Every retry the shared budget admits in this repository namespace."
RETRY_FIRST_REFUSAL_CONDITION = "The first refusal is recorded before the budget is re-armed."

# The candidate's revised retry record, shared by every after-tree a comparison rebuilds: the same
# topology with a revised statement, so rows neither side changed project the same UUIDs.
REVISED_RETRY_TEXT: tuple[str, str, tuple[str, ...]] = (
    BASE_LABEL,
    RETRY_REVISED_STATEMENT,
    (*CONDITIONS, RETRY_FIRST_REFUSAL_CONDITION),
)


def rebuild_after_tree(  # noqa: PLR0913 -- one call carries a whole candidate-side delta
    directory: Path,
    *,
    omit_claim_paths: tuple[str, ...] = (),
    extra_claims: tuple[RealizationSeed, ...] = (),
    direct_family_revision_number: int = 1,
    omit_members: tuple[tuple[str, str], ...] = (),
    direct_family_guarantee: str | None = None,
    extra_families: tuple[ExtraFamilySeed, ...] = (),
    extra_invariants: tuple[ExtraInvariantSeed, ...] = (),
    add_members: tuple[tuple[str, str], ...] = (),
    database_name: str = "knowledge-candidate.db",
) -> ReadScopeFixture:
    """Build one candidate-side tree: the revised retry record with the caller's deltas.

    The same builder mints the same text IDs in the same order as the baseline, so every row
    neither side changed projects the same UUID on both sides.
    """

    directory.mkdir(parents=True, exist_ok=True)
    return build_read_scope_fixture(
        directory,
        database_name=database_name,
        retry_text=REVISED_RETRY_TEXT,
        omit_claim_paths=omit_claim_paths,
        extra_claims=extra_claims,
        direct_family_revision_number=direct_family_revision_number,
        omit_members=omit_members,
        direct_family_guarantee=direct_family_guarantee,
        extra_families=extra_families,
        extra_invariants=extra_invariants,
        add_members=add_members,
    )


def replace_after_tree(endpoints: Any, after: ReadScopeFixture) -> Any:
    """Point one enclosure's candidate side at a rebuilt tree and re-place its datasets.

    Typed loosely to keep this module importable from the enclosure module without a cycle:
    the enclosure imports this module, so the import back happens inside the call.
    """

    from test_knowledge_review_source_endpoints import _place_datasets  # noqa: PLC0415

    new_after = dataclass_replace(
        endpoints.diff.after, fixture=after, database_path=after.database_path
    )
    new_diff = dataclass_replace(endpoints.diff, after=new_after)
    placed = dataclass_replace(endpoints, diff=new_diff)
    _place_datasets(new_diff, endpoints.contract)
    return placed

# The candidate-only realization's authored words, so the added relationship is distinguishable from
# the removed one rather than a copy of it.
SUCCESSOR_RATIONALE = "The candidate records where the revised obligation is realized."
SUCCESSOR_ROLE: RealizationRole = "enforcement"

# The candidate tree's content for the one path whose bytes move. It is a different body at the same
# path for the same recorded claims, which is what makes "a source-only change" measurable.
BATCH_PATH_CANDIDATE_TEXT = "# batch\napplied by a scheduled sweep\n"

# The candidate tree's whole content, as one table, so the two trees differ in exactly the named ways:
# ``src/batch.py`` rewrites, ``src/synchronization.py`` is gone, and the two candidate-only paths
# appear. Every other body is the baseline's own text, byte for byte, so every other anchor observes
# the same blob on both sides.
_CANDIDATE_TREE_TEXT: tuple[tuple[str, str], ...] = (
    (INTEGRATION_PATH, INTEGRATION_FILE_TEXT),
    (BATCH_PATH, BATCH_PATH_CANDIDATE_TEXT),
    (RESOLUTION_PATH, "# resolution\n"),
    (AUXILIARY_PATH, "# anchors\n"),
    (MISMATCH_PATH, "# timeout\nchanged bytes\n"),
    (SUCCESSOR_PATH, "# retry interval\nfirst refusal\n"),
    (UNMAPPED_PATH, "# unmapped\nthis file carries no recorded realization\n"),
    # Present, byte for byte, in the baseline too: the read-scope fixture's unparsed-language file is
    # part of the shared baseline tree, and a candidate that omitted it would read as a deletion of a
    # path no recorded realization attributes -- which is ``UNMAPPED_PATH``'s measurement, not this
    # one. Holding the same bytes on both sides keeps the two trees differing in exactly the named
    # ways.
    (UNPARSED_PATH, "-- schema\nCREATE TABLE budget (attempts INTEGER);\n"),
)


@dataclass(frozen=True)
class AddedRealization:
    """The identities one candidate-only realization needs: its claim and its anchor."""

    claim_id: str
    anchor_id: str


@dataclass(frozen=True)
class DiffSide:
    """One side of the comparison: its fixture, its database file and its code tree."""

    fixture: ReadScopeFixture
    database_path: Path
    git_root: Path
    git_tree_id: str
    git_blobs: dict[str, str]


@dataclass(frozen=True)
class DiffFixture:
    """Two snapshots of one topology, with the exact transitions between them named."""

    repository_id: str
    before: DiffSide
    after: DiffSide

    retry_invariant_id: str
    subject_revision_id: str
    revised_revision_id: str
    batch_revision_id: str

    # The realization the candidate moved onto the revised revision, and the sibling invariant's two
    # claims, which both snapshots select identically.
    moved_claim_id: str
    unchanged_claim_ids: tuple[str, ...]
    sibling_invariant_id: str
    removed_claim_id: str
    added_claim_id: str
    outside_selection_claim_id: str

    baseline_only_path: str
    candidate_only_path: str
    unattributed_path: str
    changed_source_path: str
    unchanged_source_path: str

    @property
    def before_tree_id(self) -> str:
        """The baseline code tree the comparison resolves the before side's anchors against."""

        return self.before.git_tree_id

    @property
    def after_tree_id(self) -> str:
        """The candidate code tree the comparison resolves the after side's anchors against."""

        return self.after.git_tree_id


def build_diff_fixture(directory: Path) -> DiffFixture:
    """Build the baseline and the candidate, and return every identity the cases assert against."""

    baseline = build_read_scope_fixture(directory / "baseline")
    before = DiffSide(
        fixture=baseline,
        database_path=baseline.database_path,
        git_root=baseline.git_root,
        git_tree_id=baseline.git_tree_id,
        git_blobs=dict(baseline.git_blobs),
    )
    added = AddedRealization(claim_id=str(uuid4()), anchor_id=str(uuid4()))
    after = _build_candidate(directory, baseline, added=added)
    return DiffFixture(
        repository_id=baseline.repository_id,
        before=before,
        after=after,
        retry_invariant_id=baseline.retry_invariant_id,
        subject_revision_id=baseline.subject_revision_id,
        revised_revision_id=after.fixture.subject_revision_id,
        batch_revision_id=baseline.batch_revision_id,
        moved_claim_id=baseline.integration.claim_id,
        unchanged_claim_ids=(
            baseline.batch_primary.claim_id,
            baseline.batch_secondary.claim_id,
        ),
        sibling_invariant_id=baseline.batch_invariant_id,
        removed_claim_id=baseline.synchronization.claim_id,
        added_claim_id=_claim_id_for_path(after.database_path, SUCCESSOR_PATH),
        outside_selection_claim_id=baseline.mismatch_anchor.claim_id,
        baseline_only_path=SYNCHRONIZATION_PATH,
        candidate_only_path=SUCCESSOR_PATH,
        unattributed_path=UNMAPPED_PATH,
        changed_source_path=BATCH_PATH,
        unchanged_source_path=AUXILIARY_PATH,
    )


def _claim_id_for_path(database_path: Path, path: str) -> str:
    """Read one claim's projected identity back from the built index, by its recorded path."""

    connection = open_read_only_database(database_path)
    try:
        row = next(
            iter(
                connection.execute(
                    "SELECT c.claim_id FROM realization_claim c JOIN source_anchor a "
                    "ON a.anchor_id = c.anchor_id WHERE a.path = ?",
                    (path,),
                )
            ),
            None,
        )
    finally:
        connection.close()
    if row is None:
        raise AssertionError(f"the candidate tree holds no claim at {path}")
    return str(row[0])


def _build_candidate(
    directory: Path,
    baseline: ReadScopeFixture,
    *,
    added: AddedRealization,
) -> DiffSide:
    """Author the candidate tree and its code tree, with the fixture's named transitions.

    The candidate is the same topology with a revised retry statement, without the retired
    claim, and with the successor claim: the same builder mints the same text IDs in the same
    order, so every row neither side changed projects the same UUID on both sides.
    """

    candidate_dir = directory / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    code_root = directory / "candidate-code"
    blobs = _write_candidate_tree(code_root, baseline.git_root)
    # The comparison names both trees in one repository, so the baseline repository -- the side the
    # probe is addressed at -- borrows the candidate's objects. Without this a probe rooted at the
    # baseline can resolve only one of the two named trees and would have to report the pair as
    # unobservable, which is a fact about the fixture and not about the comparison.
    _borrow_objects(baseline.git_root, code_root)
    candidate = build_read_scope_fixture(
        candidate_dir,
        retry_text=REVISED_RETRY_TEXT,
        omit_claim_paths=(SYNCHRONIZATION_PATH,),
        extra_claims=(
            RealizationSeed(
                realization=ReadFixtureRealization(
                    anchor_id=added.anchor_id, claim_id=added.claim_id, path=SUCCESSOR_PATH
                ),
                revision_id=SUBJECT_REVISION,
                role=SUCCESSOR_ROLE,
                locator=FileLocator(),
                recorded_blob=blobs[SUCCESSOR_PATH],
            ),
        ),
    )
    return DiffSide(
        fixture=candidate,
        database_path=candidate.database_path,
        git_root=code_root,
        git_tree_id=_git(code_root, ["rev-parse", "HEAD^{tree}"]),
        git_blobs=blobs,
    )


def _claim_row_digest(store: RowStore, claim_id: str) -> str:
    """Return one claim's row digest, recomputed from the row the store actually holds.

    A claim has no stored ``row_digest`` column -- the digest is derived over the row's own values by
    :func:`agents_remember.memory.knowledge.records.claim_row_digest`, which is the same function the
    write path used to seal it. The fixture therefore reads the row and re-seals it through that one
    owner, rather than restating the digest's composition here where it could drift from the
    production rule.
    """

    row = next(
        iter(
            store.connection.execute(
                "SELECT claim_id, invariant_revision_id, anchor_id, role, rationale, provenance "
                "FROM realization_claim WHERE repository_id = ? AND claim_id = ?",
                (store.repository_id, claim_id),
            )
        ),
        None,
    )
    if row is None:  # pragma: no cover - a fixture identity that does not exist
        raise AssertionError(f"the fixture store holds no claim {claim_id}")
    return claim_row_digest(
        store.repository_id,
        RealizationClaimDraft(
            claim_id=str(row[0]),
            invariant_revision_id=str(row[1]),
            # The stored role is read back through the vocabulary's own decoder, so the draft this
            # re-seals carries the role the row holds rather than a bare string the type does not
            # admit.
            role=stored_realization_role(str(row[3])),
            rationale=str(row[4]),
        ),
        str(row[2]),
        decode_authorship(str(row[5])),
    )


def _write_candidate_tree(root: Path, baseline_root: Path) -> dict[str, str]:
    """Write the candidate's code tree and return ``path -> blob`` for every path it holds.

    The candidate repository borrows the baseline's object store through ``objects/info/alternates``,
    so the comparison can name both trees in **one** repository. That is not a fixture convenience: a
    caller comparing two snapshots runs one command in one repository, which is exactly what the
    response's expansion command states, and a probe in a repository that could not resolve one of the
    two named trees would have to report "unavailable" for a pair that is perfectly comparable.
    """

    root.mkdir(parents=True, exist_ok=True)
    _git(root, ["init", "-q", "--initial-branch=main"])
    _git(root, ["config", "user.email", "fixture@example.invalid"])
    _git(root, ["config", "user.name", "diff fixture"])
    _borrow_objects(root, baseline_root)
    for path, text in _CANDIDATE_TREE_TEXT:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(root, ["add", "-A"])
    _git(root, ["commit", "-q", "-m", "candidate tree"])
    return {path: _git(root, ["rev-parse", f"HEAD:{path}"]) for path, _text in _CANDIDATE_TREE_TEXT}


def _borrow_objects(root: Path, baseline_root: Path) -> None:
    """Point one fixture repository's object store at another's, so one repository holds both trees."""

    alternates = root / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(str(baseline_root / ".git" / "objects") + "\n", encoding="utf-8")


def independent_changed_records(root: Path, before: str, after: str) -> dict[str, str]:
    """The pair's changed paths and Git status letters, read independently and byte-safely.

    ``--name-status -z`` is a *different* Git question from the inventory's own ``--raw -z``, so
    agreement between the two is an observation about the two trees rather than a restatement of one
    implementation. It is read through the production runner for the reason this helper exists at
    all: a pathname is bytes, and a name that is not valid UTF-8 makes strict decoding raise -- the
    fixture helper below is deliberately strict, and this observation deliberately is not.
    """

    result = run_git(root, ["diff", "--name-status", "-z", "--no-renames", before, after])
    if result.returncode != 0:
        raise AssertionError(f"independent Git observation failed: {result.stderr.strip()}")
    fields = [field for field in result.stdout.split("\0") if field]
    return {fields[index + 1]: fields[index][0] for index in range(0, len(fields) - 1, 2)}


def _git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _require(step: str, state: str, refusal_value: object, *, expected: str = "created") -> None:
    """Fail loudly when a fixture step does not do what it says: a fixture is not a probe."""

    if state != expected:
        raise AssertionError(
            f"diff fixture step {step} returned {state!r} instead of {expected!r}: "
            f"{refusal_value!r}"
        )
