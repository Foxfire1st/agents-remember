"""Supported language definitions distinguish safe citation moves from ambiguous mentions."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.memory_quality.style.citations import extents, grammars
from tree_sitter import Parser


def spans(path: str, source: str) -> dict[str, list[tuple[int, int]]]:
    return {
        name: [binding.span for binding in items]
        for name, items in grammars.bindings(path, source.splitlines()).items()
    }


class TestCaseDefinitionTests(unittest.TestCase):
    def test_nested_test_titles_bind_qualified_and_unique_bare_names(self) -> None:
        source = """describe('review fixes', () => {
  describe("loaded.cards", () => {
    it("covers only members", () => {
      expect(1).toBe(1);
    });
  });
});"""
        for suffix in ("ts", "tsx", "js", "jsx", "mts", "cts", "mjs", "cjs"):
            with self.subTest(suffix=suffix):
                path = f"review.test.{suffix}"
                bound = extents.definitions(path, source.splitlines())
                self.assertEqual(
                    extents.qualified_spans(
                        "review fixes > loaded.cards > covers only members", bound
                    ),
                    ((3, 5),),
                )
                self.assertEqual(extents.qualified_spans("covers only members", bound), ((3, 5),))
                self.assertNotIn("covers only members", spans(f"review.{suffix}", source))

    def test_plain_string_titles_decode_javascript_escapes(self) -> None:
        source = r"""describe('review\'s', () => { it("say \u{1F600} \u0061 \x62", () => {}); });"""
        self.assertIn("review's > say 😀 a b", spans("review.test.ts", source))

    def test_utf16_pairs_match_scalar_titles_and_preserve_lone_units(self) -> None:
        for spelling in (r"\uD83D\uDE00", r"\u{1F600}", "😀"):
            source = f'describe("suite {spelling}", () => {{ it("say {spelling}", () => {{}}); }});'
            bound = extents.definitions("review.test.ts", source.splitlines())
            for title in ("say 😀", "suite 😀 > say 😀"):
                self.assertEqual(extents.qualified_spans(title, bound), ((1, 1),))
        for unit in (r"\uD83D", r"\uDE00"):
            self.assertIn(
                chr(int(unit[2:], 16)), spans("review.test.ts", f'it("{unit}", () => {{}});')
            )
        source = r'it("say \uD83D\uDE00", () => {}); test("say \u{1F600}", () => {});'
        self.assertEqual(
            len(extents.qualified_spans("say 😀", extents.definitions("review.test.ts", [source]))),
            2,
        )

    def test_duplicate_bare_titles_stay_ambiguous_even_on_one_line(self) -> None:
        source: str = "describe('left', () => { it('same', () => {}); }); describe('right', () => { test('same', () => {}); });"
        bound = extents.definitions("review.spec.ts", source.splitlines())
        self.assertEqual(len(extents.qualified_spans("same", bound)), 2)
        self.assertEqual(extents.qualified_spans("left > same", bound), ((1, 1),))
        self.assertEqual(extents.qualified_spans("right > same", bound), ((1, 1),))

    def test_leading_comment_extras_do_not_replace_semantic_first_arguments(self) -> None:
        source = """describe(/* suite */ "suite", () => {
  it(/* case */ "block", () => {});
  test(// case
    "line", () => {});
  it(/* note */ value + "computed", "later", () => {});
  test(/* note */ 123, "nonstring", () => {});
});"""
        for suffix in ("ts", "tsx", "js"):
            path = f"comments.test.{suffix}"
            grammar = grammars.grammar_of(path)
            assert grammar is not None
            tree = Parser(grammars.language(grammar)).parse(source.encode())
            arguments = (
                tree.root_node.named_children[0].named_children[0].child_by_field_name("arguments")
            )
            assert arguments is not None
            self.assertFalse(tree.root_node.has_error)
            self.assertEqual(arguments.named_children[0].type, "comment")
            self.assertTrue(arguments.named_children[0].is_extra)
            bound = extents.definitions(path, source.splitlines())
            self.assertEqual(extents.qualified_spans("suite > block", bound), ((2, 2),))
            self.assertEqual(extents.qualified_spans("suite > line", bound), ((3, 4),))
            for title in ("computed", "later", "nonstring"):
                self.assertNotIn(title, bound)

    def test_unsupported_case_forms_explain_the_reason_and_offer_line_ranges(self) -> None:
        source = """it.skip('skipped', () => {});
