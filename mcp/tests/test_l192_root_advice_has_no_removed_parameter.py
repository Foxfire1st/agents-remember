"""L192-F06: no live carrier may instruct a ``context_packet`` argument the schema removed.

The shipped tool (``mcp/src/agents_remember/mcp/registration/core.py``) and the internal
``ContextPacketRequest`` admit ``repo_id``, ``include_drift``, ``include_freshness`` and the
leaf's ``task_context``; ``include_providers`` is gone, and the packet's provider projection is
fixed to ``include_providers=False``.  Advice that still tells a reader to pass the removed flag
therefore names an invalid invocation of a shipped tool.

These tests derive the admitted parameter set from the real advertised schema -- one FastMCP
server built from ``TOOL_REGISTRARS``, the same way the other tool-schema cases do -- and then
require every ``context_packet(...)`` call written in a live carrier to name only those
parameters.  The carrier inventory is the shipped instruction and declaration surface: the root
``AGENTS.md``, the canonical ``skills/`` tree, every committed projection of it, ``agents-md-files/``,
the packaged runtime copies, ``docs/``, ``README.md``, ``mcp/README.md`` and ``system/``.

Nothing here fixes the carriers: a failing case names the file and line that still prescribes the
removed argument.
"""

from __future__ import annotations

import ast
import asyncio
import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from agents_remember.application.context_packet import ContextPacketRequest
from agents_remember.mcp.registration import TOOL_REGISTRARS
from agents_remember_test_support.testing.curation_doctrine import GENERATED_SKILL_COPIES
from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parents[2]

TOOL_NAME = "context_packet"

#: The removed knob this finding is about.  Spelled once: every case compares against it.
REMOVED_PARAMETER = "include_providers"

#: The carriers that a reader (or a role seat's own instruction load) may act on.  A generated
#: copy is added per committed projection root, so a new harness cannot ship an unreported call.
CARRIER_GLOBS = (
    "AGENTS.md",
    "README.md",
    "mcp/README.md",
    "agents-md-files/**/*.md",
    "docs/**/*.md",
    "skills/**/*.md",
    *(f"{copy}/**/*.md" for copy in GENERATED_SKILL_COPIES),
    "mcp/src/agents_remember/package_data/runtime/**/*.md",
    "system/**/*.md",
    "dev-skills/**/*.md",
)

#: One shipped surface per entry, so a carrier glob that silently stops matching is a failure.
REQUIRED_SURFACE_ROOTS = (
    "AGENTS.md",
    "skills",
    "docs",
    "agents-md-files",
    "mcp/src/agents_remember/package_data/runtime",
    "system",
)

CALL_SITE = re.compile(rf"\b{TOOL_NAME}\s*\(")
PROSE_LITERALS = {"true": "True", "false": "False", "null": "None"}


@dataclass(frozen=True)
class CarrierCall:
    """One written ``context_packet`` invocation, with the parameter names it spells."""

    path: str
    line: int
    parameters: tuple[str, ...]

    def location(self) -> str:
        return f"{self.path}:{self.line}"


def _advertised_parameters() -> frozenset[str]:
    """The parameter names the shipped MCP server advertises for ``context_packet``."""

    server: Any = FastMCP("l192-f06-schema")
    for register_tools in TOOL_REGISTRARS:
        register_tools(server, None)  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert TOOL_NAME in tools, sorted(tools)
    schema = dict(tools[TOOL_NAME].inputSchema or {})
    properties = dict(schema.get("properties", {}))
    assert properties, schema
    return frozenset(properties)


def _arguments(open_index: int, text: str) -> str | None:
    """The text between a call's parentheses, or ``None`` when the call is never closed."""

    depth = 0
    quote: str | None = None
    index = open_index
    while index < len(text):
        character = text[index]
        if quote is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in "\"'":
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : index]
        index += 1
    return None


def _parameters(arguments: str) -> tuple[str, ...] | None:
    """The keyword names a written call spells, or ``None`` when the text is not a call."""

    normalized = re.sub(
        r"\b(true|false|null)\b",
        lambda match: PROSE_LITERALS[match.group(1)],
        arguments,
    )
    try:
        call = ast.parse(f"_carrier({normalized})", mode="eval").body
    except SyntaxError:
        return None
    assert isinstance(call, ast.Call), call
    return tuple(keyword.arg for keyword in call.keywords if keyword.arg)


