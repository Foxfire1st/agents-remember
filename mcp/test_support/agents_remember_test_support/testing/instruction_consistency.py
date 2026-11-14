"""MIK-R62 rule 10: executable names in the complete instruction/document corpus.

The parser, live MCP registrations and task-document dispatcher own the vocabularies.
Only a same-line conversion marker exempts retired terminology, never invalid names.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from agents_remember.application.task_docs.task_doc_tools import VALID_OPERATIONS
from agents_remember.cli.__main__ import build_parser
from agents_remember.cli.leaf_enclosure_start import COMMAND as ENCLOSURE_COMMAND
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.registration import TOOL_REGISTRARS
from mcp.server.fastmcp import FastMCP

from agents_remember_test_support.testing.capsule_budget_d695 import load_current_budget_view

# Exact list retained by MIK-R62 v1 rule 10 and v2's Unchanged section.
RETIRED_TERMS = (
    "knowledge.sqlite",
    "published dataset",
    "--baseline",
    "--publish",
    "review-record-comparison",
    "Update History",
    "until the cutover",
)
CONVERSION_MARKER = "<!-- ar-old-format-conversion -->"
_CODE = re.compile(r"`+([^`\n]+)`+")
_PATH = re.compile(r"(?<![\w/-])(?:mcp/src/agents_remember|skills)/[\w./*?{}+-]*")
_CLI = re.compile(r"\bagents-remember(?:\s+)([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?")
_COMMAND_LABEL = re.compile(
    r"(?:subcommand|command)\s+[`\"']([a-z][a-z0-9-]*(?: [a-z][a-z0-9-]*)?)[`\"']"
)
_OPERATION = re.compile(r"\boperation[\"']?\s*[:=]\s*[\"'`]([a-z_]+)")
_DOT_OPERATION = re.compile(r"\btask_doc\.([a-z_]+)")
_TOOL_CALL = re.compile(r"\b([a-z][a-z0-9_]*_[a-z0-9_]+)\s*\(")
_TOOL_ACTION = re.compile(
    r"(?i:\b(?:call|use|invoke)\s+(?:the\s+)?)"
    r"(?:`([a-z][a-z0-9_]*_[a-z0-9_]+)`|([a-z][a-z0-9_]*_[a-z0-9_]+)\s*\()"
)
_INLINE_CALL = re.compile(r"([a-z][a-z0-9_]*_[a-z0-9_]+)\s*\(")
_STANDALONE_CALL = re.compile(
    r"\s*(?:(?:[-*]|\d+\.)\s+)?([a-z][a-z0-9_]*_[a-z0-9_]+)\s*\(.*\)[.;]?\s*"
)
_TOOL_LABEL = re.compile(
    r"(?:\bMCP\s+tool|\btool)\s+[`\"']([a-z][a-z0-9_]+)[`\"']"
    r"|[`\"']([a-z][a-z0-9_]+)[`\"']\s+(?:MCP\s+)?tool\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Surface:
    path: str
    text: str


@dataclass(frozen=True)
class Registry:
    commands: frozenset[str]
    tools: frozenset[str]
    operations: frozenset[str]
    descriptions: tuple[Surface, ...] = ()
    parameters: frozenset[str] = frozenset()

    @property
    def knowledge_names(self) -> frozenset[str]:
        return frozenset(
            name
            for name in self.commands | self.tools
            if name.startswith(("knowledge-", "knowledge_")) or name == "handoff-check"
        )


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    kind: str
    name: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.name}"


# Commands outside the role instruction table. The check demands no row and no mention for
# them and refuses a row for one of them in that table. `knowledge-convert` is told on two
# guide pages, for a memory repository that was cloned before `memory_init`.
# `explainer-browser` is told on the install page and in the guide: a person at the installation
# runs it to see or fetch the browser the explainer publishes with, and no role has a step for it.
UNDOCUMENTED_COMMANDS = frozenset(
    {
        "explainer-browser",
        "knowledge-convert",
        "knowledge-census",
        "knowledge-census inventory",
        "knowledge-census report",
        "knowledge-copies",
        "memory-backfill",
        "upgrade-user-settings",
        "providers-remove",
        "removed-stores",
    }
)


def public_commands() -> frozenset[str]:
    """Enumerate every public parent and nested command from the real parser."""
    commands: set[str] = set()
    pending = [("", build_parser())]
    while pending:
        prefix, parser = pending.pop()
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    command = f"{prefix} {name}".strip()
                    commands.add(command)
                    pending.append((command, child))
    return frozenset(commands)


def command_reference(root: Path) -> str:
    """Read the role instruction table of commands."""
    return (root / "skills/l-01-agent-lifecycles/reference/knowledge/commands.md").read_text()


def _instruction_command_row(reference: str, command: str) -> str:
    rows = re.findall(rf"(?m)^\| `{re.escape(command)}` \|.*\|$", reference)
    assert len(rows) == 1, command
    cells = re.split(r"(?<!\\)\|", rows[0])[1:-1]
    assert len(cells) == 4 and all(cell.strip() for cell in cells), command
    return rows[0]


def assert_public_command_reference(reference: str) -> None:
    """Require one row for every public command, and none for an undocumented command."""
    commands = public_commands()
    assert commands >= UNDOCUMENTED_COMMANDS
    for command in sorted(commands):
        if command not in UNDOCUMENTED_COMMANDS:
            _instruction_command_row(reference, command)
            continue
        assert f"| `{command}` |" not in reference, command


def misplace_command_documentation(reference: str, command: str) -> str:
    """Take the command's row away, or give a row to a command that has none."""
    if command in UNDOCUMENTED_COMMANDS:
        return f"{reference}\n| `{command}` | What it does | Who and when | Example |\n"
    row = _instruction_command_row(reference, command)
    return reference.replace(row, "", 1)