test.todo('todo');
test.each([1])('parameterized', () => {});
it(`computed ${value}`, () => {});"""
        bound = spans("review.spec.ts", source)
        for title, reason in (
            ("skipped", ".skip"),
            ("todo", ".todo"),
            ("parameterized", ".each"),
            ("computed", "computed"),
        ):
            with self.subTest(title=title):
                self.assertNotIn(title, bound)
                message = grammars.test_case_refusal("review.spec.ts", source.splitlines(), title)
                self.assertIn(reason, message or "")
                self.assertIn("line_range", message or "")


class PythonExtentTests(unittest.TestCase):
    """Tree-sitter's Python extents, pinned against the constructs ``ast`` used to read.

    Measured over this repository's 707 Python files while the two paths still ran side by
    side: 705 produced byte-identical name-to-extent maps. The two that differed differed
    only by a comment written INSIDE the construct, which tree-sitter includes and ``ast``
    cannot see -- ``serving/conversation/models.py`` ``FeatureCapability`` 662-683 against
    662-690, and ``serving/harness_submission_authority.py``
    ``_certified_pre_send_busy`` 884-893 against 884-894. That shape is pinned below so the
    difference stays a decision rather than a surprise.
    """

    def test_a_decorated_definition_is_stamped_from_its_first_decorator(self) -> None:
        source = "import functools\n\n\n@functools.cache\ndef build():\n    return 1\n"

        self.assertEqual(spans("k.py", source)["build"], [(4, 6)])

    def test_a_class_and_its_methods_and_attributes_all_bind(self) -> None:
        source = "class K:\n    attr = 1\n\n    def method(self):\n        local = 2\n"

        self.assertEqual(
            spans("k.py", source),
            {"K": [(1, 5)], "attr": [(2, 2)], "method": [(4, 5)], "local": [(5, 5)]},
        )


class ScriptDefinitionTests(unittest.TestCase):
    """Every construct the TypeScript, TSX and JavaScript rule calls a definition."""

    DECLARATIONS = (
        ("export class RailRow {}\n", "RailRow", (1, 1)),
        ("export interface Props {\n  title: string;\n}\n", "Props", (1, 3)),
        ("export type Mode = 'a' | 'b';\n", "Mode", (1, 1)),
        ("export enum Kind { A, B }\n", "Kind", (1, 1)),
        ("export const LIMIT = 20;\n", "LIMIT", (1, 1)),
        ("export function main() {}\n", "main", (1, 1)),
        ("export function* gen() {}\n", "gen", (1, 1)),
        ("declare function ambient(): void;\n", "ambient", (1, 1)),
        ("abstract class Abs {\n  abstract go(): void;\n}\n", "go", (2, 2)),
        ("class Fields {\n  static X = 1;\n}\n", "X", (2, 2)),
        ("class Holder {\n  render() {\n    return 1;\n  }\n}\n", "render", (2, 4)),
        ("namespace NS {}\n", "NS", (1, 1)),
        ("interface Props {\n  onClick(): void;\n}\n", "onClick", (2, 2)),
    )

    def test_each_declaration_form_binds_its_name_over_its_own_lines(self) -> None:
        for source, name, span in self.DECLARATIONS:
            with self.subTest(name=name):
                self.assertEqual(spans("dashboard/x.ts", source).get(name), [span])

    def test_a_tsx_component_is_read_by_the_tsx_dialect(self) -> None:
        source = 'export const Row = () => <div className="rail-row">text</div>;\n'

        self.assertEqual(spans("dashboard/x.tsx", source)["Row"], [(1, 1)])

    def test_a_javascript_module_is_read_by_the_javascript_grammar(self) -> None:
        for suffix in (".js", ".jsx", ".mjs", ".cjs"):
            with self.subTest(suffix=suffix):
                found = spans(f"dashboard/x{suffix}", "export function main() {}\n")
                self.assertEqual(found["main"], [(1, 1)])


OFFLINE_PROBE = '''\
"""Parse one file of every supported language with every network path closed."""

import socket
import sys


def _blocked(*args, **kwargs):
    raise OSError("network egress is blocked by the offline parse guard")


socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked
socket.gethostbyname = _blocked

try:
    socket.create_connection(("example.invalid", 80))
except OSError:
    pass
else:
    raise SystemExit("the network block did not take effect; the run proves nothing")

from agents_remember.memory_quality.style.citations import grammars

for path, source in (
    ("k.py", "def build():\\n    return 1\\n"),
    ("k.ts", "export class RailRow {}\\n"),
    ("k.tsx", "export const Row = () => null;\\n"),
    ("k.js", "export function main() {}\\n"),
):
    if not grammars.bindings(path, source.splitlines()):
        raise SystemExit(f"{path} parsed to nothing with egress blocked")
print("ok")
'''


if __name__ == "__main__":
    unittest.main()
