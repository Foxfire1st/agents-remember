"""D695/D696 actual-current binding; frozen D694 comparisons remain unchanged."""

import json
from hashlib import sha256
from pathlib import Path

from agents_remember_test_support.testing.capsule_budget_d694 import (
    _current_area_findings,
    _source_findings,
    current_capsule_ceiling,
)

__all__ = ["current_capsule_ceiling"]

_RECORD_FINGERPRINT = "78acb88dfedaea04204c2af98a877946b42ea3a2fbb2b5f2d04c5803477794da"

_ORDINARY_TEMPLATE_POPULATION = (
    "templates/file-card.md",
    "templates/repo-entity-catalog.md",
    "templates/repo-overview.md",
    "templates/route-local-overview.md",
)


def _assert_record(record: dict) -> None:
    fingerprint = sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
    assert fingerprint.hexdigest() == _RECORD_FINGERPRINT, (
        "D695 decision/measurement binding changed"
    )


def load_current_budget_view(path: Path) -> dict:
    record = json.loads(path.read_text(encoding="utf-8"))
    _assert_record(record)
    return record


def current_budget_view(record: dict) -> dict:
    """Explicit current input view for live keyset checks, never a historical projection."""
    _assert_record(record)
    _assert_current_populations(record, record["current_measurements"]["step_details"])
    result: dict = {"schema": "ar-role-capsule-budgets/v1"}
    for area in ("capsules", "step_details", "digest"):
        entries = {
            key: {"size_words": size, "budget_words": record["current_limits"][area][key]}
            for key, size in record["current_measurements"][area].items()
        }
        result[area] = entries["core/every-role.md"] if area == "digest" else entries
    return result


def _assert_current_populations(record: dict, details: dict[str, int]) -> None:
    declared = record["current_detail_population"]
    linked = record["current_linked_template_population"]
    assert sorted(details) == declared, "D695 current detail population changed"
    assert len(declared) == len(set(declared)), "D695 duplicate declared detail"
    assert linked == list(_ORDINARY_TEMPLATE_POPULATION), (
        "D695 ordinary template population changed"
    )
    assert set(declared).isdisjoint(linked), "D695 detail/template populations overlap"
    assert sorted(record["current_measurements"]["step_details"]) == declared, (
        "D695 measured detail population changed"
    )
    assert sorted(record["current_limits"]["step_details"]) == declared, (
        "D695 detail limit population changed"
    )
    assert sorted(record["current_measurements"]["ordinary_templates"]) == linked, (
        "D695 ordinary measurement population changed"
    )
    assert "ordinary_templates" not in record["current_limits"], (
        "D695 ordinary templates have no word limits"
    )
    assert record["current_step_ceiling_words"] == 2500, "D695 declared detail ceiling must be 2500"


def _current_source_findings(record: dict, details: dict[str, int], root: Path) -> list[str]:
    """Validate current classification and source drift without capping ordinary templates."""
    _assert_current_populations(record, details)
    linked = record["current_linked_template_population"]
    lifecycle = root / "skills/l-01-agent-lifecycles"
    references = json.loads((lifecycle / "composition-manifest.json").read_text(encoding="utf-8"))[
        "references"
    ]
    declared = {
        entry["source"] for entry in references.values() if entry.get("step_detail") is True
    }
    ordinary = {
        entry["source"] for entry in references.values() if entry.get("step_detail") is False
    }
    assert sorted(declared) == record["current_detail_population"], (
        "D695 manifest detail population changed"
    )
    assert set(linked) <= ordinary, "D695 linked template misclassified"
    assert set(linked) <= record["compiler_input_revisions"].keys(), (
        "D695 ordinary source revisions incomplete"
    )
    assert {"skills/l-01-agent-lifecycles/" + path for path in linked} <= record[
        "approved_source_sizes"
    ].keys(), "D695 ordinary source identity inventory incomplete"
    findings = _current_area_findings(record, "step_details", details)
    for path in linked:
        source = lifecycle / path
        if not source.is_file():
            findings.append(f"ordinary_templates/{path}: missing ordinary source")
            continue
        actual = len(source.read_text(encoding="utf-8").split())
        measured = record["current_measurements"]["ordinary_templates"][path]
        if actual != measured:
            findings.append(
                f"ordinary_templates/{path}: stale recorded size {measured}; actual {actual}"
            )
    return findings + _source_findings(record, root)


def current_budget_findings(
    record: dict, sizes: dict[str, int], details: dict[str, int], digest: int, root: Path
) -> list[str]:
    _assert_record(record)
    source_findings = _current_source_findings(record, details, root)
    for path, key in (
        ("skills/l-01-agent-lifecycles/capsule-budgets.json", "historicalRegistrySha256"),
        (
            "mcp/test_support/agents_remember_test_support/testing/capsule_budget_snapshot.py",
            "historicalSnapshotSha256",
        ),
    ):
        assert sha256((root / path).read_bytes()).hexdigest() == record["decision"][key], (
            "D681/D682 frozen owner changed"
        )
    assert (
        sha256(
            (root / "skills/l-01-agent-lifecycles/capsule-budgets-d694.json").read_bytes()
        ).hexdigest()
        == record["decision"]["predecessorD694Sha256"]
    ), "D694 frozen owner changed"
    assert (
        sha256(
            (
                root
                / "mcp/test_support/agents_remember_test_support/testing/capsule_budget_d694.py"
            ).read_bytes()
        ).hexdigest()
        == record["decision"]["predecessorD694HelperSha256"]
    ), "D694 frozen checker changed"
    digest_map = root / "skills/l-01-agent-lifecycles/core/digest-map.json"
    assert (
        sha256(digest_map.read_bytes()).hexdigest() == record["decision"]["currentDigestMapSha256"]
    ), "D695 current digest map changed"
    assert (
        json.loads(digest_map.read_text(encoding="utf-8"))["digest"]["maximumWords"]
        == record["current_digest_ceiling_words"]
    ), "D695 digest maximum changed"
    findings = _current_area_findings(record, "capsules", sizes)
    findings += _current_area_findings(record, "digest", {"core/every-role.md": digest})
    return findings + source_findings