def load_registry(root: Path) -> Registry:
    """Observe registration only; do not start a server or call any handler."""
    commands = {ENCLOSURE_COMMAND, *public_commands()}
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs",
    )
    server = FastMCP("instruction-consistency-registration")
    for register in TOOL_REGISTRARS:
        register(server, config)
    registered = asyncio.run(server.list_tools())
    return Registry(
        frozenset(commands),
        frozenset(tool.name for tool in registered),
        frozenset(VALID_OPERATIONS),
        tuple(Surface(f"tool:{tool.name}", tool.description or "") for tool in registered),
        frozenset(name for tool in registered for name in tool.inputSchema.get("properties", {})),
    )


def read_corpus(root: Path) -> tuple[Surface, ...]:
    """Every text file in canonical skills/docs, plus root README; no sampling."""
    paths = {root / "README.md"}
    for directory in ("skills", "docs"):
        paths.update(path for path in (root / directory).rglob("*") if path.is_file())
    surfaces = []
    for path in sorted(paths):
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        surfaces.append(Surface(path.relative_to(root).as_posix(), text))
    return tuple(surfaces)


def _command_names(line: str, registry: Registry, column: str) -> set[str]:
    names: set[str] = set()
    parents = {name.split()[0] for name in registry.commands if " " in name}
    for match in _CLI.finditer(line):
        name, child = match.groups()
        # An executable name in prose ("Every agents-remember memory line") is not an invocation.
        if (
            name in registry.commands
            or "`agents-remember " in line
            or line.lstrip().startswith("agents-remember ")
        ):
            names.add(f"{name} {child}" if name in parents and child else name)
    names.update(match.group(1) for match in _COMMAND_LABEL.finditer(line))
    if column in {"command", "subcommand"} and (match := _CODE.search(line.split("|")[1])):
        names.add(match.group(1))
    return names


def _invoked_tool_names(line: str, registry: Registry) -> set[str]:
    names = {next(name for name in match.groups() if name) for match in _TOOL_ACTION.finditer(line)}
    for match in _CODE.finditer(line):
        call = _INLINE_CALL.match(match.group(1))
        # Explicit local-function prose identifies a Python symbol, not an MCP invocation.
        if call and not re.search(r"\blocal\s*$", line[: match.start()]):
            names.add(call.group(1))
    if call := _STANDALONE_CALL.fullmatch(line):
        names.add(call.group(1))
    return names - registry.parameters - registry.operations


def _tool_names(line: str, registry: Registry, column: str) -> set[str]:
    names = set(re.findall(r"mcp__[\w-]+__([a-z][a-z0-9_]+)", line))
    names.update(_invoked_tool_names(line, registry))
    names.update(
        next(name for name in match.groups() if name) for match in _TOOL_LABEL.finditer(line)
    )
    for match in _CODE.finditer(line):
        name = match.group(1)
        if name in registry.tools:
            names.add(name)
    for match in _TOOL_CALL.finditer(line):
        name = match.group(1)
        if name in registry.tools or name.startswith("knowledge_") or "MCP tool" in line:
            names.add(name)
    if column == "tool" and (match := _CODE.search(line.split("|")[1])):
        names.add(match.group(1))
    return names


def _path_exists(root: Path, name: str) -> bool:
    # References may select an existing directory, wildcard population or file.
    return any(root.glob(name)) if any(char in name for char in "*?[") else (root / name).exists()


