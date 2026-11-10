"""A first memory foundation traverses the real Curator tool sequence."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from agents_remember.application.knowledge_worklist import WORKLIST_FILE_NAME
from agents_remember.application.memory_quality.controller import run_memory_quality_request
from agents_remember.mcp.tools.knowledge import WriteToolRequest, knowledge_write_payload
from agents_remember.models.closeout.input import CloseoutCorrectedCall, CloseoutMessageInput
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRequest,
)
from agents_remember.models.memory import MemoryQualitySyncRequest
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.closeout_input import normalize_closeout_input
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.closeout import closeout_preview_payload
from agents_remember.worktrees.modules.context import contract_context
from curator_coherence_test_support import (
    prepared_publish_inputs,
    read_prepared_candidate_set,
    write_curator_task_topology,
)
from fresh_repository_test_support import FreshRepository, author_first_card, fresh_repository
from knowledge_writer_test_support import entry

pytestmark = pytest.mark.integration


def test_first_knowledge_from_a_fresh_baseline_reaches_closeout_preview(tmp_path: Path) -> None:
    world = fresh_repository(tmp_path)
    contract, config = world.contract, world.config
    assert contract.memory_worktree is not None
    onboarding = contract.memory_worktree / "onboarding"
    sprint = write_curator_task_topology(contract)
    write_task_doc(
        contract.task_root,
        TaskDocument.model_validate(
            {
                "id": contract.leaf_id,
                "slug": contract.leaf_id,
                "title": "First foundation",
                "kind": "subTask",
                "repo": contract.repo_name,
                "createdAt": "2026-11-05T00:00:00+00:00",
                "enclosures": [
                    {"leafId": contract.leaf_id, "enclosurePath": str(contract.contract_path)}
                ],
            }
        ),
    )
    context = contract_context(contract)
    assert context.onboarding_root == onboarding
    assert not onboarding.exists()
    request = MemoryQualitySyncRequest(
        mode="sync",
        repo_id=contract.repo_name,
        contract_path=str(contract.contract_path),
    )
    with patch(
        "agents_remember.worktrees.integration.configured_contract_authority.load_config",
        return_value=config,
    ):
        initial = run_memory_quality_request(config, request)
        assert initial["qualityChecklistStatus"] == "ready-for-closeout", initial
        assert initial["checklistStatus"] == "coherence-required", initial
        assert initial["onboardingRoot"] == str(onboarding)
        worklist_path = contract.contract_path.parent / WORKLIST_FILE_NAME
        worklist = json.loads(worklist_path.read_text(encoding="utf-8"))
        assert worklist["state"] == "complete", worklist
        assert not onboarding.exists()
        handoff = {
            "entries": [
                entry(
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
                    scope={
                        "applicability": "Calling identity.",
                        "conditions": [],
                        "exclusions": [],
                    },
                    admission={
                        "criteria": ["prevents_costly_mistake"],
                        "justification": "Changing the returned value violates the public contract.",
                    },
                )
            ],
            "history": [
                {
                    "subject": "handoff:FIRST",
                    "disposition": "no_family",
                    "reason": "The identity function has one standalone guarantee; no joint obligation exists.",
                }
            ],
        }
        for commit, state in ((False, "planned"), (True, "written")):
            written = knowledge_write_payload(
                WriteToolRequest(
                    contract_path=str(contract.contract_path),
                    handoff=handoff,
                    authorization_ref="MIK-R217@v2",
                    commit=commit,
                ),
                config,
            )
            assert written["state"] == state, written
        assert (onboarding / "app.py.json").is_file()
        sidecar = json.loads((onboarding / "app.py.json").read_text())
        assert len(sidecar["realizes"]) == 1 and sidecar["references"] == {}
        assert not list(onboarding.rglob("*.md"))
        (first,) = (contract.memory_worktree / "knowledge/invariants").glob("*.json")
        assert json.loads(first.read_text())["statement"] == "Identity returns its input unchanged."
        final_quality = run_memory_quality_request(config, request)
        assert final_quality["qualityChecklistStatus"] == "ready-for-closeout", final_quality
        assert final_quality["onboardingRoot"] == str(onboarding)
        prepared = curator_coherence_action(
            contract,
            CuratorCoherenceRequest(action="prepare", contract_path=str(contract.contract_path)),
        )
        candidates = read_prepared_candidate_set(prepared)["candidates"]
        assert candidates == []
        judgments = [
            CuratorCoherenceJudgment(
                **candidate,
                disposition="reconciled",
                rationale="The new card and overview describe the unchanged identity implementation.",
                evidenceRef="code:app.py",
            )
            for candidate in candidates
        ]
        published = curator_coherence_action(
            contract,
            CuratorCoherenceRequest(
                action="publish",
                contract_path=str(contract.contract_path),
                semantic_requirement_revision="MIK-R217@v2",
                delivery_attempt="A001",
                judgments=judgments,
                caller=DeclaredCaller(role="architect", task_document_ref=sprint),
                **prepared_publish_inputs(prepared),
            ),
        )
        assert published["state"] == "published", published
        validated = curator_coherence_action(
            contract,
            CuratorCoherenceRequest(action="validate", contract_path=str(contract.contract_path)),
        )
        assert validated["ok"] and validated["state"] == "valid", validated
        final = run_memory_quality_request(config, request)
        assert final["ok"] and final["closeoutReady"], final
        assert final["onboardingRoot"] == str(onboarding)
        effective = normalize_closeout_input(
            contract,
            CloseoutMessageInput(memory="Create first knowledge foundation"),
            route="worktree",
            corrected_call=CloseoutCorrectedCall(
                tool="worktree_closeout_preview",
                arguments={"contract_path": str(contract.contract_path)},
            ),
        )
        preview = closeout_preview_payload(contract, WorktreeArgs(closeout_input=effective))
        assert preview["state"] == "would-closeout", preview
        gate = preview["knowledge_gate"]
        assert isinstance(gate, dict) and gate["state"] == "pass"
        assert contract_context(contract).onboarding_root == onboarding
        assert not list(onboarding.rglob("*.md"))

        _check_later_card_authoring(world, request)


def _check_later_card_authoring(world: FreshRepository, request: MemoryQualitySyncRequest) -> None:
    author_first_card(world)
    contract = world.contract
    assert contract.memory_worktree is not None
    with_card = run_memory_quality_request(world.config, request)
    assert with_card["qualityChecklistStatus"] == "ready-for-closeout", with_card
    sidecar = contract.memory_worktree / "onboarding/app.py.json"
    assert len(json.loads(sidecar.read_text())["realizes"]) == 1
    after_card = curator_coherence_action(
        contract,
        CuratorCoherenceRequest(action="prepare", contract_path=str(contract.contract_path)),
    )
    assert {
        candidate["onboardingFile"]
        for candidate in read_prepared_candidate_set(after_card)["candidates"]
    } == {
        "app.py.md",
        "overview.md",
    }
