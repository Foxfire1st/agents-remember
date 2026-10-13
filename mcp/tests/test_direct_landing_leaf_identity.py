"""Rule 8 direct-landing leaf identity: leaf_id end to end (moved from gate-routes, F07).

Moved verbatim from ``test_knowledge_gate_routes.py`` so that module stays below the
1200-line hard rail. Shared fixtures and helpers are imported, not copied.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.application.worktree_tool_requests import OperationControlRequest
from agents_remember.application.worktree_tools import worktree_operation_control_tool
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.lifecycles.direct_landing import DirectLandingOperationInput
from agents_remember.worktrees import direct_landing as direct_landing_owner
from agents_remember.worktrees import direct_landing as route
from agents_remember.worktrees import knowledge_gate
from agents_remember.worktrees.integration.direct_landing.direct_landing_operation import (
    direct_landing_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_recovery import (
    recover_direct_landing_under_authority,
)
from agents_remember.worktrees.services import (
    WorktreeServices,
    bind_worktree_services,
    reset_worktree_services,
)
from test_direct_landing import (
    _converted_request,
    _converted_series_fixture,
    direct_landing_tool,
)
from test_knowledge_closeout_gate import (
    CODE_A,
    LEAF,
    TRACES,
    A,
    Gated,
    _answered,
    _series,
    build_gated,
    commit,
    trace_rows,
    write,
)
from test_knowledge_gate_routes import (
    _admitted,
    _answer,
    _configured,
    _land,
    _receipts,
)

HISTORY = f"knowledge/history/{LEAF}.json"


@pytest.fixture
def world(tmp_path: Path) -> Gated:
    return build_gated(tmp_path)


@pytest.fixture
def ports() -> Any:
    providers = mock.Mock()
    providers.setup_status.return_value = {}
    bind_worktree_services(
        WorktreeServices(
            provider_lifecycle=providers,
            memory_quality=cast(Any, None),
            citation_guard=cast(Any, None),
            knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
            knowledge_gate=KnowledgeGate(),
        )
    )
    yield
    reset_worktree_services()


def test_direct_landing_with_leaf_id_judges_and_closes_the_named_leaf(
    world: Gated, ports: None
) -> None:
    """Rule 8: leaf_id given, the landing judges and closes that leaf's history file."""

    _answer(world)
    open_bytes = (world.memory / HISTORY).read_bytes()
    code_commit = commit(world.code, {})
    closing = route._close_gated_leaf(_series(world), code_commit, leaf_id=LEAF)
    assert closing is not None
    assert closing.previous == open_bytes
    closed = json.loads((world.memory / HISTORY).read_text())
    assert closed["closed"] is True and closed["leaf"] == LEAF


def test_direct_landing_with_leaf_id_binds_the_created_closed_file_when_the_leaf_wrote_none(
    world: Gated, ports: None
) -> None:
    """Rule 8: leaf_id given and no open file of its own, the source is created closed, rowless."""

    series = _series(world)
    code_commit = commit(world.code, {})  # a changed code commit: this landing is no replay
    selected = knowledge_gate.direct_gate_source(
        series,
        code_commit=code_commit,
        memory_tree=route._memory_content_tree(series),
        leaf_id=LEAF,
    )
    assert selected.refusal is None and selected.owner == LEAF
    assert selected.source is not None
    assert selected.source.history_path == HISTORY
    created = json.loads(selected.source.history_bytes.decode("utf-8"))
    assert created == {"schema": "ar-history/v1", "leaf": LEAF, "closed": True, "rows": []}


def test_direct_landing_with_leaf_id_refuses_another_leaf_s_open_file(
    world: Gated, ports: None
) -> None:
    """Rule 8: leaf_id given, another leaf's open file refuses, naming both leaves."""

    _answer(world)
    other = "knowledge/history/260928-MIK-L96.json"
    write(
        world.memory,
        {
            other: canonical_text(
                {"schema": "ar-history/v1", "leaf": "260928-MIK-L96", "closed": False, "rows": []}
            )
        },
    )
    code_commit = commit(world.code, {})
    with pytest.raises(route.DirectLandingError) as refused:
        route._close_gated_leaf(_series(world), code_commit, leaf_id=LEAF)
    assert refused.value.status == "direct-landing-knowledge-gate-refused"
    assert LEAF in refused.value.detail and "260928-MIK-L96" in refused.value.detail


def test_direct_landing_with_an_invalid_leaf_id_refuses_by_name(world: Gated, ports: None) -> None:
    """Rule 8: a leaf_id that names no history file refuses, naming the leaf_id."""

    code_commit = commit(world.code, {})  # a changed code commit: this landing is no replay
    with pytest.raises(route.DirectLandingError) as refused:
        route._close_gated_leaf(_series(world), code_commit, leaf_id="not a leaf!!")
    assert refused.value.status == "direct-landing-knowledge-gate-refused"
    assert "names no valid leaf" in refused.value.detail


def test_direct_landing_without_leaf_id_names_leaf_id_in_the_open_file_refusal(
    world: Gated, ports: None
) -> None:
    """Bound leaf judged (MIK-R94 rules 7 and 9): without leaf_id the route lands
    the grant-bound leaf, so no none-or-several refusal names leaf_id; the
    invariant gate over the exact candidate refuses instead, naming the bound
    leaf and the finding count."""

    commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    config, series = _configured(world)
    with pytest.raises(route.DirectLandingError) as refused:
        _land(world, config, series, dry_run=True)
    assert refused.value.status == "direct-landing-knowledge-gate-refused"
    assert LEAF in refused.value.detail
    assert "4 finding(s)" in refused.value.detail


