"""Block fingerprints, per-part staleness and the judgment sorts (MIK-R187 rules 3, 6, 8).

Each case protects one promise: a locator that keeps its grade across a line shift and one that
does not; a hunk, a non-text change and a record fingerprinted by rule 3's own table; every row of
rule 8's basis table, stale and current; the three-parts-one-stale case; the trusted list and the
regeneration message the developer's press sends; and the judgment sort that puts a subject's blocks
in grade order with the ungraded ones last.

Nothing here reads Git: the target's answers come from :class:`FakeReads`, which answers ``None``
where a real reader would answer "cannot be read" -- the state the rules must treat as stale.
"""

from __future__ import annotations

import pytest
from agents_remember.canvas.explainer_grades import Grade, Grades
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
    BlockIdentity,
    CardSide,
    GradedBlock,
    HunkSides,
    PartStaleness,
    ProofEntryRead,
    StalenessContext,
    TargetComparison,
    basis_stale,
    card_fingerprint,
    carry_merged_hunk_grades,
    file_fingerprint,
    frame_stale_parts,
    grade_staleness,
    hunk_fingerprint,
    judgment_sort,
    keeps_fingerprint_across_a_line_shift,
    parse_block_identity,
    part_staleness,
    record_fingerprint,
    regeneration_request,
    trusted_list,
)
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_frame import (
    FrameAsset,
    FrameAuthoring,
    FrameBinding,
    FrameEntrypoint,
    FrameManifest,
    FramePart,
    FrameStaleness,
    FrameStalePart,
    artifacts_digest,
)
from canvas_host_test_support import packet, record

AT = "2026-10-22T09:00:00+00:00"
CODE_BASE = "a" * 40
CODE_CANDIDATE = "b" * 40
MEMORY_BASE = "c" * 40
MEMORY_CANDIDATE = "d" * 40
TREES = (CODE_BASE, CODE_CANDIDATE)
SCALE = ("sound", "thin", "wrong")


class FakeReads:
    """The target's answers, keyed by the question, with ``None`` for anything unread.

    ``content`` answers :meth:`TargetReads.content_identity` exactly; a question it does not hold is
    unreadable. ``mapping`` defaults to the identity, so a range with no entry keeps its lines -- the
    line-shift case -- and an explicit ``None`` entry makes it unmappable.
    """

    def __init__(self, **answers: object) -> None:
        self.content: dict[tuple[object, ...], str | None] = answers.get("content", {})  # type: ignore[assignment]
        self.mapping: dict[tuple[object, ...], tuple[int, int] | None] = answers.get(  # type: ignore[assignment]
            "mapping", {}
        )
        self.records: dict[tuple[str, str, str], int] = answers.get("records", {})  # type: ignore[assignment]
        self.proofs: dict[tuple[str, str], ProofEntryRead] = answers.get("proofs", {})  # type: ignore[assignment]
        self.rows: dict[str, tuple[tuple[str, int], ...]] = answers.get("rows", {})  # type: ignore[assignment]
        self.history: dict[str, tuple[str, ...]] = answers.get("history", {})  # type: ignore[assignment]
        self.held: tuple[SupplementalReceipt, ...] | None = answers.get("held", ())  # type: ignore[assignment]
        self.later: dict[str, int] = answers.get("later", {})  # type: ignore[assignment]

    def content_identity(
        self, *, side: str, tree: str, path: str, lines: tuple[int, int] | None
    ) -> str | None:
        return self.content.get((side, tree, path, lines))

    def mapped_lines(
        self, *, side: str, path: str, from_tree: str, to_tree: str, lines: tuple[int, int]
    ) -> tuple[int, int] | None:
        key = (side, path, from_tree, to_tree, lines)
        return self.mapping.get(key, lines)

    def memory_revision(self, *, side: str, record: str, path: str) -> int | None:
        return self.records.get((side, record, path))

    def proof_entry(self, *, proof: str, record: str) -> ProofEntryRead | None:
        return self.proofs.get((proof, record))

    def judgment_rows(self, *, record: str) -> tuple[tuple[str, int], ...] | None:
        return self.rows.get(record)

    def history_rows(self, *, path: str) -> tuple[str, ...] | None:
        return self.history.get(path)

    def receipts(self) -> tuple[SupplementalReceipt, ...] | None:
        return self.held

    def registered_version(self, *, source: str) -> int | None:
        return self.later.get(source)


