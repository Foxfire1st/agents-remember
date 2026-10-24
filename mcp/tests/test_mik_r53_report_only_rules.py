"""MIK-R53 Part C: the report-only rules are decided (rules 10-12).

Rule 10 gives the registry two named reasons to report -- ``merge_reports`` and
``migration_debt`` -- and no rule is registered as plainly report-only any more.
Rule 11 decides each of the ten rules. Rule 12 requires reported findings to name
their reason and the route that will refuse, debt findings to give the number of
routes not yet migrated, and bans every "reported, not refused" message.

Each test names the packet rule it proves. The trees are the MIK-R21 Doc14
fixtures (``knowledge_validator_test_support``); a test changes one thing and
checks the finding at a leaf route (one base), at a landing (a leaf
publication) and at a sync merge (two bases).
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from unittest import mock

import pytest
from agents_remember.cli.knowledge_validate import add_arguments
from agents_remember.memory_quality.knowledge_validator import (
    ValidationReport,
    ValidationRule,
    register_rule,
    registered_rules,
    rules_admission,
    validate_tree,
    writer_reported_rule_ids,
)
from agents_remember.memory_quality.knowledge_validator import registry as registry_module
from agents_remember.memory_quality.knowledge_validator.registry import migration_state
from agents_remember.memory_quality.knowledge_validator.validator import LeafCommit
from agents_remember.models.knowledge_files.census import route_slug
from knowledge_validator_test_support import (
    CODE_PATHS,
    DIRECT_LANDING_SIDECAR,
    FAMILY,
    INTEGRATE_CARD,
    LEGACY_COUNT,
    code,
    edit_json,
    encode,
    fixture_tree_files,
    invariant_document,
    invariant_path,
    tree,
)
from mik_r53_rule_surfaces import quality_control_workflow_text

DECISION = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
PROOF_SIDECAR = "onboarding/mcp/tests/test_direct_landing.py.json"
SOURCE_SIDECAR = "onboarding/mcp/src/agents_remember/models/knowledge/source.py.json"
AGENTS = "mcp/src/agents_remember"
WORKTREES = f"{AGENTS}/worktrees"
APPLICATION = f"{AGENTS}/application"
MODELS = f"{AGENTS}/models"

MERGE_RULES = {
    "R27.2-existing-record",
    "R04.1-carried-route-absent",
    "R04.4-unrealized-family",
    "R22.6-carried-stale",
    "R09-history-rows-merged",
}
DEBT_RULES = {
    "R22.3-sidecar-without-markdown",
    "R22.3-unresolved-target",
    "R04.4-route-unassigned",
    "R27.4-legacy-unassessed",
}
REFUSING_WRITER_RULES = {
    "R13.3-governs",
    "R27.2-existing-record",
    "R04.1-carried-route-absent",
    "R04.4-unrealized-family",
    "R22.6-carried-stale",
}
BANNED = (
    "reported, not refused",
    "reported-not-refused",
    "reported, never refused",
    "never refused",
    "never fail",
)


def _validate(files: dict[str, bytes], **kwargs: Any) -> ValidationReport:
    kwargs.setdefault("code", code())
    return validate_tree(tree(files), **kwargs)


def _rules(report: ValidationReport, *, refusing: bool = True) -> set[str]:
    selected = report.refusals if refusing else report.reports
    return {violation.rule for violation in selected}


def _messages(report: ValidationReport, rule: str) -> list[str]:
    return [violation.message for violation in report.violations if violation.rule == rule]


def _census_files(statuses: dict[str, str], census_id: str = "CEN1") -> dict[str, bytes]:
    """A migration census over two onboarding routes with the given statuses."""

    routes = sorted(statuses)
    root = f"knowledge/census/{census_id}"
    sources = sorted(
        ({"path": f"{route}/a.py", "route": route} for route in routes),
        key=lambda row: row["path"],
    )
    artifacts = sorted(
        ({"path": f"onboarding/{route}/a.py.md", "route": route} for route in routes),
        key=lambda row: row["path"],
    )
    files = {
        f"{root}/baseline.json": encode(
            {
                "schema": "ar-census-baseline/v1",
                "census": census_id,
                "code": {"commit": "0" * 40},
                "memory": {"commit": "1" * 40},
                "scope": [],
            }
        ),
        f"{root}/inventory.json": encode(
            {
                "schema": "ar-census-inventory/v1",
                "census": census_id,
                "sources": sources,
                "artifacts": artifacts,
            }
        ),
    }
    for route in routes:
        files[f"{root}/routes/{route_slug(route)}.json"] = encode(
            {
                "schema": "ar-census-route/v1",
                "census": census_id,
                "route": route,
                "statuses": [
                    {
                        "status": statuses[route],
                        "reason": "the migration run assessed this route",
                        "tree": "0" * 40,
                        "provenance": {
                            "leaf": "260928-MIK-L53",
                            "session": "test-session",
                            "at": "2026-10-04T00:00:00+00:00",
                        },
                    }
                ],
            }
        )
    return files


# --------------------------------------------------------------------------------------------------
# Rule 10: two named reasons to report; no plain report-only registration
# --------------------------------------------------------------------------------------------------


def test_a_plain_report_only_rule_is_refused_registration() -> None:
    rule = ValidationRule("TEST.plain", "test", "reports", lambda context: [], report_only=True)
    try:
        with pytest.raises(ValueError, match="report-only"):
            register_rule(rule)
    finally:
        registry_module._REGISTRY.pop("TEST.plain", None)
    assert "TEST.plain" not in {rule.id for rule in registered_rules()}


def test_a_rule_with_both_report_reasons_is_refused_registration() -> None:
    rule = ValidationRule(
        "TEST.both",
        "test",
        "reports",
        lambda context: [],
        merge_reports=True,
        migration_debt=True,
    )
    try:
        with pytest.raises(
            ValueError, match=r"merge_reports.*migration_debt|migration_debt.*merge_reports"
        ):
            register_rule(rule)
    finally:
        registry_module._REGISTRY.pop("TEST.both", None)
    assert "TEST.both" not in {rule.id for rule in registered_rules()}


def test_no_registered_rule_is_plainly_report_only() -> None:
    assert [rule.id for rule in registered_rules() if rule.report_only] == []


def test_each_of_the_ten_rules_carries_its_mik_r53_decision() -> None:
    registered = {rule.id: rule for rule in registered_rules()}
    assert {
        rule_id for rule_id, rule in registered.items() if rule.merge_reports
    } == MERGE_RULES | {"R22.6-link-target-path"}
    assert {rule_id for rule_id, rule in registered.items() if rule.migration_debt} == DEBT_RULES
    assert not registered["R13.3-governs"].merge_reports
    assert not registered["R13.3-governs"].migration_debt


def test_the_five_refusing_rules_report_inside_the_writer() -> None:
    assert writer_reported_rule_ids() >= REFUSING_WRITER_RULES


# --------------------------------------------------------------------------------------------------
# Rule 11: merge_reports -- refuses at commit and landing, reports only at a sync merge
# --------------------------------------------------------------------------------------------------


def test_governs_refuses_a_decision_that_governs_nothing() -> None:
    base = fixture_tree_files()
    files = edit_json(base, DECISION, lambda doc: doc.update(links=[]))

    report = _validate(files, bases=[tree(base, "K_B")])

    assert not report.ok, report.render()
    assert _rules(report) == {"R13.3-governs"}, report.render()


def test_an_existing_record_whose_criterion_no_longer_holds_refuses_off_merge() -> None:
    base = fixture_tree_files()
    candidate = edit_json(
        base,
        PROOF_SIDECAR,
        lambda document: document.update(proves=[]),
    )
    base_tree = tree(base, "K_B")

    leaf = _validate(candidate, bases=[base_tree])
    assert not leaf.ok, leaf.render()
    assert "R27.2-existing-record" in _rules(leaf), leaf.render()

    landing = _validate(candidate, bases=[base_tree], leaf_publication=True)
    assert not landing.ok, landing.render()
    assert "R27.2-existing-record" in _rules(landing), landing.render()


def test_an_existing_record_whose_criterion_no_longer_holds_reports_at_a_merge() -> None:
    base = fixture_tree_files()
    candidate = edit_json(base, PROOF_SIDECAR, lambda document: document.update(proves=[]))

    report = _validate(candidate, bases=[tree(base, "left"), tree(base, "right")])

    assert report.ok, report.render()
    assert "R27.2-existing-record" in _rules(report, refusing=False), report.render()
    (message,) = _messages(report, "R27.2-existing-record")
    assert "merge" in message and "refuse" in message


def test_a_carried_route_absent_refuses_off_merge_and_reports_at_a_merge() -> None:
    files = fixture_tree_files()
    without_application = frozenset(p for p in CODE_PATHS if not p.startswith(APPLICATION))
    short_code = code(without_application)

    leaf = _validate(files, bases=[tree(files, "K_B")], code=short_code)
    assert not leaf.ok, leaf.render()
    assert "R04.1-carried-route-absent" in _rules(leaf), leaf.render()

    landing = _validate(files, bases=[tree(files, "K_B")], code=short_code, leaf_publication=True)
    assert not landing.ok, landing.render()
    assert "R04.1-carried-route-absent" in _rules(landing), landing.render()

    merged = _validate(files, bases=[tree(files, "left"), tree(files, "right")], code=short_code)
    assert merged.ok, merged.render()
    assert "R04.1-carried-route-absent" in _rules(merged, refusing=False), merged.render()
    (message,) = _messages(merged, "R04.1-carried-route-absent")
    assert "merge" in message and "refuse" in message


def _new_family(files: dict[str, bytes]) -> dict[str, bytes]:
    document = json.loads(files[FAMILY])
    document.update(
        id="FAM-NEWR53",
        title="a family with no realization yet",
        members=["INV-R8M2TD"],
        routes=[WORKTREES],
        admission={
            "criteria": ["joint_guarantee"],
            "justification": "One guarantee spans the members.",
        },
        origin={"task": "260928-MIK", "leaf": "260928-MIK-L53"},
    )
    return {**files, "knowledge/families/FAM-NEWR53-no-realization.json": encode(document)}


def test_an_unrealized_family_refuses_off_merge_and_reports_at_a_merge() -> None:
    base = fixture_tree_files()
    candidate = _new_family(base)

    leaf = _validate(candidate, bases=[tree(base, "K_B")])
    assert not leaf.ok, leaf.render()
    assert "R04.4-unrealized-family" in _rules(leaf), leaf.render()

    merged = _validate(candidate, bases=[tree(base, "left"), tree(base, "right")])
    assert merged.ok, merged.render()
    assert "R04.4-unrealized-family" in _rules(merged, refusing=False), merged.render()
    (message,) = _messages(merged, "R04.4-unrealized-family")
    assert "merge" in message and "refuse" in message


def test_a_carried_stale_anchor_refuses_off_merge_and_reports_at_a_merge() -> None:
    files = fixture_tree_files()

    leaf = _validate(files, bases=[tree(files, "K_B")], code=code(frozenset()))
    assert not leaf.ok, leaf.render()
    assert "R22.6-carried-stale" in _rules(leaf), leaf.render()

    merged = _validate(
        files, bases=[tree(files, "left"), tree(files, "right")], code=code(frozenset())
    )
    assert merged.ok, merged.render()
    assert "R22.6-carried-stale" in _rules(merged, refusing=False), merged.render()
    stale = [v.message for v in merged.violations if v.rule == "R22.6-carried-stale"]
    assert stale and all("merge" in message and "refuse" in message for message in stale)


def _moved_row_tree() -> tuple[dict[str, bytes], dict[str, bytes]]:
    """A candidate whose row disagrees only because a merge parent moved the entry."""

    base = fixture_tree_files()
    sidecar: dict[str, Any] = dict(json.loads(base[SOURCE_SIDECAR]))
    entry = next(one for one in sidecar["realizes"] if one["id"] == "RLZ-S0VRCE")
    original = dict(entry["anchor"])
    moved = dict(original, blob="f" * 40)
    path = "mcp/src/agents_remember/models/knowledge/source.py"
    history = {
        "schema": "ar-history/v1",
        "leaf": "260928-MIK-L53",
        "closed": False,
        "rows": [
            {
                "id": "ROW-3F9Q7K",
                "subject": "INV-7K3F9Q",
                "disposition": "no_impact",
                "reason": "the entry was re-anchored elsewhere",
                "items": ["sha256:" + "e" * 64],
                "covers": [
                    {
                        "id": "RLZ-S0VRCE",
                        "before": {**moved, "path": path},
                        "after": {**original, "path": path},
                    }
                ],
                "revision": 1,
            }
        ],
    }
    history_path = "knowledge/history/260928-MIK-L53.json"
    agreeing = {**base, history_path: encode(history)}
    candidate_sidecar = {
        **sidecar,
        "realizes": [
            {**entry, "anchor": moved} if one["id"] == "RLZ-S0VRCE" else one
            for one in sidecar["realizes"]
        ],
    }
    candidate = {**agreeing, SOURCE_SIDECAR: encode(candidate_sidecar)}
    return candidate, agreeing


def test_a_conversion_authors_merge_and_debt_states() -> None:
    """A standalone conversion reports what its later routes judge (MIK-R53 rule 10)."""

    candidate = _new_family(fixture_tree_files())

    report = _validate(candidate, code=None, conversion=True)

    assert report.ok, report.render()
    assert "R04.4-unrealized-family" in _rules(report, refusing=False), report.render()
    assert LEGACY_COUNT in _rules(report, refusing=False), report.render()


def test_a_conversion_sidecar_without_markdown_names_conversion_and_next_refusal() -> None:
    """F11: a conversion's sidecar-without-Markdown report carries the debt reason (rule 11)."""

    candidate = {
        path: data for path, data in fixture_tree_files().items() if path != INTEGRATE_CARD
    }
    report = _validate(candidate, code=None, conversion=True)

    assert report.ok, report.render()
    (message,) = _messages(report, "R22.3-sidecar-without-markdown")
    assert "conversion" in message and "refused at every route" in message


