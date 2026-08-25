"""Focused proof for ambient closeout-door caller resolution."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import closeout_door
from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractAccepted,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.ambient_seat import AmbientSeatError
from agents_remember.worktrees.integration.closeout.door_control import DoorActor
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def test_public_door_resolves_actor_then_executes_admitted_contract() -> None:
    request = _value(contract_path="/tmp/contract.json", action="declare", caller=None)
    config = _value(coordination_root=Path("/tmp/coordination"))
    accepted = ConfiguredContractAccepted(
        Path(request.contract_path), cast(Any, _value()), cast(Any, _value())
    )
    actor = DoorActor(
        role="worker",
        task_document_ref=TaskDocumentRef(repository="repo", path="sprint/leaf.json"),
    )
    with (
        mock.patch.object(closeout_door, "admit_configured_contract", return_value=accepted),
        mock.patch.object(closeout_door, "_resolve_door_actor", return_value=actor) as resolve,
        mock.patch.object(
            closeout_door, "_execute_closeout_door", return_value={"state": "declared"}
        ) as execute,
    ):
        assert closeout_door.closeout_door_tool(config, request) == {"state": "declared"}
    resolve.assert_called_once_with(config, request)
    execute.assert_called_once_with(config, request, accepted, actor)


def test_hosted_and_unhosted_actor_resolution_are_disjoint() -> None:
    document = TaskDocumentRef(repository="repo", path="sprint/leaf.json")
    declared = _value(role="worker", task_document_ref=document)
    request = _value(caller=declared)
    config = _value(coordination_root=Path("/tmp/coordination"))
    caller = _value(binding_role="worker", binding_task_document_ref=document)
    with (
        mock.patch.object(closeout_door, "TerminalCatalog"),
        mock.patch.object(closeout_door, "resolve_ambient_seat", return_value=caller),
    ):
        assert closeout_door._resolve_door_actor(config, request) == DoorActor(
            role="worker", task_document_ref=document
        )

    unavailable = AmbientSeatError("ambient-seat-unavailable", "ambient")
    with (
        mock.patch.object(closeout_door, "TerminalCatalog"),
        mock.patch.object(closeout_door, "resolve_ambient_seat", side_effect=unavailable),
    ):
        assert closeout_door._resolve_door_actor(config, request) == DoorActor(
            role="worker", task_document_ref=document
        )
    assert closeout_door._unhosted_door_actor(request, unavailable) == DoorActor(
        role="worker", task_document_ref=document
    )
    with pytest.raises(CloseoutQueueError, match="could not be resolved"):
        closeout_door._unhosted_door_actor(request, AmbientSeatError("ambient-seat-stale", "stale"))

    with mock.patch.object(closeout_door, "_refuse_hosted_declared_conflict") as refuse_conflict:
        assert closeout_door._hosted_door_actor(request, "worker", document) == DoorActor(
            role="worker", task_document_ref=document
        )
    refuse_conflict.assert_called_once_with(request, "worker", document)