def _binding(number: int = 7) -> FrameBinding:
    return FrameBinding.from_packet(packet(record(number)).comparison)


def _target(reads: FakeReads | None = None, *, number: int = 8) -> TargetComparison:
    return TargetComparison(binding=_binding(number), reads=reads or FakeReads())


def _claim(
    *bases: ClaimBasis, claim_id: str = "c-one", classification: str = "recorded-fact"
) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id=claim_id,
        authoring_revision="rev-1",
        classification=classification,  # type: ignore[arg-type]
        subject="the change to src/app.py",
        applicability="this comparison",
        statement="src/app.py gained one function",
        bases=bases,
        limit="the record is bounded"
        if classification
        in {
            "unresolved-question",
            "unavailable-evidence",
        }
        else None,
    )


def _blob_basis(*, start: int | None = None, end: int | None = None) -> ClaimBasis:
    return ClaimBasis(
        kind="tree-blob",
        reference="code-candidate:src/app.py",
        state="present",
        side="code-candidate",
        tree=CODE_CANDIDATE,
        path="src/app.py",
        start_line=start,
        end_line=end,
    )


def _manifest(
    *claims: EvidenceClaim,
    part_map: tuple[FramePart, ...] = (),
    receipts: tuple[SupplementalReceipt, ...] = (),
    revision: int = 3,
) -> FrameManifest:
    comparison = record(7)
    evidence = packet(comparison, claims=claims, supplemental=receipts)
    assets = (FrameAsset(path="index.html", media_type="text/html", byte_size=24, digest="e" * 64),)
    return FrameManifest(
        frame_id="the-page",
        revision=revision,
        binding=FrameBinding.from_packet(evidence.comparison),
        focus="the change to src/app.py",
        authoring=FrameAuthoring(request_id="req-1", started_at=AT),
        created_at=AT,
        entrypoint=FrameEntrypoint(path="index.html"),
        assets=assets,
        artifact_digest=artifacts_digest(assets),
        evidence=evidence,
        supplemental=receipts,
        part_map=part_map,
    )


def _context(
    reads: FakeReads | None = None, *, number: int = 8, receipts: dict | None = None
) -> StalenessContext:
    return StalenessContext(
        retained=_binding(7), target=_target(reads, number=number), receipts=receipts or {}
    )


# --- rule 3: the locator kinds -------------------------------------------------------------------


def test_a_line_shift_keeps_a_symbol_and_a_held_line_range_but_never_a_file() -> None:
    assert keeps_fingerprint_across_a_line_shift("symbol", recorded_blob_held=False) is True
    assert keeps_fingerprint_across_a_line_shift("line_range", recorded_blob_held=True) is True
    assert keeps_fingerprint_across_a_line_shift("line_range", recorded_blob_held=False) is False
    assert keeps_fingerprint_across_a_line_shift("file", recorded_blob_held=True) is False


def test_a_card_fingerprint_is_each_sides_content_and_survives_a_shift_when_its_text_is_the_same() -> (
    None
):
    block = BlockIdentity.card(
        entry="INV-AAA", side="code-candidate", path="src/app.py", lines=(10, 14), trees=TREES
    )
    shifted = BlockIdentity.card(
        entry="INV-AAA", side="code-candidate", path="src/app.py", lines=(40, 44), trees=TREES
    )
    before = card_fingerprint(
        block=block,
        base=CardSide(content="sha256:aaa"),
        candidate=CardSide(content="sha256:bbb"),
        locator="symbol",
    )
    after = card_fingerprint(
        block=shifted,
        base=CardSide(content="sha256:aaa"),
        candidate=CardSide(content="sha256:bbb"),
        locator="symbol",
    )
    assert before.value is not None
    assert before.value == after.value


def test_a_file_locators_card_moves_when_the_file_changes_elsewhere() -> None:
    block = BlockIdentity.card(
        entry="INV-AAA", side="code-candidate", path="src/app.py", lines=(10, 14), trees=TREES
    )
    held = card_fingerprint(
        block=block,
        base=CardSide(content="sha256:aaa"),
        candidate=CardSide(content="sha256:bbb"),
        locator="file",
    )
    changed = card_fingerprint(
        block=block,
        base=CardSide(content="sha256:aaa"),
        candidate=CardSide(content="sha256:ccc"),
        locator="file",
    )
    assert held.value != changed.value


