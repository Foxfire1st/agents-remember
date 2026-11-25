"""Read certain and possible Lean tokens in scope-label positions."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from agents_remember.memory_quality.style.citations.lean_lex import LeanLine, LeanString
from agents_remember.memory_quality.style.citations.lean_names import LeanName, read_name

# Lean v4.37.0-rc1, commit 0b77e12952ad39077d80d8bd6b66d601f225c321.
# Generated in `run_cmd` after `import Lean` by reading
# `(← Lean.Parser.builtinTokenTable.get).values`, retaining only tokens with an
# identifier prefix, and sorting. Tokens introduced by imports are separate.
_BUILTIN_TOKENS = (
    "Prop",
    "Sort",
    "StateRefT",
    "Type",
    "_",
    "abbrev",
    "add_decl_doc",
    "assert!",
    "assert_not_exists",
    "assert_not_imported",
    "attribute",
    "axiom",
    "binder_predicate",
    "binop%",
    "binop_lazy%",
    "binrel%",
    "binrel_no_prop%",
    "break",
    "builtin_initialize",
    "by",
    "catch",
    "class",
    "clear%",
    "coinductive",
    "coinductive_fixpoint",
    "continue",
    "contract_eposts%",
    "dbg_trace",
    "debug_assert!",
    "decl_name%",
    "declare_syntax_cat",
    "decreasing_by",
    "def",
    "default_or_ofNonempty%",
    "deprecated_module",
    "deprecated_syntax",
    "deriving",
    "do",
    "docs_to_verso",
    "elab",
    "elabToSyntax%",
    "elab_rules",
    "else",
    "end",
    "ensure_expected_type%",
    "ensure_type_of%",
    "example",
    "export",
    "extends",
    "finally",
    "for",
    "for_in%",
    "for_in'%",
    "forall",
    "from",
    "fun",
    "gen_injective_theorems%",
    "grind_pattern",
    "have",
    "haveI",
    "hiding",
    "idbg",
    "if",
    "import",
    "in",
    "include",
    "inductive",
    "inductive_fixpoint",
    "inferInstanceAs",
    "infix",
    "infixl",
    "infixr",
    "init_grind_norm",
    "init_quot",
    "initialize",
    "instance",
    "leading_parser",
    "leftact%",
    "let",
    "letI",
    "let_delayed",
    "let_expr",
    "let_fun",
    "let_mvar%",
    "let_tmp",
    "let_λ",
    "local",
    "logNamedError",
    "logNamedErrorAt",
    "logNamedWarning",
    "logNamedWarningAt",
    "macro",
    "macro_rules",
    "match",
    "match_expr",
    "meta",
    "monotonicity_by",
    "mut",
    "mutual",
    "namespace",
    "no_error_if_unused%",
    "no_implicit_lambda%",
    "no_index",
    "nofun",
    "nomatch",
    "noncomputable",
    "nonrec",
    "notation",
    "omit",
    "opaque",
    "open",
    "panic!",
    "partial",
    "partial_fixpoint",
    "postfix",
    "prefix",
    "private",
    "private_decl%",
    "protected",
    "public",
    "recall",
    "recall?",
    "recommended_spelling",
    "register_error_explanation",
    "register_tactic_tag",
    "renaming",
    "repeat",
    "return",
    "rightact%",
    "scoped",
    "section",
    "sepBy(",
    "sepBy1(",
    "set_option",
    "show",
    "show_term_elab",
    "sorry",
    "struct_inst_default%",
    "structure",
    "suffices",
    "syntax",
    "tactic_alt",
    "tactic_extension",
    "tactic_name",
    "tactic_tag",
    "termination_by",
    "termination_by?",
    "then",
    "theorem",
    "throwNamedError",
    "throwNamedErrorAt",
    "trailing_parser",
    "try",
    "type_of%",
    "unicode(",
    "universe",
    "unless",
    "unlock_limits",
    "unop%",
    "unreachable!",
    "unsafe",
    "until",
    "value_of%",
    "variable",
    "wait_for_expected_type%",
    "wait_if_contains_mvar%",
    "wait_if_type_contains_mvar%",
    "wait_if_type_mvar%",
    "where",
    "while",
    "with",
    "with_decl_name%",
    "with_weak_namespace",
)
# The same projection of `Lean.Parser.getTokenTable` after native
# `Lean.importModules` of Init (regular and meta), excluding the built-ins.
_INIT_TOKENS = (
    "Macro.trace[",
    "at",
    "bif",
    "builtin_cbv_simproc",
    "builtin_cbv_simproc_decl",
    "builtin_cbv_simproc_pattern%",
    "builtin_dsimproc",
    "builtin_dsimproc_decl",
    "builtin_grind_propagator",
    "builtin_simproc",
    "builtin_simproc_decl",
    "builtin_simproc_pattern%",
    "by?",
    "by_elab",
    "calc",
    "cbv_eval",
    "cbv_simproc",
    "cbv_simproc_decl",
    "cbv_simproc_pattern%",
    "declare_bitwise_int_theorems",
    "declare_bitwise_uint_theorems",
    "declare_decidable_int_quantifiers",
    "declare_decidable_uint_quantifiers",
    "declare_int_theorems",
    "declare_simp_like_tactic",
    "declare_uint_theorems",
    "dsimproc",
    "dsimproc_decl",
    "eval_prec",
    "eval_prio",
    "exact?%",
    "exists",
    "f!",
    "generalizing",
    "grind_annotated",
    "grind_propagator",
    "include_str",
    "matches",
    "max_prec",
    "mod_cast",
    "nat_lit",
    "norm_cast_add_elim",
    "println!",
    "register_sym_dsimp",
    "register_sym_simp",
    "register_try?_tactic",
    "reset_grind_attrs%",
    "run_cmd",
    "run_elab",
    "run_meta",
    "s!",
    "satisfies_binder_pred%",
    "seal",
    "set_library_suggestions",
    "show_term",
    "simproc",
    "simproc_decl",
    "simproc_pattern%",
    "unif_hint",
    "unseal",
    "using",
    "using!",
    "without_expected_type",
)
_WHITESPACE = " \t\r\n"
_LAYOUT = re.compile(r"[ \t\r\n]+|=>|<\|>|\|\||\|>|<\||.")
_ESCAPE = re.compile(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|\n[ \t\r]*|.)")
_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "\n": ""}

_RULE_HEADS = frozenset({"macro_rules", "elab_rules"})
_ARROW_HEADS = frozenset(
    {"macro", "elab", "notation", "infix", "infixl", "infixr", "prefix", "postfix"}
)
_SYNTAX_HEADS = _RULE_HEADS | _ARROW_HEADS | {"syntax", "declare_syntax_cat"}
_SYNTAX_WORD = re.compile(r"\b(?:" + "|".join(sorted(_SYNTAX_HEADS)) + r")\b")
# The inventory is independent of imports, so only built-ins delimit it.
_BUILTIN_WORDS = frozenset(_BUILTIN_TOKENS)
# Distinguish rule arms from unrelated commands without treating term keywords
# in a rule body, such as if and match, as command boundaries.
_RULE_BOUNDARIES = frozenset(
    {
        "#",
        "abbrev",
        "axiom",
        "class",
        "coinductive",
        "def",
        "end",
        "example",
        "inductive",
        "instance",
        "namespace",
        "opaque",
        "section",
        "structure",
        "theorem",
    }
)
_GROUP_OPEN = frozenset({"(", "[", "{"})
_GROUP_CLOSE = frozenset({")", "]", "}"})


@dataclass
class _Trie:
    edges: dict[str, _Trie] = field(default_factory=dict)
    terminal: bool = False

    def add(self, token: str) -> None:
        node = self
        for char in token:
            child = node.edges.get(char)
            if child is None:
                node.edges[char] = child = _Trie()
            node = child
        node.terminal = True

    def competes_with(self, text: str, start: int, end: int) -> bool:
        node = self
        for offset in range(start, len(text)):
            child = node.edges.get(text[offset])
            if child is None:
                return False
            if child.terminal and offset >= end - 1:
                return True
            node = child
        return False


def _token_table(tokens: tuple[str, ...]) -> _Trie:
    table = _Trie()
    for token in tokens:
        table.add(token)
    return table


_BUILTINS = _token_table(_BUILTIN_TOKENS)
_INIT = _token_table(_INIT_TOKENS)


type _Piece = str | LeanString


def _line_code(line: LeanLine) -> tuple[str, int]:
    start = 0
    if line.starts_in_quoted_name:
        close = line.code.find("»")
        if close < 0:
            return "", 0
        start = close + 1
    if not line.ends_in_quoted_name:
        return line.code, start
    last_close = line.code.rfind("»", start)
    opening = line.code.find("«", max(start, last_close + 1))
    return line.code[:opening], start


def _pieces(line: LeanLine) -> Iterator[_Piece]:
    strings: dict[int, LeanString] = {}
    for literal in line.strings:
        if literal.start_line < line.number:
            yield literal
        else:
            strings[literal.start_column] = literal
    code, start = _line_code(line)
    while start < len(code):
        if literal := strings.get(start):
            yield literal
            start = literal.end_column
        elif name := read_name(code, start):
            yield name.text
            start = name.end
        else:
            piece = _LAYOUT.match(code, start)
            if piece is None:
                break
            start = piece.end()
            if piece[0] != '"' and piece[0].strip(_WHITESPACE):
                yield piece[0]


@dataclass
class _SyntaxAtoms:
    """Collect possible atoms while a syntax declaration's extent is unresolved.

    Lean's extensible prec and stx grammars can consume an apparent category
    tail. Continued collection can retain ordinary string operands from a
    later command as possible atoms and make a valid symbol anchor unverifiable.
    """

    head: str = ""
    depth: int = 0
    pattern: bool = False

    def consume(self, piece: str) -> None:
        if piece in _GROUP_OPEN:
            self.depth += 1
        elif piece in _GROUP_CLOSE:
            self.depth = max(0, self.depth - 1)
        elif self.depth == 0:
            self._at_command_level(piece)

    def _at_command_level(self, piece: str) -> None:
        if piece in _SYNTAX_HEADS:
            self.head = piece
            self.pattern = False
        elif self.head in _RULE_HEADS:
            if piece == "|":
                self.pattern = True
            elif piece == "=>":
                self.pattern = False
            elif piece in _RULE_BOUNDARIES:
                self.head = ""
                self.pattern = False
        elif piece in _BUILTIN_WORDS or piece in {"=>", "#"}:
            self.head = ""

    def literals(self, lexed: Sequence[LeanLine]) -> Iterator[LeanString]:
        for line in lexed:
            for piece in _pieces(line):
                if isinstance(piece, str):
                    self.consume(piece)
                elif self.pattern or self.head in _ARROW_HEADS or self.head == "syntax":
                    yield piece


def _unescape(match: re.Match[str]) -> str:
    code = match[0][1:]
    if len(code) > 1 and code[0] in "xu":
        return chr(int(code[1:], 16))
    return _ESCAPES.get(code[0], code[0])


def _literal_text(lines: Sequence[str], span: LeanString) -> str:
    start, end = span.start_line - 1, span.end_line - 1
    if start == end:
        raw = lines[start][span.start_column : span.end_column]
    else:
        raw = "\n".join(
            (
                lines[start][span.start_column :],
                *lines[start + 1 : end],
                lines[end][: span.end_column],
            )
        )
    if raw.startswith("r"):
        opening = raw.index('"')
        return raw[opening + 1 : -opening]
    return _ESCAPE.sub(_unescape, raw[1:-1])


def _possible_tokens(lines: Sequence[str], lexed: Sequence[LeanLine]) -> _Trie:
    table = _Trie()
    if not any(_SYNTAX_WORD.search(line.code) for line in lexed):
        return table
    for literal in _SyntaxAtoms().literals(lexed):
        token = _literal_text(lines, literal).strip(_WHITESPACE)
        if token.startswith("«") or any(char in _WHITESPACE for char in token):
            continue
        if read_name(token) is not None:
            table.add(token)
    return table


class ScopeTokens:
    """Match pinned certain tokens and file-wide possible tokens."""

    def __init__(self, lines: Sequence[str], lexed: Sequence[LeanLine]) -> None:
        self._possible = _possible_tokens(lines, lexed)
        self._init = True

    def use_prelude(self) -> None:
        """Remove the implicit Init import from this file's header."""

        self._init = False

    def include_init(self) -> None:
        """Activate a source-written import of Init."""

        self._init = True

    def read_label(self, text: str, start: int = 0) -> LeanName | None:
        """Return a name that no certain token can consume."""

        name = read_name(text, start)
        if name is None:
            return None
        tables = (_BUILTINS, _INIT) if self._init else (_BUILTINS,)
        return None if any(table.competes_with(text, start, name.end) for table in tables) else name

    def possibly_command_token(self, text: str, name: LeanName) -> bool:
        """Tell whether a file-written syntax atom can compete with this name."""

        start = name.end - len(name.text)
        return self._possible.competes_with(text, start, name.end)
