"""What a consolidation pass records on a rule or family row: its two dispositions and its tests.

A consolidation pass reads listed rules and families again against the code. Beside today's
dispositions a rule or family row can say:

* ``stands`` -- the record was read in full at the row's ``revision`` and stands; ``reason`` says
  in one line why. Nothing of its meaning or its card entries changed, so the set of entries the row
  covers stays as it was (as for ``no_impact``);
* ``unresolved`` -- rule and code contradict each other and the reader cannot tell which is wrong,
  or a named proof failed; ``reason`` says what contradicts what, and nothing of the record changed.

A row can also name the tests the reader ran with their results (``testsRun``), the named tests it
could not run with the reason (``testsNotRun``), or that the rule has no recorded proof
(``noProofRecorded``). The row model checks their shape only; which rows must carry them is the
knowledge writer's rule for a consolidation task.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, model_validator

from agents_remember.models.knowledge_files.shapes import FileModel, Label, Text

__all__ = [
    "CONSOLIDATION_DISPOSITIONS",
    "STANDS",
    "UNRESOLVED",
    "ConsolidationEvidence",
    "TestNotRun",
    "TestRun",
]

STANDS: Final = "stands"
UNRESOLVED: Final = "unresolved"
CONSOLIDATION_DISPOSITIONS: Final = (STANDS, UNRESOLVED)


class TestRun(FileModel):
    """``{ test, result }``: one test the reader ran, and whether it passed."""

    __test__ = False  # a model, not a pytest class

    test: Label
    result: Literal["passed", "failed"]


class TestNotRun(FileModel):
    """``{ test, reason }``: one named test the reader could not run in its seat, and why."""

    __test__ = False  # a model, not a pytest class

    test: Label
    reason: Text


class ConsolidationEvidence(FileModel):
    """The test fields a rule or family row may carry; each is absent where it says nothing."""

    tests_run: tuple[TestRun, ...] | None = Field(default=None, alias="testsRun", min_length=1)
    tests_not_run: tuple[TestNotRun, ...] | None = Field(
        default=None, alias="testsNotRun", min_length=1
    )
    no_proof_recorded: Literal[True] | None = Field(default=None, alias="noProofRecorded")
    """Written only as ``true``: the rule has no recorded proof; omit it otherwise."""

    @model_validator(mode="after")
    def _require_distinct_tests(self) -> ConsolidationEvidence:
        named = [one.test for one in (*(self.tests_run or ()), *(self.tests_not_run or ()))]
        repeated = sorted({test for test in named if named.count(test) > 1})
        if repeated:
            raise ValueError(
                f"a row names a test once, as run or as not run: {', '.join(repeated)}"
            )
        return self

    def names_tests(self) -> bool:
        """Whether the row names the tests run, the tests not run, or that no proof is recorded."""

        return bool(self.tests_run or self.tests_not_run or self.no_proof_recorded)
