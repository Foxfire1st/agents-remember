"""Each family member's realization sources carry their own recorded locator and resolved range.

The family roster owner reads a family revision's members and projects every realization claim as a
source reference. A reviewer needs the exact region each claim attributes -- two members realized in
one file are two regions with two explanations, not one file -- so every source carries the anchor's
structured recorded locator, the line ranges the read's own anchor resolver placed it on in *this*
side's exact recorded blob, and a state naming which of those facts the side established.

Every value is produced by the production path: claims are authored through the store's own
realization operation, the two sides are two real datasets and two real Git trees, and the roster is
read through :func:`read_family_roster` with the anchor resolver the side's read context selects.
Nothing here parses a ``detail`` sentence; the ranges are compared against the lines the fixture
wrote.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from agents_remember.application.review_family_rosters import (
    RosterContext,
    RosterReadRequest,
    open_family_side,
    read_family_roster,
)
from agents_remember.models.knowledge.graph import RealizationRole
from agents_remember.models.knowledge.review_family_context import ReviewFamilyMemberSource
from agents_remember.models.knowledge.source import (
    FileLocator,
    LineRangeLocator,
    SourceLocator,
    SymbolLocator,
)
from anchor_fixture_models import GitBlobIdentity, RealizationClaimDraft, SourceAnchorDraft
from knowledge_rows_test_support import (
    NewAnchor,
    RealizationClaimRequest,
    open_knowledge_store,
    realizations,
)
from read_scope_test_support import ReadScopeFixture, build_read_scope_fixture

pytestmark = pytest.mark.evidence_unit

SHARED_PATH = "src/shared.py"
# ``alpha`` is lines 1-2 and ``beta`` lines 5-6 in both trees. The after tree appends ``gamma``, so
# the file's blob changes while neither attributed region moves.
BEFORE_TEXT = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"
AFTER_TEXT = BEFORE_TEXT + "\n\ndef gamma():\n    return 3\n"
ALPHA_LINES = (1, 2)
BETA_LINES = (5, 6)
# A recorded range that ends past the six lines ``BEFORE_TEXT`` holds.
PAST_END_LINES = (50, 60)
# A second file, identical in both trees, that defines ``alpha`` twice: at lines 1-2 and 9-10.
DOUBLE_PATH = "src/double.py"
DOUBLE_TEXT = BEFORE_TEXT + "\n\ndef alpha():\n    return 3\n"
DOUBLE_ALPHA_LINES = ((1, 2), (9, 10))

ALPHA_RATIONALE = "alpha is the single entry that spends the shared retry budget."
BETA_RATIONALE = "beta records the batch obligation beside the budget it shares."

# The legacy source fields, whose values must be exactly what the roster published before locators
# were carried.
LEGACY_FIELDS = frozenset(
    {
        "claim_id",
        "invariant_revision_id",
        "role",
        "rationale",
        "path",
        "recorded_source_identity",
        "observed_source_identity",
        "resolution",
        "detail",
    }
)


@dataclass(frozen=True)
class Claim:
    """One realization this module authors: its identity and what it records."""

    claim_id: str
    revision_id: str
    role: RealizationRole
    rationale: str
    locator: SourceLocator
    blob: str
    path: str = SHARED_PATH


@dataclass(frozen=True)
class SourcesFixture:
    """Two datasets, two code trees in one repository, and every claim they record."""

    store: ReadScopeFixture
    code_root: Path
    before_database: Path
    after_database: Path
    before_tree: str
    after_tree: str
    before_blob: str
    after_blob: str
    claims: dict[str, Claim]


@pytest.fixture(scope="module")
def fixture(tmp_path_factory: pytest.TempPathFactory) -> SourcesFixture:
    return build_sources_fixture(tmp_path_factory.mktemp("member-sources"))


def build_sources_fixture(directory: Path) -> SourcesFixture:
    """Author the claims on the before dataset, then copy it and re-attest ``alpha`` on the after one."""

    store = build_read_scope_fixture(directory / "store")
    code_root = directory / "code"
    before_tree, before_blob, double_blob = _commit_tree(code_root, BEFORE_TEXT, initial=True)
    after_tree, after_blob, _ = _commit_tree(code_root, AFTER_TEXT, initial=False)
    subject, batch = store.subject_revision_id, store.batch_revision_id
    before_claims = {
        "alpha": _claim(subject, "enforcement", ALPHA_RATIONALE, _symbol("alpha"), before_blob),
        "beta": _claim(batch, "support", BETA_RATIONALE, _symbol("beta"), before_blob),
        "range": _claim(
            batch, "presentation", "The recorded lines.", _lines(*BETA_LINES), before_blob
        ),
        "file": _claim(subject, "incidental", "The whole file.", FileLocator(), before_blob),
        "unbound": _claim(subject, "support", "A name.", _symbol("missing"), before_blob),
        "past_end": _claim(batch, "support", "Lines.", _lines(*PAST_END_LINES), before_blob),
        "double": Claim(
            str(uuid4()),
            subject,
            "enforcement",
            "Both.",
            _symbol("alpha"),
            double_blob,
            DOUBLE_PATH,
        ),
    }
    _author(store, store.database_path, before_claims.values())
    after_database = directory / "after.db"
    after_database.write_bytes(store.database_path.read_bytes())
    alpha_after = _claim(subject, "enforcement", ALPHA_RATIONALE, _symbol("alpha"), after_blob)
    _author(store, after_database, (alpha_after,))
    return SourcesFixture(
        store=store,
        code_root=code_root,
        before_database=store.database_path,
        after_database=after_database,
        before_tree=before_tree,
        after_tree=after_tree,
        before_blob=before_blob,
        after_blob=after_blob,
        claims={**before_claims, "alpha_after": alpha_after},
    )


def _symbol(name: str) -> SymbolLocator:
    return SymbolLocator(language="python", qualified_name=name)


def _lines(start: int, end: int) -> LineRangeLocator:
    return LineRangeLocator(start_line=start, end_line=end)


def _claim(
    revision_id: str, role: RealizationRole, rationale: str, locator: SourceLocator, blob: str
) -> Claim:
    return Claim(str(uuid4()), revision_id, role, rationale, locator, blob)


def _author(fixture: ReadScopeFixture, database: Path, claims) -> None:
    store = open_knowledge_store(database, fixture.repository_id)
    try:
        for claim in claims:
            created = realizations.create_realization_claim(
                store,
                RealizationClaimRequest(
                    repository_id=fixture.repository_id,
                    claim=RealizationClaimDraft(
                        claim_id=claim.claim_id,
                        invariant_revision_id=claim.revision_id,
                        role=claim.role,
                        rationale=claim.rationale,
                    ),
                    anchor=NewAnchor(
                        anchor=SourceAnchorDraft(
                            anchor_id=UUID(str(uuid4())),
                            path=claim.path,
                            source_identity=GitBlobIdentity(object_id=claim.blob),
                            locator=claim.locator,
                        )
                    ),
                    provenance=fixture.authorship,
                ),
            )
            assert created.state == "created", created.refusal
    finally:
        store.close()


def _commit_tree(root: Path, text: str, *, initial: bool) -> tuple[str, str, str]:
    """Commit ``text`` at the shared path beside the double file; return tree and both blobs."""

    if initial:
        root.mkdir(parents=True)
        _git(root, "init", "-q", "--initial-branch=main")
        _git(root, "config", "user.email", "fixture@example.invalid")
        _git(root, "config", "user.name", "member sources fixture")
    for path, body in ((SHARED_PATH, text), (DOUBLE_PATH, DOUBLE_TEXT)):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "tree")
    return (
        _git(root, "rev-parse", "HEAD^{tree}"),
        _git(root, "rev-parse", f"HEAD:{SHARED_PATH}"),
        _git(root, "rev-parse", f"HEAD:{DOUBLE_PATH}"),
    )


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def roster_sources(
    fixture: SourcesFixture, side_name: str, tree: str | None
) -> dict[str, ReviewFamilyMemberSource]:
    """Read one side's roster of the fixture family and index every carried source by claim."""

    database = fixture.before_database if side_name == "before" else fixture.after_database
    side = open_family_side(
        "before" if side_name == "before" else "after",
        database,
        namespace=fixture.store.repository_id,
        code_root=None if tree is None else fixture.code_root,
        code_tree_id=tree,
    )
    family = fixture.store.family
    try:
        read = read_family_roster(
            side,
            family.family_id,
            RosterContext(
                family_revision_id=family.revision_id, recorded_revision_ids=(family.revision_id,)
            ),
            RosterReadRequest(),
        )
    finally:
        side.close()
    assert read.context.state == "recorded", read.context.detail
    return {source.claim_id: source for member in read.context.members for source in member.sources}