def _missing_paths(root: Path, line: str) -> set[str]:
    # A protocol method described as such is not a repository filesystem reference.
    methods = set(re.findall(r"`([^`]+)`\s+(?:extension|method|request)\b", line))
    paths = {match.group().rstrip(".") for match in _PATH.finditer(line)}
    return {name for name in paths - methods if not _path_exists(root, name)}


def _json_tokens(text: str) -> list[tuple[tuple[str | int, ...], int, int, bool]]:
    """Locate JSON scalar/key spans without changing physical line numbers."""
    decoder = json.JSONDecoder()
    tokens: list[tuple[tuple[str | int, ...], int, int, bool]] = []

    def whitespace(offset: int) -> int:
        while offset < len(text) and text[offset].isspace():
            offset += 1
        return offset

    def value(offset: int, path: tuple[str | int, ...]) -> int:
        offset = whitespace(offset)
        if text[offset] == "{":
            offset = whitespace(offset + 1)
            while text[offset] != "}":
                key, end = decoder.raw_decode(text, offset)
                tokens.append(((*path, key), offset, end, True))
                offset = whitespace(end)
                assert text[offset] == ":"
                offset = whitespace(value(offset + 1, (*path, key)))
                if text[offset] == ",":
                    offset = whitespace(offset + 1)
            return offset + 1
        if text[offset] == "[":
            offset = whitespace(offset + 1)
            index = 0
            while text[offset] != "]":
                offset = whitespace(value(offset, (*path, index)))
                index += 1
                if text[offset] == ",":
                    offset = whitespace(offset + 1)
            return offset + 1
        _, end = decoder.raw_decode(text, offset)
        tokens.append((path, offset, end, False))
        return end

    value(0, ())
    return tokens


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    """Historical token locations must describe the same unambiguous parsed owner."""
    result: dict = {}
    for key, value in pairs:
        assert key not in result, "duplicate JSON key in historical owner"
        result[key] = value
    return result


def _historical_path_text(root: Path, surface: Surface) -> str:
    """Mask existence-only historical fields authenticated by their existing owners."""
    prefix = "skills/l-01-agent-lifecycles/"
    owners = {
        prefix + "capsule-budgets-current.json": "current",
        prefix + "capsule-budgets-d694.json": "d694",
        prefix + "capsule-budgets.json": "frozen",
        prefix + "core/digest-map.json": "digest",
    }
    if surface.path not in owners:
        return surface.text
    current = load_current_budget_view(root / prefix / "capsule-budgets-current.json")
    target = root / surface.path
    raw = target.read_bytes()
    assert raw == surface.text.encode("utf-8"), "historical surface differs from its owner"
    document = json.loads(surface.text, object_pairs_hook=_unique_json_object)
    kind = owners[surface.path]
    expected = {
        "current": ("ar-role-capsule-budget-current/v1", None),
        "d694": ("ar-role-capsule-budget-current/v1", "predecessorD694Sha256"),
        "frozen": ("ar-role-capsule-budgets/v1", "historicalRegistrySha256"),
        "digest": ("ar-shared-rule-digest/v1", "currentDigestMapSha256"),
    }
    schema, pin = expected[kind]
    assert document["schema"] == schema, "historical owner schema differs"
    if pin is not None:
        assert sha256(raw).hexdigest() == current["decision"][pin], "historical owner changed"
    else:
        assert document == current, "historical current record changed"
    masked = list(surface.text)
    for path, start, end, is_key in _json_tokens(surface.text):
        historical = False
        if kind == "current":
            historical = (
                (
                    not is_key
                    and len(path) == 6
                    and path[:5]
                    == (
                        "decision",
                        "measuredCurrentRenewalP5_91",
                        "membership",
                        "sourceInventory",
                        "removedFromCurrent",
                    )
                    and isinstance(path[-1], int)
                )
                or (
                    not is_key
                    and len(path) == 6
                    and path[:4]
                    == (
                        "decision",
                        "measuredCurrentRenewalP5_91",
                        "membership",
                        "sourceInventory",
                    )
                    and path[4] in {"retained", "addedToCurrent"}
                    and isinstance(path[-1], int)
                )
                or (
                    not is_key
                    and len(path) == 6
                    and path[:5]
                    == (
                        "decision",
                        "measuredCurrentRenewalV14_557",
                        "membership",
                        "sources",
                        "removedFromCurrent",
                    )
                    and isinstance(path[-1], int)
                )
                or (
                    # P5-25: only a renewal sub-record marked historical with its tree is
                    # history; a sub-record without the mark is current and fully checked.
                    not is_key
                    and len(path) >= 3
                    and path[0] == "decision"
                    and isinstance(path[1], str)
                    and path[1].startswith("measuredCurrentRenewal")
                    and historical_tree(document["decision"][path[1]])
                )
            )
        elif kind == "d694":
            historical = is_key and len(path) == 2 and path[0] == "approved_source_sizes"
        elif kind == "frozen":
            historical = (
                bool(path)
                and isinstance(path[0], str)
                and path[0].startswith(("decision_", "decisions_"))
            )
        elif (
            kind == "digest"
            and len(path) >= 4
            and path[0] == "units"
            and path[2] == "retiredBinding"
        ):
            unit = document["units"][path[1]]
            historical = (
                unit["disposition"] == "retired (D699, D700)"
                and "retirementDecision" in unit
                and "evidence" not in unit
                and "digestPlace" not in unit
            )
        if historical:
            masked[start:end] = ["\n" if char == "\n" else " " for char in surface.text[start:end]]
    return "".join(masked)


