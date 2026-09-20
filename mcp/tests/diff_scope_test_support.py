"""The curator-stage diff fixture: one baseline snapshot and one curator-updated candidate.

The comparison is decided by a topology no single-snapshot fixture can carry, because half of its
obligations are statements about what *changed between two snapshots* rather than about what one
snapshot holds. This module therefore builds the same identity topology **twice** -- the baseline and
the candidate -- through the public store operations, and the candidate carries exactly one authored
change of each kind the packet distinguishes:

* **a revised statement, and the two shapes it produces** -- the candidate authors a successor of the
  retry invariant's subject revision with a different statement, and attributes a realization to it at
  ``src/retry_interval.py``. *Measured, and it is the substrate's own rule rather than a choice this
  fixture makes:* a second revision with the same identity and another sealed payload is refused
  ``duplicate_identity`` ("Keep the stored revision intact and author a successor with its own
  identity and this revision as an exact predecessor"), so a revised statement is necessarily an
  old/new *pair* of immutable revision payloads. The comparison reports that pair as
  ``superseded``/``superseding`` -- derived from the shared record identity and the two exact
  revisions, never from a label or an insertion order -- so a revised statement is not rendered as a
  deletion plus an unrelated addition. The successor's own realization gives the pair the attributed
  code the packet requires on both sides.
* **a source change with no record change** -- the two claims at ``src/batch.py`` are selected by both
  snapshots with identical claim rows, and the candidate tree holds different bytes at that path than
  the baseline tree does. The recorded anchor did not move; the bytes at the recorded path are simply
  not the recorded ones any more.
* **a removed realization** -- the claim at ``src/synchronization.py`` exists only in the baseline.
  Its anchor row stays recorded, so its before-side source context is still observable -- the packet's
  own non-conforming example (deleting a link must not erase the earlier code) made measurable.
* **an added realization** -- the claim at ``src/retry_interval.py`` exists only in the candidate.
* **unchanged siblings on both sides** -- the family the retry obligation directly belongs to holds a
  *sibling* invariant, and both of that sibling's claims are selected by both snapshots with identical
  claim rows. The rewritten code path is one of that sibling's two locations, so those two items are
  unchanged *as records* while their source observation moved -- the pair of statements the response
  has to keep apart, measured on one item each way.
* **a path only one side's selection reaches** -- ``src/timeout.py`` is recorded in the candidate
  snapshot and the candidate's selection never reaches its claim: the
  "present-but-outside-the-selected-scope is not deletion" state.
* **an added code path with no recorded attribution** -- ``src/unmapped.py`` appears in the candidate
  tree and in no claim: the packet's visible unattributed gap.
* **a record the candidate holds and neither side selects** -- a fourth retry revision is recorded in
  the candidate snapshot, carries no claim, and is selected by neither side, so the comparison reports
  it as present-outside-the-selection rather than pretending it does not exist. Its successor is a
  revision the baseline holds, which is what makes it a *divergent* retained revision.

The baseline is the shared read-scope fixture, so the two leaves cannot disagree about which records
the R07 stopping rule selects. Every identity a case asserts against is a named field of
:class:`DiffFixture`, and the identities the candidate adds are drawn once in the builder.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from agents_remember.memory.knowledge import realizations
from agents_remember.memory.knowledge.records import (
    claim_row_digest,
    decode_authorship,
    stored_realization_role,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore, open_knowledge_store
from agents_remember.models.knowledge.graph import RealizationClaimDraft, RealizationRole
from agents_remember.models.knowledge.result import (
    NewAnchor,
    RealizationClaimRequest,
    RemoveRealizationClaimRequest,
    RevisionDraft,
    RevisionRequest,
)
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    SourceAnchorDraft,
)
from read_scope_test_support import (
    APPLICABILITY,
    AUXILIARY_PATH,
    BASE_LABEL,
    BATCH_PATH,
    CONDITIONS,
    EXCLUSIONS,
    INTEGRATION_PATH,
    MISMATCH_PATH,
    RESOLUTION_PATH,
    SYNCHRONIZATION_PATH,
    UNPARSED_PATH,
    ReadScopeFixture,
    build_read_scope_fixture,
)

# The candidate's two new source paths. They exist only to carry the added realization and the
# unattributed change; the baseline tree holds neither.
SUCCESSOR_PATH = "src/retry_interval.py"
UNMAPPED_PATH = "src/unmapped.py"

# The candidate's revised statement for the retry invariant, authored as a successor revision of the
# subject revision. It differs from the baseline's text in one clause, which is what makes the pair a
# *statement* revision rather than a whole-record replacement.
RETRY_REVISED_STATEMENT = (
    "Retries share one budget across integration and synchronization and record the first refusal."
)
RETRY_REVISED_APPLICABILITY = "Every retry the shared budget admits in this repository namespace."
RETRY_FIRST_REFUSAL_CONDITION = "The first refusal is recorded before the budget is re-armed."

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
    (INTEGRATION_PATH, "# integration\nshared retry budget\n"),
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
    unselected_revision_id: str

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
    unselected_revision_id = str(uuid4())
    revised_revision_id = str(uuid4())
    after = _build_candidate(
        directory,
        baseline,
        added=added,
        unselected_revision_id=unselected_revision_id,
        revised_revision_id=revised_revision_id,
    )
    return DiffFixture(
        repository_id=baseline.repository_id,
        before=before,
        after=after,
        retry_invariant_id=baseline.retry_invariant_id,
        subject_revision_id=baseline.subject_revision_id,
        revised_revision_id=revised_revision_id,
        batch_revision_id=baseline.batch_revision_id,
        unselected_revision_id=unselected_revision_id,
        moved_claim_id=baseline.integration.claim_id,
        unchanged_claim_ids=(
            baseline.batch_primary.claim_id,
            baseline.batch_secondary.claim_id,
        ),
        sibling_invariant_id=baseline.batch_invariant_id,
        removed_claim_id=baseline.synchronization.claim_id,
        added_claim_id=added.claim_id,
        outside_selection_claim_id=baseline.mismatch_anchor.claim_id,
        baseline_only_path=SYNCHRONIZATION_PATH,
        candidate_only_path=SUCCESSOR_PATH,
        unattributed_path=UNMAPPED_PATH,
        changed_source_path=BATCH_PATH,
        unchanged_source_path=AUXILIARY_PATH,
    )


def _build_candidate(
    directory: Path,
    baseline: ReadScopeFixture,
    *,
    added: AddedRealization,
    unselected_revision_id: str,
    revised_revision_id: str,
) -> DiffSide:
    """Author the candidate database and its code tree, with the fixture's named transitions."""

    candidate_dir = directory / "candidate"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    database_path = candidate_dir / baseline.database_path.name
    code_root = directory / "candidate-code"
    blobs = _write_candidate_tree(code_root, baseline.git_root)
    # The comparison names both trees in one repository, so the baseline repository -- the side the
    # probe is addressed at -- borrows the candidate's objects. Without this a probe rooted at the
    # baseline can resolve only one of the two named trees and would have to report the pair as
    # unobservable, which is a fact about the fixture and not about the comparison.
    _borrow_objects(baseline.git_root, code_root)
    # The candidate starts as the same dataset as the baseline: a curator's starting point is the
    # baseline's records, and copying the closed baseline file is what makes that literal rather than
    # re-authored (and therefore possibly different).
    database_path.write_bytes(baseline.database_path.read_bytes())
    store = open_knowledge_store(database_path, baseline.repository_id)
    try:
        _author_revised_revision(store, baseline, revised_revision_id)
        _add_unselected_revision(store, baseline, unselected_revision_id)
        _move_retry_claim(store, baseline, revised_revision_id)
        _remove_retired_claim(store, baseline)
        _add_successor_claim(
            store,
            baseline,
            added=added,
            revised_revision_id=revised_revision_id,
            recorded_blob=blobs[SUCCESSOR_PATH],
        )
    finally:
        store.close()
    return DiffSide(
        fixture=baseline,
        database_path=database_path,
        git_root=code_root,
        git_tree_id=_git(code_root, ["rev-parse", "HEAD^{tree}"]),
        git_blobs=blobs,
    )


