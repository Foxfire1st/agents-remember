"""Lean declarations and whole dotted names through the writer and recorded-anchor reader."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.memory.knowledge.read_anchors import observe_anchor
from agents_remember.memory_quality.style.citations import extents
from agents_remember.memory_quality.style.citations.lean_lex import code_lines
from agents_remember.memory_quality.style.citations.lean_tokens import ScopeTokens
from knowledge_writer_test_support import git, write

if TYPE_CHECKING:
    from agents_remember.models.knowledge.read_anchor import AnchorResolution

SCOPES = """\
module
namespace Outer.Inner
section Local
noncomputable section
public section
private theorem hidden : True := True.intro
protected def guarded := 2
def \N{GREEK SMALL LETTER ALPHA}'₃!? := 3
def «漢.字».tail.{u} (_ : Sort u) := 4
def _root_.Isolated.top := 5
mutual
  def first : Nat := 6
  def second : Nat := 7
end
end
end
end Local
namespace Deep
def center := 8
end Deep
end Outer.Inner

namespace Outer.Inner
def reopened := 9
end Outer.Inner

def Standalone.dotted := 10
"""

LAYOUT = """\
set_option maxRecDepth 128 in
set_option maxHeartbeats 15000 in
/-- The number used by the example.
theorem thisIsDocumentation : True := True.intro
-/
@[inline
]
  def wrapped :=
    9

structure Record
where
  first : Nat

  second : Nat

def mkRecord : Record := {
  first := 1
  second := 2
}

theorem term
:
∀ x : Nat, x = x
:=
  fun _ => rfl

theorem byAtStart : True :=
by
  trivial

def countdown (n : Nat) : Nat :=
  if n = 0 then 0 else countdown (n - 1)
termination_by n
decreasing_by omega
#check countdown

def simple := Nat.succ 0

def continuation :=
Nat.succ 0

@[inline]
def attributeAbove := 2

@[inline] def attributeInline := 3
"""

LITERALS = r"""
def ordinary := "text
theorem ordinaryPhantom : True := True.intro
end"
/-! Module comments have no owner.
def modulePhantom := 0
-/
def raw := r##"payload
def rawPhantom := 0
"##

def formatted := s!"{let middle := "line
theorem interpolatedPhantom := 0
end"; middle}"
/- An unrelated block.
/- text nested within it -/
namespace CommentPhantom
theorem commentPhantom := 0
-/
-- Another unrelated line.

/-- A definition of the last value. -/
def atEof := '\"'
""".strip("\n")

INSTANCES = """\
namespace Choice
class Pick where
  value : Nat
instance named : Pick where
  value := 1
scoped instance (priority := 99) preferred : Pick where
  value := 2
instance : Pick where
  value := 3
def next := 4
end Choice
"""

LOOKUP = """\
namespace A
def item := 17
def shared := 19
end A
namespace B
def A.item := 23
def shared := 29
end B
def Owner := 31
"""

_ANCHOR_ID = "6610fb1f-30d3-4795-8aed-d00db811b3e4"


APOSTROPHES = """\
def a' (_ : Char) := 1
def f := a' '"'
def next := 2
#check next
"""

HEADERS = """\
namespace
  Outer
def /- the name follows -/
  spaced := 42
private
theorem guarded : True := trivial
class Pick where
  value : Nat
instance
  (priority := 100)
  preferred : Pick := ⟨1⟩
instance : Pick := ⟨2⟩
def after := 10
end Outer
#check Outer.spaced
#check Outer.preferred
"""

COMMAND_BOUNDARIES = """\
/-- A declaration with documentation to convert. -/ def before := 1
#where
def after := 2
#with_exporting #check after
def final := 3
docs_to_verso before
"""

NAMED_ENDS = """\
namespace A.B
def before := 1
end B
def after := 2
section S
mutual
  def member := 3
end
end S
end A
namespace Q
namespace R
def member := 3
end Q.R
def outside := 4
namespace A.B
def reopened := 5
end A.B
#check A.after
#check outside
"""

WRAPPED_END = """\
namespace Q
namespace R
def before := 1
end
  Q.R
def after := 2
#check _root_.after
#check_failure Q.after
"""

SCOPE_LABELS = """\
namespace Q
section
  S.T
def before := 1
end

-- The label starts after an ordinary comment.
/- close the inner section -/ T
def middle := 2
end /- close Q and S -/
  Q.S
