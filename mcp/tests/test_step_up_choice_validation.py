"""Regressions for step-up choice validation and post-publication advice."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import test_role_step_up
from _user_settings_guard import user_settings_path_for_write
from agents_remember.application.task_docs import task_review_step_up
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.cli import role_launch_preparation
from agents_remember.cli.role_launch_defaults import role_defaults
from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.tasks.document_refs import TaskDocumentTopology
from paseo_launch_test_support import CATALOG

launch_world = test_role_step_up.launch_world


@pytest.mark.parametrize(
    ("alternative", "key"),
    [
        ({"featureValues": {"auto_accept": True}}, "featureValues.auto_accept"),
        ({"featureValues": {"service_tier": "priority"}}, "featureValues.service_tier"),
        ({"serviceTier": "fast"}, "serviceTier"),
        (
            {"serviceTier": "fast", "featureValues": {"service_tier": "priority"}},
            "serviceTier",
        ),
    ],
)
def test_step_up_feature_refusal_names_authored_key_before_retirement(
    launch_world, alternative, key
) -> None:
    world = launch_world
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b", **alternative},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker")
    assert world.dispatch(original)[1]["status"] == "running"
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "Try another approach."}
    )
    world.runtime.calls.clear()
    refusal = world.refused(request)
    assert f"orchestration.roles.worker.stepUp.{key}" in str(refusal.detail)
    assert world.agent_of(original)["archivedAt"] is None
    assert not any(command == "agent-archive" for command, _ in world.runtime.calls)


def test_cross_model_omitted_mode_uses_host_default_without_changing_base(launch_world) -> None:
    world = launch_world
    catalog = deepcopy(CATALOG)
    provider = catalog["providers"][0]
    provider["models"].append({"id": "gpt-b", "efforts": []})
    provider["modes"] = [{"id": "auto"}, {"id": "auto-review"}]
    provider["defaultModeId"] = "auto-review"
    world.replace(world.runtime, "_catalog", return_value=catalog)
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b"},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    original = world.request("worker")
    base = world.dispatch(original)[1]
    assert base["sessionOptions"]["mode"] == "auto"
    assert base["choices"]["mode"]["source"] == "least permissive mode"
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "Try another approach."}
    )
    answer = world.dispatch(request)[1]
    assert answer["status"] == "running"
    assert answer["sessionOptions"]["mode"] == "auto-review"
    assert answer["choices"]["mode"]["value"] == "auto-review"
    assert answer["choices"]["mode"]["source"] == "host default"
    create = [payload for command, payload in world.runtime.calls if command == "agent-create"][-1]
    assert create["modeId"] == "auto-review"


@pytest.mark.parametrize(
    ("flat", "level", "owner"),
    [
        (
            {"model": "a", "stepUp": {"model": "b"}},
            {"model": "b"},
            "orchestration.roles.worker.stepUp",
        ),
        (
            {"model": "b"},
            {"stepUp": {"model": "b"}},
            "orchestration.rolesPerLevel.leaf.worker.stepUp",
        ),
        (
            {"serviceTier": "priority", "stepUp": {"serviceTier": "priority"}},
            {},
            "orchestration.roles.worker.stepUp",
        ),
        (
            {"featureValues": {"service_tier": "priority"}, "stepUp": {"serviceTier": "priority"}},
            {},
            "orchestration.roles.worker.stepUp",
        ),
        (
            {"serviceTier": "priority", "stepUp": {"featureValues": {"service_tier": "priority"}}},
            {},
            "orchestration.roles.worker.stepUp",
        ),
    ],
)
def test_effective_noop_step_up_refuses_with_authored_file_and_key(
    tmp_path: Path, flat: dict, level: dict, owner: str
) -> None:
    raw = {"roles": {"worker": flat}, "rolesPerLevel": {"leaf": {"worker": level}}}
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True, exist_ok=True)
    distinct = deepcopy(raw)
    alternative = (
        distinct["rolesPerLevel"]["leaf"]["worker"]
        if "rolesPerLevel" in owner
        else distinct["roles"]["worker"]
    )["stepUp"]
    alternative["model"] = "genuinely-different"
    path.write_text(json.dumps({"orchestration": distinct}))
    assert load_agentic_settings(tmp_path).resolved_role_knobs("worker").step_up is not None
    path.write_text(json.dumps({"orchestration": raw}))
    with pytest.raises(AgenticSettingsError) as refusal:
        load_agentic_settings(tmp_path)
    assert str(path) in str(refusal.value)
    assert owner in str(refusal.value)
    assert "different" in str(refusal.value)


def test_per_level_step_up_refusal_preserves_settings_file_and_exact_feature_key(
    launch_world,
) -> None:
    world = launch_world
    path = user_settings_path_for_write()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                **(json.loads(path.read_text()) if path.exists() else {}),
                "orchestration": {
                    "roles": {"worker": {"harness": "codex", "model": "gpt-a"}},
                    "rolesPerLevel": {
                        "leaf": {
                            "worker": {
                                "stepUp": {"model": "gpt-b", "featureValues": {"auto_accept": True}}
                            }
                        }
                    },
                },
            }
        )
    )
    world.replace(
        role_launch_preparation,
        "_role_defaults",
        side_effect=lambda config, context: role_defaults(config, context, {}),
    )
    original = world.request("worker")
    assert world.dispatch(original)[1]["status"] == "running"
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "Try another approach."}
    )
    refusal = world.refused(request)
    assert "orchestration.rolesPerLevel.leaf.worker.stepUp.featureValues.auto_accept" in str(
        refusal.detail
    )
    assert str(path) in str(refusal.detail)
    assert world.agent_of(original)["archivedAt"] is None


@pytest.mark.parametrize("error_type", [AgenticSettingsError, OSError])
def test_post_commit_settings_hint_failure_returns_the_recorded_review(
    launch_world, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    world = launch_world

    def unreadable_settings(_root):
        raise error_type("settings.json became unreadable after publication")

    monkeypatch.setattr(task_review_step_up, "load_agentic_settings", unreadable_settings)
    answer = task_doc_tool(
        world.config,
        TaskDocTarget(repo_id="agents-remember", task_name="master", slug="01_leaf"),
        operation="record_review",
        edit=TaskDocEdit(
            review={"verdict": "block", "findings": [{"findingId": "A", "description": "broken"}]}
        ),
    )
    assert answer["ok"] is True
    assert answer["operation"] == "task_doc.record_review"
    assert "recorded" in answer["warning"]
    assert "settings.json became unreadable" in answer["warning"]
    assert "Do not repeat" in answer["nextAction"]
    document = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref).document
    assert document.reviewState is not None
    assert document.reviewState.round == 1
    assert len(document.reviewState.recordedRounds) == 1
    assert document.reviewState.remainingFindingIds == ["A"]
    assert answer["reviewState"] == document.reviewState.model_dump(mode="json")


def test_receipt_loss_uses_durable_destination_as_predecessor_in_takeover_material(
    launch_world,
) -> None:
    world = launch_world
    defaults = {
        "role": "worker",
        "agent": "codex",
        "model": "gpt-a",
        "stepUp": {"model": "gpt-b"},
    }
    world.replace(
        role_launch_preparation, "_role_defaults", return_value=(defaults, ("codex", "eve"))
    )
    request = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "Try another approach."}
    )
    assert world.dispatch(request)[1]["status"] == "running"
    document = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref).document
    prior = json.loads(
        next(item for item in document.decisions if item.decision == "Step-up worker.").rationale
    )
    assert prior["to"]["model"] == "gpt-b"
    world.leaf = TaskDocumentTopology(world.config.coordination_root).resolve(world.leaf.ref)
    world.agent_of(request).update(archivedAt="gone", status="idle")
    world.receipt_path(request).unlink()
    restart = world.request("worker")
    answer = world.dispatch(restart)[1]
    assert answer["status"] == "running"
    assert answer["stepUp"]["from"]["model"] == "gpt-b"
    receipt = world.receipt(restart)
    material = json.loads(Path(receipt["report"]["path"]).with_suffix(".step-up.json").read_text())
    assert material["fromModel"] == "gpt-b"
    assert "from model gpt-b" in Path(receipt["handoverArtifact"]["path"]).read_text()


def test_same_scope_native_tier_step_up_is_not_hidden_by_inherited_alias(launch_world) -> None:
    world = launch_world
    catalog = deepcopy(CATALOG)
    catalog["providers"][0]["models"][0]["features"] = [
        {"id": "service_tier", "type": "select", "options": [{"id": "priority", "label": "Fast"}]}
    ]
    catalog["providers"][0]["models"][0]["serviceTiers"] = [{"id": "priority", "label": "Fast"}]
    world.replace(world.runtime, "_catalog", return_value=catalog)
    path = user_settings_path_for_write()
    # MIK-R137: the user settings file holds the role choices and nothing creates it for a test
    # that only reads them, so an absent file is "no choices yet", not an error.
    raw = json.loads(path.read_text()) if path.exists() else {}
    raw["orchestration"] = {
        "roles": {
            "worker": {
                "harness": "codex",
                "model": "gpt-a",
                "serviceTier": "fast",
                "stepUp": {"featureValues": {"service_tier": "not-offered"}},
            }
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))
    world.replace(
        role_launch_preparation,
        "_role_defaults",
        side_effect=lambda config, context: role_defaults(config, context, {}),
    )
    original = world.request("worker")
    assert world.dispatch(original)[1]["status"] == "running"
    world.agent_of(original).update(status="idle")
    request = RoleDispatchRequest.model_validate(
        {**world.request("worker").model_dump(by_alias=True), "stepUp": "Try another approach."}
    )
    world.runtime.calls.clear()
    refusal = world.refused(request)
    assert "orchestration.roles.worker.stepUp.featureValues.service_tier" in str(refusal.detail)
    assert str(path) in str(refusal.detail)
    assert world.agent_of(original)["archivedAt"] is None
    assert not any(command == "agent-archive" for command, _ in world.runtime.calls)
