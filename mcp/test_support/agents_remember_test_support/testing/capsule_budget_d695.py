"""Check renewed instruction measurements against independently compiled sizes and policy."""

import json
from hashlib import sha256
from pathlib import Path

from agents_remember.models.role_capsules.manifest_derived import compose_manifest

from agents_remember_test_support.testing.instruction_renewal import RENEW_COMMAND

COMMAND = RENEW_COMMAND
_REGISTRIES = {"capsule-budgets.json", "capsule-budgets-d694.json", "capsule-budgets-current.json"}


def load_current_budget_view(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def current_budget_view(record: dict) -> dict:
    """Expose measured membership without reinstating individual word budgets."""
    measured = record["derived"]["current_measurements"]
    _assert_current_populations(record, measured["step_details"])
    result: dict = {"schema": "ar-role-capsule-budgets/v1"}
    for area in ("capsules", "step_details", "digest"):
        entries = {key: {"size_words": size} for key, size in measured[area].items()}
        result[area] = entries["core/every-role.md"] if area == "digest" else entries
    return result


def _assert_current_populations(record: dict, details: dict[str, int]) -> None:
    derived = record["derived"]
    declared = derived["current_detail_population"]
    linked = derived["current_linked_template_population"]
    assert sorted(details) == declared, f"current detail population changed; run {COMMAND}"
    assert len(declared) == len(set(declared)), f"duplicate declared detail; run {COMMAND}"
    assert declared == sorted(derived["current_measurements"]["step_details"]), (
        f"measured detail population changed; run {COMMAND}"
    )
    assert set(declared).isdisjoint(linked), f"detail/template populations overlap; run {COMMAND}"
    assert sorted(derived["current_measurements"]["ordinary_templates"]) == linked, (
        f"ordinary measurement population changed; run {COMMAND}"
    )


def _current_area_findings(record: dict, area: str, actual: dict[str, int]) -> list[str]:
    measured = record["derived"]["current_measurements"][area]
    findings = [
        f"{area}/{key}: missing measurement; run {COMMAND}"
        for key in actual.keys() - measured.keys()
    ]
    findings += [
        f"{area}/{key}: measurement names no current source; run {COMMAND}"
        for key in measured.keys() - actual.keys()
    ]
    for key in actual.keys() & measured.keys():
        size = actual[key]
        if size != measured[key]:
            findings.append(
                f"{area}/{key}: stale recorded size {measured[key]}; actual {size}; run {COMMAND}"
            )
    return findings


def _source_findings(record: dict, root: Path) -> list[str]:
    derived = record["derived"]
    inventory = derived["approved_source_sizes"]
    lifecycle = root / "skills/l-01-agent-lifecycles"
    actual = {
        path.relative_to(root).as_posix()
        for path in lifecycle.rglob("*")
        if path.is_file() and path.suffix in {".md", ".json"} and path.name not in _REGISTRIES
    }
    findings = [
        f"{path}: missing source measurement; run {COMMAND}" for path in actual - inventory.keys()
    ]
    findings += [
        f"{path}: measurement names no current source; run {COMMAND}"
        for path in inventory.keys() - actual
    ]
    for path in actual & inventory.keys():
        raw = (root / path).read_bytes()
        expected = inventory[path]
        if sha256(raw).hexdigest() != expected["sha256"]:
            findings.append(f"{path}: stale source identity; run {COMMAND}")
        if len(raw.decode("utf-8").split()) != expected["size_words"]:
            findings.append(f"{path}: stale source size; run {COMMAND}")
    for path, revision in derived["compiler_input_revisions"].items():
        source = lifecycle / path
        if not source.is_file() or "sha256:" + sha256(source.read_bytes()).hexdigest() != revision:
            findings.append(f"{path}: stale compiler input revision; run {COMMAND}")
    return findings


def _current_source_findings(record: dict, details: dict[str, int], root: Path) -> list[str]:
    _assert_current_populations(record, details)
    derived = record["derived"]
    lifecycle = root / "skills/l-01-agent-lifecycles"
    references = compose_manifest(
        json.loads((lifecycle / "composition-manifest.json").read_text())
    )["references"]
    declared = {
        entry["source"] for entry in references.values() if entry.get("step_detail") is True
    }
    linked = {
        entry["source"]
        for entry in references.values()
        if entry.get("step_detail") is False
        and entry["source"].startswith("templates/")
        and entry["source"].endswith(".md")
    }
    assert sorted(declared) == derived["current_detail_population"], (
        f"manifest detail population changed; run {COMMAND}"
    )
    assert sorted(linked) == derived["current_linked_template_population"], (
        f"ordinary template population changed; run {COMMAND}"
    )
    assert declared <= derived["compiler_input_revisions"].keys(), (
        f"compiler source inventory incomplete; run {COMMAND}"
    )
    ordinary = {path: len((lifecycle / path).read_text().split()) for path in linked}
    return (
        _current_area_findings(record, "step_details", details)
        + _current_area_findings(record, "ordinary_templates", ordinary)
        + _source_findings(record, root)
    )


def _assert_history(record: dict, root: Path) -> None:
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
    ):
        assert sha256((root / path).read_bytes()).hexdigest() == record["decision"][key], (
            "frozen budget history changed"
        )


def current_budget_findings(
    record: dict, sizes: dict[str, int], details: dict[str, int], digest: int, root: Path
) -> list[str]:
    _assert_history(record, root)
    return (
        _current_source_findings(record, details, root)
        + _current_area_findings(record, "capsules", sizes)
        + _current_area_findings(record, "digest", {"core/every-role.md": digest})
    )
