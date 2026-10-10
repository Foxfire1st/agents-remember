"""Role-capsule budgets (MIK-R101 rule 6).

Every compiled capsule gets a budget. The capsules are the whole matrix the source
yields: every role with every operation it has, and the launcher's, each under its
role and operation. A capsule's size is the whitespace-split word count of its
compiled instruction text (`render_instructions()` — task data and seals excluded).
Its budget is that number after the sweep, times eleven, divided by ten, rounded up.

The registry is `skills/l-01-agent-lifecycles/capsule-budgets.json`, recorded in the
same change as the texts it measures. These cases fail when a capsule exceeds its
budget, when a capsule has no budget (a new role or operation without a recorded
decision), and when the registry names a capsule the manifest no longer yields.
Growth, a new role and a new operation are each a decision recorded in the same
change as the raised or added budget.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

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


def test_every_yielded_capsule_has_a_budget_and_no_other_key_does() -> None:
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    assert budgets["schema"] == "ar-role-capsule-budgets/v1"
    assert sorted(budgets["capsules"]) == sorted(matrix_keys())


def test_no_capsule_exceeds_its_recorded_budget() -> None:
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))["capsules"]
    for key in matrix_keys():
        role, operation = key.split("/", 1)
        size = compiled_size(role, operation)
        entry = budgets[key]
        assert entry["budget_words"] == math.ceil(entry["size_words"] * 11 / 10)
        assert size <= entry["budget_words"], (
            f"{key}: {size} words exceed the recorded budget of {entry['budget_words']}; "
            "growth is a decision recorded in the same change as the raised budget"
        )
