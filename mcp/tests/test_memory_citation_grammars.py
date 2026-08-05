"""L6-R17/R28: one parser for every language a citation points into.

The capability being bought is telling a DEFINITION from a MENTION, because that is what
the ``--fix`` uniqueness tiebreaker runs on. :class:`TypeScriptPureMoveTests` is the proof
that it bites (L6-R16): the same TypeScript move, repaired with the grammar loaded and
DECLINED with it withdrawn, in one class so the two directions cannot drift apart.

:class:`PinnedDependencyTests` is the parser compatibility half: every claim ``grammars``
makes names an exact measured tree-sitter version, and the tests fail when the project's
installation bounds stop admitting it. R30's citation provenance rule is stricter and lives
in ``test_memory_citation_change_detection.py``: a permissive bound is never a resolved
historical version.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import AbstractContextManager
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.errors import GrammarUnavailableError
from agents_remember.memory_quality.style.citations import extents, grammars, model, symbol_index
from agents_remember.memory_quality.style.citations.resolution import Trees
from test_memory_citation_fix import TreeCase, filler
from tree_sitter import Node, Parser


def spans(path: str, source: str) -> dict[str, list[tuple[int, int]]]:
    return grammars.definitions(path, source.splitlines())


def names(path: str, source: str) -> set[str]:
    return set(spans(path, source))


def node_of(path: str, source: str, kind: str) -> Node:
    """The first ``kind`` node in ``source``, for asserting on a reader's floor."""
    grammar = grammars.grammar_of(path)
    assert grammar is not None
    tree = Parser(grammars.language(grammar)).parse(source.encode("utf-8"))
    return next(one for one in grammars._walk(tree.root_node) if one.type == kind)


class PinnedDependencyTests(unittest.TestCase):
    """Parser measurements remain compatible with declared and installed dependencies."""

    def setUp(self) -> None:
        declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
        self.pins = {
            name: bounds
            for name, _, bounds in (
                one.partition(">=") for one in declared["project"]["dependencies"]
            )
        }

    def bounds(self, package: str) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """The ``>=lower,<upper`` a dependency line declares, as comparable tuples."""
        self.assertIn(package, self.pins, f"{package} is claimed but not a declared dependency")
        lower, separator, upper = self.pins[package].partition(",<")
        self.assertEqual(separator, ",<", f"{package} carries no upper bound: {self.pins[package]}")
        return numbered(lower), numbered(upper)

    def test_every_measured_version_is_a_declared_dependency_within_its_pin(self) -> None:
        for package, measured in grammars.MEASURED_VERSIONS.items():
            with self.subTest(package=package):
                lower, upper = self.bounds(package)
                self.assertLessEqual(lower, numbered(measured))
                self.assertLess(numbered(measured), upper)

    def test_the_installed_version_is_the_one_the_claims_were_measured_against(self) -> None:
        """The installed parser stays inside the compatibility range measured here."""
        for package in grammars.MEASURED_VERSIONS:
            with self.subTest(package=package):
                lower, upper = self.bounds(package)
                self.assertLessEqual(lower, numbered(version(package)))
                self.assertLess(numbered(version(package)), upper)

    def test_every_grammar_this_check_declares_can_be_loaded(self) -> None:
        for grammar in sorted(set(grammars.SUFFIX_GRAMMARS.values())):
            with self.subTest(grammar=grammar):
                self.assertIsNotNone(grammars.language(grammar))


def numbered(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.strip().split("."))