def test_a_card_side_never_read_is_uncomputable_and_an_absent_side_is_a_deletion() -> None:
    block = BlockIdentity.card(
        entry="INV-AAA", side="code-candidate", path="src/app.py", lines=None, trees=TREES
    )
    unread = card_fingerprint(
        block=block,
        base=CardSide(content="sha256:aaa"),
        candidate=CardSide(),
        locator="symbol",
    )
    assert unread.current is False
    assert unread.reason is not None and "candidate" in unread.reason
    deleted = card_fingerprint(
        block=block,
        base=CardSide(content="sha256:aaa"),
        candidate=CardSide(absent=True),
        locator="symbol",
    )
    assert deleted.current is True
    assert (
        deleted.value
        != card_fingerprint(
            block=block,
            base=CardSide(content="sha256:aaa"),
            candidate=CardSide(content="sha256:bbb"),
            locator="symbol",
        ).value
    )
    with pytest.raises(ValueError):
        CardSide(content="sha256:aaa", absent=True)


# --- rule 3: hunks, non-text changes and records -------------------------------------------------


def _hunk(*, base: str, candidate: str, before=(10, 4), after=(10, 4)) -> HunkSides:
    return HunkSides(
        path="src/app.py",
        base=base,
        candidate=candidate,
        base_span=before,
        candidate_span=after,
        trees=TREES,
    )


def test_an_unexplained_hunk_is_fingerprinted_by_its_subject_and_identified_by_its_span() -> None:
    sides = _hunk(base="sha256:aaa", candidate="sha256:bbb", after=(40, 4))
    fingerprint = sides.fingerprint()
    assert fingerprint.value == "hunk:src/app.py@sha256:aaa..sha256:bbb"
    assert fingerprint.block.kind == "unexplained-hunk"
    assert (fingerprint.block.start_line, fingerprint.block.end_line) == (40, 43)
    same_lines = _hunk(base="sha256:aaa", candidate="sha256:bbb", after=(90, 4))
    assert same_lines.fingerprint().value == fingerprint.value
    assert same_lines.fingerprint().reference != fingerprint.reference


def test_a_pure_deletion_hunk_keeps_its_subject_and_is_identified_by_the_base_span() -> None:
    sides = _hunk(base="sha256:aaa", candidate="absent", before=(12, 3), after=(11, 0))
    fingerprint = sides.fingerprint()
    assert sides.pure_deletion is True
    assert fingerprint.value == "hunk:src/app.py@sha256:aaa..absent"
    assert fingerprint.block.kind == "pure-deletion-hunk"
    assert (fingerprint.block.start_line, fingerprint.block.end_line) == (12, 14)  # 3 lines from 12


def test_a_hunk_without_a_subject_is_uncomputable_rather_than_guessed() -> None:
    assert (
        HunkSides.from_lane_hunk(
            path="src/app.py",
            subject=None,
            before=(1, 1),
            after=(1, 1),
            trees=TREES,
        )
        is None
    )
    loose = hunk_fingerprint(None, path="src/app.py", trees=TREES)
    assert loose.current is False
    assert loose.reason is not None and "not hashed" in loose.reason


def test_merged_hunks_carry_their_grades_onto_one_hunk_all_stale_with_the_highest_chip() -> None:
    target = _hunk(base="sha256:aaa", candidate="sha256:999").fingerprint()
    sources = (
        Grade(
            reference="hunk-one",
            category="risk",
            grade="thin",
            reason="one hunk",
            fingerprint="hunk:src/app.py@sha256:aaa..sha256:bbb",
            binding=_binding(7),
            made_at=AT,
        ),
        Grade(
            reference="hunk-two",
            category="risk",
            grade="sound",
            reason="another hunk",
            fingerprint="hunk:src/app.py@sha256:aaa..sha256:ccc",
            binding=_binding(7),
            made_at=AT,
        ),
    )
    carry = carry_merged_hunk_grades(target=target, sources=sources, scale=SCALE)
    assert [state.stale for state in carry.carried] == [True, True]
    assert [state.reference for state in carry.carried] == [target.reference, target.reference]
    assert [state.grade.reference for state in carry.carried] == [
        target.reference,
        target.reference,
    ]
    assert all("merged into" in (state.reason or "") for state in carry.carried)
    assert carry.chip == "sound"
    assert [item["grade"] for item in carry.hover] == ["sound", "thin"]


