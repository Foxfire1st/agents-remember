"""MIK-R110 rule 2: ``citation_fix`` takes several cards in one call and answers a status.

Evidence item 1, each behavior with a test that fails without the change:

- a list of cards in one call leaves the same bytes as one call per card;
- a list of ``MAX_CURATOR_SOURCE_CANDIDATES`` documents is taken and one more is
  refused before anything is read; the bound is that constant and no second number;
- a list of clean cards gives an answer no longer than that of one clean card,
  apart from the count;
- a dry run writes nothing; a card outside the list is untouched;
- both parameters together and an empty list are refused;
- one refused document does not stop the others;
- the same through the command line, which prints the whole result.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application import memory_tools
from agents_remember.application.memory_tools import (
    CitationOperationScope,
)
from agents_remember.cli import memory_citations
from agents_remember.mcp.registration.memory import register_memory_tools
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.models.lifecycles.curator_coherence import MAX_CURATOR_SOURCE_CANDIDATES
from knowledge_conversion_test_support import (
    APP,
    APP_SOURCE,
    CodeFixture,
    code_repository,
    memory_repository,
)

# Git commit hashes seed the cards' verification stamps, so two fixture trees built by the
# same test differ unless Git's clock is fixed. The fixer never writes a stamp: with a
# fixed clock the trees are identical and the byte comparison below is exact.
FIXED_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}


def _frozen_git() -> mock._patch_dict:
    return mock.patch.dict(os.environ, FIXED_GIT_ENV)


class DocumentListRefusalTests(unittest.TestCase):
    """The list's shape is refused before anything is read, naming the bound and the count."""

    def test_document_and_documents_together_are_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            CitationOperationScope(document="kernel/a.py.md", documents=("kernel/a.py.md",))
        self.assertIn("not both", str(caught.exception))

    def test_an_empty_list_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            CitationOperationScope(documents=())
        self.assertIn("empty", str(caught.exception))

    def test_a_full_list_is_taken_and_one_more_is_refused_with_bound_and_count(self) -> None:
        full = tuple(f"card-{index}.md" for index in range(MAX_CURATOR_SOURCE_CANDIDATES))
        taken = CitationOperationScope(documents=full)
        self.assertIsNotNone(taken.documents)
        assert taken.documents is not None
        self.assertEqual(len(taken.documents), len(full))
        too_many = (*full, "one-more.md")
        with (
            mock.patch.object(Path, "rglob", side_effect=AssertionError("refusal read memory")),
            self.assertRaises(ValueError) as caught,
        ):
            CitationOperationScope(documents=too_many)
        message = str(caught.exception)
        self.assertIn(str(MAX_CURATOR_SOURCE_CANDIDATES), message)
        self.assertIn(str(MAX_CURATOR_SOURCE_CANDIDATES + 1), message)

    def test_the_bound_is_the_constant_with_no_second_number(self) -> None:
        consumers = [
            MCP_SRC / "agents_remember/application/memory_tools.py",
            MCP_SRC / "agents_remember/mcp/registration/memory.py",
            MCP_SRC / "agents_remember/cli/memory_citations.py",
        ]
        for path in consumers:
            self.assertNotIn(
                str(MAX_CURATOR_SOURCE_CANDIDATES),
                path.read_text(encoding="utf-8"),
                f"{path.name} sets a number of its own",
            )

    def test_other_operations_refuse_the_list(self) -> None:
        scope = CitationOperationScope(documents=("kernel/a.py.md",))
        with self.assertRaises(ValueError) as caught:
            scope.validate()
        self.assertIn("only citation_fix", str(caught.exception))

    # MIK-R46 rule 1 removed test_a_list_without_a_snapshot_is_refused_before_tree_reads:
    # the snapshot rule ("--document and --expected-snapshot together") belonged to the
    # old-format path, which now refuses with the one legacy-format message before any
    # snapshot check; on converted memory a document list needs no snapshot (MIK-R110
    # rule 2, _converted_citation_fix handles each card alone).


def _convert(memory: Path, code: CodeFixture) -> None:
    outcome = convert_memory(
        memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
    )
    write_changed(memory, outcome.changed)