def root := 3
namespace «def»
section
/- A section without a label. -/
def inside := 4
end
/-- This definition remains inside the namespace. -/
def later := 5
end
  «def»
def outside := 6
#check Q.before
#check Q.middle
#check _root_.root
#check «def».later
#check _root_.outside
namespace Edge
section
/- A label with a project command spelling. -/ alias
def beforeAliasEnd := 7
end
  alias
def afterAliasEnd := 8
end Edge
namespace «»
def insideEmpty := 9
end
def afterEmpty := 10
#check Edge.afterAliasEnd
#check _root_.afterEmpty
"""

ANONYMOUS_SECTION = """\
namespace Outer
section
  universe
    u
def before : Nat := 1
end
end Outer
def after : Nat := 2
#check _root_.after
#check_failure Outer.after
"""

SECTION_WITH_CUSTOM_COMMAND = """\
macro "tick " target:ident : command => `(command| #check Nat)
namespace Outer
section
  tick
    customToken
def before : Nat := 1
end
end Outer
def after : Nat := 2
#check _root_.after
#check_failure Outer.after
"""

SECTION_WITH_EMPTY_COMPONENT = """\
namespace Outer
section
  S.«»
def before := 1
end
def middle := 2
end Outer.S
def after := 3
#check Outer.middle
#check _root_.after
"""

DOTTED_COMMAND_AFTER_END = """\
macro "Outer.universe" : command => `(command| #check Nat)
namespace Outer
section
  universe
    u
def before : Nat := 1
end
  Outer.universe
def middle : Nat := 2
end Outer
def after : Nat := 3
#check _root_.Outer.middle
#check_failure _root_.middle
"""

LOCAL_TOKEN_LIFETIME = """\
namespace Outer
section Temp
local macro "Key" : command => `(command| #check Nat)
end Temp
section Key
def inside : Nat := 2
end Outer.Key
def outside : Nat := 3
#check _root_.Outer.inside
#check _root_.outside
#check_failure _root_.Outer.outside
"""


IMPORTED_SCOPE_RECONVERGENCE = """\
import Imported
section «Outer»
namespace Outer
section
  importedMark
def before := 1
end
  Outer.importedMark
def middle := 2
def _root_.explicitRoot := 3
end «Outer»
def recovered := 4
"""


UNRELATED_COMMAND_STRINGS = """\
macro "keepStrings " _left:str _right:str : command => `(command| #check Nat)
syntax "addedAtom" : command
keepStrings "Label" "Outer.Label"
namespace Outer
section
  Label
def before : Nat := 1
end
  Outer.Label
def middle : Nat := 2
#check _root_.middle
#check_failure _root_.Outer.middle
"""


PRECEDENCE_LEXICAL_EXTENT = """\
syntax "localPrec" ":" "precEnd" : prec
macro_rules | `(prec| localPrec : precEnd) => `(prec| max)
syntax:localPrec:precEnd "sampleStart" : command
syntax:localPrec:precEnd "Outer.sampleStart" : command
macro_rules
  | `(command| sampleStart) => `(command| #check Nat)
  | `(command| Outer.sampleStart) => `(command| #check Nat)
namespace Outer
section
  sampleStart
def before : Nat := 1
end
  Outer.sampleStart
def middle : Nat := 2
#check _root_.Outer.middle
#check_failure _root_.middle
end Outer
"""

CUSTOM_PRECEDENCE = """\
syntax "localPrec" : prec
macro_rules | `(prec| localPrec) => `(prec| max)
syntax "sampleStart" term:localPrec "sampleEnd" : command
"""

CATEGORY_AS_PRECEDENCE = """\
syntax "command" : prec
macro_rules | `(prec| command) => `(prec| max)
syntax "sampleStart" term:command "sampleEnd" : «command»
"""

COLON_IN_SYNTAX_BODY = """\
macro:arg p:stx:max atomic(":" &"marker") : stx => `(stx| $p)
syntax "firstAtom" : marker "followingAtom" : command
macro_rules
  | `(command| firstAtom followingAtom) => `(command| #check Nat)
firstAtom followingAtom
"""


ALIASES = """\
namespace Outer
public alias total :=
  original
/-- Directions of the equivalence. -/
@[simp]
private alias ⟨forward,
  reverse⟩ :=
  equivalence
externalCommand
alias ⟨_, _root_.Global.right⟩ := equivalence
alias ⟨«left, :=», _⟩ := equivalence
def after := 1
end Outer
"""


@pytest.fixture(scope="module")
def code(tmp_path_factory: pytest.TempPathFactory) -> CodeSnapshot:
    root = tmp_path_factory.mktemp("lean-symbols")
    git(root, "init", "-q", "-b", "main")
    write(
        root,
        {
            "Scopes.lean": SCOPES,
            "Layout.lean": LAYOUT,
            "Literals.lean": LITERALS,
            "Instances.lean": INSTANCES,
            "Lookup.lean": LOOKUP,
            "Apostrophes.lean": APOSTROPHES,
            "Headers.lean": HEADERS,
            "CommandBoundaries.lean": COMMAND_BOUNDARIES,
            "Aliases.lean": ALIASES,
            "NamedEnds.lean": NAMED_ENDS,
            "WrappedEnd.lean": WRAPPED_END,
            "ScopeLabels.lean": SCOPE_LABELS,
            "AnonymousSection.lean": ANONYMOUS_SECTION,
            "InlineAnonymousSection.lean": ANONYMOUS_SECTION.replace(
                "section\n  universe\n    u", "section universe u"
            ),
            "SectionWithCustomCommand.lean": SECTION_WITH_CUSTOM_COMMAND,
            "SectionWithEmptyComponent.lean": SECTION_WITH_EMPTY_COMPONENT,
            "DottedCommandAfterEnd.lean": DOTTED_COMMAND_AFTER_END,
            "LocalTokenLifetime.lean": LOCAL_TOKEN_LIFETIME,
            "ImportedScopeReconvergence.lean": IMPORTED_SCOPE_RECONVERGENCE,
            "PrecedenceLexicalExtent.lean": PRECEDENCE_LEXICAL_EXTENT,
            "UnrelatedCommandStrings.lean": UNRELATED_COMMAND_STRINGS,
            "DirectPrecedenceColonOperands.lean": UNRELATED_COMMAND_STRINGS.replace(
                'syntax "addedAtom" : command\n', 'syntax "addedAtom" term:arg : command\n'
            ),
            "InlineSyntaxOperandBoundary.lean": UNRELATED_COMMAND_STRINGS.replace(
                'syntax "addedAtom" : command\n', 'syntax "addedAtom" : command '
            ),
            "WrappedSyntaxOperandBoundary.lean": UNRELATED_COMMAND_STRINGS.replace(
                'syntax "addedAtom" : command\n',
                'syntax:80\n  "addedAtom" term:max "tailAtom"\n  :\n    command\n',
            ),
            "OutOfRange.lean": "def 漢 := 0\ndef after := 1\n",
            "namespace.ts": "namespace A.B {\n  export const selected = 1;\n}\n",
            "ui.test.ts": 'it("renders A.B", () => {\n  render();\n});\n',
        },
    )
    git(root, "add", "-A")
    return CodeSnapshot.of_tree(root, git(root, "write-tree"))


def _observe(code: CodeSnapshot, path: str, name: str, language: str = "lean") -> AnchorResolution:
    return observe_anchor(
        {
            "anchor_id": _ANCHOR_ID,
            "path": path,
            "source_identity": {"object_id": code.blobs[path]},
            "locator": {"kind": "symbol", "language": language, "qualified_name": name},
        },
        repository_root=code.root,
        tree_id=code.tree,
    )


def _assert_span(
    code: CodeSnapshot, path: str, name: str, span: tuple[int, int], *, language: str = "lean"
) -> None:
    first, last = span
    expected = code.resolve(path, {"kind": "line_range", "start": first, "end": last})
    written = code.resolve(path, {"kind": "symbol", "name": name})
    assert (written.blob, written.content) == (expected.blob, expected.content)
    observed = _observe(code, path, name, language)
    assert observed.resolution == "exact_recorded_blob"
    assert [(row.start_line, row.end_line) for row in observed.resolved_ranges] == [span]


def _assert_undefined(code: CodeSnapshot, path: str, name: str) -> None:
    with pytest.raises(AnchorResolutionError, match="does not define"):
        code.resolve(path, {"kind": "symbol", "name": name})
    observed = _observe(code, path, name)
    assert observed.resolution == "recorded_blob_mismatch"
    assert not observed.resolved_ranges


def _assert_unverifiable_scope(code: CodeSnapshot, path: str, name: str) -> None:
    with pytest.raises(AnchorResolutionError, match=r"uncertain Lean scope.*line_range"):
        code.resolve(path, {"kind": "symbol", "name": name})
    observed = _observe(code, path, name)
    assert observed.resolution == "unsupported_locator"
    assert not observed.resolved_ranges


def test_lean_namespace_names_survive_section_and_mutual_frames(
    code: CodeSnapshot, subtests: pytest.Subtests
) -> None:
    expected = {
        "hidden": (6, 6),
        "Outer.Inner.hidden": (6, 6),
        "Outer.Inner.guarded": (7, 7),
        "Outer.Inner.\N{GREEK SMALL LETTER ALPHA}'₃!?": (8, 8),
        "Outer.Inner.«漢.字».tail": (9, 9),
        "«漢.字».tail": (9, 9),
        "Isolated.top": (10, 10),
        "_root_.Isolated.top": (10, 10),
        "Outer.Inner.first": (12, 12),
        "Outer.Inner.second": (13, 13),
        "Outer.Inner.Deep.center": (19, 19),
        "Outer.Inner.reopened": (24, 24),
        "Standalone.dotted": (27, 27),
    }
    for name, span in expected.items():
        with subtests.test(name=name):
            _assert_span(code, "Scopes.lean", name, span)


def test_lean_anchors_reach_prefixes_and_column_zero_continuations(
    code: CodeSnapshot, subtests: pytest.Subtests
) -> None:
    expected = {
        "wrapped": (1, 9),
        "Record": (11, 15),
        "mkRecord": (17, 20),
        "term": (22, 26),
        "byAtStart": (28, 30),
        "countdown": (32, 35),
        "simple": (38, 38),
        "continuation": (40, 41),
        "attributeAbove": (43, 44),
        "attributeInline": (46, 46),
    }
    for name, span in expected.items():
        with subtests.test(name=name):
            _assert_span(code, "Layout.lean", name, span)
    wrapped = extents.definitions("Layout.lean", LAYOUT.splitlines())["wrapped"]
    assert [definition.declaration for definition in wrapped] == [8]


def test_lean_comments_and_multiline_literals_keep_only_real_declarations(
    code: CodeSnapshot, subtests: pytest.Subtests
) -> None:
    expected = {"ordinary": (1, 3), "raw": (7, 9), "formatted": (11, 13), "atEof": (21, 22)}
    for name, span in expected.items():
        with subtests.test(name=name):
            _assert_span(code, "Literals.lean", name, span)
    for name in (
        "ordinaryPhantom",
        "modulePhantom",
        "rawPhantom",
        "interpolatedPhantom",
        "commentPhantom",
    ):
        with subtests.test(name=name):
            _assert_undefined(code, "Literals.lean", name)


def test_lean_instances_read_priority_and_end_before_an_unnamed_instance(
    code: CodeSnapshot,
) -> None:
    _assert_span(code, "Instances.lean", "Choice.named", (4, 5))
    _assert_span(code, "Instances.lean", "preferred", (6, 7))
    _assert_span(code, "Instances.lean", "Choice.preferred", (6, 7))
    _assert_span(code, "Instances.lean", "Choice.next", (10, 10))
    _assert_undefined(code, "Instances.lean", "Choice.instPick")


def test_lean_apostrophe_in_name_keeps_following_character_literal(code: CodeSnapshot) -> None:
    _assert_span(code, "Apostrophes.lean", "f", (2, 2))
    _assert_span(code, "Apostrophes.lean", "next", (3, 3))


def test_lean_names_can_follow_a_header_line_break(
    code: CodeSnapshot, subtests: pytest.Subtests
) -> None:
    expected = {"Outer.spaced": (3, 4), "Outer.guarded": (5, 6), "Outer.preferred": (9, 11)}
    for name, span in expected.items():
        with subtests.test(name=name):
            _assert_span(code, "Headers.lean", name, span)
    found = extents.definitions("Headers.lean", HEADERS.splitlines())
    assert found["Outer.spaced"][0].declaration == 3
    assert found["Outer.guarded"][0].declaration == 6
    assert found["Outer.preferred"][0].declaration == 9


def test_lean_pinned_commands_end_active_declarations(
    code: CodeSnapshot, subtests: pytest.Subtests
) -> None:
    for name, line in {"before": 1, "after": 3, "final": 5}.items():
        with subtests.test(name=name):
            _assert_span(code, "CommandBoundaries.lean", name, (line, line))


def test_lean_alias_names_resolve_at_their_command_extents(
    code: CodeSnapshot, subtests: pytest.Subtests
) -> None:
    path = "Aliases.lean"
    found = extents.definitions(path, ALIASES.splitlines())
    expected = {
        "Outer.total": (2, 2, 3),
        "Outer.forward": (4, 6, 8),
        "Outer.reverse": (4, 6, 8),
        "Global.right": (10, 10, 10),
        "Outer.«left, :=»": (11, 11, 11),
        "Outer.after": (12, 12, 12),
    }
    for name, (first, keyword, last) in expected.items():
        with subtests.test(name=name):
            _assert_span(code, path, name, (first, last))
            assert [span.declaration for span in found[name]] == [keyword]
    _assert_undefined(code, path, "_")


def test_lean_named_ends_close_the_matching_scope_suffix(
    code: CodeSnapshot, subtests: pytest.Subtests
) -> None:
    expected = {
        "A.B.before": (2, 2),
        "A.after": (4, 4),
        "A.member": (7, 7),
        "Q.R.member": (13, 13),
        "outside": (15, 15),
        "A.B.reopened": (17, 17),
    }
    for name, span in expected.items():
        with subtests.test(name=name):
            _assert_span(code, "NamedEnds.lean", name, span)
    _assert_undefined(code, "NamedEnds.lean", "Q.outside")


def test_lean_wrapped_end_closes_the_same_suffix_as_inline_end(code: CodeSnapshot) -> None:
    _assert_span(code, "WrappedEnd.lean", "after", (6, 6))
    _assert_undefined(code, "WrappedEnd.lean", "Q.after")


def test_lean_wrapped_scope_labels_respect_comments_and_unnamed_ends(
    code: CodeSnapshot, subtests: pytest.Subtests
) -> None:
    expected = {
        "Q.before": (4, 4),
        "Q.middle": (9, 9),
        "root": (12, 12),
        "«def».inside": (16, 16),
        "«def».later": (18, 19),
        "outside": (22, 22),
        "Edge.afterAliasEnd": (34, 34),
        "afterEmpty": (39, 39),
    }
    for name, span in expected.items():
        with subtests.test(name=name):
            _assert_span(code, "ScopeLabels.lean", name, span)
    _assert_undefined(code, "ScopeLabels.lean", "Q.root")
    _assert_undefined(code, "ScopeLabels.lean", "«def».outside")
    _assert_undefined(code, "ScopeLabels.lean", "«».afterEmpty")


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("AnonymousSection.lean", 8),
        ("InlineAnonymousSection.lean", 6),
        ("SectionWithCustomCommand.lean", 9),
    ],
)
def test_lean_anonymous_section_can_begin_with_a_command(
    code: CodeSnapshot, path: str, line: int
) -> None:
    _assert_span(code, path, "after", (line, line))
    _assert_undefined(code, path, "Outer.after")


def test_lean_bare_end_closes_only_the_empty_component_of_a_wrapped_section(
    code: CodeSnapshot,
) -> None:
    path = "SectionWithEmptyComponent.lean"
    _assert_span(code, path, "Outer.middle", (6, 6))
    _assert_span(code, path, "after", (8, 8))
    _assert_undefined(code, path, "Outer.after")


def test_lean_bare_section_end_leaves_a_dotted_command_outside_its_label(
    code: CodeSnapshot,
) -> None:
    path = "DottedCommandAfterEnd.lean"
    _assert_span(code, path, "Outer.middle", (9, 9))
    _assert_span(code, path, "after", (11, 11))
    _assert_undefined(code, path, "_root_.middle")


def test_lean_local_syntax_stops_reserving_its_token_after_the_scope_ends(
    code: CodeSnapshot,
) -> None:
    path = "LocalTokenLifetime.lean"
    _assert_span(code, path, "Outer.inside", (6, 6))
    _assert_span(code, path, "outside", (8, 8))
    _assert_undefined(code, path, "Outer.outside")


def test_lean_scope_recovers_when_namespace_projections_agree(code: CodeSnapshot) -> None:
    path = "ImportedScopeReconvergence.lean"
    for name, line in (("Outer.before", 6), ("explicitRoot", 10), ("recovered", 12)):
        _assert_span(code, path, name, (line, line))
        observed = _observe(code, path, name)
        assert observed.resolution == "exact_recorded_blob"
        assert [(item.start_line, item.end_line) for item in observed.resolved_ranges] == [
            (line, line)
        ]

    for name in ("middle", "Outer.middle"):
        _assert_unverifiable_scope(code, path, name)

    _assert_undefined(code, path, "Never.middle")
    _assert_undefined(code, path, "Outer.recovered")


@pytest.mark.parametrize(
    ("path", "before", "middle"),
    [
        ("UnrelatedCommandStrings.lean", 7, 10),
        ("InlineSyntaxOperandBoundary.lean", 6, 9),
        ("WrappedSyntaxOperandBoundary.lean", 10, 13),
        ("DirectPrecedenceColonOperands.lean", 7, 10),
    ],
)
def test_lean_syntax_tail_false_uncertainty_is_reported(
    code: CodeSnapshot, path: str, before: int, middle: int
) -> None:
    """Ordinary operands can look like atoms when the syntax tail is unproved."""
    _assert_span(code, path, "Outer.before", (before, before))
    for name in ("middle", "Outer.middle"):
        _assert_unverifiable_scope(code, path, name)
    explicit = code.resolve(path, {"kind": "line_range", "start": middle, "end": middle})
    assert explicit.blob == code.blobs[path]


def test_lean_custom_precedence_with_inner_colon_keeps_scope_unverifiable(
    code: CodeSnapshot,
) -> None:
    path = "PrecedenceLexicalExtent.lean"
    _assert_span(code, path, "Outer.before", (11, 11))
    for name in ("middle", "Outer.middle"):
        _assert_unverifiable_scope(code, path, name)


@pytest.mark.parametrize(
    ("source", "atoms"),
    [
        pytest.param(
            PRECEDENCE_LEXICAL_EXTENT,
            ("localPrec", "precEnd", "sampleStart", "Outer.sampleStart"),
            id="precedence-contains-colon",
        ),
        pytest.param(
            CUSTOM_PRECEDENCE, ("localPrec", "sampleStart", "sampleEnd"), id="custom-precedence"
        ),
        pytest.param(
            CATEGORY_AS_PRECEDENCE, ("command", "sampleStart", "sampleEnd"), id="category-spelling"
        ),
        pytest.param(COLON_IN_SYNTAX_BODY, ("firstAtom", "followingAtom"), id="stx-extension"),
    ],
)
def test_lean_extensible_precedence_and_syntax_keep_possible_atoms(
    source: str, atoms: tuple[str, ...]
) -> None:
    lines = source.splitlines()
    tokens = ScopeTokens(lines, tuple(code_lines(lines)))
    for atom in atoms:
        label = tokens.read_label(atom)
        assert label is not None
        assert tokens.possibly_command_token(atom, label), atom


def test_lean_full_name_takes_precedence_over_another_declarations_written_name(
    code: CodeSnapshot,
) -> None:
    _assert_span(code, "Lookup.lean", "A.item", (2, 2))
    _assert_span(code, "Lookup.lean", "B.A.item", (6, 6))


def test_lean_dotted_anchors_require_an_exact_binding(code: CodeSnapshot) -> None:
    _assert_undefined(code, "Lookup.lean", "Owner.item")
    _assert_undefined(code, "Lookup.lean", "item.")


def test_lean_ambiguous_writer_names_full_choices_and_reader_returns_both_ranges(
    code: CodeSnapshot,
) -> None:
    with pytest.raises(AnchorResolutionError, match=r"bound more than once.*A\.shared.*B\.shared"):
        code.resolve("Lookup.lean", {"kind": "symbol", "name": "shared"})
    observed = _observe(code, "Lookup.lean", "shared")
    assert observed.resolution == "exact_recorded_blob"
    assert [(row.start_line, row.end_line) for row in observed.resolved_ranges] == [(3, 3), (7, 7)]
    _assert_span(code, "Lookup.lean", "A.shared", (3, 3))
    _assert_span(code, "Lookup.lean", "B.shared", (7, 7))


def test_lean_bare_character_outside_pinned_range_yields_no_name(code: CodeSnapshot) -> None:
    _assert_undefined(code, "OutOfRange.lean", "漢")
    _assert_span(code, "OutOfRange.lean", "after", (2, 2))


@pytest.mark.parametrize(("path", "name"), [("namespace.ts", "A.B"), ("ui.test.ts", "renders A.B")])
def test_typescript_writer_and_reader_both_find_whole_dotted_name(
    code: CodeSnapshot, path: str, name: str
) -> None:
    _assert_span(code, path, name, (1, 3), language="typescript")
