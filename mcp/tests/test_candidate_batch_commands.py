"""Focused behaviour of the candidate command union and its batch preconditions.

Each case protects one consequential operation or failure: every command kind the union declares,
the receipt that reports what was written, a no-op that must leave no trace, an expectation that
must match, and the payload shapes the closed union refuses at its own boundary. Constructor
validation is not re-tested here; what is tested is that the union cannot express something the
operation must not do.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import get_args
from uuid import UUID, uuid4

import pytest
from agents_remember.memory.knowledge import anchors, families, memberships, realizations
from agents_remember.models.knowledge.candidate import (
    AddFamily,
    AddFamilyMember,
    AddFamilyRevision,
    AddInvariant,
    AddInvariantRevision,
    AddRealizationClaim,
    AddSourceAnchor,
    AnchorReference,
    ChangeBatch,
    ExpectedRecord,
    MutationResult,
    ProposedCommand,
    RemoveFamilyMember,
    RemoveRealizationClaim,
    RemoveSourceAnchor,
    SetFamilyLabel,
    SetInvariantLabel,
)
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft, RealizationClaimDraft
from agents_remember.models.knowledge.result import RevisionDraft
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    SourceAnchorDraft,
)
from candidate_batch_test_support import (
    CandidateHarness,
    CommandSeeds,
    build_candidate_harness,
)

pytestmark = pytest.mark.evidence_unit

# One authored blob identity, stable for the case's recorded path. The anchor cases never resolve
# it: storage records what the author attributed and never consults a snapshot.
BLOB_IDENTITY = "c" * 40


@pytest.fixture
def candidate(tmp_path: Path) -> CandidateHarness:
    return build_candidate_harness(tmp_path / "candidate")


def revision_draft(
    harness: CandidateHarness,
    *,
    invariant_id: str,
    revision_id: str,
    predecessors: tuple[str, ...] = (),
    statement: str = "An authored candidate statement about atomic writes.",
) -> RevisionDraft:
    """One invariant revision draft carrying the harness's own provenance envelope."""

    return RevisionDraft(
        revision_id=revision_id,
        invariant_id=invariant_id,
        display_version="v1",
        statement=statement,
        applicability="Every admitted candidate write in this namespace.",
        conditions=("The batch is admitted for this namespace.",),
        exclusions=("A stored revision is never rewritten.",),
        predecessors=predecessors,
        provenance=harness.authorship,
    )


def family_revision_draft(
    harness: CandidateHarness, *, family_id: str, revision_id: str
) -> FamilyRevisionDraft:
    return FamilyRevisionDraft(
        family_id=family_id,
        revision_id=revision_id,
        display_version="v1",
        joint_guarantee="The authored obligations hold together under one admitted batch.",
        provenance=harness.authorship,
    )


def anchor_draft(anchor_id: UUID, *, path: str = "src/candidate_batch.py") -> SourceAnchorDraft:
    return SourceAnchorDraft(
        anchor_id=anchor_id,
        path=path,
        source_identity=GitBlobIdentity(object_id=BLOB_IDENTITY),
        locator=FileLocator(),
    )


def claim_draft(*, claim_id: str, revision_id: str) -> RealizationClaimDraft:
    return RealizationClaimDraft(
        claim_id=claim_id,
        invariant_revision_id=revision_id,
        role="enforcement",
        rationale="The batch boundary refuses a partially applied change.",
    )


