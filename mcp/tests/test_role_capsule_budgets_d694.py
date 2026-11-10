"""Actual-current D694 budget layer and independent negative controls."""

import copy
import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest
from agents_remember_test_support.testing import capsule_budget_d695 as current_budget_module
from agents_remember_test_support.testing.capsule_budget_d694 import (
    _current_area_findings,
    _source_findings,
    current_budget_findings,
    current_budget_view,
    load_current_budget_view,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    _assert_record as d695_assert_record,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    _current_source_findings,
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


def test_d695_rejects_changed_record() -> None:
    record = load_d695_view(D695_CURRENT_BUDGETS_PATH)
    for field in record:
        changed = copy.deepcopy(record)
        changed[field] = None
        with pytest.raises(AssertionError, match="binding changed"):
            d695_assert_record(changed)


@pytest.mark.parametrize("area", ["capsules", "step_details", "digest"])
def test_d695_rejects_each_unmeasured_growth_and_population_change(area: str) -> None:
    record = load_d695_view(D695_CURRENT_BUDGETS_PATH)
    baseline = record["current_measurements"][area]
    assert not _current_area_findings(record, area, dict(baseline))
    for key in baseline:
        actual = dict(baseline)
        actual[key] += 1
        assert any(key in f and "stale" in f for f in _current_area_findings(record, area, actual))
        actual[key] = record["current_limits"][area][key] + 1
        assert any(key in f and "exceed" in f for f in _current_area_findings(record, area, actual))
        del actual[key]
        assert any(
            key in f and "no current" in f for f in _current_area_findings(record, area, actual)
        )
    assert any(
        "missing" in f
        for f in _current_area_findings(record, area, {**baseline, "unregistered": 1})
    )


def test_d695_authorized_boundaries_and_real_population() -> None:
    record = load_d695_view(D695_CURRENT_BUDGETS_PATH)
    assert record["current_step_ceiling_words"] == 2500
    assert "ordinary_templates" not in record["current_limits"]
    references = json.loads(
        (REPOSITORY_ROOT / "skills/l-01-agent-lifecycles/composition-manifest.json").read_text()
    )["references"]
    assert record["current_detail_population"] == sorted(
        entry["source"] for entry in references.values() if entry.get("step_detail") is True
    )
    assert "reference/curator/knowledge-ingest.md" in record["current_measurements"]["step_details"]
    sizes = dict(record["current_measurements"]["capsules"])
    details = dict(record["current_measurements"]["step_details"])
    digest = record["current_measurements"]["digest"]["core/every-role.md"]
    assert not current_d695_findings(record, sizes, details, digest, REPOSITORY_ROOT)
    for changed in (
        {
            key: size
            for key, size in details.items()
            if key != "reference/curator/knowledge-ingest.md"
        },
        {**details, "reference/unregistered.md": 1},
    ):
        with pytest.raises(AssertionError, match="population changed"):
            current_d695_findings(record, sizes, changed, digest, REPOSITORY_ROOT)
    over_digest = record["current_digest_ceiling_words"] + 1
    assert any(
        str(over_digest) in finding and "exceed" in finding
        for finding in current_d695_findings(record, sizes, details, over_digest, REPOSITORY_ROOT)
    )
    sizes["curator/curation"] = record["current_capsule_ceilings_words"]["curator"] + 1
    assert any(
        str(sizes["curator/curation"]) in finding and "exceed" in finding
        for finding in current_d695_findings(record, sizes, details, digest, REPOSITORY_ROOT)
    )


def _current_source_fixture(root: Path) -> tuple[dict, dict[str, int]]:
    """Synthetic predicate inputs only; never a replacement authenticated current record."""
    lifecycle = root / "skills/l-01-agent-lifecycles"
    ordinary = [
        "templates/file-card.md",
        "templates/repo-entity-catalog.md",
        "templates/repo-overview.md",
        "templates/route-local-overview.md",
    ]
    declared = "reference/declared.md"
    references = {"declared": {"source": declared, "step_detail": True}}
    for index, path in enumerate(ordinary):
        references[f"ordinary-{index}"] = {"source": path, "step_detail": False}
    record = {
        "current_detail_population": [declared],
        "current_linked_template_population": ordinary,
        "current_measurements": {"step_details": {declared: 2500}, "ordinary_templates": {}},
        "current_limits": {"step_details": {declared: 2500}},
        "current_step_ceiling_words": 2500,
        "approved_source_sizes": {},
        "compiler_input_revisions": {},
    }
    for index, path in enumerate([declared, *ordinary]):
        source = lifecycle / path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            ("declared " * 2500)
            if path == declared
            else ("ordinary " * (2501 if index == 1 else 1))
        )
        raw = source.read_bytes()
        count = len(raw.decode().split())
        record["approved_source_sizes"]["skills/l-01-agent-lifecycles/" + path] = {
            "sha256": sha256(raw).hexdigest(),
            "size_words": count,
        }
        record["compiler_input_revisions"][path] = "sha256:" + sha256(raw).hexdigest()
        if path != declared:
            record["current_measurements"]["ordinary_templates"][path] = count
    (lifecycle / "composition-manifest.json").write_text(json.dumps({"references": references}))
    return record, {declared: 2500}


