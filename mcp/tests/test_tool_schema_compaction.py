"""Lossless JSON-Schema compaction unit and server-parity tests."""

from __future__ import annotations

import asyncio
import copy
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.mcp import server as server_module
from agents_remember.mcp.registration.explainer_surface import EXPLAINER_BINDING_ENV
from agents_remember.mcp.server import create_server
from agents_remember.models.task_document_ref import TaskScopedReaderContext
from agents_remember.models.tools.tool_schema_compaction import compact_tool_schema

REPO = "agents-remember"
TASK = "261124_cap"
LEAF = "261124-CAP-L4"


@pytest.fixture(autouse=True)
def _no_inherited_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("AR_"):
            monkeypatch.delenv(key, raising=False)


def _world(root: Path) -> tuple[McpRuntimeConfig, Path]:
    (root / "tasks" / REPO / TASK).mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    workspace = root / "explainer" / REPO / TASK / LEAF
    workspace.mkdir(parents=True)
    config = McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root / "workspace",
        transcript_root=root / "transcripts",
        repositories={
            REPO: RepositoryScope(repo_id=REPO, path=root / "repo", memory_root=root / "memory")
        },
    )
    return config, workspace


def _explainer_binding(workspace: Path) -> str:
    return json.dumps(
        {
            "capability": "explainer",
            "agent": "ca6f591f-1b27-4554-a530-0cca576b7529",
            "request": "req-1",
            "repository": REPO,
            "task": TASK,
            "leaf": LEAF,
            "workspace": str(workspace),
            "generation": 1,
            "seat": "ca6f591f-1b27-4554-a530-0cca576b7529",
        }
    )


def _map_children(
    node: Mapping[str, Any], path: str, *, is_root: bool
) -> list[tuple[str, Mapping[str, Any], bool]]:
    children: list[tuple[str, Mapping[str, Any], bool]] = []
    for key in ("properties", "patternProperties", "dependentSchemas", "$defs"):
        mapping = node.get(key)
        if isinstance(mapping, Mapping):
            children.extend(
                (f"{path}.{key}.{name}", sub, is_root and key == "$defs")
                for name, sub in mapping.items()
                if isinstance(sub, Mapping)
            )
    return children


def _other_children(
    node: Mapping[str, Any], path: str
) -> list[tuple[str, Mapping[str, Any], bool]]:
    children: list[tuple[str, Mapping[str, Any], bool]] = []
    for key in ("items", "not", "if", "then", "else", "additionalProperties"):
        sub = node.get(key)
        if isinstance(sub, Mapping):
            children.append((f"{path}.{key}", sub, False))
    for key in ("items", "anyOf", "oneOf", "allOf", "prefixItems"):
        seq = node.get(key)
        if isinstance(seq, list):
            children.extend(
                (f"{path}.{key}[{idx}]", item, False)
                for idx, item in enumerate(seq)
                if isinstance(item, Mapping)
            )
    return children


def _schema_compaction_violations(
    node: Mapping[str, Any], *, is_root: bool = True, path: str = "$"
) -> list[str]:
    """Return any unstripped schema-level title, default:null, or top-level $defs description."""

    violations: list[str] = []
    if "title" in node:
        violations.append(f"{path}.title")
    if "default" in node and node["default"] is None:
        violations.append(f"{path}.default=null")
    for sub_path, sub, check_desc in (
        *_map_children(node, path, is_root=is_root),
        *_other_children(node, path),
    ):
        if check_desc and "description" in sub:
            violations.append(f"{sub_path}.description")
        violations.extend(_schema_compaction_violations(sub, is_root=False, path=sub_path))
    return violations


_SIGNATURE_MAP_KEYS = ("properties", "patternProperties", "dependentSchemas", "$defs")
_SIGNATURE_SINGLE_KEYS = ("items", "additionalProperties", "not", "if", "then", "else")
_SIGNATURE_LIST_KEYS = ("items", "anyOf", "oneOf", "allOf", "prefixItems")


def _signature_map_entry(sub: Any, *, strip_description: bool) -> Any:
    if not isinstance(sub, Mapping):
        return copy.deepcopy(sub)
    compacted = _contract_signature(sub, is_root=False)
    if not strip_description:
        return compacted
    return {k: v for k, v in compacted.items() if k != "description"}


