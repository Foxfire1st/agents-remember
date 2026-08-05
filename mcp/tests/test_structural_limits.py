"""Tests for the function, class-surface, and directory structural caps.

Repository tests arm all three caps and validate the bounded ``layers.toml`` sequencing
declaration. Synthetic bites require complete offender lists. Known-good fixtures pin
decorators, overloads, properties, protocols, nested scopes, relocated-method
classification, external receivers, and exact limit boundaries.
"""

from __future__ import annotations

import ast
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

import pytest
from agents_remember.code_quality import structural_limits

pytestmark = pytest.mark.fitness

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = MCP_SRC / "agents_remember"
LAYERS_PATH = REPOSITORY_ROOT / structural_limits.LAYERS_FILE

FUNCTION_REMEDY = (
    "extract the cohesive steps into named helpers, or hoist a nested def to module "
    "level. A flat declaration block splits by grouping its declarations."
)
CLASS_REMEDY = (
    "the class has taken a second job; move the methods that belong to it into a "
    "collaborator, or make internal steps private. Moving them into a base class it "
    "inherits is not a split -- the surface is unchanged and only the measurement moves -- "
    "and moving them into a sibling module as free functions over the instance is not a "
    "split either; those are counted here as the methods they are. "
    "A sequencing deviation, if one is genuinely warranted, is declared in "
    f"{structural_limits.LAYERS_FILE} for the whole DIRECTORY under "
    f"[{structural_limits.SEQUENCING_TABLE}.<name>] with an owner, a date, the caps it "
    "departs from and the leaf that deletes it -- never for a named class, and never as "
    "an entry in this test."
)
DIRECTORY_REMEDY = (
    "split the directory into sub-packages. A sequencing deviation, if one is genuinely "
    f"warranted, is declared in {structural_limits.LAYERS_FILE} under "
    f"[{structural_limits.SEQUENCING_TABLE}.<name>] with an owner, a date, the caps it "
    "departs from and the leaf that deletes it -- never as an entry in this test."
)


# One well-formed `[sequencing.*]` entry, as TOML source. The declaration tests each remove
# or corrupt exactly one field of it, so "what a complete deviation looks like" is written
# once and every negative case is visibly a single deviation from it.
WELL_FORMED_DEVIATION = {
    "directory": '"drawer/"',
    "limits": '["directory_modules"]',
    "declared_on": '"2026-08-01"',
    "owner": '"someone"',
    "deleted_by": '"260731-EFA-L12"',
}


def declared_deviations() -> list[structural_limits.DirectoryDeviation]:
    return structural_limits.read_directory_deviations(LAYERS_PATH)


def deviation(directory: str, *limits: str) -> structural_limits.DirectoryDeviation:
    """A complete deviation for a fixture package, so no test builds a half-formed one."""
    return structural_limits.DirectoryDeviation(
        name=f"{directory}_size",
        directory=directory,
        declared_on="2026-08-01",
        owner="someone",
        deleted_by="260731-EFA-L12",
        limits=limits or (structural_limits.DIRECTORY_MODULES,),
    )


def write_package(root: Path, modules: dict[str, str]) -> Path:
    """A throwaway package shaped like this one, from ``{relative path: source}``."""
    package = root / "sample_package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    for relative, source in modules.items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source), encoding="utf-8")
    return package


def function_of_length(name: str, body_lines: int) -> str:
    """A function whose measured length is exactly ``body_lines`` + 1."""
    body = "\n".join(f"    value_{index} = {index}" for index in range(body_lines))
    return f"def {name}() -> None:\n{body}\n"


def class_with_public_methods(name: str, count: int) -> str:
    methods = "\n".join(
        f"    def method_{index}(self) -> int:\n        return {index}\n" for index in range(count)
    )
    return f"class {name}:\n{methods}\n"


def relocated_parser(*, methods: int, steps: int, private_steps: bool = False) -> dict[str, str]:
    """A state machine and a sibling module of free functions that drive its cursor.

    The shape 260731-EFA-L6 built to get ``_MarkdownSettingsParser`` from 31 public methods
    under the cap, and then deleted: each step takes the parser as an UNANNOTATED first
    parameter and writes the same two cursor fields the methods wrote. ``private_steps``
    spells the sibling functions with a leading underscore, which is the same honest
    remedy as a ``_``-prefixed method.
    """
    prefix = "_" if private_steps else ""
    body = "\n".join(
        f"    def method_{index}(self) -> None:\n        self.current_rule = {index}\n"
        for index in range(methods)
    )
    sibling = "\n".join(
        f"def {prefix}step_{index}(parser, line):\n"
        f"    parser.current_rule = line\n"
        f"    parser.current_list = None\n"
        for index in range(steps)
    )
    return {
        "parser.py": f"class Parser:\n    current_rule = None\n    current_list = None\n\n{body}",
        "parser_steps.py": sibling,
    }


