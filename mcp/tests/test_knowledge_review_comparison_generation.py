"""Read existing legacy JSON source records without recreating their canonical datasets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import apsw
import pytest
from agents_remember.application.review_comparison_generation import (
    _UNSEALED_FIELDS,
    COMPARISON_GENERATION_VERSION,
    ComparisonGenerationManifest,
    ComparisonKnowledgeBinding,
    ComparisonPublicationLineage,
    ComparisonRecordBinding,
    ComparisonScopeBinding,
    ComparisonSourceBinding,
    generation_directory,
    generation_identity,
    read_manifest,
)
from agents_remember.application.review_comparison_reopen import reopen_comparison_generation
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.worktrees.modules.future_code_candidate import capture_future_code_candidate
from agents_remember.worktrees.worktree_contract import load_contract
from test_review_git_trees import LEAF, MASTER, REPO, World, build_world, commit, git

pytestmark = pytest.mark.evidence_unit


def _assemble_legacy_manifest(
    payload: dict[str, Any], *, recorded_at: str
) -> ComparisonGenerationManifest:
    """Build a confined existing-format JSON fixture through the retained codec's seal vocabulary."""
    body = dict(payload)
    body["recorded_at"] = recorded_at
    body["binding_digest"] = sha256_digest(
        {key: value for key, value in body.items() if key not in _UNSEALED_FIELDS}
    )
    body["generation_id"] = generation_identity(body["binding_digest"])
    return ComparisonGenerationManifest.model_validate(body)


def _legacy_manifest(world: World):
    """An existing-format JSON fixture with real source identities and declared legacy operands."""
    landed = commit(world.code_worktree, {"pkg/later.py": "LATER = 1\n"})
    contract = load_contract(world.contract(live=True))
    capture = capture_future_code_candidate(contract)
    identity = {
        "repository_id": "00000000-0000-4000-8000-000000000001",
        "schema_version": "legacy",
        "logical_digest": "0" * 64,
    }
    payload: dict[str, Any] = {
        "generation_index": 1,
        "repository_id": REPO,
        "master": MASTER,
        "leaf_id": LEAF,
        "task_root": str(world.task_root),
        "temporary_storage_scope": "notes/reports/comparison-generations",
        "contract_path": str(contract.contract_path),
        "source": {
            "code_repository_root": str(world.code),
            "baseline_code_tree_id": world.code_base,
            "candidate_code_tree_id": capture.codeCandidateTree,
            "candidate_capture": capture.model_dump(),
            "custody": "committed-history",
            "custody_commits": [landed],
        },
        "knowledge": [
            {
                "side": side,
                "state": "retained",
                "identity": identity,
                "artifact": {
                    "relative_path": f"knowledge/{side}/legacy-copy",
                    "sha256": "0" * 64,
                    "byte_count": 0,
                    "deletion_owner": "application.review_comparison_reclamation.discard_comparison_snapshots",
                    "cleanup_scope": f"knowledge/{side}/legacy-copy",
                },
            }
            for side in ("before", "after")
        ],
        "scope": {
            "selected": "task-context",
            "inventory_state": "unavailable",
            "inventory_digest": hashlib.sha256(b"unavailable").hexdigest(),
            "changed_path_count": 0,
            "inventory_partial": True,
            "detail": "This historical fixture records no measured inventory.",
        },
        "records": {
            "state": "not-supplied",
            "assessments": 0,
            "signals": 0,
            "observations": 0,
            "current_measured": False,
            "collection_digest": hashlib.sha256(b"{}").hexdigest(),
            "detail": "No record collection was captured.",
        },
        "policies": [],
        "lineage": {},
    }
    payload["manifest_version"] = COMPARISON_GENERATION_VERSION
    payload["evidence"] = []
    for key, model in (
        ("source", ComparisonSourceBinding),
        ("scope", ComparisonScopeBinding),
        ("records", ComparisonRecordBinding),
        ("lineage", ComparisonPublicationLineage),
    ):
        payload[key] = model.model_validate(payload[key]).model_dump(mode="json")
    payload["knowledge"] = [
        ComparisonKnowledgeBinding.model_validate(side).model_dump(mode="json")
        for side in payload["knowledge"]
    ]
    manifest = _assemble_legacy_manifest(payload, recorded_at="2026-01-01T00:00:00Z")
    directory = generation_directory(world.task_root, LEAF, manifest.generation_id)
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_bytes(manifest.manifest_bytes())
    return manifest


def test_a_legacy_record_preserves_exact_source_and_never_reads_its_dataset(
    tmp_path: Path, monkeypatch
) -> None:
    world = build_world(tmp_path)
    manifest = _legacy_manifest(world)
    candidate = manifest.source.candidate_code_tree_id
    commit(world.code_worktree, {"pkg/later.py": "LATER = 2\n"})

    def forbidden(*args, **kwargs):
        raise AssertionError("a historical canonical dataset was opened")

    monkeypatch.setattr(apsw, "Connection", forbidden)
    reopened = reopen_comparison_generation(world.config, REPO, MASTER, LEAF)
    assert reopened.source is not None and reopened.source.candidate_code_tree_id == candidate
    assert reopened.source.state == "available"
    assert {side.state for side in reopened.knowledge} == {"legacy-unavailable"}
    assert all(side.identity is None for side in reopened.knowledge)
    assert git(world.code_worktree, "rev-parse", "HEAD^{tree}") != candidate


def test_a_tampered_historical_source_record_is_refused_instead_of_retargeted(
    tmp_path: Path,
) -> None:
    world = build_world(tmp_path)
    manifest = _legacy_manifest(world)
    path = generation_directory(world.task_root, LEAF, manifest.generation_id) / "manifest.json"
    payload = json.loads(path.read_bytes())
    payload["source"]["candidate_code_tree_id"] = world.code_base
    path.write_text(json.dumps(payload))
    with pytest.raises(KnowledgeStorageError, match="valid comparison generation"):
        read_manifest(path)
    reopened = reopen_comparison_generation(world.config, REPO, MASTER, LEAF)
    assert reopened.state == "manifest-unreadable" and reopened.manifest is None