def _signature_value(key: str, value: Any, *, is_root: bool) -> Any:
    if isinstance(value, Mapping):
        if key in _SIGNATURE_MAP_KEYS:
            strip_desc = is_root and key == "$defs"
            return {
                name: _signature_map_entry(sub, strip_description=strip_desc)
                for name, sub in value.items()
            }
        if key in _SIGNATURE_SINGLE_KEYS:
            return _contract_signature(value, is_root=False)
    elif isinstance(value, list) and key in _SIGNATURE_LIST_KEYS:
        return [
            _contract_signature(item, is_root=False)
            if isinstance(item, Mapping)
            else copy.deepcopy(item)
            for item in value
        ]
    return copy.deepcopy(value)


def _contract_signature(node: Any, *, is_root: bool = True) -> Any:
    """Strip only lossless schema metadata; recurse only through JSON-Schema subschema containers."""

    if not isinstance(node, Mapping):
        return copy.deepcopy(node)
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" or (key == "default" and value is None):
            continue
        out[key] = _signature_value(key, value, is_root=is_root)
    return out


def _sample_compaction_schema() -> dict[str, Any]:
    return {
        "title": "RootArgs",
        "default": None,
        "type": "object",
        "required": ["title", "default"],
        "additionalProperties": False,
        "properties": {
            "title": {
                "title": "Title",
                "type": "string",
                "description": "User-supplied title field.",
                "default": None,
                "pattern": "^[a-z]+$",
            },
            "description": {
                "title": "Description",
                "type": "string",
                "description": "User-supplied description field.",
                "const": "fixed",
            },
            "default": {"title": "Default", "type": "boolean", "default": False},
            "defaults_kept": {
                "title": "DefaultsKept",
                "type": "object",
                "properties": {
                    "flag": {"title": "Flag", "type": "boolean", "default": True},
                    "zero": {"title": "Zero", "type": "integer", "default": 0},
                    "empty": {"title": "Empty", "type": "string", "default": ""},
                    "items": {"title": "Items", "type": "array", "default": []},
                    "limit": {"title": "Limit", "type": "integer", "default": 50},
                    "schema": {"title": "Schema", "type": "string", "default": "task-intent/v1"},
                },
                "additionalProperties": {"title": "ExtraVal", "type": "string", "default": None},
            },
            "tuple_items": {
                "title": "TupleItems",
                "type": "array",
                "items": [{"title": "First", "type": "string", "default": None}, "malformed-entry"],
                "prefixItems": [{"title": "Prefix", "type": "integer", "default": None}],
            },
            "combinators": {
                "title": "Combinators",
                "anyOf": [
                    {"$ref": "#/$defs/title"},
                    {"type": "null", "title": "NullBranch"},
                    "malformed-branch",
                ],
                "oneOf": [{"title": "One", "enum": ["a", "b"]}],
                "allOf": [{"title": "All", "type": "object"}],
                "not": {"title": "Not", "type": "integer"},
                "if": {"title": "If", "type": "object"},
                "then": {"title": "Then", "required": ["title"]},
                "else": {"title": "Else", "type": "null"},
            },
        },
        "patternProperties": {"title": {"title": "PatTitle", "type": "string", "default": None}},
        "dependentSchemas": {
            "title": {"title": "DepTitle", "required": ["description"], "default": None}
        },
        "$defs": {
            "title": {
                "title": "TitleModel",
                "description": "Top-level model docstring to strip.",
                "type": "object",
                "properties": {
                    "description": {
                        "title": "NestedDesc",
                        "type": "string",
                        "description": "Nested property description survives.",
                        "default": None,
                    }
                },
            }
        },
    }


