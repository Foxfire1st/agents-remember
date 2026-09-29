"""MIK-R22: the mandatory knowledge validator's rules, over converted fixture trees.

The last section proves the admission rules MIK-R27 adds to the registry (MIK-R22 rule 9).

Each test names the packet rule it proves. The trees are the MIK-R21 Doc14 fixtures assembled into
one converted memory tree (``knowledge_validator_test_support``); a test changes one thing and checks
that exactly the owning rule answers, with the file, the field and the rule named.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from agents_remember.memory_quality.knowledge_validator import (
    Finding,
    KnowledgeValidationError,
    ValidationContext,
    ValidationReport,
    ValidationRule,
    register_rule,
    registered_rules,
    require_valid_commit,
    validate_tree,
    validation_applies,
)
from agents_remember.memory_quality.knowledge_validator import registry as registry_module
from agents_remember.memory_quality.knowledge_validator.markers import find_markers
from agents_remember.memory_quality.knowledge_validator.rules_admission import ADMISSION_RULES
from agents_remember.memory_quality.knowledge_validator.trees import is_excluded_from_knowledge
from agents_remember.models.knowledge_files import history_path
from agents_remember.models.knowledge_files.history import empty_history
from agents_remember.models.knowledge_files.ids import derived_record_id
from knowledge_validator_test_support import (
    DIRECT_LANDING,
    DIRECT_LANDING_CARD,
    DIRECT_LANDING_SIDECAR,
    FAMILY,
    INTEGRATE,
    INTEGRATE_CARD,
    INTEGRATE_SIDECAR,
    INVARIANT,
    LEGACY_COUNT,
    ROUTE_CARD,
    ROUTE_SIDECAR,
    code,
    edit_json,
    encode,
    fixture_tree_files,
    invariant_document,
    invariant_path,
    tree,
)

BLOB = "0" * 40
CONTENT = "sha256:" + "a" * 64


def _validate(files: dict[str, bytes], **kwargs: Any) -> ValidationReport:
    kwargs.setdefault("code", code())
    return validate_tree(tree(files), **kwargs)


def _rules(report: ValidationReport, *, refusing: bool = True) -> set[str]:
    selected = report.refusals if refusing else report.reports
    return {violation.rule for violation in selected}


def _only(report: ValidationReport, rule: str) -> list[str]:
    assert _rules(report) == {rule}, report.render()
    return [violation.render() for violation in report.refusals]


def _code_anchor(path: str | None = None, name: str = "helper") -> dict[str, Any]:
    anchor: dict[str, Any] = {
        "blob": BLOB,
        "content": CONTENT,
        "locator": {"kind": "symbol", "name": name},
    }
    if path is not None:
        anchor["path"] = path
    return anchor


def _add_reference(number: str, target: dict[str, Any]) -> Callable[[dict[str, Any]], None]:
    def change(document: dict[str, Any]) -> None:
        document["references"][number] = {"targets": [target]}

    return change


def test_converted_fixture_tree_passes_every_rule() -> None:
    report = _validate(fixture_tree_files())

    assert report.ok, report.render()
    assert [violation.rule for violation in report.violations] == [LEGACY_COUNT]
    rule_ids = [rule.id for rule in registered_rules()]
    assert len(rule_ids) == len(set(rule_ids))
    assert {
        rule.id
        for rule in registered_rules()
        if rule.report_only and rule.owner.startswith("MIK-R22")
    } == {
        "R22.3-sidecar-without-markdown",
        "R22.3-unresolved-target",
        "R22.6-carried-stale",
    }


# --------------------------------------------------------------------------------------------------
# Rule 1: shape and canonical formatting
# --------------------------------------------------------------------------------------------------


def test_shape_refuses_a_bad_field_and_names_file_field_and_rule() -> None:
    files = edit_json(fixture_tree_files(), INVARIANT, lambda doc: doc.update(revision=0))

    [line] = _only(_validate(files), "R22.1-shape")

    assert line.startswith(f"{INVARIANT}: revision: [R22.1-shape]")


def test_non_canonical_file_names_the_formatter_command() -> None:
    files = fixture_tree_files()
    files[INVARIANT] = files[INVARIANT].replace(b"  ", b"    ")

    [line] = _only(_validate(files), "R22.1-canonical")

    assert f"agents-remember knowledge-format {INVARIANT}" in line


def test_a_file_outside_the_layout_and_a_misplaced_sidecar_are_shape_violations() -> None:
    files = fixture_tree_files()
    files["knowledge/notes.json"] = files[INVARIANT]
    files["onboarding/elsewhere/overview.json"] = files.pop(ROUTE_SIDECAR)
    files["onboarding/elsewhere/overview.md"] = files.pop(ROUTE_CARD)

    lines = _only(_validate(files), "R22.1-shape")

    assert any("knowledge/notes.json" in line and "location" in line for line in lines)
    assert any("this sidecar lives at" in line for line in lines)


# --------------------------------------------------------------------------------------------------
# Rule 2: identity
# --------------------------------------------------------------------------------------------------


def test_filename_must_begin_with_the_record_id() -> None:
    files = fixture_tree_files()
    files[invariant_path("INV-AAAAAA")] = files.pop(invariant_path("INV-R8M2TD"))

    lines = _only(_validate(files), "R22.2-identity")

    assert "the filename begins with INV-AAAAAA, not the record's ID INV-R8M2TD" in lines[0]


def test_duplicate_ids_after_a_merge_are_a_conflict_naming_both_files() -> None:
    base = fixture_tree_files()
    left = dict(base)
    left[invariant_path("INV-D0P111", "left")] = encode(invariant_document("INV-D0P111"))
    right = dict(base)
    right[invariant_path("INV-D0P111", "right")] = encode(invariant_document("INV-D0P111"))
    merged = {**left, **right}

    report = _validate(merged, bases=[tree(left, "left"), tree(right, "right")])

    [line] = _only(report, "R22.2-identity")
    assert "merge conflict: ID INV-D0P111" in line
    assert invariant_path("INV-D0P111", "left") in line
    assert invariant_path("INV-D0P111", "right") in line


def test_parallel_leaves_minting_different_ids_merge_cleanly() -> None:
    base = fixture_tree_files()
    left = {**base, invariant_path("INV-AAAAAA"): encode(invariant_document("INV-AAAAAA"))}
    right = {**base, invariant_path("INV-BBBBBB"): encode(invariant_document("INV-BBBBBB"))}

    report = _validate({**left, **right}, bases=[tree(left, "left"), tree(right, "right")])

    assert report.ok, report.render()


def test_duplicate_entry_ids_across_sidecars_are_refused() -> None:
    files = edit_json(
        fixture_tree_files(),
        INTEGRATE_SIDECAR,
        lambda doc: doc["realizes"][0].update(id="RLZ-D1RW4N"),
    )

    [line] = _only(_validate(files), "R22.2-identity")

    assert "RLZ-D1RW4N" in line and DIRECT_LANDING_SIDECAR in line and INTEGRATE_SIDECAR in line


# --------------------------------------------------------------------------------------------------
# Rule 3: markers, references, record links, relations, unresolved targets
# --------------------------------------------------------------------------------------------------


def test_a_hand_added_marker_without_a_reference_is_refused() -> None:
    """Packet non-conforming example: ``[4]`` added to ``integrate.py.md`` with no entry."""

    files = fixture_tree_files()
    files[INTEGRATE_CARD] += b"\nThe landing pair is recorded here [4].\n"

    [line] = _only(_validate(files), "R22.3-markers")

    assert line.startswith(f"{INTEGRATE_CARD}: line 3: [R22.3-markers] marker [4] has no reference")


def test_an_unused_reference_is_refused() -> None:
    files = fixture_tree_files()
    files[DIRECT_LANDING_CARD] = files[DIRECT_LANDING_CARD].replace(b" [7]", b"")

    [line] = _only(_validate(files), "R22.3-markers")

    assert line.startswith(f"{DIRECT_LANDING_SIDECAR}: references.7:")


def test_markdown_without_a_sidecar_and_a_record_card_have_no_markers() -> None:
    files = fixture_tree_files()
    files["onboarding/mcp/README.md.md"] = b"# README\n\nSee [1].\n"
    files[INVARIANT.replace(".json", ".md")] = b"# Landing pair\n\nProved by [2].\n"

    lines = _only(_validate(files), "R22.3-markers")

    assert len(lines) == 2
    assert all("without a sidecar" in line for line in lines)


def test_a_file_sidecar_without_markdown_is_reported_not_refused() -> None:
    files = fixture_tree_files()
    del files[INTEGRATE_CARD]

    report = _validate(files)

    assert report.ok, report.render()
    assert _rules(report, refusing=False) == {"R22.3-sidecar-without-markdown", LEGACY_COUNT}


def test_a_file_sidecar_without_markdown_holds_no_references() -> None:
    files = edit_json(
        fixture_tree_files(),
        INTEGRATE_SIDECAR,
        _add_reference("1", {"kind": "invariant", "id": "INV-7K3F9Q"}),
    )
    del files[INTEGRATE_CARD]

    assert _only(_validate(files), "R22.3-markers")[0].startswith(
        f"{INTEGRATE_SIDECAR}: references.1"
    )


def test_every_referenced_or_linked_record_id_exists() -> None:
    files = edit_json(
        fixture_tree_files(),
        DIRECT_LANDING_SIDECAR,
        lambda doc: doc["references"]["5"]["targets"][0].update(id="INV-M1SS11"),
    )
    decision = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
    files = edit_json(files, decision, lambda doc: doc["links"][0].update(target="INV-M1SS22"))

    lines = _only(_validate(files), "R22.3-record-links")

    assert any("references.5.targets.0.id" in line and "INV-M1SS11" in line for line in lines)
    assert any(f"{decision}: links.0.target" in line for line in lines)


def test_a_retired_record_still_resolves() -> None:
    files = edit_json(fixture_tree_files(), INVARIANT, lambda doc: doc.update(status="retired"))

    assert _validate(files).ok


def test_an_unresolved_target_is_reported_not_refused() -> None:
    files = edit_json(
        fixture_tree_files(),
        DIRECT_LANDING_SIDECAR,
        lambda doc: doc["references"]["7"]["targets"].append(
            {"kind": "unresolved", "text": "legacy citation"}
        ),
    )

    report = _validate(files)

    assert report.ok and _rules(report, refusing=False) == {"R22.3-unresolved-target", LEGACY_COUNT}


def test_a_disallowed_relation_is_refused_under_the_relations_rule_by_field() -> None:
    decision = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
    files = edit_json(
        fixture_tree_files(), decision, lambda doc: doc["links"][1].update(relation="bounds")
    )

    [line] = _only(_validate(files), "R22.3-relations")

    assert line.startswith(f"{decision}: links.1.relation: [R22.3-relations] relation 'bounds'")


def test_a_dot_named_card_and_its_sidecar_are_validated() -> None:
    """A card named for a dot-file (``.git-blame-ignore-revs``) is knowledge like any other."""

    files = fixture_tree_files()
    files["onboarding/.git-blame-ignore-revs.md"] = b"# .git-blame-ignore-revs\n\nSee [1].\n"
    files["onboarding/.git-blame-ignore-revs.json"] = encode(
        {
            "schema": "ar-onboarding-file/v1",
            "path": ".git-blame-ignore-revs",
            "references": {},
            "realizes": [],
        }
    )

    [line] = _only(_validate(files), "R22.3-markers")

    assert line.startswith("onboarding/.git-blame-ignore-revs.md: line 3:")
    assert "has no reference in onboarding/.git-blame-ignore-revs.json" in line  # sidecar read
    assert is_excluded_from_knowledge("onboarding/.ar-index/overview.json")
    assert is_excluded_from_knowledge("onboarding/mcp/overview.index.json")
    assert not is_excluded_from_knowledge("onboarding/.git-blame-ignore-revs.json")


# --------------------------------------------------------------------------------------------------
# Rules 4 and 5: single owner, family members
# --------------------------------------------------------------------------------------------------


def test_an_invariant_record_may_not_list_its_realizations() -> None:
    files = edit_json(
        fixture_tree_files(), INVARIANT, lambda doc: doc.update(realizations=["RLZ-D1RW4N"])
    )

    [line] = _only(_validate(files), "R22.4-single-owner")

    assert "'realizations' is not a field of this record" in line


def test_every_family_member_exists() -> None:
    family = "knowledge/families/FAM-SEQNTS6C-attribution-and-landing-pairing.json"
    files = fixture_tree_files()
    del files[invariant_path("INV-R8M2TD")]

    [line] = _only(_validate(files), "R22.5-family-members")

    assert line.startswith(f"{family}: members.1:") and "INV-R8M2TD" in line


# --------------------------------------------------------------------------------------------------
# Rule 6: anchors
# --------------------------------------------------------------------------------------------------


def test_an_added_anchor_must_name_an_existing_path() -> None:
    target = {"kind": "code", "anchor": _code_anchor("mcp/src/gone.py")}
    files = edit_json(fixture_tree_files(), DIRECT_LANDING_SIDECAR, _add_reference("8", target))
    files[DIRECT_LANDING_CARD] += b"\nAlso [8].\n"

    [line] = _only(_validate(files, bases=[tree(fixture_tree_files(), "K_B")]), "R22.6-anchor-path")

    assert "references.8.targets.0.anchor.path" in line and "mcp/src/gone.py" in line


def test_a_carried_anchor_at_a_deleted_path_is_reported_stale_not_refused() -> None:
    files = fixture_tree_files()

    report = _validate(files, bases=[tree(files, "K_B")], code=code(frozenset()))

    assert report.ok, report.render()
    # The family's carried routes are gone with the code, too: reported the same way (MIK-R04).
    assert _rules(report, refusing=False) == {
        "R22.6-carried-stale",
        "R04.1-carried-route-absent",
        LEGACY_COUNT,
    }


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

    [line] = _only(report, "R22.6-anchor-path")
    assert line.startswith(f"{INTEGRATE_SIDECAR}: realizes.RLZ-4E1C9A.anchor") and INTEGRATE in line


def test_locator_kind_and_content_hash_are_anchor_rules() -> None:
    def bad(document: dict[str, Any]) -> None:
        document["realizes"][0]["anchor"]["locator"] = {"kind": "regex", "pattern": "x"}

    files = edit_json(fixture_tree_files(), INTEGRATE_SIDECAR, bad)
    assert _rules(_validate(files)) == {"R22.6-locator"}

    files = edit_json(
        fixture_tree_files(),
        INTEGRATE_SIDECAR,
        lambda doc: doc["realizes"][0]["anchor"].update(content="md5:abc"),
    )
    assert _rules(_validate(files)) == {"R22.6-content"}


def test_an_unconverted_base_is_refused_and_a_standalone_conversion_checks_no_path() -> None:
    files = fixture_tree_files()
    unconverted = {"onboarding/x.md": b"# x\n"}

    report = _validate(files, bases=[tree(unconverted, "K_B")])
    assert _rules(report) == {"R22.6-base-converted"}

    conversion = validate_tree(
        tree(files), bases=[tree(unconverted, "K_B")], code=None, conversion=True
    )
    assert conversion.ok and _rules(conversion, refusing=False) == {LEGACY_COUNT}
    with pytest.raises(ValueError, match="paired code tree"):
        validate_tree(tree(files))


# --------------------------------------------------------------------------------------------------
# Rule 7: history files
# --------------------------------------------------------------------------------------------------


def _history(owner: str, *, closed: bool, anchor_path: str = INTEGRATE) -> bytes:
    document = empty_history("leaf", owner, closed=closed).to_document()
    document["rows"] = [
        {
            "id": "ROW-AAAAAA",
            "subject": "INV-RET1R3",
            "disposition": "moved",
            "reason": "re-anchored after the rename",
            "items": [],
            "revision": 1,
            "covers": [
                {
                    "id": "RLZ-4E1C9A",
                    "before": _code_anchor(anchor_path, "old"),
                    "after": _code_anchor(INTEGRATE, "new"),
                }
            ],
        }
    ]
    return encode(document)


def test_a_closed_history_file_is_frozen() -> None:
    """Packet non-conforming example: a closed leaf's history file is edited later."""

    path = history_path("260928-MIK-L01")
    base = {**fixture_tree_files(), path: _history("260928-MIK-L01", closed=True)}
    edited = dict(base)
    edited[path] = edited[path].replace(b"re-anchored", b"moved")
    deleted = {key: value for key, value in base.items() if key != path}

    assert _only(_validate(edited, bases=[tree(base, "K_B")]), "R22.7-history-frozen")
    assert (
        "was deleted"
        in _only(_validate(deleted, bases=[tree(base, "K_B")]), "R22.7-history-frozen")[0]
    )
    merged_bases = [tree(fixture_tree_files(), "left"), tree(base, "right")]
    assert _only(_validate(edited, bases=merged_bases), "R22.7-history-frozen")