class FunctionLengthTests(unittest.TestCase):
    """No function in the package may exceed the measured cap."""

    def test_no_function_in_the_package_exceeds_the_line_limit(self) -> None:
        offenders = structural_limits.long_functions(PACKAGE_ROOT)

        self.assertEqual(
            offenders,
            [],
            structural_limits.render_offenders("function(s)", offenders, FUNCTION_REMEDY),
        )

    def test_the_limit_is_the_measured_one_and_not_a_number_that_fits_the_tree(self) -> None:
        # A cap is only meaningful against the distribution it was drawn from. Loosening
        # The cap is itself part of the enforced contract.
        self.assertEqual(structural_limits.FUNCTION_LINE_LIMIT, 100)


class ClassSurfaceTests(unittest.TestCase):
    """A class's public surface is its declared, non-underscore method names."""

    def test_no_class_in_the_package_exceeds_the_surface_limit_without_a_declared_deviation(
        self,
    ) -> None:
        offenders = structural_limits.wide_classes(PACKAGE_ROOT, deviations=declared_deviations())

        self.assertEqual(
            offenders,
            [],
            structural_limits.render_offenders("class(es)", offenders, CLASS_REMEDY),
        )

    def test_the_limit_is_the_measured_one(self) -> None:
        self.assertEqual(structural_limits.CLASS_PUBLIC_METHOD_LIMIT, 15)

    def test_a_wide_class_is_reported_with_its_measured_surface(self) -> None:
        source = class_with_public_methods("Wide", 16)

        offenders = [
            offender
            for offender in structural_limits.measure_classes(source, display_path="wide.py")
            if offender.measured > structural_limits.CLASS_PUBLIC_METHOD_LIMIT
        ]

        self.assertEqual([offender.name for offender in offenders], ["Wide"])
        self.assertEqual(offenders[0].measured, 16)
        self.assertEqual(offenders[0].excess, 1)


class RelocationTests(unittest.TestCase):
    """Moving a method to the next file does not remove it from the class's surface.

    A cap satisfiable by relocation teaches relocation, and this leaf demonstrated it: the
    first attempt at ``_MarkdownSettingsParser`` moved fifteen methods into two sibling
    modules as ``def step(parser, ...)``, which lowered the measured number to 13 and
    changed nothing about the class -- the same names, the same cursor fields, the same
    single caller, and pyright checking ``Any`` where it had checked the parser. Measured
    by the rule below, that tree reports the parser at 22.
    """

    def test_moving_methods_into_a_sibling_module_does_not_lower_the_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = write_package(Path(tmp), relocated_parser(methods=8, steps=10))

            offenders = structural_limits.wide_classes(package)
            message = structural_limits.render_offenders("class(es)", offenders, CLASS_REMEDY)

            self.assertEqual([offender.name for offender in offenders], ["Parser"])
            self.assertEqual(offenders[0].measured, 18)
            self.assertIn("parser.py:1 Parser", message)

    def test_the_same_class_without_the_relocated_steps_is_within_the_cap(self) -> None:
        # The other half of the previous assertion: the eight methods on the class are not
        # what makes it wide, so the ten in the sibling module are what the cap caught.
        with tempfile.TemporaryDirectory() as tmp:
            package = write_package(Path(tmp), relocated_parser(methods=8, steps=0))

            self.assertEqual(structural_limits.wide_classes(package), [])

    def test_making_the_relocated_steps_private_is_a_real_remedy(self) -> None:
        # A `_`-prefixed module function is no more public surface than a `_`-prefixed
        # method, so the remedy the cap sanctions still works after the rule is added.
        with tempfile.TemporaryDirectory() as tmp:
            package = write_package(
                Path(tmp), relocated_parser(methods=8, steps=10, private_steps=True)
            )

            self.assertEqual(structural_limits.wide_classes(package), [])

    def test_an_annotated_receiver_is_charged_to_the_class_it_names(self) -> None:
        sources = [
            ("row.py", "class Row:\n    status = ''\n"),
            ("steps.py", "def approve(row: Row) -> None:\n    row.status = 'approved'\n"),
        ]

        self.assertEqual(
            structural_limits.relocated_surface(sources), {("row.py", "Row"): {"approve"}}
        )

    def test_a_relocated_step_sharing_a_method_name_costs_one_member(self) -> None:
        # Counted as a union of NAMES, like a property and its setter: a thin method that
        # forwards to a module function of the same name is one member, not two.
        sources = [
            ("row.py", "class Row:\n    def approve(self):\n        self.status = ''\n"),
            ("steps.py", "def approve(row) -> None:\n    row.status = 'approved'\n"),
        ]
        relocated = structural_limits.relocated_surface(sources)

        measured = structural_limits.measure_classes(
            sources[0][1], display_path="row.py", relocated=relocated
        )

        self.assertEqual(measured[0].measured, 1)

    def test_a_function_matching_two_classes_is_charged_to_both(self) -> None:
        # The ambiguous case, decided towards the cap. Annotating the parameter is what
        # narrows it back to one, and that is the fix the docstring names.
        sources = [
            ("a.py", "class First:\n    status = ''\n"),
            ("b.py", "class Second:\n    status = ''\n"),
            ("steps.py", "def approve(row) -> None:\n    row.status = 'x'\n"),
        ]

        self.assertEqual(
            structural_limits.relocated_surface(sources),
            {("a.py", "First"): {"approve"}, ("b.py", "Second"): {"approve"}},
        )

    def test_a_quoted_forward_reference_names_its_class_as_plainly_as_a_bare_one(self) -> None:
        """A quoted receiver annotation charges exactly the named class."""
        sources = [
            ("a.py", "class First:\n    status = ''\n"),
            ("b.py", "class Second:\n    status = ''\n"),
            (
                "steps.py",
                "from __future__ import annotations\n"
                "def approve(row: 'First') -> None:\n"
                "    row.status = 'x'\n",
            ),
        ]

        self.assertEqual(
            structural_limits.relocated_surface(sources), {("a.py", "First"): {"approve"}}
        )