def test_compact_tool_schema_is_lossless_and_immutable() -> None:
    """Compaction strips only lossless metadata, preserves literal property names, and never aliases."""

    raw = _sample_compaction_schema()
    snapshot = copy.deepcopy(raw)
    compacted = compact_tool_schema(raw)

    assert raw == snapshot
    assert _schema_compaction_violations(compacted) == []
    assert compact_tool_schema(compacted) == compacted
    assert set(compacted["properties"]) >= {"title", "description", "default"}
    assert compacted["properties"]["title"]["description"] == "User-supplied title field."
    assert compacted["properties"]["title"]["pattern"] == "^[a-z]+$"
    assert compacted["properties"]["description"]["const"] == "fixed"
    assert compacted["properties"]["default"]["default"] is False
    kept = compacted["properties"]["defaults_kept"]["properties"]
    assert [kept[k]["default"] for k in ("flag", "zero", "empty", "items", "limit", "schema")] == [
        True,
        0,
        "",
        [],
        50,
        "task-intent/v1",
    ]
    assert compacted["properties"]["defaults_kept"]["additionalProperties"] == {"type": "string"}
    assert compacted["additionalProperties"] is False
    assert compacted["properties"]["tuple_items"]["items"] == [
        {"type": "string"},
        "malformed-entry",
    ]
    assert "title" in compacted["patternProperties"]
    assert "title" in compacted["dependentSchemas"]
    assert "title" in compacted["$defs"]
    assert "description" not in compacted["$defs"]["title"]
    assert (
        compacted["$defs"]["title"]["properties"]["description"]["description"]
        == "Nested property description survives."
    )

    compacted["required"].append("mutated")
    compacted["properties"]["combinators"]["oneOf"][0]["enum"].append("c")
    compacted["properties"]["defaults_kept"]["properties"]["items"]["default"].append("x")
    assert raw == snapshot


@pytest.mark.parametrize("with_explainer", [False, True])
def test_create_server_compacts_every_advertised_schema_with_contract_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_explainer: bool
) -> None:
    """create_server compacts unbound and explainer surfaces without altering any wire contract."""

    config, workspace = _world(tmp_path)
    if with_explainer:
        monkeypatch.setenv(EXPLAINER_BINDING_ENV, _explainer_binding(workspace))
    compacted_tools = asyncio.run(create_server(config).list_tools())
    monkeypatch.setattr(server_module, "_compact_server_tool_schemas", lambda _server: None)
    raw_tools = asyncio.run(create_server(config).list_tools())

    assert [tool.name for tool in compacted_tools] == [tool.name for tool in raw_tools]
    for compacted, raw in zip(compacted_tools, raw_tools, strict=True):
        assert compacted.annotations == raw.annotations
        assert _schema_compaction_violations(compacted.input_schema) == []
        assert compacted.input_schema == _contract_signature(raw.input_schema)


def test_literal_const_enum_and_default_payloads_survive_compaction_and_signature() -> None:
    """Both production compaction and the parity oracle preserve literal JSON payloads verbatim."""

    raw = {
        "title": "LiteralPayloadArgs",
        "type": "object",
        "properties": {
            "const_payload": {
                "title": "ConstPayload",
                "const": {"title": "fixed"},
            },
            "enum_payload": {
                "title": "EnumPayload",
                "enum": [{"title": "a", "default": None}],
            },
            "default_payload": {
                "title": "DefaultPayload",
                "type": "object",
                "default": {"title": "x", "default": None},
            },
        },
    }
    expected = {
        "type": "object",
        "properties": {
            "const_payload": {"const": {"title": "fixed"}},
            "enum_payload": {"enum": [{"title": "a", "default": None}]},
            "default_payload": {
                "type": "object",
                "default": {"title": "x", "default": None},
            },
        },
    }

    assert compact_tool_schema(raw) == expected
    assert _contract_signature(raw) == expected


def test_worktree_sync_description_keeps_raw_merge_warning(tmp_path: Path) -> None:
    """worktree_sync warns that memory merges only through worktree_sync and raw git merge is unsupported."""

    config, _workspace = _world(tmp_path)
    tools = {tool.name: tool for tool in asyncio.run(create_server(config).list_tools())}
    sync_description = tools["worktree_sync"].description or ""

    assert len(sync_description) <= 500
    lowered = sync_description.lower()
    assert "merges memory only here" in lowered
    assert "raw git merge" in lowered
    assert "unsupported hand edit" in lowered


def test_read_ar_files_description_names_task_scoped_reader_inputs(tmp_path: Path) -> None:
    """read_ar_files names TaskScopedReaderContext input fields rather than response fields."""

    config, _workspace = _world(tmp_path)
    tools = {tool.name: tool for tool in asyncio.run(create_server(config).list_tools())}
    read_description = tools["read_ar_files"].description or ""

    assert len(read_description) <= 500
    assert tuple(TaskScopedReaderContext.model_fields) == ("task_document_ref", "contract_path")
    assert "(task_document_ref, contract_path)" in read_description
    assert all(
        field not in read_description for field in ("worktreeScope", "sourceRoot", "memoryRoot")
    )