class TypeScriptPureMoveTests(TreeCase):
    """L6-R16, both directions: the grammar is what turns this decline into a repair.

    One tree, one move. ``RailRow`` is DECLARED in one file and MENTIONED in another, and
    the citation points at the file it left. With the TypeScript grammar the mention is not
    a candidate, so the declaration resolves uniquely and ``--fix`` repoints the claim; with
    the grammar withdrawn both files are mentions, nothing resolves uniquely, and the claim
    is handed to the curator with both locations named.
    """

    def moved(self) -> None:
        self.tree.source(
            "dashboard/src/rail/RailRow.tsx",
            'import { useState } from "react";\n\n'
            "export class RailRow {\n  render() {\n    return null;\n  }\n}\n",
        )
        self.tree.source(
            "dashboard/src/panels/FlowTab.tsx",
            'import { RailRow } from "../rail/RailRow";\n\n'
            "export const FlowTab = () => new RailRow();\n",
        )
        self.tree.card(
            "dashboard/src/panels/FlowTab.tsx",
            "| The rail row owns its own render. | `RailRow` | dashboard/src/rail.tsx:1-4 |",
        )

    def withdrawn(self) -> AbstractContextManager[Any]:
        """The tree exactly as it was before this leaf: no grammar reads ``.tsx``."""
        remaining = {
            suffix: grammar
            for suffix, grammar in grammars.SUFFIX_GRAMMARS.items()
            if grammar not in {grammars.TSX, grammars.TYPESCRIPT, grammars.JAVASCRIPT}
        }
        return mock.patch.dict(grammars.SUFFIX_GRAMMARS, remaining, clear=True)

    def test_with_the_grammar_the_move_is_repaired_onto_the_declaration(self) -> None:
        self.moved()

        result = self.tree.fix()

        self.assertEqual(self.sources(result), "dashboard/src/rail/RailRow.tsx:3-7")
        self.assertEqual(result["declinedCount"], 0)
        self.assert_check_clean()

    def test_without_the_grammar_the_same_move_is_declined(self) -> None:
        self.moved()

        with self.withdrawn():
            result = self.tree.fix()

        self.assertEqual(result["claimsRepaired"], 0)
        self.assertEqual(self.declined(result)["code"], "anchor_ambiguous")

    def test_the_decline_without_the_grammar_names_both_mentioning_files(self) -> None:
        """The importing file is indistinguishable from the declaring one without a parse."""
        self.moved()

        with self.withdrawn():
            message = self.declined(self.tree.fix())["message"]

        self.assertIn("2 file(s) hold it", message)
        self.assertIn("dashboard/src/panels/FlowTab.tsx", message)
        self.assertIn("dashboard/src/rail/RailRow.tsx", message)

    def test_the_row_is_left_exactly_as_written_when_the_grammar_is_withdrawn(self) -> None:
        self.moved()

        with self.withdrawn():
            self.tree.fix()

        self.assertIn(
            "dashboard/src/rail.tsx:1-4", self.tree.row("dashboard/src/panels/FlowTab.tsx.md")
        )