def test_d695_ordinary_template_above_2500_has_no_size_limit(tmp_path: Path) -> None:
    record, details = _current_source_fixture(tmp_path)
    assert record["current_measurements"]["ordinary_templates"]["templates/file-card.md"] == 2501
    assert "ordinary_templates" not in record["current_limits"]
    assert not _current_source_findings(record, details, tmp_path)


def test_d695_declared_detail_2501_still_fails(tmp_path: Path) -> None:
    record, details = _current_source_fixture(tmp_path)
    path = "reference/declared.md"
    source = tmp_path / "skills/l-01-agent-lifecycles" / path
    source.write_text("declared " * 2501)
    raw = source.read_bytes()
    record["current_measurements"]["step_details"][path] = 2501
    record["current_limits"]["step_details"][path] = 2501
    record["approved_source_sizes"]["skills/l-01-agent-lifecycles/" + path] = {
        "sha256": sha256(raw).hexdigest(),
        "size_words": 2501,
    }
    record["compiler_input_revisions"][path] = "sha256:" + sha256(raw).hexdigest()
    details[path] = 2501
    assert any(
        path in finding and "ceiling 2500" in finding
        for finding in _current_source_findings(record, details, tmp_path)
    )


@pytest.mark.parametrize(
    "defect",
    [
        "measurement",
        "same-size-content",
        "hash",
        "revision",
        "missing-file",
        "extra-approved-source",
    ],
)
def test_d695_ordinary_source_drift_is_still_rejected(tmp_path: Path, defect: str) -> None:
    record, details = _current_source_fixture(tmp_path)
    assert not _current_source_findings(record, details, tmp_path)
    path = "templates/file-card.md"
    source = tmp_path / "skills/l-01-agent-lifecycles" / path
    if defect == "measurement":
        record["current_measurements"]["ordinary_templates"][path] += 1
        expected = "stale recorded size"
    elif defect == "same-size-content":
        source.write_text("different " * 2501)
        expected = "stale source identity"
    elif defect == "hash":
        record["approved_source_sizes"]["skills/l-01-agent-lifecycles/" + path]["sha256"] = "0" * 64
        expected = "stale source identity"
    elif defect == "revision":
        record["compiler_input_revisions"][path] = "sha256:" + "0" * 64
        expected = "compiler input revision changed"
    elif defect == "missing-file":
        source.unlink()
        expected = "missing ordinary source"
    else:
        record["approved_source_sizes"]["unregistered.md"] = {"sha256": "0" * 64, "size_words": 1}
        expected = "missing approved source"
    assert any(
        expected in finding for finding in _current_source_findings(record, details, tmp_path)
    )


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
        "ordinary-limit",
    ],
)
def test_d695_current_classification_rejects_population_corruption(
    tmp_path: Path, defect: str
) -> None:
    record, details = _current_source_fixture(tmp_path)
    assert not _current_source_findings(record, details, tmp_path)
    ordinary = "templates/file-card.md"
    if defect == "missing-detail":
        details.clear()
    elif defect == "extra-detail":
        details["reference/unregistered.md"] = 1
    elif defect == "missing-measurement":
        del record["current_measurements"]["ordinary_templates"][ordinary]
    elif defect == "extra-measurement":
        record["current_measurements"]["ordinary_templates"]["templates/unregistered.md"] = 1
    elif defect == "overlap":
        record["current_detail_population"] = sorted([*details, ordinary])
        details[ordinary] = 2501
        record["current_measurements"]["step_details"][ordinary] = 2501
        record["current_limits"]["step_details"][ordinary] = 2501
    elif defect == "missing-linked":
        record["current_linked_template_population"].remove(ordinary)
    elif defect == "extra-linked":
        record["current_linked_template_population"].append("templates/unregistered.md")
    elif defect == "ordinary-limit":
        record["current_limits"]["ordinary_templates"] = {ordinary: 2500}
    with pytest.raises(AssertionError):
        _current_source_findings(record, details, tmp_path)


