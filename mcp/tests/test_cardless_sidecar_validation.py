"""Entry sidecars stand without prose while the validator keeps its other checks."""

from __future__ import annotations

import json
from typing import Any

import pytest
from agents_remember.memory_quality.knowledge_validator import (
    CodePathSet,
    require_valid_commit,
    validate_tree,
)
from agents_remember.models.knowledge_files import history_path
from agents_remember.models.knowledge_files.history import empty_history
from knowledge_validator_test_support import (
    DIRECT_LANDING,
    INTEGRATE,
    INTEGRATE_SIDECAR,
    INVARIANT,
    TEST_DIRECT_LANDING,
    UNCHECKED_ADMISSION,
    encode,
    invariant_document,
    load_fixture,
    tree,
)

CODE = CodePathSet(
    label="paired-code",
    paths=frozenset({DIRECT_LANDING, INTEGRATE, TEST_DIRECT_LANDING}),
)


def _base_files() -> dict[str, bytes]:
    return {
        "knowledge/layout.json": encode(load_fixture("layout.json")),
        INVARIANT: encode(invariant_document("INV-7K3F9Q", admission=UNCHECKED_ADMISSION)),
    }


def _cardless_files() -> dict[str, bytes]:
    return {
        **_base_files(),
        INTEGRATE_SIDECAR: encode(load_fixture("r21-integrate.py.json")),
    }


@pytest.mark.parametrize("fixture", ["r21-integrate.py.json", "4.4-test_direct_landing.py.json"])
def test_a_new_entry_sidecar_without_references_needs_no_markdown(fixture: str) -> None:
    sidecar = load_fixture(fixture)
    path = f"onboarding/{sidecar['path']}.json"
    files = {**_base_files(), path: encode(sidecar)}

    report = require_valid_commit(
        tree(files), bases=[tree(_base_files(), "before-first-entry")], code=CODE
    )

    assert report is not None and report.ok, None if report is None else report.render()
    assert report.violations == ()
    assert not any(path.endswith(".md") for path in files)


def test_references_still_require_their_markdown_card() -> None:
    files = _cardless_files()
    document = json.loads(files[INTEGRATE_SIDECAR])
    document["references"] = {"1": {"targets": [{"kind": "invariant", "id": "INV-7K3F9Q"}]}}
    files[INTEGRATE_SIDECAR] = encode(document)

    refused = validate_tree(tree(files), bases=[tree(_cardless_files(), "entries-only")], code=CODE)
    assert not refused.ok
    assert any(
        finding.rule == "R22.3-markers" and finding.field == "references.1"
        for finding in refused.refusals
    ), refused.render()
    assert any(finding.rule == "R22.3-sidecar-without-markdown" for finding in refused.refusals), (
        refused.render()
    )

    files[f"onboarding/{INTEGRATE}.md"] = b"# Integration\n\nThe invariant is enforced here [1].\n"
    accepted = validate_tree(tree(files), code=CODE)
    assert accepted.ok, accepted.render()


@pytest.mark.parametrize(
    ("fault", "rule"),
    [
        ("duplicate-entry", "R22.2-identity"),
        ("malformed-entry-id", "R22.1-shape"),
        ("missing-invariant", "R22.3-record-links"),
        ("locator", "R22.6-locator"),
        ("content", "R22.6-content"),
        ("blob", "R22.1-shape"),
        ("absent-source", "R22.6-anchor-path"),
    ],
)
def test_cardless_entries_keep_identity_link_and_anchor_checks(fault: str, rule: str) -> None:
    files = _cardless_files()
    document: dict[str, Any] = json.loads(files[INTEGRATE_SIDECAR])
    entry = document["realizes"][0]
    code = CODE
    if fault == "duplicate-entry":
        duplicate = {**document, "path": DIRECT_LANDING}
        files[f"onboarding/{DIRECT_LANDING}.json"] = encode(duplicate)
    elif fault == "malformed-entry-id":
        entry["id"] = "not-an-entry-id"
    elif fault == "missing-invariant":
        entry["invariant"] = "INV-M1SS11"
    elif fault == "locator":
        entry["anchor"]["locator"] = {"kind": "unknown"}
    elif fault == "content":
        entry["anchor"]["content"] = "not-a-content-identity"
    elif fault == "blob":
        entry["anchor"]["blob"] = "not-a-git-blob"
    else:
        code = CodePathSet(label="source-removed", paths=frozenset())
    files[INTEGRATE_SIDECAR] = encode(document)

    report = validate_tree(tree(files), code=code)

    assert not report.ok
    assert {finding.rule for finding in report.refusals} == {rule}, report.render()


def test_cardless_entries_keep_closed_history_freezing_and_shape_checks() -> None:
    files = _cardless_files()
    path = history_path("foundation-leaf")
    closed = empty_history("leaf", "foundation-leaf", closed=True).to_document()
    base = {**files, path: encode(closed)}
    reopened = {**base, path: encode({**closed, "closed": False})}

    frozen = validate_tree(tree(reopened), bases=[tree(base, "closed")], code=CODE)
    assert {finding.rule for finding in frozen.refusals} == {"R22.7-history-frozen"}, (
        frozen.render()
    )

    malformed = {**files, path: encode({**closed, "closed": "yes"})}
    invalid = validate_tree(tree(malformed), code=CODE)
    assert {finding.rule for finding in invalid.refusals} == {"R22.1-shape"}, invalid.render()
