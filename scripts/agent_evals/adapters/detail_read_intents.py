"""Finite native read-intent syntax recognizers; no command or code execution."""

from __future__ import annotations

import ast
import posixpath
import re
import shlex
from itertools import islice
from pathlib import PurePosixPath
from typing import Any

Segment = tuple[list[str], str | None]
_DISPLAY = {"echo", "printf", "pwd", "ls", "wc", "git"}
_READ = {"cat", "sed", "head", "tail", "rg", "grep"}
_CONTROL = {
    "if",
    "while",
    "until",
    "case",
    "for",
    "do",
    "done",
    "eval",
    "source",
    ".",
    "exec",
    "read",
    "export",
    "unset",
    "local",
    "declare",
    "typeset",
    "set",
    "shift",
}


class _StopRegion(ValueError):
    """A region cannot safely be interpreted by this deliberately limited grammar."""


def _literal(raw: str, bindings: dict[str, str]) -> str:
    variable = re.fullmatch(r'"?\$([A-Za-z_]\w*)"?', raw)
    if variable and variable[1] in bindings:
        return bindings[variable[1]]
    (value,) = shlex.split(raw)
    if any(char in value for char in "$`*?[]{}\n") or value.startswith("~"):
        raise ValueError("nonliteral token")
    return value


def _segments(command: object) -> list[Segment]:
    if not isinstance(command, str) or len(command) > 65536 or "\n" in command:
        raise ValueError("input size/type limit")
    lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|<>()")
    lexer.whitespace_split, lexer.commenters = True, ""
    tokens = list(islice(lexer, 4097))
    if len(tokens) > 4096:
        raise ValueError("token limit")
    segments: list[Segment] = []
    current: list[str] = []
    for token in tokens:
        if token in {";", "&&"}:
            segments.append((current, token))
            current = []
        else:
            current.append(token)
    return [*segments, (current, None)]


def _range_paths(args: list[str]) -> list[str]:
    if len(args) > 2 and args[0] in {"-n", "-c"} and args[1].isdigit():
        return args[2:]
    return args[1:] if len(args) > 1 and re.fullmatch(r"-\d+", args[0]) else args


def _operands(argv: list[str]) -> tuple[list[str], str, bool]:
    name, args = argv[0], argv[1:]
    if name == "cat":
        return args, "opened", True
    if name in {"head", "tail"}:
        return _range_paths(args), "opened", False
    if (
        name == "sed"
        and len(args) >= 3
        and args[0] == "-n"
        and re.fullmatch(r"\d+(,\d+)?p", args[1])
    ):
        return args[2:], "opened", False
    if name in {"rg", "grep"}:
        while args and re.fullmatch(r"-[nHiFEwolcv]+", args[0]):
            args = args[1:]
        if args and not args[0].startswith("-"):
            return args[1:], "searched", False
    return [], "opened", False


