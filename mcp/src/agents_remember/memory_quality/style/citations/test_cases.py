"""Plain-string and parameterized JavaScript/TypeScript test cases as citation definitions.

A plain ``it``/``test`` with a plain string title is a definition, and ``path::title`` names it. Every
other case form is no definition, and the refusal says why and how to prove the case instead
(MIK-R54, MIK-R194 rules 2 and 3):

* a case that does not run (``.skip``, ``.todo``, ``.skipIf``, ``.runIf``, ``.fails``), on the case
  itself or on any enclosing suite, proves nothing, and no line range reaches it either;
* a case that runs but that no name reaches (``.each``, ``.for``, ``.only``, ``.concurrent``,
  ``.sequential``, a computed title, a computed ``describe`` title) is proved by its exact lines.

Modifiers are read by NAME from the callee's member chain, never by substring: ``.skipIf`` is not
``.skip``, and an argument holding the modifier's spelling is not a modifier. The chain is read by
walking the callee's calls and members in alternation, because a modifier may sit either side of a
call: ``describe.skipIf(flag).each([1])`` carries both, and ``it.each([1])`` puts a call under the
identifier before the case's own call.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from tree_sitter import Node

_ESCAPE = re.compile(r"\\(?:u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r\n|[\s\S])")

# The members a test call may carry, outermost modifier last (``it.each(...)('title', body)``). A
# member outside this set -- ``test.describe``, ``test.step``, ``test.setTimeout`` -- is no case.
MODIFIERS = frozenset(
    {
        "each",
        "for",
        "only",
        "concurrent",
        "sequential",
        "skip",
        "todo",
        "skipIf",
        "runIf",
        "fails",
    }
)
CALL_ROOTS = frozenset({"it", "test"})
DESCRIBE = "describe"
# A case carrying any of these proves nothing at all (MIK-R194 rule 2's refusals).
NOT_RUNNING = frozenset({"skip", "todo"})
CONDITIONAL = frozenset({"skipIf", "runIf"})
FAILS = "fails"
# What a call's title and body can be: a case holds both, so a bare parameter table is no case.
BODIES = frozenset({"arrow_function", "function", "function_expression"})
# The modifiers that build the case in a second call, so the case must carry its title and body.
TABLE_MODIFIERS = frozenset({"each", "for"})


def _unescape(match: re.Match[str]) -> str:
    token = match.group()[1:]
    if token.startswith("u{"):
        return chr(int(token[2:-1], 16))
    if token.startswith(("u", "x")) and len(token) > 1:
        return chr(int(token[1:], 16))
    return {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "b": "\b",
        "f": "\f",
        "v": "\v",
        "0": "\0",
        "\n": "",
        "\r": "",
        "\r\n": "",
    }.get(token, token)


def is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return ".test." in name or ".spec." in name


def span_of(node: Node) -> tuple[int, int]:
    """The one-based line range ``node`` occupies: its first line to the line that closes it."""

    start = node.start_point[0] + 1
    row, column = node.end_point
    return start, max(start, row if column == 0 else row + 1)


def chain(node: Node) -> tuple[str, list[str], Node | None]:
    """The identifier a call's callee is rooted at, the members it passes, and the root's node.

    The callee is read through the members' NAMES, never through the callee's text: a text match
    reports ``.skipIf`` as ``.skip`` and reads an argument as a member. Calls and members alternate,
    and both are followed: ``it.each([...])('t', b)`` puts a call under the members, and
    ``describe.skipIf(flag).each([1])`` puts members on both sides of a call, so a walk that stops at
    the first call drops the suite's own modifier.
    """

    function = node.child_by_field_name("function")
    names: list[str] = []
    while function is not None:
        if function.type == "call_expression":
            function = function.child_by_field_name("function")
            continue
        if function.type != "member_expression":
            break
        member = function.child_by_field_name("property")
        if member is None or member.type != "property_identifier" or member.text is None:
            return "", [], None
        names.append(member.text.decode("utf-8"))
        function = function.child_by_field_name("object")
    if function is None or function.type != "identifier" or function.text is None:
        return "", [], None
    return function.text.decode("utf-8"), list(reversed(names)), function


def arguments_of(node: Node) -> list[Node]:
    """The call's own arguments, comments aside, in order."""

    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [child for child in arguments.named_children if child.type != "comment"]


def title_of(node: Node) -> str | None:
    """The plain string title a test or ``describe`` call passes first, or ``None``."""

    first = next(iter(arguments_of(node)), None)
    if first is None or first.type != "string" or first.has_error or first.text is None:
        return None
    title = _ESCAPE.sub(_unescape, first.text.decode("utf-8")[1:-1])
    # Combine valid UTF-16 pairs; preserve lone surrogate units as JavaScript does.
    return title.encode("utf-16-le", errors="surrogatepass").decode(
        "utf-16-le", errors="surrogatepass"
    )


def is_case_call(node: Node) -> bool:
    """Whether ``node`` is a case call: ``it``/``test``, known members, and a test form of its own.

    A table modifier (``.each``, ``.for``) builds the case in a second call, so the case must carry
    both its title and its body: the bare table (``it.each([1]);``) registers no test and is no case.
    Every other form registers a test with its title, and ``.todo``/``.skip`` may carry no body.
    """

    if node.type != "call_expression" or node.has_error:
        return False
    root, names, _ = chain(node)
    if root not in CALL_ROOTS or not all(one in MODIFIERS for one in names):
        return False
    arguments = arguments_of(node)
    if not arguments:
        return False
    if any(one in TABLE_MODIFIERS for one in names):
        return len(arguments) >= 2 and arguments[1].type in BODIES
    return True