def _author_revised_revision(
    store: OpenedKnowledgeStore, baseline: ReadScopeFixture, revision_id: str
) -> None:
    """Author the candidate's revised statement as a successor of the baseline's subject revision.

    This is the substrate's only shape for a revised statement, and the store says so itself: the
    baseline revision keeps its identity and its payload, the successor carries its own identity and
    names the revision it supersedes as its exact predecessor, and both remain selectable. A
    comparison therefore has to pair them by the record identity they share, which is what
    ``superseded``/``superseding`` report.
    """

    created = store.create_revision(
        RevisionRequest(
            repository_id=baseline.repository_id,
            revision=RevisionDraft(
                revision_id=revision_id,
                invariant_id=baseline.retry_invariant_id,
                display_version=BASE_LABEL,
                statement=RETRY_REVISED_STATEMENT,
                applicability=RETRY_REVISED_APPLICABILITY,
                conditions=(*CONDITIONS, RETRY_FIRST_REFUSAL_CONDITION),
                exclusions=EXCLUSIONS,
                predecessors=(baseline.subject_revision_id,),
                provenance=baseline.authorship,
            ),
        )
    )
    _require("author_revised_revision", created.state, created.refusal)


def _move_retry_claim(
    store: OpenedKnowledgeStore, baseline: ReadScopeFixture, revised_revision_id: str
) -> None:
    """Move the baseline's realization of the retry obligation onto the successor revision.

    A realization claim cannot be rewritten in place either -- the schema's own trigger refuses it
    ("a realization claim cannot be rewritten in place", measured) -- so the candidate's revision of
    the *relationship* is its removal plus the authoring of the successor's own claim. The removed
    relationship's anchor row stays recorded, which is what keeps the before-side source observable
    after the link is gone.
    """

    removed = realizations.remove_realization_claim(
        store,
        RemoveRealizationClaimRequest(
            repository_id=baseline.repository_id,
            claim_id=baseline.integration.claim_id,
            expected_row_digest=_claim_row_digest(store, baseline.integration.claim_id),
        ),
    )
    _require("move_retry_claim", removed.state, removed.refusal, expected="removed")


