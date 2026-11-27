"""Supported language definitions distinguish safe citation moves from ambiguous mentions."""

from __future__ import annotations

import sys
import tomllib
import unittest
from importlib.metadata import version
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
UV_LOCK = Path(__file__).resolve().parents[1] / "uv.lock"
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.memory_quality.style.citations import extents, grammars
from tree_sitter import Parser


def spans(path: str, source: str) -> dict[str, list[tuple[int, int]]]:
    return {
        name: [binding.span for binding in items]
        for name, items in grammars.bindings(path, source.splitlines()).items()
    }


def _numbered(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.strip().split("."))


class PinnedDependencyTests(unittest.TestCase):
    """Parser measurements remain aligned with pyproject bounds, uv.lock, and installed wheels."""

    def setUp(self) -> None:
        declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        self.pins = {
            name: bounds
            for name, _, bounds in (
                one.partition(">=") for one in declared["project"]["dependencies"]
            )
        }
        lock_doc = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
        self.locked = {
            pkg["name"]: pkg["version"]
            for pkg in lock_doc.get("package", ())
            if isinstance(pkg, dict) and "name" in pkg and "version" in pkg
        }

    def bounds(self, package: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """The ``>=lower,<upper`` a dependency line declares, as comparable tuples."""
        self.assertIn(package, self.pins, f"{package} is claimed but not a declared dependency")
        lower, separator, upper = self.pins[package].partition(",<")
        self.assertEqual(separator, ",<", f"{package} carries no upper bound: {self.pins[package]}")
        return _numbered(lower), _numbered(upper)

    def test_every_measured_version_is_a_declared_dependency_within_its_pin(self) -> None:
        for package, measured in grammars.MEASURED_VERSIONS.items():
            with self.subTest(package=package):
                lower, upper = self.bounds(package)
                self.assertLessEqual(lower, _numbered(measured))
                self.assertLess(_numbered(measured), upper)

    def test_every_measured_version_matches_uv_lock_and_installed_wheel(self) -> None:
        for package, measured in grammars.MEASURED_VERSIONS.items():
            with self.subTest(package=package):
                self.assertEqual(
                    self.locked.get(package),
                    measured,
                    f"{package} measured {measured!r} drifted from uv.lock {self.locked.get(package)!r}",
                )
                lower, upper = self.bounds(package)
                installed = version(package)
                self.assertLessEqual(lower, _numbered(installed))
                self.assertLess(_numbered(installed), upper)
                self.assertEqual(installed, measured)

    def test_every_declared_language_provider_loads_and_compiles_queries(self) -> None:
        default_set = grammars.default_language_set()
        for suffix, key in grammars.SUFFIX_GRAMMARS.items():
            with self.subTest(suffix=suffix, key=key):
                self.assertEqual(default_set.grammar_of(f"sample{suffix}"), key)
        for provider in default_set.providers:
            with self.subTest(provider=provider.key):
                if isinstance(provider, grammars.TreeSitterLanguageProvider):
                    self.assertIsNotNone(provider.load_language())
                    self.assertIsNotNone(provider.compiled_tags_query())
                    if provider.has_test_cases:
                        self.assertIsNotNone(provider.compiled_tests_query())
                else:
                    self.assertEqual(provider.key, grammars.LEAN)


class TestCaseDefinitionTests(unittest.TestCase):
    def test_test_file_eligibility_is_once_per_parse_and_preserves_bindings(self) -> None:
        lines = [
            "function helper() {}",
            'it("preserved case", () => {',
            "  const value = 1;",
            "});",
        ]
        ordinary = {
            "helper": [grammars.Binding((1, 1), 1)],
            "value": [grammars.Binding((3, 3), 3)],
        }
        for suffix in ("ts", "tsx", "js", "jsx", "mts", "cts", "mjs", "cjs"):
            for is_test in (True, False):
                path = f"review{'.test' if is_test else ''}.{suffix}"
                expected = dict(ordinary)
                if is_test:
                    expected["preserved case"] = [grammars.Binding((2, 4), 2, test_case=True)]
                with (
                    self.subTest(path=path),
                    mock.patch.object(
                        grammars.test_cases, "is_test_file", wraps=grammars.test_cases.is_test_file
                    ) as eligible,
                ):
                    self.assertEqual(grammars.bindings(path, lines), expected)
                    eligible.assert_called_once_with(path)
                    # A second parse checks its own eligibility; no cross-call cache is added.
                    self.assertEqual(grammars.bindings(path, lines), expected)
                    self.assertEqual(eligible.call_args_list, [mock.call(path), mock.call(path)])
        with mock.patch.object(grammars.test_cases, "is_test_file") as eligible:
            self.assertEqual(
                grammars.bindings("review.test.py", ["def helper():", "    return 1"]),
                {"helper": [grammars.Binding((1, 2), 1)]},
            )
            self.assertEqual(grammars.bindings("review.test.txt", lines), {})
            eligible.assert_not_called()

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
        # A case that does not run proves nothing, so it offers no line range either (rule 3).
        for title, reason, offers_range in (
            ("skipped", ".skip, which does not run", False),
            ("todo", ".todo, which does not run", False),
            ("parameterized", ".each", True),
            ("computed", "computed", True),
        ):
            with self.subTest(title=title):
                self.assertNotIn(title, bound)
                message = grammars.test_case_refusal("review.spec.ts", source.splitlines(), title)
                self.assertIn(reason, message or "")
                self.assertEqual(offers_range, "line_range" in (message or ""))


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


if __name__ == "__main__":
    unittest.main()
