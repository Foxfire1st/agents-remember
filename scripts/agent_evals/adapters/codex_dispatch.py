"""Two Codex exec wires: native TurnItems, or source-bound returned tool values.

The second wire pairs custom_tool_call(input JS) with custom_tool_call_output
(input_text blocks). We parse a bounded AST; we never execute JS or search
printed strings for tools. Supported: local-only cells, one unconditional
text(await tools.exec_command(literal/resolved object)), an apply_patch
verification failure, and a source-bound local TypeError before every tool call.
Retained E3 wires additionally prove one direct exec print followed by the
local ALL_TOOLS catalog print, or a first direct apply_patch rejection before
later source expressions. A completed script does not make an exit-1 command
successful; a rejected first await does not establish the later calls/stores.
Proven allSettled/forEach stores resolve later load data.
Everything else remains unknown with its exact schema and source identity.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from agents_remember.memory_quality.style.citations.grammars import JAVASCRIPT, language
from tree_sitter import Node, Parser

from ..artifacts import json_digest
from .models import tool_server

_LOCAL_METHODS = frozenset({"slice", "indexOf", "split", "startsWith", "filter", "map", "join"})


class UnsupportedDispatch(ValueError):
    pass


@dataclass(frozen=True)
class _Unresolved:
    reason: str


def _walk(node: Node):
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode()


def _field(node: Node, name: str) -> Node:
    value = node.child_by_field_name(name)
    if value is None:
        raise UnsupportedDispatch(f"missing AST field {node.type}.{name}")
    return value


def _args(node: Node) -> list[Node]:
    return _field(node, "arguments").named_children


def _callee(node: Node) -> str:
    return _text(_field(node, "function"))


def _string(node: Node) -> str:
    raw = _text(node)
    if raw.startswith('"'):
        return json.loads(raw)
    if raw.startswith("'"):
        return json.loads('"' + raw[1:-1].replace('"', '\\"').replace("\\'", "'") + '"')
    raise UnsupportedDispatch("unsupported string literal")


def _object_value(node: Node, symbols: dict[str, Any], stores: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for pair in node.named_children:
        if pair.type != "pair":
            raise UnsupportedDispatch("object spread/computed arguments unsupported")
        key = _field(pair, "key")
        if key.type not in ("string", "property_identifier"):
            raise UnsupportedDispatch("computed object key unsupported")
        name = _string(key) if key.type == "string" else _text(key)
        result[name] = _value(_field(pair, "value"), symbols, stores)
    return result


def _reference_value(node: Node, symbols: dict[str, Any], stores: dict[str, Any]) -> Any:
    if node.type == "identifier" and _text(node) in symbols:
        if isinstance(symbols[_text(node)], _Unresolved):
            raise UnsupportedDispatch(symbols[_text(node)].reason)
        return symbols[_text(node)]
    if node.type == "member_expression":
        parent = _value(_field(node, "object"), symbols, stores)
        key = _text(_field(node, "property"))
        if isinstance(parent, dict) and key in parent:
            return parent[key]
    if node.type == "call_expression" and _callee(node) == "load":
        args = _args(node)
        key = _value(args[0], symbols, stores) if len(args) == 1 else None
        if isinstance(key, str) and key in stores:
            return stores[key]
        raise UnsupportedDispatch(f"unbound store key {key!r}")
    raise UnsupportedDispatch(
        f"unresolved argument AST kind={node.type} value={_text(node)[:80]!r}"
    )


def _template_value(node: Node, symbols: dict[str, Any], stores: dict[str, Any]) -> str:
    parts = []
    for child in node.named_children:
        if child.type == "string_fragment" and "\\" not in _text(child):
            parts.append(_text(child))
        elif child.type == "template_substitution" and len(child.named_children) == 1:
            parts.append(str(_value(child.named_children[0], symbols, stores)))
        else:
            raise UnsupportedDispatch(f"unsupported template node {child.type}")
    return "".join(parts)


def _value(node: Node, symbols: dict[str, Any], stores: dict[str, Any]) -> Any:
    kind = node.type
    if kind in ("string", "number", "true", "false", "null"):
        return _string(node) if kind == "string" else json.loads(_text(node))
    if kind == "object":
        return _object_value(node, symbols, stores)
    if kind == "array":
        return [_value(item, symbols, stores) for item in node.named_children]
    if kind == "binary_expression" and _text(node.child_by_field_name("operator")) == "+":
        return _value(_field(node, "left"), symbols, stores) + _value(
            _field(node, "right"), symbols, stores
        )
    if kind == "template_string":
        return _template_value(node, symbols, stores)
    return _reference_value(node, symbols, stores)


def _symbols(tree: Node, stores: dict[str, Any]) -> dict[str, Any]:
    values = {}
    for statement in tree.named_children:
        if statement.type not in ("lexical_declaration", "variable_declaration"):
            continue
        for declaration in statement.named_children:
            if declaration.type != "variable_declarator":
                continue
            try:
                values[_text(_field(declaration, "name"))] = _value(
                    _field(declaration, "value"), values, stores
                )
            except (UnsupportedDispatch, ValueError, KeyError, TypeError) as error:
                values[_text(_field(declaration, "name"))] = _Unresolved(str(error))
    return values


def _blocks(output: dict[str, Any]) -> list[str]:
    blocks = output.get("output")
    if not isinstance(blocks, list):
        raise UnsupportedDispatch(
            f"expected input_text array; kind={type(blocks).__name__}; JSONPath=$.payload.output; sha256={json_digest(blocks)}"
        )
    for index, block in enumerate(blocks):
        if (
            not isinstance(block, dict)
            or set(block) != {"type", "text"}
            or block["type"] != "input_text"
            or not isinstance(block["text"], str)
        ):
            kind = block.get("type") if isinstance(block, dict) else type(block).__name__
            keys = sorted(block) if isinstance(block, dict) else []
            raise UnsupportedDispatch(
                f"expected exact input_text block; kind={kind}; keys={keys}; JSONPath=$.payload.output[{index}]; sha256={json_digest(block)}"
            )
    return [block["text"] for block in blocks]


def _completed(blocks: list[str]) -> bool:
    return bool(
        blocks
        and re.fullmatch(r"Script completed\nWall time [0-9.]+ seconds\nOutput:\n", blocks[0])
    )


def _local_call(node: Node) -> bool:
    name = _callee(node)
    if name in ("text", "load", "store", "JSON.parse", "Object.keys"):
        return True
    function = _field(node, "function")
    if function.type != "member_expression":
        return False
    method = _text(_field(function, "property"))
    return method in _LOCAL_METHODS or (
        method == "test" and _field(function, "object").type == "regex"
    )


def _tool_calls(tree: Node) -> list[Node]:
    return [
        node
        for node in _walk(tree)
        if node.type == "call_expression" and _callee(node).startswith("tools.")
    ]


def _pure(tree: Node) -> bool:
    return all(
        _local_call(node) for node in _walk(tree) if node.type == "call_expression"
    ) and not any(
        node.type in ("new_expression", "import", "import_statement")
        or (
            node.type == "identifier"
            and _text(node) in ("tools", "eval", "Function", "globalThis", "require")
        )
        for node in _walk(tree)
    )


def _immutable_declarations(tree: Node) -> None:
    mutations = {"assignment_expression", "augmented_assignment_expression", "update_expression"}
    for node in _walk(tree):
        if node.type in mutations:
            raise UnsupportedDispatch(
                f"mutating AST kind={node.type} is outside the immutable binding grammar"
            )
    names = []
    for statement in tree.named_children:
        if statement.type in ("lexical_declaration", "variable_declaration"):
            for declaration in statement.named_children:
                name = declaration.child_by_field_name("name")
                if name is None or name.type != "identifier" or _text(name) in names:
                    raise UnsupportedDispatch("ambiguous or destructured variable binding")
                names.append(_text(name))


def _direct_statement(tree: Node, tool: Node) -> tuple[Node, Node]:
    parent = tool.parent
    if (
        parent is None
        or parent.type != "await_expression"
        or parent.parent is None
        or parent.parent.type != "arguments"
    ):
        raise UnsupportedDispatch("tool call is not a direct awaited text result")
    text_call = parent.parent.parent
    if text_call is None or text_call.type != "call_expression" or _callee(text_call) != "text":
        raise UnsupportedDispatch("tool result is not passed directly to text")
    if len(_args(text_call)) != 1:
        raise UnsupportedDispatch("direct tool print requires exactly one argument")
    statement = text_call.parent
    if statement is None or statement.type != "expression_statement" or statement.parent != tree:
        raise UnsupportedDispatch("tool call is conditional/nested, not unconditional")
    return statement, text_call


def _direct_call(tree: Node, calls: list[Node]) -> Node:
    _immutable_declarations(tree)
    if len(calls) != 1:
        raise UnsupportedDispatch(f"returned-value wire needs one tool call, found {len(calls)}")
    tool = calls[0]
    statement, text_call = _direct_statement(tree, tool)
    if any(
        node.type in ("lexical_declaration", "variable_declaration")
        and node.start_byte > statement.start_byte
        for node in tree.named_children
    ):
        raise UnsupportedDispatch("declaration after tool call cannot bind earlier arguments")
    if any(
        node != statement
        and node.type not in ("lexical_declaration", "variable_declaration", "comment")
        for node in tree.named_children
    ):
        raise UnsupportedDispatch(
            "unsupported top-level statement before/after the direct tool call"
        )
    other_calls = [
        node
        for node in _walk(tree)
        if node.type == "call_expression" and node not in (tool, text_call)
    ]
    if any(_callee(node) != "load" for node in other_calls):
        raise UnsupportedDispatch("returned-value cell has extra calls")
    return tool


def _ast_shape(node: Node) -> tuple[Any, ...]:
    """Exact observed syntax tree, ignoring only insignificant whitespace."""
    if not node.children:
        return node.type, _text(node)
    return node.type, tuple(_ast_shape(child) for child in node.children)


def _catalog_print(statement: Node) -> bool:
    # CE11 retained input: the callbacks read native catalog data only. Match
    # the actual AST, including literal regex receiver and callback bodies;
    # an arbitrary method called .test/.map is not proof of local-only work.
    source = (
        "text(ALL_TOOLS.filter(x=>/skills.*(list|read)/.test(x.name))"
        ".map(x=>({name:x.name,description:x.description.slice(0,100)})));"
    )
    template = Parser(language(JAVASCRIPT)).parse(source.encode()).root_node
    return _ast_shape(statement) == _ast_shape(template.named_children[0])


def _patch_refused(blocks: list[str]) -> bool:
    return bool(
        len(blocks) == 2
        and re.fullmatch(r"Script failed\nWall time [0-9.]+ seconds\nOutput:\n", blocks[0])
        and blocks[1].startswith("Script error:\napply_patch verification failed:")
    )


def _returned_call(
    tree: Node, blocks: list[str], children: list[dict[str, Any]]
) -> tuple[Node, str]:
    calls = _tool_calls(tree)
    statements = [node for node in tree.named_children if node.type != "comment"]
    if len(calls) > 1 and _patch_refused(blocks):
        _immutable_declarations(tree)
        tool = calls[0]
        statement, _ = _direct_statement(tree, tool)
        args = _args(tool)
        if (
            children
            or statement != statements[0]
            or _callee(tool) != "tools.apply_patch"
            or len(args) != 1
            or args[0].type != "string"
        ):
            raise UnsupportedDispatch("refused patch is not the first direct literal tool attempt")
        # The first awaited call rejected before text produced any value.
        # Its exact native tool-specific error is the only data block; later
        # source calls and stores are not executed receipts.
        return tool, "failed-first-apply-patch"
    if (
        len(calls) == 1
        and len(statements) == 2
        and _completed(blocks)
        and len(blocks) == 3
        and _catalog_print(statements[1])
    ):
        _immutable_declarations(tree)
        tool = calls[0]
        statement, _ = _direct_statement(tree, tool)
        if statement != statements[0] or _callee(tool) != "tools.exec_command" or children:
            raise UnsupportedDispatch("catalog wire requires one first direct exec print")
        catalog = json.loads(blocks[2])
        if not isinstance(catalog, list) or any(
            not isinstance(row, dict)
            or set(row) != {"name", "description"}
            or not all(isinstance(value, str) for value in row.values())
            for row in catalog
        ):
            raise UnsupportedDispatch("local catalog output has unsupported row shape")
        return tool, "tool-then-local-catalog"
    return _direct_call(tree, calls), "direct-tool-value"


@dataclass(frozen=True)
class DispatchReturn:
    kind: str
    call_id: str
    name: str | None = None
    arguments: Any = None
    result: Any = None
    provenance: dict[str, Any] = field(default_factory=dict)


def _local_failure_site(tree: Node, blocks: list[str]) -> tuple[int, str] | None:
    """Bind this native TypeError frame to its exact ASCII source position."""
    if len(blocks) != 2 or not re.fullmatch(
        r"Script failed\nWall time [0-9.]+ seconds\nOutput:\n", blocks[0]
    ):
        return None
    frame = re.fullmatch(
        r"Script error:\nTypeError: Cannot read properties of undefined \(reading '([0-9]+)'\)"
        r"\n    at exec_main\.mjs:([1-9][0-9]*):([1-9][0-9]*)",
        blocks[1],
    )
    script = _text(tree)
    if frame is None or not script.isascii():
        return None
    property_name, line_number, column_number = frame.groups()
    lines = script.splitlines(keepends=True)
    row, column = int(line_number) - 1, int(column_number) - 1
    if row >= len(lines) or column >= len(lines[row].rstrip("\r\n")):
        return None
    return sum(len(line) for line in lines[:row]) + column, property_name


def _failed_declaration(tree: Node, offset: int, property_name: str) -> Node | None:
    for node in _walk(tree):
        if node.type != "subscript_expression" or _text(_field(node, "index")) != property_name:
            continue
        brackets = [child for child in node.children if child.type == "["]
        if len(brackets) != 1 or brackets[0].start_byte != offset:
            continue
        statement = node
        while statement.parent is not None and statement.parent != tree:
            statement = statement.parent
        if statement.type == "lexical_declaration" and _pure(statement):
            return statement
    return None


def _failed_local_prefix(tree: Node, blocks: list[str], children: list[dict[str, Any]]) -> bool:
    site = _local_failure_site(tree, blocks)
    if site is None or children:
        return False
    statement = _failed_declaration(tree, *site)
    if statement is None:
        return False
    prefix = [node for node in tree.named_children if node.start_byte < statement.end_byte]
    if any(
        node.type not in ("lexical_declaration", "expression_statement", "comment")
        or not _pure(node)
        for node in prefix
    ):
        return False
    return all(tool.start_byte >= statement.end_byte for tool in _tool_calls(tree))


def _returned(
    call: dict[str, Any],
    output: dict[str, Any],
    tree: Node,
    stores: dict[str, Any],
    children: list[dict[str, Any]],
) -> DispatchReturn:
    blocks = _blocks(output)
    if _pure(tree):
        return DispatchReturn("local-only", call["call_id"])
    if _failed_local_prefix(tree, blocks, children):
        # The native error is retained on the outer result. The later tool
        # expression was never reached, so it creates no child action/result.
        return DispatchReturn("failed-local-prefix", call["call_id"])
    tool, shape = _returned_call(tree, blocks, children)
    name = _callee(tool).removeprefix("tools.")
    args = _args(tool)
    if len(args) != 1:
        raise UnsupportedDispatch("tool requires exactly one argument")
    arguments = _value(args[0], _symbols(tree, stores), stores)
    if (
        name == "exec_command"
        and _completed(blocks)
        and (len(blocks) == 2 or shape == "tool-then-local-catalog")
    ):
        result = json.loads(blocks[1])
        required = {"chunk_id", "wall_time_seconds", "output"}
        if (
            not isinstance(result, dict)
            or not required <= result.keys()
            or not ({"exit_code", "session_id"} & result.keys())
        ):
            raise UnsupportedDispatch(
                "exec_command return missing native chunk/output/exit-or-session fields"
            )
    elif name == "apply_patch" and _patch_refused(blocks):
        result = {"status": "failed", "error": blocks[1], "nativeScriptHeader": blocks[0]}
    else:
        raise UnsupportedDispatch(f"unrecognized returned-value tool/header shape: {name}")
    provenance = {
        "wire": "custom_tool_call_output.input_text",
        "outerCallId": call["call_id"],
        "identityBasis": "outer-call-id plus zero-based direct-tool index; not a native TurnItem id",
        "inputSha256": json_digest(call),
        "outputSha256": json_digest(output),
    }
    if shape != "direct-tool-value":
        provenance["returnedShape"] = shape
    if shape == "tool-then-local-catalog":
        provenance["localOutput"] = output["output"][2]
    return DispatchReturn(
        "returned-tool-value",
        call["call_id"],
        name,
        arguments,
        result,
        provenance,
    )


def _loop_body_matches(arrow: Node, value: Node, index: Node) -> bool:
    parameters = arrow.child_by_field_name("parameters")
    if parameters is None or [_text(part) for part in parameters.named_children] != [
        _text(value),
        _text(index),
    ]:
        return False
    statements = _field(arrow, "body").named_children
    if len(statements) != 2 or any(
        statement.type != "expression_statement" for statement in statements
    ):
        return False
    calls = [statement.named_children[0] for statement in statements]
    if any(call.type != "call_expression" for call in calls) or [
        _callee(call) for call in calls
    ] != ["store", "text"]:
        return False
    output_args = _args(calls[1])
    if len(output_args) != 1 or output_args[0].type != "object":
        return False
    fields = output_args[0].named_children
    return (
        len(fields) == 2
        and _text(fields[0]) == _text(index)
        and fields[1].type == "spread_element"
        and _text(fields[1].named_children[0]) == _text(value)
    )


def _settled_calls(tree: Node, variable: str) -> list[Node] | None:
    for declaration in _walk(tree):
        if (
            declaration.type != "variable_declarator"
            or _text(_field(declaration, "name")) != variable
        ):
            continue
        expression = _field(declaration, "value")
        if expression.type != "await_expression" or len(expression.named_children) != 1:
            continue
        promise = expression.named_children[0]
        if promise.type == "call_expression" and _callee(promise) == "Promise.allSettled":
            args = _args(promise)
            if len(args) == 1 and args[0].type == "array":
                return args[0].named_children
    return None


def _loop_binding(node: Node, tree: Node) -> tuple[str, list[Node]] | None:
    key, value = _args(node)
    if key.type != "binary_expression" or value.type != "identifier":
        return None
    left, right = _field(key, "left"), _field(key, "right")
    if (
        left.type != "string"
        or right.type != "identifier"
        or _text(key.child_by_field_name("operator")) != "+"
    ):
        return None
    arrow = node.parent
    while arrow is not None and arrow.type != "arrow_function":
        arrow = arrow.parent
    if arrow is None or arrow.parent is None or arrow.parent.parent is None:
        return None
    function = arrow.parent.parent.child_by_field_name("function")
    if (
        function is None
        or function.type != "member_expression"
        or _text(_field(function, "property")) != "forEach"
        or not _loop_body_matches(arrow, value, right)
    ):
        return None
    calls = _settled_calls(tree, _text(_field(function, "object")))
    return (_string(left), calls) if calls is not None else None


def _store_rows(
    prefix: str,
    calls: list[Node],
    blocks: list[str],
    context: tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]],
) -> None:
    symbols, stores, children = context
    rows = [json.loads(block) for block in blocks]
    if len(rows) != len(calls):
        return
    for index, (call, row) in enumerate(zip(calls, rows, strict=True)):
        if (
            not isinstance(row, dict)
            or set(row) != {"i", "status", "value"}
            or row["i"] != index
            or row["status"] != "fulfilled"
            or call.type != "call_expression"
        ):
            continue
        name = _callee(call).removeprefix("tools.")
        server = tool_server(name)
        if server is None or len(_args(call)) != 1:
            continue
        try:
            args = _value(_args(call)[0], symbols, stores)
        except (UnsupportedDispatch, ValueError, TypeError, KeyError):
            continue
        matches = [
            child
            for child in children
            if child.get("type") == "McpToolCall"
            and child.get("server") == server
            and child.get("tool") == name.rsplit("__", 1)[-1]
            and child.get("arguments") == args
            and child.get("result") == row["value"]
        ]
        if len(matches) == 1:
            stores[prefix + str(index)] = {"status": "fulfilled", "value": row["value"]}


def _store_statement(statement: Node, tree: Node) -> bool:
    if statement.type != "expression_statement" or not statement.named_children:
        return False
    call = statement.named_children[0]
    if call.type != "call_expression":
        return False
    if _callee(call) == "store":
        return len(_args(call)) == 2 and all(
            node == call or _callee(node) == "load"
            for node in _walk(call)
            if node.type == "call_expression"
        )
    function = _field(call, "function")
    if function.type != "member_expression" or _text(_field(function, "property")) != "forEach":
        return False
    return any(
        _loop_binding(node, tree) is not None
        for node in _walk(call)
        if node.type == "call_expression" and _callee(node) == "store" and len(_args(node)) == 2
    )


def _store_declaration(statement: Node, tree: Node) -> bool:
    for declaration in statement.named_children:
        name = declaration.child_by_field_name("name")
        calls = [node for node in _walk(declaration) if node.type == "call_expression"]
        if all(_callee(node) == "load" for node in calls):
            continue
        settled = _settled_calls(tree, _text(name))
        if settled is None or any(
            _callee(node) not in ("load", "Promise.allSettled") and node not in settled
            for node in calls
        ):
            return False
    return True


def _store_grammar(tree: Node) -> bool:
    try:
        _immutable_declarations(tree)
    except UnsupportedDispatch:
        return False
    saw_expression = False
    for statement in tree.named_children:
        if statement.type == "comment":
            continue
        if statement.type in ("lexical_declaration", "variable_declaration"):
            if saw_expression or not _store_declaration(statement, tree):
                return False
        elif _store_statement(statement, tree):
            saw_expression = True
        else:
            return False
    return True


def _direct_return(tree: Node) -> bool:
    try:
        _direct_call(tree, _tool_calls(tree))
    except UnsupportedDispatch:
        return False
    return True


def _store_results(
    tree: Node, output: dict[str, Any], children: list[dict[str, Any]], stores: dict[str, Any]
) -> None:
    """Recover only literal stores or exact source/native-bound allSettled stores."""
    blocks = _blocks(output)
    if _completed(blocks) and _direct_return(tree):
        return
    if not _completed(blocks) or not _store_grammar(tree):
        # Unsupported local code still has no tool action, but must never
        # supply state to a subsequent action. Invalidate potentially mutated
        # store state rather than reconstructing a stale argument value.
        if any(
            node.type
            in ("assignment_expression", "augmented_assignment_expression", "update_expression")
            or (node.type == "call_expression" and _callee(node) in ("load", "store"))
            for node in _walk(tree)
        ):
            stores.clear()
        return
    symbols = _symbols(tree, stores)
    for statement in tree.named_children:
        if statement.type == "expression_statement" and statement.named_children:
            node = statement.named_children[0]
            if (
                node.type == "call_expression"
                and _callee(node) == "store"
                and len(_args(node)) == 2
            ):
                key, value = _args(node)
                with suppress(UnsupportedDispatch, ValueError, KeyError, TypeError):
                    stores[_value(key, symbols, stores)] = _value(value, symbols, stores)
    for node in _walk(tree):
        if node.type == "call_expression" and _callee(node) == "store" and len(_args(node)) == 2:
            binding = _loop_binding(node, tree)
            if binding is not None:
                _store_rows(*binding, blocks[1:], (symbols, stores, children))


@dataclass
class _Pending:
    call: dict[str, Any]
    tree: Node
    children: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Scan:
    session_id: str
    active: dict[str, _Pending] = field(default_factory=dict)
    stores: dict[str, Any] = field(default_factory=dict)
    returns: dict[str, DispatchReturn] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def _native_child(scan: _Scan, payload: dict[str, Any]) -> None:
    if (
        len(scan.active) != 1
        or payload.get("thread_id") != scan.session_id
        or not isinstance(payload.get("item"), dict)
    ):
        return
    pending = next(iter(scan.active.values()))
    metadata = pending.call.get("internal_chat_message_metadata_passthrough")
    turn = metadata.get("turn_id") if isinstance(metadata, dict) else None
    if isinstance(turn, str) and turn == payload.get("turn_id"):
        pending.children.append(payload["item"])


def _close(scan: _Scan, identity: str, output: dict[str, Any], covered: set[str]) -> None:
    pending = scan.active.pop(identity)
    call, tree = pending.call, pending.tree
    try:
        if tree.has_error:
            raise UnsupportedDispatch("invalid JavaScript AST")
        _store_results(tree, output, pending.children, scan.stores)
        if identity not in covered:
            scan.returns[identity] = _returned(call, output, tree, scan.stores, pending.children)
    except (UnsupportedDispatch, ValueError, KeyError, TypeError) as error:
        if identity not in covered:
            scan.errors[identity] = (
                f"{error}; inputKind={call.get('type')}; inputKeys={sorted(call)}; inputSha256={json_digest(call)}; outputKind={output.get('type')}; outputKeys={sorted(output)}; outputSha256={json_digest(output)}; outputJSONPath=$.payload.output"
            )


def returned_dispatches(
    records: list[dict[str, Any]], covered: set[str], session_id: str
) -> tuple[dict[str, DispatchReturn], dict[str, str]]:
    parser = Parser(language(JAVASCRIPT))
    scan = _Scan(session_id)
    for row in records:
        payload = row.get("payload", {})
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "event_msg" and payload.get("type") == "item_completed":
            _native_child(scan, payload)
        elif row.get("type") == "response_item":
            _response(scan, payload, covered, parser)
    for identity, pending in scan.active.items():
        scan.errors[identity] = (
            f"missing paired dispatch output; inputKind={pending.call.get('type')}; inputKeys={sorted(pending.call)}; outputJSONPath=$.payload.output"
        )
    return scan.returns, scan.errors


def _response(scan: _Scan, payload: dict[str, Any], covered: set[str], parser: Parser) -> None:
    identity = payload.get("call_id")
    if (
        payload.get("type") in ("custom_tool_call", "function_call")
        and payload.get("name") in ("exec", "functions.exec")
        and isinstance(identity, str)
    ):
        script = payload.get("input", payload.get("arguments"))
        if isinstance(script, str):
            scan.active[identity] = _Pending(payload, parser.parse(script.encode()).root_node)
        else:
            scan.errors[identity] = (
                f"non-string dispatch input; inputKind={payload.get('type')}; inputKeys={sorted(payload)}; inputJSONPath=$.payload.input"
            )
    elif (
        payload.get("type") in ("custom_tool_call_output", "function_call_output")
        and identity in scan.active
    ):
        _close(scan, identity, payload, covered)
