"""Product task-document memory reviews keep their own fixed-list sequence."""

from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import test_role_step_up as step_up_support
from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.task_docs.task_doc_route_review import (
    _enforce_route_review_authority,
    _with_route_review_state,
    candidate_root_for_review,
)
from agents_remember.application.task_docs.task_doc_tools import TaskDocError
from agents_remember.application.task_docs.task_review_step_up import with_review_step_up
from agents_remember.cli import role_launch_preparation
from agents_remember.cli.role_step_up import select_step_up
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.task_doc import TaskDocResponse
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentTopology
from agents_remember.tasks.launch_review_read import tolerate_unreadable_review
from agents_remember.tasks.route_review import RouteReviewRecord, RouteReviewUnit
from agents_remember.worktrees.review_history import (
    ReviewHistoryError,
    begin_task_review,
    failed_role_tries,
    record_task_review,
    role_review_state,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract
from pydantic import ValidationError
from test_review_state import _document
from test_task_doc_review_public import _call, _create, _leaf_doc
from test_task_document import _config

launch_world = step_up_support.launch_world


def test_memory_record_answer_uses_curator_lane_without_reopening_passed_code(launch_world) -> None:
    settings_path = user_settings_path_for_write()
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    settings.setdefault("orchestration", {}).setdefault("roles", {})["curator"] = {
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b", "afterTries": 3},
    }
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings))
    passed_code = {
        "round": 1,
        "pending": False,
        "limit": 5,
        "recordedRounds": [
            {"round": 1, "kind": "code", "findingsRemain": False, "verdictRef": "code-pass.md"}
        ],
    }
    memory = {
        "round": 3,
        "pending": False,
        "limit": 5,
        "baselineFindings": [{"findingId": "M1", "description": "memory is incomplete"}],
        "remainingFindingIds": ["M1"],
        "recordedRounds": [
            {
                "round": number,
                "kind": "memory",
                "findingsRemain": True,
                "verdictRef": f"memory-{number}.md",
            }
            for number in range(1, 4)
        ],
    }
    answer = {
        "ok": True,
        "operation": "task_doc.record_review",
        "reviewKind": "memory",
        "reviewState": deepcopy(passed_code),
        "memoryReviewState": memory,
    }
    result = with_review_step_up(launch_world.config, answer)
    assert "curator seat steps up after 3 tries" in result.get("nextAction", "")
    assert "worker seat steps up" not in result["nextAction"]
    assert result["reviewState"] == passed_code
    world = launch_world
    document = type(world.leaf.document).model_validate(
        {
            **world.leaf.document.model_dump(),
            "reviewState": passed_code,
            "memoryReviewState": memory,
        }
    )
    world.leaf = ResolvedTaskDocument(ref=world.leaf.ref, path=world.leaf.path, document=document)
    write_task_doc(world.leaf.path.parent, document)
    defaults = {
        "role": "curator",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b", "afterTries": 3},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    request = world.request("curator")
    _, started = world.dispatch(request)
    assert started["sessionOptions"]["model"] == "gpt-b"
    material = json.loads(
        Path(world.receipt(request)["report"]["path"]).with_suffix(".step-up.json").read_text()
    )
    assert material["lastVerdict"] == "memory-3.md"
    assert material["remainingFindingIds"] == ["M1"]
    reread = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref).document
    assert reread.reviewState == document.reviewState
    assert reread.memoryReviewState == document.memoryReviewState