def test_a_non_text_change_is_fingerprinted_by_its_file_subject_and_a_new_blob_moves_it() -> None:
    held = file_fingerprint(path="img/mark.png", blob="1" * 40, trees=TREES)
    changed = file_fingerprint(path="img/mark.png", blob="2" * 40, trees=TREES)
    assert held.value == f"file:img/mark.png@{'1' * 40}"
    assert held.block.kind == "non-text-file"
    assert held.value != changed.value
    deleted = file_fingerprint(path="img/mark.png", blob=None, trees=TREES)
    assert deleted.current is True and deleted.value == "file:img/mark.png@absent"
    unread = file_fingerprint(path="img/mark.png", blob=None, trees=TREES, unread=True)
    assert unread.current is False


def test_block_identity_preserves_all_native_l89_discriminators_and_round_trips() -> None:
    hunk_one = HunkSides(
        path="src/app.py",
        base="sha256:aaa",
        candidate="sha256:bbb",
        base_span=(10, 2),
        candidate_span=(40, 4),
        trees=TREES,
    ).identity()
    hunk_two = HunkSides(
        path="src/app.py",
        base="sha256:aaa",
        candidate="sha256:bbb",
        base_span=(10, 5),
        candidate_span=(40, 4),
        trees=TREES,
    ).identity()
    assert hunk_one.reference() != hunk_two.reference()
    assert hunk_one.block_key() != hunk_two.block_key()
    assert parse_block_identity(hunk_one.block_key()) == hunk_one
    assert parse_block_identity(hunk_one.reference()) == hunk_one

    # Opposite spans are distinct: the same numbers in the other direction are another block.
    reversed_spans = HunkSides(
        path="src/app.py",
        base="sha256:aaa",
        candidate="sha256:bbb",
        base_span=(40, 4),
        candidate_span=(10, 2),
        trees=TREES,
    ).identity()
    assert reversed_spans.reference() != hunk_one.reference()
    assert reversed_spans.block_key() != hunk_one.block_key()
    assert parse_block_identity(reversed_spans.block_key()) == reversed_spans

    card_one = BlockIdentity.card(
        entry="RLZ-AAA",
        side="after",
        path="src/app.py",
        lines=(10, 14),
        trees=(CODE_BASE, CODE_CANDIDATE, MEMORY_BASE, MEMORY_CANDIDATE),
    )
    card_two = BlockIdentity.card(
        entry="RLZ-AAA",
        side="after",
        path="src/app.py",
        lines=(10, 14),
        trees=(CODE_BASE, CODE_CANDIDATE, MEMORY_BASE, "e" * 40),
    )
    assert card_one.reference() != card_two.reference()
    assert card_one.block_key() != card_two.block_key()
    assert parse_block_identity(card_one.block_key()) == card_one
    assert parse_block_identity(card_one.reference()) == card_one


def test_a_record_is_fingerprinted_by_its_revision_and_a_code_change_under_it_does_not_move_it() -> (
    None
):
    first = record_fingerprint(kind="invariant", record="INV-AAA", revision=3)
    again = record_fingerprint(kind="invariant", record="INV-AAA", revision=3)
    later = record_fingerprint(kind="family", record="FAM-AAA", revision=4)
    assert first.value == "invariant:INV-AAA@3"
    assert first.value == again.value
    assert later.value == "family:FAM-AAA@4"
    unnamed = record_fingerprint(kind="invariant", record="INV-AAA", revision=None)
    assert unnamed.current is False


# --- rule 8's basis table: each row, stale and current -------------------------------------------


def test_a_tree_blob_is_current_when_the_mapped_range_hashes_the_same() -> None:
    reads = FakeReads(
        content={
            ("code-candidate", CODE_CANDIDATE, "src/app.py", (10, 14)): "sha256:same",
        },
        mapping={
            ("code-candidate", "src/app.py", CODE_CANDIDATE, CODE_CANDIDATE, (10, 14)): (40, 44)
        },
    )
    # The recorded range and the mapped range both read the same bytes.
    reads.content[("code-candidate", CODE_CANDIDATE, "src/app.py", (40, 44))] = "sha256:same"
    assert basis_stale(_blob_basis(start=10, end=14), _context(reads)) is None


