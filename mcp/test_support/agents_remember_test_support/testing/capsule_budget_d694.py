"""D694 current layer; frozen D681/D682 registry and predicates remain separate."""

import json
from hashlib import sha256
from pathlib import Path

_D694_RECORD_FINGERPRINT = "d187469cd3de6cef6e0efa80072078ff638ead8a194f47934909fd0ef4652b86"


def _assert_record(record: dict) -> None:
    fingerprint = sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
    assert fingerprint.hexdigest() == _D694_RECORD_FINGERPRINT, (
        "D694 decision/measurement binding changed"
    )


def load_current_budget_view(path: Path) -> dict:
    record = json.loads(path.read_text(encoding="utf-8"))
    _assert_record(record)
    return record


def current_budget_view(record: dict) -> dict:
    """Explicit current input view for live keyset checks, never a historical projection."""
    _assert_record(record)
    result: dict = {"schema": "ar-role-capsule-budgets/v1"}
    for area in ("capsules", "step_details", "digest"):
        entries = {
            key: {"size_words": size, "budget_words": record["current_limits"][area][key]}
            for key, size in record["current_measurements"][area].items()
            if area != "step_details" or key in record["current_detail_population"]
        }
        result[area] = entries["core/every-role.md"] if area == "digest" else entries
    return result


def current_capsule_ceiling(key: str, record: dict) -> int:
    role = key.split("/", 1)[0]
    ceilings = record["current_capsule_ceilings_words"]
    return ceilings[role] if role in ceilings else ceilings["every_other_role"]


def _current_area_findings(record: dict, area: str, actual: dict[str, int]) -> list[str]:
    measured = record["current_measurements"][area]
    limits = record["current_limits"][area]
    findings = [f"{area}/{key}: missing budget" for key in actual.keys() - measured.keys()]
    findings += [
        f"{area}/{key}: budget names no current source" for key in measured.keys() - actual.keys()
    ]
    for key in actual.keys() & measured.keys():
        size = actual[key]
        ceiling = (
            current_capsule_ceiling(key, record)
            if area == "capsules"
            else record["current_step_ceiling_words"]
            if area == "step_details"
            else record["current_digest_ceiling_words"]
        )
        if size != measured[key]:
            findings.append(f"{area}/{key}: stale recorded size {measured[key]}; actual {size}")
        if size > limits[key]:
            findings.append(f"{area}/{key}: {size} words exceed budget {limits[key]}")
        if size > ceiling or limits[key] > ceiling:
            findings.append(f"{area}/{key}: ceiling {ceiling} exceeded")
    return findings


def _source_findings(record: dict, root: Path) -> list[str]:
    findings = []
    for path, expected in record["approved_source_sizes"].items():
        source = root / path
        if not source.is_file():
            findings.append(f"{path}: missing approved source")
            continue
        raw = source.read_bytes()
        if sha256(raw).hexdigest() != expected["sha256"]:
            findings.append(f"{path}: stale source identity")
        if len(raw.decode("utf-8").split()) != expected["size_words"]:
            findings.append(f"{path}: approved source size changed")
    for path, revision in record["compiler_input_revisions"].items():
        source = root / "skills/l-01-agent-lifecycles" / path
        if not source.is_file() or "sha256:" + sha256(source.read_bytes()).hexdigest() != revision:
            findings.append(f"{path}: compiler input revision changed")
    return findings


def current_budget_findings(
    record: dict, sizes: dict[str, int], details: dict[str, int], digest: int, root: Path
) -> list[str]:
    _assert_record(record)
    assert sorted(details) == record["current_detail_population"], (
        "D694 current detail population changed"
    )
    linked = record["current_linked_template_population"]
    assert set(details).isdisjoint(linked), "D694 detail/template populations overlap"
    assert sorted([*details, *linked]) == sorted(record["current_measurements"]["step_details"]), (
        "D694 source budget population changed"
    )
    source_sizes = {
        **details,
        **{
            path: len(
                (root / "skills/l-01-agent-lifecycles" / path).read_text(encoding="utf-8").split()
            )
            for path in linked
        },
    }
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
    findings = _current_area_findings(record, "capsules", sizes)
    findings += _current_area_findings(record, "step_details", source_sizes)
    findings += _current_area_findings(record, "digest", {"core/every-role.md": digest})
    return findings + _source_findings(record, root)