def test_memory_baseline_after_code_pass_is_independent_and_persisted(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _create(config)
    code = _call(config, "record_review", review={"verdict": "pass", "findings": []})["reviewState"]
    begun = _call(config, "begin_review", review={"reviewKind": "memory"})
    assert begun["reviewKind"] == "memory"
    assert begun["memoryReviewState"]["round"] == 1
    assert begun["memoryReviewState"]["pending"] is True
    assert begun["reviewState"] == code
    recorded = _call(
        config,
        "record_review",
        review={
            "reviewKind": "memory",
            "verdict": "block",
            "findings": [{"findingId": "M1", "description": "Memory obligation missing."}],
            "verdictRef": "reports/memory-first.md",
        },
    )
    assert recorded["reviewState"] == code
    assert recorded["reviewKind"] == "memory"
    state = recorded["memoryReviewState"]
    assert state["candidateIdentity"] is None
    with pytest.raises(TaskDocError, match="review-candidate-changed"):
        _call(
            config,
            "record_review",
            review={"reviewKind": "memory", "verdict": "pass", "remainingFindingIds": []},
        )
    assert state["remainingFindingIds"] == ["M1"]
    assert state["recordedRounds"] == [
        {
            "round": 1,
            "kind": "memory",
            "findingsRemain": True,
            "verdictRef": "reports/memory-first.md",
        }
    ]
    TaskDocResponse.model_validate(recorded)
    restored = TaskDocument.model_validate_json(_leaf_doc(config).model_dump_json())
    assert restored.reviewState is not None
    assert restored.reviewState.model_dump(mode="json") == code
    assert restored.memoryReviewState is not None
    assert restored.memoryReviewState.model_dump(mode="json") == state
    assert failed_role_tries(restored, "curator") == 1
    assert failed_role_tries(restored, "worker") == 0


def _memory_config(tmp_path: Path) -> tuple[McpRuntimeConfig, Path]:
    config = _config(tmp_path)
    memory = tmp_path / "memory-candidate"
    subprocess.run(
        ["git", "clone", "--quiet", str(config.repositories["agents-remember"].path), str(memory)],
        check=True,
        capture_output=True,
    )
    repository = replace(config.repositories["agents-remember"], memory_root=memory)
    return replace(config, repositories={"agents-remember": repository}), memory


def test_memory_record_only_binds_memory_bytes_not_code_bytes(tmp_path: Path) -> None:
    config, memory = _memory_config(tmp_path)
    _create(config)
    code = _call(config, "record_review", review={"verdict": "pass"})["reviewState"]
    first = _call(
        config,
        "record_review",
        review={
            "reviewKind": "memory",
            "verdict": "block",
            "findings": [{"findingId": "M1", "description": "Memory repair needed."}],
        },
    )
    (config.repositories["agents-remember"].path / "base.txt").write_text("code changed\n")
    checked = _call(
        config,
        "record_review",
        review={"reviewKind": "memory", "verdict": "block", "remainingFindingIds": ["M1"]},
    )
    assert checked["memoryReviewState"] == first["memoryReviewState"]
    (memory / "memory.json").write_text('{"candidate": "changed"}\n')
    with pytest.raises(TaskDocError, match="review-candidate-changed"):
        _call(
            config,
            "record_review",
            review={"reviewKind": "memory", "verdict": "pass", "remainingFindingIds": []},
        )
    begun = _call(config, "begin_review", review={"reviewKind": "memory"})
    assert begun["memoryReviewState"]["round"] == 2
    passed = _call(
        config,
        "record_review",
        review={"reviewKind": "memory", "verdict": "pass", "remainingFindingIds": []},
    )
    assert (
        passed["memoryReviewState"]["candidateIdentity"]
        != first["memoryReviewState"]["candidateIdentity"]
    )
    assert passed["reviewState"] == code
    assert failed_role_tries(_leaf_doc(config), "curator") == 1


def test_memory_lane_strict_reader_and_launch_only_diagnostic_preserve_raw_state() -> None:
    raw = _document().model_dump(mode="json", exclude_none=True)
    raw["memoryReviewState"] = {
        "round": 1,
        "pending": False,
        "recordedRounds": [{"round": 1, "kind": "code", "findingsRemain": True}],
    }
    with pytest.raises(ValidationError, match="memoryReviewState"):
        TaskDocument.model_validate(raw)
    with tolerate_unreadable_review():
        diagnostic = TaskDocument.model_validate(raw)
    assert diagnostic.review_read_error is not None
    assert "memoryReviewState" in diagnostic.review_read_error
    assert failed_role_tries(diagnostic, "curator") is None
    assert diagnostic.model_dump(mode="json", exclude_none=True) == raw
    assert "memoryReviewState" not in diagnostic.model_dump(exclude={"memoryReviewState"})
    with pytest.raises(ReviewHistoryError, match="review-state-unreadable"):
        begin_task_review(diagnostic, {"reviewKind": "memory"})
    with pytest.raises(ReviewHistoryError, match="review-state-unreadable"):
        record_task_review(diagnostic, {"reviewKind": "memory", "verdict": "pass"})
    with tolerate_unreadable_review(), pytest.raises(ValidationError):
        TaskDocument.model_validate({**raw, "kind": "invented"})


def test_memory_lane_cannot_be_authored_or_erased_by_generic_task_mutations() -> None:
    original = _document()
    raw = original.model_dump(mode="json")
    raw["memoryReviewState"] = {"round": 1, "pending": True}
    candidate = TaskDocument.model_validate(raw)
    for operation, before, after in (
        ("create", None, candidate),
        ("replace", original, candidate),
        ("replace", candidate, original),
    ):
        with pytest.raises(TaskDocError, match="memory-review state"):
            _enforce_route_review_authority(operation, before, after)
    _enforce_route_review_authority("replace", candidate, candidate)


def test_memory_candidate_authority_uses_contract_or_configured_fallback_only(
    tmp_path: Path,
) -> None:
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    fallback = tmp_path / "configured-memory"
    contract = WorktreeContract(
        task_id="fixture",
        task_name="fixture",
        repo_name="fixture",
        workflow_kind="light-task",
        memory_mode="external",
        coordination_root=tmp_path,
        task_root=tmp_path / "task",
        contract_path=tmp_path / "contract.md",
        task_artifact=tmp_path / "task.json",
        worktree_group=tmp_path,
        code_repo_path=code,
        code_source_branch="main",
        code_work_branch="fixture",
        code_base_commit="",
        code_worktree=code,
        memory_worktree=memory,
    )
    assert (
        candidate_root_for_review(contract, code, review_kind="memory", memory_root=fallback)
        == memory
    )
    assert (
        candidate_root_for_review(
            replace(contract, memory_worktree=None),
            code,
            review_kind="memory",
            memory_root=fallback,
        )
        == fallback
    )
    assert candidate_root_for_review(None, code, review_kind="memory") is None


def test_memory_kind_cannot_publish_code_route_evidence() -> None:
    review = RouteReviewRecord(
        candidateTree="ab" * 20,
        verdict="pass",
        verdictRef="reports/code.md",
        reviewedAt="fixture",
        routes=[RouteReviewUnit(route="code", verdict="pass", evidenceRef="reports/code.md")],
    )
    with pytest.raises(TaskDocError, match=r"record_review.*reviewKind=memory"):
        _with_route_review_state(_document(), {"reviewKind": "memory", "verdict": "pass"}, review)


def test_product_memory_threshold_own_limits_approval_and_shrinking_list(tmp_path: Path) -> None:
    settings = user_settings_path_for_write()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "orchestration": {
                    "loops": {"defaults": {"maxRounds": 2}},
                    "roles": {
                        "curator": {"model": "base", "stepUp": {"model": "larger", "afterTries": 2}}
                    },
                }
            }
        )
    )
    config, _ = _memory_config(tmp_path)
    _create(config)
    _call(config, "record_review", review={"verdict": "pass"})
    _call(config, "begin_review", review={})
    code = _call(config, "record_review", review={"verdict": "pass", "remainingFindingIds": []})[
        "reviewState"
    ]
    _call(config, "begin_review", review={"reviewKind": "memory"})
    first = _call(
        config,
        "record_review",
        review={
            "reviewKind": "memory",
            "verdict": "block",
            "findings": [
                {"findingId": "M1", "description": "First memory issue."},
                {"findingId": "M2", "description": "Second memory issue."},
            ],
        },
    )
    defaults = {
        "role": "curator",
        "agent": "codex",
        "model": "base",
        "stepUp": {"model": "larger", "afterTries": 2},
    }
    assert select_step_up(defaults, _leaf_doc(config))["model"] == "base"
    begun = _call(config, "begin_review", review={"reviewKind": "memory"})
    assert begun["memoryReviewState"]["round"] == 2
    for invalid in (
        {"findings": [{"findingId": "M3", "description": "New finding."}]},
        {"remainingFindingIds": ["M3"]},
    ):
        with pytest.raises(TaskDocError):
            _call(
                config,
                "record_review",
                review={"reviewKind": "memory", "verdict": "block", **invalid},
            )
    second = _call(
        config,
        "record_review",
        review={"reviewKind": "memory", "verdict": "block", "remainingFindingIds": ["M2"]},
    )
    restored = TaskDocument.model_validate_json(_leaf_doc(config).model_dump_json())
    assert failed_role_tries(restored, "curator") == 2
    assert failed_role_tries(restored, "worker") == 0
    assert select_step_up(defaults, restored)["model"] == "larger"
    assert "curator seat steps up after 2 tries" in second["nextAction"]
    assert (
        second["memoryReviewState"]["baselineFindings"]
        == first["memoryReviewState"]["baselineFindings"]
    )
    for kind in ("code", "memory"):
        with pytest.raises(TaskDocError, match="count=2, limit=2"):
            _call(config, "begin_review", review={"reviewKind": kind})
    third = _call(
        config,
        "begin_review",
        review={
            "reviewKind": "memory",
            "approval": {"by": "architect", "text": "One further verification."},
            "additionalRounds": 1,
        },
    )
    assert third["memoryReviewState"]["limit"] == 3
    assert third["memoryReviewState"]["approval"] == {
        "by": "architect",
        "text": "One further verification.",
    }
    assert third["reviewState"] == code
    with pytest.raises(TaskDocError, match="subset"):
        _call(
            config,
            "record_review",
            review={
                "reviewKind": "memory",
                "verdict": "block",
                "remainingFindingIds": ["M1", "M2"],
            },
        )
    passed = _call(
        config,
        "record_review",
        review={"reviewKind": "memory", "verdict": "pass", "remainingFindingIds": []},
    )
    assert passed["reviewState"] == code
    assert passed["memoryReviewState"]["remainingFindingIds"] == []
    assert len(passed["memoryReviewState"]["recordedRounds"]) == 3
    with pytest.raises(TaskDocError, match="count=2, limit=2"):
        _call(config, "begin_review", review={})


