"""Stable architecture contracts retained after the model-split proof expired."""

from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

from agents_remember.models import conversations
from pydantic import BaseModel, RootModel

REPO_ROOT = Path(__file__).resolve().parents[2]
CONVERSATION_MODULE_ROOT = REPO_ROOT / "mcp/src/agents_remember/models/conversations"
REMOVED_MODEL_MODULES = (
    "agents_remember.serving.conversation.models",
    "agents_remember.serving.conversation._models_wire",
    "agents_remember.serving.conversation._models_blocks",
    "agents_remember.serving.conversation._models_operations",
    "agents_remember.serving.conversation._models_status",
    "agents_remember.serving.conversation._models_telemetry",
)
CONTROL_FACADE = "agents_remember.serving.harness_control_models"


def test_removed_conversation_model_modules_have_no_compatibility_shims() -> None:
    for module in REMOVED_MODEL_MODULES:
        assert importlib.util.find_spec(module) is None, f"compatibility shim survived: {module}"


def test_conversation_wire_types_are_not_imported_through_the_control_facade() -> None:
    shared_wire_names = set(conversations.__all__)
    offenders: list[str] = []
    for root in (REPO_ROOT / "mcp/src", REPO_ROOT / "mcp/tests"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != CONTROL_FACADE:
                    continue
                for alias in node.names:
                    if alias.name in shared_wire_names:
                        relative = path.relative_to(REPO_ROOT)
                        offenders.append(f"{relative}:{node.lineno} {alias.name}")
    assert offenders == []


def test_conversation_models_have_resolved_forward_references() -> None:
    for path in sorted(CONVERSATION_MODULE_ROOT.glob("*.py")):
        module = importlib.import_module(f"agents_remember.models.conversations.{path.stem}")
        for value in vars(module).values():
            if not isinstance(value, type) or not issubclass(value, (BaseModel, RootModel)):
                continue
            if not value.__module__.startswith("agents_remember.models.conversations"):
                continue
            value.model_rebuild()
            assert value.__pydantic_complete__, f"{value.__module__}.{value.__name__}"
