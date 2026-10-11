"""MIK-R110 rules 1-2: the citation row's cases derived through the real tool path.

``citation_fix`` runs against a real leaf fixture on both converted and
unconverted memory; the answers the payload builder projects are proved
against the row (fields, next-step uses, reason/action preservation, no
guard cut, measured bounds, production-bound equality). The same module
proves the rule-6 guard: a real over-bound call hits it, normal largest
cases do not.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from agents_remember.application.memory_tools import (
    CitationOperationScope,
    citation_fix_tool,
    citation_source_index_build_tool,
)
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.mcp.tools.memory import _citation_fix_answer
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.citations.source_index import (
    open_repository_index,
)
from agents_remember.models.tools.tool_answer import bound_for
from test_worktree_support import git, open_external_contract_fixture
from tool_answer_l110_rows import (
    assert_answer_survives_the_guard,
    assert_production_answer,
)

_TOOL = "citation_fix"
LAYOUT_MARKER = '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"\n}\n'


def _scoped_pair(root: Path) -> tuple[Any, McpRuntimeConfig, McpRuntimeConfig]:
    """One real leaf pair with a scoped runtime config over its worktrees."""

    contract = open_external_contract_fixture(root)
    config_path = root / "settings" / "mcp.json"
    configured = McpRuntimeConfig(
        config_path=config_path,
        coordination_root=contract.coordination_root,
        workspace_root=contract.worktree_group,
        transcript_root=contract.coordination_root / "logs" / "mcp",
        repositories={
            contract.repo_name: RepositoryScope(
                repo_id=contract.repo_name,
                path=contract.code_repo_path,
                memory_root=contract.memory_repo_path,
            )
        },
    )
    scoped = McpRuntimeConfig(
        config_path=config_path,
        coordination_root=contract.coordination_root,
        workspace_root=contract.worktree_group,
        transcript_root=configured.transcript_root,
        repositories={
            contract.repo_name: RepositoryScope(
                repo_id=contract.repo_name,
                path=contract.code_worktree,
                memory_root=contract.memory_worktree,
                contract_path=contract.contract_path,
            )
        },
    )
    return contract, configured, scoped


def _write_card(onboarding: Path, name: str, source: str, *, anchor: str = "run") -> None:
    onboarding.mkdir(parents=True, exist_ok=True)
    (onboarding / name).write_text(
        f"# {name}\n\n"
        "| Finding | Anchor | Source |\n"
        "| --- | --- | --- |\n"
        f"| Does things. | `{anchor}` | {source} |\n",
        encoding="utf-8",
    )


def _run_fix(
    scoped: McpRuntimeConfig,
    configured: McpRuntimeConfig,
    contract: Any,
    scope: CitationOperationScope,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    with patch(
        "agents_remember.worktrees.integration.configured_contract_authority.load_config",
        return_value=configured,
    ):
        # The real workflow builds the frozen source generation first; the
        # fixer enumerates tree-wide candidates from the published index.
        citation_source_index_build_tool(
            scoped,
            repo_id=contract.repo_name,
            contract_path=contract.contract_path.as_posix(),
            operation_scope=CitationOperationScope(),
        )
        return citation_fix_tool(
            scoped,
            repo_id=contract.repo_name,
            contract_path=contract.contract_path.as_posix(),
            operation_scope=scope,
            dry_run=dry_run,
        )


class CitationProductionTests(unittest.TestCase):
    def test_unconverted_paths_derive_the_row_cases(self) -> None:
        """Real unconverted runs: one clean card passes, 25 broken ones fail bounded."""

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract, configured, scoped = _scoped_pair(root)
            package = contract.code_worktree / "pkg"
            package.mkdir(parents=True, exist_ok=True)
            (package / "real.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            onboarding = contract.memory_worktree / "onboarding"
            _write_card(onboarding, "clean.md", "pkg/real.py:1-2")
            whole_clean = _run_fix(scoped, configured, contract, CitationOperationScope())
            for index in range(25):
                _write_card(
                    onboarding,
                    f"broken-{index:02d}.md",
                    f"pkg/missing-{index:02d}.py:1-2",
                    anchor=f"zzq{index:02d}uniq",
                )
            git(contract.memory_worktree, "add", "-A")
            whole_failed = _run_fix(scoped, configured, contract, CitationOperationScope())
        with self.subTest("ordinary"):
            answer = _citation_fix_answer(whole_clean)
            self.assertTrue(answer["ok"])
            assert_production_answer(self, _TOOL, answer)
            assert_answer_survives_the_guard(_TOOL, whole_clean, answer)
        with self.subTest("largest"):
            answer = _citation_fix_answer(whole_failed)
            self.assertFalse(answer["ok"])
            self.assertEqual(answer["refusedCount"], 0)
            self.assertGreaterEqual(answer["staleCount"], 20)
            self.assertEqual(len(answer["declined"]), 10)
            # Round-3 obligation: the slim entry keeps the known place (line)
            # beside document, code, and message; anchors ride in the message.
            for entry in answer["declined"]:
                self.assertIn("line", entry)
            measured = assert_production_answer(self, _TOOL, answer)
            self.assertLessEqual(measured, 1500)
            self.assertLessEqual(1500, 2 * measured)
            assert_answer_survives_the_guard(_TOOL, whole_failed, answer)
        with self.subTest("documents"):
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                contract, configured, scoped = _scoped_pair(root)
                package = contract.code_worktree / "pkg"
                package.mkdir(parents=True, exist_ok=True)
                (package / "real.py").write_text("def run():\n    return 1\n", encoding="utf-8")
                onboarding = contract.memory_worktree / "onboarding"
                _write_card(onboarding, "a.md", "pkg/real.py:1-2")
                _write_card(onboarding, "b.md", "pkg/real.py:1-2")
                with open_repository_index(
                    Trees(
                        code_root=contract.code_worktree,
                        memory_root=contract.memory_worktree,
                    )
                ) as index:
                    snapshot = index.snapshot_id
                whole = _run_fix(
                    scoped,
                    configured,
                    contract,
                    CitationOperationScope(documents=("a.md", "b.md"), expected_snapshot=snapshot),
                )
            answer = _citation_fix_answer(whole)
            self.assertTrue(answer["ok"])
            self.assertEqual(answer["documentCount"], 2)
            assert_production_answer(self, _TOOL, answer)

    def test_converted_paths_derive_the_row_cases(self) -> None:
        """Real converted runs: authored cards pass, unresolvable ones refuse bounded."""

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract, configured, scoped = _scoped_pair(root)
            package = contract.code_worktree / "pkg"
            package.mkdir(parents=True, exist_ok=True)
            (package / "real.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            memory = contract.memory_worktree
            (memory / "knowledge").mkdir(parents=True, exist_ok=True)
            (memory / "knowledge" / "layout.json").write_text(LAYOUT_MARKER, encoding="utf-8")
            onboarding = memory / "onboarding"
            _write_card(onboarding, "good.md", "pkg/real.py:1-2")
            whole_clean = _run_fix(scoped, configured, contract, CitationOperationScope())
            for index in range(25):
                name = f"lost-{index:02d}.md"
                _write_card(onboarding, name, "pkg/real.py:1-2")
                # A sidecar that does not parse refuses the card at authoring:
                # the refusal names the card and the parse failure, nothing more.
                (onboarding / f"lost-{index:02d}.json").write_text("{not json", encoding="utf-8")
            whole_failed = _run_fix(scoped, configured, contract, CitationOperationScope())
        with self.subTest("ordinary"):
            answer = _citation_fix_answer(whole_clean)
            self.assertTrue(answer["ok"])
            assert_production_answer(self, _TOOL, answer)
            assert_answer_survives_the_guard(_TOOL, whole_clean, answer)
        with self.subTest("largest"):
            answer = _citation_fix_answer(whole_failed)
            self.assertFalse(answer["ok"])
            self.assertGreaterEqual(answer["unreadableCount"], 20)
            self.assertEqual(len(answer["unreadableSidecars"]), 10)
            assert_production_answer(self, _TOOL, answer)
            assert_answer_survives_the_guard(_TOOL, whole_failed, answer)

    def test_a_real_over_bound_call_hits_the_guard(self) -> None:
        """A real pathological call (verbose declines) exceeds the bound and is cut."""

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract, configured, scoped = _scoped_pair(root)
            package = contract.code_worktree / "pkg"
            package.mkdir(parents=True, exist_ok=True)
            (package / "real.py").write_text("def run():\n    return 1\n", encoding="utf-8")
            onboarding = contract.memory_worktree / "onboarding"
            # Adversarially long card paths: even slimmed to document, code,
            # and message and capped at the status entry cap, ten such rows
            # exceed the default bound, so the guard still cuts a real call.
            # (230 chars stays under the OS filename limit.)
            long_dir = "deep-" + "x" * 230
            for index in range(20):
                _write_card(
                    onboarding,
                    f"{long_dir}-{index:02d}.md",
                    f"pkg/missing-{index:02d}.py:1-2",
                )
            git(contract.memory_worktree, "add", "-A")
            whole = _run_fix(scoped, configured, contract, CitationOperationScope())
        answer = _citation_fix_answer(whole)
        self.assertFalse(answer["ok"])
        delivered = complete_tool_response(_TOOL, dict(whole), answer=dict(answer))
        self.assertTrue(delivered.get("overflow"))
        self.assertLessEqual(bound_for(_TOOL), 1500)
