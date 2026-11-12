"""The Doc12 measures over census claims (MIK-R20 rule 4).

The accounting is ``N = T + F + U + P`` over the cohort: claims whose ``applicability`` is
``assessable``. Claims recorded ``non_claim`` or ``historical_non_applicable`` are counted beside the
cohort, never inside it, so they neither vanish nor dilute a share.

The measures are reported together and never folded into one score:

* verified truth share ``T / N``;
* correctness among resolved claims ``T / (T + F)``, always rendered with ``U`` and ``P``;
* contradiction share ``F / N``;
* assessment completion ``(T + F + U) / N``;
* realization coverage and relevant truth coverage ``C / K``: not applicable while there is no
  reference inventory, which no census file declares yet.

A zero denominator is *not applicable*, never a perfect score, and every rendered percentage carries
its counts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal

from agents_remember.models.knowledge_files.census import CensusClaim

MeasureState = Literal["computed", "not_applicable"]
NO_REFERENCE_INVENTORY: Final = "no reference inventory"
ZERO_DENOMINATOR: Final = "zero denominator"


@dataclass(frozen=True)
class Counts:
    """The cohort's four cells and the two out-of-cohort counts."""

    supported: int = 0
    contradicted: int = 0
    unresolved: int = 0
    pending: int = 0
    non_claim: int = 0
    historical_non_applicable: int = 0

    @property
    def total(self) -> int:
        """``N``: by construction ``T + F + U + P``."""

        return self.supported + self.contradicted + self.unresolved + self.pending

    @classmethod
    def of(cls, claims: Iterable[CensusClaim]) -> Counts:
        cells = {"T": 0, "F": 0, "U": 0, "P": 0}
        outside = {"non_claim": 0, "historical_non_applicable": 0}
        for claim in claims:
            if claim.in_cohort:
                cells[claim.cell] += 1
            else:
                outside[claim.applicability] += 1
        return cls(
            supported=cells["T"],
            contradicted=cells["F"],
            unresolved=cells["U"],
            pending=cells["P"],
            non_claim=outside["non_claim"],
            historical_non_applicable=outside["historical_non_applicable"],
        )

    def render(self) -> str:
        return (
            f"N {self.total} (T {self.supported}, F {self.contradicted}, U {self.unresolved}, "
            f"P {self.pending})"
        )

    def to_document(self) -> dict[str, int]:
        return {
            "N": self.total,
            "T": self.supported,
            "F": self.contradicted,
            "U": self.unresolved,
            "P": self.pending,
            "nonClaim": self.non_claim,
            "historicalNonApplicable": self.historical_non_applicable,
        }


@dataclass(frozen=True)
class Measure:
    """One measure: its counts, and its value only when the denominator is not zero."""

    name: str
    label: str
    numerator: int | None
    denominator: int | None
    state: MeasureState
    reason: str = ""
    context: str = ""

    @property
    def value(self) -> float | None:
        if self.state != "computed" or not self.denominator or self.numerator is None:
            return None
        return self.numerator / self.denominator

    def render(self) -> str:
        context = f"; {self.context}" if self.context else ""
        value = self.value
        if value is None:
            counts = "" if self.numerator is None else f": {self.numerator} of {self.denominator}"
            return f"{self.label}: not applicable ({self.reason}{counts}{context})"
        return f"{self.label}: {value:.1%} ({self.numerator} of {self.denominator}{context})"

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "name": self.name,
            "state": self.state,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
        }
        if self.reason:
            document["reason"] = self.reason
        return document


def _ratio(name: str, label: str, numerator: int, denominator: int, context: str = "") -> Measure:
    if denominator == 0:
        return Measure(
            name, label, numerator, denominator, "not_applicable", ZERO_DENOMINATOR, context
        )
    return Measure(name, label, numerator, denominator, "computed", context=context)


def _without_reference(name: str, label: str) -> Measure:
    return Measure(name, label, None, None, "not_applicable", NO_REFERENCE_INVENTORY)


def compute_measures(counts: Counts) -> tuple[Measure, ...]:
    """Return every declared measure for ``counts``, in Doc12's order."""

    t, f, u, p, n = (
        counts.supported,
        counts.contradicted,
        counts.unresolved,
        counts.pending,
        counts.total,
    )
    return (
        _ratio("verified_truth_share", "verified truth share", t, n),
        _ratio(
            "correctness_among_resolved",
            "correctness among resolved claims",
            t,
            t + f,
            context=f"U {u}, P {p}",
        ),
        _ratio("contradiction_share", "contradiction share", f, n),
        _ratio("assessment_completion", "assessment completion", t + f + u, n),
        _without_reference("relevant_truth_coverage", "relevant truth coverage (C/K)"),
        _without_reference("realization_coverage", "realization coverage"),
    )
