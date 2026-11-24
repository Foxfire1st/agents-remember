"""Scan line-start Lean commands for source-written declarations."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from enum import Enum, auto

from agents_remember.memory_quality.style.citations.lean_aliases import AliasHeader
from agents_remember.memory_quality.style.citations.lean_lex import LeanLine, code_lines
from agents_remember.memory_quality.style.citations.lean_names import LeanName, read_name
from agents_remember.memory_quality.style.citations.lean_scopes import Scopes
from agents_remember.memory_quality.style.citations.lean_tokens import ScopeTokens

_DECLARATIONS = frozenset(
    [
        "abbrev",
        "alias",
        "axiom",
        "class",
        "coinductive",
        "def",
        "inductive",
        "instance",
        "lemma",
        "opaque",
        "structure",
        "theorem",
    ]
)
_MODIFIERS = frozenset(
    [
        "local",
        "meta",
        "noncomputable",
        "nonrec",
        "partial",
        "private",
        "protected",
        "public",
        "scoped",
        "unsafe",
    ]
)
# Command-category leading tokens from Lean 4.37.0-rc1 (import Lean).
# Declaration modifiers, doc comments, attributes and quotations are handled
# by the command prefix reader and lexer. Only deriving instance is a boundary.
_COMMANDS = (
    frozenset(
        [
            "?",
            "add_decl_doc",
            "alias",
            "assert_not_exists",
            "assert_not_imported",
            "attribute",
            "binder_predicate",
            "builtin_cbv_simproc",
            "builtin_cbv_simproc_decl",
            "builtin_cbv_simproc_pattern%",
            "builtin_dsimproc",
            "builtin_dsimproc_decl",
            "builtin_grind_propagator",
            "builtin_initialize",
            "builtin_simproc",
            "builtin_simproc_decl",
            "builtin_simproc_pattern%",
            "cbv_simproc",
            "cbv_simproc_decl",
            "cbv_simproc_pattern%",
            "declare_bitwise_int_theorems",
            "declare_bitwise_uint_theorems",
            "declare_command_config_elab",
            "declare_command_config_elab_legacy",
            "declare_config_elab",
            "declare_config_elab_legacy",
            "declare_core_config_elab",
            "declare_decidable_int_quantifiers",
            "declare_decidable_uint_quantifiers",
            "declare_eval_bin",
            "declare_eval_bin_bitwise",
            "declare_eval_bin_bool_pred",
            "declare_int_theorems",
            "declare_simp_like_tactic",
            "declare_sint_simprocs",
            "declare_syntax_cat",
            "declare_term_config_elab",
            "declare_uint_simprocs",
            "declare_uint_theorems",
            "def_eval_config_item",
            "deprecated_module",
            "deprecated_syntax",
            "docs_to_verso",
            "dsimproc",
            "dsimproc_decl",
            "elab",
            "elab_rules",
            "elab_stx_quot",
            "end",
            "example",
            "export",
            "gen_injective_theorems%",
            "grind_annotated",
            "grind_pattern",
            "grind_propagator",
            "import",
            "include",
            "infix",
            "infixl",
            "infixr",
            "init_grind_norm",
            "init_quot",
            "initialize",
            "macro",
            "macro_rules",
            "module",
            "mutual",
            "namespace",
            "norm_cast_add_elim",
            "notation",
            "omit",
            "open",
            "postfix",
            "prefix",
            "recall",
            "recall?",
            "recommended_spelling",
            "register_builtin_option",
            "register_error_explanation",
            "register_grind_attr",
            "register_label_attr",
            "register_linter_set",
            "register_option",
            "register_simp_attr",
            "register_sym_dsimp",
            "register_sym_simp",
            "register_sym_simp_attr",
            "register_tactic_tag",
            "register_try?_tactic",
            "reprove",
            "reset_grind_attrs%",
            "run_cmd",
            "run_elab",
            "run_meta",
            "seal",
            "section",
            "set_library_suggestions",
            "set_option",
            "show_panel_widgets",
            "simproc",
            "simproc_decl",
            "simproc_pattern%",
            "syntax",
            "tactic_extension",
            "test_extern",
            "unif_hint",
            "universe",
            "unlock_limits",
            "unseal",
            "variable",
            "with_weak_namespace",
            "#check",
            "#check_assertions",
            "#check_failure",
            "#check_simp",
            "#check_tactic",
            "#check_tactic_failure",
            "#discr_tree_key",
            "#discr_tree_simp_key",
            "#dump_async_env_state",
            "#eval",
            "#eval!",
            "#exit",
            "#grind_lint",
            "#guard",
            "#guard_expr",
            "#guard_msgs",
            "#guard_panic",
            "#html",
            "#import_path",
            "#info_trees",
            "#print",
            "#reduce",
            "#show_deprecated_modules",
            "#synth",
            "#time",
            "#version",
            "#where",
            "#widget",
            "#with_exporting",
        ]
    )
    | _DECLARATIONS
)
_SCOPE_HEADS = frozenset({"mutual", "namespace", "section"})
_BRACKETS = re.compile(r"«[^»]*»|[\[\]]")
_PARENS = re.compile(r"«[^»]*»|[()]")
_DERIVING_INSTANCE = re.compile(r"\s+(?:noncomputable\s+)?instance\b")
_WRAP = re.compile(r"\s+in\s*$")


@dataclass(frozen=True)
class LeanDeclaration:
    """A source-written name and its full name, with one-based inclusive lines."""

    written_name: str
    full_name: str | None
    start: int
    end: int
    declaration: int
    possible_full_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Command:
    head: str
    argument: int
    attribute_depth: int
    prefix: bool


def _head(code: str, start: int) -> tuple[str, int]:
    rest = code[start:]
    start += len(rest) - len(rest.lstrip())
    if code[start:].strip() == "?":
        return "?", start + 1
    marker = "#" if code.startswith("#", start) else ""
    name = read_name(code, start + len(marker))
    if name is None:
        return "", start
    end = name.end + code.startswith("%", name.end)
    return code[start:end], end


def _balanced_end(
    code: str, start: int, depth: int, tokens: re.Pattern[str], opening: str
) -> tuple[int, int]:
    for token in tokens.finditer(code, start):
        value = token[0]
        if value.startswith("«"):
            continue
        depth += 1 if value == opening else -1
        if depth == 0:
            return token.end(), 0
    return len(code), depth


def _parse_command(code: str, depth: int) -> _Command:
    start = 0
    prefix = bool(depth)
    if depth:
        start, depth = _balanced_end(code, start, depth, _BRACKETS, "[")
    while not depth:
        head, end = _head(code, start)
        if code.startswith("@[", end) and not head:
            prefix = True
            start, depth = _balanced_end(code, end + 2, 1, _BRACKETS, "[")
        elif head in _MODIFIERS:
            prefix = True
            start = end
        else:
            return _Command(head, end, 0, prefix)
    return _Command("", start, depth, prefix)


class _PriorityStage(Enum):
    OPEN = auto()
    LABEL = auto()
    ASSIGNMENT = auto()
    TERM = auto()


_PRIORITY_PREFIX = {
    _PriorityStage.OPEN: ("(", _PriorityStage.LABEL),
    _PriorityStage.LABEL: ("priority", _PriorityStage.ASSIGNMENT),
    _PriorityStage.ASSIGNMENT: (":=", _PriorityStage.TERM),
}


@dataclass
class _InstancePriority:
    """Consume an optional priority, retaining delimiter depth across lines."""

    stage: _PriorityStage = _PriorityStage.OPEN
    depth: int = 1

    def consume(self, text: str) -> tuple[bool, str | None]:
        while self.stage != _PriorityStage.TERM:
            if not text:
                return False, None
            token, next_stage = _PRIORITY_PREFIX[self.stage]
            if not text.startswith(token):
                return True, text if self.stage == _PriorityStage.OPEN else None
            text = text[len(token) :].lstrip()
            self.stage = next_stage
        stop, self.depth = _balanced_end(text, 0, self.depth, _PARENS, "(")
        return (False, None) if self.depth else (True, text[stop:].lstrip())


@dataclass
class _NameHeader:
    """A declaration or namespace whose name may start on a following line."""

    head: str
    first: int
    declaration: int
    indent: int
    priority: _InstancePriority | None
    alias: AliasHeader | None = None

    def read(self, text: str) -> tuple[LeanName, ...] | None:
        if self.alias is not None:
            return self.alias.read(text)
        text = text.lstrip()
        if self.priority is not None:
            finished, remaining = self.priority.consume(text)
            if not finished:
                return None
            self.priority = None
            if remaining is None:
                return ()
            text = remaining
        if self.head == "class":
            following, end = _head(text, 0)
            if following in {"abbrev", "inductive"}:
                self.head = following
                text = text[end:].lstrip()
        if not text:
            return None
        name = read_name(text)
        return (name,) if name is not None else ()


def _named_declaration(
    line: LeanLine, name: LeanName, header: _NameHeader, scopes: Scopes
) -> LeanDeclaration:
    if name.parts[0] == "_root_" and len(name.parts) > 1:
        names = (".".join(name.parts[1:]),)
    else:
        names = tuple(
            sorted({".".join((*parts, *name.parts)) for parts in scopes.namespace_parts()})
        )
    return LeanDeclaration(
        name.text,
        names[0] if len(names) == 1 else None,
        header.first,
        _last_line(line),
        header.declaration,
        names if len(names) > 1 else (),
    )


def _last_line(line: LeanLine) -> int:
    return line.number + line.code.count("\n")


def _joined_names(lines: Iterator[LeanLine]) -> Iterator[LeanLine]:
    """Join only the physical lines of a quoted identifier that spans a newline."""

    for line in lines:
        if not line.ends_in_quoted_name:
            yield line
            continue
        parts = [line.code]
        for continuation in lines:
            parts.append(continuation.code)
            if not continuation.ends_in_quoted_name:
                break
        yield replace(line, code="\n".join(parts))


class _Scan:
    """The command layout and the declaration, if any, that still owns text."""

    def __init__(self, tokens: ScopeTokens) -> None:
        self.scopes = Scopes()
        self.tokens = tokens
        self.active: LeanDeclaration | None = None
        self.prefix: int | None = None
        self.attribute_depth = 0
        self.wrapped = False
        self.pending_name: _NameHeader | None = None
        self.pending_scope: tuple[str, int] | None = None
        self.has_unknown_imports = False
        self.pending_import = False
        self.module_header = True

    def _at_command_indent(self, indent: int) -> bool:
        return (
            indent == 0
            or self.wrapped
            or bool(self.attribute_depth)
            or self.scopes.at_member_indent(indent)
        )

    def _retain_prefix(self, line: int) -> tuple[LeanDeclaration, ...]:
        self.prefix = self.prefix if self.prefix is not None else line
        return self.finish()

    def _inside_scope(self, line: LeanLine, command: _Command) -> None:
        if command.head != "end" and command.head not in _SCOPE_HEADS:
            return
        text = line.code[command.argument :].lstrip()
        named_end = command.head == "end" and self.scopes.may_have_end_label()
        if not text and (command.head == "section" or named_end):
            self.pending_scope = command.head, len(line.code) - len(line.code.lstrip())
            return
        self._apply_scope(command.head, line.indent, text)

    def _continue_scope_label(self, line: LeanLine) -> bool:
        if self.pending_scope is None:
            return False
        text = line.code.lstrip()
        if not text and line.comment is None:
            return False
        head, previous_column = self.pending_scope
        self.pending_scope = None
        column = len(line.code) - len(text)
        if column <= previous_column or line.comment is not None:
            text = ""
        return self._apply_scope(head, previous_column, text)

    def _apply_scope(self, head: str, indent: int, text: str) -> bool:
        name = self.tokens.read_label(text)
        may_be_command = (
            name is not None
            and not name.text.startswith("«")
            and (self.has_unknown_imports or self.tokens.possibly_command_token(text, name))
        )
        if head == "end":
            return self.scopes.close(name, may_be_command=may_be_command)
        self.scopes.open(head, indent, name, tentative=head == "section" and may_be_command)
        return False

    def _read_name(
        self, line: LeanLine, header: _NameHeader, start: int
    ) -> tuple[LeanDeclaration, ...]:
        names = header.read(line.code[start:])
        self.pending_name = header if names is None else None
        if not names:
            return ()
        if header.head == "namespace":
            self.scopes.open("namespace", header.indent, names[0], tentative=False)
        elif header.alias is not None:
            # The target identifier ends the alias; no continuation can extend its range.
            return tuple(_named_declaration(line, name, header, self.scopes) for name in names)
        else:
            self.active = _named_declaration(line, names[0], header, self.scopes)
        return ()

    def _continue_pending_name(self, line: LeanLine) -> tuple[LeanDeclaration, ...] | None:
        header = self.pending_name
        if header is None:
            return None
        if (
            (header.priority is None or header.priority.stage == _PriorityStage.OPEN)
            and line.indent <= header.indent
            and self._recognized(line, _parse_command(line.code, 0))
        ):
            self.pending_name = None
            return None
        return self._read_name(line, header, 0)

    def finish(self) -> tuple[LeanDeclaration, ...]:
        result, self.active = self.active, None
        return (result,) if result is not None else ()

    def _continue(self, line: LeanLine) -> None:
        if line.significant and self.active is not None:
            self.active = replace(self.active, end=_last_line(line))

    def _recognized(self, line: LeanLine, command: _Command) -> bool:
        return command.head in _COMMANDS or (
            command.head == "deriving"
            and _DERIVING_INSTANCE.match(line.code, command.argument) is not None
        )

    def _read_import(self, text: str) -> None:
        text = text.lstrip()
        name = read_name(text)
        if name is not None and name.parts == ("all",):
            text = text[name.end :].lstrip()
            name = read_name(text)
        self.pending_import = not text
        if name is not None and name.parts in {("Init",), ("«Init»",)}:
            self.tokens.include_init()
        elif not self.pending_import:
            self.has_unknown_imports = True

    def _start(self, line: LeanLine, command: _Command) -> tuple[LeanDeclaration, ...]:
        previous = self.finish()
        if command.head == "import":
            self._read_import(line.code[command.argument :])
        elif command.head != "module":
            self.module_header = False
        if command.head != "end":
            self.scopes.record_member_indent(line.indent)
        if command.head in _DECLARATIONS or command.head == "namespace":
            header = _NameHeader(
                command.head,
                self.prefix or line.number,
                line.number,
                line.indent,
                _InstancePriority() if command.head == "instance" else None,
                AliasHeader() if command.head == "alias" else None,
            )
            previous += self._read_name(line, header, command.argument)
        elif _WRAP.search(line.code) and command.head not in _SCOPE_HEADS:
            self.wrapped = True
            self._retain_prefix(line.number)
            return previous
        else:
            self._inside_scope(line, command)
        self.prefix = None
        self.wrapped = False
        return previous

    def _command_line(self, line: LeanLine) -> tuple[LeanDeclaration, ...]:
        previous_depth = self.attribute_depth
        command = _parse_command(line.code, previous_depth)
        self.attribute_depth = command.attribute_depth
        if self.module_header and command.head == "prelude":
            self.tokens.use_prelude()
            return ()
        if not command.head and command.prefix:
            return self._retain_prefix(line.number)
        if self._recognized(line, command):
            return self._start(line, command)
        self._continue(line)
        return ()

    def visit(self, line: LeanLine) -> Iterator[LeanDeclaration]:
        if self.pending_import:
            self._read_import(line.code)
            return
        if self._continue_scope_label(line):
            return
        pending = self._continue_pending_name(line)
        if pending is not None:
            yield from pending
            return
        eligible = self._at_command_indent(line.indent) and not line.starts_in_literal
        if not eligible:
            self._continue(line)
            return
        if line.comment == "module":
            self.prefix = None
            self.wrapped = False
            yield from self.finish()
        elif line.comment == "declaration":
            yield from self._retain_prefix(line.number)
        yield from self._command_line(line)


def declarations(lines: Sequence[str]) -> Iterator[LeanDeclaration]:
    """Yield all named Lean declarations in source order."""

    lexed = tuple(code_lines(lines))
    scanner = _Scan(ScopeTokens(lines, lexed))
    for line in _joined_names(iter(lexed)):
        yield from scanner.visit(line)
    yield from scanner.finish()
