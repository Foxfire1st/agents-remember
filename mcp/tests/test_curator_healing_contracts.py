"""R62 rule 34 rejects healing restrictions while retaining bounded routing."""

import pytest
from agents_remember_test_support.testing.curator_contracts import (
    ROLE_HEALING,
    healing_findings,
)


@pytest.mark.parametrize(
    ("text", "finding"),
    [
        ("Route all encountered debt away.", "route-all-debt"),
        ("Limit healing to your own faults.", "own-fault-only"),
        ("Route every encountered memory fault to the Manager.", "route-all-debt"),
        ("Report all memory debt you find to the Manager.", "route-all-debt"),
        ("Repair only faults you caused.", "own-fault-only"),
        ("Fix only memory your own leaf broke.", "own-fault-only"),
        ("Route every encountered debt away.", "route-all-debt"),
        ("Defer all encountered faults to the owner.", "route-all-debt"),
        ("Escalate every memory error to another leaf.", "route-all-debt"),
        ("Report every debt you find to the Manager.", "route-all-debt"),
        ("Route all memory problems you encounter away.", "route-all-debt"),
        ("Limit healing to your own fault.", "own-fault-only"),
        ("Restrict repairs to your own memory errors.", "own-fault-only"),
        ("Repair only the fault you caused.", "own-fault-only"),
        ("Correct only mistakes you introduced.", "own-fault-only"),
        ("Heal exclusively the Curator's own memory debt.", "own-fault-only"),
        ("Fix only your own error.", "own-fault-only"),
    ],
)
def test_healing_check_rejects_avoided_debt_and_own_fault_only(text, finding):
    assert healing_findings(text) == (finding,)


@pytest.mark.parametrize(
    "text",
    [
        " ".join(ROLE_HEALING),
        "Repair wrong or missing memory you meet while curating, whoever left it; "
        "make no broad or unrelated sweeps; route repairs needing code, "
        "a requirement decision or another leaf's work.",
        "Repair faults your work or instrument causes.",
    ],
)
def test_healing_check_keeps_bounded_routing_and_positive_repair_duties(text):
    assert healing_findings(text) == ()