# F16 (round 1): conversion admits no base, code or publication. ---


def test_conversion_with_a_base_is_refused_at_entry() -> None:
    files = fixture_tree_files()
    with pytest.raises(ValueError, match="standalone conversion"):
        _validate(files, bases=[tree(dict(files), "K_B")], code=None, conversion=True)


def test_conversion_with_code_is_refused_at_entry() -> None:
    with pytest.raises(ValueError, match="standalone conversion"):
        _validate(fixture_tree_files(), conversion=True)


def test_conversion_with_publication_is_refused_at_entry() -> None:
    files = fixture_tree_files()
    with pytest.raises(ValueError, match="standalone conversion"):
        _validate(files, code=None, conversion=True, leaf_publication=True)
    with pytest.raises(ValueError, match="standalone conversion"):
        _validate(
            files,
            bases=[tree(dict(files), "own"), tree(dict(files), "incoming")],
            code=None,
            conversion=True,
            leaf_publication=LeafCommit(),
        )


def test_standalone_conversion_still_reports() -> None:
    report = _validate(fixture_tree_files(), code=None, conversion=True)
    assert report.ok, report.render()
    assert LEGACY_COUNT in _rules(report, refusing=False), report.render()


# F17 (round 1): the migration population includes candidate-tree routes. ---