@pytest.mark.parametrize("defect", ["misclassified", "ordinary-flag-absent"])
def test_d695_current_classification_rejects_manifest_corruption(
    tmp_path: Path, defect: str
) -> None:
    record, details = _current_source_fixture(tmp_path)
    assert not _current_source_findings(record, details, tmp_path)
    if defect == "misclassified":
        manifest = tmp_path / "skills/l-01-agent-lifecycles/composition-manifest.json"
        document = json.loads(manifest.read_text())
        document["references"]["ordinary-0"]["step_detail"] = True
        manifest.write_text(json.dumps(document))
    elif defect == "ordinary-flag-absent":
        manifest = tmp_path / "skills/l-01-agent-lifecycles/composition-manifest.json"
        document = json.loads(manifest.read_text())
        del document["references"]["ordinary-0"]["step_detail"]
        manifest.write_text(json.dumps(document))
    with pytest.raises(AssertionError):
        _current_source_findings(record, details, tmp_path)


@pytest.mark.parametrize("defect", ["missing-hash", "missing-revision"])
def test_d695_current_classification_rejects_source_inventory_corruption(
    tmp_path: Path, defect: str
) -> None:
    record, details = _current_source_fixture(tmp_path)
    assert not _current_source_findings(record, details, tmp_path)
    ordinary = "templates/file-card.md"
    if defect == "missing-hash":
        del record["approved_source_sizes"]["skills/l-01-agent-lifecycles/" + ordinary]
    elif defect == "missing-revision":
        del record["compiler_input_revisions"][ordinary]
    with pytest.raises(AssertionError):
        _current_source_findings(record, details, tmp_path)


def test_d695_whole_path_authentication_precedes_source_checks(tmp_path: Path) -> None:
    record = load_d695_view(D695_CURRENT_BUDGETS_PATH)
    changed = copy.deepcopy(record)
    key = changed["current_linked_template_population"][0]
    changed["current_measurements"]["ordinary_templates"][key] += 1
    with pytest.raises(AssertionError, match="binding changed"):
        current_d695_findings(changed, {}, {}, 0, tmp_path)


def test_d695_whole_path_authenticates_uncapped_ordinary_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, details = _current_source_fixture(tmp_path)
    record["current_measurements"].update(capsules={}, digest={"core/every-role.md": 0})
    record["current_limits"].update(capsules={}, digest={"core/every-role.md": 0})
    record["decision"] = {}
    for path, key in (
        ("skills/l-01-agent-lifecycles/capsule-budgets.json", "historicalRegistrySha256"),
        (
            "mcp/test_support/agents_remember_test_support/testing/capsule_budget_snapshot.py",
            "historicalSnapshotSha256",
        ),
        ("skills/l-01-agent-lifecycles/capsule-budgets-d694.json", "predecessorD694Sha256"),
        (
            "mcp/test_support/agents_remember_test_support/testing/capsule_budget_d694.py",
            "predecessorD694HelperSha256",
        ),
        ("skills/l-01-agent-lifecycles/core/digest-map.json", "currentDigestMapSha256"),
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY_ROOT / path, target)
        record["decision"][key] = sha256(target.read_bytes()).hexdigest()
    digest_map = tmp_path / "skills/l-01-agent-lifecycles/core/digest-map.json"
    record["current_digest_ceiling_words"] = json.loads(digest_map.read_text())["digest"][
        "maximumWords"
    ]
    # Admit exactly this synthetic fixture record; keep the real authentication function active.
    fixture_fingerprint = sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    monkeypatch.setattr(current_budget_module, "_RECORD_FINGERPRINT", fixture_fingerprint)
    assert record["current_measurements"]["ordinary_templates"]["templates/file-card.md"] == 2501
    assert not current_d695_findings(record, {}, details, 0, tmp_path)
    changed = copy.deepcopy(record)
    changed["current_measurements"]["ordinary_templates"]["templates/file-card.md"] += 1
    with pytest.raises(AssertionError, match="binding changed"):
        current_d695_findings(changed, {}, details, 0, tmp_path)
    ordinary = tmp_path / "skills/l-01-agent-lifecycles/templates/file-card.md"
    ordinary.write_text("different " * 2501)
    assert any(
        "stale source identity" in finding
        for finding in current_d695_findings(record, {}, details, 0, tmp_path)
    )