def _ranges(source: ReviewFamilyMemberSource) -> tuple[tuple[int, int], ...]:
    return tuple((line.start_line, line.end_line) for line in source.resolved_ranges)


def test_two_members_in_one_file_carry_their_own_locators_ranges_and_rationale(
    fixture: SourcesFixture,
) -> None:
    """Two members realized in one file are two sources with two regions and two explanations."""

    sources = roster_sources(fixture, "before", fixture.before_tree)
    alpha = sources[fixture.claims["alpha"].claim_id]
    beta = sources[fixture.claims["beta"].claim_id]

    assert alpha.path == beta.path == SHARED_PATH
    assert alpha.invariant_revision_id != beta.invariant_revision_id
    assert (alpha.locator, beta.locator) == (_symbol("alpha"), _symbol("beta"))
    assert (_ranges(alpha), _ranges(beta)) == ((ALPHA_LINES,), (BETA_LINES,))
    assert alpha.locator_state == beta.locator_state == "resolved"
    assert (alpha.role, alpha.rationale) == ("enforcement", ALPHA_RATIONALE)
    assert (beta.role, beta.rationale) == ("support", BETA_RATIONALE)

    # A recorded line range on the exact recorded blob is the recorded range; a file locator is the
    # whole blob and names no range.
    ranged = sources[fixture.claims["range"].claim_id]
    whole = sources[fixture.claims["file"].claim_id]
    assert (ranged.locator_state, _ranges(ranged)) == ("resolved", (BETA_LINES,))
    assert (whole.locator_state, whole.resolved_ranges) == ("whole_file", ())

    # The transport carries the structured values the client decodes.
    wire = alpha.model_dump(mode="json", exclude_none=True)
    assert wire["locator"] == {"kind": "symbol", "language": "python", "qualified_name": "alpha"}
    assert wire["resolved_ranges"] == [{"kind": "line_range", "start_line": 1, "end_line": 2}]
    assert wire["locator_state"] == "resolved"


