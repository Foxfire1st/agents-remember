"""MIK-R189 rule 6 and MIK-R102 v2 item 1: the ``intended`` authority and its enforcement.

One case per promise of the requirement sources:

* the claim model has the class, and an intention cannot rest on a tree blob alone;
* on a planned binding, a claim resting only on packet, task-document or reference receipts is
  refused as ``recorded-fact`` and as ``mechanical-signal``, and accepted as ``intended``;
* the same claim on another binding is unchanged, so existing pages are untouched;
* a claim that also cites code or memory at the master tip keeps today's kinds;
* only the three intended roots are admitted -- ``DEVELOPER-DIRECTION.md`` is not one of them;
* ``prepare_evidence`` runs the enforcement itself, on a planned binding and only there.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.review_evidence_adapter import (
    EvidencePrecondition,
    IntendedAuthorityRefusal,
    prepare_evidence,
    require_intended_authority,
    require_intended_packet,
)
from agents_remember.application.review_evidence_packet import (
    COMPARISON_OWNER,
    INTENDED_AUTHORITY,
    PLANNED_OWNER,
    SideState,
    build_evidence_packet,
    intended_receipt_ids,
    intended_source_root,
    planned_reference,
)
from agents_remember.application.review_evidence_read import DEFAULT_READ_LIMITS
from agents_remember.application.review_planned_comparison import explainer_application_ports
from agents_remember.application.review_tree_comparison import (
    ReviewTrees,
    TreeKnowledge,
    comparison_directory,
    comparison_records,
    reopened_trees,
    write_files_tree,
)
from agents_remember.application.review_tree_knowledge import read_review_trees
from agents_remember.canvas.explainer_paths import leaf_workspace
from agents_remember.canvas.explainer_service import LeafScope
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.models.knowledge.review_evidence import (
    CLAIM_CLASSIFICATIONS,
    INTENDED_CLASSIFICATION,
    ClaimBasis,
    EvidenceClaim,
    EvidenceMap,
    SupplementalReceipt,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.serving.explainer import (
    _SEND_SELECTION,
    ExplainerWiring,
    _SendRequest,
    explainer_composition,
    register_explainer_routes,
)
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember_test_support.testing.review_git_trees import CODE_FILE, build_world
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L183"
SLUG = "183_the-review-pages-explainer"
PACKET = "requirements/MIK-R189-v1-an-intent-review-before-the-work-starts.md"
DIGEST = "a" * 64


def _receipt(receipt_id: str = "packet-01", source: str = PACKET) -> SupplementalReceipt:
    return SupplementalReceipt(
        receipt_id=receipt_id,
        canonical_source=source,
        producer=f"leaf {LEAF}",
        digest=DIGEST,
        byte_size=512,
        read_state="present",
        capture_identity="a planned page's build",
        establishes="a document the intended change set rests on",
    )


def _receipt_basis(receipt: SupplementalReceipt) -> ClaimBasis:
    return ClaimBasis(
        kind="supplemental-receipt",
        reference=receipt.receipt_id,
        state="present",
        receipt_id=receipt.receipt_id,
    )


def _code_basis() -> ClaimBasis:
    return ClaimBasis(
        kind="tree-blob",
        reference="code-candidate:src/app.py",
        state="present",
        side="code-candidate",
        tree="b" * 40,
        path="src/app.py",
    )


def _memory_basis() -> ClaimBasis:
    return ClaimBasis(
        kind="knowledge-record",
        reference="memory-candidate:knowledge/invariants/INV-AAAAAA-x.json",
        state="present",
        record_id="INV-AAAAAA",
        record_revision=1,
        path="knowledge/invariants/INV-AAAAAA-x.json",
    )


def _claim(classification: str, *bases: ClaimBasis, claim_id: str = "c1") -> EvidenceClaim:
    return EvidenceClaim(
        claim_id=claim_id,
        authoring_revision="revision-1",
        classification=classification,  # type: ignore[arg-type]
        subject="the intent review's page",
        applicability="a planning leaf",
        statement="the page shows the task's intended changes before any work starts",
        bases=bases,
    )


def _enforce(
    claim: EvidenceClaim, receipt: SupplementalReceipt, owner: str = PLANNED_OWNER
) -> None:
    require_intended_authority(
        (claim,),
        supplemental=(receipt,),
        comparison_owner=owner,
        leaf_id=LEAF,
    )


# --- the class and its basis bounds (MIK-R102 v2 item 1) ---------------------------------------


def test_the_model_carries_intended_among_its_authorities() -> None:
    assert INTENDED_CLASSIFICATION == INTENDED_AUTHORITY == "intended"
    assert INTENDED_CLASSIFICATION in CLAIM_CLASSIFICATIONS


def test_an_intended_claim_cannot_rest_on_a_tree_blob_alone() -> None:
    with pytest.raises(ValidationError, match="supplemental receipt"):
        _claim("intended", _code_basis())


def test_an_intended_claim_resting_on_a_packet_receipt_is_accepted() -> None:
    receipt = _receipt()
    claim = _claim("intended", _receipt_basis(receipt))

    assert claim.classification == "intended"
    assert claim.bases[0].kind == "supplemental-receipt"


def test_the_admitted_intended_roots_are_the_three_the_rule_names() -> None:
    assert intended_source_root(PACKET, leaf_id=LEAF) == "requirements"
    assert intended_source_root(f"{SLUG}.json", leaf_id=LEAF, leaf_document=SLUG) == (
        "task-document"
    )
    assert intended_source_root(f"{SLUG}.md", leaf_id=LEAF, leaf_document=SLUG) == "task-document"
    assert intended_source_root(f"{LEAF}.json", leaf_id=LEAF) == "task-document"
    assert intended_source_root(f"notes/reference/{LEAF}/picks.md", leaf_id=LEAF) == (
        f"notes/reference/{LEAF}"
    )


def test_developer_direction_and_every_other_root_are_not_intended_sources() -> None:
    for refused in (
        "DEVELOPER-DIRECTION.md",
        "notes/planning/decisions.md",
        f"notes/reference/{LEAF}-other/picks.md",
        f"../{PACKET}",
        f"/{PACKET}",
        f"task-documents/{SLUG}.json",
    ):
        assert intended_source_root(refused, leaf_id=LEAF, leaf_document=SLUG) is None, refused


# --- enforcement on a planned binding ---------------------------------------------------------


def test_a_planned_binding_refuses_a_receipt_only_claim_as_recorded_fact() -> None:
    receipt = _receipt()

    with pytest.raises(IntendedAuthorityRefusal, match="is intended, not recorded-fact"):
        _enforce(_claim("recorded-fact", _receipt_basis(receipt)), receipt)

    assert IntendedAuthorityRefusal.reason == "intended-claim-required"
    assert issubclass(IntendedAuthorityRefusal, EvidencePrecondition)


def test_a_planned_binding_refuses_a_receipt_only_claim_as_a_mechanical_signal() -> None:
    receipt = _receipt()

    with pytest.raises(IntendedAuthorityRefusal, match="is intended, not mechanical-signal"):
        _enforce(_claim("mechanical-signal", _receipt_basis(receipt)), receipt)


def test_the_same_claim_is_accepted_as_intended() -> None:
    receipt = _receipt()

    _enforce(_claim("intended", _receipt_basis(receipt)), receipt)


def test_the_same_claim_on_another_binding_is_unchanged() -> None:
    receipt = _receipt()

    _enforce(_claim("recorded-fact", _receipt_basis(receipt)), receipt, owner=COMPARISON_OWNER)


def test_a_claim_that_also_cites_code_at_the_tip_keeps_a_recorded_fact() -> None:
    receipt = _receipt()

    _enforce(_claim("recorded-fact", _receipt_basis(receipt), _code_basis()), receipt)


def test_a_claim_that_also_cites_memory_at_the_tip_keeps_a_recorded_fact() -> None:
    receipt = _receipt()

    _enforce(_claim("recorded-fact", _receipt_basis(receipt), _memory_basis()), receipt)


def test_a_claim_resting_on_developer_direction_is_not_relabelled() -> None:
    direction = _receipt("direction", "DEVELOPER-DIRECTION.md")

    assert intended_receipt_ids((direction,), leaf_id=LEAF) == frozenset()
    _enforce(_claim("recorded-fact", _receipt_basis(direction)), direction)


def test_a_packet_level_check_refuses_the_same_claim() -> None:
    receipt = _receipt()
    record = _record(
        tree="b" * 40,
        memory_tree="c" * 40,
    )
    packet = build_evidence_packet(
        record,
        repository_id=TASK,
        scope="the intent review's page",
        question="what is the leaf meant to build?",
        code_base=_side("available"),
        code_candidate=_side("available"),
        memory_before=_side("available", index_state="complete"),
        memory_after=_side("available", index_state="complete"),
        evidence_map=EvidenceMap(coverage="the planned change set"),
        limits=_limits(),
        supplemental=(receipt,),
        claims=(_claim("recorded-fact", _receipt_basis(receipt)),),
        recorded_at="planned-master-tip",
        comparison_owner=PLANNED_OWNER,
        comparison_reference_value=planned_reference(TASK, LEAF),
    )

    with pytest.raises(IntendedAuthorityRefusal):
        require_intended_packet(packet, comparison_owner=PLANNED_OWNER, leaf_id=LEAF)

    require_intended_packet(packet, comparison_owner=COMPARISON_OWNER, leaf_id=LEAF)


# --- preparation itself -------------------------------------------------------------------------


def _repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _invariant() -> bytes:
    return json.dumps(
        {
            "schema": "ar-invariant/v1",
            "id": "INV-AAAAAA",
            "origin": {"task": TASK, "leaf": LEAF},
            "revision": 1,
            "status": "accepted",
            "statement": "a claim's authority rests on a resolving exact basis",
            "applicability": "every review page",
            "conditions": [],
            "exclusions": [],
            "supersedes": [],
            "admission": "legacy-unassessed",
        }
    ).encode()


@pytest.fixture()
def planned_trees(tmp_path: Path) -> Any:
    code = _repository(tmp_path / "code")
    memory = _repository(tmp_path / "memory")
    tree = write_files_tree(code, {"src/app.py": b"def run():\n    return 1\n"})
    memory_tree = write_files_tree(
        memory, {"knowledge/invariants/INV-AAAAAA-basis.json": _invariant()}
    )
    record = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=0,
        code_base=ReviewTreeSide(repository=str(code), tree=tree),
        code_candidate=ReviewTreeSide(repository=str(code), tree=tree),
        memory_base=ReviewTreeSide(repository=str(memory), tree=memory_tree),
        memory_candidate=ReviewTreeSide(repository=str(memory), tree=memory_tree),
        recorded_at="planned-master-tip",
    )
    return reopened_trees(tmp_path / "coordination", record, None)


def _prepare(trees: Any, claim: EvidenceClaim, receipt: SupplementalReceipt, owner: str) -> Any:
    return prepare_evidence(
        trees,
        scope="the intent review's page",
        question="what is the leaf meant to build?",
        evidence_map=EvidenceMap(coverage="the planned change set"),
        claims=(claim,),
        supplemental=(receipt,),
        comparison_owner=owner,
        comparison_reference=(
            planned_reference(TASK, LEAF)
            if owner == PLANNED_OWNER
            else "review-comparison:" + f"{TASK}/{LEAF}/0"
        ),
    )


def test_prepare_evidence_refuses_the_claim_on_a_planned_binding(planned_trees: Any) -> None:
    receipt = _receipt()

    with pytest.raises(IntendedAuthorityRefusal, match="claim 'c1'"):
        _prepare(
            planned_trees, _claim("recorded-fact", _receipt_basis(receipt)), receipt, PLANNED_OWNER
        )

    prepared = _prepare(
        planned_trees, _claim("intended", _receipt_basis(receipt)), receipt, PLANNED_OWNER
    )
    assert prepared.packet.comparison.comparison_owner == PLANNED_OWNER
    assert prepared.packet.claims.claims[0].classification == "intended"


def test_prepare_evidence_leaves_the_claim_alone_on_a_recorded_binding(planned_trees: Any) -> None:
    receipt = _receipt()

    prepared = _prepare(
        planned_trees, _claim("recorded-fact", _receipt_basis(receipt)), receipt, COMPARISON_OWNER
    )

    assert prepared.packet.comparison.comparison_owner == COMPARISON_OWNER
    assert prepared.packet.claims.claims[0].classification == "recorded-fact"


def test_prepare_evidence_recognises_the_leaf_documents_own_receipt(planned_trees: Any) -> None:
    # The receipt composer names the leaf's own document ``task-document`` and ``task-document-md``,
    # so those ids are recognised even when the caller does not pass the document's stem.
    for receipt_id, filename in (
        ("task-document", f"{SLUG}.json"),
        ("task-document-md", f"{SLUG}.md"),
    ):
        document = _receipt(receipt_id, filename)
        with pytest.raises(IntendedAuthorityRefusal):
            _prepare(
                planned_trees,
                _claim("recorded-fact", _receipt_basis(document)),
                document,
                PLANNED_OWNER,
            )

    # Another id needs the stem: the same claim stays as it is until the stem is supplied.
    odd = _receipt("packet-07", f"{SLUG}.md")
    claim = _claim("recorded-fact", _receipt_basis(odd))
    _prepare(planned_trees, claim, odd, PLANNED_OWNER)
    with pytest.raises(IntendedAuthorityRefusal):
        prepare_evidence(
            planned_trees,
            scope="the intent review's page",
            question="what is the leaf meant to build?",
            evidence_map=EvidenceMap(coverage="the planned change set"),
            claims=(claim,),
            supplemental=(odd,),
            comparison_owner=PLANNED_OWNER,
            comparison_reference=planned_reference(TASK, LEAF),
            leaf_document=SLUG,
        )


def test_duplicate_receipt_ids_or_sources_and_inconsistent_basis_states_are_refused() -> None:
    record = _record(tree="b" * 40, memory_tree="c" * 40)
    first = _receipt("task-document", f"{SLUG}.json")
    duplicate_id = _receipt("task-document", f"{SLUG}.md")
    duplicate_source = _receipt("task-document-2", f"{SLUG}.json")

    for bad_pair, match in (
        ((first, duplicate_id), "used twice"),
        ((first, duplicate_source), "receipted twice"),
    ):
        with pytest.raises(ValidationError, match=match):
            build_evidence_packet(
                record,
                repository_id=TASK,
                scope="the intent review's page",
                question="what is the leaf meant to build?",
                code_base=_side("available"),
                code_candidate=_side("available"),
                memory_before=_side("available", index_state="complete"),
                memory_after=_side("available", index_state="complete"),
                evidence_map=EvidenceMap(coverage="the planned change set"),
                limits=_limits(),
                supplemental=bad_pair,
                recorded_at="planned-master-tip",
            )

    with pytest.raises(ValidationError, match="same receipt"):
        ClaimBasis(
            kind="supplemental-receipt",
            reference="packet-01",
            state="present",
            receipt_id="packet-02",
        )

    unread = SupplementalReceipt(
        receipt_id="packet-01",
        canonical_source=PACKET,
        producer=f"leaf {LEAF}",
        read_state="unavailable",
        capture_identity="a planned page's build",
        establishes="a document that could not be read",
        limitation="missing from disk",
    )
    for classification in ("intended", "recorded-fact"):
        with pytest.raises(ValidationError, match="no present examined bytes"):
            build_evidence_packet(
                record,
                repository_id=TASK,
                scope="the intent review's page",
                question="what is the leaf meant to build?",
                code_base=_side("available"),
                code_candidate=_side("available"),
                memory_before=_side("available", index_state="complete"),
                memory_after=_side("available", index_state="complete"),
                evidence_map=EvidenceMap(coverage="the planned change set"),
                limits=_limits(),
                supplemental=(unread,),
                claims=(_claim(classification, _receipt_basis(unread)),),
                recorded_at="planned-master-tip",
            )

    # Separate JSON and MD receipts do not collapse in staleness lookup; each tracks its own drift.
    json_rec = _receipt("task-document", f"{SLUG}.json")
    md_rec = SupplementalReceipt(
        receipt_id="task-document-md",
        canonical_source=f"{SLUG}.md",
        producer=f"leaf {LEAF}",
        digest="b" * 64,
        byte_size=128,
        read_state="present",
        capture_identity="a planned page's build",
        establishes="rendered markdown",
    )
    valid_packet = build_evidence_packet(
        record,
        repository_id=TASK,
        scope="the intent review's page",
        question="what is the leaf meant to build?",
        code_base=_side("available"),
        code_candidate=_side("available"),
        memory_before=_side("available", index_state="complete"),
        memory_after=_side("available", index_state="complete"),
        evidence_map=EvidenceMap(coverage="the planned change set"),
        limits=_limits(),
        supplemental=(json_rec, md_rec),
        claims=(
            _claim("intended", _receipt_basis(json_rec), claim_id="c-json"),
            _claim("intended", _receipt_basis(md_rec), claim_id="c-md"),
        ),
        recorded_at="planned-master-tip",
    )
    assert {r.receipt_id for r in valid_packet.supplemental} == {
        "task-document",
        "task-document-md",
    }


# --- small builders for the packet-level case -------------------------------------------------


def _side(state: str, index_state: str | None = None) -> SideState:
    return SideState(state, index_state=index_state)


def _limits() -> Any:
    return DEFAULT_READ_LIMITS


def _record(*, tree: str, memory_tree: str) -> ReviewTreeComparisonRecord:
    return ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=0,
        code_base=ReviewTreeSide(repository="/code", tree=tree),
        code_candidate=ReviewTreeSide(repository="/code", tree=tree),
        memory_base=ReviewTreeSide(repository="/memory", tree=memory_tree),
        memory_candidate=ReviewTreeSide(repository="/memory", tree=memory_tree),
        recorded_at="planned-master-tip",
    )


# --- E01: the real open build and Update measure both code sides (MIK-R185 v2 evidence 1) --------


class _RecordingHost:
    """A host that records seat creation and answers a live, idle seat; the only faked side."""

    def __init__(self) -> None:
        self.created: list[dict[str, str]] = []
        self.sent: list[tuple[str, str]] = []

    def create_seat(
        self,
        *,
        agent_id: str,
        workspace: Any,
        title: str,
        capsule: str,
        labels: Any,
    ) -> None:
        self.created.append({"agent_id": agent_id, "workspace": str(workspace), "title": title})
        del capsule, labels

    def resume(self, agent_id: str) -> None:
        del agent_id

    def send(self, agent_id: str, text: str, *, message_id: str | None = None) -> None:
        self.sent.append((agent_id, text))
        del message_id

    def archive(self, agent_id: str) -> None:
        del agent_id

    def seat_state(self, agent_id: str) -> str:
        del agent_id
        return "live"

    def cancel(self, agent_id: str, *, request_id: str) -> dict[str, Any] | None:
        del agent_id, request_id
        return {
            "status": "cancelled",
            "turnStopped": True,
            "permissionClosed": True,
            "requestUncertain": False,
        }

    def in_turn(self, agent_id: str) -> bool:
        del agent_id
        return False


def _open_world(tmp_path: Path, leaf_id: str) -> Any:
    """A real open world: live code and memory worktrees with an uncommitted change set."""
    world = build_world(tmp_path)
    world.config = replace(
        world.config,
        repositories={
            "agents-remember": RepositoryScope(
                repo_id="agents-remember", path=world.code, memory_root=world.memory
            )
        },
    )
    world.contract()
    (world.task_root / f"{leaf_id}.json").write_text(
        json.dumps({"id": leaf_id, "status": "inProgress"}), encoding="utf-8"
    )
    world.edit()
    return world


def _real_composition(world: Any, host: _RecordingHost) -> Any:
    """The dashboard's own review-trees port and the explainer's real routes, host/runner faked."""

    def port(query: Any) -> Any:
        return read_review_trees(world.config, query)

    composed = explainer_composition(
        world.config,
        port,
        wiring=ExplainerWiring(host=host, application=explainer_application_ports()),
    )
    app = FastAPI()
    register_explainer_routes(app, composed)
    return TestClient(app), composed


def _environment_packet(world: Any, leaf_id: str) -> dict[str, Any]:
    """The prepared packet the build itself staged, from the environment's own bytes."""
    workspace = leaf_workspace(world.config, "agents-remember", "260101_tree_review", leaf_id)
    return json.loads((workspace / "instructions" / "evidence.json").read_text())


