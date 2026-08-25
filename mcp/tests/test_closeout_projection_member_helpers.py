"""Focused proof for candidate-local closeout readiness helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.worktrees.queue import closeout_projection_members as members


def _value(**fields: object) -> Any:
    return cast(Any, SimpleNamespace(**fields))


def _ref(path: str) -> TaskDocumentRef:
    return TaskDocumentRef(repository="agents-remember", path=path)


def test_admission_and_sequential_waiting_reasons_are_explicit() -> None:
    door = _value(
        admissionProvenance=_value(
            resourceReady=False,
            resourceReason="worker unavailable",
            admissionReady=False,
            admissionReason="predecessor active",
        )
    )
    assert members._admission_waiting_reasons(door) == [
        "resource-unavailable: worker unavailable",
        "admission-blocked: predecessor active",
    ]

    master_ref = _ref("sprint/master.json")
    first_ref = _ref("sprint/first.json")
    context = _value(
        sequential_owner=None,
        first_master=None,
        master=_value(ref=master_ref),
    )
    assert members._sequential_waiting_and_order(context, 7) == ([], 7)

    context.first_master = first_ref
    assert members._sequential_waiting_and_order(context, 7) == (
        [f"atomic-series-lane-owned-by: {first_ref.key}"],
        7,
    )

    context.sequential_owner = master_ref
    assert members._sequential_waiting_and_order(context, 7) == ([], 7)


def test_dependency_order_falls_back_or_uses_the_exact_graph_node() -> None:
    context = _value(
        graph=None,
        master=_value(ref=_ref("sprint/master.json")),
        candidate=_value(ref=_ref("sprint/leaf.json")),
        sequential_owner=None,
        first_master=None,
    )
    assert members._dependency_waiting_and_order(context, 12) == ([], 12)

    graph = _value(node_order={"candidate": 4})
    context.graph = graph
    with (
        mock.patch.object(
            members, "predecessor_waiting_reasons", return_value=["predecessor-active"]
        ),
        mock.patch.object(members, "candidate_node", side_effect=(None, "candidate")),
    ):
        assert members._dependency_waiting_and_order(context, 12) == (
            ["predecessor-active"],
            12,
        )
        assert members._dependency_waiting_and_order(context, 12) == (
            ["predecessor-active"],
            4012,
        )