def test_a_changed_file_keeps_its_unchanged_attributed_range_on_each_side(
    fixture: SourcesFixture,
) -> None:
    """The file's blob moved while the attributed region did not, so each side keeps that region."""

    assert fixture.before_blob != fixture.after_blob
    before = roster_sources(fixture, "before", fixture.before_tree)[
        fixture.claims["alpha"].claim_id
    ]
    after = roster_sources(fixture, "after", fixture.after_tree)[
        fixture.claims["alpha_after"].claim_id
    ]

    assert _ranges(before) == _ranges(after) == (ALPHA_LINES,)
    assert (before.observed_source_identity, after.observed_source_identity) == (
        fixture.before_blob,
        fixture.after_blob,
    )
    assert before.locator == after.locator == _symbol("alpha")


def test_an_unresolved_locator_is_stated_with_its_recorded_locator_and_no_range(
    fixture: SourcesFixture,
) -> None:
    """A locator the side could not place keeps its recorded value, its reason and no guessed range."""

    before = roster_sources(fixture, "before", fixture.before_tree)
    unbound = before[fixture.claims["unbound"].claim_id]
    assert (unbound.locator_state, unbound.resolution) == ("unresolved", "recorded_blob_mismatch")
    assert (unbound.locator, unbound.resolved_ranges) == (_symbol("missing"), ())

    # The after tree holds other bytes than these claims recorded: every one of them is unresolved on
    # that side, carries its own recorded locator, and is never placed on the new bytes.
    after = roster_sources(fixture, "after", fixture.after_tree)
    for name in ("alpha", "beta", "range", "file"):
        stale = after[fixture.claims[name].claim_id]
        assert (stale.locator_state, stale.resolution) == ("unresolved", "recorded_blob_mismatch")
        assert stale.locator == fixture.claims[name].locator
        assert stale.resolved_ranges == ()

    # A read that named no code tree resolves nothing and says so for every source.
    untreed = roster_sources(fixture, "before", None)
    assert {source.resolution for source in untreed.values()} == {"not_requested"}
    assert {source.locator_state for source in untreed.values()} == {"unresolved"}


def test_a_recorded_range_past_the_blob_end_is_unresolved_with_no_range(
    fixture: SourcesFixture,
) -> None:
    """Lines 50-60 of a six-line exact blob are not lines it holds, so no region is published."""

    past_end = roster_sources(fixture, "before", fixture.before_tree)[
        fixture.claims["past_end"].claim_id
    ]
    assert past_end.observed_source_identity == fixture.before_blob
    # The blob-identity fact is unchanged; what is withheld is the region those bytes do not hold.
    assert (past_end.locator_state, past_end.resolution) == ("unresolved", "exact_recorded_blob")
    assert (past_end.locator, past_end.resolved_ranges) == (_lines(*PAST_END_LINES), ())
    assert "hold 6 line(s)" in past_end.detail


def test_a_symbol_defined_twice_carries_every_defining_range(fixture: SourcesFixture) -> None:
    """Every extent the extractor finds defining the name is carried, in order, not the first."""

    double = roster_sources(fixture, "before", fixture.before_tree)[
        fixture.claims["double"].claim_id
    ]
    assert (double.path, double.locator_state) == (DOUBLE_PATH, "resolved")
    assert _ranges(double) == DOUBLE_ALPHA_LINES


def test_the_existing_source_fields_keep_their_published_values(fixture: SourcesFixture) -> None:
    """Carrying the locator adds fields; every field the roster already published is unchanged."""

    alpha = roster_sources(fixture, "before", fixture.before_tree)[fixture.claims["alpha"].claim_id]
    wire = alpha.model_dump(mode="json", exclude_none=True)

    assert set(wire) == LEGACY_FIELDS | {"locator", "resolved_ranges", "locator_state"}
    assert {field: wire[field] for field in LEGACY_FIELDS} == {
        "claim_id": fixture.claims["alpha"].claim_id,
        "invariant_revision_id": fixture.store.subject_revision_id,
        "role": "enforcement",
        "rationale": ALPHA_RATIONALE,
        "path": SHARED_PATH,
        "recorded_source_identity": fixture.before_blob,
        "observed_source_identity": fixture.before_blob,
        "resolution": "exact_recorded_blob",
        "detail": (
            f"the author recorded this realization at {SHARED_PATH} (exact_recorded_blob): the "
            "requested tree holds the exact recorded blob at the recorded path, and those bytes "
            f"define 'alpha' at {SHARED_PATH}:1-2"
        ),
    }