def test_candidate_overview_route_joins_the_migration_population() -> None:
    candidate = tree(
        {
            **_census_files({"a": "migrated", "b": "excluded"}),
            "onboarding/new/overview.md": b"# new\n",
        }
    )
    state = migration_state(candidate)
    assert state.routes == ("a", "b", "new")
    assert state.unmigrated == ("new",)
    assert not state.complete


def test_no_census_no_routes_stays_incomplete() -> None:
    state = migration_state(tree({}))
    assert state.routes == () and state.unmigrated == ()
    assert not state.complete


def test_latest_status_across_censuses_governs() -> None:
    candidate = tree(
        {
            **_census_files({"a": "migrated"}, census_id="CEN1"),
            **_census_files({"a": "pending"}, census_id="CEN2"),
        }
    )
    state = migration_state(candidate)
    assert state.routes == ("a",)
    assert state.unmigrated == ("a",)
    assert not state.complete


def test_carried_debt_reports_until_candidate_routes_migrate() -> None:
    base = dict(fixture_tree_files())
    base.update(_census_files({"a": "migrated", "b": "excluded"}))
    candidate = dict(base)
    candidate["onboarding/new/overview.md"] = b"# new\n"
    report = _validate(candidate, bases=[tree(base, "K_B")])
    assert LEGACY_COUNT in _rules(report, refusing=False), report.render()
    assert LEGACY_COUNT not in _rules(report), report.render()
    (count,) = [one for one in report.reports if one.rule == LEGACY_COUNT]
    assert "2 of 4 onboarding route(s) not yet migrated" in count.message