def test_legacy_mixed_code_records_are_not_migrated_into_curator_count() -> None:
    raw = _document().model_dump(mode="json", exclude_none=True)
    raw["reviewState"] = {
        "round": 1,
        "pending": False,
        "recordedRounds": [{"round": 1, "kind": "memory", "findingsRemain": True}],
    }
    restored = TaskDocument.model_validate(raw)
    assert restored.memoryReviewState is None
    assert role_review_state(restored, "curator") is None
    assert role_review_state(restored, "worker") == restored.reviewState
    assert role_review_state(restored, "reviewer") is None
    assert failed_role_tries(restored, "curator") == 0
    assert restored.reviewState is not None
    assert restored.model_dump(mode="json", exclude_none=True)[
        "reviewState"
    ] == restored.reviewState.model_dump(mode="json", exclude_none=True)
    assert "memoryReviewState" not in restored.model_dump(mode="json", exclude_none=True)


def test_invalid_kinds_and_incomplete_memory_count_fail_closed() -> None:
    for invalid in ("Memory", "other", None, [], {}):
        for operation in (begin_task_review, record_task_review):
            with pytest.raises(ReviewHistoryError, match="reviewKind must be code or memory"):
                operation(_document(), {"reviewKind": invalid, "verdict": "pass"})
    raw = _document().model_dump(mode="json", exclude_none=True)
    raw["memoryReviewState"] = {"round": 2, "pending": False, "recordedRounds": []}
    unreadable_count = TaskDocument.model_validate(raw)
    assert failed_role_tries(unreadable_count, "curator") is None
    defaults = {"role": "curator", "model": "base", "stepUp": {"model": "larger", "afterTries": 1}}
    assert select_step_up(defaults, unreadable_count)["model"] == "base"
