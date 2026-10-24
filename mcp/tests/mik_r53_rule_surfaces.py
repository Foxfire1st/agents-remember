"""Where the ten merge/debt rules are stated on a skill surface (MIK-R53).

The command help and the shipped skill text are the two surfaces that list the ten rules with
their decision. The curated rule listing moved out of the retired c-02 skill into the c-05
onboarding workflow (MIK-R196 rule 6), so the skill surface the case reads is named once here
rather than spelled out inside the test.
"""

from __future__ import annotations

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
QUALITY_CONTROL_WORKFLOW = (
    REPOSITORY_ROOT
    / "skills"
    / "c-05-create-or-update-onboarding-files"
    / "workflows"
    / "memory-quality-control.md"
)


def quality_control_workflow_text() -> str:
    """The shipped workflow that lists the ten rules with their decision."""

    return QUALITY_CONTROL_WORKFLOW.read_text(encoding="utf-8")