def test_a_tree_blob_is_stale_when_the_bytes_moved_shifted_unmappable_or_unreadable() -> None:
    moved = FakeReads(
        content={
            ("code-candidate", CODE_CANDIDATE, "src/app.py", (10, 14)): "sha256:before",
            ("code-candidate", CODE_CANDIDATE, "src/app.py", (40, 44)): "sha256:after",
        },
        mapping={
            ("code-candidate", "src/app.py", CODE_CANDIDATE, CODE_CANDIDATE, (10, 14)): (40, 44)
        },
    )
    assert "changed" in (basis_stale(_blob_basis(start=10, end=14), _context(moved)) or "")

    unmappable = FakeReads(
        content={("code-candidate", CODE_CANDIDATE, "src/app.py", (10, 14)): "sha256:before"},
        mapping={("code-candidate", "src/app.py", CODE_CANDIDATE, CODE_CANDIDATE, (10, 14)): None},
    )
    reason = basis_stale(_blob_basis(start=10, end=14), _context(unmappable))
    assert reason is not None and "maps to no range" in reason

    unreadable = FakeReads()
    reason = basis_stale(_blob_basis(), _context(unreadable))
    assert reason is not None and "no readable" in reason


def test_a_knowledge_record_is_stale_on_another_revision_or_on_none_and_reads_its_named_side() -> (
    None
):
    basis = ClaimBasis(
        kind="knowledge-record",
        reference="knowledge:INV-AAA",
        state="present",
        side="memory-base",
        record_id="INV-AAA",
        record_revision=3,
        path="invariants/inv-aaa.md",
    )
    current = FakeReads(records={("memory-base", "INV-AAA", "invariants/inv-aaa.md"): 3})
    assert basis_stale(basis, _context(current)) is None

    later = FakeReads(records={("memory-base", "INV-AAA", "invariants/inv-aaa.md"): 4})
    assert "revision 4" in (basis_stale(basis, _context(later)) or "")

    # The candidate side is checked only when the basis names no memory side.
    unnamed = ClaimBasis(
        kind="knowledge-record",
        reference="knowledge:INV-AAA",
        state="present",
        record_id="INV-AAA",
        record_revision=3,
        path="invariants/inv-aaa.md",
    )
    elsewhere = FakeReads(records={("memory-base", "INV-AAA", "invariants/inv-aaa.md"): 3})
    assert "holds no record" in (basis_stale(unnamed, _context(elsewhere)) or "")


def test_a_proof_entry_is_stale_when_its_execution_or_its_fingerprint_differs_or_it_is_gone() -> (
    None
):
    basis = ClaimBasis(
        kind="proof-entry",
        reference="proof:PRF-1",
        state="present",
        proof_id="PRF-1",
        record_id="INV-AAA",
        execution="established",
    )
    current = FakeReads(proofs={("PRF-1", "INV-AAA"): ProofEntryRead("sha256:p", "established")})
    with_baseline = StalenessContext(
        retained=_binding(7),
        target=_target(current),
        recorded_fingerprints={"PRF-1": "sha256:p"},
    )
    assert basis_stale(basis, with_baseline) is None
    assert "no baseline fingerprint" in (basis_stale(basis, _context(current)) or "")

    unexecuted = FakeReads(proofs={("PRF-1", "INV-AAA"): ProofEntryRead("sha256:p", "claimed")})
    assert "claimed at the target" in (basis_stale(basis, _context(unexecuted)) or "")

    gone = FakeReads()
    assert "is gone" in (basis_stale(basis, _context(gone)) or "")

    changed = FakeReads(proofs={("PRF-1", "INV-AAA"): ProofEntryRead("sha256:q", "established")})
    context = StalenessContext(
        retained=_binding(7),
        target=_target(changed),
        recorded_fingerprints={"PRF-1": "sha256:p"},
    )
    assert "changed" in (basis_stale(basis, context) or "")


def test_a_judgment_row_is_stale_on_a_later_attempt_or_a_missing_row() -> None:
    basis = ClaimBasis(
        kind="judgment-row",
        reference="judgment:J-1",
        state="present",
        record_id="J-1",
        row_id="row-2",
        attempt=2,
    )
    current = FakeReads(rows={"J-1": (("row-2", 2),)})
    assert basis_stale(basis, _context(current)) is None
    exact_with_earlier = FakeReads(rows={"J-1": (("row-2", 1), ("row-2", 2))})
    assert basis_stale(basis, _context(exact_with_earlier)) is None

    earlier_only = FakeReads(rows={"J-1": (("row-2", 1),)})
    assert "holds no attempt 2" in (basis_stale(basis, _context(earlier_only)) or "")

    later = FakeReads(rows={"J-1": (("row-2", 3),)})
    assert "attempt 3" in (basis_stale(basis, _context(later)) or "")

    exact_plus_later = FakeReads(rows={"J-1": (("row-2", 2), ("row-2", 3))})
    assert "attempt 3" in (basis_stale(basis, _context(exact_plus_later)) or "")

    missing = FakeReads(rows={"J-1": (("row-1", 1),)})
    assert "holds no row" in (basis_stale(basis, _context(missing)) or "")

    unreadable = FakeReads()
    assert "cannot be read" in (basis_stale(basis, _context(unreadable)) or "")