def test_an_open_history_file_may_change() -> None:
    path = history_path("260928-MIK-L02")
    base = {**fixture_tree_files(), path: _history("260928-MIK-L02", closed=False)}
    edited = {**base, path: _history("260928-MIK-L02", closed=True)}

    assert _validate(edited, bases=[tree(base, "K_B")]).ok


def test_history_is_checked_for_shape_only() -> None:
    """Packet boundary example: a closed history names a renamed path and a retired subject."""

    path = history_path("260928-MIK-L03")
    renamed = "mcp/src/agents_remember/worktrees/modules/integrate_old.py"
    files = {
        **fixture_tree_files(),
        path: _history("260928-MIK-L03", closed=True, anchor_path=renamed),
    }

    assert _validate(files, bases=[tree(files, "K_B")]).ok
    files[path] = files[path].replace(b'"closed": true', b'"closed": "yes"')
    assert _rules(_validate(files)) == {"R22.1-shape"}


# --------------------------------------------------------------------------------------------------
# Rule 8: applicability, and the commit-route entry point
# --------------------------------------------------------------------------------------------------


def test_commit_routes_validate_only_when_a_side_has_the_layout_marker() -> None:
    unconverted = tree({"onboarding/x.md": b"# x [1]\n"}, "unconverted")
    converted = tree(fixture_tree_files(), "K_B")
    marker_deleted = {k: v for k, v in fixture_tree_files().items() if k != "knowledge/layout.json"}

    assert not validation_applies(unconverted, [unconverted])
    assert require_valid_commit(unconverted, bases=[unconverted], code=code()) is None
    assert require_valid_commit(converted, bases=[converted], code=code()) is not None
    with pytest.raises(KnowledgeValidationError) as refused:
        require_valid_commit(tree(marker_deleted), bases=[converted], code=code())
    assert "knowledge/layout.json: -: [R22.1-shape] the layout marker is missing" in str(
        refused.value
    )


