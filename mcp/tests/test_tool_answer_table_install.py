"""The runtime-install final row and its operation-produced cases (MIK-R112 F11).

Split text-identical out of test_tool_answer_table.py under MIK-R196 rule 10."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.application.runtime.install import RuntimeInstallRequest, run_runtime_install
from agents_remember.application.tool_answer_log import TOOL_LOG_BASENAME_PREFIX, tool_call_context
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.install import provider_watchers as install_provider_watchers
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.mcp.tools.core import compact_runtime_install_payload
from agents_remember.models.tokens import count_response_tokens
from agents_remember.models.tools.tool_answer import (
    INTERIM_TOOL_LEAF,
    L112_FINAL_BOUNDS,
    RUNTIME_INSTALL_ANSWER_FIELDS,
    TOOL_ANSWER_EXCEPTION_MAX_TOKENS,
    TOOL_ANSWER_OVERFLOW_MAX_TOKENS,
    bound_for,
)
from tool_answer_table_support import (
    _CASE_ROOT,
    _INSTALL_FIELDS,
    _INSTALL_LARGEST_CASE,
    _INSTALL_ORDINARY_CASE,
    L112_FINAL_ROWS,
    _stub_config,
)

_PACKAGE_DATA = Path(__file__).resolve().parents[1] / "src" / "agents_remember" / "package_data"


def _produced_install_compact(*, stop_ok: bool) -> tuple[dict[str, Any], str]:
    """Run the real install and project it: the operation-produced answer.

    A disposable dry run from the packaged tree with the watcher stop mocked
    at the lifecycle boundary (ok or failed with a fixed process); returns
    the projected compact and the coordination root for normalization. The
    run with the failed stop finds a store of a removed part and a gate log
    in its root, so that it is the largest answer the operation gives.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        coord = (root / "coord").resolve()
        coord.mkdir(parents=True, exist_ok=True)
        if not stop_ok:
            workspace = coord / "logs" / "observer" / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "operator-inbox.jsonl").write_text("{}\n", encoding="utf-8")
            (workspace / "gates.jsonl").write_text("{}\n", encoding="utf-8")
        config = McpRuntimeConfig(
            config_path=coord / "mcp.settings.json",
            coordination_root=coord,
            workspace_root=(root / "ws").resolve(),
            transcript_root=coord / "logs",
            repositories={},
        )
        request = RuntimeInstallRequest(dry_run=True, source_root=_PACKAGE_DATA.resolve())
        if stop_ok:
            stop_result: dict[str, Any] = {
                "provider": "watchers",
                "action": "stop",
                "partial": False,
                "recoveryActions": [],
                "ok": True,
            }
        else:
            stop_result = {
                "provider": "watchers",
                "action": "stop",
                "partial": False,
                "recoveryActions": [],
                "ok": False,
                "process": {"pid": 1234, "command": "cgc watch"},
            }

        def stop_watcher(args: Any, action: str) -> dict[str, Any]:
            return {"provider": "watchers", **stop_result, "action": action}

        with mock.patch.object(
            install_provider_watchers.lifecycle,
            "watchers_run",
            side_effect=stop_watcher,
        ):
            full = run_runtime_install(config, request)
        compact = compact_runtime_install_payload(full, f"{_CASE_ROOT}/report.json")
        raw = json.dumps(compact).replace(coord.as_posix(), _CASE_ROOT)
        normalized = json.loads(raw)
        return normalized, coord.as_posix()


class L112FinalRowProductionTests(unittest.TestCase):
    """MIK-R112 F11: the two final rows govern their production answers.

    The row bounds come from the production lookup (bound_for), the row
    fields match the production projection, the cases are operation-produced
    and pinned byte-for-byte, over-bound answers overflow through the
    unchanged L108 guard, and L109-L111 ownership is untouched.
    """

    def test_final_bounds_live_in_the_production_lookup(self) -> None:
        self.assertEqual(L112_FINAL_BOUNDS, {"runtime_install": 600})
        for tool, row in L112_FINAL_ROWS.items():
            if tool in L112_FINAL_BOUNDS:
                self.assertEqual(row.bound, bound_for(tool))

    def test_row_fields_match_the_production_projection(self) -> None:
        self.assertEqual(set(_INSTALL_FIELDS), set(RUNTIME_INSTALL_ANSWER_FIELDS))

    def test_l109_l111_ownership_is_unchanged(self) -> None:
        self.assertNotIn("L112", set(INTERIM_TOOL_LEAF.values()))
        self.assertNotIn("runtime_install", INTERIM_TOOL_LEAF)

    # The answer no longer reads the user settings file: MIK-R196 rule 3 with the architect's
    # ruling puts the host block in the MCP authority settings file, so these cases are
    # deterministic in any shell and need no XDG precondition.
    def test_ordinary_install_case_is_operation_produced(self) -> None:
        produced, _ = _produced_install_compact(stop_ok=True)
        self.assertEqual(produced, _INSTALL_ORDINARY_CASE)
        self.assertLessEqual(
            count_response_tokens(dict(_INSTALL_ORDINARY_CASE)),
            bound_for("runtime_install"),
        )

    def test_largest_install_case_is_operation_produced(self) -> None:
        produced, _ = _produced_install_compact(stop_ok=False)
        self.assertEqual(produced, _INSTALL_LARGEST_CASE)
        self.assertLessEqual(
            count_response_tokens(dict(_INSTALL_LARGEST_CASE)),
            bound_for("runtime_install"),
        )

    def test_projection_forwards_no_undeclared_fields(self) -> None:
        for stop_ok in (True, False):
            produced, _ = _produced_install_compact(stop_ok=stop_ok)
            self.assertNotIn("operation", produced)
            self.assertEqual(set(produced) - set(RUNTIME_INSTALL_ANSWER_FIELDS), set())
            for message in produced.get("messages", []):
                self.assertLessEqual(len(message), 201)

    def test_row_bound_governs_install_overflow(self) -> None:
        # Over the 600 row bound but under the 4,000 net: only the row bound
        # overflows this answer, through the unchanged L108 guard.
        whole = dict(_INSTALL_LARGEST_CASE)
        whole["messages"] = [f"note {index:04d}-abcdefgh;" * 8 for index in range(5)]
        measured = count_response_tokens(whole)
        self.assertGreater(measured, bound_for("runtime_install"))
        self.assertLess(measured, TOOL_ANSWER_EXCEPTION_MAX_TOKENS)
        with tempfile.TemporaryDirectory() as tmp:
            coord = Path(tmp)
            config = _stub_config(coord)
            with tool_call_context("runtime_install", {"dry_run": True}, config=config):
                answer = complete_tool_response("runtime_install", whole)
            self.assertTrue(answer.get("overflow"))
            self.assertLessEqual(count_response_tokens(answer), TOOL_ANSWER_OVERFLOW_MAX_TOKENS)
            lines = [
                json.loads(line)
                for file in sorted(
                    (coord / "logs" / "mcp").glob(f"{TOOL_LOG_BASENAME_PREFIX}*.jsonl")
                )
                for line in file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0]["guardCut"])