def test_a_history_event_is_stale_when_the_target_history_has_no_such_row() -> None:
    basis = ClaimBasis(
        kind="history-event",
        reference="history:notes/x.md",
        state="present",
        path="notes/x.md",
        row_id="row-9",
    )
    current = FakeReads(history={"notes/x.md": ("row-9", "row-10")})
    assert basis_stale(basis, _context(current)) is None
    missing = FakeReads(history={"notes/x.md": ("row-10",)})
    assert "holds no row" in (basis_stale(basis, _context(missing)) or "")


def test_a_supplemental_receipt_is_stale_when_its_bytes_or_its_version_moved() -> None:
    receipt = SupplementalReceipt(
        receipt_id="RCP-1",
        canonical_source="requirements/MIK-R999-v1-a-packet.md",
        digest="f" * 64,
        byte_size=120,
        read_state="present",
        capture_identity="cap-1",
        source_version="v1",
        establishes="the packet's rule 2",
    )
    basis = ClaimBasis(
        kind="supplemental-receipt", reference="RCP-1", state="present", receipt_id="RCP-1"
    )
    context = StalenessContext(
        retained=_binding(7),
        target=_target(FakeReads(held=(receipt,))),
        receipts={"RCP-1": receipt},
    )
    assert basis_stale(basis, context) is None

    other_bytes = FakeReads(
        held=(receipt.model_copy(update={"digest": "0" * 64}),),
    )
    stale = StalenessContext(
        retained=_binding(7), target=_target(other_bytes), receipts={"RCP-1": receipt}
    )
    assert "holds no receipt" in (basis_stale(basis, stale) or "")

    later = FakeReads(held=(receipt,), later={receipt.canonical_source: 2})
    superseded = StalenessContext(
        retained=_binding(7), target=_target(later), receipts={"RCP-1": receipt}
    )
    assert "registers version 2" in (basis_stale(basis, superseded) or "")


def test_a_comparison_field_worklist_row_or_a_claim_with_no_basis_is_stale_when_a_side_moves() -> (
    None
):
    field = ClaimBasis(kind="comparison-field", reference="comparison:base", state="present")
    row = ClaimBasis(kind="worklist-row", reference="worklist:row-1", state="present")
    still = _context(number=7)
    assert basis_stale(field, still) is None
    assert basis_stale(row, still) is None
    assert basis_stale(None, still) is None

    moved = StalenessContext(
        retained=_binding(7),
        target=_target(number=9),
    )
    assert "comparison" in (basis_stale(field, moved) or "")
    assert "comparison" in (basis_stale(row, moved) or "")
    reason = basis_stale(None, moved)
    assert reason is not None and "names no basis" in reason


# --- rule 8: the parts ----------------------------------------------------------------------------


def _three_part_manifest() -> FrameManifest:
    """Three parts: ``change`` rests on the changed blob, ``evidence`` and ``risk`` do not."""

    changed = _blob_basis(start=10, end=14)
    steady = ClaimBasis(
        kind="knowledge-record",
        reference="knowledge:INV-BBB",
        state="present",
        side="memory-candidate",
        record_id="INV-BBB",
        record_revision=2,
        path="invariants/inv-bbb.md",
    )
    claims = (
        _claim(changed, claim_id="c-changed"),
        _claim(steady, claim_id="c-steady", classification="recorded-fact"),
    )
    part_map = (
        FramePart(part="change", title="The change", claims=("c-changed",)),
        FramePart(part="evidence", title="The evidence", claims=("c-steady",)),
        FramePart(part="risk", title="The risk", claims=()),
    )
    return _manifest(*claims, part_map=part_map)


def _changed_target() -> TargetComparison:
    reads = FakeReads(
        content={
            ("code-candidate", CODE_CANDIDATE, "src/app.py", (10, 14)): "sha256:before",
            ("code-candidate", CODE_CANDIDATE, "src/app.py", (40, 44)): "sha256:after",
        },
        mapping={
            ("code-candidate", "src/app.py", CODE_CANDIDATE, CODE_CANDIDATE, (10, 14)): (40, 44)
        },
        records={("memory-candidate", "INV-BBB", "invariants/inv-bbb.md"): 2},
    )
    return _target(reads)