# --------------------------------------------------------------------------------------------------
# Rule 9: the registry
# --------------------------------------------------------------------------------------------------


def test_a_later_packets_rule_runs_everywhere_and_report_only_never_refuses() -> None:
    def always(context: ValidationContext) -> list[Finding]:
        return [Finding(INVARIANT, "statement", f"checked {len(context.parsed.records)} records")]

    report_only = ValidationRule("TEST.report", "test", "reports", always, report_only=True)
    refusing = ValidationRule("TEST.refuse", "test", "refuses", always)
    converted = tree(fixture_tree_files(), "K_B")
    try:
        register_rule(report_only)
        with pytest.raises(ValueError, match="already registered"):
            register_rule(report_only)
        assert require_valid_commit(converted, bases=[converted], code=code()) is not None
        register_rule(refusing)
        with pytest.raises(KnowledgeValidationError, match=r"\[TEST.refuse\] checked 6 records"):
            require_valid_commit(converted, bases=[converted], code=code())
    finally:
        registry_module._REGISTRY.pop("TEST.report", None)
        registry_module._REGISTRY.pop("TEST.refuse", None)


# --------------------------------------------------------------------------------------------------
# Markers
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("See [1] and [12].", ["1", "12"]),
        ("Escaped \\[3], code `x[4]`, double ``y`[5]`` and \\\\[6].", ["6"]),
        ("```\n[7]\n```\n~~~~\n[8]\n~~~\n[9]\n~~~~\nafter [10]", ["10"]),
        ("signals[0] and [01]", ["0", "01"]),
        ("an unclosed ` backtick [2]", ["2"]),
    ],
)
def test_markers_are_unescaped_bracketed_numbers_outside_code(
    text: str, expected: list[str]
) -> None:
    assert [marker.number for marker in find_markers(text)] == expected


