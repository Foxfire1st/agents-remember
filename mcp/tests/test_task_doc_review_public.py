from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.application.task_docs import task_doc_tools as tools
from agents_remember.application.task_docs.task_doc_route_review import (
    _with_route_review_state,
)
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocError,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.candidate_identity import resolve_candidate_identity
from test_task_document import _config


def _target() -> TaskDocTarget:
    return TaskDocTarget(repo_id="agents-remember", task_name="review-api", slug="review_api")


def _create(config: McpRuntimeConfig) -> dict[str, Any]:
    # The review API is exercised on a leaf, so the task root needs its master: the authoring
    # plane refuses a leaf document whose series has no master document at all.
    write_task_doc(
        config.coordination_root / "tasks" / "agents-remember" / "review-api",
        TaskDocument.model_validate(
            {
                "id": "review-series",
                "slug": "task",
                "title": "Review Series",
                "kind": "master",
                "repo": "agents-remember",
                "type": "Master (Code)",
                "createdAt": "2026-01-01T00:00",
                "sections": [{"kind": "subTasks", "heading": "Sub-tasks"}],
            }
        ),
    )
    return task_doc_tool(
        config,
        TaskDocTarget(repo_id="agents-remember", task_name="review-api"),
        operation="create",
        edit=TaskDocEdit(
            fields={
                "id": "review-api",
                "slug": "review_api",
                "title": "Review API",
                "kind": "subTask",
                "repo": "agents-remember",
                "type": "Code",
                "createdAt": "2026-01-01T00:00",
            }
        ),
    )


def _call(
    config: McpRuntimeConfig,
    operation: str,
    *,
    review: dict[str, object] | None = None,
    fields: dict[str, object] | None = None,
) -> dict[str, Any]:
    return task_doc_tool(
        config,
        _target(),
        operation=operation,
        edit=TaskDocEdit(review=review, fields=fields),
    )


def test_public_review_operations_seal_then_shrink_findings(tmp_path: Path) -> None:
    config = _config(tmp_path)
    created = _create(config)
    assert created["reviewState"] == {
        "round": 0,
        "pending": False,
        "baselineFindings": [],
        "remainingFindingIds": [],
    }

    begun = _call(config, "begin_review", review={})
    assert begun["reviewState"]["round"] == 1
    assert begun["reviewState"]["pending"] is True
    resumed = _call(config, "begin_review", review={})
    assert resumed["reviewState"] == begun["reviewState"]

    first = _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [
                {"findingId": "A", "description": "first issue"},
                {"findingId": "B", "description": "second issue"},
            ],
        },
    )
    assert first["reviewState"]["pending"] is False
    assert first["reviewState"]["remainingFindingIds"] == ["A", "B"]

    _call(config, "begin_review", review={})
    second = _call(
        config,
        "record_review",
        review={"verdict": "block", "remainingFindingIds": ["B"]},
    )
    assert second["reviewState"]["round"] == 2
    assert second["reviewState"]["baselineFindings"] == first["reviewState"]["baselineFindings"]
    assert second["reviewState"]["remainingFindingIds"] == ["B"]

    _call(config, "begin_review", review={})
    final = _call(
        config,
        "record_review",
        review={"verdict": "pass", "remainingFindingIds": []},
    )
    assert final["reviewState"]["round"] == 3
    assert final["reviewState"]["remainingFindingIds"] == []


