"""Settings and launch protections for a task seat's single step-up."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest
import test_paseo_launch as launch_support
from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.role_launch_context import resolve_role_launch_context
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.task_docs.task_review_step_up import with_review_step_up
from agents_remember.cli import (
    paseo_catalog,
    role_launch_preparation,
    role_launch_routes,
    role_step_up_decisions,
    role_step_up_runtime,
)
from agents_remember.cli.paseo_catalog import (
    LaunchSelectionRefused,
    forget_launcher_catalogs,
    resolve_agent_selection,
)
from agents_remember.cli.paseo_role_tools import _started
from agents_remember.cli.role_step_up import select_step_up
from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.models.role_agents import RoleStartResponse
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.tasks import write_task_doc
from agents_remember.tasks.document_refs import ResolvedTaskDocument, TaskDocumentTopology
from agents_remember.tasks.launch_review_read import tolerate_unreadable_review
from agents_remember.worktrees.review_history import (
    begin_task_review,
    failed_role_tries,
    record_task_review,
)
from paseo_launch_test_support import CATALOG as LAUNCH_CATALOG
from test_paseo_catalog import CATALOG, HARNESS_ORDER, FakeBridge, runtime_config
from test_review_state import _document


def write_role(role: str, knobs: dict) -> Path:
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"orchestration": {"roles": {role: knobs}}}))
    return path


def test_user_step_up_accepts_own_choices_and_defaults_to_three(tmp_path: Path) -> None:
    write_role(
        "worker",
        {
            "harness": "codex",
            "model": "a",
            "stepUp": {
                "harness": "hermes",
                "model": "b",
                "effort": "high",
                "mode": "auto",
                "featureValues": {"auto_accept": True},
                "notes": "Take the existing candidate.",
            },
        },
    )
    role = load_agentic_settings(tmp_path).role_knobs("worker")
    assert role.step_up is not None
    assert role.step_up.after_tries == 3
    assert role.step_up.choices.harness == "hermes"
    assert role.step_up.choices.feature_values == {"auto_accept": True}
    assert role.step_up.choices.mode == "auto"
    assert role.step_up.choices.step_up is None


def test_invalid_step_ups_name_file_and_exact_key(tmp_path: Path) -> None:
    cases = [
        ("worker", {"model": "a", "stepUp": {"model": "b", "typo": 1}}, "stepUp"),
        ("worker", {"model": "a", "stepUp": {"model": "b", "afterTries": 0}}, "afterTries"),
        ("worker", {"model": "a", "stepUp": {"model": "b", "afterTries": True}}, "afterTries"),
        ("worker", {"harness": "codex", "model": "a", "stepUp": {"model": "a"}}, "stepUp"),
        ("worker", {"model": "a", "stepUp": {"model": "a"}}, "stepUp"),
        ("reviewer", {"model": "a", "stepUp": {"model": "b", "afterTries": 3}}, "afterTries"),
        ("worker", {"model": "a", "stepUp": {"model": "b", "stepUp": {}}}, "stepUp"),
    ]
    for role, knobs, key in cases:
        path = write_role(role, knobs)
        with pytest.raises(AgenticSettingsError) as refusal:
            load_agentic_settings(tmp_path)
        assert str(path) in str(refusal.value)
        assert f"orchestration.roles.{role}.stepUp" in str(refusal.value)
        assert key in str(refusal.value)


def test_failed_tries_are_review_kind_rounds_not_record_checks(launch_world) -> None:

    doc = begin_task_review(_document())
    doc = record_task_review(
        doc,
        {
            "verdict": "block",
            "reviewKind": "code",
            "findings": [{"findingId": "A", "description": "broken"}],
            "verdictRef": "reports/first.md",
        },
        actual_candidate="one",
    )
    assert failed_role_tries(doc, "worker") == 1
    assert failed_role_tries(doc, "curator") == 0
    assert doc.reviewState is not None
    before = doc.reviewState.model_dump(mode="json")
    doc = record_task_review(
        doc,
        {"verdict": "block", "remainingFindingIds": ["A"], "verdictRef": "reports/fixed-record.md"},
        actual_candidate="one",
    )
    assert failed_role_tries(doc, "worker") == 1
    assert doc.reviewState is not None
    assert doc.reviewState.round == before["round"]
    assert doc.reviewState.limit == before["limit"]
    assert doc.reviewState.recordedRounds[0].verdictRef == "reports/fixed-record.md"
    assert doc.reviewState.recordedRounds[0].findingsRemain is True
    world = launch_world
    document = world.leaf.document.model_copy(update={"reviewState": doc.reviewState})
    world.leaf = ResolvedTaskDocument(ref=world.leaf.ref, path=world.leaf.path, document=document)
    write_task_doc(world.leaf.path.parent, document)
    defaults = {"role": "worker", "agent": "codex", "model": "gpt-a", "stepUp": {"model": "gpt-b"}}
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    request = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "The repairs stalled."}
    )
    assert world.dispatch(request)[1]["status"] == "running"
    material = json.loads(
        Path(world.receipt(request)["report"]["path"]).with_suffix(".step-up.json").read_text()
    )
    assert material["lastVerdict"] == "reports/fixed-record.md"
    assert material["remainingFindingIds"] == ["A"]
    code_before_memory = doc.reviewState.model_dump(mode="json")
    doc = begin_task_review(doc, {"reviewKind": "memory"})
    doc = record_task_review(
        doc,
        {
            "verdict": "block",
            "reviewKind": "memory",
            "findings": [{"findingId": "M1", "description": "memory is incomplete"}],
        },
        actual_candidate="two",
    )
    assert failed_role_tries(doc, "worker") == 1
    assert failed_role_tries(doc, "curator") == 1
    assert failed_role_tries(doc, "reviewer") == 0
    restored = type(doc).model_validate_json(doc.model_dump_json())
    assert failed_role_tries(restored, "curator") == 1
    assert restored.reviewState is not None
    assert restored.reviewState.model_dump(mode="json") == code_before_memory
    assert restored.reviewState.limit == 5
    assert restored.memoryReviewState is not None
    assert restored.memoryReviewState.round == 1
    assert restored.memoryReviewState.limit == 5
    assert restored.memoryReviewState.remainingFindingIds == ["M1"]


def test_step_up_selection_uses_own_choices_and_never_steps_down(tmp_path: Path) -> None:

    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "a",
        "mode": "danger",
        "effort": "low",
        "featureValues": {"old": True},
        "notes": "Private base instructions.",
        "stepUp": {"harness": "hermes", "model": "b", "afterTries": 3},
        "hostRole": {"profile": {"provider": "codex"}},
    }
    doc = _document()
    assert select_step_up(defaults, doc)["model"] == "a"
    manual = select_step_up(defaults, doc, reason="The task needs another approach.")
    assert manual["model"] == "b"
    assert manual["mode"] is None
    assert manual["effort"] is None
    assert manual["featureValues"] == {}
    assert manual["hostRole"] == {}
    assert manual["stepUpSelection"]["from"]["notes"] == {
        "source": "our settings",
        "key": "orchestration.roles.worker.notes",
        "digest": "sha256:" + hashlib.sha256(b"Private base instructions.").hexdigest(),
        "present": True,
        "baselineReplaced": False,
    }
    assert "Private base instructions." not in json.dumps(manual["stepUpSelection"])
    assert (
        manual["stepUpSelection"]["source"] == "step-up requested: The task needs another approach."
    )
    again = select_step_up(defaults, doc, prior=manual["stepUpSelection"])
    assert again["model"] == "b"
    with pytest.raises(ValueError, match=r"orchestration.roles.worker.stepUp"):
        select_step_up({**defaults, "stepUp": None}, doc, reason="Try another approach.")


def test_count_selects_step_up_at_and_above_threshold() -> None:

    defaults = {"role": "worker", "agent": "codex", "model": "a", "stepUp": {"model": "b"}}
    doc = _document()
    for number in range(1, 5):
        doc = begin_task_review(doc)
        payload = (
            {"verdict": "block", "findings": [{"findingId": "A", "description": "broken"}]}
            if number == 1
            else {"verdict": "block", "remainingFindingIds": ["A"]}
        )
        doc = record_task_review(doc, payload)
        selected = select_step_up(defaults, doc)
        assert selected["model"] == ("b" if number >= 3 else "a")
        if number >= 3:
            assert selected["stepUpSelection"]["source"] == f"step-up after {number} tries"


def test_step_up_catalog_provenance_and_unoffered_choice(tmp_path: Path) -> None:

    config = runtime_config(tmp_path)
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b", "mode": None},
    }
    selected = select_step_up(defaults, None, reason="Use a different configured model.")
    forget_launcher_catalogs()
    with patch.object(paseo_catalog, "bridge_call", FakeBridge(CATALOG)):
        agent, options = resolve_agent_selection(config, selected, HARNESS_ORDER, None)
        assert agent == "codex"
        assert options["model"] == "gpt-b"
        assert options["choices"]["model"]["source"] == selected["stepUpSelection"]["source"]
        with pytest.raises(
            LaunchSelectionRefused, match=r"orchestration.roles.worker.stepUp.model"
        ):
            resolve_agent_selection(
                config, {**selected, "model": "not-offered"}, HARNESS_ORDER, None
            )
    forget_launcher_catalogs()


@pytest.fixture
def launch_world():
    world = launch_support.PaseoLaunchTestCase()
    world.setUp()
    catalog = deepcopy(LAUNCH_CATALOG)
    catalog["providers"][0]["models"].append(
        {"id": "gpt-b", "label": "Configured alternative", "efforts": []}
    )
    world.replace(world.runtime, "_catalog", return_value=catalog)
    try:
        yield world
    finally:
        world.doCleanups()


def test_manual_start_replaces_idle_holder_and_keeps_step_up_on_restart(launch_world) -> None:

    world = launch_world
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "effort": "low",
        "stepUp": {"harness": "codex", "model": "gpt-b"},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker")
    world.dispatch(original)
    old_id = world.receipt(original)["agentId"]
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {
            **world.request("worker").model_dump(by_alias=True),
            "stepUp": "A different approach is needed.",
        }
    )
    _, result = world.dispatch(request)
    assert result["status"] == "running"
    assert result["stepUp"]["source"] == "step-up requested: A different approach is needed."
    assert world.runtime.agents[old_id]["archivedAt"] is not None
    assert world.receipt(request)["agent"]["model"] == "gpt-b"

    tool_answer = _started(world.config, request)
    assert "Stepped-up" in tool_answer["detail"]
    assert tool_answer["stepUp"]["model"] == "gpt-b"
    assert RoleStartResponse.model_validate(tool_answer).stepUp is not None
    artifact = Path(world.receipt(request)["handoverArtifact"]["path"]).read_text()
    assert "gpt-a" in artifact
    assert "A different approach is needed." in artifact
    assert "remainingFindingIds" in artifact
    assert "uncommittedFiles" in artifact
    world.agent_of(request).update(archivedAt="gone", status="idle")
    world.leaf = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref)
    decisions = [
        item for item in world.leaf.document.decisions if item.decision == "Step-up worker."
    ]
    assert len(decisions) == 1
    assert json.loads(decisions[0].rationale)["to"]["model"] == "gpt-b"
    world.receipt_path(request).unlink()
    _, restarted = world.dispatch(world.request("worker"))
    assert restarted["sessionOptions"]["model"] == "gpt-b"
    world.runtime.agents[restarted["execution"]["agentId"]].update(archivedAt="gone", status="idle")
    defaults["stepUp"] = None
    _, returned = world.dispatch(world.request("worker"))
    assert returned["sessionOptions"]["model"] == "gpt-a"
    assert "no longer name a step-up" in returned["warning"]


@pytest.mark.parametrize(
    "override", [{"modelId": "gpt-b"}, {"effortId": "high"}, {"agentId": "eve"}]
)
def test_step_up_provenance_uses_actual_predecessor_choices(launch_world, override) -> None:
    world = launch_world
    catalog = deepcopy(LAUNCH_CATALOG)
    catalog["providers"][0]["models"].append(
        {
            "id": "gpt-b",
            "label": "Configured alternative",
            "efforts": [{"id": "high", "label": "High"}],
        }
    )
    world.replace(world.runtime, "_catalog", return_value=catalog)
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "effort": "low",
        "notes": "Private predecessor instructions.",
        "stepUp": {"model": "gpt-b", "notes": "Private successor instructions."},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker", **override)
    world.dispatch(original)
    previous = world.receipt(original)
    actual = previous["agent"]
    expected = {
        "agent": actual["id"],
        **{
            key: actual.get(key, previous["sessionOptions"].get(key))
            for key in ("model", "effort", "serviceTier", "mode", "featureValues")
        },
        "notes": {
            "source": "our settings",
            "key": "orchestration.roles.worker.notes",
            "digest": "sha256:" + hashlib.sha256(b"Private predecessor instructions.").hexdigest(),
            "present": True,
            "baselineReplaced": False,
        },
    }
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {
            **world.request("worker").model_dump(by_alias=True),
            "stepUp": "The existing approach has stalled.",
            "agentOverride": {"modelId": "gpt-a"} if "modelId" in override else override,
        }
    )
    _, answer = world.dispatch(request)
    assert answer["stepUp"]["from"] == expected
    field, actual_choice = (
        ("model", "gpt-a")
        if "modelId" in override
        else ("effort", "high")
        if "effortId" in override
        else ("agent", "eve")
    )
    assert answer["choices"][field]["source"] == "start call"
    assert answer["choices"][field]["value"] == actual_choice
    assert answer["stepUp"]["model"] == world.receipt(request)["agent"]["model"]
    material = json.loads(
        Path(world.receipt(request)["report"]["path"]).with_suffix(".step-up.json").read_text()
    )
    assert material["fromModel"] == actual["model"]
    artifact = Path(world.receipt(request)["handoverArtifact"]["path"]).read_text()
    assert f"from model {actual['model']}" in artifact
    document = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref).document
    decision = next(item for item in document.decisions if item.decision == "Step-up worker.")
    assert json.loads(decision.rationale)["from"] == expected
    assert json.loads(decision.rationale)["to"][field] == actual_choice
    destination = {
        "source": "step-up requested: The existing approach has stalled.",
        "key": "orchestration.roles.worker.stepUp.notes",
        "origin": {"key": "orchestration.roles.worker.stepUp.notes"},
        "digest": "sha256:" + hashlib.sha256(b"Private successor instructions.").hexdigest(),
        "present": True,
        "baselineReplaced": False,
    }
    assert json.loads(decision.rationale)["to"]["notes"] == destination
    assert json.loads(decision.rationale)["requestedTo"]["notes"] == destination
    for text in ("Private predecessor instructions.", "Private successor instructions."):
        assert text not in json.dumps(answer)
        assert text not in decision.rationale


def test_step_up_refuses_active_turn_or_permission_without_archiving(launch_world) -> None:
    world = launch_world
    defaults = {"role": "worker", "agent": "codex", "model": "gpt-a", "stepUp": {"model": "gpt-b"}}
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker")
    world.dispatch(original)
    old_id = world.receipt(original)["agentId"]
    request = RoleDispatchRequest.model_validate(
        {
            **world.request("worker").model_dump(by_alias=True),
            "stepUp": "The repairs have stalled.",
            "replacesInterruptTurn": True,
        }
    )
    for state, words in [
        ({"status": "running"}, "turn"),
        ({"status": "idle", "pendingPermissions": [{"name": "Edit"}]}, "Edit"),
    ]:
        world.agent_of(original).update(state)
        error = world.refused(request)
        assert words in str(error.detail)
        assert world.runtime.agents[old_id]["archivedAt"] is None


def test_recorded_round_names_the_step_up_without_granting_a_round(launch_world) -> None:
    world = launch_world
    write_role("worker", {"model": "gpt-a", "stepUp": {"model": "gpt-b", "afterTries": 1}})
    answer = task_doc_tool(
        world.config,
        TaskDocTarget(repo_id="agents-remember", task_name="master", slug="01_leaf"),
        operation="record_review",
        edit=TaskDocEdit(
            review={"verdict": "block", "findings": [{"findingId": "A", "description": "broken"}]}
        ),
    )
    assert "worker seat steps up" in answer["nextAction"]
    assert "after 1 tries" in answer["nextAction"]
    assert answer["reviewState"]["round"] == 1
    assert answer["reviewState"]["limit"] == 5
    assert answer["reviewState"]["remainingFindingIds"] == ["A"]


def test_failed_step_up_creation_leaves_empty_seat_and_same_request_retries(launch_world) -> None:
    world = launch_world
    defaults = {"role": "worker", "agent": "codex", "model": "gpt-a", "stepUp": {"model": "gpt-b"}}
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker")
    world.dispatch(original)
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {
            **world.request("worker").model_dump(by_alias=True),
            "stepUp": "The current approach has stalled.",
        }
    )
    world.runtime.fail("agent-create", "paseo_call_failed", "refused")
    code, refused = world.dispatch(request)
    assert code == 502
    assert "seat is empty" in refused["detail"]
    assert refused["canRetry"] is True
    assert world.dispatch(request)[1]["status"] == "running"


def test_host_refusal_keeps_holder_and_user_permission_authority(launch_world) -> None:
    world = launch_world
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "not-offered"},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker")
    world.dispatch(original)
    old_id = world.receipt(original)["agentId"]
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {
            **world.request("worker").model_dump(by_alias=True),
            "stepUp": "Another approach is needed.",
        }
    )
    world.runtime.calls.clear()
    error = world.refused(request)
    assert "orchestration.roles.worker.stepUp.model" in str(error.detail)
    assert world.runtime.agents[old_id]["archivedAt"] is None
    assert not any(command == "agent-archive" for command, _ in world.runtime.calls)


def test_third_failed_round_changes_harness_with_own_mode_and_keeps_reviewer(launch_world) -> None:
    world = launch_world
    catalog = deepcopy(LAUNCH_CATALOG)
    catalog["providers"].append(
        {
            "id": "hermes",
            "label": "Hermes",
            "modes": [{"id": "own-mode", "label": "User mode"}],
            "models": [
                {
                    "id": "alternative",
                    "label": "User alternative",
                    "efforts": [],
                    "features": [
                        {
                            "id": "auto_accept",
                            "label": "User permission",
                            "type": "toggle",
                            "value": False,
                        }
                    ],
                }
            ],
        }
    )
    world.replace(world.runtime, "_catalog", return_value=catalog)
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {
            "harness": "hermes",
            "model": "alternative",
            "mode": "own-mode",
            "featureValues": {"auto_accept": True},
        },
    }
    world.replace(
        role_launch_preparation,
        "_role_defaults",
        side_effect=lambda _config, context: (
            defaults
            if context.role == "worker"
            else {"role": context.role, "agent": "codex", "model": "gpt-a"},
            ("codex", "hermes", "eve"),
        ),
    )
    original = world.request("worker")
    world.dispatch(original)
    reviewer = world.request("reviewer")
    world.dispatch(reviewer)
    reviewer_id = world.receipt(reviewer)["agentId"]
    old_id = world.receipt(original)["agentId"]
    world.agent_of(original).update(status="idle")
    document = world.leaf.document
    for number in range(1, 4):
        document = begin_task_review(document)
        review = (
            {"verdict": "block", "findings": [{"findingId": "A", "description": "broken"}]}
            if number == 1
            else {"verdict": "block", "remainingFindingIds": ["A"]}
        )
        document = record_task_review(
            document, {**review, "verdictRef": f"reports/verdict-{number}.md"}
        )
    world.leaf = ResolvedTaskDocument(ref=world.leaf.ref, path=world.leaf.path, document=document)
    write_task_doc(world.leaf.path.parent, document)
    request = world.request("worker")
    world.runtime.calls.clear()
    _, answer = world.dispatch(request)
    assert answer["sessionOptions"]["mode"] == "own-mode"
    assert answer["sessionOptions"]["featureValues"] == {"auto_accept": True}
    assert answer["choices"]["mode"]["source"] == "step-up after 3 tries"
    assert world.runtime.agents[old_id]["archivedAt"] is not None
    assert world.receipt(reviewer)["agentId"] == reviewer_id
    material = json.loads(
        Path(world.receipt(request)["report"]["path"]).with_suffix(".step-up.json").read_text()
    )
    assert material["remainingFindingIds"] == ["A"]
    assert material["lastVerdict"] == "reports/verdict-3.md"
    persisted = (
        TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref).document
    )
    assert persisted.reviewState == document.reviewState


@pytest.mark.parametrize("review", [{"round": "unreadable"}, {"recordedRounds": "broken"}])
def test_unreadable_review_launches_base_with_warning(launch_world, review) -> None:
    world = launch_world
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b", "afterTries": 1},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    # Supply a real canonical hierarchy: do not mock away the failing document read.
    sprint = world.sprint.document.model_dump(mode="json")
    sprint["orchestrates"] = [world.master.path.parent.name]
    write_task_doc(world.sprint.path.parent, type(world.sprint.document).model_validate(sprint))
    master = world.master.document.model_dump(mode="json")
    master["subTasks"] = [
        {"number": world.leaf.document.id, "name": "Leaf", "file": world.leaf.path.name}
    ]
    write_task_doc(world.master.path.parent, type(world.master.document).model_validate(master))
    original = world.request("worker")
    world.dispatch(original)
    world.agent_of(original).update(archivedAt="gone", status="idle")
    raw = json.loads(world.leaf.path.read_text())
    raw["reviewState"] = review
    world.leaf.path.write_text(json.dumps(raw))
    before = world.leaf.path.read_bytes()
    world.replace(role_launch_routes, "resolve_role_launch_context", resolve_role_launch_context)
    world.replace(
        role_launch_preparation, "resolve_role_launch_context", resolve_role_launch_context
    )
    code, answer = world.dispatch(world.request("worker"))
    assert code == 200
    assert answer["sessionOptions"]["model"] == "gpt-a"
    assert not answer.get("stepUp")
    assert "count could not be read" in answer["warning"]
    assert "reviewState" in answer["warning"]
    assert world.leaf.path.read_bytes() == before
    # The exception is launch-only: review mutations and ordinary reads remain strict.
    with pytest.raises(ValueError):
        TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref)


@pytest.mark.parametrize("manual", [True, False])
def test_unreadable_review_preserves_explicit_or_prior_step_up(launch_world, manual) -> None:
    world = launch_world
    raw = world.leaf.document.model_dump(mode="json")
    raw["reviewState"] = {"round": "broken"}
    with tolerate_unreadable_review():
        document = type(world.leaf.document).model_validate(raw)
    world.leaf = ResolvedTaskDocument(ref=world.leaf.ref, path=world.leaf.path, document=document)
    defaults = {"role": "worker", "agent": "codex", "model": "gpt-a", "stepUp": {"model": "gpt-b"}}
    prior = None if manual else {"stepUp": {"source": "step-up after 3 tries"}}
    with patch.object(role_step_up_runtime, "_read_receipt", return_value=prior):
        selected = role_step_up_runtime.selected_defaults(
            world.config,
            world.context(world.config, world.request("worker")),
            defaults,
            "The approach has stalled." if manual else None,
        )
    assert selected["model"] == "gpt-b"
    assert "count could not be read" in selected["stepUpWarning"]


def test_launch_review_fallback_preserves_raw_evidence_and_other_validation() -> None:
    raw = _document().model_dump(mode="json")
    raw["reviewState"] = {
        "round": 7,
        "limit": 8,
        "remainingFindingIds": ["A"],
        "recordedRounds": "broken",
    }
    with tolerate_unreadable_review():
        document = type(_document()).model_validate(raw)
        assert (
            document.model_dump(mode="json", exclude_none=True)["reviewState"] == raw["reviewState"]
        )
        assert "reviewState" not in document.model_dump(exclude={"reviewState"})
        assert "reviewState" not in document.model_dump(include={"id"})
        with pytest.raises(ValueError):
            type(document).model_validate({**raw, "unknownTaskKey": True})
    with pytest.raises(ValueError):
        type(document).model_validate(raw)


@pytest.mark.parametrize("response_lost", [False, True])
def test_step_up_replay_reconciles_decision_before_host_and_survives_receipt_loss(
    launch_world, response_lost
) -> None:
    world = launch_world
    defaults = {"role": "worker", "agent": "codex", "model": "gpt-a", "stepUp": {"model": "gpt-b"}}
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker")
    world.dispatch(original)
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "The approach stalled."}
    )
    real_append = role_step_up_decisions.task_doc_tool

    def fail_append(*args, **kwargs):
        if response_lost:
            assert real_append(*args, **kwargs)["ok"]
        return {"ok": False, "error": "injected append acknowledgement failure"}

    world.runtime.calls.clear()
    with patch.object(role_step_up_decisions, "task_doc_tool", side_effect=fail_append):
        assert "decision could not be recorded" in str(world.refused(request).detail)
        assert world.receipt(request)["status"] == "starting"
        if not response_lost:
            assert "decision could not be recorded" in str(world.refused(request).detail)
        assert not any(command == "agent-create" for command, _ in world.runtime.calls)
    assert world.dispatch(request)[1]["status"] == "running"
    assert world.dispatch(request)[1]["status"] == "running"
    document = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref).document
    decisions = [item for item in document.decisions if item.decision == "Step-up worker."]
    assert len(decisions) == 1
    assert json.loads(decisions[0].rationale)["requestId"] == str(request.request_id)
    world.leaf = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref)
    world.agent_of(request).update(archivedAt="gone", status="idle")
    world.receipt_path(request).unlink()
    _, restarted = world.dispatch(world.request("worker"))
    assert restarted["sessionOptions"]["model"] == "gpt-b"
    assert restarted["stepUp"]
    document = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref).document
    assert len([item for item in document.decisions if item.decision == "Step-up worker."]) == 1


@pytest.mark.parametrize("fail_observation", [False, True])
def test_host_default_successor_model_is_observed_without_losing_requested_provenance(
    launch_world, fail_observation
) -> None:
    world = launch_world
    defaults = {"role": "worker", "agent": "codex", "model": "gpt-a", "stepUp": {"harness": "eve"}}
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker")
    world.dispatch(original)
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "The approach stalled."}
    )
    real_edit = role_step_up_decisions.task_doc_tool
    unrelated_rationale = "A separate human-authored decision, not launch metadata."

    def edit(*args, **kwargs):
        if fail_observation and kwargs["operation"] == "replace":
            return {"ok": False, "error": "injected observed-choice publication failure"}
        result = real_edit(*args, **kwargs)
        if kwargs["operation"] == "append_decision":
            assert real_edit(
                *args,
                operation="append_decision",
                edit=TaskDocEdit(
                    decision={
                        "at": "2026-10-02T00:00:00+00:00",
                        "decision": "Step-up worker.",
                        "rationale": unrelated_rationale,
                    }
                ),
            )["ok"]
        return result

    with patch.object(role_step_up_decisions, "task_doc_tool", side_effect=edit):
        if fail_observation:
            assert "decision could not be recorded" in str(world.refused(request).detail)
            assert world.receipt(request)["status"] == "running"
        else:
            assert world.dispatch(request)[1]["status"] == "running"
    _, answer = world.dispatch(request)
    assert answer["stepUp"]["model"] == "eve-default"
    assert answer["stepUp"]["requestedModel"] is None
    assert answer["choices"]["model"]["source"] == "host default"
    assert answer["choices"]["model"]["observedValue"] == "eve-default"
    assert world.receipt(request)["sessionOptions"].get("model") is None
    tool = _started(world.config, request)
    assert "Stepped-up · eve-default" in tool["detail"]
    document = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref).document
    decisions = [item for item in document.decisions if item.decision == "Step-up worker."]
    assert len(decisions) == 2
    assert any(item.rationale == unrelated_rationale for item in decisions)
    data = json.loads(
        next(item.rationale for item in decisions if item.rationale != unrelated_rationale)
    )
    assert data["to"]["model"] == "eve-default"
    assert data["requestedTo"]["model"] is None
    assert data["choices"]["model"]["source"] == "host default"
    assert data["requestId"] == str(request.request_id)
    assert len([1 for command, _ in world.runtime.calls if command == "agent-create"]) == 2


@pytest.mark.parametrize("history", [[], [{"round": 3, "kind": "code", "findingsRemain": True}]])
def test_incomplete_historical_count_warns_without_inventing_tries(launch_world, history) -> None:
    world = launch_world
    raw = world.leaf.document.model_dump(mode="json")
    raw["reviewState"] = {"round": 3, "limit": 5, "recordedRounds": history}
    document = type(world.leaf.document).model_validate(raw)
    world.leaf = ResolvedTaskDocument(ref=world.leaf.ref, path=world.leaf.path, document=document)
    write_task_doc(world.leaf.path.parent, document)
    before = json.loads(world.leaf.path.read_text())["reviewState"]
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b", "afterTries": 1},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    assert failed_role_tries(document, "worker") is None
    settings_path = user_settings_path_for_write()
    settings = json.loads(settings_path.read_text())
    settings["orchestration"] = {
        "roles": {"worker": {"model": "gpt-a", "stepUp": {"model": "gpt-b", "afterTries": 1}}}
    }
    settings_path.write_text(json.dumps(settings))
    review_answer = with_review_step_up(
        world.config,
        {
            "ok": True,
            "operation": "task_doc.record_review",
            "reviewState": {
                **before,
                "baselineFindings": [{"findingId": "A", "description": "broken"}],
                "remainingFindingIds": ["A"],
            },
        },
    )
    assert "count could not be read" in review_answer["nextAction"]
    assert "seat steps up" not in review_answer["nextAction"]
    ordinary = world.request("worker")
    _, base = world.dispatch(ordinary)
    assert base["sessionOptions"]["model"] == "gpt-a"
    assert not base.get("stepUp")
    assert "count could not be read" in base["warning"]
    assert "recordedRounds" in base["warning"]
    assert str(world.leaf.path) in base["warning"]
    world.agent_of(ordinary).update(status="idle")
    explicit = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "The repairs stalled."}
    )
    _, stepped = world.dispatch(explicit)
    assert stepped["sessionOptions"]["model"] == "gpt-b"
    assert stepped["stepUp"]["tries"] is None
    assert "count could not be read" in stepped["warning"]
    world.agent_of(explicit).update(archivedAt="gone", status="idle")
    world.receipt_path(explicit).unlink()
    world.leaf = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref)
    _, retained = world.dispatch(world.request("worker"))
    assert retained["sessionOptions"]["model"] == "gpt-b"
    assert retained["stepUp"]["tries"] is None
    assert "count could not be read" in retained["warning"]
    assert json.loads(world.leaf.path.read_text())["reviewState"] == before


def test_repository_cannot_supply_step_up(tmp_path: Path) -> None:
    write_role("worker", {"model": "a"})
    repo = tmp_path / "repo"
    local = repo / "system/settings.json"
    local.parent.mkdir(parents=True)
    local.write_text(
        json.dumps({"orchestration": {"roles": {"worker": {"stepUp": {"model": "b"}}}}})
    )
    with pytest.raises(AgenticSettingsError, match="stepUp is user-layer only") as refusal:
        load_agentic_settings(tmp_path, repo)
    assert str(local) in str(refusal.value)