class MentionNeverResolvesAMoveTests(TreeCase):
    """The safety rule the ``--fix`` probe bought, now carried by every parsed language."""

    def lone_mention(self, path: str, body: str, anchor: str) -> dict[str, object]:
        # `kernel/` has to exist for `kernel/gone.py` to read as a vanished source of ours
        # rather than as a citation into somebody else's tree, which is carried through.
        self.tree.source("kernel/present.py", "keep = 1\n")
        self.tree.source(path, body)
        self.tree.card("kernel/caller.py", f"| Gone. | `{anchor}` | kernel/gone.py:1-2 |")
        return self.tree.fix()

    def test_a_lone_typescript_mention_does_not_resolve_a_move(self) -> None:
        result = self.lone_mention(
            "dashboard/src/notes.ts", "// The RailRow path was removed.\n", "RailRow"
        )

        self.assertEqual(result["claimsRepaired"], 0)
        self.assertEqual(self.declined(result)["code"], "anchor_ambiguous")

    def test_a_lone_python_mention_still_does_not_resolve_a_move(self) -> None:
        result = self.lone_mention(
            "kernel/notes.py", '"""The `persist` path was removed."""\n', "persist"
        )

        self.assertEqual(result["claimsRepaired"], 0)

    def test_a_lone_mention_in_an_unparsed_language_still_resolves(self) -> None:
        """The ceiling, stated as behaviour: no grammar, so presence is all there is."""
        result = self.lone_mention("scripts/deploy.sh", "rail_row=1\n", "rail_row")

        self.assertEqual(self.sources(result), "scripts/deploy.sh:1-1")


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

    def test_an_async_def_is_a_definition_like_any_other(self) -> None:
        self.assertEqual(spans("k.py", "async def run():\n    return 1\n")["run"], [(1, 2)])

    def test_a_class_and_its_methods_and_attributes_all_bind(self) -> None:
        source = "class K:\n    attr = 1\n\n    def method(self):\n        local = 2\n"

        self.assertEqual(
            spans("k.py", source),
            {"K": [(1, 5)], "attr": [(2, 2)], "method": [(4, 5)], "local": [(5, 5)]},
        )

    def test_a_chained_assignment_binds_both_names(self) -> None:
        self.assertEqual(spans("k.py", "a = b = 1\n"), {"a": [(1, 1)], "b": [(1, 1)]})

    def test_a_nested_unpacking_target_binds_every_plain_name(self) -> None:
        self.assertEqual(set(spans("k.py", "(first,), second = call()\n")), {"first", "second"})

    def test_a_trailing_comment_inside_a_construct_belongs_to_it(self) -> None:
        source = "def build():\n    return 1\n    # why it returns 1\n\n\nx = 2\n"

        self.assertEqual(spans("k.py", source)["build"], [(1, 3)])

    def test_a_construct_that_does_not_parse_binds_nothing_and_its_file_survives(self) -> None:
        source = "line1 = 1\ndef persist(:\n"

        found = spans("k.py", source)

        self.assertNotIn("persist", found)
        self.assertEqual(found["line1"], [(1, 1)])

    def test_a_definition_that_ends_at_the_last_line_of_the_file_stops_there(self) -> None:
        self.assertEqual(spans("k.py", "def build():\n    return 1\n")["build"], [(1, 2)])


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

    def test_a_decorator_on_an_exported_class_is_part_of_the_construct(self) -> None:
        """The decorator is a SIBLING under ``export_statement``, not a child of the class."""
        source = "@Component({})\nexport class Decorated {}\n"

        self.assertEqual(spans("dashboard/x.ts", source)["Decorated"], [(1, 2)])

    def test_a_decorator_on_a_local_class_is_part_of_the_construct(self) -> None:
        source = "@Component({})\nclass Local {}\n"

        self.assertEqual(spans("dashboard/x.ts", source)["Local"], [(1, 2)])

    def test_destructuring_binds_the_bound_side_and_never_the_key_or_the_default(self) -> None:
        source = "const { p: renamed, plain, missing = fallback } = obj;\nconst [head] = list;\n"

        self.assertEqual(
            set(spans("dashboard/x.ts", source)), {"renamed", "plain", "missing", "head"}
        )

    def test_a_rest_element_binds_its_name(self) -> None:
        self.assertEqual(
            set(spans("dashboard/x.ts", "const [one, ...rest] = list;\n")), {"one", "rest"}
        )

    def test_a_tsx_component_is_read_by_the_tsx_dialect(self) -> None:
        source = 'export const Row = () => <div className="rail-row">text</div>;\n'

        self.assertEqual(spans("dashboard/x.tsx", source)["Row"], [(1, 1)])

    def test_a_javascript_module_is_read_by_the_javascript_grammar(self) -> None:
        for suffix in (".js", ".jsx", ".mjs", ".cjs"):
            with self.subTest(suffix=suffix):
                found = spans(f"dashboard/x{suffix}", "export function main() {}\n")
                self.assertEqual(found["main"], [(1, 1)])

    def test_the_typescript_dialect_reads_every_typescript_suffix(self) -> None:
        for suffix in (".ts", ".mts", ".cts"):
            with self.subTest(suffix=suffix):
                self.assertEqual(grammars.grammar_of(f"x{suffix}"), grammars.TYPESCRIPT)