def test_e01_a_real_open_build_and_update_measure_both_code_sides_and_creates_the_seat(
    tmp_path: Path,
) -> None:
    world = _open_world(tmp_path, "260101-TRV-L1")
    host = _RecordingHost()
    client, _composition = _real_composition(world, host)

    built = client.post(
        "/api/explainer/agents-remember/260101_tree_review/260101-TRV-L1/build",
        json={"requestId": "e01-build", "message": "explain the change set"},
    )
    assert built.status_code == 200, built.text
    assert built.json()["state"] == "ok"
    assert len(host.created) == 1 and host.created[0]["agent_id"]
    assert built.json()["generation"] == 1

    # The packet the build staged carries the comparison owner's measurement of both code sides.
    packet = _environment_packet(world, "260101-TRV-L1")
    assert packet["comparison"]["code_base"]["state"] == "available"
    assert packet["comparison"]["code_candidate"]["state"] == "available"
    assert (
        packet["comparison"]["code_candidate"]["tree"] != packet["comparison"]["code_base"]["tree"]
    )

    # An Update after the candidate moved measures the new candidate, and reaches the same answer.
    (world.code_worktree / CODE_FILE).write_text(
        (world.code_worktree / CODE_FILE).read_text() + "\nKEEP = 1\n", encoding="utf-8"
    )
    updated = client.post("/api/explainer/agents-remember/260101_tree_review/260101-TRV-L1/update")
    assert updated.status_code == 200, updated.text
    updated_packet = _environment_packet(world, "260101-TRV-L1")
    assert updated_packet["comparison"]["code_base"]["state"] == "available"
    assert updated_packet["comparison"]["code_candidate"]["state"] == "available"
    assert (
        updated_packet["comparison"]["code_candidate"]["tree"]
        != packet["comparison"]["code_candidate"]["tree"]
    )
    assert updated_packet["comparison"]["comparison_number"] == 2