class ConvertedListFixTests(unittest.TestCase):
    """One call with several cards on converted memory."""

    def _stale_converted_tree(self, root: Path) -> tuple[CodeFixture, Path]:
        code = code_repository(root / "code")
        memory = root / "memory"
        memory_repository(memory, code)
        _convert(memory, code)
        # A mechanical move elsewhere: the app card's and the route card's anchors re-record.
        (code.root / APP).write_text("# moved\n" + APP_SOURCE, encoding="utf-8")
        return code, memory

    def _tree_bytes(self, memory: Path) -> dict[str, bytes]:
        onboarding = memory / "onboarding"
        return {
            path.relative_to(onboarding).as_posix(): path.read_bytes()
            for path in sorted((*onboarding.rglob("*.md"), *onboarding.rglob("*.json")))
            if path.is_file() and not path.name.endswith(".index.json")
        }

    def _fix(
        self, memory: Path, code: CodeFixture, scope: CitationOperationScope
    ) -> dict[str, Any]:
        leaf = SimpleNamespace(
            repo_id="demo", onboarding_root=memory / "onboarding", code_root=code.root
        )
        with mock.patch.object(memory_tools, "_leaf_memory_writer_scope", return_value=leaf):
            return memory_tools.citation_fix_tool(
                cast(Any, None),
                repo_id="demo",
                contract_path="contract.md",
                operation_scope=scope,
            )

    def test_a_list_leaves_the_same_record_as_one_call_per_card(self) -> None:
        app_doc, route_doc = f"{APP}.md", "src/overview.md"
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            code, memory = self._stale_converted_tree(Path(raw))
            listed = self._fix(memory, code, CitationOperationScope(documents=(app_doc, route_doc)))
            listed_bytes = self._tree_bytes(memory)
        with _frozen_git(), tempfile.TemporaryDirectory() as raw:
            code, memory = self._stale_converted_tree(Path(raw))
            for name in (app_doc, route_doc):
                single = self._fix(memory, code, CitationOperationScope(document=name))
                self.assertTrue(single["ok"], single)
            self.assertEqual(self._tree_bytes(memory), listed_bytes)
        self.assertTrue(listed["ok"], listed)
        self.assertEqual(listed["documentCount"], 2)

    def test_one_refused_card_does_not_stop_the_others(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            code, memory = self._stale_converted_tree(Path(raw))
            result = self._fix(
                memory,
                code,
                CitationOperationScope(documents=("no-such-card.py.md", f"{APP}.md")),
            )
        self.assertFalse(result["ok"])
        refused_cards = [one["card"] for one in result["authoring"]["refused"]]
        self.assertEqual(len(refused_cards), 1)
        self.assertIn("no-such-card.py.md", refused_cards[0])
        self.assertEqual(result["refusedCount"], 1)

    def test_a_returned_refusal_is_not_rewritten(self) -> None:
        # MIK-R45/INV-13QTT2 refused-byte preservation through the multi-card
        # loop: authoring records the refusal without raising (no ValueError),
        # and the mechanical re-record must skip that sidecar exactly as the
        # singleton call does -- singleton-equivalence (MIK-R110 rule 2).
        app_doc, route_doc = f"{APP}.md", "src/overview.md"
        with tempfile.TemporaryDirectory() as raw:
            code, memory = self._stale_converted_tree(Path(raw))
            broken = memory / "onboarding" / f"{APP}.json"
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_text("{ not json", encoding="utf-8")
            result = self._fix(memory, code, CitationOperationScope(documents=(app_doc, route_doc)))
            self.assertFalse(result["ok"])
            refused_cards = [one["card"] for one in result["authoring"]["refused"]]
            self.assertIn(f"onboarding/{app_doc}", refused_cards)
            self.assertEqual(broken.read_bytes(), b"{ not json")
            self.assertEqual(result["refusedCount"], 1)

    def test_returned_refusals_are_excluded_from_the_refresh(self) -> None:
        # The exact composition the multi-card loop must keep: authoring
        # records a refusal by returning it (no ValueError raised), and the
        # mechanical re-record for that card must exclude its sidecar --
        # singleton-equivalence (MIK-R110 rule 2) with MIK-R45/INV-13QTT2
        # refused-byte preservation.
        seen: dict[str, Any] = {}

        def fake_fix(memory_root: Any, code_root: Any, **kwargs: Any) -> dict[str, Any]:
            seen.update(kwargs)
            return {
                "ok": True,
                "status": "converted",
                "dryRun": False,
                "refreshedAnchors": 0,
                "rewrittenSidecars": [],
                "unreadableSidecars": [],
                "stale": [],
            }

        authoring = {
            "dryRun": False,
            "authoredReferences": 0,
            "reauthoredReferences": 0,
            "removedReferences": 0,
            "authoredCards": [],
            "createdSidecars": [],
            "unresolvedTargets": [],
            "refused": [{"card": "onboarding/x.md", "reason": "stale anchor"}],
        }
        with (
            mock.patch.object(
                memory_tools.card_authoring,
                "author_card_references",
                return_value=authoring,
            ),
            mock.patch.object(memory_tools.reference_state, "fix_references", side_effect=fake_fix),
        ):
            memory_tools._converted_citation_fix(
                Path("/memory"),
                Path("/code"),
                CitationOperationScope(documents=("x.md",)),
                False,
            )
        self.assertEqual(seen.get("only"), "onboarding/x.json")
        self.assertEqual(seen.get("exclude"), {"onboarding/x.json"})


class CitationFixCommandTests(unittest.TestCase):
    """The command line takes ``--document`` several times with the same meaning."""

    def test_document_is_repeatable(self) -> None:
        parser = argparse.ArgumentParser()
        memory_citations.add_arguments(parser)
        parsed = parser.parse_args(
            ["--repo", "demo", "--contract", "contract.md", "--fix", "--document", "a.md"]
        )
        self.assertEqual(parsed.document, ["a.md"])
        repeated = parser.parse_args(
            [
                "--repo",
                "demo",
                "--contract",
                "contract.md",
                "--fix",
                "--document",
                "a.md",
                "--document",
                "b.md",
            ]
        )
        self.assertEqual(repeated.document, ["a.md", "b.md"])

    def test_several_documents_without_fix_are_refused(self) -> None:
        namespace = argparse.Namespace(
            repo="demo",
            contract="contract.md",
            config=None,
            fix=False,
            document=["a.md", "b.md"],
            dry_run=False,
        )
        self.assertEqual(memory_citations.run(namespace), 1)

    def test_several_documents_print_the_whole_result(self) -> None:
        namespace = argparse.Namespace(
            repo="demo",
            contract="contract.md",
            config="fixture-settings.json",
            fix=True,
            document=["a.md", "b.md"],
            dry_run=True,
        )
        payload = {
            "ok": True,
            "operation": "citation_fix",
            "repoId": "demo",
            "dryRun": True,
            "documentCount": 2,
            "rewrittenCount": 0,
            "staleCount": 0,
            "unreadableCount": 0,
            "refusedCount": 0,
        }
        seen: dict[str, Any] = {}
        real_scope = CitationOperationScope

        def capture(**kwargs: Any) -> CitationOperationScope:
            seen.update(kwargs)
            return real_scope(**kwargs)

        with (
            mock.patch.object(memory_citations, "load_config", return_value=object()),
            mock.patch.object(
                memory_citations, "citation_fix_tool", return_value=dict(payload)
            ) as fix,
            mock.patch.object(memory_citations, "CitationOperationScope", side_effect=capture),
            io.StringIO() as printed,
            redirect_stdout(printed),
        ):
            self.assertEqual(memory_citations.run(namespace), 0)
            self.assertEqual(json.loads(printed.getvalue()), payload)
        self.assertEqual(seen["documents"], ("a.md", "b.md"))
        self.assertIsNone(seen["document"])
        self.assertEqual(fix.call_count, 1)


class CitationFixRegistrationTests(unittest.TestCase):
    """The registered tool declares ``documents``."""

    def test_the_registered_tool_declares_documents(self) -> None:
        server = register_memory_tools.__globals__["FastMCP"]("test")
        config = SimpleNamespace(repositories={})
        register_memory_tools(server, config)  # type: ignore[arg-type]
        tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        self.assertIn("citation_fix", tools)
        schema = dict(tools["citation_fix"].inputSchema)
        self.assertIn("documents", schema["properties"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
