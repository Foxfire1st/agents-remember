"""The extent of one JavaScript or TypeScript test case, for a proof named by its lines (MIK-R194).

A proof may name a case that no ``path::name`` reaches -- an ``it.each``, a ``for`` table, a computed
title, a title bound twice -- by the exact lines the case occupies (rules 1 to 3). This module answers
which case a range is, and why a range is no case at all.

It reads syntax nodes rather than parsing: :mod:`grammars` parses and passes its own walk in. That
direction is fixed, because ``grammars`` imports ``test_cases``, and this module's texts are the
writer's, so nothing here may be reachable from ``test_cases``.

False-positive boundaries:

1. Only the OUTERMOST matching call is a case. ``it.each([...] as const)('title', body)`` parses as a
   call whose function is another matching call; the inner call holds the parameters, not the title
   and the body, so it is no case and its own line is never a case's extent.
2. A member of the callee that is not a known modifier (``test.describe``, ``test.step``,
   ``test.setTimeout``) makes the call no case at all, and a call with no title and body of its own
   (a bare parameter table, ``it.each([1]);``) registers no test and is no case.
3. A call with a parse error is no case.
4. ``path::name`` reaches a case only when the call is a bare ``it``/``test`` and every enclosing
   ``describe`` carries a plain title; that is the one form :func:`test_cases.case_names` binds. Which
   names REACH the case is decided by the real resolver when the caller supplies one, because a
   title can be taken by an ordinary declaration as well as by a test call.
5. A case that does not run -- its own modifier or any enclosing suite's -- proves nothing, and the
   refusal names that modifier rather than offering a range.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from tree_sitter import Node

from agents_remember.memory_quality.style.citations import test_cases

TEST_CASE: Final = "test_case"
# Why a name does not reach this case: the caller's real resolver answers the title with a reason
# when ``path::title`` resolves to this case, and ``None`` when it does not reach it.
Reaches = Callable[[str], str | None]


@dataclass(frozen=True)
class CaseExtent:
    """One test case's extent, in the shape :class:`extents.Extent` reports a range, and its titles.

    ``titles`` are the titles the proof's evidence pairs with: the case's own plain title, and the
    qualified ``a > b > title`` behind its plain-titled enclosing suites. They are what the evidence
    text is matched against, not which names are definitions -- a computed enclosing suite keeps the
    bare title here and still leaves the case unreachable by ``path::name``. The range fields mirror
    ``extents.Extent`` rather than importing it, because ``extents`` imports ``grammars``.
    """

    start: int
    end: int
    kind: str
    declaration: int | None
    test_case: bool
    titles: tuple[str, ...]


def not_a_case_file(path: str, start: int, end: int) -> str:
    """Why a range in ``path`` is no test case's extent: the file is no test file at all."""

    return _refusal(
        path,
        start,
        end,
        f"the file is not a JavaScript or TypeScript .test. or .spec. file; name the test "
        f"'{path}::<name>'",
    )


def case_at(
    path: str,
    nodes: Sequence[Node],
    start: int,
    end: int,
    reaches: Reaches | None = None,
) -> CaseExtent | str:
    """The one case the range ``start``-``end`` is, or the refusal naming why it is none (rule 2).

    ``reaches`` is the caller's real name resolver, asked for each title the case could be named by,
    and answers a reason when ``path::title`` resolves to the case. Without it this function falls
    back to the parsed calls alone, which answers the same question for every title only a test
    binds; a caller with a code snapshot passes the real resolver so that an ordinary declaration of
    the same name also keeps the range open.
    """

    cases = [node for node in nodes if _is_case(node)]
    ask = _Ask(path, start, end, cases, reaches)
    for node in cases:
        if test_cases.span_of(node) == (start, end):
            return _admitted_or_refused(node, ask)
    return _refusal(path, start, end, _no_extent(cases, start, end))


@dataclass(frozen=True)
class _Ask:
    """One question: which case do these lines name, in a file whose names this resolver answers."""

    path: str
    start: int
    end: int
    cases: Sequence[Node]
    reaches: Reaches | None


def _admitted_or_refused(node: Node, ask: _Ask) -> CaseExtent | str:
    path, start, end = ask.path, ask.start, ask.end
    """One case whose extent is exactly the range: admitted, or refused naming why not."""

    blocking = test_cases.blocking_reason(node)
    if blocking is not None:
        return _refusal(path, start, end, f"the case {blocking}")
    titles = test_cases.pairing_titles(node)
    reached = _reached_name(node, ask.cases, titles, ask.reaches)
    if reached is not None:
        return _refusal(path, start, end, f"the case is a definition; name it '{path}::{reached}'")
    return CaseExtent(
        start=start, end=end, kind=TEST_CASE, declaration=None, test_case=True, titles=titles
    )


def _reached_name(
    node: Node, cases: Sequence[Node], titles: Sequence[str], reaches: Reaches | None
) -> str | None:
    """The first title that really names this case, or ``None`` when the range is the only proof."""

    for title in titles:
        if reaches is not None:
            if reaches(title) is None:
                return title
            continue
        if _names_only(title, node, cases):
            return title
    return None


def _names_only(title: str, node: Node, cases: Sequence[Node]) -> bool:
    """Whether ``title`` names this case and no other one in the file.

    A title bound twice resolves to two extents, so ``path::title`` names neither; the range is then
    the only proof, which is the remedy ``code_anchors`` names for an ambiguous symbol. This is the
    fallback for callers with no code snapshot: it counts test calls only, so it cannot see a
    declaration that takes the same name.
    """

    holders = [one for one in cases if title in test_cases.case_names(one)]
    return len(holders) == 1 and holders[0].id == node.id


def _no_extent(cases: Sequence[Node], start: int, end: int) -> str:
    """Why no case occupies exactly the range, naming the cases it overlaps when there are any."""

    spans = [test_cases.span_of(one) for one in cases]
    overlapping = sorted({one for one in spans if one[0] <= end and start <= one[1]})
    if not overlapping:
        return "they are no test case's extent; no test case overlaps them"
    found = ", ".join(f"{one[0]}-{one[1]}" for one in overlapping)
    return f"they are no test case's extent; the cases overlapping them span {found}"


def _refusal(path: str, start: int, end: int, why: str) -> str:
    return f"lines {start}-{end} of {path} are not a case a line range proves: {why}"


def _is_case(node: Node) -> bool:
    """Whether ``node`` is a case: the outermost call rooted at ``it``/``test`` holding its own body."""

    if not test_cases.is_case_call(node):
        return False
    parent = node.parent
    if parent is None or parent.type != "call_expression":
        return True
    function = parent.child_by_field_name("function")
    # The inner call of ``it.each([...])('title', body)`` holds the parameters, not the case.
    return function is None or function.id != node.id or not test_cases.is_case_call(parent)