def test_an_invalid_marker_number_names_how_to_write_it_as_text() -> None:
    files = fixture_tree_files()
    files[INTEGRATE_CARD] += b"\nThe first slot signals[0] is empty.\n"

    [line] = _only(_validate(files), "R22.3-markers")

    assert "[0] is not a reference number" in line and "\\[n]" in line


# --------------------------------------------------------------------------------------------------
# MIK-R27: the admission rule (rule 9's registry). A new record is refused, naming the record and the
# criterion; an exported, stored or retired record is only reported; legacy records are counted.
# --------------------------------------------------------------------------------------------------

NEW = "R27.2-new-record"
EXISTING = "R27.2-existing-record"
DECISION = "knowledge/decisions/DEC-D12RTE-local-family-routes.json"
PROOF_SIDECAR = "onboarding/mcp/tests/test_direct_landing.py.json"


def _messages(report: ValidationReport, rule: str) -> list[str]:
    return [violation.render() for violation in report.violations if violation.rule == rule]


def _admission(criteria: list[str], justification: str) -> Any:
    def change(document: dict[str, Any]) -> None:
        document["admission"] = {"criteria": criteria, "justification": justification}

    return change


def _without_proof(files: dict[str, bytes]) -> dict[str, bytes]:
    return edit_json(files, PROOF_SIDECAR, lambda document: document.update(proves=[]))