def test_recovery_through_operation_control_settles_the_kept_closing_receipt(
    world: Gated, ports: None
) -> None:
    """Rule 8: recovery settles the kept closing receipt in the same call, as apply does —
    through the real recovery executor, with no mocked stand-in.

    Architect ruling 0f278214 part (b): the test with the real executor that calls
    recover_direct_landing_under_authority and shows the kept closing receipt settled
    in the same call. The recovery runs unmocked: ``recover_direct_landing_under_authority`` executes the
    route's own ``execute_or_require_direct_landing_recovery``. Since MIK-R143 the
    outer control entry (``control_operation``) admits this step too — the turned
    test (a) above proves the admission through the public tool on the same
    fixture state, where the control once offered only ``cancel``.
    """

    _config, series, _open_bytes = _admitted(world)
    store = direct_landing_store(series)
    record = store.read()
    assert record is not None and record.status == "running"
    assert _receipts(series)

    settled = recover_direct_landing_under_authority(series, store, record)
    assert settled.status == "completed"
    assert not _receipts(series)


def _public_supplied_fixture(tmp_path: Path, name: str) -> dict[str, Any]:
    """A converted direct series with answered rows, ready for the public tool."""

    fixture: dict[str, Any] = _converted_series_fixture(tmp_path / name)
    world = fixture["world"]
    code = commit(world.code, {A: CODE_A.replace("return value", "return -value")})
    _answered(world, world.reanchor("RLZ-A00001"), *trace_rows(*TRACES))
    bind_worktree_services(build_default_worktree_services())
    fixture["code_commit"] = code
    return fixture


def test_public_direct_landing_with_the_supplied_open_leaf_lands_and_records_the_leaf(
    tmp_path: Path, ports: None
) -> None:
    """F04 public direct discriminator (supplied): the request's leaf_id travels the public
    tool into the landed receipt and the named history file closes for that leaf."""

    fixture = _public_supplied_fixture(tmp_path, "supplied")
    world = fixture["world"]
    request = replace(_converted_request(fixture, fixture["code_commit"]), leaf_id=LEAF)
    assert request.leaf_id == LEAF
    result = direct_landing_tool(fixture["config"], request)
    assert result["ok"], result
    record = direct_landing_store(fixture["contract"]).read()
    assert record is not None
    assert isinstance(record.input, DirectLandingOperationInput)
    assert record.input.leafId == LEAF
    closed = json.loads((world.memory / HISTORY).read_text(encoding="utf-8"))
    assert closed["closed"] is True and closed["leaf"] == LEAF


def test_public_direct_landing_with_a_wrong_leaf_refuses_naming_the_leaf(
    tmp_path: Path, ports: None
) -> None:
    """F04 public direct discriminator (wrong): a supplied leaf_id that is not the landing's
    leaf refuses through the public tool. Under MIK-R94 rule 7 the route lands its
    grant-bound leaf, so the refusal names the bound leaf and the open other leaf,
    with the leaf_id hint, instead of the supplied string."""

    fixture = _public_supplied_fixture(tmp_path, "wrong")
    request = _converted_request(fixture, fixture["code_commit"])
    other = "260928-MIK-L96"
    write(
        fixture["world"].memory,
        {
            f"knowledge/history/{other}.json": canonical_text(
                {"schema": "ar-history/v1", "leaf": other, "closed": False, "rows": []}
            )
        },
    )
    bind_worktree_services(build_default_worktree_services())
    refused = direct_landing_tool(fixture["config"], replace(request, leaf_id=LEAF))
    assert not refused["ok"], refused
    assert LEAF in str(refused["detail"]) and other in str(refused["detail"])
    bogus = direct_landing_tool(fixture["config"], replace(request, leaf_id="260928-MIK-LXX-none"))
    assert not bogus["ok"], bogus
    assert LEAF in str(bogus["detail"]) and other in str(bogus["detail"])
    assert "leaf_id" in str(bogus["detail"])


def test_control_recover_on_the_interrupted_running_generation_is_admitted(
    tmp_path: Path, ports: None
) -> None:
    """MIK-R143 turns L52's test (a) (architect ruling 0f278214, F04): a direct
    landing IS recovered through the control now — through the PUBLIC
    ``worktree_operation_control`` tool, recover of the intent-less interrupted
    running generation is admitted, completes the same generation, and settles
    the kept closing receipt in the same call (rule 3b, first branch). The
    refusal this test pinned before L143 is superseded by design; the
    interruption vehicle (a first leg cut short of proof) is unchanged, and the
    refusal alters nothing before the recover runs."""

    fixture = _public_supplied_fixture(tmp_path, "control-refused")
    store = direct_landing_store(fixture["contract"])
    request = _converted_request(fixture, fixture["code_commit"])
    with mock.patch.object(
        direct_landing_owner,
        "execute_or_require_direct_landing_recovery",
        side_effect=route.DirectLandingError("fixture-stop", "stopped after admission"),
    ):
        stopped = direct_landing_tool(fixture["config"], request)
    assert stopped["status"] == "fixture-stop", stopped
    record = store.read()
    assert record is not None and record.status == "running"
    assert _receipts(fixture["contract"])
    bind_worktree_services(build_default_worktree_services())
    recovered = worktree_operation_control_tool(
        fixture["config"],
        OperationControlRequest(
            contract_path=fixture["contract"].contract_path.as_posix(),
            operation_kind="direct-landing",
            action="recover",
            expected_generation=record.generation,
            intent_note="pin the admitted recovery",
        ),
    )
    assert recovered["ok"], recovered
    current = store.read()
    assert current is not None
    assert (current.status, current.generation) == ("completed", record.generation)
    assert current.operationKey == record.operationKey
    assert _receipts(fixture["contract"]) == []
