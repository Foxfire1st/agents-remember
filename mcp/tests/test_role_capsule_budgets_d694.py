"""Actual-current D694 budget layer and independent negative controls."""

import copy
import json
from hashlib import sha256
from pathlib import Path

import pytest
from agents_remember_test_support.testing.capsule_budget_d694 import (
    _current_area_findings,
    _source_findings,
    current_budget_findings,
    current_budget_view,
    load_current_budget_view,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    COMMAND,
    _current_source_findings,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    _current_area_findings as current_area_findings,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    _source_findings as current_source_findings,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    current_budget_findings as current_d695_findings,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    load_current_budget_view as load_d695_view,
)
from test_role_capsule_budgets import (
    CURRENT_BUDGETS_PATH,
    D695_CURRENT_BUDGETS_PATH,
    REPOSITORY_ROOT,
    compiled_size,
    detail_sizes,
    digest_size,
    matrix_keys,
)


def test_d695_binds_the_actual_current_population() -> None:
    record = load_d695_view(
        REPOSITORY_ROOT / "skills/l-01-agent-lifecycles/capsule-budgets-current.json"
    )
    sizes = {key: compiled_size(*key.split("/", 1)) for key in matrix_keys()}
    assert not current_d695_findings(record, sizes, detail_sizes(), digest_size(), REPOSITORY_ROOT)


@pytest.mark.parametrize(
    "area,key",
    [
        ("capsules", "curator/curation"),
        ("capsules", "architect/orientation"),
        ("step_details", "reference/knowledge-use.md"),
        ("step_details", "reference/task-document-authoring.md"),
        ("capsules", "worker/implementation"),
        ("step_details", "reference/knowledge-commands.md"),
        ("digest", "core/every-role.md"),
        ("step_details", "templates/file-card.md"),
        ("step_details", "templates/repo-overview.md"),
        ("step_details", "templates/route-local-overview.md"),
        ("step_details", "templates/repo-entity-catalog.md"),
    ],
)
def test_d694_rejects_later_growth_and_population_drift(area: str, key: str) -> None:
    record = load_current_budget_view(CURRENT_BUDGETS_PATH)
    actual = dict(record["current_measurements"][area])
    assert not _current_area_findings(record, area, actual)
    actual[key] += 1
    assert any(
        key in finding and "stale" in finding
        for finding in _current_area_findings(record, area, actual)
    )
    actual[key] = record["current_limits"][area][key] + 1
    assert any(
        key in finding and "exceed" in finding
        for finding in _current_area_findings(record, area, actual)
    )
    del actual[key]
    assert any(
        key in finding and "no current" in finding
        for finding in _current_area_findings(record, area, actual)
    )
    actual["unregistered"] = 1
    assert any(
        "unregistered" in finding and "missing" in finding
        for finding in _current_area_findings(record, area, actual)
    )


def test_d694_rejects_changed_authority_limit_or_detail_set(tmp_path: Path) -> None:
    record = load_current_budget_view(CURRENT_BUDGETS_PATH)
    for field in (
        "decision",
        "current_limits",
        "current_measurements",
        "current_detail_population",
        "current_linked_template_population",
    ):
        changed = copy.deepcopy(record)
        changed[field] = {}
        path = tmp_path / "current.json"
        path.write_text(json.dumps(changed))
        with pytest.raises(AssertionError, match="binding changed"):
            load_current_budget_view(path)


def test_d694_rejects_same_size_changed_source_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("one two\n")
    record = {
        "approved_source_sizes": {
            "source.md": {"sha256": sha256(source.read_bytes()).hexdigest(), "size_words": 2}
        },
        "compiler_input_revisions": {},
    }
    assert not _source_findings(record, tmp_path)
    source.write_text("two one\n")
    assert _source_findings(record, tmp_path) == ["source.md: stale source identity"]
    source.unlink()
    assert _source_findings(record, tmp_path) == ["source.md: missing approved source"]


def test_d694_current_curator_ceiling_and_real_population_guard() -> None:
    record = load_current_budget_view(CURRENT_BUDGETS_PATH)
    sizes = dict(record["current_measurements"]["capsules"])
    key = "curator/orientation"
    sizes[key] = 4177
    assert not any(
        key in finding and "ceiling" in finding
        for finding in _current_area_findings(record, "capsules", sizes)
    )
    sizes[key] = 4178
    assert any(
        key in finding and "ceiling" in finding
        for finding in _current_area_findings(record, "capsules", sizes)
    )
    details = {
        path: record["current_measurements"]["step_details"][path]
        for path in record["current_detail_population"]
    }
    for remove in (True, False):
        changed = dict(details)
        if remove:
            del changed["reference/curator-card-details.md"]
        else:
            changed["reference/unregistered.md"] = 1
        with pytest.raises(AssertionError, match="current detail population"):
            current_budget_findings(
                record, record["current_measurements"]["capsules"], changed, 1474, REPOSITORY_ROOT
            )