# F20 (round 1): per-record classification is constant-time, not a rescan. ---


def _unassessed_files(identifiers: list[str]) -> dict[str, bytes]:
    return {
        invariant_path(identifier, "unassessed"): encode(
            invariant_document(identifier, admission="legacy-unassessed")
        )
        for identifier in identifiers
    }


def _validate_counted(files: dict[str, bytes], **kwargs: Any) -> tuple[ValidationReport, int]:
    """Validate, counting ``_Admitted`` equality comparisons (F20 operation evidence)."""

    calls = 0
    original = rules_admission._Admitted.__eq__

    def counting(self: Any, other: Any) -> bool:
        nonlocal calls
        calls += 1
        return original(self, other)

    with mock.patch.object(rules_admission._Admitted, "__eq__", counting):
        return _validate(files, **kwargs), calls


def test_all_new_unassessed_classifies_each_record_in_constant_time() -> None:
    identifiers = [f"INV-NEW00{index}" for index in range(6)]
    report, calls = _validate_counted(_unassessed_files(identifiers))
    assert not report.ok, report.render()
    assert calls <= 2 * len(identifiers), calls
    refused = [one.message for one in report.refusals if one.rule == LEGACY_COUNT]
    assert len(refused) == len(identifiers)
    for identifier, message in zip(identifiers, refused, strict=True):
        assert identifier in message


