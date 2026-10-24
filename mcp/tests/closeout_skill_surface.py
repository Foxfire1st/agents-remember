"""The closeout text a landing publishes, canonical and packaged (MIK-R152 rule 10).

A landing publishes nothing without the check, and the text that says so lives in the closeout
workflow of the w-02 light-task skill, with a generated copy under the packaged runtime skills
that the capsule compiler reads. The cases that assert the corrected wording read both copies
from here, so the skill tree is named once and a rename of that tree is one edit.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "agents_remember"
CHECKOUT = Path(__file__).resolve().parent.parent.parent
SKILL = Path("skills") / "w-02-light-task-workflow" / "workflows" / "closeout.md"
PACKAGED = Path("package_data") / "runtime" / "skills" / "w-02-light-task-workflow" / "workflows"
PACKAGED_SKILL = PACKAGED / "closeout.md"


def closeout_texts() -> tuple[str, str]:
    """The canonical closeout text and the packaged copy the compiler reads."""

    return (
        (CHECKOUT / SKILL).read_text(encoding="utf-8"),
        (PACKAGE / PACKAGED_SKILL).read_text(encoding="utf-8"),
    )
