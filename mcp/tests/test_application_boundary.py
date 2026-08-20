"""The MCP adapter cannot bypass the application layer (L6-R7)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest
from agents_remember.code_quality.application_boundary import (
    BoundaryContractError,
    application_boundary_violations,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "mcp" / "src" / "agents_remember"
LAYERS = PROJECT_ROOT / "layers.toml"
pytestmark = pytest.mark.fitness


class ApplicationBoundaryRuleTests(unittest.TestCase):
    def _write_layout(
        self,
        package_root: Path,
        overrides: dict[str, str] | None = None,
        *,
        omitted: frozenset[str] = frozenset(),
    ) -> None:
        sources = {
            "mcp/tools/sample.py": "",
            "mcp/registration/sample.py": "",
            "mcp/server.py": "",
            # L9 owns the runtime config move. It is not an MCP behavior adapter and therefore
            # remains legal on the reverse side of this bounded R7 check.
            "serving/app.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig\n"
            ),
        }
        sources.update(overrides or {})
        for relative, source in sources.items():
            if relative in omitted:
                continue
            path = package_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")

    def _violations(self, source: str, relative: str = "mcp/tools/sample.py") -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            package_root = Path(tmp) / "agents_remember"
            self._write_layout(package_root, {relative: source})
            return [str(item) for item in application_boundary_violations(package_root, LAYERS)]

    def test_application_wire_kernel_and_same_adapter_imports_are_allowed(self) -> None:
        source = (
            "from agents_remember.application.task_docs.task_doc_tools import task_doc_tool\n"
            "from agents_remember.errors import AuthorityError\n"
            "from agents_remember.kernel.atomic_write import atomic_write_text\n"
            "from agents_remember.models.base import ResponseEnvelope\n"
            "from agents_remember.mcp.tool_reports import write_tool_report\n"
            "from .base import _tool_payload\n"
            "from ..config import McpRuntimeConfig\n"
        )
        self.assertEqual(self._violations(source), [])

    def test_absolute_domain_imports_are_reported_with_exact_remediation(self) -> None:
        self.assertEqual(
            self._violations(
                "from agents_remember.controlplane.store import GateStore\n"
                "import agents_remember.serving.terminal\n"
            ),
            [
                "mcp/tools/sample.py:1 imports domain package 'controlplane': "
                "from agents_remember.controlplane.store import GateStore; route the use case "
                "through agents_remember.application",
                "mcp/tools/sample.py:2 imports domain package 'serving': "
                "import agents_remember.serving.terminal; route the use case through "
                "agents_remember.application",
            ],
        )

    def test_relative_domain_import_cannot_evade_the_boundary(self) -> None:
        self.assertEqual(
            self._violations("from ...observer import ambient\n"),
            [
                "mcp/tools/sample.py:1 imports domain package 'observer': "
                "from ...observer import ambient; route the use case through "
                "agents_remember.application"
            ],
        )

    def test_type_checking_imports_are_still_dependencies(self) -> None:
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from agents_remember.worktrees.worktree_contract import WorktreeContract\n"
        )
        violations = self._violations(source)
        self.assertEqual(len(violations), 1)
        self.assertIn("domain package 'worktrees'", violations[0])

    def test_registration_server_and_all_serving_reverse_edges_are_reported_stably(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package_root = Path(tmp) / "agents_remember"
            self._write_layout(
                package_root,
                {
                    "mcp/tools/sample.py": (
                        "from agents_remember.controlplane.store import GateStore\n"
                    ),
                    "mcp/registration/sample.py": (
                        "from agents_remember.benchmarks.runner import run\n"
                    ),
                    "mcp/server.py": "from agents_remember.observer import install_ambient\n",
                    "serving/app.py": (
                        "from agents_remember.application.gate_tools import record_gate_decision\n"
                        "from agents_remember.mcp.tools.gates import gate_decide_payload\n"
                    ),
                    "serving/routes/nested.py": (
                        "from agents_remember.application.operator_inbox_tools import post_operator_inbox\n"
                        "from agents_remember.mcp.registration.gates import register_gate_tools\n"
                        "from agents_remember.mcp.server import create_server\n"
                    ),
                },
            )

            violations = [
                str(item) for item in application_boundary_violations(package_root, LAYERS)
            ]

        self.assertEqual(
            violations,
            [
                "mcp/registration/sample.py:1 imports domain package 'benchmarks': "
                "from agents_remember.benchmarks.runner import run; route the use case through "
                "agents_remember.application",
                "mcp/server.py:1 imports domain package 'observer': "
                "from agents_remember.observer import install_ambient; route the use case through "
                "agents_remember.application",
                "mcp/tools/sample.py:1 imports domain package 'controlplane': "
                "from agents_remember.controlplane.store import GateStore; route the use case "
                "through agents_remember.application",
                "serving/app.py:1 imports domain package 'application': "
                "from agents_remember.application.gate_tools import record_gate_decision; move "
                "shared vocabulary to agents_remember.models or call a serving/lower-ranked "
                "domain owner; serving must not import application or MCP adapters",
                "serving/app.py:2 imports domain package 'mcp': "
                "from agents_remember.mcp.tools.gates import gate_decide_payload; move shared "
                "vocabulary to agents_remember.models or call a serving/lower-ranked domain "
                "owner; serving must not import application or MCP adapters",
                "serving/routes/nested.py:1 imports domain package 'application': "
                "from agents_remember.application.operator_inbox_tools import "
                "post_operator_inbox; move shared vocabulary to agents_remember.models or call "
                "a serving/lower-ranked domain owner; serving must not import application or MCP "
                "adapters",
                "serving/routes/nested.py:2 imports domain package 'mcp': "
                "from agents_remember.mcp.registration.gates import register_gate_tools; move "
                "shared vocabulary to agents_remember.models or call a serving/lower-ranked "
                "domain owner; serving must not import application or MCP adapters",
                "serving/routes/nested.py:3 imports domain package 'mcp': "
                "from agents_remember.mcp.server import create_server; move shared vocabulary to "
                "agents_remember.models or call a serving/lower-ranked domain owner; serving "
                "must not import application or MCP adapters",
            ],
        )

    def test_missing_and_empty_owned_surfaces_refuse_instead_of_passing_vacuously(self) -> None:
        cases = (
            (frozenset({"mcp/tools/sample.py"}), "missing MCP transport package"),
            (frozenset({"mcp/registration/sample.py"}), "missing MCP transport package"),
            (frozenset({"mcp/server.py"}), "missing MCP server startup module"),
            (frozenset({"serving/app.py"}), "missing serving package"),
        )
        for omitted, message in cases:
            with self.subTest(omitted=sorted(omitted)), tempfile.TemporaryDirectory() as tmp:
                package_root = Path(tmp) / "agents_remember"
                self._write_layout(package_root, omitted=omitted)
                with self.assertRaisesRegex(BoundaryContractError, message):
                    application_boundary_violations(package_root, LAYERS)

        for relative in ("mcp/tools/sample.py", "mcp/registration/sample.py"):
            with self.subTest(empty=relative), tempfile.TemporaryDirectory() as tmp:
                package_root = Path(tmp) / "agents_remember"
                self._write_layout(package_root, omitted=frozenset({relative}))
                (package_root / relative).parent.mkdir(parents=True, exist_ok=True)
                with self.assertRaisesRegex(
                    BoundaryContractError, "MCP transport package contains no Python modules"
                ):
                    application_boundary_violations(package_root, LAYERS)

        with tempfile.TemporaryDirectory() as tmp:
            package_root = Path(tmp) / "agents_remember"
            self._write_layout(package_root, omitted=frozenset({"serving/app.py"}))
            (package_root / "serving").mkdir(parents=True, exist_ok=True)
            with self.assertRaisesRegex(
                BoundaryContractError, "serving package contains no Python modules"
            ):
                application_boundary_violations(package_root, LAYERS)


class RepositoryApplicationBoundaryTests(unittest.TestCase):
    def test_the_complete_mcp_transport_reaches_domain_only_through_application(self) -> None:
        violations = application_boundary_violations(PACKAGE_ROOT, LAYERS)
        self.assertEqual(
            violations,
            [],
            "MCP application-boundary violations:\n" + "\n".join(str(item) for item in violations),
        )

    def test_fresh_process_imports_serving_and_application_in_both_orders_without_a_cycle(
        self,
    ) -> None:
        serving_first = (
            "import sys\n"
            "import agents_remember.serving.app\n"
            "forbidden = sorted(name for name in sys.modules if "
            "name.startswith('agents_remember.application') or "
            "name.startswith(('agents_remember.mcp.tools', "
            "'agents_remember.mcp.registration', 'agents_remember.mcp.server')))\n"
            "assert not forbidden, forbidden\n"
            "import agents_remember.application.gate_tools\n"
            "import agents_remember.application.operator_inbox_tools\n"
            "import agents_remember.application.tool_response\n"
        )
        application_first = (
            "import agents_remember.application.gate_tools\n"
            "import agents_remember.application.operator_inbox_tools\n"
            "import agents_remember.application.tool_response\n"
            "import agents_remember.serving.app\n"
        )
        for source in (serving_first, application_first):
            with self.subTest(order=source.splitlines()[0:2]):
                completed = subprocess.run(
                    [sys.executable, "-c", source],
                    cwd=PROJECT_ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
                )