def test_mixed_complete_migration_classifies_each_record_in_constant_time() -> None:
    carried = [f"INV-CAR00{index}" for index in range(4)]
    fresh = [f"INV-NEW01{index}" for index in range(4)]
    census = _census_files({"a": "migrated"})
    base = {**_unassessed_files(carried), **census}
    candidate = {**base, **_unassessed_files(fresh)}
    report, calls = _validate_counted(candidate, bases=[tree(base, "K_B")])
    assert not report.ok, report.render()
    assert calls <= 2 * (len(carried) + len(fresh)), calls
    refused = [one.message for one in report.refusals if one.rule == LEGACY_COUNT]
    assert len(refused) == len(carried) + len(fresh)


def test_a_row_the_merge_moved_reports_at_the_merge_only() -> None:
    candidate, agreeing = _moved_row_tree()
    other = fixture_tree_files()

    merged = _validate(candidate, bases=[tree(agreeing, "own"), tree(other, "incoming")])
    assert merged.ok, merged.render()
    assert "R09-history-rows-merged" in _rules(merged, refusing=False), merged.render()
    assert "R09-history-rows" not in _rules(merged), merged.render()
    (message,) = _messages(merged, "R09-history-rows-merged")
    assert "merge" in message and "refuses" in message
    assert "publication or landing" in message

    leaf = _validate(candidate, bases=[tree(agreeing, "K_B")])
    assert "R09-history-rows-merged" not in _rules(leaf, refusing=False), leaf.render()


# --------------------------------------------------------------------------------------------------
# Rule 11: migration_debt -- carried findings report until every route is migrated or excluded
# --------------------------------------------------------------------------------------------------


def test_a_carried_sidecar_without_markdown_reports_while_a_new_one_refuses() -> None:
    base = fixture_tree_files()
    del base[INTEGRATE_CARD]
    carried = _validate(dict(base), bases=[tree(dict(base), "K_B")])
    assert carried.ok, carried.render()
    assert "R22.3-sidecar-without-markdown" in _rules(carried, refusing=False), carried.render()
    (message,) = _messages(carried, "R22.3-sidecar-without-markdown")
    assert "conversion" in message and "refuse" in message

    files = fixture_tree_files()
    del files[INTEGRATE_CARD]
    new = _validate(files)
    assert not new.ok, new.render()
    assert "R22.3-sidecar-without-markdown" in _rules(new), new.render()


def test_a_carried_unresolved_target_reports_while_a_new_one_refuses() -> None:
    def add_target(document: dict[str, Any]) -> None:
        document["references"]["7"]["targets"].append(
            {"kind": "unresolved", "text": "legacy citation"}
        )

    base = edit_json(fixture_tree_files(), DIRECT_LANDING_SIDECAR, add_target)
    carried = _validate(base, bases=[tree(dict(base), "K_B")])
    assert carried.ok, carried.render()
    assert "R22.3-unresolved-target" in _rules(carried, refusing=False), carried.render()
    (message,) = _messages(carried, "R22.3-unresolved-target")
    assert "conversion" in message and "refuse" in message

    new = _validate(edit_json(fixture_tree_files(), DIRECT_LANDING_SIDECAR, add_target))
    assert not new.ok, new.render()
    assert "R22.3-unresolved-target" in _rules(new), new.render()


def test_a_carried_unassigned_family_reports_while_a_new_state_refuses() -> None:
    def unassign(document: dict[str, Any]) -> None:
        document["routes"] = []

    routeless = edit_json(fixture_tree_files(), FAMILY, unassign)
    carried = _validate(routeless, bases=[tree(dict(routeless), "K_B")])
    assert carried.ok, carried.render()
    assert "R04.4-route-unassigned" in _rules(carried, refusing=False), carried.render()
    (message,) = _messages(carried, "R04.4-route-unassigned")
    assert "conversion" in message and "refuse" in message

    new = _validate(edit_json(fixture_tree_files(), FAMILY, unassign))
    assert not new.ok, new.render()
    assert "R04.4-route-unassigned" in _rules(new), new.render()


