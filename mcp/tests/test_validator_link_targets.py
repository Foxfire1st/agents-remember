"""Anchor and record-link path validation, including carried targets at sync merges."""

from __future__ import annotations

from typing import Any

import pytest
from agents_remember.memory_quality.knowledge_validator import validate_tree
from knowledge_validator_test_support import (
    CODE_PATHS,
    DIRECT_LANDING,
    DIRECT_LANDING_CARD,
    DIRECT_LANDING_SIDECAR,
    INTEGRATE,
    INTEGRATE_SIDECAR,
    LEGACY_COUNT,
    code,
    edit_json,
    fixture_tree_files,
    tree,
)
from test_knowledge_validator import _add_reference, _code_anchor, _rules, _validate

# Rule 6: anchors (moved without changing their assertions from test_knowledge_validator.py).


def test_an_added_anchor_must_name_an_existing_path() -> None:
    target = {"kind": "code", "anchor": _code_anchor("mcp/src/gone.py")}
    files = edit_json(fixture_tree_files(), DIRECT_LANDING_SIDECAR, _add_reference("8", target))
    files[DIRECT_LANDING_CARD] += b"\nAlso [8].\n"

    # MIK-R198 rule 5 refuses the same absent anchor as a stale reference anchor; the anchor-path
    # refusal is the one this case owns.
    report = _validate(files, bases=[tree(fixture_tree_files(), "K_B")])
    [line] = [
        violation.render() for violation in report.refusals if violation.rule == "R22.6-anchor-path"
    ]

    assert "references.8.targets.0.anchor.path" in line and "mcp/src/gone.py" in line


def test_a_carried_anchor_at_a_deleted_path_refuses_off_merge() -> None:
    files = fixture_tree_files()

    report = _validate(files, bases=[tree(files, "K_B")], code=code(frozenset()))

    assert not report.ok, report.render()
    # The family's carried routes are gone with the code, too: refused the same way (MIK-R04).
    assert _rules(report) == {
        "R22.6-carried-stale",
        "R04.1-carried-route-absent",
        # MIK-R198 rule 5: the same anchors are stale reference anchors as well.
        "R198.1-reference-anchor",
    } | {"R22.6-link-target-path"}
    assert _rules(report, refusing=False) == {LEGACY_COUNT}


def test_merge_where_one_parent_deleted_a_file_the_other_parents_card_cites() -> None:
    """Packet boundary example: the anchors are equal in one parent, so they are carried."""

    cites = fixture_tree_files()
    for number in ("8", "9", "10"):
        target = {"kind": "code", "anchor": _code_anchor("cli/knowledge_ingest.py", f"f{number}")}
        cites = edit_json(cites, DIRECT_LANDING_SIDECAR, _add_reference(number, target))
    cites[DIRECT_LANDING_CARD] += b"\nIngest [8], [9] and [10].\n"
    deleted = fixture_tree_files()

    report = _validate(
        cites, bases=[tree(cites, "citing parent"), tree(deleted, "deleting parent")]
    )

    assert report.ok, report.render()
    stale = [v for v in report.reports if v.rule == "R22.6-carried-stale"]
    assert len(stale) == 3


def test_a_re_anchored_entry_is_checked_and_a_moved_entry_carries_nothing() -> None:
    def move(document: dict[str, Any]) -> None:
        document["realizes"][0]["anchor"]["locator"]["name"] = "renamed"

    files = edit_json(fixture_tree_files(), INTEGRATE_SIDECAR, move)
    paths = frozenset({DIRECT_LANDING, "mcp/tests/test_direct_landing.py"})
    paths |= {"mcp/src/agents_remember/application/lifecycle/direct_landing.py"}

    report = _validate(files, bases=[tree(fixture_tree_files(), "K_B")], code=code(paths))

    assert _rules(report) == {
        "R22.6-anchor-path",
        "R22.6-carried-stale",
        "R04.1-carried-route-absent",
        "R198.1-reference-anchor",
    } | {"R22.6-link-target-path"}, report.render()
    [line] = [
        violation.render() for violation in report.refusals if violation.rule == "R22.6-anchor-path"
    ]
    assert line.startswith(f"{INTEGRATE_SIDECAR}: realizes.RLZ-4E1C9A.anchor") and INTEGRATE in line


def test_locator_kind_and_content_hash_are_anchor_rules() -> None:
    def bad(document: dict[str, Any]) -> None:
        document["realizes"][0]["anchor"]["locator"] = {"kind": "regex", "pattern": "x"}

    files = edit_json(fixture_tree_files(), INTEGRATE_SIDECAR, bad)
    assert _rules(_validate(files, bases=[tree(dict(files), "K_B")])) == {"R22.6-locator"}

    files = edit_json(
        fixture_tree_files(),
        INTEGRATE_SIDECAR,
        lambda doc: doc["realizes"][0]["anchor"].update(content="md5:abc"),
    )
    assert _rules(_validate(files, bases=[tree(dict(files), "K_B")])) == {"R22.6-content"}


def test_an_unconverted_base_is_refused_and_a_standalone_conversion_checks_no_path() -> None:
    files = fixture_tree_files()
    unconverted = {"onboarding/x.md": b"# x\n"}

    report = _validate(files, bases=[tree(unconverted, "K_B")])
    assert _rules(report) == {"R22.6-base-converted", LEGACY_COUNT}

    conversion = validate_tree(tree(files), code=None, conversion=True)
    assert conversion.ok and _rules(conversion, refusing=False) == {LEGACY_COUNT}
    with pytest.raises(ValueError, match="paired code tree"):
        validate_tree(tree(files))


@pytest.mark.parametrize("target_kind", ["anchor", "route"])
def test_record_link_path_targets_report_only_when_carried_at_sync_merge(target_kind: str) -> None:
    target = (
        {
            "path": "gone/file.py",
            "blob": "0" * 40,
            "content": "sha256:" + "a" * 64,
            "locator": {"kind": "symbol", "name": "gone"},
        }
        if target_kind == "anchor"
        else "route:gone"
    )
    base_files = fixture_tree_files()
    record_path = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
    candidate = edit_json(
        base_files,
        record_path,
        lambda doc: doc.setdefault("links", []).append(
            {"relation": "constrains", "target": target}
        ),
    )

    def findings(bases, **kwargs):
        return [
            one
            for one in validate_tree(tree(candidate), bases=bases, code=code(), **kwargs).violations
            if one.rule == "R22.6-link-target-path"
        ]

    for bases in ((), (tree(base_files),), (tree(base_files), tree(base_files))):
        (finding,) = findings(bases)
        assert finding.report_only is False
        assert finding.path == record_path and "gone" in finding.message
    (carried,) = findings((tree(candidate), tree(base_files)))
    assert carried.report_only is True
    assert "sync merge" in carried.message
    (landing,) = findings((tree(candidate), tree(base_files)), leaf_publication=True)
    assert landing.report_only is False
    (commit,) = findings((tree(candidate),))
    assert commit.report_only is False
    repaired_code = code(frozenset({*CODE_PATHS, "gone/file.py"}))
    repaired = validate_tree(tree(candidate), bases=(tree(base_files),), code=repaired_code)
    assert not [one for one in repaired.violations if one.rule == "R22.6-link-target-path"]
