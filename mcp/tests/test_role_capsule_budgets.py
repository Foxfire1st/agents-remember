"""Role-capsule and separately delivered step-file budgets (MIK-R101/R125).

The matrix includes every role/operation and launcher capsule. Capsule size is the
whitespace-split word count of render_instructions(), excluding task data and seals.
Each declared step file is measured from its complete source text. The registry is
skills/l-01-agent-lifecycles/capsule-budgets.json. Budgets are ceil(size * 11 / 10),
capped at 3700 for capsules and 2500 for step files; neither size nor budget may
exceed its ceiling. Missing and orphan entries fail. Growth remains a recorded
decision; the seed control invokes this same guard rather than duplicating it.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.sources import CapsuleAdmissionRequest
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleLauncherSeat,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_ROOT = REPOSITORY_ROOT / "skills" / "l-01-agent-lifecycles"
BUDGETS_PATH = LIFECYCLE_ROOT / "capsule-budgets.json"
MANIFEST_RELATIVE = "composition-manifest.json"


def matrix_keys() -> list[str]:
    """Every capsule key the landed source yields, each under role and operation."""
    parsed = parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())
    keys = [
        f"{role}/{operation}"
        for role in parsed.role_order
        for operation in parsed.operations
        if role in parsed.operations[operation].applies_to_roles
    ]
    keys.extend(f"launcher/{operation}" for operation in parsed.launcher.operations)
    return keys


def compiled_size(role: str, operation: str) -> int:
    """Whitespace-split words of the compiled instruction text, seals excluded."""
    manifest_bytes = (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes()
    parsed = parse_composition_manifest(manifest_bytes)
    if role == "launcher":
        seat: CapsuleRoleSeat | CapsuleLauncherSeat = CapsuleLauncherSeat(
            routing_condition=parsed.launcher.routing_condition
        )
        request = CapsuleAdmissionRequest(
            root=LIFECYCLE_ROOT,
            manifest=MANIFEST_RELATIVE,
            core=tuple(parsed.core[name].source for name in parsed.launcher.core),
            operation=(parsed.operations[operation].source,),
            skill=tuple(entry.source for entry in parsed.skills.values()),
        )
    else:
        seat = CapsuleRoleSeat(role=role, altitude=parsed.roles[role].altitude)  # type: ignore[arg-type]
        request = routed_admission_for(
            LIFECYCLE_ROOT,
            MANIFEST_RELATIVE,
            manifest_bytes,
            CapsuleSeatAddress(role=role, operation=operation),  # type: ignore[arg-type]
        )
    binding = CapsuleBinding(
        operation=operation,  # type: ignore[arg-type]
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=seat,
            repository_id="agents-remember",
            work_branch="fixture",
            tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
        ),
    )
    outcome = compile_admitted_capsule(binding, request)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    return len(outcome.result.render_instructions().split())


def detail_sizes() -> dict[str, int]:
    """Every manifest-declared step file gets a budget, even before its first launch."""
    parsed = parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())
    return {
        entry.source: len((LIFECYCLE_ROOT / entry.source).read_text(encoding="utf-8").split())
        for entry in parsed.detail_references
    }


def _budget_findings(sizes: dict[str, int], entries: dict, ceiling: int) -> list[str]:
    findings = [f"{key}: missing budget" for key in sizes.keys() - entries.keys()]
    findings.extend(
        f"{key}: budget names no current file or capsule" for key in entries.keys() - sizes.keys()
    )
    for key in sizes.keys() & entries.keys():
        size, entry = sizes[key], entries[key]
        budget = entry["budget_words"]
        if size > ceiling or entry["size_words"] > ceiling or budget > ceiling:
            findings.append(
                f"{key}: size {size}, recorded size {entry['size_words']}, "
                f"budget {budget}; ceiling {ceiling}"
            )
        if budget != min(math.ceil(entry["size_words"] * 11 / 10), ceiling):
            findings.append(f"{key}: budget does not follow the capped eleven-tenths rule")
        if size > budget:
            findings.append(f"{key}: {size} words exceed recorded budget {budget}")
    return findings


def test_every_yielded_capsule_has_a_budget_and_no_other_key_does() -> None:
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    assert budgets["schema"] == "ar-role-capsule-budgets/v1"
    assert sorted(budgets["capsules"]) == sorted(matrix_keys())
    assert sorted(budgets.get("step_details", {})) == sorted(detail_sizes())


def test_no_capsule_exceeds_its_recorded_budget() -> None:
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    sizes = {key: compiled_size(*key.split("/", 1)) for key in matrix_keys()}
    findings = _budget_findings(sizes, budgets["capsules"], 3700)
    findings += _budget_findings(detail_sizes(), budgets.get("step_details", {}), 2500)
    assert not findings, "\n".join(findings)


def test_budget_guard_rejects_growth_ceiling_and_unregistered_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutate the real guard's inputs, not a second implementation of its rules."""
    module = sys.modules[__name__]
    path = tmp_path / "budgets.json"
    monkeypatch.setattr(module, "BUDGETS_PATH", path)
    monkeypatch.setattr(module, "matrix_keys", lambda: ["worker/implementation"])
    observed = {"capsule": 100, "detail": 10}
    monkeypatch.setattr(module, "compiled_size", lambda *_: observed["capsule"])
    monkeypatch.setattr(
        module, "detail_sizes", lambda: {"reference/example.md": observed["detail"]}, raising=False
    )
    capsule = {"size_words": 100, "budget_words": 110}
    detail = {"size_words": 10, "budget_words": 11}

    def write(capsule_entry: dict, detail_entries: dict) -> None:
        path.write_text(
            json.dumps(
                {
                    "capsules": {"worker/implementation": capsule_entry},
                    "step_details": detail_entries,
                }
            )
        )

    write(capsule, {"reference/example.md": detail})
    test_no_capsule_exceeds_its_recorded_budget()
    for count, entry, details, expected in (
        (111, capsule, {"reference/example.md": detail}, "exceed"),
        (
            3710,
            {"size_words": 3710, "budget_words": 4081},
            {"reference/example.md": detail},
            "ceiling",
        ),
        (
            100,
            {"size_words": 3701, "budget_words": 3700},
            {"reference/example.md": detail},
            "ceiling",
        ),
        (100, capsule, {}, "missing"),
        (
            100,
            capsule,
            {"reference/example.md": {"size_words": 2501, "budget_words": 2752}},
            "ceiling",
        ),
    ):
        observed["capsule"] = count
        write(entry, details)
        with pytest.raises(AssertionError, match=expected):
            test_no_capsule_exceeds_its_recorded_budget()