class NotADefinitionTests(unittest.TestCase):
    """L6-R18: the known-good constructs this rule must not call a declaration.

    Each of these names a symbol that is DECLARED somewhere else. Calling one a definition
    would give the tiebreaker a second defining file and turn a repairable move into a
    refusal, or worse, repoint a claim at the file that merely imports the thing.
    """

    FIXTURES = (
        ("an import specifier", "import { RailRow } from './rail';\n"),
        ("a default import", "import RailRow from './rail';\n"),
        ("a re-export specifier", "export { RailRow } from './rail';\n"),
        ("an object literal key", "send({ RailRow: 1 });\n"),
        ("a call argument", "render(RailRow);\n"),
        ("a type reference", "let value: RailRow;\n"),
        ("a line comment", "// RailRow lives elsewhere now\n"),
        ("a string literal", "const name = 'RailRow';\n"),
    )

    def test_no_mention_of_a_symbol_is_read_as_a_declaration_of_it(self) -> None:
        for label, source in self.FIXTURES:
            with self.subTest(shape=label):
                self.assertNotIn("RailRow", names("dashboard/x.ts", source))

    def test_a_python_attribute_or_subscript_target_binds_nothing(self) -> None:
        self.assertEqual(spans("k.py", "holder.field = 1\nrows[0] = 2\n"), {})

    def test_a_jsx_attribute_is_not_a_declaration(self) -> None:
        source = "const el = <Row title={heading} />;\n"

        self.assertEqual(set(spans("dashboard/x.tsx", source)), {"el"})

    def test_a_reader_returns_nothing_for_a_node_that_binds_no_name(self) -> None:
        """Both readers take an OPTIONAL child and both recurse, so each needs a floor.

        No declarator shape in the pinned grammars reaches the script reader's floor. It
        exists so that a grammar which grows one degrades to occurrence matching for that
        construct rather than raising in the middle of a whole-tree walk.
        """
        self.assertEqual(grammars._python_targets(None), [])
        self.assertEqual(grammars._script_targets(None), [])
        self.assertEqual(
            grammars._script_targets(node_of("dashboard/x.ts", "const a = 1;", "number")), []
        )


class UnparsedLanguageTests(unittest.TestCase):
    """The stated ceiling: a language with no grammar binds nothing, and says so."""

    def test_a_suffix_with_no_grammar_binds_nothing(self) -> None:
        self.assertEqual(spans("dashboard/rail.css", ".rail-row { color: red; }\n"), {})

    def test_parsed_answers_for_the_languages_this_check_reads(self) -> None:
        self.assertTrue(grammars.parsed("kernel/store.py"))
        self.assertTrue(grammars.parsed("dashboard/PANEL.TSX"))
        self.assertFalse(grammars.parsed("docs/overview.md"))
        self.assertFalse(grammars.parsed("Makefile"))

    def test_the_extent_layer_asks_the_grammar_registry_rather_than_a_second_suffix_test(
        self,
    ) -> None:
        self.assertIs(extents.parsed, grammars.parsed)


class GrammarLoadingTests(unittest.TestCase):
    """A grammar that will not load is fatal, and never a quiet change of parser."""

    MISSING = ("no_such_tree_sitter_package", "language")

    def test_a_grammar_that_cannot_be_imported_raises_rather_than_falling_back(self) -> None:
        with (
            mock.patch.dict(grammars.GRAMMAR_PACKAGES, {"nonesuch": self.MISSING}),
            self.assertRaises(GrammarUnavailableError) as raised,
        ):
            grammars.language("nonesuch")

        self.assertIn("no-such-tree-sitter-package", str(raised.exception))
        self.assertIn("parses rather than guesses", str(raised.exception))

    def test_a_declared_language_whose_grammar_is_missing_fails_the_run(self) -> None:
        with (
            mock.patch.dict(grammars.SUFFIX_GRAMMARS, {".zz": "nonesuch"}),
            mock.patch.dict(grammars.GRAMMAR_PACKAGES, {"nonesuch": self.MISSING}),
            self.assertRaises(GrammarUnavailableError),
        ):
            spans("kernel/store.zz", "anything\n")

    def test_a_grammar_package_missing_its_entry_point_raises_the_same_way(self) -> None:
        with (
            mock.patch.dict(
                grammars.GRAMMAR_PACKAGES,
                {"nonesuch": ("tree_sitter_python", "no_such_attribute")},
            ),
            self.assertRaises(GrammarUnavailableError),
        ):
            grammars.language("nonesuch")

    def test_a_loaded_grammar_is_built_once_and_shared(self) -> None:
        self.assertIs(grammars.language(grammars.PYTHON), grammars.language(grammars.PYTHON))


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
    if not grammars.definitions(path, source.splitlines()):
        raise SystemExit(f"{path} parsed to nothing with egress blocked")