def test_e01_a_code_side_measured_unavailable_history_is_carried_with_its_limitation(
    tmp_path: Path,
) -> None:
    """Evidence 4's `_missing_tree` injection: a lost code tree is carried, never substituted."""
    world = _open_world(tmp_path, "260101-TRV-L1")
    # One real resolution records the comparison the port will later reopen as record 2.
    live = read_review_trees(
        world.config,
        ReviewTreesQuery(
            repository_id="agents-remember", master="260101_tree_review", leaf_id="260101-TRV-L1"
        ),
    )
    assert live.state == "trees" and live.comparison is not None

    host = _RecordingHost()
    _client, composed = _real_composition(world, host)
    assert composed.deps is not None

    first = comparison_records(world.task_root, "260101-TRV-L1")[-1]
    lost = first.model_copy(
        update={
            "number": 2,
            "code_candidate": first.code_candidate.model_copy(update={"tree": "f" * 40}),
        }
    )
    (comparison_directory(world.task_root, "260101-TRV-L1") / "2.json").write_text(
        lost.model_dump_json(by_alias=True), encoding="utf-8"
    )

    scope = LeafScope(
        repository_id="agents-remember", task="260101_tree_review", leaf_id="260101-TRV-L1"
    )
    facts = composed.deps.facts(scope)
    token = _SEND_SELECTION.set(
        _SendRequest(
            request_id="e01-recorded",
            message="explain the recorded change set",
            history="recorded",
            comparison_number=2,
        )
    )
    try:
        bound = composed.deps.comparison(scope, facts)
        prepared = composed.deps.prepare(scope, facts, bound)
    finally:
        _SEND_SELECTION.reset(token)

    candidate = prepared.packet.comparison.code_candidate
    assert candidate.state == "unavailable-history"
    assert "Git can no longer produce the tree" in (candidate.detail or "")
    assert prepared.packet.comparison.code_base.state == "available"


def test_e01_a_truly_absent_code_side_is_a_named_refusal_never_a_packet(tmp_path: Path) -> None:
    """A comparison with no measured code sides is refused, at the seam the build answers from."""
    world = _open_world(tmp_path, "260101-TRV-L1")
    result = read_review_trees(
        world.config,
        ReviewTreesQuery(
            repository_id="agents-remember", master="260101_tree_review", leaf_id="260101-TRV-L1"
        ),
    )
    assert result.state == "trees" and result.comparison is not None

    sides = result.knowledge_sides
    record = result.comparison
    trees = ReviewTrees(
        record,
        None,
        Path(record.memory_candidate.repository),
        TreeKnowledge(sides[0], None),
        TreeKnowledge(sides[1], None),
        code_sides=(),
    )
    with pytest.raises(EvidencePrecondition) as refused:
        prepare_evidence(
            trees,
            scope="the change set",
            question="explain",
            evidence_map=EvidenceMap(coverage="the change set"),
        )
    assert refused.value.reason == "evidence-source-unavailable"
    assert "measured code sides" in str(refused.value)
