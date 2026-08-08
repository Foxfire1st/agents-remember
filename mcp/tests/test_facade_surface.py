"""Mechanical facade surface pin (260731-EFA-L7 F1 / reviewer CS-6).

Every split facade must keep the base module's top-level names importable: R12
("public surfaces unchanged") plus the private names the repository actually
imports or patches. The base is the L16-synced commit a3e43cb; the pin reads the
base file from git (the checkout is a git worktree) and compares against the
imported facade, so a missing name is a blocking finding even when no in-repo
consumer references it yet.
"""

from __future__ import annotations

import ast
import importlib
import re
import subprocess
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

BASE_COMMIT = "a3e43cb0877c18b9d2b0e6ada4eb5719a01f251f"

FACADES: list[tuple[str, str]] = [
    ("agents_remember.observer.snapshots", "mcp/src/agents_remember/observer/snapshots.py"),
    ("agents_remember.observer.reducer", "mcp/src/agents_remember/observer/reducer.py"),
    (
        "agents_remember.kernel.agentic_settings",
        "mcp/src/agents_remember/kernel/agentic_settings.py",
    ),
    (
        "agents_remember.serving.conversation.projectors.codex",
        "mcp/src/agents_remember/serving/conversation/projectors/codex.py",
    ),
    (
        "agents_remember.serving.harness_control_client",
        "mcp/src/agents_remember/serving/harness_control_client.py",
    ),
    ("agents_remember.serving.app", "mcp/src/agents_remember/serving/app.py"),
    (
        "agents_remember.serving.conversation.models",
        "mcp/src/agents_remember/serving/conversation/models.py",
    ),
    ("agents_remember.serving.agent_notifier", "mcp/src/agents_remember/serving/agent_notifier.py"),
]

# 260713-TES-L1: renamed module paths at the pinned base commit (the base has the old name).
BASE_PATH_RENAMES = {
    "mcp/src/agents_remember/serving/agent_notifier.py": (
        "mcp/src/agents_remember/serving/supervisor.py"
    ),
}

# 260713-TES-L1 (supervisor -> agent-notifier): base names deliberately renamed. The pin
# compares against the L16 base commit, so an expected-old-name hit is satisfied by its
# rename target instead of failing; an entry here that stops matching (or a rename target
# that disappears) fails the pin just like any other lost name.
RENAMED_FACADE_NAMES: dict[str, dict[str, str]] = {
    "agents_remember.serving.agent_notifier": {
        "SupervisorContext": "AgentNotifierContext",
        "SupervisorFinding": "AgentNotifierFinding",
        "SupervisorActionResult": "AgentNotifierActionResult",
        "SupervisorSweepResult": "AgentNotifierSweepResult",
        "run_supervisor_sweep": "run_agent_notifier_sweep",
    },
    "agents_remember.serving.app": {
        "_supervisor_context": "_agent_notifier_context",
        "_supervisor_heartbeat_payload": "_agent_notifier_heartbeat_payload",
        "_supervisor_loop": "_agent_notifier_loop",
    },
    "agents_remember.kernel.agentic_settings": {
        "SupervisorSettings": "AgentNotifierSettings",
        "KNOWN_SUPERVISOR_FIELDS": "KNOWN_AGENT_NOTIFIER_FIELDS",
        "DEFAULT_SUPERVISOR_ESCALATION_BUDGET": "DEFAULT_AGENT_NOTIFIER_ESCALATION_BUDGET",
        "DEFAULT_SUPERVISOR_INTERVAL_SECONDS": "DEFAULT_AGENT_NOTIFIER_INTERVAL_SECONDS",
        "DEFAULT_SUPERVISOR_REDELIVER_BUDGET": "DEFAULT_AGENT_NOTIFIER_REDELIVER_BUDGET",
        "DEFAULT_SUPERVISOR_STALE_CUTOFF_SECONDS": "DEFAULT_AGENT_NOTIFIER_STALE_CUTOFF_SECONDS",
        "_parse_supervisor": "_parse_agent_notifier",
        "_require_supervisor_floor_seconds": "_require_agent_notifier_floor_seconds",
    },
}


def base_top_level_names(path: str, source: str | None = None) -> list[str]:
    """Every top-level class, function, constant, and type alias at the base commit."""
    if source is None:
        completed = subprocess.run(
            ["git", "show", f"{BASE_COMMIT}:{path}"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        source = completed.stdout
    tree = ast.parse(source)
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
    return names


def consumer_imported_names(facade_module: str) -> set[str]:
    """Names any repo file imports from the facade (public or private patch targets)."""
    imported: set[str] = set()
    pattern = re.compile(rf"from\s+{re.escape(facade_module)}\s+import\s+(?:\(([^)]*)\)|([^\n]*))")
    for root in (REPOSITORY_ROOT / "mcp" / "src", REPOSITORY_ROOT / "mcp" / "tests"):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                body = match.group(1) or match.group(2)
                for raw_entry in body.split(","):
                    entry = raw_entry.strip()
                    if not entry:
                        continue
                    name = entry.split(" as ")[0].strip()
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                        imported.add(name)
    return imported


class FacadeSurfaceTests(unittest.TestCase):
    def test_base_name_extraction_ignores_non_name_assignment_targets(self) -> None:
        path = FACADES[0][1]
        names = base_top_level_names(
            path,
            "class K:\n    ...\ndef f() -> None:\n    ...\nx = 1\ny: int = 2\na, b = (1, 2)\n",
        )
        self.assertEqual(names, ["K", "f", "x", "y"])

    def test_every_base_top_level_name_is_importable_from_its_facade(self) -> None:
        for module, path in FACADES:
            facade = importlib.import_module(module)
            renamed = RENAMED_FACADE_NAMES.get(module, {})
            missing = []
            for name in base_top_level_names(BASE_PATH_RENAMES.get(path, path)):
                if hasattr(facade, name):
                    continue
                target = renamed.get(name)
                if target is not None and hasattr(facade, target):
                    continue
                missing.append(name)
            with self.subTest(module=module):
                self.assertEqual(missing, [], f"facade lost base top-level names: {missing}")

    def test_every_consumer_import_name_is_available_on_its_facade(self) -> None:
        for module, _path in FACADES:
            facade = importlib.import_module(module)
            missing = sorted(
                name for name in consumer_imported_names(module) if not hasattr(facade, name)
            )
            with self.subTest(module=module):
                self.assertEqual(
                    missing, [], f"consumer-imported names missing from facade: {missing}"
                )