def _realized_in_one_file(files: dict[str, bytes]) -> dict[str, bytes]:
    """Leave INV-7K3F9Q's realizations in ``integrate.py`` only."""

    realizing = [
        path
        for path, data in files.items()
        if path.startswith("onboarding/")
        and path.endswith(".json")
        and path != INTEGRATE_SIDECAR
        and b'"realizes"' in data
        and b"INV-7K3F9Q" in data
    ]
    edited = dict(files)
    for path in realizing:
        edited = edit_json(edited, path, lambda document: document.update(realizes=[]))
    return edited


EXPORTED_LEGACY_ID = "4f0c2a3e-legacy-invariant"
EXPORTED = derived_record_id("invariant", EXPORTED_LEGACY_ID)


def _with_export(
    files: dict[str, bytes], admission: Any, *, identifier: str = EXPORTED
) -> dict[str, bytes]:
    """Add an exported invariant with no entries: ``origin.legacyId`` names its legacy record.

    With the default ``identifier`` the legacy ID derives the record's ID, as the conversion writes
    it (MIK-R24 rule 4); any other ``identifier`` forges the legacy ID.
    """

    document = invariant_document(identifier, admission=admission)
    document["origin"] = {"task": "260915-KS", "legacyId": EXPORTED_LEGACY_ID}
    return {**files, invariant_path(identifier, "exported"): encode(document)}


