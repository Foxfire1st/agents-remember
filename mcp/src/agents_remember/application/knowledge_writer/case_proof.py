"""A proof that names a test case by its lines, resolved at the code candidate (MIK-R194 rules 1-3).

The writer's own resolution of a range proof lives here rather than in :mod:`authoring`, which keeps
only the call: the check parses the candidate bytes, asks whether any ``path::name`` really reaches the
case through the code snapshot's own resolver, and answers the titles the proof's evidence pairs with.
That pairing is deliberately separate from name eligibility: a literal case inside a computed
``describe`` suite is reached by no name and still pairs its evidence by its own title, while a case
whose OWN title is computed pairs with nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from agents_remember.application.knowledge_writer.code_anchors import AnchorResolutionError
from agents_remember.application.knowledge_writer.handoff import TestRangeReference
from agents_remember.errors import GrammarUnavailableError
from agents_remember.memory_quality.style.citations import grammars

__all__ = ["CaseProof", "proven_tests", "range_proof"]


@dataclass(frozen=True)
class CaseProof:
    """What a range proof came to: its locator and pairing titles, or the reason it writes nothing."""

    locator: Mapping[str, Any] | None = None
    titles: tuple[str, ...] = ()
    refusal: str | None = None


def range_proof(code: Any, test: TestRangeReference) -> CaseProof:
    """The locator and pairing titles of one range proof at C, or why the lines are no case."""

    document = test.locator.document
    start, end = int(document["start"]), int(document["end"])
    try:
        lines = code.blob_bytes(test.path).decode("utf-8").split("\n")
    except (AnchorResolutionError, UnicodeDecodeError) as error:
        return CaseProof(refusal=str(error))
    try:
        found = grammars.test_case_range(
            test.path, lines, start, end, _reaches(code, test, start, end)
        )
    except GrammarUnavailableError as error:
        return CaseProof(refusal=str(error))
    if isinstance(found, str):
        return CaseProof(refusal=found)
    return CaseProof(locator=dict(document), titles=found.titles)


def _reaches(
    code: Any, test: TestRangeReference, start: int, end: int
) -> Callable[[str], str | None]:
    """``path::title`` asked of the snapshot's resolver: a reason, or ``None`` when it reaches THIS case.

    Resolving is not the question. A name can resolve to an ordinary declaration in the same file, or to
    another case, and either way the range is still the only proof of this one; the name reaches the case
    only when its own anchor holds the very bytes this case's extent holds.
    """

    try:
        extent = code.resolve(test.path, {"kind": "line_range", "start": start, "end": end})
    except AnchorResolutionError:
        extent = None

    def ask(title: str) -> str | None:
        try:
            named = code.resolve(test.path, {"kind": "symbol", "name": title})
        except AnchorResolutionError as error:
            return str(error)
        if extent is not None and named.content != extent.content:
            return f"'{title}' resolves to another construct in {test.path}"
        return None

    return ask


def proven_tests(
    proofs: Iterable[Any], write: Callable[[Any], tuple[str, tuple[Any, ...]]]
) -> dict[Any, str]:
    """Each cited test's outcome, from the proofs written and the tests each proof pairs with.

    A ``path::name`` proof is cited by its own name, so it lands under its own reference. A range proof
    is cited by nothing, so it lands only under the titles its case pairs with, and the first outcome
    recorded for a title stands: a case proved twice is one result, not the last writer's.
    """

    proven: dict[Any, str] = {}
    for proof in proofs:
        outcome, paired = write(proof)
        if not isinstance(proof.test, TestRangeReference):
            proven[proof.test] = outcome
        for test in paired:
            proven.setdefault(test, outcome)
    return proven