def test_every_declared_command_is_applied_and_read_back(
    candidate: CandidateHarness,
) -> None:
    """Every command kind the union declares is reachable, and each effect is readable after.

    This is the union's own coverage: if a command kind lost its apply step, or wrote a row that a
    reader cannot see, one of the two batches below would not come back as the identity it authored.
    The count is deliberately not restated here either -- the union's own annotation is its
    membership, and a case that pinned a number would have to be edited by every widening (D-40).
    """

    seeds = CommandSeeds()
    first = candidate.apply(
        candidate.batch(
            AddInvariant(invariant_id=seeds.invariant_id, display_label="first invariant"),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=seeds.invariant_id,
                    revision_id=seeds.revision_id,
                )
            ),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=seeds.invariant_id,
                    revision_id=seeds.successor_id,
                    predecessors=(seeds.revision_id,),
                    statement="A successor that names its exact predecessor.",
                )
            ),
            AddFamily(family_id=seeds.family_id, display_label="first family"),
            AddFamilyRevision(
                revision=family_revision_draft(
                    candidate, family_id=seeds.family_id, revision_id=seeds.family_revision_id
                )
            ),
            AddFamilyMember(
                member=FamilyMemberDraft(
                    member_id=seeds.member_id,
                    family_revision_id=seeds.family_revision_id,
                    invariant_revision_id=seeds.revision_id,
                    provenance=candidate.authorship,
                )
            ),
            AddSourceAnchor(anchor=anchor_draft(seeds.anchor_id)),
            AddRealizationClaim(
                claim=claim_draft(claim_id=seeds.claim_id, revision_id=seeds.revision_id),
                anchor=AnchorReference(anchor_id=str(seeds.anchor_id)),
            ),
        )
    )
    assert first.state == "changed"
    assert first.refusal is None

    second = candidate.apply(
        candidate.batch(
            SetInvariantLabel(
                invariant_id=seeds.invariant_id,
                display_label="renamed invariant",
                expected_row_digest=_invariant_digest(candidate, seeds.invariant_id),
            ),
            SetFamilyLabel(
                family_id=seeds.family_id,
                display_label="renamed family",
                expected_row_digest=_family_digest(candidate, seeds.family_id),
            ),
            RemoveRealizationClaim(
                claim_id=seeds.claim_id,
                expected_row_digest=_claim_digest(candidate, seeds.claim_id),
            ),
            RemoveFamilyMember(
                member_id=seeds.member_id,
                expected_row_digest=_member_digest(candidate, seeds.member_id),
            ),
            RemoveSourceAnchor(anchor_id=str(seeds.anchor_id)),
        )
    )
    assert second.state == "changed"

    store = candidate.open()
    try:
        invariant = store.get_invariant(seeds.invariant_id)
        family = families.get_family(store, seeds.family_id)
        successor = store.get_revision(seeds.successor_id)
        assert invariant is not None
        assert family is not None
        assert successor is not None
        assert invariant.display_label == "renamed invariant"
        assert family.display_label == "renamed family"
        assert successor.predecessors_sorted == (seeds.revision_id,)
        assert realizations.get_realization_claim(store, seeds.claim_id) is None
        assert memberships.get_family_member(store, seeds.member_id) is None
        assert anchors.get_anchor(store, str(seeds.anchor_id)) is None
    finally:
        store.close()

    # D-40, the same subject from the other side: the union's own annotation IS its membership, so
    # the prose that describes it states no count. It said "twelve" in three places while the union
    # carried thirty-one members, and a retyped count is a second declaration that rots on its own.
    # The sites are extracted by their own structure -- the module docstring, ``_refuse_unreachable``'s
    # docstring, and the comment block above the union -- so an edit that reintroduces a number fails
    # here instead of being discovered by a reader who trusted it.
    sites = {
        "memory/knowledge/candidate.py": _module_docstring("memory/knowledge/candidate.py"),
        "memory/knowledge/batch_commands.py": _function_docstring(
            "memory/knowledge/batch_commands.py", "_refuse_unreachable"
        ),
        "models/knowledge/candidate.py": _comment_block_above(
            "models/knowledge/candidate.py", "ProposedCommand = Annotated["
        ),
    }
    declared = len(get_args(get_args(ProposedCommand)[0]))
    assert declared > 0
    for path, prose in sites.items():
        assert prose.strip(), f"{path}: the union's prose site is gone, so this proves nothing"
        normalized = " ".join(re.sub(r"[#*`]", " ", prose).split())
        claims = [match.group(0) for match in _CARDINAL_CLAIM.finditer(normalized)]
        assert claims == [], (
            f"{path} restates the command union's membership ({claims}) while its one declaration "
            f"carries {declared} members; state no count instead of a second one"
        )