def test_carried_legacy_debt_is_counted_while_new_legacy_refuses() -> None:
    base = fixture_tree_files()
    carried = _validate(base, bases=[tree(dict(base), "K_B")])
    assert carried.ok, carried.render()
    (count,) = [v for v in carried.reports if v.rule == LEGACY_COUNT]
    assert "live record(s) are still legacy-unassessed" in count.message
    assert "conversion" in count.message and "refuse" in count.message

    new = _validate(fixture_tree_files())
    assert not new.ok, new.render()
    assert LEGACY_COUNT in _rules(new), new.render()


# F10 tail: carried debt of every kind refuses once the migration is complete. ---


def _complete_census_files() -> dict[str, bytes]:
    return _census_files({"a": "migrated", "mcp/src/agents_remember/worktrees": "migrated"})


def test_carried_sidecar_without_markdown_refuses_once_complete() -> None:
    base = fixture_tree_files()
    del base[INTEGRATE_CARD]
    base.update(_complete_census_files())
    report = _validate(dict(base), bases=[tree(dict(base), "K_B")])
    assert not report.ok, report.render()
    assert "R22.3-sidecar-without-markdown" in _rules(report), report.render()


def test_carried_unresolved_target_refuses_once_complete() -> None:
    def add_target(document: dict[str, Any]) -> None:
        document["references"]["7"]["targets"].append(
            {"kind": "unresolved", "text": "legacy citation"}
        )

    base = edit_json(fixture_tree_files(), DIRECT_LANDING_SIDECAR, add_target)
    base.update(_complete_census_files())
    report = _validate(dict(base), bases=[tree(dict(base), "K_B")])
    assert not report.ok, report.render()
    assert "R22.3-unresolved-target" in _rules(report), report.render()


def test_carried_unassigned_family_refuses_once_complete() -> None:
    base = edit_json(fixture_tree_files(), FAMILY, lambda document: document.update(routes=[]))
    base.update(_complete_census_files())
    report = _validate(dict(base), bases=[tree(dict(base), "K_B")])
    assert not report.ok, report.render()
    assert "R04.4-route-unassigned" in _rules(report), report.render()


def test_carried_debt_reports_before_and_refuses_after_a_full_migration_census() -> None:
    base = fixture_tree_files()
    pending = {**base, **_census_files({WORKTREES: "pending", MODELS: "in_progress"})}
    before = _validate(pending, bases=[tree(dict(pending), "K_B")])
    assert before.ok, before.render()
    assert LEGACY_COUNT in _rules(before, refusing=False), before.render()
    (count,) = [v for v in before.reports if v.rule == LEGACY_COUNT]
    assert "2" in count.message, count.message

    migrated = {**base, **_census_files({WORKTREES: "migrated", MODELS: "excluded"})}
    after = _validate(migrated, bases=[tree(dict(migrated), "K_B")])
    assert not after.ok, after.render()
    assert LEGACY_COUNT in _rules(after), after.render()
    assert LEGACY_COUNT not in _rules(after, refusing=False), after.render()
    refused = [v for v in after.refusals if v.rule == LEGACY_COUNT]
    assert len(refused) == 1, after.render()


def test_new_debt_refuses_even_before_the_migration_census() -> None:
    base = fixture_tree_files()
    pending = {**base, **_census_files({WORKTREES: "pending", MODELS: "pending"})}

    report = _validate(pending)
    assert not report.ok, report.render()
    assert LEGACY_COUNT in _rules(report), report.render()


def test_debt_reports_when_the_census_cannot_be_read() -> None:
    base = fixture_tree_files()
    census = _census_files({WORKTREES: "migrated", MODELS: "migrated"})
    broken_path = "knowledge/census/CEN1/inventory.json"
    census[broken_path] = census[broken_path].replace(b"  ", b"    ")
    candidate = {**base, **census}

    report = _validate(candidate, bases=[tree(dict(candidate), "K_B")])

    assert "R20.1-census-canonical" in _rules(report), report.render()
    assert LEGACY_COUNT in _rules(report, refusing=False), report.render()


# --------------------------------------------------------------------------------------------------
# Rule 12: the messages say which it is
# --------------------------------------------------------------------------------------------------