class DirectorySizeTests(unittest.TestCase):
    """A directory holds a bounded number of modules, or the contract says why not."""

    def test_no_directory_exceeds_the_module_limit_without_a_declared_deviation(self) -> None:
        offenders = structural_limits.crowded_directories(
            PACKAGE_ROOT, deviations=declared_deviations()
        )

        self.assertEqual(
            offenders,
            [],
            structural_limits.render_offenders("director(ies)", offenders, DIRECTORY_REMEDY),
        )

    def test_the_limit_is_the_measured_one(self) -> None:
        self.assertEqual(structural_limits.DIRECTORY_MODULE_LIMIT, 25)


class DeclaredDeviationTests(unittest.TestCase):
    """Keep the sequencing register bounded, owned, scoped, and non-stale."""

    def test_every_declared_deviation_is_still_needed_for_every_cap_it_names(self) -> None:
        # Each declared cap must still be exceeded; otherwise the entry must be narrowed
        # or deleted.
        stale = structural_limits.stale_deviations(PACKAGE_ROOT, declared_deviations())

        self.assertEqual(
            [entry.describe() for entry in stale],
            [],
            "a declared sequencing deviation names a cap the tree now meets; narrow or "
            f"delete the entry in {structural_limits.LAYERS_FILE} rather than re-dating it",
        )

    def test_the_sequencing_register_is_bounded_and_speaks_the_closed_vocabulary(self) -> None:
        """Allow at most one actionable entry using the measured cap vocabulary."""
        self.assertTrue(
            LAYERS_PATH.is_file(),
            f"{LAYERS_PATH} is missing; every assertion below would pass vacuously because "
            "read_directory_deviations answers 'no deviation declared' for a file it "
            "cannot find",
        )

        # Parses, or raises: a shipped entry with no owner, no date, no deleter or no
        # limits never reaches the assertions below.
        deviations = declared_deviations()

        self.assertLessEqual(
            len(deviations),
            1,
            "the sequencing register has grown a second entry. One package waiting on one "
            "restructure is a sequencing decision; two are a list of known violations, "
            f"which 260731-EFA-L6 R12 forbids: {[entry.describe() for entry in deviations]}",
        )
        for entry in deviations:
            with self.subTest(deviation=entry.name):
                self.assertLessEqual(
                    set(entry.limits),
                    set(structural_limits.SEQUENCING_LIMITS),
                    f"{entry.describe()} departs from a cap nothing measures",
                )
                self.assertTrue(
                    (PACKAGE_ROOT / entry.directory).is_dir(),
                    f"{entry.describe()} names a directory that is not in the package; it "
                    "excuses nothing and the cap it claims to cover is unguarded",
                )

    def test_a_deviation_is_scoped_to_a_directory_and_can_never_name_a_class(self) -> None:
        # No field permits construct-level exceptions.
        self.assertNotIn("class", structural_limits.SEQUENCING_FIELDS)
        self.assertEqual(
            set(structural_limits.DirectoryDeviation.__dataclass_fields__),
            {"name", "directory", "declared_on", "owner", "deleted_by", "limits"},
        )


