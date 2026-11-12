"""The surfaces a retired curation sentence shipped, named once for the guard-tooth cases.

The registry in ``agents_remember_test_support.testing.curation_doctrine`` records, per retired
sentence, the surfaces that shipped it. The guard is a census of surfaces, not a single-file
check, so a case that proves it needs three things from the registry: one retired sentence, a
surface it shipped on, and a surface that never carried it.

The undeclared surface is a surviving lifecycle criterion that never carried the sentence;
the declared surface retains the exact registered fragment and its source scope.
"""

from __future__ import annotations

from agents_remember_test_support.testing.curation_doctrine import RETIRED_CURATION_STATEMENTS

#: The template that shipped fragment 1 of the retired curation statement.
DECLARED_SURFACE = "l-01-agent-lifecycles/templates/curator-brief.md"
#: A skill surface that never carried that sentence.
UNDECLARED_SURFACE = "l-01-agent-lifecycles/criteria/code-seam.md"


def fragment_one_surfaces() -> tuple[str, str, str]:
    """The statement fragment 1 shipped, the surface it shipped on, and one it never reached."""

    row = next(
        row
        for row in RETIRED_CURATION_STATEMENTS
        if row.reason.startswith("fragment 1:") and row.sources == (f"skills/{DECLARED_SURFACE}",)
    )
    return row.statement, DECLARED_SURFACE, UNDECLARED_SURFACE
