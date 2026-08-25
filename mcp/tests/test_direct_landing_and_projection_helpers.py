"""Focused proof for unreadable direct landing and series-door projections."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.lifecycle import direct_landing
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentRefError
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.queue import closeout_projection as projection


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _ref(path: str) -> TaskDocumentRef:
    return TaskDocumentRef(repository="repo", path=path)


def test_direct_operation_location_returns_exact_location_or_public_decision() -> None:
    location = _value(contract_path=Path("/tmp/contract.json"))
    with mock.patch.object(
        direct_landing,
        "configured_lifecycle_operation_location",
        return_value=(_value(), location),
    ):
        assert direct_landing._direct_operation_location(
            cast(Any, _value()), Path("/tmp/contract.json")
        ) == (location, None)

    error = LifecycleOperationLocationError(
        "operation-location-invalid",
        "locator mismatch",
        expected={"route": "locator"},
        observed={"state": "mismatch"},
    )
    with mock.patch.object(
        direct_landing,
        "configured_lifecycle_operation_location",
        side_effect=error,
    ):
        resolved, decision = direct_landing._direct_operation_location(
            cast(Any, _value()), Path("/tmp/contract.json")
        )
    assert resolved is None
    assert decision == {
        "expected": {"route": "locator"},
        "observed": {"state": "mismatch"},
        "nextAction": "developer-decision",
        "developerDecisionRequired": True,
        "decisionSurface": "locator mismatch",
    }


def test_unreadable_direct_projection_uses_only_direct_landing_dict_results() -> None:
    location = _value()
    contract_path = Path("/tmp/contract.json")
    error = OSError("unreadable")
    direct = _value(kind="direct-landing", result={"state": "recover"})
    closeout = _value(kind="closeout", result={"state": "other"})
    with mock.patch.object(
        direct_landing,
        "unreadable_contract_operation_projections",
        return_value=(closeout, direct),
    ):
        assert (
            direct_landing._first_unreadable_direct_projection(location, contract_path, error)
            is direct
        )
        assert direct_landing._unreadable_direct_projection(location, contract_path, error) == {
            "state": "recover"
        }

    with mock.patch.object(
        direct_landing,
        "_first_unreadable_direct_projection",
        return_value=None,
    ):
        assert direct_landing._unreadable_direct_projection(location, contract_path, error) is None
    with mock.patch.object(
        direct_landing,
        "_first_unreadable_direct_projection",
        return_value=_value(result="not-a-dict"),
    ):
        assert direct_landing._unreadable_direct_projection(location, contract_path, error) is None


def test_unreadable_direct_decision_prefers_location_then_projection_then_missing() -> None:
    config = cast(Any, _value())
    contract_path = Path("/tmp/contract.json")
    error = OSError("unreadable")
    location = _value(contract_path=contract_path)
    located = {"nextAction": "developer-decision", "decisionSurface": "locator"}
    with mock.patch.object(
        direct_landing, "_direct_operation_location", return_value=(None, located)
    ):
        assert direct_landing._unreadable_direct_decision(config, contract_path, error) is located

    projected = {"state": "recover"}
    with (
        mock.patch.object(
            direct_landing, "_direct_operation_location", return_value=(location, None)
        ),
        mock.patch.object(direct_landing, "_unreadable_direct_projection", return_value=projected),
    ):
        assert direct_landing._unreadable_direct_decision(config, contract_path, error) is projected

    with (
        mock.patch.object(
            direct_landing, "_direct_operation_location", return_value=(location, None)
        ),
        mock.patch.object(direct_landing, "_unreadable_direct_projection", return_value=None),
    ):
        missing = direct_landing._unreadable_direct_decision(config, contract_path, error)
    assert missing["developerDecisionRequired"] is True
    assert missing["expected"] == {
        "contractPath": contract_path.as_posix(),
        "operationKind": "direct-landing",
    }


def _series_context() -> tuple[Any, Any, Any, Any]:
    sprint = _value(ref=_ref("sprint/task.json"))
    master = _value(ref=_ref("master/task.json"))
    candidate = _value(ref=_ref("master/leaf.json"))
    topology = _value(
        resolve=lambda _ref, _overrides: candidate,
        parent=lambda _ref: master.ref,
    )
    tasks = _value(topology=topology, sprint=sprint)
    door = _value(
        taskDocumentRef=candidate.ref,
        owningMasterTaskDocumentRef=master.ref,
        sprintTaskDocumentRef=sprint.ref,
        contractPath="/tmp/series.json",
    )
    source = projection._DoorSource(Path("/tmp/series.json"), None, door)
    return tasks, source, master, candidate


def test_series_projection_loop_keeps_only_resolved_doors() -> None:
    tasks, source, master, candidate = _series_context()
    resolved = (source, candidate, master, 1)
    with mock.patch.object(projection, "_series_projection_door", side_effect=(None, resolved)):
        assert projection._series_projection_doors(
            tasks,
            [(source, master, 0), (source, master, 1)],
            [],
            [],
            None,
        ) == [resolved]


def test_series_projection_door_reports_identity_and_candidate_ref_errors() -> None:
    tasks, source, master, candidate = _series_context()
    context = projection._SeriesProjectionContext(tasks, [], [], None)
    with (
        mock.patch.object(projection, "_door_fact", return_value={"door": "fact"}),
        mock.patch.object(projection, "_series_door_identity_matches", return_value=False),
    ):
        assert projection._series_projection_door(context, source, master, 1) is None
    assert context.door_rows == [{"door": "fact"}]
    assert context.problems[-1].errorType == "door-canonical-identity-mismatch"

    context = projection._SeriesProjectionContext(tasks, [], [], None)
    with (
        mock.patch.object(projection, "_door_fact", return_value={}),
        mock.patch.object(projection, "_series_door_identity_matches", return_value=True),
        mock.patch.object(
            projection,
            "_resolved_series_candidate",
            side_effect=TaskDocumentRefError("candidate-invalid", "bad candidate"),
        ),
    ):
        assert projection._series_projection_door(context, source, master, 2) is None
    assert context.problems[-1].errorType == "candidate-invalid"

    context = projection._SeriesProjectionContext(tasks, [], [], None)
    with (
        mock.patch.object(projection, "_door_fact", return_value={}),
        mock.patch.object(projection, "_series_door_identity_matches", return_value=True),
        mock.patch.object(projection, "_resolved_series_candidate", return_value=candidate),
    ):
        assert projection._series_projection_door(context, source, master, 3) == (
            source,
            candidate,
            master,
            3,
        )


def test_series_candidate_and_door_identity_bind_exact_parent_and_address() -> None:
    tasks, source, master, candidate = _series_context()
    door = source.door
    assert door is not None
    assert projection._series_door_identity_matches(source, master, tasks.sprint)
    assert projection._resolved_series_candidate(tasks, door, master, None) is candidate

    tasks.topology.parent = lambda _candidate_ref: _ref("other/task.json")
    with pytest.raises(TaskDocumentRefError, match="canonical master"):
        projection._resolved_series_candidate(tasks, door, master, None)
    door.contractPath = "/tmp/other.json"
    assert not projection._series_door_identity_matches(source, master, tasks.sprint)
