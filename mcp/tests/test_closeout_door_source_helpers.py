"""Focused proof for door provenance successor replay semantics."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.worktrees.integration.closeout import door_source
from agents_remember.worktrees.queue.closeout_queue_errors import CloseoutQueueError


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _current(**overrides: object) -> Any:
    fields: dict[str, object] = {
        "generationId": "current",
        "predecessorGenerationId": "previous",
        "disposition": "waiting",
    }
    fields.update(overrides)
    return _value(**fields)


def _request(**overrides: object) -> Any:
    fields: dict[str, object] = {
        "expected_generation_id": "current",
        "action": "update-provenance",
    }
    fields.update(overrides)
    return _value(**fields)


def test_provenance_successor_updates_current_or_replays_predecessor() -> None:
    current = _current()
    context = _value(contract=_value(closeout_door=current))
    request = _request()
    successor = _current(generationId="successor", predecessorGenerationId="current")
    with mock.patch.object(door_source, "_declare_generation", return_value=successor) as declare:
        assert door_source._provenance_successor(context, request, cast(Any, _value())) is successor
    declare.assert_called_once()

    request.expected_generation_id = "previous"
    with mock.patch.object(
        door_source, "_replayed_provenance_successor", return_value=current
    ) as replay:
        assert door_source._provenance_successor(context, request, cast(Any, _value())) is current
    replay.assert_called_once_with(context, request, mock.ANY, current)


def test_required_provenance_generation_and_disposition_fail_closed() -> None:
    current = _current()
    assert (
        door_source._required_provenance_generation(_value(contract=_value(closeout_door=current)))
        is current
    )
    with pytest.raises(CloseoutQueueError, match="no current door generation"):
        door_source._required_provenance_generation(_value(contract=_value(closeout_door=None)))

    door_source._require_provenance_source_disposition(current)
    door_source._require_provenance_source_disposition(_current(disposition="deferred"))
    with pytest.raises(CloseoutQueueError, match="waiting or deferred"):
        door_source._require_provenance_source_disposition(_current(disposition="claimed"))


def test_replayed_provenance_successor_converges_only_on_exact_generation_chain() -> None:
    actor = cast(Any, _value())
    current = _current(generationId="current", predecessorGenerationId="previous")
    context = cast(Any, _value())
    request = _request(expected_generation_id="previous")
    replay = _current(generationId="current", predecessorGenerationId="previous")
    with mock.patch.object(door_source, "_declare_generation", return_value=replay):
        assert (
            door_source._replayed_provenance_successor(context, request, actor, current) is current
        )

    request.expected_generation_id = "current"
    current.predecessorGenerationId = "current"
    mismatch = _current(generationId="different", predecessorGenerationId="current")
    with (
        mock.patch.object(door_source, "_declare_generation", return_value=mismatch),
        mock.patch.object(door_source, "_require_expected"),
        pytest.raises(AssertionError, match="must raise"),
    ):
        door_source._replayed_provenance_successor(context, request, actor, current)

    request.expected_generation_id = "other"
    current.predecessorGenerationId = "previous"
    with (
        mock.patch.object(door_source, "_declare_generation", return_value=mismatch),
        pytest.raises(CloseoutQueueError),
    ):
        door_source._replayed_provenance_successor(context, request, actor, current)