def test_public_review_api_records_without_begin_and_replace_can_set_state(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _create(config)

    # record_review no longer requires a prior begin_review; the first result is the
    # baseline and state is derived from the review records, not gated by them.
    direct = _call(config, "record_review", review={"verdict": "pass", "findings": []})
    assert direct["reviewState"]["round"] == 1
    assert direct["reviewState"]["pending"] is False

    _call(config, "begin_review", review={})
    replaced = _call(
        config,
        "replace",
        fields={
            "id": "review-api",
            "slug": "review_api",
            "title": "Review API changed",
            "kind": "subTask",
            "repo": "agents-remember",
            "type": "Code",
            "createdAt": "2026-01-01T00:00",
        },
    )
    assert replaced["reviewState"]["round"] == 0
    assert replaced["reviewState"]["pending"] is False


def test_successor_cannot_add_a_new_finding(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _create(config)
    _call(config, "begin_review", review={})
    _call(
        config,
        "record_review",
        review={"verdict": "block", "findings": [{"findingId": "A", "description": "issue"}]},
    )
    _call(config, "begin_review", review={})

    with pytest.raises(TaskDocError, match="review-successor-new-findings"):
        _call(
            config,
            "record_review",
            review={
                "verdict": "block",
                "findings": [{"findingId": "B", "description": "new issue"}],
            },
        )


def test_public_review_api_enforces_cap_and_carries_explicit_extra_rounds(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _create(config)

    first_pending = _call(config, "begin_review", review={})
    assert _call(config, "begin_review", review={})["reviewState"] == first_pending["reviewState"]
    _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "issue"}],
        },
    )
    for expected_round in (2, 3, 4, 5):
        begun = _call(config, "begin_review", review={})
        assert begun["reviewState"]["round"] == expected_round
        assert begun["reviewState"]["limit"] == 5
        _call(
            config,
            "record_review",
            review={"verdict": "block", "remainingFindingIds": ["A"]},
        )

    with pytest.raises(TaskDocError, match=r"review-budget-exhausted.*count=5.*limit=5"):
        _call(config, "begin_review", review={})

    approval = {
        "developerApproval": "Developer authorizes one extra fix verification.",
        "additionalRounds": 1,
    }
    sixth_pending = _call(config, "begin_review", review=approval)
    state = sixth_pending["reviewState"]
    assert state["round"] == 6
    assert state["pending"] is True
    assert state["limit"] == 6
    # A legacy approval is recorded in the approval shape, naming the developer.
    assert state["approval"] == {
        "by": "developer",
        "text": "Developer authorizes one extra fix verification.",
    }
    assert state["developerApproval"] is None
    assert state["additionalRounds"] == 1
    assert state["baselineFindings"] == [{"findingId": "A", "description": "issue"}]
    assert state["remainingFindingIds"] == ["A"]

    assert _call(config, "begin_review", review=approval)["reviewState"] == state
    recorded = _call(
        config,
        "record_review",
        review={"verdict": "block", "remainingFindingIds": ["A"]},
    )
    assert recorded["reviewState"]["limit"] == 6
    with pytest.raises(TaskDocError, match=r"review-budget-exhausted.*count=6.*limit=6"):
        _call(config, "begin_review", review={})

    seventh_pending = _call(
        config,
        "begin_review",
        review={
            "approval": {
                "by": "architect",
                "text": "Sealed list shrank; repair is concrete.",
            },
            "additionalRounds": 1,
        },
    )
    state = seventh_pending["reviewState"]
    assert state["round"] == 7
    assert state["limit"] == 7
    assert state["additionalRounds"] == 2
    assert state["approval"] == {
        "by": "architect",
        "text": "Sealed list shrank; repair is concrete.",
    }
    assert state["remainingFindingIds"] == ["A"]


def _write_user_settings(data: dict[str, object]) -> Path:
    path = Path(os.environ["XDG_CONFIG_HOME"]) / "agents-remember" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _block(config: McpRuntimeConfig, candidate: str | None = None) -> None:
    review: dict[str, object] = {
        "verdict": "block",
        "findings": [{"findingId": "A", "description": "issue"}],
    }
    if candidate is not None:
        review["candidateTree"] = candidate
    _call(config, "record_review", review=review)


def test_begin_review_takes_limit_from_settings(tmp_path: Path) -> None:
    """MIK-R116 evidence 1 and 4: with 2 in the settings the third is refused."""
    _write_user_settings({"orchestration": {"loops": {"defaults": {"maxRounds": 2}}}})
    config = _config(tmp_path)
    _create(config)

    fresh = _call(config, "get")
    assert fresh["reviewState"]["round"] == 0
    assert fresh["reviewState"]["limit"] == 2

    first = _call(config, "begin_review", review={})
    assert (first["reviewState"]["round"], first["reviewState"]["limit"]) == (1, 2)
    _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "issue"}],
        },
    )
    second = _call(config, "begin_review", review={})
    assert (second["reviewState"]["round"], second["reviewState"]["limit"]) == (2, 2)
    _call(
        config,
        "record_review",
        review={"verdict": "block", "remainingFindingIds": ["A"]},
    )
    with pytest.raises(TaskDocError, match=r"review-budget-exhausted.*count=2.*limit=2"):
        _call(config, "begin_review", review={})


