"""L102-A5: only the sealed F05, F10 and F16 residual properties."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from agents_remember.application import review_frame_store as store
from agents_remember.application.review_evidence_adapter import prepare_evidence
from agents_remember.application.review_evidence_read import read_bound_source
from agents_remember.application.review_evidence_relations import read_bound_relationships
from agents_remember.application.review_tree_comparison import reopened_trees, write_files_tree
from agents_remember.memory.knowledge_index import KnowledgeIndex, KnowledgeIndexCache
from agents_remember.memory.knowledge_index import cache as cache_owner
from agents_remember.memory.knowledge_index.tree import MemoryTreeError
from agents_remember.models.knowledge.review_frame import MAX_MANIFEST_BYTES, MAX_REVISION_BYTES
from test_review_evidence_packet import (
    FAMILY,
    INVARIANT,
    MEMORY_FILES,
    SIBLING,
    _invariant,
    _map,
    world,
)
from test_review_frame_store import _publish, _record, task

__all__ = ["task", "world"]


def test_f05_filename_is_only_a_locator_not_the_native_identity(world) -> None:
    record = world["record"]
    path = f"knowledge/invariants/{INVARIANT}-packet.json"
    family = f"knowledge/families/{FAMILY}-evidence.json"
    positive = read_bound_relationships(record, "memory-candidate", family)
    expected = next(edge for edge in positive.edges if edge.record_id == INVARIANT)
    assert expected.lookup_state == "resolved" and expected.target_revision == 1
    tree = write_files_tree(
        world["memory"],
        {**MEMORY_FILES, path: json.dumps(_invariant(SIBLING, "foreign", revision=77)).encode()},
    )
    foreign = record.model_copy(
        update={"memory_candidate": record.memory_candidate.model_copy(update={"tree": tree})}
    )
    answer = read_bound_relationships(foreign, "memory-candidate", family, depth=1)
    rejected = next(edge for edge in answer.edges if edge.record_id == INVARIANT)
    assert rejected.lookup_state == "unreadable"
    assert rejected.target_revision is None and rejected.target_origin_leaf is None
    sibling = next(edge for edge in answer.edges if edge.record_id == SIBLING)
    assert sibling.lookup_state == "resolved" and sibling.target_revision == 1
    print(
        json.dumps(
            {
                "finding": "F05",
                "nativeId": SIBLING,
                "requestedId": INVARIANT,
                "answer": rejected.model_dump(),
                "examinedBytes": answer.examined_bytes,
            }
        )
    )


@pytest.mark.parametrize("kind", ["manifest", "digest", "asset", "pointer"])
def test_f10_corrupt_store_reads_stop_at_the_admitted_byte_bound(task, monkeypatch, kind) -> None:
    record = _record()
    _publish(task, record)
    scope = store.frame_scope(task, record.leaf_id, "comparison-bound-evidence")
    revision = scope.frame_dir / store.REVISIONS_DIRECTORY / "1"
    target, cap = {
        "manifest": (revision / store.MANIFEST_NAME, MAX_MANIFEST_BYTES),
        "digest": (revision / store.MANIFEST_DIGEST_NAME, 64),
        "asset": (revision / store.ASSETS_DIRECTORY / "index.html", 0),
        "pointer": (scope.frame_dir / store.READY_POINTER, MAX_MANIFEST_BYTES),
    }[kind]
    if kind == "asset":
        cap = target.stat().st_size
    target.write_bytes(b"x" * (MAX_MANIFEST_BYTES * 2 + 37))
    original = Path.open
    reads: list[tuple[int, int]] = []

    class Measured:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            payload = self.stream.read(size)
            reads.append((size, len(payload)))
            return payload

    @contextmanager
    def measured(path, mode="r", *args, **kwargs):
        with original(path, mode, *args, **kwargs) as stream:
            yield Measured(stream) if path == target and mode == "rb" else stream

    monkeypatch.setattr(Path, "open", measured)
    assert store.reopen_frame(scope).state == "unavailable"
    assert reads and sum(count for _requested, count in reads) <= cap + 1
    assert all(0 <= requested <= cap + 1 for requested, _count in reads)
    print(json.dumps({"finding": "F10", "kind": kind, "cap": cap, "reads": reads}))


def test_f10_asset_metadata_cannot_authorize_an_over_cap_read(task, monkeypatch) -> None:
    record = _record()
    _publish(task, record)
    scope = store.frame_scope(task, record.leaf_id, "comparison-bound-evidence")
    revision = scope.frame_dir / store.REVISIONS_DIRECTORY / "1"
    manifest = revision / store.MANIFEST_NAME
    document = json.loads(manifest.read_bytes())
    document["assets"][0]["byte_size"] = MAX_REVISION_BYTES + 1
    encoded = json.dumps(document).encode()
    manifest.write_bytes(encoded)
    (revision / store.MANIFEST_DIGEST_NAME).write_text(hashlib.sha256(encoded).hexdigest())
    asset = revision / store.ASSETS_DIRECTORY / document["assets"][0]["path"]
    original = Path.open
    touched = []

    def observed(path, *args, **kwargs):
        if path == asset:
            touched.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observed)
    assert store.reopen_frame(scope).state == "unavailable"
    assert not touched


def test_f16_real_partial_index_cold_rebuild_and_historical_packet(world, tmp_path) -> None:
    assert world["trees"].after.wire.index_state == "complete"
    record = world["record"]
    bad_path = "knowledge/invariants/INV-XXXXXX-broken.json"
    tree = write_files_tree(world["memory"], {**MEMORY_FILES, bad_path: b"{broken"})
    record = record.model_copy(
        update={"memory_candidate": record.memory_candidate.model_copy(update={"tree": tree})}
    )
    coordination = tmp_path / "coordination"
    cache = KnowledgeIndexCache(coordination / "runtime" / "knowledge-index")
    assert not cache.path_for(tree).exists()
    cold = reopened_trees(coordination, record, None)
    assert cold.after.wire.index_state == "partial"
    assert [path for path, _reason in cold.after.wire.problems] == [bad_path]
    with cache.for_git_tree(world["memory"], tree) as index:
        assert index.record(INVARIANT).value is not None
        sibling = index.invariant(SIBLING).value
        assert sibling is not None and sibling.proofs
        assert index.record("INV-XXXXXX").value is None
        assert index.state.state == "partial"
    assert cache.last_outcome is not None and cache.last_outcome.reused
    prepared = prepare_evidence(
        cold,
        scope="one historical comparison",
        question="what is known?",
        evidence_map=_map(),
        paths=(f"knowledge/invariants/{INVARIANT}-packet.json",),
        family_path=f"knowledge/families/{FAMILY}-evidence.json",
    )

    memory = prepared.packet.comparison.memory_candidate
    assert memory.index_state == "partial" and memory.detail and bad_path in memory.detail
    assert any("absence" in reason for reason in prepared.packet.limitations)
    original_packet = prepared.packet.model_dump_json()
    cache.path_for(tree).unlink()
    (world["memory"] / "knowledge").mkdir(exist_ok=True)
    (world["memory"] / "knowledge" / "checkout-only.json").write_text("not this tree")
    rebuilt = reopened_trees(coordination, record, None)
    assert rebuilt.after.wire == cold.after.wire
    assert rebuilt.after.database is not None
    with KnowledgeIndex(rebuilt.after.database, expected_key=tree) as index:
        assert index.record(INVARIANT).value is not None
        assert index.state.state == "partial" and index.state.key == tree
    again = prepare_evidence(
        rebuilt,
        scope="one historical comparison",
        question="what is known?",
        evidence_map=_map(),
        paths=(f"knowledge/invariants/{INVARIANT}-packet.json",),
        family_path=f"knowledge/families/{FAMILY}-evidence.json",
    )
    assert again.packet.model_dump_json() == original_packet
    lost = record.model_copy(
        update={"memory_candidate": record.memory_candidate.model_copy(update={"tree": "f" * 40})}
    )
    unavailable = prepare_evidence(
        reopened_trees(coordination, lost, None),
        scope="missing history",
        question="what remains?",
        evidence_map=_map(),
        paths=(f"knowledge/invariants/{INVARIANT}-packet.json",),
    )
    assert unavailable.packet.comparison.memory_candidate.state == "unavailable-history"
    assert unavailable.packet.comparison.memory_candidate.index_state is None
    sources = unavailable.packet.collection("source-inventory-and-linked-locations")
    assert sources is not None and sources.state == "unavailable"
    assert read_bound_source(lost, "code-candidate", "src/app.py").state == "present"
    assert prepared.packet.model_dump_json() == original_packet
    print(
        json.dumps(
            {
                "finding": "F16",
                "tree": tree,
                "coldAndRebuilt": memory.model_dump(),
                "cacheBytes": cache.path_for(tree).stat().st_size,
                "partialFile": bad_path,
                "source": __file__,
            }
        )
    )


def test_f16_failed_cache_build_keeps_the_owners_reason(world, tmp_path, monkeypatch) -> None:
    def failed_build(*_args, **_kwargs):
        raise MemoryTreeError("fixture index build failed")

    monkeypatch.setattr(cache_owner, "build_index", failed_build)
    trees = reopened_trees(tmp_path / "failed-cache", world["record"], None)
    assert trees.after.wire.state == "unavailable-history"
    assert trees.after.database is None
    prepared = prepare_evidence(
        trees, scope="cache build failure", question="what remains?", evidence_map=_map()
    )
    side = prepared.packet.comparison.memory_candidate
    assert side.state == "unavailable-history" and side.index_state is None
    assert side.detail is not None and "fixture index build failed" in side.detail
    assert any("fixture index build failed" in reason for reason in prepared.packet.limitations)
    assert read_bound_source(world["record"], "code-candidate", "src/app.py").state == "present"