print("ok")
'''


class OfflineParseTests(unittest.TestCase):
    """The closeout gate runs where there is no egress, so the parse path must too.

    A subprocess because the parent already imported the grammars: in-process the load
    under test would be a module-cache hit that proves nothing about a cold start. The
    child blocks egress first and refuses to continue if the block did not take.
    """

    def test_every_grammar_loads_and_parses_with_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                [sys.executable, "-c", OFFLINE_PROBE],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(MCP_SRC),
                env={"PATH": "/usr/bin:/bin", "HOME": home, "PYTHONPATH": str(MCP_SRC)},
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ok")


class SymbolIndexLanguageTests(unittest.TestCase):
    """The tree-wide index, on the languages this leaf added to it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.code = self.root / "code"
        self.memory = self.root / "memory"
        for base in (self.code, self.memory):
            base.mkdir(parents=True)

    def write(self, relative: str, body: str) -> None:
        path = self.code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def sightings(self, name: str) -> symbol_index.Sightings:
        anchor = model.Anchor(kind=model.SYMBOL, text=name)
        trees = Trees(code_root=self.code, memory_root=self.memory)
        return symbol_index.locate((anchor,), trees)[anchor]

    def test_a_typescript_declaration_outranks_forty_typescript_callers(self) -> None:
        self.write("dashboard/rail/RailRow.tsx", "export class RailRow {\n  x = 1;\n}\n")
        for index in range(40):
            self.write(f"dashboard/use{index}.tsx", "const one = new RailRow();\n")

        found = self.sightings("RailRow")

        self.assertEqual(found.defining_files, 1)
        self.assertEqual(found.files, 41)
        assert found.unique is not None
        self.assertEqual(found.unique.written, "dashboard/rail/RailRow.tsx:1-3")

    def test_a_name_declared_in_two_typescript_files_resolves_nowhere(self) -> None:
        self.write("dashboard/left.ts", "export const emit = 1;\n")
        self.write("dashboard/right.ts", "export const emit = 2;\n")

        self.assertIsNone(self.sightings("emit").unique)

    def test_a_declaration_and_a_mention_in_two_languages_still_resolves(self) -> None:
        """A Python caller of a TypeScript name is a mention in a parsed file either way."""
        self.write("dashboard/rail.ts", "export const LIMIT = 20;\n")
        self.write("kernel/notes.py", '"""LIMIT is set on the rail."""\n')

        found = self.sightings("LIMIT")

        self.assertEqual(found.defining_files, 1)
        assert found.unique is not None
        self.assertEqual(found.unique.path, "dashboard/rail.ts")


class ExtentBoundaryTests(unittest.TestCase):
    """The two ways a construct's last line is spelled as a tree-sitter Point."""

    def test_a_construct_ending_mid_line_keeps_that_line(self) -> None:
        self.assertEqual(spans("k.py", "class K:\n    pass\nx = 1\n")["K"], [(1, 2)])

    def test_a_construct_ending_at_a_newline_stops_on_the_line_before(self) -> None:
        """A block that swallows its trailing newline reports column 0 of the NEXT row."""
        source = "class K:\n    def m(self):\n        pass\n"

        self.assertEqual(spans("k.py", source)["K"], [(1, 3)])

    def test_a_single_line_construct_never_reports_an_end_before_its_start(self) -> None:
        self.assertEqual(spans("dashboard/x.ts", "type Mode = 'a';\n")["Mode"], [(1, 1)])

    def test_every_whole_file_derivation_a_view_shares_is_built_once(self) -> None:
        """A batch asks one file about many anchors; each derivation costs one pass."""
        view = extents.FileView(path="docs/x.md", lines=["## Scoping", "prefixes win"])

        self.assertIs(view.words(), view.words())
        self.assertIs(view.headings(), view.headings())


class FallbackVolumeTests(TreeCase):
    """R18's other half: the fallback still works, and it is not disguised as a parse."""

    def test_an_unparsed_language_still_generates_a_range_from_occurrences(self) -> None:
        self.tree.source("config/app.toml", filler(4, marker="key") + "limit = 20\n")
        self.tree.card("kernel/caller.py", '| The cap. | "limit = 20" | config/app.toml:1-1 |')

        self.assertEqual(self.sources(self.tree.fix()), "config/app.toml:5-5")

    def test_an_occurrence_range_is_marked_as_an_occurrence_not_a_definition(self) -> None:
        found = extents.anchor_extents(
            model.Anchor(kind=model.SYMBOL, text="limit"), "config/app.toml", ["limit = 20"]
        )

        self.assertEqual([one.kind for one in found], [extents.OCCURRENCE])

    def test_the_per_file_entry_point_dispatches_every_anchor_kind(self) -> None:
        """``FileView`` is what a batch uses; this is the same three rules unbatched."""
        lines = ["## Scoping", "prefixes win"]
        kinds = [
            extents.anchor_extents(model.Anchor(kind=kind, text=text), "docs/x.md", lines)[0].kind
            for kind, text in (
                (model.HEADING, "## Scoping"),
                (model.QUOTE, "prefixes win"),
                (model.SYMBOL, "Scoping"),
            )
        ]

        self.assertEqual(kinds, [extents.SECTION, extents.QUOTED, extents.OCCURRENCE])

    def test_a_parsed_range_is_marked_as_a_definition(self) -> None:
        found = extents.anchor_extents(
            model.Anchor(kind=model.SYMBOL, text="limit"), "dashboard/x.ts", ["const limit = 20;"]
        )

        self.assertEqual([one.kind for one in found], [extents.DEFINITION])


class TypeScriptAnchorGrammarTests(unittest.TestCase):
    """R32 modes 1, 2, 3 and 5, plus the nearby shapes that stay negative."""

    def test_generic_and_call_spellings_resolve_to_their_exact_identifier(self) -> None:
        anchors, skipped = model.anchors_in(
            '`startCatalogPollDriver()`; `Pick<TaskDocNode, "id">`; '
            "`mirrorMustDeclare<ServedOnly extends never>()`"
        )

        self.assertEqual(
            [anchor.text for anchor in anchors],
            ["startCatalogPollDriver", "Pick", "mirrorMustDeclare"],
        )
        self.assertEqual(skipped, 0)

    def test_member_calls_and_broken_syntax_are_not_guessed_into_identifiers(self) -> None:
        anchors, skipped = model.anchors_in("`client.start()`; `unfinished(`; `broken(???)`")

        self.assertEqual(anchors, ())
        self.assertEqual(skipped, 3)

    def test_a_quote_spans_a_contiguous_typescript_line_comment_block(self) -> None:
        source = [
            "// State is owned by the reducer and",
            "// projected only after validation.",
            "export const ready = true;",
        ]
        anchor = model.Anchor(
            kind=model.QUOTE,
            text="State is owned by the reducer and projected only after validation.",
        )

        self.assertEqual(
            extents.anchor_extents(anchor, "state.ts", source),
            (extents.Extent(1, 2, extents.QUOTED),),
        )
        self.assertTrue(model.occurs_in(anchor, "\n".join(source[:2])))

    def test_a_quote_does_not_cross_between_separate_line_comment_blocks(self) -> None:
        source = ["// first half", "", "// second half"]
        anchor = model.Anchor(kind=model.QUOTE, text="first half second half")

        self.assertEqual(extents.anchor_extents(anchor, "state.ts", source), ())

    def test_a_url_does_not_lose_its_double_slash_during_quote_matching(self) -> None:
        anchor = model.Anchor(kind=model.QUOTE, text="https://example.invalid/a")

        self.assertTrue(model.occurs_in(anchor, 'const url = "https://example.invalid/a";'))

    def test_a_test_name_string_argument_widens_to_the_enclosing_call(self) -> None:
        source = [
            'it("keeps the selected row", () => {',
            "  expect(selected).toBe(row);",
            "});",
        ]
        anchor = model.Anchor(kind=model.QUOTE, text="keeps the selected row")

        self.assertEqual(
            extents.anchor_extents(anchor, "row.test.ts", source),
            (extents.Extent(1, 3, extents.CALL),),
        )

    def test_call_widening_retains_the_same_literal_inside_the_callback_body(self) -> None:
        source = [
            'it("same label", () => {',
            '  const unrelated = "same label";',
            "  consume(unrelated);",
            "});",
        ]
        anchor = model.Anchor(kind=model.QUOTE, text="same label")

        self.assertEqual(
            extents.anchor_extents(anchor, "row.test.ts", source),
            (
                extents.Extent(1, 4, extents.CALL),
                extents.Extent(2, 2, extents.QUOTED),
            ),
        )

    def test_call_widening_retains_a_same_line_duplicate_assignment(self) -> None:
        source = ['it("same label", () => {}); const unrelated = "same label";']
        anchor = model.Anchor(kind=model.QUOTE, text="same label")

        self.assertEqual(
            extents.anchor_extents(anchor, "row.test.ts", source),
            (
                extents.Extent(1, 1, extents.CALL),
                extents.Extent(1, 1, extents.QUOTED),
            ),
        )

    def test_a_string_that_is_not_a_call_argument_stays_on_its_literal_line(self) -> None:
        source = ['const label = "keeps the selected row";', "consume(label);"]
        anchor = model.Anchor(kind=model.QUOTE, text="keeps the selected row")

        self.assertEqual(
            extents.anchor_extents(anchor, "row.ts", source),
            (extents.Extent(1, 1, extents.QUOTED),),
        )

    def test_inner_quotes_round_trip_in_a_package_json_pin_anchor(self) -> None:
        anchors, skipped = model.anchors_in(r'"\"jsdom\": \"^25.0.1\""')

        self.assertEqual(skipped, 0)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0].text, '"jsdom": "^25.0.1"')
        self.assertEqual(anchors[0].written, r'"\"jsdom\": \"^25.0.1\""')


class TypeScriptInterfacePoolRepairTests(TreeCase):
    """R32 mode 4: pooled members repair to their defining interface file."""

    def test_three_interface_members_in_another_file_repair_as_one_claim(self) -> None:
        self.tree.source("dashboard/src/panels/old.ts", "export const keep = true;\n")
        self.tree.source(
            "dashboard/src/types/panel.ts",
            "export interface PanelProps {\n"
            "  title: string;\n"
            "  onSelect(): void;\n"
            "  disabled?: boolean;\n"
            "}\n",
        )
        self.tree.card(
            "dashboard/src/panels/view.tsx",
            "| Panel inputs are shared. | `title`; `onSelect`; `disabled` "
            "| dashboard/src/panels/gone.ts:1-4 |",
        )

        result = self.tree.fix()

        self.assertEqual(result["claimsRepaired"], 1, result["declined"])
        self.assertEqual(result["declinedCount"], 0, result["declined"])
        self.assertEqual(
            self.sources(result),
            "dashboard/src/types/panel.ts:2-4",
        )


class PackageJsonQuotedPinTests(TreeCase):
    """R32 mode 5 through the checker, not only the anchor tokenizer."""

    def test_an_escaped_verbatim_pin_is_one_anchor_and_satisfies_its_range(self) -> None:
        self.tree.source(
            "dashboard/package.json",
            '{\n  "devDependencies": {\n    "jsdom": "^25.0.1"\n  }\n}\n',
        )
        self.tree.card(
            "dashboard/src/test/setup.ts",
            r'| The DOM contract is versioned. | "\"jsdom\": \"^25.0.1\"" '
            "| dashboard/package.json:3-3 |",
        )

        self.assert_check_clean()


if __name__ == "__main__":
    unittest.main()