class ShellReadIntents:
    """Syntax-only literal intent extractor with finite loops and shared visit bounds."""

    def __init__(self) -> None:
        self.intents: list[dict[str, Any]] = []
        self.diagnostics: list[str] = []
        self.visits = 0

    def parse(
        self, command: object, cwd: str | None = None
    ) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            directory = cwd if isinstance(cwd, str) and cwd.startswith("/") else None
            self._walk(_segments(command), directory, {}, loop=False)
        except ValueError as error:
            self.diagnostics.append(str(error))
        return self.intents, self.diagnostics

    def _walk(
        self, parts: list[Segment], directory: str | None, bindings: dict[str, str], *, loop: bool
    ) -> None:
        bindings = dict(bindings)
        index = 0
        while index < len(parts):
            raw, separator = parts[index]
            index += 1
            self.visits += len(raw) + 1
            if self.visits > 8192 or len(self.intents) >= 1024:
                raise _StopRegion("visit/intent limit")
            if not raw:
                continue
            try:
                if raw[0] == "for" and not loop:
                    index = self._loop(parts, index, raw, directory)
                else:
                    directory = self._statement(raw, separator, directory, bindings)
            except _StopRegion as error:
                self.diagnostics.append(str(error))
                return
            except ValueError as error:
                self.diagnostics.append(str(error))

    def _loop(self, parts: list[Segment], index: int, raw: list[str], directory: str | None) -> int:
        end = next((n for n in range(index, len(parts)) if parts[n][0] == ["done"]), None)
        if (
            end is None
            or len(raw) < 4
            or raw[2] != "in"
            or not re.fullmatch(r"[A-Za-z_]\w*", raw[1])
            or len(raw[3:]) > 128
        ):
            raise _StopRegion("unsupported finite loop")
        values = [_literal(word, {}) for word in raw[3:]]
        if any(any(char.isspace() for char in value) for value in values):
            raise _StopRegion("unsupported loop item whitespace")
        body = [(list(words), sep) for words, sep in parts[index:end]]
        if (
            not body
            or body[0][0][:1] != ["do"]
            or any("cd" in words or "for" in words for words, _ in body)
        ):
            raise _StopRegion("unsupported loop body")
        body[0][0].pop(0)
        for value in values:
            self._walk(body, directory, {raw[1]: value}, loop=True)
        return end + 1

    def _statement(
        self, raw: list[str], separator: str | None, directory: str | None, bindings: dict[str, str]
    ) -> str | None:
        if re.match(r"[A-Za-z_]\w*=", raw[0]):
            raise _StopRegion("unsupported control flow; stop region")
        try:
            name = _literal(raw[0], bindings)
        except ValueError as error:
            raise _StopRegion("nonliteral command name; stop region") from error
        if name in _CONTROL or any(word in {"(", ")", "||", "&"} for word in raw):
            raise _StopRegion("unsupported control flow; stop region")
        if name == "cd":
            return self._cd(raw, separator, directory, bindings)
        if name in _DISPLAY:
            return directory
        if name not in _READ:
            self.diagnostics.append("unsupported neighbor; cwd/bindings invalidated")
            bindings.clear()
            return None
        self._read(raw, directory, bindings)
        return directory

    def _cd(
        self, raw: list[str], separator: str | None, directory: str | None, bindings: dict[str, str]
    ) -> str | None:
        try:
            target = _literal(raw[1], bindings) if len(raw) == 2 else ""
        except ValueError:
            self.diagnostics.append("nonliteral cd target; cwd invalidated")
            return None
        if target and (target.startswith("/") or directory) and separator == "&&":
            return posixpath.normpath(posixpath.join(directory or "", target))
        self.diagnostics.append("cwd change not proven by cd-and")
        return None

    def _read(self, raw: list[str], directory: str | None, bindings: dict[str, str]) -> None:
        if any(re.fullmatch(r"[;&|<>()]+", word) for word in raw):
            self.diagnostics.append("pipeline/redirection neighbor ignored")
            return
        argv = [_literal(word, bindings) for word in raw]
        paths, state, whole = _operands(argv)
        if paths[:1] == ["--"]:
            paths = paths[1:]
        if any(not path or path.startswith("-") for path in paths):
            raise ValueError("ambiguous read operands")
        for path in paths:
            self._emit(path, directory, (state, whole), raw, bindings)

    def _emit(
        self,
        path: str,
        directory: str | None,
        observation: tuple[str, bool],
        raw: list[str],
        bindings: dict[str, str],
    ) -> None:
        if len(self.intents) >= 1024:
            raise _StopRegion("intent limit")
        if not path.startswith("/") and not directory:
            self.diagnostics.append("relative read without explicit cwd")
            return
        self.intents.append(
            {
                "path": posixpath.normpath(posixpath.join(directory or "", path)),
                "cwd": directory,
                "state": observation[0],
                "whole": observation[1],
                "segmentTokens": raw,
                "bindings": dict(bindings),
                "intentOnly": True,
            }
        )