def test_d694_linked_templates_keep_source_budgets_without_becoming_details() -> None:
    record = load_current_budget_view(CURRENT_BUDGETS_PATH)
    linked = set(record["current_linked_template_population"])
    assert linked == {
        "templates/file-card.md",
        "templates/repo-overview.md",
        "templates/route-local-overview.md",
        "templates/repo-entity-catalog.md",
    }
    declared = set(record["current_detail_population"])
    assert len(declared) == 28
    assert declared.isdisjoint(linked)
    assert declared | linked == set(record["current_measurements"]["step_details"])
    view = current_budget_view(record)
    assert set(view["step_details"]) == declared
    assert len(record["current_limits"]["step_details"]) == 32
    assert set(record["current_limits"]["step_details"]) == declared | linked


@pytest.mark.parametrize("area", ["capsules", "step_details", "digest", "ordinary_templates"])
def test_current_measurements_reject_staleness_and_population_drift(area: str) -> None:
    record = load_d695_view(D695_CURRENT_BUDGETS_PATH)
    baseline = record["derived"]["current_measurements"][area]
    assert not current_area_findings(record, area, dict(baseline))
    for key in baseline:
        actual = dict(baseline)
        actual[key] += 1
        assert any(
            key in finding and "stale" in finding and COMMAND in finding
            for finding in current_area_findings(record, area, actual)
        )
        del actual[key]
        assert any(
            key in finding and "no current" in finding and COMMAND in finding
            for finding in current_area_findings(record, area, actual)
        )
    assert any(
        "missing measurement" in finding and COMMAND in finding
        for finding in current_area_findings(record, area, {**baseline, "unregistered": 1})
    )


def test_current_growth_within_a_ceiling_has_no_individual_budget() -> None:
    record = load_d695_view(D695_CURRENT_BUDGETS_PATH)
    assert "current_limits" not in record
    actual = {"worker/implementation": 1}
    record["derived"]["current_measurements"]["capsules"] = dict(actual)
    for size in (2, record["current_capsule_ceilings_words"]["every_other_role"]):
        actual["worker/implementation"] = size
        record["derived"]["current_measurements"]["capsules"] = dict(actual)
        assert not current_area_findings(record, "capsules", actual)


def test_current_ceiling_failures_name_size_ceiling_and_developer() -> None:
    record = load_d695_view(D695_CURRENT_BUDGETS_PATH)
    for role, ceiling in record["current_capsule_ceilings_words"].items():
        key = ("worker" if role == "every_other_role" else role) + "/orientation"
        for size in (ceiling, ceiling + 1):
            actual = {key: size}
            record["derived"]["current_measurements"]["capsules"] = dict(actual)
            findings = current_area_findings(record, "capsules", actual)
            if size == ceiling:
                assert not findings
            else:
                assert any(
                    key in finding
                    and str(size) in finding
                    and str(ceiling) in finding
                    and "developer's decision" in finding
                    for finding in findings
                )
    for area, key, ceiling in (
        ("step_details", "reference/example.md", record["current_step_ceiling_words"]),
        ("digest", "core/every-role.md", record["current_digest_ceiling_words"]),
    ):
        record["derived"]["current_measurements"][area] = {key: ceiling + 1}
        findings = current_area_findings(record, area, {key: ceiling + 1})
        assert any(
            key in finding
            and str(ceiling + 1) in finding
            and str(ceiling) in finding
            and "developer's decision" in finding
            for finding in findings
        )


def _current_source_fixture(root: Path) -> tuple[dict, dict[str, int]]:
    lifecycle = root / "skills/l-01-agent-lifecycles"
    declared = "reference/declared.md"
    ordinary = "templates/file-card.md"
    manifest = {
        "references": {
            "declared": {"source": declared, "step_detail": True},
            "ordinary": {"source": ordinary, "step_detail": False},
        },
        "derived": {
            "references": {
                "declared": {"roles": ["worker"], "step": "before work"},
                "ordinary": {"roles": ["worker"], "step": "before writing"},
            }
        },
    }
    content = {
        declared: b"one two\n",
        ordinary: b"one two\n",
        "composition-manifest.json": json.dumps(manifest).encode(),
    }
    inventory = {}
    revisions = {}
    for path, raw in content.items():
        target = lifecycle / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        inventory["skills/l-01-agent-lifecycles/" + path] = {
            "sha256": sha256(raw).hexdigest(),
            "size_words": len(raw.decode().split()),
        }
        revisions[path] = "sha256:" + sha256(raw).hexdigest()
    return {
        "current_step_ceiling_words": 2500,
        "derived": {
            "current_detail_population": [declared],
            "current_linked_template_population": [ordinary],
            "current_measurements": {
                "step_details": {declared: 2},
                "ordinary_templates": {ordinary: 2},
            },
            "approved_source_sizes": inventory,
            "compiler_input_revisions": revisions,
        },
    }, {declared: 2}