def enclosing_describes(node: Node) -> list[Node]:
    """The ``describe`` calls this node sits in, innermost first."""

    found: list[Node] = []
    parent = node.parent
    while parent is not None:
        if parent.type == "call_expression":
            root, _names, _ = chain(parent)
            if root == DESCRIBE:
                found.append(parent)
        parent = parent.parent
    return found


def modifier_chains(node: Node) -> list[list[str]]:
    """The member names of the case, then of each enclosing suite (innermost first)."""

    return [chain(node)[1], *(chain(one)[1] for one in enclosing_describes(node))]


def blocking_reason(node: Node) -> str | None:
    """Why this case does not run, read on the case AND every enclosing suite, or ``None``.

    The strongest reason wins, so ``it.skipIf(x).skip`` reports that it does not run rather than that
    it is conditional: a curator who is told the case is conditional would offer a proof that fails.
    """

    named = {name for names in modifier_chains(node) for name in names}
    for group, reason in (
        (NOT_RUNNING, "does not run"),
        (CONDITIONAL, "runs only when its condition holds"),
        ({FAILS}, "passes only when its body fails"),
    ):
        blocking = next((name for name in sorted(group) if name in named), None)
        if blocking is not None:
            return f"uses .{blocking}, which {reason}"
    return None


def suite_titles(node: Node) -> list[str] | None:
    """The plain titles of the suites this case sits in, innermost first, or ``None``.

    ``None`` means no qualified name exists: an enclosing suite whose title is computed, or one built
    by a table (``describe.each``, ``describe.for``), names a pattern rather than a suite, so nothing
    behind it can be named. A suite's other modifiers do not do this: ``describe.skip('x')`` still
    carries the title ``x``.
    """

    found: list[str] = []
    for describe in enclosing_describes(node):
        _root, names, _ = chain(describe)
        title = title_of(describe)
        if title is None or any(one in TABLE_MODIFIERS for one in names):
            return None
        found.append(title)
    return found


def pairing_titles(node: Node) -> tuple[str, ...]:
    """The titles a proof of this case's lines pairs its evidence with.

    The case's own plain title always, and the qualified ``suite > case`` behind its plain-titled
    enclosing suites. A computed or table-built enclosing suite contributes nothing, so the case's own
    literal title is still paired bare; a computed OWN title pairs with nothing.
    """

    title = title_of(node)
    if title is None:
        return ()
    suites = suite_titles(node)
    if suites is None:
        return (title,)
    qualified = " > ".join((*reversed(suites), title))
    return tuple(dict.fromkeys((qualified, title)))


def titles_at(node: Node) -> tuple[str, ...]:
    """The titles ``path::title`` reaches one test call by, or ``()`` when none does.

    A bare title, then the qualified ``suite > case`` behind its plain-titled enclosing ``describe``
    titles. Empty for a computed title, and for a case under a computed or table-built ``describe``:
    those are reached by their lines alone. A suite's other modifiers do not change this: a plain case
    under ``describe.skip`` was a definition before MIK-R194 and stays one, and only the range route
    gained the running restriction.
    """

    if suite_titles(node) is None:
        return ()
    return pairing_titles(node)


def case_names(node: Node) -> tuple[str, ...]:
    """Qualified title and bare title for one plain ``it`` or ``test`` call."""

    if not is_case_call(node):
        return ()
    root, names, _ = chain(node)
    if root not in CALL_ROOTS or names:
        return ()
    return titles_at(node)


def refusal(nodes: list[Node], path: str, name: str) -> str:
    """Why an unsupported test call cannot name a symbol proof, and how it is proved instead."""

    for node in nodes:
        if node.type != "call_expression" or node.has_error:
            continue
        root, names, _ = chain(node)
        if root not in CALL_ROOTS:
            continue
        raw = "".join(one.text.decode("utf-8") for one in arguments_of(node) if one.text)
        if name not in raw:
            continue
        blocking = blocking_reason(node)
        if blocking is not None:
            # No line-range offer: a case that does not run is reachable by no range either.
            return f"test case {name!r} {blocking}"
        modifier = next((one for one in names if one in MODIFIERS), None)
        if modifier is not None:
            return _offer_lines(
                f"test case {name!r} uses .{modifier}, which is not a definition", path, node
            )
        computed = _computed_title(node)
        if computed is not None:
            return _offer_lines(f"test case {name!r} {computed}", path, node)
    return (
        f"test case {name!r} does not resolve uniquely: only plain-string it/test titles are "
        "definitions; .skip, .todo, .each and computed titles are not; prove it by its lines: "
        + _line_range_proof(path, "S", "E")
    )


def _computed_title(node: Node) -> str | None:
    """Why no name reaches this call: its own title is computed, or an enclosing describe's is."""

    if title_of(node) is None:
        return "has a computed title, not a plain string"
    if any(title_of(one) is None for one in enclosing_describes(node)):
        return "sits under a computed describe title, so no name reaches it"
    return None


def _offer_lines(reason: str, path: str, node: Node) -> str:
    """``reason``, then the exact proof that names the case by its lines (rule 3)."""

    start, end = span_of(node)
    return f"{reason}; prove it by its lines: " + _line_range_proof(path, str(start), str(end))


def _line_range_proof(path: str, start: str, end: str) -> str:
    """The proof the curator writes instead of a name: ``{path, locator: {kind: line_range, …}}``."""

    return (
        '"test": {"path": "'
        + path
        + '", "locator": {"kind": "line_range", "start": '
        + start
        + ', "end": '
        + end
        + "}}"
    )