def test_begin_review_with_eight_settings_opens_eighth(tmp_path: Path) -> None:
    """MIK-R116 evidence 1: with 8 in the settings the eighth begins."""
    _write_user_settings({"orchestration": {"loops": {"defaults": {"maxRounds": 8}}}})
    config = _config(tmp_path)
    _create(config)

    for expected_round in range(1, 9):
        begun = _call(config, "begin_review", review={})
        assert begun["reviewState"]["round"] == expected_round
        assert begun["reviewState"]["limit"] == 8
        if expected_round == 1:
            _block(config)
        else:
            _call(
                config,
                "record_review",
                review={"verdict": "block", "remainingFindingIds": ["A"]},
            )
    with pytest.raises(TaskDocError, match=r"review-budget-exhausted.*count=8.*limit=8"):
        _call(config, "begin_review", review={})


def test_begin_review_follows_changed_settings_without_restart(tmp_path: Path) -> None:
    """MIK-R116 evidence 3: one server, changed number, next begin follows it."""
    config = _config(tmp_path)
    _create(config)

    _call(config, "begin_review", review={})
    _block(config)
    for _ in range(4):
        _call(config, "begin_review", review={})
        _call(
            config,
            "record_review",
            review={"verdict": "block", "remainingFindingIds": ["A"]},
        )
    with pytest.raises(TaskDocError, match=r"review-budget-exhausted.*count=5.*limit=5"):
        _call(config, "begin_review", review={})

    _write_user_settings({"orchestration": {"loops": {"defaults": {"maxRounds": 8}}}})
    for expected_round in (6, 7, 8):
        begun = _call(config, "begin_review", review={})
        assert begun["reviewState"]["round"] == expected_round
        assert begun["reviewState"]["limit"] == 8
        _call(
            config,
            "record_review",
            review={"verdict": "block", "remainingFindingIds": ["A"]},
        )
    with pytest.raises(TaskDocError, match=r"review-budget-exhausted.*count=8.*limit=8"):
        _call(config, "begin_review", review={})


def test_record_only_check_without_new_round(tmp_path: Path) -> None:
    """MIK-R116 evidence 5, both ways, through task_doc."""
    config = _config(tmp_path)
    _create(config)
    candidate = "ab" * 20

    _call(config, "begin_review", review={})
    recorded = _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "issue"}],
            "candidateTree": candidate,
        },
    )
    assert recorded["reviewState"]["candidateTree"] == candidate

    checked = _call(
        config,
        "record_review",
        review={"verdict": "pass", "remainingFindingIds": [], "candidateTree": candidate},
    )
    assert checked["reviewState"]["round"] == 1
    assert checked["reviewState"]["pending"] is False
    assert checked["reviewState"]["remainingFindingIds"] == []

    with pytest.raises(TaskDocError, match=r"review-candidate-changed.*begin_review"):
        _call(
            config,
            "record_review",
            review={
                "verdict": "pass",
                "remainingFindingIds": [],
                "candidateTree": "cd" * 20,
            },
        )


def test_begin_review_refused_when_settings_unreadable(tmp_path: Path) -> None:
    """MIK-R116 failure behavior: an unreadable settings file refuses the begin."""
    path = _write_user_settings({"orchestration": {"loops": {"defaults": {"maxRounds": 2}}}})
    config = _config(tmp_path)
    _create(config)
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(TaskDocError, match="review-settings-unreadable"):
        _call(config, "begin_review", review={})

    # No round was counted, and a get stays readable while the file is broken.
    gotten = _call(config, "get")
    assert gotten["reviewState"]["round"] == 0

    _write_user_settings({"orchestration": {"loops": {"defaults": {"maxRounds": 2}}}})
    begun = _call(config, "begin_review", review={})
    assert (begun["reviewState"]["round"], begun["reviewState"]["limit"]) == (1, 2)