def test_the_packets_admitted_example_passes_as_a_new_record() -> None:
    """``spans_locations`` realized in ``integrate.py`` and more; ``guarded_by_test`` proved."""

    report = _validate(fixture_tree_files())

    assert report.ok, report.render()
    assert _messages(report, NEW) == [] and _messages(report, EXISTING) == []


def test_a_new_record_whose_justification_is_only_a_reference_is_refused() -> None:
    refused = (
        "introduced by L43",
        "Added in L43.",
        "260928-MIK-L27",
        "Per MIK-R27@v1 rule 2",
        "See ICR-R03@v1 (L43), acceptance criteria.",
        # Developer rulings and commit hashes are provenance too (L27 rulings round, Q2).
        "D14",
        "4e1c9a7f2b",
        "D14, L43",
        # Provenance phrasings (review round F1).
        "Per ruling D14",
        "Per developer ruling D14",
        "Added in commit a4eba7b7",
        "L43's acceptance criteria",
        "L43/L44",
        "Implements R27.2",
        "§2",
        "Required by MIK-R27 §2",
        "ICR L45",
        "L43 \u2014 see requirement",
        "Implemented in L43 step S2",
        "Added on 2026-09-28 in L43",
    )
    admitted = (
        "Guards against landing half a pair (L43).",
        "Realized in integrate.py and direct_landing.py; added in L43.",
        "Per D14 at 4e1c9a7f2b: a landing that pairs the wrong commits corrupts the ledger.",
        # Real prose that merely contains reference-shaped words is never refused.
        "Deadbeef cafe faced a decade",
        "Prevents a2b3c4d5 collisions",
        "Uses D3 to render the chart",
        "The D14 rule forbids stale reads",
        "L1 cache and L2 cache",
        "Fixes the commit ordering",
    )
    for justification in refused:
        files = edit_json(
            fixture_tree_files(), INVARIANT, _admission(["spans_locations"], justification)
        )
        (message,) = _messages(_validate(files), NEW)
        assert "INV-7K3F9Q" in message and "admission.justification" in message, justification
        assert "only task, leaf, requirement or ruling references or commit hashes" in message
        assert "spans_locations" in message
    for justification in admitted:
        files = edit_json(
            fixture_tree_files(), INVARIANT, _admission(["spans_locations"], justification)
        )
        assert _validate(files).ok, justification

    # The same rule holds for a new family and a new decision.
    files = edit_json(
        fixture_tree_files(),
        DECISION,
        _admission(["real_alternatives"], "Added in 260928-MIK-L12."),
    )
    files = edit_json(files, FAMILY, _admission(["joint_guarantee"], "From 260915-KS."))
    files = edit_json(files, FAMILY, lambda document: document["origin"].pop("legacyId"))
    decision, family = _messages(_validate(files), NEW)
    assert "new decision DEC-D12RTE" in decision and "real_alternatives" in decision
    assert "new family FAM-SEQNTS6C" in family and "joint_guarantee" in family