def test_no_violation_message_says_reported_not_refused() -> None:
    files = fixture_tree_files()
    without_application = frozenset(p for p in CODE_PATHS if not p.startswith(APPLICATION))
    routes: list[dict[str, Any]] = [
        {"bases": [tree(files, "K_B")]},
        {"bases": [tree(files, "K_B")], "leaf_publication": True},
        {"bases": [tree(files, "left"), tree(files, "right")]},
    ]
    for kwargs in routes:
        report = _validate(files, code=code(without_application), **kwargs)
        for violation in report.violations:
            for banned in BANNED:
                assert banned not in violation.message, (violation.rule, violation.message)


def test_a_reported_finding_names_its_reason_and_the_route_that_refuses() -> None:
    files = fixture_tree_files()
    without_application = frozenset(p for p in CODE_PATHS if not p.startswith(APPLICATION))
    merged = _validate(
        files,
        bases=[tree(files, "left"), tree(files, "right")],
        code=code(without_application),
    )
    assert merged.ok, merged.render()
    for violation in merged.reports:
        assert "refuse" in violation.message, (violation.rule, violation.message)


def test_the_command_help_lists_the_ten_rules_with_their_decision() -> None:
    parser = argparse.ArgumentParser(prog="agents-remember knowledge-validate")
    add_arguments(parser)
    help_text = parser.format_help()

    for rule_id in sorted(MERGE_RULES | DEBT_RULES | {"R13.3-governs"}):
        assert rule_id in help_text, rule_id
    assert "merge_reports" in help_text and "migration_debt" in help_text


def test_the_c02_skill_lists_the_ten_rules_with_their_decision() -> None:
    text = quality_control_workflow_text()

    for rule_id in sorted(MERGE_RULES | DEBT_RULES | {"R13.3-governs"}):
        assert rule_id in text, rule_id
    assert "merge_reports" in text and "migration_debt" in text


def test_registry_module_helpers_do_not_leak() -> None:
    assert set(registry_module.ValidationRule.__dataclass_fields__) >= {
        "merge_reports",
        "migration_debt",
    }


def test_an_unrealized_family_refuses_at_publication() -> None:
    """F13 round 4: R04.4-unrealized-family refuses the landing/publication route too."""

    base = fixture_tree_files()
    candidate = _new_family(base)
    landing = _validate(candidate, bases=[tree(base, "K_B")], leaf_publication=True)
    assert not landing.ok, landing.render()
    assert "R04.4-unrealized-family" in _rules(landing), landing.render()


def test_completed_census_debt_refuses_at_landing_and_sync() -> None:
    """F13 round 4: once every onboarding route is migrated, carried debt refuses everywhere."""

    legacy = {
        "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        invariant_path("INV-CED977", "complete"): encode(
            invariant_document("INV-CED977", admission="legacy-unassessed")
        ),
        **_census_files({"a": "migrated"}),
    }
    landing = _validate(dict(legacy), bases=[tree(dict(legacy), "K_B")], leaf_publication=True)
    assert not landing.ok, landing.render()
    assert LEGACY_COUNT in _rules(landing), landing.render()
    sync = _validate(dict(legacy), bases=[tree(dict(legacy), "left"), tree(dict(legacy), "right")])
    assert not sync.ok, sync.render()
    assert LEGACY_COUNT in _rules(sync), sync.render()

    # Incomplete control: the same carried debt without a census still reports.
    bare = {
        "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        invariant_path("INV-CED977", "complete"): encode(
            invariant_document("INV-CED977", admission="legacy-unassessed")
        ),
    }
    reported = _validate(dict(bare), bases=[tree(dict(bare), "K_B")])
    assert reported.ok, reported.render()
    assert LEGACY_COUNT in _rules(reported, refusing=False), reported.render()


def test_a_merge_moved_row_is_refused_at_the_next_publication() -> None:
    """F13 round 4: an R09 merged report becomes a refusal at the next publication.

    The merge-only rule reports at the merge; at the next publication the same moved row
    is refused by the ordinary covered-entries rule (MIK-R07 rule 4).
    """

    candidate, agreeing = _moved_row_tree()
    other = fixture_tree_files()
    publication = _validate(
        candidate,
        bases=[tree(agreeing, "own"), tree(other, "incoming")],
        leaf_publication=True,
    )
    assert not publication.ok, publication.render()
    assert "R09-history-rows" in _rules(publication), publication.render()
    assert "ROW-3F9Q7K" in publication.render(), publication.render()