def _head_tree(config: McpRuntimeConfig) -> str:
    repo = config.repositories["agents-remember"].path
    return subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()


def test_replayed_claim_after_a_tracked_mutation_is_refused_before_write(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _create(config)
    claimed = _head_tree(config)
    _call(config, "begin_review", review={})
    first = _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "record only"}],
            "candidateTree": claimed,
        },
    )
    assert first["reviewState"]["round"] == 1
    repo = config.repositories["agents-remember"].path
    (repo / "base.txt").write_text("changed candidate\n", encoding="utf-8")
    assert subprocess.check_output(["git", "diff", "--", "base.txt"], cwd=repo)
    with pytest.raises(TaskDocError, match="review-candidate-changed"):
        _call(
            config,
            "record_review",
            review={"verdict": "pass", "remainingFindingIds": [], "candidateTree": claimed},
        )
    reread = _call(config, "get")
    assert reread["reviewState"]["round"] == 1
    assert reread["reviewState"]["remainingFindingIds"] == ["A"]


def test_unrecorded_tree_claim_is_adopted_but_the_binding_governs(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _create(config)
    _call(config, "begin_review", review={})
    _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "implementation unresolved"}],
        },
    )
    forged = "cd" * 20
    second = _call(
        config,
        "record_review",
        review={"verdict": "pass", "remainingFindingIds": [], "candidateTree": forged},
    )
    assert second["reviewState"]["round"] == 1
    repo = config.repositories["agents-remember"].path
    assert second["reviewState"]["candidateIdentity"] == resolve_candidate_identity(repo)
    assert second["reviewState"]["candidateTree"] == forged
    # The forged string moves nothing: a real mutation afterwards still refuses.
    (repo / "base.txt").write_text("changed candidate\n", encoding="utf-8")
    with pytest.raises(TaskDocError, match="review-candidate-changed"):
        _call(
            config,
            "record_review",
            review={"verdict": "pass", "remainingFindingIds": [], "candidateTree": forged},
        )


