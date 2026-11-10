"""Public leaf writes can author entries before any Markdown card exists."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.application.memory_quality.controller import run_memory_quality_request
from agents_remember.mcp.tools.knowledge import WriteToolRequest, knowledge_write_payload
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.memory import MemoryQualitySyncRequest
from agents_remember.tasks import TaskDocument, write_task_doc
from curator_coherence_test_support import write_curator_task_topology
from fresh_repository_test_support import FreshRepository, fresh_repository
from knowledge_writer_test_support import entry, tree_bytes

pytestmark = pytest.mark.usefixtures("worktree_services")


def _world(root: Path, *, worklist: bool = True) -> FreshRepository:
    world = fresh_repository(root)
    contract = world.contract
    (contract.code_worktree / "test_identity.py").write_text(
        "from app import identity\n\ndef test_identity():\n    assert identity(3) == 3\n",
        encoding="utf-8",
    )
    write_curator_task_topology(contract)
    write_task_doc(
        contract.task_root,
        TaskDocument.model_validate(
            {
                "id": contract.leaf_id,
                "slug": contract.leaf_id,
                "title": "First foundation",
                "kind": "subTask",
                "repo": contract.repo_name,
                "createdAt": "2026-11-06T00:00:00+00:00",
            }
        ),
    )
    if worklist:
        with patch(
            "agents_remember.worktrees.integration.configured_contract_authority.load_config",
            return_value=world.config,
        ):
            run_memory_quality_request(
                world.config,
                MemoryQualitySyncRequest(
                    mode="sync",
                    repo_id=contract.repo_name,
                    contract_path=str(contract.contract_path),
                ),
            )
        worklist_path = contract.contract_path.parent / "knowledge-worklist.json"
        assert json.loads(worklist_path.read_text(encoding="utf-8"))["state"] == "complete"
    assert contract.memory_worktree is not None
    assert not (contract.memory_worktree / "onboarding").exists()
    return world


def _handoff(*, proof: bool = False) -> dict[str, Any]:
    authored = entry(
        "FIRST",
        statement="Identity returns its input unchanged.",
        target=[
            {
                "path": "app.py",
                "locator": {"kind": "symbol", "value": "identity"},
                "role": "primary-authority",
                "rationale": "The function returns its input.",
            }
        ],
        scope={"applicability": "Calling identity.", "conditions": [], "exclusions": []},
        admission={
            "criteria": ["prevents_costly_mistake"],
            "justification": "Changing the returned value violates the public contract.",
        },
    )
    if proof:
        authored["proofs"] = [
            {"test": "test_identity.py::test_identity", "facet": "The value is returned unchanged."}
        ]
    return {
        "entries": [authored],
        "history": [
            {
                "subject": "handoff:FIRST",
                "disposition": "no_family",
                "reason": "This standalone function has no joint guarantee.",
            }
        ],
    }


def _write(world: FreshRepository, handoff: dict[str, Any], *, commit: bool) -> dict[str, Any]:
    return knowledge_write_payload(
        WriteToolRequest(
            contract_path=str(world.contract.contract_path),
            handoff=handoff,
            handoff_name="cardless-foundation.json",
            authorization_ref="fixture-cardless-foundation",
            commit=commit,
        ),
        world.config,
    )


@pytest.mark.parametrize("proof", [False, True])
def test_plan_and_commit_first_entries_without_markdown(tmp_path: Path, proof: bool) -> None:
    world = _world(tmp_path)
    memory = world.contract.memory_worktree
    assert memory is not None
    handoff = _handoff(proof=proof)
    before = tree_bytes(memory)
    planned = _write(world, handoff, commit=False)
    assert planned["state"] == "planned", planned
    assert tree_bytes(memory) == before
    written = _write(world, handoff, commit=True)
    assert written["state"] == "written", written
    sidecar = json.loads((memory / "onboarding/app.py.json").read_text(encoding="utf-8"))
    assert sidecar["references"] == {}
    assert len(sidecar["realizes"]) == 1
    (invariant_path,) = (memory / "knowledge/invariants").glob("*.json")
    invariant = json.loads(invariant_path.read_text(encoding="utf-8"))
    assert sidecar["realizes"][0]["invariant"] == invariant["id"]
    history = json.loads(
        (memory / f"knowledge/history/{world.contract.leaf_id}.json").read_text(encoding="utf-8")
    )
    assert any(
        row["subject"] == f"membership:{invariant['id']}" and row["disposition"] == "no_family"
        for row in history["rows"]
    )
    if proof:
        test_sidecar = json.loads(
            (memory / "onboarding/test_identity.py.json").read_text(encoding="utf-8")
        )
        assert test_sidecar["references"] == {} and test_sidecar["realizes"] == []
        assert len(test_sidecar["proves"]) == 1
        assert test_sidecar["proves"][0]["invariant"] == invariant["id"]
    else:
        assert not (memory / "onboarding/test_identity.py.json").exists()
    assert list((memory / "onboarding").rglob("*.md")) == []
    final_bytes = tree_bytes(memory)
    repeated = _write(world, handoff, commit=True)
    assert repeated["state"] == "written", repeated
    assert tree_bytes(memory) == final_bytes


def test_references_still_require_the_markdown_card(tmp_path: Path) -> None:
    world = _world(tmp_path)
    written = _write(world, _handoff(), commit=True)
    assert written["state"] == "written", written
    memory = world.contract.memory_worktree
    assert memory is not None
    path = memory / "onboarding/app.py.json"
    sidecar = json.loads(path.read_text(encoding="utf-8"))
    anchor = sidecar["realizes"][0]["anchor"]
    sidecar["references"] = {
        "1": {
            "note": "The implementation returns its input.",
            "targets": [{"kind": "code", "anchor": anchor}],
        }
    }
    path.write_text(canonical_text(sidecar), encoding="utf-8")
    before = tree_bytes(memory)
    refused = _write(world, {"entries": []}, commit=True)
    assert refused["state"] == "refused", refused
    assert any(row["rule"] == "R22.3-sidecar-without-markdown" for row in refused["violations"])
    assert tree_bytes(memory) == before


def test_a_cardless_leaf_still_requires_its_persisted_worklist(tmp_path: Path) -> None:
    world = _world(tmp_path, worklist=False)
    refused = _write(world, _handoff(), commit=True)
    assert refused["state"] == "refused", refused
    assert "memory_quality_check" in refused["refusalDetail"]
    assert world.contract.memory_worktree is not None
    assert not (world.contract.memory_worktree / "onboarding").exists()