def test_a_receipt_reports_the_rows_the_store_now_holds(
    candidate: CandidateHarness,
) -> None:
    """Every receipt row names a stored record and carries the digest that record now has.

    A receipt that reported the digest the command asked for, rather than the one the store
    computed, would let a caller believe a row was stored under an identity it does not have.
    """

    seeds = CommandSeeds()
    result = candidate.apply(
        candidate.batch(
            AddInvariant(invariant_id=seeds.invariant_id, display_label="receipt invariant"),
            AddInvariantRevision(
                revision=revision_draft(
                    candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
                )
            ),
        )
    )
    assert result.state == "changed"
    reported = {(row.table, row.record_id): row.digest for row in result.changed}
    assert set(reported) == {
        ("invariant", seeds.invariant_id),
        ("invariant_revision", seeds.revision_id),
    }

    store = candidate.open()
    try:
        stored_invariant = store.get_invariant(seeds.invariant_id)
        stored_revision = store.get_revision(seeds.revision_id)
        assert stored_invariant is not None
        assert stored_revision is not None
        assert reported[("invariant", seeds.invariant_id)] == stored_invariant.row_digest
        assert reported[("invariant_revision", seeds.revision_id)] == (
            stored_revision.revision.payload_digest
        )
    finally:
        store.close()

    assert result.before.repository_id == candidate.repository_id
    assert result.after.logical_digest == candidate.logical_digest()
    assert result.before.logical_digest != result.after.logical_digest

    # D-42: one entry per row written, even when two commands touch the same row. ``add_realization_
    # claim`` writes the anchor it cites when the batch did not place that anchor first, so the
    # documented anchor-then-claim order reported that anchor twice -- once per command -- and a
    # consumer counting entries over-counted the rows it wrote. The whole-list ingest in the
    # knowledge lane places the anchor and the citing claim adjacent by design, so the second batch
    # below is the shipped shape rather than a contrived one.
    claim_seeds = CommandSeeds()
    cited = candidate.apply(
        candidate.batch(
            AddInvariantRevision(
                revision=revision_draft(
                    candidate,
                    invariant_id=seeds.invariant_id,
                    revision_id=seeds.successor_id,
                    predecessors=(seeds.revision_id,),
                    statement="A successor the citing claim resolves against.",
                )
            ),
            AddSourceAnchor(anchor=anchor_draft(claim_seeds.anchor_id)),
            AddRealizationClaim(
                claim=claim_draft(claim_id=claim_seeds.claim_id, revision_id=seeds.successor_id),
                anchor=AnchorReference(anchor_id=str(claim_seeds.anchor_id)),
            ),
        )
    )
    assert cited.state == "changed"
    entries = [(row.table, row.record_id) for row in cited.changed]
    assert len(entries) == len(set(entries)), (
        f"a receipt entry is a row the batch wrote, so no row may appear twice: {entries}"
    )
    assert set(entries) == {
        ("invariant_revision", seeds.successor_id),
        ("source_anchor", str(claim_seeds.anchor_id)),
        ("realization_claim", claim_seeds.claim_id),
    }
    assert cited.after.logical_digest == candidate.logical_digest()


def test_a_changed_receipt_must_name_at_least_one_touched_record(
    candidate: CandidateHarness,
) -> None:
    """A result that moved the dataset cannot report that it touched nothing.

    The two identities in a receipt and its entry list are two statements of one fact: ``after``
    differs from ``before`` because a row was written or removed. A receipt claiming otherwise is
    not a terse success, it is a contradiction, and the model refuses to construct it -- which is
    what keeps a caller from acting on a change no entry can explain.
    """

    before = candidate.open()
    try:
        identity = before.snapshot_identity()
    finally:
        before.close()

    # The contradiction in its plainest form: one identity reported as both sides of a change.
    with pytest.raises(ValueError, match="at least one touched record"):
        MutationResult(state="changed", before=identity, after=identity)

    # And with the dataset genuinely moved, which is the shape a batch leaves behind.
    moved = identity.model_copy(update={"logical_digest": "0" * 64})
    with pytest.raises(ValueError, match="at least one touched record"):
        MutationResult(state="changed", before=identity, after=moved)