def test_exactly_the_part_resting_on_the_changed_blob_is_stale() -> None:
    manifest = _three_part_manifest()
    report = part_staleness(manifest, manifest.evidence.claims, _changed_target())
    assert report.part_ids == ("change",)
    assert report.whole_page is False
    assert report.checked == 3
    reason = report.parts[0].reasons[0]
    assert "tree-blob" in reason and "src/app.py:10-14" in reason and "changed" in reason


def test_the_trusted_list_names_each_stale_part_and_says_when_nothing_is_stale() -> None:
    manifest = _three_part_manifest()
    stale = trusted_list(part_staleness(manifest, manifest.evidence.claims, _changed_target()))
    assert stale.stale is True
    assert stale.lines[0] == (
        "revision 3 is stale in 1 part(s) against review-comparison:"
        f"{manifest.binding.task_id}/{manifest.binding.leaf_id}/8"
    )
    assert stale.lines[1].startswith("The change (change): tree-blob")

    steady_reads = FakeReads(
        content={
            ("code-candidate", CODE_CANDIDATE, "src/app.py", (10, 14)): "sha256:same",
            ("code-candidate", CODE_CANDIDATE, "src/app.py", (40, 44)): "sha256:same",
        },
        mapping={
            ("code-candidate", "src/app.py", CODE_CANDIDATE, CODE_CANDIDATE, (10, 14)): (40, 44)
        },
        records={("memory-candidate", "INV-BBB", "invariants/inv-bbb.md"): 2},
    )
    current = trusted_list(
        part_staleness(manifest, manifest.evidence.claims, _target(steady_reads))
    )
    assert current.stale is False
    assert "every claim basis still resolves" in current.lines[0]


def test_the_regeneration_message_names_the_part_its_changed_basis_and_the_stale_grade() -> None:
    manifest = _three_part_manifest()
    report = part_staleness(manifest, manifest.evidence.claims, _changed_target())
    graded = Grade(
        reference=report.parts[0].part,
        category="risk",
        grade="thin",
        reason="one hunk",
        fingerprint="sha256:old",
        binding=_binding(7),
        made_at=AT,
    )
    states = grade_staleness(
        Grades(leaf_id="leaf", grades=(graded,)),
        {
            graded.reference: BlockFingerprint.computed(
                BlockIdentity(kind="invariant", record="INV-AAA", revision=1), "sha256:new"
            )
        },
    )
    request = regeneration_request(report, states)
    assert request.label == "Regenerate stale (1 part · 1 grade)"
    assert request.part_count == 1 and request.grade_count == 1
    assert "The change (change): tree-blob" in request.message
    assert f"- {graded.reference} (thin)" in request.message
    assert "Nothing regenerates by itself" in request.message
    assert request.grade_references == (graded.reference,)


def test_a_page_without_a_part_map_is_one_part_stale_as_a_whole() -> None:
    manifest = _manifest(_claim(_blob_basis()))
    report = part_staleness(manifest, manifest.evidence.claims, _target())
    assert report.whole_page is True
    assert report.part_ids == ("page",)
    assert report.checked == 1
    assert trusted_list(report).lines[0].startswith("revision 3 is stale as a whole")


def test_an_older_part_map_of_basis_references_is_stale_as_a_whole() -> None:
    manifest = _manifest(
        _claim(_blob_basis()),
        part_map=(FramePart(part="change", title="The change", claims=()),),
    )
    report = part_staleness(manifest, manifest.evidence.claims, _target())
    assert report.whole_page is True
    assert report.part_ids == ("page",)
    assert "no readable" in report.parts[0].reasons[0]


def test_a_stale_claim_no_part_admits_makes_the_page_stale_as_a_whole() -> None:
    manifest = _manifest(
        _claim(_blob_basis()), part_map=(FramePart(part="risk", title="The risk"),)
    )
    report = part_staleness(manifest, manifest.evidence.claims, _target())
    assert report.unmapped_claims == ("c-one",)
    assert report.whole_page is True
    assert report.part_ids == ("page",)
    assert report.parts[0].claims == ("c-one",)


def test_a_current_claim_in_a_part_that_another_claim_makes_stale_stays_out_of_the_list() -> None:
    manifest = _three_part_manifest()
    report = part_staleness(manifest, manifest.evidence.claims, _changed_target())
    assert report.parts[0].claims == ("c-changed",)
    assert "c-steady" not in [claim for part in report.parts for claim in part.claims]