def test_a_new_record_without_a_criterion_or_marked_legacy_unassessed_is_refused() -> None:
    files = edit_json(fixture_tree_files(), INVARIANT, _admission([], "It matters."))
    report = _validate(files)
    assert not report.ok
    # The record does not parse (MIK-R21 rule 4: at least one criterion), so the shape rule refuses
    # it, naming the file and the admission's criteria field.
    shapes = [one for one in report.refusals if one.rule == "R22.1-shape"]
    assert {one.path for one in shapes} == {INVARIANT}
    assert any(
        one.field.startswith("admission") and one.field.endswith("criteria") for one in shapes
    )

    files = edit_json(
        fixture_tree_files(),
        INVARIANT,
        lambda document: document.update(admission="legacy-unassessed"),
    )
    (message,) = _messages(_validate(files), NEW)
    assert "new invariant INV-7K3F9Q is legacy-unassessed" in message


def test_an_unsupported_checkable_criterion_on_a_new_record_is_refused_naming_it() -> None:
    no_test = _validate(_without_proof(fixture_tree_files()))
    (message,) = _messages(no_test, NEW)
    assert "INV-7K3F9Q claims guarded_by_test but no proof entry" in message

    one_file = _validate(_realized_in_one_file(fixture_tree_files()))
    (message,) = _messages(one_file, NEW)
    assert "INV-7K3F9Q claims spans_locations" in message
    assert "mcp/src/agents_remember/worktrees/modules/integrate.py" in message
    assert not one_file.ok and not no_test.ok