_SKILL_RELATIVE = re.compile(
    r"^(?:reference|templates|criteria|roles|operations|core)/[\w./-]+\.(?:md|json)$"
)
_TREE = re.compile(r"^[0-9a-f]{40}$")
CURRENT_BUDGET_RECORD = "skills/l-01-agent-lifecycles/capsule-budgets-current.json"


def historical_tree(record: object) -> bool:
    """A renewal sub-record kept as history carries ``historicalTree``: its 40-hex tree."""
    if not isinstance(record, dict):
        return False
    tree = record.get("historicalTree")
    return isinstance(tree, str) and _TREE.fullmatch(tree) is not None


def current_record_absent_paths(root: Path, document: dict) -> set[str]:
    """P5-25: skill-relative paths the current budget record names that do not exist.

    Removal lists (keys starting with "removed") name absent paths by meaning, and a
    ``decision.measuredCurrentRenewal*`` sub-record marked with ``historicalTree`` is
    history; everything else in the record is a current claim and must exist.
    """
    skill = root / "skills/l-01-agent-lifecycles"
    absent: set[str] = set()

    def walk(value: object, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            renewal = len(path) == 2 and path[0] == "decision"
            if renewal and path[1].startswith("measuredCurrentRenewal") and historical_tree(value):
                return
            for key, item in value.items():
                if not key.startswith("removed"):
                    walk(key, (*path, key))
                    walk(item, (*path, key))
        elif isinstance(value, list):
            for item in value:
                walk(item, path)
        elif isinstance(value, str) and _SKILL_RELATIVE.match(value):
            if not (skill / value).exists():
                absent.add(value)

    walk(document, ())
    return absent


def _surface_findings(root: Path, surface: Surface, registry: Registry) -> set[Finding]:
    findings: set[Finding] = set()
    if surface.path == CURRENT_BUDGET_RECORD:
        findings.update(
            Finding(surface.path, 0, "missing-path", name)
            for name in current_record_absent_paths(root, json.loads(surface.text))
        )
    path_lines = _historical_path_text(root, surface).splitlines()
    task_doc_block = False
    column = ""
    for number, line in enumerate(surface.text.splitlines(), 1):
        if line.lstrip().startswith("|"):
            first = line.split("|")[1].strip().casefold()
            if first in {"tool", "command", "subcommand"}:
                column = first
        else:
            column = ""
        task_doc_block = (task_doc_block or "task_doc" in line) and bool(line.strip())
        operations = set(_DOT_OPERATION.findall(line))
        if task_doc_block:
            operations.update(_OPERATION.findall(line))
        checks = (
            (
                "retired-term",
                {term for term in RETIRED_TERMS if term.casefold() in line.casefold()}
                if CONVERSION_MARKER not in line
                else set(),
            ),
            ("unregistered-command", _command_names(line, registry, column) - registry.commands),
            ("unregistered-tool", _tool_names(line, registry, column) - registry.tools),
            ("missing-task-doc-operation", operations - registry.operations),
            (
                "missing-path",
                _missing_paths(root, path_lines[number - 1]),
            ),
        )
        findings.update(
            Finding(surface.path, number, kind, name) for kind, names in checks for name in names
        )
    return findings


def check_consistency(
    root: Path, surfaces: tuple[Surface, ...], registry: Registry
) -> list[Finding]:
    """Return every invalid reference and every missing bidirectional knowledge mention."""
    findings = set().union(*(_surface_findings(root, surface, registry) for surface in surfaces))
    for prefix in ("skills/", "docs/reference/"):
        text = "\n".join(surface.text for surface in surfaces if surface.path.startswith(prefix))
        for name in registry.knowledge_names - UNDOCUMENTED_COMMANDS:
            if not re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text):
                findings.add(Finding(prefix, 0, "undocumented-knowledge-name", name))
    return sorted(findings)