def test_an_empty_batch_commits_no_record_and_reports_no_change(
    candidate: CandidateHarness,
) -> None:
    """An empty batch returns ``no_change`` and leaves the dataset identity exactly where it was.

    The failure this catches is a batch that writes an audit timestamp, a counter or a cache row to
    record that "nothing happened" -- a row that would make an empty request a mutation.
    """

    counts_before = candidate.table_counts()
    digest_before = candidate.logical_digest()

    result = candidate.apply(candidate.batch())

    assert result.state == "no_change"
    assert result.changed == ()
    assert result.refusal is None
    assert result.before == result.after
    assert result.after.logical_digest == digest_before
    assert candidate.table_counts() == counts_before


def test_a_command_whose_effect_is_already_stored_is_refused_not_absorbed(
    candidate: CandidateHarness,
) -> None:
    """Re-stating a stored revision as a new insertion refuses instead of quietly matching it.

    An insertion is never an upsert. A caller whose read was stale has to find that out, because the
    alternative -- the operation deciding the two aggregates are "the same" and returning success --
    would hide a proposal the caller believed it had authored.
    """

    seeds = CommandSeeds()
    draft = revision_draft(
        candidate, invariant_id=seeds.invariant_id, revision_id=seeds.revision_id
    )
    assert (
        candidate.apply(
            candidate.batch(
                AddInvariant(invariant_id=seeds.invariant_id, display_label="absorb invariant"),
                AddInvariantRevision(revision=draft),
            )
        ).state
        == "changed"
    )

    counts_before = candidate.table_counts()
    repeated = candidate.apply(candidate.batch(AddInvariantRevision(revision=draft)))
    assert repeated.state == "refused"
    assert repeated.refusal is not None
    assert repeated.refusal.code == "stale_precondition"
    assert repeated.refusal.operation == "change_candidate"
    assert candidate.table_counts() == counts_before


def test_an_expected_record_that_matches_permits_the_batch(
    candidate: CandidateHarness,
) -> None:
    """A batch proceeds when every stated expectation is exactly the stored state."""

    (invariant_id, revision_id) = candidate.seed(1)[0]
    digest = _invariant_digest(candidate, invariant_id)
    context = candidate.context()
    batch = ChangeBatch(
        expected=context,
        expected_records=(
            ExpectedRecord(
                state="present", table="invariant", record_id=invariant_id, digest=digest
            ),
            ExpectedRecord(
                state="present",
                table="invariant_revision",
                record_id=revision_id,
                digest=_revision_digest(candidate, revision_id),
            ),
            ExpectedRecord(state="absent", table="family", record_id=str(uuid4())),
        ),
        commands=(
            SetInvariantLabel(
                invariant_id=invariant_id,
                display_label="expectation satisfied",
                expected_row_digest=digest,
            ),
        ),
    )
    result = candidate.apply(batch)
    assert result.state == "changed"
    assert result.refusal is None


def test_the_closed_union_refuses_an_unknown_field_and_an_unknown_command(
    candidate: CandidateHarness,
) -> None:
    """The payload boundary refuses extra fields and a command kind the union does not declare.

    Both refusals are the union doing its job: a free-form field is how an approval or an author
    would try to ride along, and an undeclared kind is how arbitrary SQL would try to arrive. Neither
    is representable, so both fail at the model boundary rather than at a check inside the operation.
    """

    context = candidate.context().model_dump(mode="json")
    with pytest.raises(ValueError, match="approved"):
        ChangeBatch.model_validate(
            {
                "expected": context,
                "commands": [
                    {
                        "kind": "add_invariant",
                        "invariant_id": str(uuid4()),
                        "display_label": "attempt",
                        "approved": True,
                    }
                ],
            }
        )
    with pytest.raises(ValueError):
        ChangeBatch.model_validate(
            {
                "expected": context,
                "commands": [{"kind": "insert_raw_sql", "statement": "DELETE FROM invariant"}],
            }
        )