def test_the_report_and_the_manifest_staleness_carry_the_same_stale_parts() -> None:
    manifest = _three_part_manifest()
    report = part_staleness(manifest, manifest.evidence.claims, _changed_target())
    parts = frame_stale_parts(report)
    assert parts == (
        FrameStalePart(part="change", title="The change", reasons=report.parts[0].reasons),
    )
    current = FrameStaleness(
        state="current",
        frame_id=manifest.frame_id,
        revision=manifest.revision,
        retained=manifest.binding,
        selected=manifest.binding,
        detail="current",
    )
    assert current.stale_parts == ()
    with pytest.raises(ValueError):
        FrameStaleness(
            state="current",
            frame_id=manifest.frame_id,
            revision=manifest.revision,
            stale_parts=parts,
            detail="current",
        )


# --- rule 3's grades ------------------------------------------------------------------------------


def test_a_grade_is_current_only_when_its_block_still_carries_its_fingerprint() -> None:
    grade = Grade(
        reference="block-a",
        category="risk",
        grade="sound",
        reason="checked",
        fingerprint="sha256:same",
        binding=_binding(7),
        made_at=AT,
    )
    block = BlockIdentity(kind="invariant", record="INV-AAA", revision=3)
    current = grade_staleness(
        Grades(leaf_id="leaf", grades=(grade,)),
        {"block-a": BlockFingerprint.computed(block, "sha256:same")},
    )
    assert current[0].stale is False
    changed = grade_staleness(
        Grades(leaf_id="leaf", grades=(grade,)),
        {"block-a": BlockFingerprint.computed(block, "sha256:other")},
    )
    assert changed[0].stale is True
    assert "sha256:same is not sha256:other" in (changed[0].reason or "")
    uncomputable = grade_staleness(
        Grades(leaf_id="leaf", grades=(grade,)),
        {"block-a": BlockFingerprint.uncomputable(block, "the blob is not held")},
    )
    assert uncomputable[0].stale is True and uncomputable[0].reason == "the blob is not held"
    absent = grade_staleness(Grades(leaf_id="leaf", grades=(grade,)), {})
    assert absent[0].stale is True
    assert "cannot be computed" in (absent[0].reason or "")


# --- rule 6: the judgment sort --------------------------------------------------------------------


def test_the_judgment_sort_orders_by_grade_then_priority_and_puts_the_ungraded_last() -> None:
    blocks = (
        GradedBlock(reference="block-a", grade="thin"),
        GradedBlock(reference="block-b"),
        GradedBlock(reference="block-c", grade="sound", stale=True),
        GradedBlock(reference="block-d", grade="thin"),
        GradedBlock(reference="block-e", grade="wrong"),
    )
    order = judgment_sort((("the change", blocks),), scale=SCALE)[0]
    assert [block.reference for block in order.blocks] == [
        "block-c",
        "block-a",
        "block-d",
        "block-e",
        "block-b",
    ]
    assert order.blocks[0].stale is True
    assert [block.reference for block in order.ungraded] == ["block-b"]
    assert order.ungraded_label == "ungraded (1)"
    assert order.subject == "the change"


def test_subjects_keep_priority_and_a_grade_off_the_scale_is_never_promoted() -> None:
    first = (GradedBlock(reference="a-1", grade="retired-grade"), GradedBlock(reference="a-2"))
    second = (GradedBlock(reference="b-1", grade="sound"),)
    orders = judgment_sort((("first", first), ("second", second)), scale=SCALE)
    assert [order.subject for order in orders] == ["first", "second"]
    assert [block.reference for block in orders[0].blocks] == ["a-1", "a-2"]
    assert orders[0].ungraded_label == "ungraded (2)"
    assert orders[1].blocks[0].reference == "b-1"


def test_a_part_report_with_no_stale_part_regenerates_nothing_and_says_so() -> None:
    manifest = _manifest(_claim(_blob_basis()))
    steady = FakeReads(content={("code-candidate", CODE_CANDIDATE, "src/app.py", None): "sha256:x"})
    report = part_staleness(manifest, manifest.evidence.claims, _target(steady))
    request = regeneration_request(report, ())
    assert request.label == "Regenerate stale (0 parts · 0 grades)"
    assert "No part is stale" in request.message
    assert "No grade is stale" in request.message
    assert isinstance(report, PartStaleness)