def _complete_minimal(extra: dict[str, bytes]) -> dict[str, bytes]:
    """A minimal tree with a complete one-route census, plus the carried debt under test."""

    return {
        "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        **_census_files({"a": "migrated"}),
        **extra,
    }


def _complete_sidecar(
    extra_reference: dict[str, Any] | None = None, *, card: bool = False
) -> dict[str, bytes]:
    references: dict[str, Any] = {}
    if extra_reference is not None:
        references = {"1": {"targets": [extra_reference]}}
    files = {
        "onboarding/x/a.py.json": encode(
            {
                "schema": "ar-onboarding-file/v1",
                "path": "x/a.py",
                "references": references,
                "realizes": [],
            }
        ),
    }
    if card:
        files["onboarding/x/a.py.md"] = b"# x/a.py\n\nSee [1].\n"
    return files


def test_completed_census_sidecar_without_markdown_refuses_at_landing_and_sync() -> None:
    """F13 round 5: completed-census sidecar debt refuses at landing and at sync."""

    debt = _complete_minimal(_complete_sidecar())
    assert migration_state(tree(dict(debt))).complete, "census must be complete"
    landing = _validate(dict(debt), bases=[tree(dict(debt), "K_B")], leaf_publication=True)
    assert not landing.ok, landing.render()
    assert "R22.3-sidecar-without-markdown" in _rules(landing), landing.render()
    sync = _validate(dict(debt), bases=[tree(dict(debt), "left"), tree(dict(debt), "right")])
    assert not sync.ok, sync.render()
    assert "R22.3-sidecar-without-markdown" in _rules(sync), sync.render()

    bare = {
        "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        **_complete_sidecar(),
    }
    reported = _validate(dict(bare), bases=[tree(dict(bare), "K_B")])
    assert reported.ok, reported.render()
    assert "R22.3-sidecar-without-markdown" in _rules(reported, refusing=False), reported.render()


def test_completed_census_unresolved_target_refuses_at_landing_and_sync() -> None:
    """F13 round 5: completed-census unresolved-target debt refuses at landing and sync."""

    debt = _complete_minimal(
        _complete_sidecar({"kind": "unresolved", "text": "legacy citation"}, card=True)
    )
    assert migration_state(tree(dict(debt))).complete, "census must be complete"
    landing = _validate(dict(debt), bases=[tree(dict(debt), "K_B")], leaf_publication=True)
    assert not landing.ok, landing.render()
    assert "R22.3-unresolved-target" in _rules(landing), landing.render()
    sync = _validate(dict(debt), bases=[tree(dict(debt), "left"), tree(dict(debt), "right")])
    assert not sync.ok, sync.render()
    assert "R22.3-unresolved-target" in _rules(sync), sync.render()

    bare = {
        "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        **_complete_sidecar({"kind": "unresolved", "text": "legacy citation"}, card=True),
    }
    reported = _validate(dict(bare), bases=[tree(dict(bare), "K_B")])
    assert reported.ok, reported.render()
    assert "R22.3-unresolved-target" in _rules(reported, refusing=False), reported.render()


def test_completed_census_route_unassigned_refuses_at_landing_and_sync() -> None:
    """F13 round 5: completed-census route-unassigned debt refuses at landing and sync."""

    family = {
        "knowledge/families/FAM-CMPETE-lone.json": encode(
            {
                "schema": "ar-family/v1",
                "id": "FAM-CMPETE",
                "origin": {"task": "260928-MIK", "leaf": "260928-MIK-L53"},
                "revision": 1,
                "status": "accepted",
                "title": "Lone",
                "guarantee": "Nothing yet.",
                "members": [],
                "routes": [],
                "admission": "legacy-unassessed",
            }
        ),
    }
    debt = _complete_minimal(family)
    assert migration_state(tree(dict(debt))).complete, "census must be complete"
    landing = _validate(dict(debt), bases=[tree(dict(debt), "K_B")], leaf_publication=True)
    assert not landing.ok, landing.render()
    assert "R04.4-route-unassigned" in _rules(landing), landing.render()
    sync = _validate(dict(debt), bases=[tree(dict(debt), "left"), tree(dict(debt), "right")])
    assert not sync.ok, sync.render()
    assert "R04.4-route-unassigned" in _rules(sync), sync.render()

    bare = {
        "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        **family,
    }
    reported = _validate(dict(bare), bases=[tree(dict(bare), "K_B")])
    assert "R04.4-route-unassigned" in _rules(reported, refusing=False), reported.render()