def test_an_existing_record_whose_test_was_deleted_is_only_reported() -> None:
    base = fixture_tree_files()
    candidate = _without_proof(base)

    report = _validate(candidate, bases=[tree(base, "K_B")])

    assert report.ok, report.render()
    (reported,) = [one for one in report.reports if one.rule == EXISTING]
    assert reported.path == INVARIANT and reported.field == "admission.criteria"
    assert "guarded_by_test" in reported.message and "reported, not refused" in reported.message


def test_exported_retired_and_merged_records_are_never_refused() -> None:
    unsupported = _without_proof(fixture_tree_files())

    claim = {"criteria": ["guarded_by_test"], "justification": "L43"}
    report = _validate(_with_export(fixture_tree_files(), claim))
    assert report.ok, report.render()
    (reported,) = _messages(report, EXISTING)
    assert EXPORTED in reported and "guarded_by_test" in reported

    retired = edit_json(unsupported, INVARIANT, lambda document: document.update(status="retired"))
    retired = edit_json(retired, INVARIANT, _admission(["guarded_by_test"], "L43"))
    report = _validate(retired)
    assert report.ok and _messages(report, EXISTING) == [], report.render()

    # At a merge, a record either parent holds is not new: an unrelated sync is never blocked.
    other_parent = {path: data for path, data in unsupported.items() if path != INVARIANT}
    report = _validate(
        unsupported, bases=[tree(unsupported, "own"), tree(other_parent, "incoming")]
    )
    assert report.ok and len(_messages(report, EXISTING)) == 1, report.render()


def test_a_forged_legacy_id_does_not_make_a_record_exported() -> None:
    """Review round F2: only a ``legacyId`` that derives the record's ID marks an export."""

    unsupported = {"criteria": ["guarded_by_test"], "justification": "A test pins the export."}
    genuine = _validate(_with_export(fixture_tree_files(), unsupported))
    assert genuine.ok and _messages(genuine, NEW) == [], genuine.render()
    assert len(_messages(genuine, EXISTING)) == 1

    forged = _validate(_with_export(fixture_tree_files(), unsupported, identifier="INV-F0RG3D"))
    (message,) = _messages(forged, NEW)
    assert "new invariant INV-F0RG3D claims guarded_by_test" in message
    forged_unassessed = _validate(
        _with_export(fixture_tree_files(), "legacy-unassessed", identifier="INV-F0RG3D")
    )
    (message,) = _messages(forged_unassessed, NEW)
    assert "new invariant INV-F0RG3D is legacy-unassessed" in message


def test_legacy_records_are_counted_until_assessed_or_demoted() -> None:
    files = _with_export(fixture_tree_files(), "legacy-unassessed")

    (count,) = [one for one in _validate(files).reports if one.rule == LEGACY_COUNT]
    assert count.message.startswith("2 live record(s) are still legacy-unassessed")
    assert "1 family(s), 1 invariant(s)" in count.message

    # Assessed: the exported invariant states its criterion. Demoted: the family is retired and its
    # file kept. Neither is counted any more.
    assessed = edit_json(
        files,
        invariant_path(EXPORTED, "exported"),
        _admission(["prevents_costly_mistake"], "An unpaired landing corrupts the ledger."),
    )
    demoted = edit_json(assessed, FAMILY, lambda document: document.update(status="retired"))
    report = _validate(demoted)
    assert FAMILY in demoted and [one for one in report.reports if one.rule == LEGACY_COUNT] == []


def test_the_rules_are_registered_refusing_new_and_reporting_the_rest() -> None:
    registered = {rule.id: rule for rule in registered_rules()}
    flags = {rule.id: (rule.report_only, rule.writer_reports) for rule in ADMISSION_RULES}
    assert flags == {
        NEW: (False, False),
        EXISTING: (True, False),
        LEGACY_COUNT: (True, False),
    }
    assert all(
        registered[rule_id] is rule for rule_id, rule in ((r.id, r) for r in ADMISSION_RULES)
    )