def carrier_paths(root: Path) -> list[Path]:
    """Every live carrier file under ``root``, sorted, without duplicates."""

    found: dict[str, Path] = {}
    for pattern in CARRIER_GLOBS:
        for path in root.glob(pattern):
            if path.is_file():
                found[path.relative_to(root).as_posix()] = path
    return [found[key] for key in sorted(found)]


def carrier_calls(root: Path | None = None) -> list[CarrierCall]:
    """Every ``context_packet`` call written in a live carrier under ``root``.

    A call whose argument text cannot be read as a call is a failure in its own right: it would
    otherwise slip past the parameter check by being unparseable prose.
    """

    base = REPO_ROOT if root is None else root
    calls: list[CarrierCall] = []
    unreadable: list[str] = []
    for path in carrier_paths(base):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(base).as_posix()
        for match in CALL_SITE.finditer(text):
            arguments = _arguments(match.end() - 1, text)
            parameters = None if arguments is None else _parameters(arguments)
            if parameters is None:
                unreadable.append(f"{relative}:{text.count(chr(10), 0, match.start()) + 1}")
                continue
            calls.append(
                CarrierCall(
                    path=relative,
                    line=text.count("\n", 0, match.start()) + 1,
                    parameters=parameters,
                )
            )
    assert not unreadable, (
        "these carriers write a context_packet call whose arguments could not be read as a "
        f"call: {unreadable}"
    )
    return calls


def test_the_advertised_schema_never_admits_the_removed_parameter() -> None:
    admitted = _advertised_parameters()

    assert REMOVED_PARAMETER not in admitted, admitted
    assert "repo_id" in admitted, admitted
    # The leaf binding and the two measurement options survive: removing them would be a
    # different defect, not a repair of this one.
    assert "task_context" in admitted, admitted
    assert {"include_drift", "include_freshness"} <= admitted, admitted


def test_the_internal_request_never_readmits_the_removed_parameter() -> None:
    admitted = _advertised_parameters()
    request_fields = frozenset(field.name for field in fields(ContextPacketRequest))

    assert REMOVED_PARAMETER not in request_fields, request_fields
    # The request still carries the three arguments the tool forwards; an internal knob
    # (a drift limit, a fetch timeout) is not an advertised argument, so the request is not
    # required to be a subset of the schema -- it is required to forward only advertised ones.
    forwarded = {"repo_id", "include_drift", "include_freshness"}
    assert forwarded <= request_fields, request_fields
    assert forwarded <= admitted, admitted


def test_no_carrier_instructs_the_removed_parameter() -> None:
    offenders = [
        call.location() for call in carrier_calls() if REMOVED_PARAMETER in call.parameters
    ]

    assert not offenders, (
        f"these carriers still prescribe {TOOL_NAME}(... {REMOVED_PARAMETER}=...), which the "
        f"shipped schema does not admit ({sorted(_advertised_parameters())}): {offenders}"
    )


def test_every_carrier_call_names_only_admitted_parameters() -> None:
    admitted = _advertised_parameters()
    offending = [
        (call.location(), sorted(set(call.parameters) - admitted))
        for call in carrier_calls()
        if set(call.parameters) - admitted
    ]

    assert not offending, (
        f"these carriers call {TOOL_NAME} with parameters the shipped schema does not admit "
        f"({sorted(admitted)}): {offending}"
    )


def test_the_carrier_inventory_covers_every_shipped_surface() -> None:
    base = REPO_ROOT
    paths = [path.relative_to(base).as_posix() for path in carrier_paths(base)]

    assert "AGENTS.md" in paths, paths
    for surface in REQUIRED_SURFACE_ROOTS:
        assert any(path == surface or path.startswith(f"{surface}/") for path in paths), (
            f"{surface} contributed no carrier: {paths}"
        )
    for copy in GENERATED_SKILL_COPIES:
        assert any(path.startswith(f"{copy}/") for path in paths), (copy, paths)
    # The canonical skills tree and its copies both resolve to real files, not a stale glob.
    # MIK-R196 retired the per-harness skill folders, so the package-data copy is the only
    # generated one now; an empty tuple would silently switch off the per-copy assertion above.
    assert GENERATED_SKILL_COPIES, GENERATED_SKILL_COPIES
    assert len(carrier_calls(base)) >= 10, [call.location() for call in carrier_calls(base)]