def read_intents(command: object, cwd: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Extract bounded literal intents; neither shell exit status nor opening is inferred."""
    return ShellReadIntents().parse(command, cwd)


type _Value = str | int | list[str]


class _Unsupported(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Unsupported("literal-read-unsupported: " + reason)


def _text(value: _Value) -> str:
    _require(isinstance(value, str), "expected literal string")
    assert isinstance(value, str)
    return value


class _LiteralReads:
    def __init__(self, code: str, nodes: int, limit: int) -> None:
        self.code, self.limit = code, limit
        self.remaining = 4 * nodes * (limit + 1)
        self.values: dict[str, _Value] = {}
        self.aliases: dict[str, str] = {}
        self.candidates: list[dict[str, Any]] = []
        self.cwd: str | None = None

    def spend(self) -> None:
        self.remaining -= 1
        if self.remaining < 0:
            raise _Unsupported("literal-read-budget: finite AST work exhausted")

    def fresh(self, name: str) -> None:
        _require(
            name not in self.values
            and name not in self.aliases
            and name not in {"open", "print", "os"},
            "mutation or shadowing binding",
        )

    def value(self, node: ast.AST) -> _Value:
        self.spend()
        if isinstance(node, ast.Constant) and type(node.value) in (str, int):
            assert isinstance(node.value, (str, int))
            return node.value
        if isinstance(node, ast.Name) and node.id in self.values:
            return self.values[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            _require(len(node.elts) <= self.limit, "literal list exceeds frozen corpus")
            return [_text(self.value(item)) for item in node.elts]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.bounded(_text(self.value(node.left)) + _text(self.value(node.right)))
        if isinstance(node, ast.JoinedStr):
            return self.bounded("".join(self.formatted(part) for part in node.values))
        raise _Unsupported("literal-read-unsupported: expression has no finite literal value")

    def bounded(self, value: str) -> str:
        _require(len(value) <= len(self.code), "string expansion exceeds source bound")
        return value

    def formatted(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        _require(isinstance(node, ast.FormattedValue), "dynamic interpolation")
        assert isinstance(node, ast.FormattedValue)
        _require(node.conversion == -1 and node.format_spec is None, "dynamic interpolation")
        return _text(self.value(node.value))

    def banner(self, node: ast.AST) -> None:
        self.spend()
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            _require(
                isinstance(node.left, ast.Constant) and isinstance(node.left.value, str),
                "dynamic banner",
            )
            _require(isinstance(node.right, ast.Constant), "dynamic banner repeat")
            assert isinstance(node.right, ast.Constant)
            _require(
                type(node.right.value) is int and 0 <= node.right.value <= 64,
                "unbounded banner repeat",
            )
            return
        self.value(node)

    def append(self, candidate: dict[str, Any]) -> None:
        self.spend()
        if len(self.candidates) >= self.limit * 2:
            raise _Unsupported("literal-read-budget: candidate bound exhausted")
        path = candidate["path"]
        if path is not None:
            _require(bool(path) and not any(c in path for c in "\x00\n"), "invalid literal path")
            if not PurePosixPath(path).is_absolute() and self.cwd is not None:
                candidate["path"] = str(PurePosixPath(self.cwd) / path)
        self.candidates.append({**candidate, "cwd": self.cwd})

    def keywords(self, call: ast.Call) -> dict[str, _Value]:
        result: dict[str, _Value] = {}
        for keyword in call.keywords:
            _require(
                keyword.arg is not None and keyword.arg not in result, "dynamic/duplicate keyword"
            )
            assert keyword.arg is not None
            result[keyword.arg] = self.value(keyword.value)
        return result

    def projection(self, node: ast.Subscript) -> None:
        _require(
            isinstance(node.value, ast.Call) and isinstance(node.slice, ast.Constant),
            "unknown result projection",
        )
        assert isinstance(node.value, ast.Call) and isinstance(node.slice, ast.Constant)
        call = node.value
        tool = self.aliases.get(call.func.id) if isinstance(call.func, ast.Name) else None
        _require(tool in {"read_file", "terminal"}, "unknown projected tool")
        _require(
            node.slice.value == ("content" if tool == "read_file" else "output"),
            "unknown result field",
        )
        _require(len(call.args) == 1, "ambiguous native read arguments")
        argument, keywords = _text(self.value(call.args[0])), self.keywords(call)
        if tool == "read_file":
            _require(set(keywords) <= {"offset", "limit"}, "unsupported read_file keywords")
            self.append(
                {
                    "tool": tool,
                    "path": argument,
                    "whole": not keywords,
                    "command": None,
                    "workdir": None,
                }
            )
        else:
            _require(set(keywords) <= {"workdir"}, "unsupported terminal keywords")
            workdir = _text(keywords["workdir"]) if keywords else None
            self.append(
                {
                    "tool": tool,
                    "path": None,
                    "whole": False,
                    "command": argument,
                    "workdir": workdir,
                }
            )

    def open_read(self, node: ast.Call) -> None:
        _require(
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "read"
            and not node.args
            and not node.keywords,
            "unknown method call",
        )
        assert isinstance(node.func, ast.Attribute)
        call = node.func.value
        _require(isinstance(call, ast.Call), "unknown open receiver")
        assert isinstance(call, ast.Call)
        _require(
            isinstance(call.func, ast.Name) and call.func.id == "open", "unknown open callable"
        )
        _require("open" not in self.values and "open" not in self.aliases, "shadowed open")
        _require(len(call.args) == 1 and not call.keywords, "non-read open mode or arguments")
        self.append(
            {
                "tool": "python-open",
                "path": _text(self.value(call.args[0])),
                "whole": True,
                "command": None,
                "workdir": None,
            }
        )

    def request(self, node: ast.AST) -> None:
        self.spend()
        if isinstance(node, ast.Subscript):
            self.projection(node)
        elif isinstance(node, ast.Call):
            self.open_read(node)
        else:
            self.banner(node)

    def imports(self, node: ast.ImportFrom) -> None:
        _require(node.module == "hermes_tools" and node.level == 0, "unknown import")
        for alias in node.names:
            _require(alias.name in {"read_file", "terminal"}, "unknown imported tool")
            local = alias.asname or alias.name
            self.fresh(local)
            self.aliases[local] = alias.name

    def os_import(self, node: ast.Import) -> None:
        _require(len(node.names) == 1, "multiple module imports")
        alias = node.names[0]
        _require(
            alias.name == "os" and alias.asname is None and "os" not in self.aliases,
            "unknown module import",
        )
        self.aliases["os"] = "os"

    def assign(self, node: ast.Assign) -> None:
        _require(
            len(node.targets) == 1 and isinstance(node.targets[0], ast.Name), "dynamic assignment"
        )
        target = node.targets[0]
        assert isinstance(target, ast.Name)
        self.fresh(target.id)
        self.values[target.id] = self.value(node.value)

    def loop(self, node: ast.For) -> None:
        _require(isinstance(node.target, ast.Name) and not node.orelse, "unknown loop")
        assert isinstance(node.target, ast.Name)
        name, items = node.target.id, self.value(node.iter)
        self.fresh(name)
        _require(
            isinstance(items, list) and len(items) <= self.limit, "nonliteral or unbounded loop"
        )
        assert isinstance(items, list)
        for item in items:
            self.values[name] = item
            self.statements(node.body, in_loop=True)
        self.values.pop(name, None)

    def chdir(self, call: ast.Call) -> None:
        _require(isinstance(call.func, ast.Attribute), "unknown expression")
        assert isinstance(call.func, ast.Attribute)
        _require(isinstance(call.func.value, ast.Name), "unknown cwd receiver")
        assert isinstance(call.func.value, ast.Name)
        _require(
            self.aliases.get(call.func.value.id) == "os" and call.func.attr == "chdir",
            "unknown side effect",
        )
        _require(len(call.args) == 1 and not call.keywords, "unknown cwd arguments")
        directory = _text(self.value(call.args[0]))
        _require(PurePosixPath(directory).is_absolute(), "cwd is not an absolute literal")
        self.cwd = directory

    def expression(self, node: ast.Expr, *, in_loop: bool) -> None:
        _require(isinstance(node.value, ast.Call), "non-call expression")
        assert isinstance(node.value, ast.Call)
        call = node.value
        if isinstance(call.func, ast.Name) and call.func.id == "print" and not call.keywords:
            for argument in call.args:
                self.request(argument)
        else:
            _require(not in_loop, "mutation inside loop")
            self.chdir(call)

    def statement(self, node: ast.stmt, *, in_loop: bool) -> None:
        self.spend()
        if isinstance(node, ast.Expr):
            self.expression(node, in_loop=in_loop)
            return
        _require(not in_loop, "control/mutation inside loop")
        if isinstance(node, ast.ImportFrom):
            self.imports(node)
        elif isinstance(node, ast.Import):
            self.os_import(node)
        elif isinstance(node, ast.Assign):
            self.assign(node)
        elif isinstance(node, ast.For):
            self.loop(node)
        else:
            raise _Unsupported("literal-read-unsupported: dynamic/control/mutation statement")

    def statements(self, body: list[ast.stmt], *, in_loop: bool = False) -> None:
        for node in body:
            self.statement(node, in_loop=in_loop)


def extract_execute_code_reads(
    code: object, *, max_targets: int
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return candidate intents; successful reads require independently paired body evidence."""
    if type(max_targets) is not int or not 1 <= max_targets <= 64:
        return [], ["literal-read-budget: invalid frozen corpus bound"]
    if not isinstance(code, str) or len(code) > 65536:
        return [], ["literal-read-budget: source exceeds 65536 characters"]
    try:
        tree = ast.parse(code)
        nodes = sum(1 for _ in ast.walk(tree))
        if nodes > 4096:
            return [], ["literal-read-budget: AST exceeds 4096 nodes"]
        recognizer = _LiteralReads(code, nodes, max_targets)
        recognizer.statements(tree.body)
    except (SyntaxError, ValueError, RecursionError) as error:
        return [], [str(error) or "literal-read-budget: syntax recursion exhausted"]
    return recognizer.candidates, []