def _add_unselected_revision(
    store: OpenedKnowledgeStore, baseline: ReadScopeFixture, revision_id: str
) -> None:
    """Author a fourth retry revision that neither side's selection reaches and no claim cites."""

    created = store.create_revision(
        RevisionRequest(
            repository_id=baseline.repository_id,
            revision=RevisionDraft(
                revision_id=revision_id,
                invariant_id=baseline.retry_invariant_id,
                display_version=BASE_LABEL,
                statement="Retries are recorded once per admitted candidate write.",
                applicability=APPLICABILITY,
                conditions=CONDITIONS,
                exclusions=EXCLUSIONS,
                predecessors=(baseline.subject_revision_id,),
                provenance=baseline.authorship,
            ),
        )
    )
    _require("add_unselected_revision", created.state, created.refusal)


def _remove_retired_claim(store: OpenedKnowledgeStore, baseline: ReadScopeFixture) -> None:
    """Remove the baseline's claim at the retired path, leaving its anchor row recorded.

    Only the *relationship* is removed. The anchor row stays, which is what lets the comparison show
    the removed item's before-side source: this fixture removes the link and not the location, and the
    packet's non-conforming example is exactly the case where losing the link loses the earlier code.
    """

    removed = realizations.remove_realization_claim(
        store,
        RemoveRealizationClaimRequest(
            repository_id=baseline.repository_id,
            claim_id=baseline.synchronization.claim_id,
            expected_row_digest=_claim_row_digest(store, baseline.synchronization.claim_id),
        ),
    )
    _require("remove_retired_claim", removed.state, removed.refusal, expected="removed")


def _add_successor_claim(
    store: OpenedKnowledgeStore,
    baseline: ReadScopeFixture,
    *,
    added: AddedRealization,
    revised_revision_id: str,
    recorded_blob: str,
) -> None:
    """Add the candidate-only realization, at a path the baseline records no claim at.

    The recorded identity is the blob the candidate tree *really holds* at that path, read from the
    tree this fixture just wrote. That is not decoration: it is the difference between a claim whose
    observation is the exact recorded blob and one whose observation is a mismatch, and the case that
    reads it asserts which of the two the candidate's own attribution earns.
    """

    created = realizations.create_realization_claim(
        store,
        RealizationClaimRequest(
            repository_id=baseline.repository_id,
            claim=RealizationClaimDraft(
                claim_id=added.claim_id,
                invariant_revision_id=revised_revision_id,
                role=SUCCESSOR_ROLE,
                rationale=SUCCESSOR_RATIONALE,
            ),
            anchor=NewAnchor(
                anchor=SourceAnchorDraft(
                    anchor_id=UUID(added.anchor_id),
                    path=SUCCESSOR_PATH,
                    source_identity=GitBlobIdentity(object_id=recorded_blob),
                    locator=FileLocator(),
                )
            ),
            provenance=baseline.authorship,
        ),
    )
    _require("add_successor_claim", created.state, created.refusal)


def _claim_row_digest(store: OpenedKnowledgeStore, claim_id: str) -> str:
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
