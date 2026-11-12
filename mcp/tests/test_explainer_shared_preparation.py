import json
from typing import cast

import pytest
import test_explainer_planned_binding as planned_fixture
from agents_remember.application import review_comparison_preparation as shared_module
from agents_remember.application.review_answer_preparation import RecordedReviewAnswers
from agents_remember.application.review_comparison_preparation import (
    PreparedComparison,
    SharedComparisonPreparation,
)
from agents_remember.application.review_evidence_adapter import prepare_evidence
from agents_remember.application.review_planned_comparison import explainer_application_ports
from agents_remember.canvas.explainer_service import ExplainerHost, ExplainerRefused, LeafScope
from agents_remember.models.knowledge.review_evidence import EvidenceMap
from agents_remember.serving.explainer import (
    ExplainerWiring,
    _prepared_trees,
    explainer_composition,
)
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    LEAF,
    MASTER,
    REPO,
    build_world,
)


def test_production_comparison_uses_shared_prepare_and_rechecks_without_full_view(tmp_path):
    world = build_world(tmp_path)
    world.edit()
    (world.task_root / "leaf.json").write_text('{"id":"' + LEAF + '","status":"inProgress"}')
    shared = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )

    def forbidden_full_view(_query):
        raise AssertionError("explainer must not read a full projected page")

    composition = explainer_composition(
        world.config,
        forbidden_full_view,
        wiring=ExplainerWiring(
            host=cast(ExplainerHost, object()),
            application=explainer_application_ports(),
            comparison_prepare=shared.prepare,
            task_root_for=lambda _scope: world.task_root,
        ),
    )
    deps = composition.deps
    assert deps is not None
    scope = LeafScope(REPO, MASTER, LEAF)
    facts = deps.facts(scope)
    bound = deps.comparison(scope, facts)
    assert bound.witness is not None
    assert deps.recheck(scope, facts, bound)
    (world.code_worktree / CODE_FILE).write_text("def changed():\n    return 99\n")
    assert not deps.recheck(scope, facts, bound)


def test_composition_preserves_native_indexes_and_packet_groups(tmp_path, monkeypatch):

    world = build_world(tmp_path)
    world.edit()
    (world.task_root / "leaf.json").write_text('{"id":"' + LEAF + '","status":"inProgress"}')
    shared = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    count = []
    original = shared_module.resolve_tree_comparison

    def resolve(*args):
        count.append(1)
        return original(*args)

    monkeypatch.setattr(shared_module, "resolve_tree_comparison", resolve)
    prepared = shared.prepare(ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF))
    assert isinstance(prepared, PreparedComparison)
    native = prepared.native[1]
    assert native.after.database is not None
    composition = explainer_composition(
        world.config,
        lambda _query: (_ for _ in ()).throw(AssertionError("full page")),
        wiring=ExplainerWiring(
            host=cast(ExplainerHost, object()),
            application=explainer_application_ports(),
            comparison_prepare=shared.prepare,
            task_root_for=lambda _scope: world.task_root,
        ),
    )
    scope = LeafScope(REPO, MASTER, LEAF)
    deps = composition.deps
    assert deps is not None
    bound = deps.comparison(scope, deps.facts(scope))
    carried = _prepared_trees(bound)
    assert carried is native
    assert carried.live == native.live
    assert carried.memory_worktree == native.memory_worktree
    direct = prepare_evidence(
        native,
        scope="the change set",
        question="explain",
        evidence_map=EvidenceMap(coverage="change set"),
    ).packet
    through = prepare_evidence(
        carried,
        scope="the change set",
        question="explain",
        evidence_map=EvidenceMap(coverage="change set"),
    ).packet
    assert direct == through
    assert direct.map is not None
    assert direct.map.groups
    assert sum(group.count for group in direct.map.groups) > 0
    assert len(count) == 1


def test_final_recheck_rejects_new_leaf_status_and_archive(tmp_path):
    world = build_world(tmp_path)
    world.edit()
    leaf = world.task_root / "leaf.json"
    leaf.write_text(json.dumps({"id": LEAF, "status": "inProgress"}))
    shared = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    composition = explainer_composition(
        world.config,
        lambda _query: (_ for _ in ()).throw(AssertionError("full page")),
        wiring=ExplainerWiring(
            host=cast(ExplainerHost, object()),
            application=explainer_application_ports(),
            comparison_prepare=shared.prepare,
            task_root_for=lambda _scope: world.task_root,
        ),
    )
    deps = composition.deps
    assert deps is not None
    scope = LeafScope(REPO, MASTER, LEAF)
    facts = deps.facts(scope)
    bound = deps.comparison(scope, facts)
    assert deps.recheck(scope, facts, bound)
    leaf.write_text(json.dumps({"id": LEAF, "status": "planned"}))
    assert not deps.recheck(scope, facts, bound)
    leaf.write_text(json.dumps({"id": LEAF, "status": "inProgress"}))
    retired = world.task_root / "notes/reports/master-retirement.json"
    retired.parent.mkdir(parents=True, exist_ok=True)
    retired.write_text("{}")
    assert not deps.recheck(scope, facts, bound)


@pytest.mark.parametrize("missing_side", ["code", "memory"])
def test_planned_missing_repository_keeps_named_refusal(tmp_path, missing_side):
    runtime, root, code, memory, contract = planned_fixture._planning_world(tmp_path)

    def forbidden(_query):
        raise AssertionError("planning never reads a recorded comparison")

    composition = explainer_composition(
        runtime,
        forbidden,
        wiring=ExplainerWiring(
            host=cast(ExplainerHost, object()),
            application=explainer_application_ports(),
            comparison_prepare=forbidden,
            contract_for=lambda _scope: contract,
            task_root_for=lambda _scope: root,
        ),
    )
    deps = composition.deps
    assert deps is not None
    scope = LeafScope(planned_fixture.REPO, planned_fixture.TASK, planned_fixture.LEAF)
    facts = deps.facts(scope)
    bound = deps.comparison(scope, facts)
    repository = code if missing_side == "code" else memory
    repository.rename(repository.with_name(repository.name + "-removed"))
    with pytest.raises(ExplainerRefused) as refused:
        deps.prepare(scope, facts, bound)
    assert refused.value.reason == "evidence-source-unavailable"
    assert missing_side in refused.value.detail