class DeviationDeclarationTests(unittest.TestCase):
    """A deviation with no owner cannot be honoured -- that is what an allowlist is."""

    def write_layers(self, root: Path, body: str) -> Path:
        path = root / structural_limits.LAYERS_FILE
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_a_deviation_must_name_a_directory_an_owner_a_date_and_a_deleter(self) -> None:
        for missing in structural_limits.SEQUENCING_FIELDS[1:]:
            fields = dict(WELL_FORMED_DEVIATION)
            del fields[missing]
            lines = "\n".join(f"{key} = {value}" for key, value in fields.items())
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                path = self.write_layers(Path(tmp), f"[sequencing.drawer]\n{lines}\n")

                with self.assertRaises(structural_limits.DeclarationError) as raised:
                    structural_limits.read_directory_deviations(path)

                self.assertIn(missing, str(raised.exception))

    def test_a_deviation_must_name_the_caps_it_departs_from(self) -> None:
        # Without this an entry excuses every cap, including caps written after it -- a
        # standing exemption rather than a decision about a known departure.
        fields = {key: value for key, value in WELL_FORMED_DEVIATION.items() if key != "limits"}
        lines = "\n".join(f"{key} = {value}" for key, value in fields.items())
        for body in (lines, f"{lines}\nlimits = []"):
            with self.subTest(limits=body.endswith("[]")), tempfile.TemporaryDirectory() as tmp:
                path = self.write_layers(Path(tmp), f"[sequencing.drawer]\n{body}\n")

                with self.assertRaises(structural_limits.DeclarationError) as raised:
                    structural_limits.read_directory_deviations(path)

                self.assertIn("limits", str(raised.exception))

    def test_a_deviation_cannot_name_a_cap_nothing_measures(self) -> None:
        # A closed vocabulary. "everything", or a cap invented in the entry itself, is how
        # a scoped decision turns into a blanket one.
        fields = dict(WELL_FORMED_DEVIATION) | {"limits": '["whatever_fails"]'}
        lines = "\n".join(f"{key} = {value}" for key, value in fields.items())
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_layers(Path(tmp), f"[sequencing.drawer]\n{lines}\n")

            with self.assertRaises(structural_limits.DeclarationError) as raised:
                structural_limits.read_directory_deviations(path)

            self.assertIn("whatever_fails", str(raised.exception))

    def test_a_blank_field_is_no_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_layers(
                Path(tmp),
                """
                [sequencing.drawer]
                directory = "drawer/"
                limits = ["directory_modules"]
                declared_on = "2026-08-01"
                owner = "   "
                deleted_by = "260731-EFA-L12"
                """,
            )

            with self.assertRaises(structural_limits.DeclarationError):
                structural_limits.read_directory_deviations(path)

    def test_a_sequencing_entry_about_something_other_than_a_directory_is_passed_over(
        self,
    ) -> None:
        # `[sequencing]` is the contract's general register; only entries naming a
        # directory are this check's business, and the rest must not raise.
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_layers(
                Path(tmp),
                """
                [sequencing.some_other_thing]
                statement = "not about a directory at all"
                """,
            )

            self.assertEqual(structural_limits.read_directory_deviations(path), [])

    def test_a_sequencing_value_that_is_not_a_table_is_passed_over(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_layers(Path(tmp), '[sequencing]\nnote = "a bare string"\n')

            self.assertEqual(structural_limits.read_directory_deviations(path), [])

    def test_a_sequencing_table_that_is_not_a_table_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_layers(Path(tmp), 'sequencing = "not a table"\n')

            with self.assertRaises(structural_limits.DeclarationError):
                structural_limits.read_directory_deviations(path)

    def test_a_missing_contract_declares_no_deviation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / structural_limits.LAYERS_FILE

            self.assertEqual(structural_limits.read_directory_deviations(absent), [])

    def test_a_deviation_naming_a_directory_within_the_cap_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = write_package(root, {"small/__init__.py": "", "small/one.py": ""})
            declared = deviation("small", structural_limits.DIRECTORY_MODULES)

            stale = structural_limits.stale_deviations(package, [declared])

            self.assertEqual([entry.deviation.directory for entry in stale], ["small"])
            self.assertEqual(stale[0].cleared, (structural_limits.DIRECTORY_MODULES,))
            self.assertIn("small/", declared.describe())

    def test_a_deviation_is_stale_the_moment_ONE_of_its_caps_clears(self) -> None:
        # The property that makes a two-cap entry safe, and the one that fired for real:
        # `serving/`'s entry named the module count and the class cap, the class cap
        # cleared first (260731-EFA-L6 split the six), and the build failed on the half
        # that had gone rather than surviving on the half that had not.
        with tempfile.TemporaryDirectory() as tmp:
            package = write_package(
                Path(tmp),
                {f"drawer/module_{index}.py": "" for index in range(40)}
                | {"drawer/wide.py": class_with_public_methods("StillWide", 20)},
            )
            declared = deviation(
                "drawer", structural_limits.DIRECTORY_MODULES, structural_limits.CLASS_SURFACE
            )

            self.assertEqual(structural_limits.stale_deviations(package, [declared]), [])

            # The wide class is split; the module count is untouched. Half the entry's
            # justification is gone, and that is enough.
            (package / "drawer" / "wide.py").write_text("", encoding="utf-8")
            stale = structural_limits.stale_deviations(package, [declared])

            self.assertEqual(
                [entry.cleared for entry in stale], [(structural_limits.CLASS_SURFACE,)]
            )
            self.assertIn("no longer departs from class_surface", stale[0].describe())


class KnownGoodConstructTests(unittest.TestCase):
    """Constructs this repository contains that the checks must never flag.

    Every entry here is a shape that a naive implementation gets wrong, and each is named
    in ``structural_limits``'s own docstring so the false-positive reasoning travels with
    the checker rather than living only in its tests.
    """

    def public_names(self, source: str) -> set[str]:
        """The public surface of the fixture's one TOP-LEVEL class.

        Top-level rather than "the only class in the file": a nested-class fixture has two,
        and charging the inner one's methods to the outer is the very thing being ruled out.
        """
        tree = ast.parse(textwrap.dedent(source))
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        self.assertEqual(len(classes), 1, "fixture must declare exactly one top-level class")
        return structural_limits.public_method_names(classes[0])

    def measure(self, source: str) -> dict[str, int]:
        measured = structural_limits.measure_functions(
            textwrap.dedent(source), display_path="fixture.py"
        )
        return {offender.name: offender.measured for offender in measured}

    def test_decorators_are_not_counted_in_function_length(self) -> None:
        # CPython puts `lineno` on the `def`, not on the first decorator. A short function
        # under a stack of decorators -- every `@server.tool()` in the registration modules,
        # every FastAPI route -- must measure its body.
        decorators = "\n".join(f"@decorator_{index}" for index in range(30))
        source = f"{decorators}\ndef short() -> int:\n    return 1\n"

        self.assertEqual(self.measure(source)["short"], 2)

    def test_a_long_signature_is_measured_but_a_long_call_above_it_is_not(self) -> None:
        # The published MCP tool declarations carry 16-parameter signatures because the
        # signature IS the wire schema. They are part of the function and counted; nothing
        # above the `def` is.
        parameters = ",\n".join(f"    value_{index}: int = {index}" for index in range(16))
        entries = ",\n".join(f"    {index}" for index in range(16))
        source = f"CONSTANT = [\n{entries}\n]\n\n\ndef wide(\n{parameters},\n) -> None:\n    pass\n"

        self.assertEqual(self.measure(source)["wide"], 19)

    def test_a_property_and_its_setter_count_once(self) -> None:
        names = self.public_names(
            """
            class Subject:
                @property
                def value(self) -> int:
                    return self._value

                @value.setter
                def value(self, incoming: int) -> None:
                    self._value = incoming
            """
        )

        self.assertEqual(names, {"value"})

    def test_a_singledispatchmethod_and_its_registrations_count_once(self) -> None:
        names = self.public_names(
            """
            class Subject:
                @singledispatchmethod
                def render(self, value: object) -> str:
                    return str(value)

                @render.register
                def _(self, value: int) -> str:
                    return "int"

                @render.register
                def render_bytes(self, value: bytes) -> str:
                    return "bytes"
            """
        )

        # `render` and the explicitly named registration; the `_` overload is private.
        self.assertEqual(names, {"render", "render_bytes"})

    def test_typing_overloads_count_once(self) -> None:
        names = self.public_names(
            """
            class Subject:
                @overload
                def read(self, key: str) -> str: ...

                @overload
                def read(self, key: int) -> int: ...

                def read(self, key: str | int) -> str | int:
                    return key
            """
        )

        self.assertEqual(names, {"read"})

    def test_a_typing_module_qualified_overload_counts_once(self) -> None:
        names = self.public_names(
            """
            class Subject:
                @typing.overload
                def read(self, key: str) -> str: ...

                def read(self, key: str) -> str:
                    return key
            """
        )

        self.assertEqual(names, {"read"})

    def test_a_call_decorated_method_is_ordinary_public_surface(self) -> None:
        # `@field_validator("x")` and `@app.get(...)` are Call nodes rather than a bare
        # name. They are not overloads and must not be mistaken for one, which is what the
        # decorator reader's fall-through arm decides.
        names = self.public_names(
            """
            class Subject:
                @field_validator("value")
                def check(cls, value: int) -> int:
                    return value

                @app.get("/thing")
                def route(self) -> None: ...
            """
        )

        self.assertEqual(names, {"check", "route"})

    def test_nested_class_methods_do_not_charge_the_outer_class(self) -> None:
        names = self.public_names(
            """
            class Outer:
                class Config:
                    def one(self) -> None: ...
                    def two(self) -> None: ...
                    def three(self) -> None: ...

                def only(self) -> None: ...
            """
        )

        self.assertEqual(names, {"only"})

    def test_a_closure_inside_a_method_is_not_a_method(self) -> None:
        names = self.public_names(
            """
            class Subject:
                def run(self) -> int:
                    def helper() -> int:
                        return 1

                    return helper()
            """
        )

        self.assertEqual(names, {"run"})

    def test_a_protocol_of_read_only_fields_is_a_record_not_a_wide_class(self) -> None:
        # `controlplane/seats.py::SeatRow` is this shape at sixteen members. It declares a
        # record someone else satisfies; it has no behaviour to have taken a second job
        # with. The same record as a dataclass measures zero because its fields are
        # annotations, and a COVARIANT protocol cannot use annotations -- `status: str` is
        # read-write, hence invariant, and would reject a row whose status is a narrow
        # Literal. The property spelling is forced, so counting it would measure spelling.
        fields = "\n".join(
            f"    @property\n    def field_{index}(self) -> str: ...\n" for index in range(20)
        )

        self.assertEqual(self.public_names(f"class Row(Protocol):\n{fields}"), set())

    def test_a_protocols_operations_are_still_counted(self) -> None:
        # The fold is for declared FIELDS only. A port protocol with many operations is a
        # fat interface and exactly what this cap should see --
        # `serving/harness_control_adapter.py::HarnessProtocolAdapter` has eleven.
        names = self.public_names(
            """
            class Port(Protocol):
                @property
                def name(self) -> str: ...

                def start(self) -> None: ...
                def stop(self) -> None: ...
            """
        )

        self.assertEqual(names, {"start", "stop"})

    def test_a_property_with_a_body_is_counted_even_on_a_protocol(self) -> None:
        # Only a STUB is a declaration. A protocol member that computes something is code,
        # and the fold must not reach it.
        names = self.public_names(
            """
            class Row(Protocol):
                @property
                def declared(self) -> str: ...

                @property
                def computed(self) -> str:
                    return self.declared.upper()
            """
        )

        self.assertEqual(names, {"computed"})

    def test_a_read_only_property_outside_a_protocol_is_ordinary_surface(self) -> None:
        # Nothing about `@property` alone is exempt. A concrete class with twenty computed
        # accessors is wide, and the `...` body of an abstract base is still an operation
        # it means to be implemented.
        names = self.public_names(
            """
            class Concrete:
                @property
                def one(self) -> int: ...

                @property
                def two(self) -> int: ...
            """
        )

        self.assertEqual(names, {"one", "two"})

    def test_a_module_qualified_protocol_base_is_recognised(self) -> None:
        source = textwrap.dedent(
            """
            class Row(typing.Protocol):
                @property
                def only(self) -> str: ...
            """
        )
        node = ast.parse(source).body[0]
        assert isinstance(node, ast.ClassDef)

        self.assertTrue(structural_limits.is_protocol(node))
        self.assertEqual(structural_limits.public_method_names(node), set())

    def test_private_helpers_are_not_public_surface_however_many_there_are(self) -> None:
        methods = "\n".join(f"    def _step_{index}(self) -> None: ...\n" for index in range(40))

        self.assertEqual(self.public_names(f"class Parser:\n{methods}"), set())

    def test_a_dunder_is_not_public_surface(self) -> None:
        names = self.public_names(
            """
            class Subject:
                def __init__(self) -> None: ...
                def __enter__(self) -> "Subject": ...
                def __exit__(self, *info: object) -> None: ...
                def open(self) -> None: ...
            """
        )

        self.assertEqual(names, {"open"})

    def test_methods_behind_a_class_body_conditional_are_counted(self) -> None:
        # The one place a "direct children of the class body" reading under-reports. This
        # is a false NEGATIVE rather than a false positive, and the walk closes it.
        names = self.public_names(
            """
            class Subject:
                if sys.platform == "win32":
                    def windows_only(self) -> None: ...
                else:
                    def posix_only(self) -> None: ...

                try:
                    def optimistic(self) -> None: ...
                except ImportError:
                    def fallback(self) -> None: ...
                finally:
                    def always(self) -> None: ...
            """
        )

        self.assertEqual(names, {"windows_only", "posix_only", "optimistic", "fallback", "always"})

    def test_a_function_that_only_reads_its_parameter_is_not_a_relocated_method(self) -> None:
        # The line between a relocated method and an ordinary collaborator. A renderer, a
        # predicate or a projection over a row is not part of the row's surface, and this
        # package is full of them; only an ASSIGNMENT to the parameter's attributes counts.
        sources = [
            ("row.py", "class Row:\n    status = ''\n    name = ''\n"),
            (
                "views.py",
                "def render(row) -> str:\n    return f'{row.name}: {row.status}'\n\n\n"
                "def is_open(row) -> bool:\n    return row.status == 'open'\n",
            ),
        ]

        self.assertEqual(structural_limits.relocated_surface(sources), {})

    def test_a_parameter_typed_outside_the_package_is_not_a_relocated_method(self) -> None:
        # `providers/lifecycle/cli.py` normalises five `argparse.Namespace` objects in
        # place. A class this package does not define has no surface here to charge.
        sources = [
            ("row.py", "class Row:\n    coordination_root = None\n"),
            (
                "cli.py",
                "def normalize(args: Namespace, root) -> None:\n"
                "    args.coordination_root = root\n",
            ),
        ]

        self.assertEqual(structural_limits.relocated_surface(sources), {})

    def test_a_factory_that_assigns_to_something_it_made_is_not_a_relocated_method(
        self,
    ) -> None:
        # The first parameter is the receiver; a local built inside the function is not.
        sources = [
            ("row.py", "class Row:\n    status = ''\n"),
            (
                "build.py",
                "def build(status: str) -> Row:\n    row = Row()\n    row.status = status\n"
                "    return row\n",
            ),
        ]

        self.assertEqual(structural_limits.relocated_surface(sources), {})

    def test_a_member_with_no_parameters_does_not_truncate_the_class_fingerprint(self) -> None:
        """A zero-argument member does not hide later class fields from the fingerprint."""
        sources = [
            (
                "row.py",
                "class Row:\n"
                "    @staticmethod\n"
                "    def make():\n"
                "        return Row()\n"
                "\n"
                "    status = ''\n",
            ),
            ("steps.py", "def approve(row) -> None:\n    row.status = 'approved'\n"),
        ]

        self.assertEqual(
            structural_limits.relocated_surface(sources), {("row.py", "Row"): {"approve"}}
        )

    def test_a_sub_package_is_counted_in_its_own_right(self) -> None:
        # Nesting is the remedy for a crowded directory, so a module in a sub-package must
        # not still count against its parent.
        with tempfile.TemporaryDirectory() as tmp:
            package = write_package(
                Path(tmp),
                {f"child/module_{index}.py": "" for index in range(30)} | {"child/__init__.py": ""},
            )

            counts = structural_limits.module_counts(package)

            self.assertEqual(counts["."], 1)
            self.assertEqual(counts["child"], 31)

    def test_a_one_line_function_measures_one(self) -> None:
        self.assertEqual(self.measure("def tiny() -> None: ...\n")["tiny"], 1)

    def test_a_function_exactly_at_the_limit_is_not_an_offender(self) -> None:
        # Off-by-one on a cap is the difference between a check and a nuisance.
        with tempfile.TemporaryDirectory() as tmp:
            limit = structural_limits.FUNCTION_LINE_LIMIT
            package = write_package(
                Path(tmp), {"at_limit.py": function_of_length("at_limit", limit - 1)}
            )

            self.assertEqual(structural_limits.long_functions(package), [])

    def test_a_class_exactly_at_the_limit_is_not_an_offender(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            limit = structural_limits.CLASS_PUBLIC_METHOD_LIMIT
            package = write_package(
                Path(tmp), {"at_limit.py": class_with_public_methods("AtLimit", limit)}
            )

            self.assertEqual(structural_limits.wide_classes(package), [])

    def test_a_directory_exactly_at_the_limit_is_not_an_offender(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            limit = structural_limits.DIRECTORY_MODULE_LIMIT
            package = write_package(
                Path(tmp), {f"module_{index}.py": "" for index in range(limit - 1)}
            )

            self.assertEqual(structural_limits.crowded_directories(package), [])


class ProbeTests(unittest.TestCase):
    """Each check, shown rejecting a deliberate violation (R16).

    A check that has never rejected anything is indistinguishable from one that cannot, and
    a check that reports one offender at a time turns a batch fix into an iteration loop.
    Both properties are asserted here against a throwaway package.
    """

    def test_the_function_length_check_reports_every_offender_not_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            over = structural_limits.FUNCTION_LINE_LIMIT + 20
            package = write_package(
                Path(tmp),
                {
                    "first.py": function_of_length("first_offender", over),
                    "second.py": function_of_length("second_offender", over + 10),
                    "innocent.py": function_of_length("innocent", 3),
                },
            )

            offenders = structural_limits.long_functions(package)
            message = structural_limits.render_offenders("function(s)", offenders, FUNCTION_REMEDY)

            self.assertEqual(
                [offender.name for offender in offenders],
                ["second_offender", "first_offender"],
            )
            self.assertIn("2 function(s) over the limit", message)
            self.assertIn("first.py:1 first_offender", message)
            self.assertIn("second.py:1 second_offender", message)
            self.assertNotIn("innocent", message)
            self.assertIn(FUNCTION_REMEDY, message)

    def test_the_class_surface_check_reports_every_offender_not_the_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            over = structural_limits.CLASS_PUBLIC_METHOD_LIMIT + 1
            package = write_package(
                Path(tmp),
                {
                    "wide.py": class_with_public_methods("FirstWide", over),
                    "wider.py": class_with_public_methods("SecondWide", over + 5),
                    "narrow.py": class_with_public_methods("Narrow", 2),
                },
            )

            offenders = structural_limits.wide_classes(package)
            message = structural_limits.render_offenders("class(es)", offenders, CLASS_REMEDY)

            self.assertEqual([offender.name for offender in offenders], ["SecondWide", "FirstWide"])
            self.assertIn("2 class(es) over the limit", message)
            self.assertIn("wide.py:1 FirstWide", message)
            self.assertNotIn("Narrow", message)
            self.assertIn(CLASS_REMEDY, message)

    def test_the_directory_check_rejects_a_crowded_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            over = structural_limits.DIRECTORY_MODULE_LIMIT + 5
            package = write_package(
                Path(tmp), {f"drawer/module_{index}.py": "" for index in range(over)}
            )

            offenders = structural_limits.crowded_directories(package)
            message = structural_limits.render_offenders(
                "director(ies)", offenders, DIRECTORY_REMEDY
            )

            self.assertEqual([offender.name for offender in offenders], ["drawer"])
            self.assertEqual(offenders[0].measured, over)
            self.assertIn("1 director(ies) over the limit", message)
            # A directory has no line to point at, so the location is the directory itself.
            self.assertIn("  30 (limit 25, +5)  drawer drawer", message)
            self.assertIn(DIRECTORY_REMEDY, message)

    def test_a_declared_deviation_silences_exactly_the_directory_it_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            over = structural_limits.DIRECTORY_MODULE_LIMIT + 5
            package = write_package(
                Path(tmp),
                {f"declared/module_{index}.py": "" for index in range(over)}
                | {f"undeclared/module_{index}.py": "" for index in range(over)},
            )

            offenders = structural_limits.crowded_directories(
                package, deviations=[deviation("declared", structural_limits.DIRECTORY_MODULES)]
            )

            self.assertEqual([offender.name for offender in offenders], ["undeclared"])

    def test_a_deviation_silences_a_cap_it_names_and_not_one_it_does_not(self) -> None:
        # The `serving/` entry names both caps. An entry naming only one must not spill
        # over to the other, or "which cap does this excuse" would be decoration.
        with tempfile.TemporaryDirectory() as tmp:
            over = structural_limits.CLASS_PUBLIC_METHOD_LIMIT + 1
            package = write_package(
                Path(tmp),
                {"declared/wide.py": class_with_public_methods("DeclaredWide", over)}
                | {f"declared/module_{index}.py": "" for index in range(30)},
            )
            modules_only = deviation("declared", structural_limits.DIRECTORY_MODULES)
            both = deviation(
                "declared", structural_limits.DIRECTORY_MODULES, structural_limits.CLASS_SURFACE
            )

            self.assertEqual(
                [entry.name for entry in structural_limits.wide_classes(package)],
                ["DeclaredWide"],
            )
            self.assertEqual(
                [
                    entry.name
                    for entry in structural_limits.wide_classes(package, deviations=[modules_only])
                ],
                ["DeclaredWide"],
            )
            self.assertEqual(structural_limits.wide_classes(package, deviations=[both]), [])
            self.assertEqual(structural_limits.crowded_directories(package, deviations=[both]), [])

    def test_a_class_deviation_covers_the_directory_and_nothing_beside_it(self) -> None:
        # Scoped to a directory, so a sibling package with a similar name is not swept in
        # by a prefix match -- `serving_extra/` must not ride on `serving/`.
        with tempfile.TemporaryDirectory() as tmp:
            over = structural_limits.CLASS_PUBLIC_METHOD_LIMIT + 1
            package = write_package(
                Path(tmp),
                {
                    "declared/wide.py": class_with_public_methods("Inside", over),
                    "declared_extra/wide.py": class_with_public_methods("Beside", over),
                    "declared/deeper/wide.py": class_with_public_methods("Nested", over),
                },
            )

            offenders = structural_limits.wide_classes(
                package, deviations=[deviation("declared", structural_limits.CLASS_SURFACE)]
            )

            self.assertEqual([entry.name for entry in offenders], ["Beside"])

    def test_a_function_whose_end_the_parser_did_not_record_measures_one_line(self) -> None:
        # `end_lineno` is typed optional because the node classes are shared with trees
        # built by hand. A checker that crashes on an unexpected shape gets switched off,
        # so the fallback is exercised rather than assumed unreachable.
        node = ast.parse("def small() -> None:\n    pass\n").body[0]
        node.end_lineno = None

        self.assertEqual(structural_limits.source_span(node), 1)


if __name__ == "__main__":
    unittest.main()
