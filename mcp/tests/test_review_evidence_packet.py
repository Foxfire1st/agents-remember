"""MIK-R102: comparison-bound evidence, bounded investigation and the prepared seam.

Each case builds a real comparison -- four Git trees written into two temporary repositories -- and
asserts one property the approved packet names. The memory tree holds the *landed* schemas: an
invariant record with no relationship of its own, a family that owns ``members``, a file sidecar
that owns ``realizes`` and ``proves``, a decision that owns its ``links``, and a history file that
owns its rows. A fixture with an invented field would prove nothing about these owners.

The cases are the round-1 findings' acceptance evidence, one section per finding:

1. F01 identity: a foreign repository, task, leaf, number, ref or conversion is not this comparison;
2. F02 the effective converted base, with the stored base named beside it;
3. F03 empty, past-the-end and past-the-decoded-prefix reads, and a continuation that progresses;
4. F04 byte, page, path and record budgets measured on the work actually done;
5. F05 the landed relationship owners, typed edges and preserved uncertainty;
6. F06 an admitted supplemental source bound to the bytes examined;
7. F07 an authority label earned by the kind and state of its basis;
8. F08 the landed payload prepared end to end, with addresses a consumer opens;
9. F16 counters read from the receipts at two sizes, and the caps at their boundary.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application import review_evidence_read as reader
from agents_remember.application.review_evidence_adapter import (
    NAME_FAMILY,
    NAME_JUDGMENTS,
    NAME_PROOFS,
    NAME_SOURCES,
    consume_address,
    prepare_evidence,
)
from agents_remember.application.review_evidence_claims import resolve_claim
from agents_remember.application.review_evidence_packet import (
    PacketBindingMismatch,
    SideState,
    build_evidence_packet,
    require_same_binding,
    same_comparison,
)
from agents_remember.application.review_evidence_read import (
    DEFAULT_READ_LIMITS,
    MAX_PAGE_LINES,
    measured_limits,
    read_bound_source,
)
from agents_remember.application.review_evidence_relations import read_bound_relationships as follow
from agents_remember.application.review_supplemental_evidence import (
    MAX_SUPPLEMENTAL_BYTES,
    admit_supplemental,
    capture_supplemental,
    reread_supplemental,
)
from agents_remember.application.review_tree_comparison import reopened_trees, write_files_tree
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    ClaimClassification,
    ClaimMap,
    EvidenceClaim,
    EvidenceCollection,
    EvidenceLimitMeasurement,
    EvidenceMap,
    EvidencePacket,
)
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.models.knowledge.review_trees import (
    ReviewConvertedBase,
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from pydantic import ValidationError

AT = "2026-10-18T00:00:00Z"
TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L102"
INVARIANT = "INV-AAAAAA"
SIBLING = "INV-BBBBBB"
FAMILY = "FAM-CCCCCC"
DECISION = "DEC-DDDDDD"
PROOF = "PRF-EEEEEE"
REALIZATION = "RLZ-FFFFFF"


def _repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _invariant(identifier: str, statement: str, revision: int = 1) -> dict[str, object]:
    return {
        "schema": "ar-invariant/v1",
        "id": identifier,
        "origin": {"task": "260928-MIK", "leaf": LEAF},
        "revision": revision,
        "status": "accepted",
        "statement": statement,
        "applicability": "Every review comparison.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
    }


def _family(revision: int = 1) -> dict[str, object]:
    return {
        "schema": "ar-family/v1",
        "id": FAMILY,
        "origin": {"task": "260928-MIK", "leaf": LEAF},
        "revision": revision,
        "status": "accepted",
        "title": "Comparison-bound evidence",
        "guarantee": "Every realization of the family is read at the comparison's own trees.",
        "members": [INVARIANT, SIBLING],
        "routes": ["mcp/src/agents_remember"],
        "admission": "legacy-unassessed",
    }


def _sidecar() -> dict[str, object]:
    return {
        "schema": "ar-onboarding-file/v1",
        "path": "mcp/src/agents_remember/application/review_evidence.py",
        "references": {},
        "realizes": [
            {
                "id": REALIZATION,
                "invariant": INVARIANT,
                "anchor": {
                    "locator": {"kind": "symbol", "name": "EvidencePacket"},
                    "blob": "b" * 40,
                    "content": "sha256:" + "1" * 64,
                },
                "role": "enforcement",
                "rationale": "The packet is the enforcement of the invariant.",
            }
        ],
        "proves": [
            {
                "id": PROOF,
                "invariant": SIBLING,
                "anchor": {
                    "locator": {"kind": "symbol", "name": "test_the_packet"},
                    "blob": "c" * 40,
                    "content": "sha256:" + "2" * 64,
                },
                "facet": "a packet names one comparison",
            }
        ],
    }


def _decision() -> dict[str, object]:
    return {
        "schema": "ar-decision/v1",
        "id": DECISION,
        "origin": {"task": "260928-MIK"},
        "revision": 1,
        "status": "active",
        "context": "Evidence is bound to one comparison.",
        "alternatives": [
            {"option": "Bind the comparison", "status": "chosen", "reason": "One owner."}
        ],
        "consequences": ["One owner per relationship."],
        "decider": "Developer",
        "supersedes": [],
        "links": [
            {"relation": "explains", "target": INVARIANT},
            {"relation": "constrains", "target": FAMILY},
        ],
        "admission": "legacy-unassessed",
    }


def _history(rows: list[dict[str, object]], leaf: str = LEAF) -> dict[str, object]:
    return {"schema": "ar-history/v1", "leaf": leaf, "closed": False, "rows": rows}


MEMORY_FILES: dict[str, bytes] = {
    f"knowledge/invariants/{INVARIANT}-packet.json": json.dumps(
        _invariant(INVARIANT, "one")
    ).encode(),
    f"knowledge/invariants/{SIBLING}-bounded.json": json.dumps(_invariant(SIBLING, "two")).encode(),
    f"knowledge/families/{FAMILY}-evidence.json": json.dumps(_family()).encode(),
    f"knowledge/decisions/{DECISION}-binding.json": json.dumps(_decision()).encode(),
    "onboarding/mcp/src/agents_remember/application/review_evidence.py.json": json.dumps(
        _sidecar()
    ).encode(),
    f"knowledge/history/{LEAF}.json": json.dumps(
        _history(
            [
                {
                    "id": "ROW-AAAAA1",
                    "subject": INVARIANT,
                    "disposition": "no_impact",
                    "reason": "Only prose moved.",
                    "items": [],
                    "covers": [],
                    "revision": 1,
                },
                {
                    "id": "ROW-AAAAA2",
                    "subject": FAMILY,
                    "disposition": "no_impact",
                    "reason": "Members unchanged.",
                    "items": [],
                    "examined": [{"id": INVARIANT, "revision": 1}],
                },
            ]
        )
    ).encode(),
}


@pytest.fixture()
def world(tmp_path: Path) -> dict[str, object]:
    code = _repository(tmp_path / "code")
    memory = _repository(tmp_path / "memory")
    base = write_files_tree(
        code,
        {"src/app.py": b"def run():\n    return 1\n", "docs/blob.bin": b"head\x00tail"},
    )
    candidate = write_files_tree(
        code,
        {"src/app.py": b"def run():\n    return 2\n", "docs/blob.bin": b"head\x00tail"},
    )
    memory_base = write_files_tree(
        memory,
        {
            "knowledge/invariants/INV-AAAAAA-packet.json": json.dumps(
                _invariant(INVARIANT, "superseded statement")
            ).encode()
        },
    )
    memory_candidate = write_files_tree(memory, MEMORY_FILES)
    converted = write_files_tree(
        memory, {**MEMORY_FILES, "knowledge/converted.txt": b"converted\n"}
    )
    record = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=3,
        code_base=ReviewTreeSide(repository=str(code), tree=base),
        code_candidate=ReviewTreeSide(repository=str(code), tree=candidate),
        memory_base=ReviewTreeSide(repository=str(memory), tree=memory_base),
        memory_candidate=ReviewTreeSide(repository=str(memory), tree=memory_candidate),
        recorded_at=AT,
    )
    return {
        "code": code,
        "memory": memory,
        "record": record,
        "trees": reopened_trees(tmp_path / "coordination", record, None),
        "base": base,
        "candidate": candidate,
        "memory_base": memory_base,
        "memory_candidate": memory_candidate,
        "converted": converted,
    }


def _converted(record: ReviewTreeComparisonRecord, tree: str) -> ReviewTreeComparisonRecord:
    return record.model_copy(
        update={
            "converted_base": ReviewConvertedBase(
                commit="f" * 40, version="ar-review-conversion/v1", code_commit="e" * 40, tree=tree
            )
        }
    )


def _map(**over: object) -> EvidenceMap:
    values: dict[str, object] = {"coverage": "the selected change, nothing wider"}
    values.update(over)
    return EvidenceMap(**values)  # type: ignore[arg-type]


def _packet(record: ReviewTreeComparisonRecord, **over: object) -> EvidencePacket:
    arguments: dict[str, object] = {
        "repository_id": "agents-remember",
        "scope": "the change to src/app.py",
        "question": "why did run() return 2?",
        "code_base": SideState("available"),
        "code_candidate": SideState("available"),
        "memory_before": SideState("available", index_state="complete"),
        "memory_after": SideState("available", index_state="complete"),
        "evidence_map": _map(),
        "limits": DEFAULT_READ_LIMITS,
        "recorded_at": AT,
    }
    arguments.update(over)
    return build_evidence_packet(record, **arguments)  # type: ignore[arg-type]


# --- F01: the comparison's full identity ---------------------------------------------------------


def test_f01_a_foreign_repository_with_equal_trees_is_never_this_comparison(world) -> None:
    record = world["record"]
    packet = _packet(record)
    assert same_comparison(packet, record)
    require_same_binding(packet, record)

    moved = record.model_copy(
        update={
            "code_candidate": ReviewTreeSide(
                repository="/another/repository", tree=record.code_candidate.tree
            )
        }
    )
    assert not same_comparison(packet, moved)
    with pytest.raises(PacketBindingMismatch):
        require_same_binding(packet, moved)

    for field, value in (
        ("task_id", "other-task"),
        ("leaf_id", "other-leaf"),
        ("number", 4),
    ):
        other = record.model_copy(update={field: value})
        assert not same_comparison(packet, other), field
    ref_moved = record.model_copy(
        update={"code_candidate": record.code_candidate.model_copy(update={"ref": "refs/other"})}
    )
    assert not same_comparison(packet, ref_moved)


def test_f01_a_different_conversion_is_a_different_comparison(world) -> None:
    record = world["record"]
    packet = _packet(_converted(record, str(world["converted"])))
    assert same_comparison(packet, _converted(record, str(world["converted"])))
    recompressd = _converted(record, str(world["converted"])).model_copy(
        update={
            "converted_base": ReviewConvertedBase(
                commit="a" * 40,
                version="ar-review-conversion/v1",
                code_commit="e" * 40,
                tree=str(world["converted"]),
            )
        }
    )
    assert not same_comparison(packet, recompressd)


# --- F02: the effective converted base ------------------------------------------------------------


def test_f02_reads_the_effective_converted_tree_and_names_the_stored_one(world) -> None:
    record = _converted(world["record"], str(world["converted"]))
    receipt = read_bound_source(record, "memory-base", "knowledge/converted.txt")
    assert receipt.state == "present"
    assert receipt.text == "converted"
    assert receipt.tree == str(world["converted"])
    assert receipt.stored_tree == world["memory_base"]
    assert receipt.object_id and receipt.object_id != ""

    relations = follow(record, "memory-base", f"knowledge/families/{FAMILY}-evidence.json")
    assert relations.state == "records"
    assert relations.tree == str(world["converted"])
    assert relations.stored_tree == world["memory_base"]
    assert {edge.record_id for edge in relations.edges} >= {INVARIANT, SIBLING}


# --- F03: empty, past-the-end and past-the-prefix reads -------------------------------------------


def test_f03_an_existing_empty_file_is_measured_empty_not_absent(world, tmp_path: Path) -> None:
    code = world["code"]
    record = world["record"].model_copy(
        update={
            "code_candidate": ReviewTreeSide(
                repository=str(code),
                tree=write_files_tree(code, {"empty.txt": b""}),
            )
        }
    )
    receipt = read_bound_source(record, "code-candidate", "empty.txt")
    assert receipt.state == "present"
    assert receipt.text == ""
    assert receipt.object_id is not None
    assert "measured empty" in receipt.detail


def test_f03_a_range_past_the_end_is_a_measured_absence_with_the_real_line_count(world) -> None:
    receipt = read_bound_source(
        world["record"], "code-candidate", "src/app.py", start_line=9, end_line=9
    )
    assert receipt.state == "absent"
    assert "2 line(s)" in receipt.detail


def _continuation(text: str) -> tuple[int, int]:
    """The byte offset and the line that offset begins, as a continuation states them."""

    parts = dict(part.split("=", 1) for part in text.split(";"))
    return int(parts["start_byte"]), int(parts["resume_line"])


def test_f03_a_range_past_the_decoded_prefix_is_truncated_and_its_continuation_progresses(
    world,
) -> None:
    code = world["code"]
    long_text = "".join(f"line {number}\n" for number in range(1, 200_001)).encode()
    tree = write_files_tree(code, {"long.txt": long_text})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(code), tree=tree)}
    )
    receipt = read_bound_source(
        record, "code-candidate", "long.txt", start_line=150_000, end_line=150_001
    )
    assert receipt.state == "truncated"
    assert receipt.limit == "scan_bytes"
    assert receipt.continuation is not None
    assert receipt.next_byte and receipt.next_byte > 0

    offset, resume_line = _continuation(receipt.continuation)
    for _attempt in range(40):
        moved = read_bound_source(
            record,
            "code-candidate",
            "long.txt",
            start_line=150_000,
            end_line=150_001,
            start_byte=offset,
            resume_line=resume_line,
        )
        if moved.state == "present":
            assert moved.text is not None and moved.text.startswith("line 150000")
            assert moved.returned_range == (150_000, 150_001)
            return
        if moved.state == "refused":
            assert moved.refusal_code == "read-beyond-limit"
            return
        assert moved.state == "truncated", moved.state
        next_offset, resume_line = _continuation(moved.continuation)  # type: ignore[arg-type]
        assert next_offset > offset
        offset = next_offset
    pytest.fail("the bounded continuation never reached the requested line")


def test_f03_a_long_utf8_line_stays_text_and_is_never_reported_absent(world) -> None:
    code = world["code"]
    payload = ("héllo-" * 400).encode() + b"\n"
    tree = write_files_tree(code, {"wide.txt": payload})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(code), tree=tree)}
    )
    receipt = read_bound_source(record, "code-candidate", "wide.txt", start_line=1, end_line=1)
    assert receipt.state in {"present", "truncated"}
    assert receipt.text is not None and "héllo" in receipt.text


# --- F04: budgets bound the work and the packet populations --------------------------------------


def test_f04_the_read_bounds_bound_bytes_examined_at_two_sizes(world, tmp_path: Path) -> None:
    code = world["code"]
    small = write_files_tree(code, {"small.txt": b"one\ntwo\n"})
    large = write_files_tree(code, {"small.txt": b"one\ntwo\n" * 20_000})
    for tree, expected_min in ((small, 1), (large, 1)):
        record = world["record"].model_copy(
            update={"code_candidate": ReviewTreeSide(repository=str(code), tree=tree)}
        )
        receipt = read_bound_source(record, "code-candidate", "small.txt")
        assert receipt.examined_bytes >= expected_min
        assert (
            receipt.examined_bytes
            <= DEFAULT_READ_LIMITS.bytes_per_read + DEFAULT_READ_LIMITS.bytes_per_read // 2
        )
        assert receipt.returned_bytes <= receipt.examined_bytes
        assert receipt.page == 0
    limited = measured_limits(
        EvidenceLimitMeasurement(
            inputs=(("small", 2), ("large", 40960)),
            observed_bytes=2,
            observed_paths=1,
            observed_records=0,
            observed_depth=1,
            elapsed_ms=1,
        ),
        limits=DEFAULT_READ_LIMITS,
    )
    assert limited.bytes_per_read <= DEFAULT_READ_LIMITS.bytes_per_read
    assert limited.paths_per_packet <= DEFAULT_READ_LIMITS.paths_per_packet


def test_f04_a_symlink_is_bounded_like_any_other_bytes(world) -> None:
    code = world["code"]
    (code / "target.txt").write_text("x" * 4096)
    (code / "link").symlink_to("target.txt")
    subprocess.run(["git", "-C", str(code), "add", "target.txt", "link"], check=True)
    tree = subprocess.run(
        ["git", "-C", str(code), "write-tree"], check=True, capture_output=True, text=True
    ).stdout.strip()
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(code), tree=tree)}
    )
    receipt = read_bound_source(record, "code-candidate", "link")
    assert receipt.state == "symlink"
    assert receipt.returned_bytes <= DEFAULT_READ_LIMITS.bytes_per_read
    assert receipt.next_byte is None or receipt.next_byte == receipt.returned_bytes


def test_f04_a_packet_over_its_path_or_record_cap_is_refused(world) -> None:
    tight = DEFAULT_READ_LIMITS.model_copy(
        update={"paths_per_packet": 2, "records_per_collection": 1}
    )
    collections = {
        NAME_SOURCES: EvidenceCollection(
            name=NAME_SOURCES,
            producer="review-comparison:test",
            population="three paths",
            state="present",
            count=3,
            items=tuple({"path": f"p{number}"} for number in range(3)),
        )
    }
    with pytest.raises(ValueError, match="distinct path"):
        _packet(world["record"], collections=collections, limits=tight)
    many = {
        NAME_SOURCES: EvidenceCollection(
            name=NAME_SOURCES,
            producer="review-comparison:test",
            population="two records",
            state="present",
            count=2,
            items=({"path": "a"}, {"path": "b"}),
        )
    }
    with pytest.raises(ValueError, match="per collection"):
        _packet(world["record"], collections=many, limits=tight)


def test_f04_a_truncated_relationship_population_names_its_continuation(world) -> None:
    tight = DEFAULT_READ_LIMITS.model_copy(
        update={"records_per_collection": 1, "relationship_depth": 1}
    )
    relations = follow(
        world["record"],
        "memory-candidate",
        f"knowledge/families/{FAMILY}-evidence.json",
        limits=tight,
    )
    assert relations.state == "records"
    assert len(relations.edges) == 1
    assert relations.limit == "records_per_collection"
    assert relations.continuation is not None


# --- F05: the landed owners' relationships -------------------------------------------------------


def test_f05_a_family_owns_its_members_and_a_decision_owns_its_links(world) -> None:
    record = world["record"]
    family = follow(record, "memory-candidate", f"knowledge/families/{FAMILY}-evidence.json")
    owned = {(edge.record_id, edge.relation, edge.direction) for edge in family.edges}
    assert (INVARIANT, "members", "owns") in owned
    assert (SIBLING, "members", "owns") in owned
    assert family.from_revision == 1
    assert family.from_record == FAMILY

    decision = follow(record, "memory-candidate", f"knowledge/decisions/{DECISION}-binding.json")
    linked = {(edge.record_id, edge.relation) for edge in decision.edges}
    assert (INVARIANT, "links:explains") in linked
    assert (FAMILY, "links:constrains") in linked


def test_f05_a_sidecar_and_a_history_row_are_read_from_their_own_owners(world) -> None:
    record = world["record"]
    sidecar = follow(
        record,
        "memory-candidate",
        "onboarding/mcp/src/agents_remember/application/review_evidence.py.json",
    )
    assert {(edge.record_id, edge.relation) for edge in sidecar.edges} >= {
        (INVARIANT, "realizes"),
        (SIBLING, "proves"),
    }

    invariant = follow(record, "memory-candidate", f"knowledge/invariants/{INVARIANT}-packet.json")
    recorded = {(edge.relation, edge.path, edge.row_id, edge.attempt) for edge in invariant.edges}
    assert ("members", f"knowledge/families/{FAMILY}-evidence.json", None, None) in recorded
    assert (
        "history",
        f"knowledge/history/{LEAF}.json",
        "ROW-AAAAA1",
        1,
    ) in recorded
    assert any(edge.direction == "recorded-by" for edge in invariant.edges)


def test_f05_a_lost_tree_is_unavailable_and_never_a_measured_absence(world) -> None:
    record = world["record"].model_copy(
        update={"memory_candidate": ReviewTreeSide(repository=str(world["memory"]), tree="9" * 40)}
    )
    family = follow(record, "memory-candidate", f"knowledge/families/{FAMILY}-evidence.json")
    assert family.state == "refused"
    assert family.refusal_code == "record-unreadable"

    invariant = follow(record, "memory-candidate", f"knowledge/invariants/{INVARIANT}-packet.json")
    assert invariant.state == "refused"


def test_f05_an_unreadable_owner_file_is_invalid_not_absent(world, tmp_path: Path) -> None:
    memory = world["memory"]
    tree = write_files_tree(
        memory,
        {
            f"knowledge/families/{FAMILY}-evidence.json": b"{not json",
            f"knowledge/invariants/{INVARIANT}-packet.json": json.dumps(
                _invariant(INVARIANT, "one")
            ).encode(),
        },
    )
    record = world["record"].model_copy(
        update={"memory_candidate": ReviewTreeSide(repository=str(memory), tree=tree)}
    )
    relations = follow(record, "memory-candidate", f"knowledge/invariants/{INVARIANT}-packet.json")
    invalid = [edge for edge in relations.edges if edge.state == "invalid"]
    assert invalid, "an owner file that cannot be read is reported, not skipped"
    assert any(edge.path and FAMILY in edge.path for edge in invalid)


# --- F06: admitted supplemental sources ----------------------------------------------------------


def _task_root(tmp_path: Path) -> Path:
    root = tmp_path / "task"
    (root / "requirements").mkdir(parents=True)
    (root / "notes").mkdir()
    (root / "requirements" / "MIK-R102.md").write_text("# R102\n")
    return root


def test_f06_a_supplemental_receipt_binds_the_bytes_it_admits(tmp_path: Path) -> None:
    root = _task_root(tmp_path)
    receipt = capture_supplemental(
        root,
        "requirements/MIK-R102.md",
        receipt_id="sup-1",
        capture_identity="capture-1",
        establishes="the approved packet",
        producer="architect",
        authority="approved",
        source_version="v1",
    )
    assert receipt.read_state == "present"
    assert receipt.canonical_source == "requirements/MIK-R102.md"
    assert receipt.digest is not None and receipt.byte_size == len("# R102\n")

    moved = reread_supplemental(
        receipt,
        task_root=root,
        receipt_id="sup-2",
        capture_identity="capture-2",
        source_version="v2",
    )
    assert moved.read_state == "present"
    assert moved.digest == receipt.digest


def test_f06_a_changed_source_answers_a_successor_and_never_reuses_the_old_identity(
    tmp_path: Path,
) -> None:
    root = _task_root(tmp_path)
    first = capture_supplemental(
        root,
        "requirements/MIK-R102.md",
        receipt_id="sup-1",
        capture_identity="capture-1",
        establishes="the approved packet",
        authority="approved",
        source_version="v1",
    )
    (root / "requirements" / "MIK-R102.md").write_text("# R102, revised\n")
    successor = reread_supplemental(
        first, task_root=root, receipt_id="sup-2", capture_identity="capture-2"
    )
    assert successor.read_state == "changed"
    assert successor.digest != first.digest
    assert successor.canonical_source == first.canonical_source
    assert successor.source_version is None, "a version the new bytes do not carry is not copied"
    assert successor.limitation is not None


def test_f06_a_foreign_symlinked_or_oversized_source_is_refused(tmp_path: Path) -> None:
    root = _task_root(tmp_path)
    assert admit_supplemental(root, "../outside.md").refusal_code == "foreign-source"
    assert admit_supplemental(root, "/etc/hostname").refusal_code == "foreign-source"
    assert admit_supplemental(root, "memory/onboarding/x.md").refusal_code == "foreign-source"
    (root / "notes" / "big.md").write_text("x" * (MAX_SUPPLEMENTAL_BYTES + 1))
    oversized = admit_supplemental(root, "notes/big.md")
    assert oversized.refusal_code == "read-beyond-limit"
    (root / "notes" / "link.md").symlink_to(root / "requirements" / "MIK-R102.md")
    assert admit_supplemental(root, "notes/link.md").refusal_code == "foreign-source"


def test_f06_a_lost_source_keeps_the_earlier_receipts_digest(tmp_path: Path) -> None:
    root = _task_root(tmp_path)
    receipt = capture_supplemental(
        root,
        "notes/ruling.md"
        if (root / "notes" / "ruling.md").exists()
        else "requirements/MIK-R102.md",
        receipt_id="sup-1",
        capture_identity="capture-1",
        establishes="a ruling",
    )
    (root / receipt.canonical_source).unlink()
    lost = reread_supplemental(
        receipt, task_root=root, receipt_id="sup-2", capture_identity="capture-2"
    )
    assert lost.read_state == "unavailable"
    assert lost.digest is None
    assert receipt.digest is not None


# --- F07: authority earned by a typed basis ------------------------------------------------------


def _basis(**over: object) -> ClaimBasis:
    values: dict[str, object] = {
        "kind": "tree-blob",
        "reference": "code-candidate:src/app.py",
        "state": "present",
        "side": "code-candidate",
        "tree": "a" * 40,
        "path": "src/app.py",
    }
    values.update(over)
    return ClaimBasis(**values)  # type: ignore[arg-type]


def _claim(classification: ClaimClassification, bases: tuple[ClaimBasis, ...]) -> EvidenceClaim:
    return EvidenceClaim(
        claim_id="c1",
        authoring_revision="rev-1",
        classification=classification,
        subject="run() returns 2",
        applicability="the selected change",
        statement="the change returns 2",
        bases=bases,
    )


def test_f07_a_recorded_fact_needs_a_mechanical_basis() -> None:
    with pytest.raises(ValidationError, match="mechanical basis"):
        _claim("recorded-fact", (_basis(kind="comparison-field"),))
    claim = _claim("recorded-fact", (_basis(),))
    assert claim.classification == "recorded-fact"


def test_f07_an_authored_judgment_needs_a_judgment_row_and_an_established_proof() -> None:
    with pytest.raises(ValidationError, match="judgment row"):
        _claim("authored-judgment", (_basis(),))
    with pytest.raises(ValidationError, match="execution"):
        _claim(
            "recorded-fact",
            (
                _basis(
                    kind="proof-entry",
                    proof_id=PROOF,
                    record_id=SIBLING,
                    execution="unestablished",
                ),
            ),
        )
    supported = _claim(
        "authored-judgment",
        (
            _basis(
                kind="judgment-row",
                record_id=INVARIANT,
                row_id="ROW-AAAAA1",
                attempt=1,
            ),
        ),
    )
    assert supported.classification == "authored-judgment"


def test_f07_a_present_label_on_an_absent_basis_is_unresolved(world) -> None:
    packet = _packet(world["record"])
    claim = _claim(
        "authored-judgment",
        (
            _basis(
                kind="judgment-row",
                reference=f"memory-candidate:knowledge/history/{LEAF}.json#ROW-AAAAA1",
                side="memory-candidate",
                tree=world["memory_candidate"],
                path=f"knowledge/history/{LEAF}.json",
                record_id=INVARIANT,
                row_id="ROW-AAAAA1",
                attempt=1,
            ),
        ),
    )
    with_packet = packet.model_copy(update={"claims": ClaimMap(claims=(claim,))})
    resolution = resolve_claim(with_packet, "c1")
    assert not resolution.resolvable
    assert "judgments" in resolution.unresolved[0]


# --- F08: the prepared seam, end to end ---------------------------------------------------------


def test_f08_the_landed_payload_prepares_a_packet_with_addresses_a_consumer_opens(world) -> None:
    prepared = prepare_evidence(
        world["trees"],
        scope="the change to src/app.py",
        question="why did run() return 2?",
        evidence_map=_map(),
        paths=(
            f"knowledge/invariants/{INVARIANT}-packet.json",
            f"knowledge/families/{FAMILY}-evidence.json",
        ),
        family_path=f"knowledge/families/{FAMILY}-evidence.json",
    )
    names = {collection.name: collection for collection in prepared.packet.collections}
    assert names[NAME_SOURCES].state == "present"
    assert names[NAME_SOURCES].count == 2
    assert names[NAME_FAMILY].state == "present"
    assert names[NAME_JUDGMENTS].state == "present"
    assert names[NAME_JUDGMENTS].count == 2
    assert names[NAME_PROOFS].state == "present"
    assert prepared.addresses

    rows = [address for address in prepared.addresses if address.kind == "judgment-row"]
    assert {address.row_id for address in rows} == {"ROW-AAAAA1", "ROW-AAAAA2"}
    assert all(address.attempt == 1 for address in rows)
    read = consume_address(world["record"], prepared, rows[0].address_id)
    assert read.state == "resolved"
    assert read.read_state == "present"
    assert read.object_id


def test_f08_an_unselected_scope_stays_stated_and_the_state_is_honest(world) -> None:
    prepared = prepare_evidence(
        world["trees"],
        scope="the change to src/app.py",
        question="why did run() return 2?",
        evidence_map=_map(),
    )
    assert prepared.state == "partial"
    assert prepared.limitation is not None
    names = {collection.name: collection.state for collection in prepared.packet.collections}
    assert names[NAME_SOURCES] == "not-selected"
    assert names[NAME_FAMILY] == "not-selected"


# --- F16: measured evidence at two sizes --------------------------------------------------------


def test_f16_two_sizes_measure_the_read_bounds(world, tmp_path: Path) -> None:
    code = world["code"]
    measured: list[tuple[int, int, int]] = []
    for size in (1, 200):
        body = "".join(f"line {number}\n" for number in range(size)).encode()
        tree = write_files_tree(code, {"sized.txt": body})
        record = world["record"].model_copy(
            update={"code_candidate": ReviewTreeSide(repository=str(code), tree=tree)}
        )
        receipt = read_bound_source(record, "code-candidate", "sized.txt")
        assert receipt.state == "present"
        assert receipt.page == 0
        assert receipt.examined_bytes == len(body)
        assert receipt.returned_bytes == len(body.rstrip(b"\n"))
        measured.append((size, receipt.examined_bytes, receipt.returned_bytes))
    assert measured[0][1] < measured[1][1]
    assert all(examined - returned <= 1 for _size, examined, returned in measured)


def test_f16_the_page_bound_is_refused_beyond_its_admitted_pages(world, tmp_path: Path) -> None:
    code = world["code"]
    body = "".join(f"line {number}\n" for number in range(MAX_PAGE_LINES * 3)).encode()
    tree = write_files_tree(code, {"pages.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(code), tree=tree)}
    )
    first = read_bound_source(record, "code-candidate", "pages.txt", start_line=1)
    assert first.state in {"present", "truncated"}
    beyond = read_bound_source(
        record, "code-candidate", "pages.txt", start_line=1, page=DEFAULT_READ_LIMITS.pages_per_read
    )
    assert beyond.state == "refused"
    assert beyond.refusal_code == "read-beyond-limit"


# --- round 2: the remaining fixed-list properties ------------------------------------------------


def test_f01_r2_no_identity_field_alone_returns_current(world) -> None:
    record = _record_with(world, "b" * 40)
    kept = FrameBinding.from_record(
        record, comparison_owner="agents-remember", comparison_reference="review-comparison:x"
    )
    variants = [
        record.model_copy(
            update={"code_base": record.code_base.model_copy(update={"commit": "1" * 40})}
        ),
        record.model_copy(
            update={
                "code_candidate": record.code_candidate.model_copy(update={"ref": "refs/other"})
            }
        ),
        record.model_copy(
            update={"memory_base": record.memory_base.model_copy(update={"commit": "2" * 40})}
        ),
        record.model_copy(
            update={
                "memory_candidate": record.memory_candidate.model_copy(update={"ref": "refs/b"})
            }
        ),
    ]
    for variant in variants:
        other = FrameBinding.from_record(
            variant, comparison_owner="agents-remember", comparison_reference="review-comparison:x"
        )
        assert not kept.same_comparison(other)


def _record_with(world, code_candidate: str):
    return world["record"].model_copy(
        update={
            "code_base": ReviewTreeSide(
                repository=str(world["code"]), tree=world["base"], commit="9" * 40
            ),
            "code_candidate": ReviewTreeSide(
                repository=str(world["code"]),
                tree=code_candidate,
                commit="8" * 40,
                ref="refs/ar/review/x/1",
            ),
            "memory_base": ReviewTreeSide(
                repository=str(world["memory"]), tree=world["memory_base"], commit="7" * 40
            ),
            "memory_candidate": ReviewTreeSide(
                repository=str(world["memory"]),
                tree=world["memory_candidate"],
                commit="6" * 40,
                ref="refs/ar/review/x/1",
            ),
        }
    )


def test_f03_r2_a_byte_capped_prefix_of_a_long_line_is_truncated(world) -> None:
    code = world["code"]
    line = "x" * 200 + "\n"
    tree = write_files_tree(code, {"one-line.txt": line.encode()})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(code), tree=tree)}
    )
    tight = DEFAULT_READ_LIMITS.model_copy(update={"bytes_per_read": 16})
    first = read_bound_source(
        record, "code-candidate", "one-line.txt", start_line=1, end_line=1, limits=tight
    )
    assert first.state == "truncated"
    assert first.limit == "bytes_per_read"
    assert first.remaining_state == "unknown"
    assert first.continuation is not None

    collected = first.text or ""
    offset, resume_line = _continuation(first.continuation)
    for _attempt in range(40):
        moved = read_bound_source(
            record,
            "code-candidate",
            "one-line.txt",
            start_line=resume_line,
            end_line=resume_line,
            start_byte=offset,
            resume_line=resume_line,
            limits=tight,
        )
        collected += moved.text or ""
        if moved.state == "present":
            break
        assert moved.state == "truncated", moved.state
        assert moved.continuation is not None
        offset, resume_line = _continuation(moved.continuation)
    assert collected == line.rstrip("\n")


def test_f04_r2_a_resumed_read_reports_the_prefix_it_consumed(world) -> None:
    code = world["code"]
    body = "".join(f"line {number}\n" for number in range(1, 40_000)).encode()
    tree = write_files_tree(code, {"stream.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(code), tree=tree)}
    )
    tight = DEFAULT_READ_LIMITS.model_copy(update={"bytes_per_read": 16})
    mounted = read_bound_source(
        record,
        "code-candidate",
        "stream.txt",
        start_line=1,
        start_byte=300_000,
        resume_line=30_000,
        limits=tight,
    )
    assert mounted.state in {"present", "truncated"}
    assert mounted.examined_bytes >= 300_000


def test_f04_r2_the_reverse_roster_stops_at_the_edge_budget(world) -> None:
    tight = DEFAULT_READ_LIMITS.model_copy(
        update={"records_per_collection": 1, "relationship_depth": 1}
    )
    relations = follow(
        world["record"],
        "memory-candidate",
        f"knowledge/families/{FAMILY}-evidence.json",
        limits=tight,
    )
    assert relations.state == "records"
    assert len(relations.edges) == 1
    assert relations.continuation is not None
    assert relations.examined_bytes > 0


def test_f05_r2_a_continuation_consumes_the_next_edges(world) -> None:
    tight = DEFAULT_READ_LIMITS.model_copy(
        update={"records_per_collection": 1, "relationship_depth": 1}
    )
    first = follow(
        world["record"],
        "memory-candidate",
        f"knowledge/families/{FAMILY}-evidence.json",
        limits=tight,
    )
    second = follow(
        world["record"],
        "memory-candidate",
        f"knowledge/families/{FAMILY}-evidence.json",
        limits=tight,
        continuation=first.continuation,
    )
    assert second.state == "records"
    assert first.edges[0].record_id != second.edges[0].record_id
    assert second.edges[0].lookup_state == "resolved"
    assert second.edges[0].target_revision == 1
    assert second.edges[0].target_origin_leaf == LEAF


def test_f06_r2_the_bound_is_the_bytes_read_not_a_prior_stat(tmp_path: Path) -> None:
    root = _task_root(tmp_path)
    source = root / "requirements" / "grow.md"
    source.write_bytes(b"x" * MAX_SUPPLEMENTAL_BYTES)
    assert admit_supplemental(root, "requirements/grow.md").state == "present"
    source.write_bytes(b"x" * (MAX_SUPPLEMENTAL_BYTES + 1))
    refused = admit_supplemental(root, "requirements/grow.md")
    assert refused.refusal_code == "read-beyond-limit"
    assert refused.byte_size is not None and refused.byte_size > MAX_SUPPLEMENTAL_BYTES


def test_f07_r2_an_absent_item_earns_no_authority(world) -> None:
    prepared = prepare_evidence(
        world["trees"],
        scope="the change",
        question="why?",
        evidence_map=_map(),
        paths=(
            f"knowledge/invariants/{INVARIANT}-packet.json",
            "knowledge/absent-record.json",
        ),
    )
    sources = prepared.packet.collection(NAME_SOURCES)
    assert sources is not None
    absent = [item for item in sources.items if item.get("state") == "absent"]
    assert absent, "the preparation records the absent path as absent"
    claim = _claim(
        "recorded-fact",
        (
            _basis(
                reference="memory-candidate:knowledge/absent-record.json",
                side="memory-candidate",
                tree=world["memory_candidate"],
                path="knowledge/absent-record.json",
            ),
        ),
    )
    with_packet = prepared.packet.model_copy(update={"claims": ClaimMap(claims=(claim,))})
    resolution = resolve_claim(with_packet, "c1")
    assert not resolution.resolvable


def test_f08_r2_an_address_is_never_resolved_for_a_foreign_comparison(world) -> None:
    prepared = prepare_evidence(
        world["trees"],
        scope="the change",
        question="why?",
        evidence_map=_map(),
        paths=(f"knowledge/invariants/{INVARIANT}-packet.json",),
        family_path=f"knowledge/families/{FAMILY}-evidence.json",
    )
    address = next(entry for entry in prepared.addresses if entry.kind == "judgment-row")
    foreign = world["record"].model_copy(update={"task_id": "another-task"})
    refused = consume_address(foreign, prepared, address.address_id)
    assert refused.state == "refused"
    assert "different identity" in refused.detail
    resolved = consume_address(world["record"], prepared, address.address_id)
    assert resolved.state == "resolved"


def test_f03_r4_a_literal_continuation_crosses_a_separator_and_a_long_line(world) -> None:
    payload = b"one\n" + b"x" * 100 + b"\nlast"
    tree = write_files_tree(world["code"], {"mixed.txt": payload})
    record = _record_with(world, tree)
    limits = DEFAULT_READ_LIMITS.model_copy(update={"bytes_per_read": 16, "pages_per_read": 20})
    arguments = {"start_line": 1, "end_line": 2}
    offsets = []
    pieces = []
    for _ in range(12):
        receipt = read_bound_source(
            record, "code-candidate", "mixed.txt", limits=limits, **arguments
        )
        pieces.append(receipt.text or "")
        if receipt.state == "present":
            break
        assert receipt.state == "truncated"
        assert receipt.continuation is not None
        arguments = {
            key: int(value)
            for key, value in (part.split("=", 1) for part in receipt.continuation.split(";"))
        }
        offsets.append(arguments["start_byte"])
    else:
        pytest.fail("the literal continuation stalled")
    assert offsets == sorted(set(offsets))
    assert pieces[0] == "one"
    assert "".join(pieces[1:]) == "x" * 100


def test_f04_r4_resume_work_is_bounded_before_streaming(world, monkeypatch) -> None:
    tree = write_files_tree(world["code"], {"wide.txt": b"x" * (reader.MAX_SCAN_BYTES + 100)})
    record = _record_with(world, tree)
    actual = []
    original = reader._Blob._read

    def measured(blob, length):
        payload = original(blob, length)
        actual.append(len(payload))
        return payload

    monkeypatch.setattr(reader._Blob, "_read", measured)
    for offset in (reader.MAX_SCAN_BYTES + 1, 2 * reader.MAX_SCAN_BYTES):
        receipt = read_bound_source(
            record,
            "code-candidate",
            "wide.txt",
            start_byte=offset,
            limits=DEFAULT_READ_LIMITS.model_copy(update={"bytes_per_read": 16}),
        )
        assert receipt.state == "refused"
        assert receipt.refusal_code == "read-beyond-limit"
    assert sum(actual) == 0


def test_f05_r4_paging_covers_the_same_owned_nested_and_reverse_roster(world) -> None:
    for path in (
        f"knowledge/invariants/{INVARIANT}-packet.json",
        f"knowledge/families/{FAMILY}-evidence.json",
    ):
        whole = follow(world["record"], "memory-candidate", path)
        limits = DEFAULT_READ_LIMITS.model_copy(
            update={"records_per_collection": 1, "pages_per_read": 20}
        )
        collected = []
        cursor = None
        for _ in range(20):
            page = follow(
                world["record"], "memory-candidate", path, limits=limits, continuation=cursor
            )
            collected.extend(page.edges)
            cursor = page.continuation
            if cursor is None:
                break
        assert collected == list(whole.edges)
        if path.startswith("knowledge/invariants/"):
            assert any(edge.direction == "recorded-by" for edge in collected)


def test_f07_r4_a_claim_can_only_cite_the_inspected_range(world) -> None:
    path = f"knowledge/invariants/{INVARIANT}-packet.json"
    prepared = prepare_evidence(
        world["trees"], scope="the change", question="why?", evidence_map=_map(), paths=(path,)
    )
    for line, expected in ((1, True), (999, False)):
        claim = _claim(
            "recorded-fact",
            (
                _basis(
                    reference=f"memory-candidate:{path}#L{line}",
                    side="memory-candidate",
                    tree=world["memory_candidate"],
                    path=path,
                    start_line=line,
                    end_line=line,
                ),
            ),
        )
        packet = prepared.packet.model_copy(update={"claims": ClaimMap(claims=(claim,))})
        assert resolve_claim(packet, "c1").resolvable is expected


def test_f08_r4_a_row_address_stays_at_its_actual_owner(world) -> None:
    prepared = prepare_evidence(
        world["trees"], scope="the change", question="why?", evidence_map=_map()
    )
    address = next(entry for entry in prepared.addresses if entry.kind == "judgment-row")
    assert consume_address(world["record"], prepared, address.address_id).state == "resolved"
    foreign_path = f"knowledge/invariants/{INVARIANT}-packet.json"
    forged = replace(address, path=foreign_path, reference=f"memory-candidate:{foreign_path}")
    changed = replace(prepared, addresses=(forged,))
    assert consume_address(world["record"], changed, address.address_id).state == "unresolved"