def test_legacy_pre_limit_get_carries_the_effective_settings_number(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _create(config)
    _call(
        config,
        "replace",
        fields={
            "id": "review-api",
            "slug": "review_api",
            "title": "Review API",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-01-01T00:00",
            "reviewState": {
                "round": 2,
                "pending": False,
                "baselineFindings": [{"findingId": "A", "description": "issue"}],
                "remainingFindingIds": ["A"],
            },
        },
    )
    answer = _call(config, "get")
    assert answer["reviewState"]["limit"] == 5
    assert answer["reviewState"]["round"] == 2
    assert answer["reviewState"]["remainingFindingIds"] == ["A"]


def test_legacy_pre_limit_get_adds_approved_extra_rounds(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _create(config)
    _call(
        config,
        "replace",
        fields={
            "id": "review-api",
            "slug": "review_api",
            "title": "Review API",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-01-01T00:00",
            "reviewState": {
                "round": 2,
                "pending": False,
                "baselineFindings": [{"findingId": "A", "description": "issue"}],
                "remainingFindingIds": ["A"],
                "limit": 0,
                "developerApproval": "two extra rounds",
                "additionalRounds": 2,
            },
        },
    )
    answer = _call(config, "get")
    assert answer["reviewState"]["limit"] == 7
    assert answer["reviewState"]["developerApproval"] == "two extra rounds"


def test_direct_first_record_carries_the_settings_number(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _create(config)
    answer = _call(
        config,
        "record_review",
        review={"verdict": "block", "findings": [{"findingId": "A", "description": "issue"}]},
    )
    assert answer["reviewState"]["limit"] == 5
    assert answer["reviewState"]["round"] == 1


def test_clean_recorded_round_does_not_reseal_without_begin(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _create(config)
    _call(config, "begin_review", review={})
    claimed = _head_tree(config)
    _call(
        config,
        "record_review",
        review={"verdict": "pass", "findings": [], "candidateTree": claimed},
    )
    with pytest.raises(TaskDocError, match="review-candidate-changed"):
        _call(
            config,
            "record_review",
            review={
                "verdict": "block",
                "findings": [{"findingId": "NEW", "description": "new criterion"}],
                "candidateTree": "cd" * 20,
            },
        )
    reread = _call(config, "get")
    assert reread["reviewState"]["round"] == 1
    assert reread["reviewState"]["remainingFindingIds"] == []


def test_untracked_addition_between_records_is_refused(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _create(config)
    claimed = _head_tree(config)
    _call(config, "begin_review", review={})
    _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "record only"}],
            "candidateTree": claimed,
        },
    )
    repo = config.repositories["agents-remember"].path
    (repo / "notes-scratch.txt").write_text("untracked bytes\n", encoding="utf-8")
    with pytest.raises(TaskDocError, match="review-candidate-changed"):
        _call(
            config,
            "record_review",
            review={"verdict": "pass", "remainingFindingIds": [], "candidateTree": claimed},
        )
    reread = _call(config, "get")
    assert reread["reviewState"]["remainingFindingIds"] == ["A"]


def test_route_sibling_record_carries_the_plane_tree_and_the_binding(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _create(config)
    repo = config.repositories["agents-remember"].path
    doc = TaskDocument.model_validate(
        {
            "id": "review-api",
            "slug": "review_api",
            "title": "Review API",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-01-01T00:00",
        }
    )
    plane_tree = _head_tree(config)
    review = SimpleNamespace(candidateTree=plane_tree, model_dump=lambda mode="json": None)
    updated = _with_route_review_state(
        doc,
        {"verdict": "block", "findings": [{"findingId": "A", "description": "route"}]},
        review,
        settings_limit=5,
        actual_candidate=resolve_candidate_identity(repo),
    )
    assert updated.reviewState is not None
    assert updated.reviewState.candidateTree == plane_tree
    assert updated.reviewState.candidateIdentity == resolve_candidate_identity(repo)
    assert updated.reviewState.limit == 5


def test_failed_identity_reads_refuse_around_a_real_mutation(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    _create(config)
    claimed = _head_tree(config)
    _call(config, "begin_review", review={})
    first = _call(
        config,
        "record_review",
        review={
            "verdict": "block",
            "findings": [{"findingId": "A", "description": "record only"}],
            "candidateTree": claimed,
        },
    )
    assert first["reviewState"]["candidateIdentity"] is not None
    # Failure after record: the resolver goes unproven while tracked bytes change.
    monkeypatch.setattr(tools, "resolve_candidate_identity", lambda root: None)
    repo = config.repositories["agents-remember"].path
    (repo / "base.txt").write_text("changed candidate\n", encoding="utf-8")
    with pytest.raises(TaskDocError, match="review-candidate-changed"):
        _call(
            config,
            "record_review",
            review={"verdict": "pass", "remainingFindingIds": [], "candidateTree": claimed},
        )
    reread = _call(config, "get")
    assert reread["reviewState"]["round"] == 1
    assert reread["reviewState"]["remainingFindingIds"] == ["A"]
    # Failure before record: a recorded round with no binding admits no
    # same-round record even when the caller replays the recorded tree.
    monkeypatch.setattr(tools, "resolve_candidate_identity", lambda root: None)
    _call(
        config,
        "replace",
        fields={
            "id": "review-api",
            "slug": "review_api",
            "title": "Review API",
            "kind": "subTask",
            "repo": "agents-remember",
            "createdAt": "2026-01-01T00:00",
            "reviewState": {
                "round": 1,
                "pending": False,
                "baselineFindings": [{"findingId": "A", "description": "record only"}],
                "remainingFindingIds": ["A"],
                "candidateTree": claimed,
            },
        },
    )
    with pytest.raises(TaskDocError, match="review-candidate-changed"):
        _call(
            config,
            "record_review",
            review={"verdict": "pass", "remainingFindingIds": [], "candidateTree": claimed},
        )