def test_two_expectations_for_one_record_are_refused_at_the_boundary(
    candidate: CandidateHarness,
) -> None:
    """A batch may not state two expectations for one record, because they could contradict."""

    context = candidate.context()
    record_id = str(uuid4())
    with pytest.raises(ValueError, match="two expectations"):
        ChangeBatch(
            expected=context,
            expected_records=(
                ExpectedRecord(state="absent", table="family", record_id=record_id),
                ExpectedRecord(state="absent", table="family", record_id=record_id),
            ),
        )


def test_the_context_digest_seals_the_whole_resolved_context(
    candidate: CandidateHarness,
) -> None:
    """A context whose fields were edited after resolution is refused rather than compared.

    The operation compares a re-derived digest before it compares anything field by field, which is
    what stops a caller from assembling a context that is internally inconsistent -- a candidate
    reference from one resolution beside a dataset identity from another.
    """

    context = candidate.context()
    tampered = context.model_copy(update={"candidate_ref": "draft:somewhere-else"})
    with pytest.raises(ValueError, match="context_digest"):
        type(context).model_validate(tampered.model_dump(mode="json"))


_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "agents_remember"

_CARDINAL_CLAIM = re.compile(
    r"\b(?:\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty)\b"
    r"(?:\s+[\w'-]+){0,2}\s+(?:commands|command kinds|kinds)\b",
    re.IGNORECASE,
)


def _module_docstring(relative: str) -> str:
    source = (_PACKAGE_ROOT / relative).read_text(encoding="utf-8")
    return ast.get_docstring(ast.parse(source, filename=relative)) or ""


def _function_docstring(relative: str, name: str) -> str:
    source = (_PACKAGE_ROOT / relative).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source, filename=relative)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_docstring(node) or ""
    raise AssertionError(f"{relative} declares no {name!r}, so its prose cannot be checked")


def _comment_block_above(relative: str, declaration: str) -> str:
    lines = (_PACKAGE_ROOT / relative).read_text(encoding="utf-8").splitlines()
    index = next(
        (position for position, line in enumerate(lines) if line.startswith(declaration)), None
    )
    if index is None:
        raise AssertionError(f"{relative} declares no {declaration!r}")
    block: list[str] = []
    cursor = index - 1
    while cursor >= 0 and lines[cursor].lstrip().startswith("#"):
        block.append(lines[cursor])
        cursor -= 1
    return "\n".join(reversed(block))


def _invariant_digest(harness: CandidateHarness, invariant_id: str) -> str:
    store = harness.open()
    try:
        identity = store.get_invariant(invariant_id)
        assert identity is not None
        return identity.row_digest
    finally:
        store.close()


def _revision_digest(harness: CandidateHarness, revision_id: str) -> str:
    store = harness.open()
    try:
        stored = store.get_revision(revision_id)
        assert stored is not None
        return stored.revision.payload_digest
    finally:
        store.close()


def _family_digest(harness: CandidateHarness, family_id: str) -> str:
    store = harness.open()
    try:
        identity = families.get_family(store, family_id)
        assert identity is not None
        return identity.row_digest
    finally:
        store.close()


def _member_digest(harness: CandidateHarness, member_id: str) -> str:
    store = harness.open()
    try:
        member = memberships.get_family_member(store, member_id)
        assert member is not None
        return member.row_digest
    finally:
        store.close()


def _claim_digest(harness: CandidateHarness, claim_id: str) -> str:
    store = harness.open()
    try:
        claim = realizations.get_realization_claim(store, claim_id)
        assert claim is not None
        return claim.row_digest
    finally:
        store.close()