@pytest.mark.parametrize(
    "defect",
    [
        "missing-detail",
        "extra-detail",
        "missing-measurement",
        "extra-measurement",
        "overlap",
        "missing-linked",
        "extra-linked",
    ],
)
def test_current_classification_rejects_population_corruption(tmp_path: Path, defect: str) -> None:
    record, details = _current_source_fixture(tmp_path)
    assert not _current_source_findings(record, details, tmp_path)
    derived = record["derived"]
    ordinary = "templates/file-card.md"
    if defect == "missing-detail":
        details.clear()
    elif defect == "extra-detail":
        details["reference/unregistered.md"] = 1
    elif defect == "missing-measurement":
        del derived["current_measurements"]["ordinary_templates"][ordinary]
    elif defect == "extra-measurement":
        derived["current_measurements"]["ordinary_templates"]["templates/unregistered.md"] = 1
    elif defect == "overlap":
        derived["current_detail_population"].append(ordinary)
        details[ordinary] = 2
        derived["current_measurements"]["step_details"][ordinary] = 2
    elif defect == "missing-linked":
        derived["current_linked_template_population"].clear()
    else:
        derived["current_linked_template_population"].append("templates/unregistered.md")
    with pytest.raises(AssertionError, match=COMMAND):
        _current_source_findings(record, details, tmp_path)


@pytest.mark.parametrize("field", ["sha256", "size_words", "compiler_input_revisions"])
def test_current_source_staleness_names_the_renewal_command(tmp_path: Path, field: str) -> None:
    record, details = _current_source_fixture(tmp_path)
    path = "templates/file-card.md"
    if field == "compiler_input_revisions":
        record["derived"][field][path] = "sha256:" + "0" * 64
    else:
        row = record["derived"]["approved_source_sizes"]["skills/l-01-agent-lifecycles/" + path]
        row[field] = "0" * 64 if field == "sha256" else 3
    findings = _current_source_findings(record, details, tmp_path)
    assert findings and all(COMMAND in finding for finding in findings)


def test_same_size_source_edit_and_missing_source_are_detected(tmp_path: Path) -> None:
    record, _ = _current_source_fixture(tmp_path)
    path = tmp_path / "skills/l-01-agent-lifecycles/templates/file-card.md"
    path.write_text("two one\n")
    findings = current_source_findings(record, tmp_path)
    assert any("stale source identity" in finding and COMMAND in finding for finding in findings)
    path.unlink()
    assert any(
        "no current source" in finding and COMMAND in finding
        for finding in current_source_findings(record, tmp_path)
    )


def test_ordinary_templates_have_measurements_and_no_detail_ceiling(tmp_path: Path) -> None:
    record, details = _current_source_fixture(tmp_path)
    relative = "templates/file-card.md"
    path = tmp_path / "skills/l-01-agent-lifecycles" / relative
    raw = ("word " * 2501).encode()
    path.write_bytes(raw)
    derived = record["derived"]
    derived["current_measurements"]["ordinary_templates"][relative] = 2501
    derived["approved_source_sizes"]["skills/l-01-agent-lifecycles/" + relative] = {
        "sha256": sha256(raw).hexdigest(),
        "size_words": 2501,
    }
    derived["compiler_input_revisions"][relative] = "sha256:" + sha256(raw).hexdigest()
    assert not _current_source_findings(record, details, tmp_path)


@pytest.mark.parametrize(
    "defect", ["misclassified", "ordinary-flag-absent", "missing-hash", "missing-revision"]
)
def test_current_manifest_and_source_membership_stays_structural(
    tmp_path: Path, defect: str
) -> None:
    record, details = _current_source_fixture(tmp_path)
    ordinary = "templates/file-card.md"
    if defect in {"misclassified", "ordinary-flag-absent"}:
        manifest = tmp_path / "skills/l-01-agent-lifecycles/composition-manifest.json"
        document = json.loads(manifest.read_text())
        if defect == "misclassified":
            document["references"]["ordinary"]["step_detail"] = True
        else:
            del document["references"]["ordinary"]["step_detail"]
        manifest.write_text(json.dumps(document))
    elif defect == "missing-hash":
        del record["derived"]["approved_source_sizes"]["skills/l-01-agent-lifecycles/" + ordinary]
        findings = _current_source_findings(record, details, tmp_path)
        assert any(
            "missing source measurement" in finding and COMMAND in finding for finding in findings
        )
        return
    else:
        del record["derived"]["compiler_input_revisions"]["reference/declared.md"]
    with pytest.raises(AssertionError, match=COMMAND):
        _current_source_findings(record, details, tmp_path)
