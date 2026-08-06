from __future__ import annotations

import argparse
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

from agents_remember.application import memory_tools
from agents_remember.application.memory_tools import (
    CitationOperationScope,
    citation_check_tool,
    citation_fix_tool,
    citation_migrate_tool,
    citation_source_index_build_tool,
)
from agents_remember.cli import memory_citations
from agents_remember.cli.__main__ import build_parser
from agents_remember.errors import AuthorityError
from agents_remember.memory_quality.style.citations import (
    extents,
    model,
    source_index,
    source_index_cache,
    source_index_database,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.worktrees.worktree_contract import write_contract
from test_memory_citation_fix import _frozen_no_discovery, document
from test_memory_tool_enclosure_scope import REPO, _enclosure


class SymbolIndexTests(unittest.TestCase):
    """The one walk both halves share: what it reads, what it skips, what it counts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.code = self.root / "code"
        self.memory = self.root / "memory"
        for base in (self.code, self.memory):
            base.mkdir(parents=True)

    def write(self, base: Path, relative: str, body: str) -> None:
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def trees(self) -> Trees:
        return Trees(code_root=self.code, memory_root=self.memory)

    def locate(self, *names: str) -> dict[model.Anchor, symbol_index.Sightings]:
        anchors = tuple(model.Anchor(kind=model.SYMBOL, text=name) for name in names)
        return symbol_index.locate(anchors, self.trees())

    def test_an_empty_batch_walks_nothing(self) -> None:
        self.write(self.code, "kernel/store.py", "def persist():\n    pass\n")

        self.assertEqual(symbol_index.locate((), self.trees()), {})

    def test_build_output_and_dependencies_are_not_locations(self) -> None:
        self.write(self.code, "node_modules/pkg/index.js", "const persist = 1;\n")
        self.write(self.code, "kernel/__pycache__/store.py", "persist = 1\n")
        self.write(self.code, "kernel/store.py", "def persist():\n    pass\n")

        self.assertEqual(self.locate("persist")[self.anchor("persist")].files, 1)

    def test_a_binary_suffix_is_not_read(self) -> None:
        self.write(self.code, "docs/persist.png", "persist\n")

        self.assertEqual(self.locate("persist")[self.anchor("persist")].files, 0)

    def test_an_internal_memory_tree_inside_the_code_tree_is_not_indexed(self) -> None:
        """A card holds its own anchor by construction; indexing cards ends the tiebreaker."""
        trees = Trees(code_root=self.code, memory_root=self.code / "memory")
        self.write(self.code, "memory/onboarding/card.md", "persist\n")
        self.write(self.code, "kernel/store.py", "def persist():\n    pass\n")

        found = symbol_index.locate((self.anchor("persist"),), trees)[self.anchor("persist")]

        self.assertEqual(found.files, 1)
        self.assertEqual(found.locations[0].path, "kernel/store.py")

    def test_a_file_that_cannot_be_read_is_skipped_rather_than_crashing_the_gate(self) -> None:
        self.write(self.code, "kernel/store.py", "def persist():\n    pass\n")

        with mock.patch.object(Path, "read_bytes", side_effect=OSError("locked")):
            found = self.locate("persist")[self.anchor("persist")]

        self.assertEqual(found.files, 0)

    def test_a_vacuous_anchor_reports_its_own_vacuity_as_an_exact_count(self) -> None:
        for index in range(symbol_index.LOCATION_FILE_LIMIT + 5):
            self.write(self.code, f"kernel/mod{index}.py", "print(value)\n")

        found = self.locate("value")[self.anchor("value")]

        self.assertEqual(found.files, symbol_index.LOCATION_FILE_LIMIT + 5)
        self.assertIsNone(found.unique)
        described = symbol_index.described(self.anchor("value"), found)
        self.assertIn(f"first {symbol_index.LOCATION_FILE_LIMIT} mentions", described)

    def test_a_python_mention_never_resolves_a_move_even_when_it_is_the_only_one(self) -> None:
        """Measured live: a docstring discussing a deleted name was almost cited as its home."""
        self.write(self.code, "kernel/notes.py", '"""The `persist` path was removed."""\n')

        self.assertIsNone(self.locate("persist")[self.anchor("persist")].unique)

    def test_a_name_defined_twice_in_one_file_does_not_resolve_uniquely(self) -> None:
        self.write(
            self.code, "kernel/store.py", "def persist():\n    pass\n\n\ndef persist():\n    pass\n"
        )

        self.assertIsNone(self.locate("persist")[self.anchor("persist")].unique)

    def test_two_symbol_anchors_share_one_read_and_one_parse_of_each_file(self) -> None:
        self.write(self.code, "kernel/store.py", "def persist():\n    pass\n\n\nreload = 1\n")

        found = self.locate("persist", "reload")

        self.assertEqual(found[self.anchor("persist")].locations[0].written, "kernel/store.py:1-2")
        self.assertEqual(found[self.anchor("reload")].locations[0].written, "kernel/store.py:5-5")

    def anchor(self, name: str) -> model.Anchor:
        return model.Anchor(kind=model.SYMBOL, text=name)


class ExtentTests(unittest.TestCase):
    """The generator, on the shapes a whole-tree run meets."""

    def test_every_assignment_form_binds_its_names(self) -> None:
        lines = ["first, second = 1, 2", "third: int = 3", "fourth = 0", "fourth += 1"].copy()
        found = extents.definitions("kernel/store.py", lines)

        self.assertEqual([one.start for one in found["first"]], [1])
        self.assertEqual([one.start for one in found["second"]], [1])
        self.assertEqual([one.start for one in found["third"]], [2])
        self.assertEqual([one.start for one in found["fourth"]], [3, 4])

    def test_an_unpacking_target_that_is_not_a_name_binds_nothing(self) -> None:
        self.assertEqual(extents.definitions("kernel/store.py", ["holder.field = 1"]), {})

    def test_an_empty_quote_matches_nothing_rather_than_every_offset(self) -> None:
        self.assertEqual(extents.quote_extents("   ", ["anything at all"]), ())

    def test_a_quote_that_appears_twice_yields_both_windows(self) -> None:
        found = extents.quote_extents("import string", ["import string", "x", "import string"])

        self.assertEqual([(one.start, one.end) for one in found], [(1, 1), (3, 3)])

    def test_word_mark_lookup_is_logarithmic_at_first_middle_and_last_offsets(self) -> None:
        class CountingMarks:
            def __init__(self, size: int) -> None:
                self.size = size
                self.accesses = 0

            def __len__(self) -> int:
                return self.size

            # 260731-EFA-L7 R10: test moved verbatim in L7 split; branch not exercised by the unchanged assertion set (mcp/tests/test_memory_citation_fix_operations.py:164).
            def __getitem__(self, index: int) -> extents.WordMark:  # pragma: no cover
                if not 0 <= index < self.size:
                    raise IndexError(index)
                self.accesses += 1
                start = index * 2
                return extents.WordMark(start, start + 1, index + 1, index, "x")

        marks = CountingMarks(65_536)
        indexed = cast(tuple[extents.WordMark, ...], marks)
        for index in (0, 32_768, 65_535):
            with self.subTest(index=index):
                marks.accesses = 0
                found = extents.word_mark_at(indexed, index * 2)
                self.assertEqual(found.collapsed_start, index * 2)
                self.assertLessEqual(marks.accesses, 20)

    def test_word_mark_lookup_keeps_stop_iteration_outside_mark_intervals(self) -> None:
        marks = (
            extents.WordMark(0, 1, 1, 0, "a"),
            extents.WordMark(2, 3, 1, 2, "b"),
        )

        for offset in (-1, 1, 3):
            with self.subTest(offset=offset), self.assertRaises(StopIteration):
                extents.word_mark_at(marks, offset)

    def test_dense_short_quotes_keep_exact_utf8_source_byte_boundaries(self) -> None:
        lines = ["λ0é0" * 64, "雪0ø0" * 64]

        found = extents.quote_matches_in("0", extents.collapsed(lines))

        expected: list[tuple[int, int, int, int]] = []
        line_start = 0
        for line_number, line in enumerate(lines, start=1):
            at = line.find("0")
            while at >= 0:
                byte_start = line_start + len(line[:at].encode("utf-8"))
                expected.append((line_number, line_number, byte_start, byte_start + 1))
                at = line.find("0", at + 1)
            line_start += len(line.encode("utf-8")) + 1
        self.assertEqual(
            [(one.start, one.end, one.source_byte_start, one.source_byte_end) for one in found],
            expected,
        )

    def test_a_heading_inside_a_fence_is_not_a_section(self) -> None:
        lines = ["```md", "## Scoping", "```", "## Scoping", "body"]

        found = extents.heading_extents("## Scoping", lines)

        self.assertEqual([(one.start, one.end) for one in found], [(4, 5)])

    def test_consecutive_mentions_group_and_distant_ones_do_not(self) -> None:
        pattern = model.whole_identifier("mark")
        found = extents.occurrence_runs(pattern, ["mark", "mark", "x", "mark"])

        self.assertEqual([(one.start, one.end) for one in found], [(1, 2), (4, 4)])

    def test_overlapping_and_adjacent_spans_merge_and_gapped_ones_do_not(self) -> None:
        self.assertEqual(extents.merged([(1, 3), (2, 5), (6, 7), (20, 21)]), ((1, 7), (20, 21)))

    def test_an_anchor_kind_dispatches_to_its_own_rule(self) -> None:
        lines = ["## Scoping", "body"]

        self.assertEqual(
            extents.anchor_extents(
                model.Anchor(kind=model.HEADING, text="## Scoping"), "a.md", lines
            ),
            (extents.Extent(start=1, end=2, kind=extents.SECTION),),
        )


class WriteGuardTests(unittest.TestCase):
    """L6-R27: the fixer writes into a leaf's memory worktree or it does not write."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.enclosure = _enclosure(Path(self._tmp.name))
        self.contract_path = self.enclosure.contract.contract_path.as_posix()

    def snapshot(self, root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def plant(self) -> None:
        """A failing citation in the LEAF's memory worktree, against the LEAF's code."""
        card = self.enclosure.leaf_onboarding / "leaf_only.py.fix.md"
        card.write_text(
            document(
                "| The leaf's own constant. | `VALUE` | leaf_only.py:400-480 |",
                path="leaf_only.py",
            ),
            encoding="utf-8",
        )

    def test_a_contract_scoped_fix_writes_the_leaf_and_leaves_the_official_repo_alone(
        self,
    ) -> None:
        self.plant()
        before = self.snapshot(self.enclosure.official_onboarding)

        payload = citation_fix_tool(
            self.enclosure.config, repo_id=REPO, contract_path=self.contract_path
        )

        self.assertEqual(payload["claimsRepaired"], 1)
        self.assertEqual(payload["repairs"][0]["now"], "leaf_only.py:1-1")
        self.assertEqual(payload["onboardingRoot"], self.enclosure.leaf_onboarding.as_posix())
        self.assertEqual(self.snapshot(self.enclosure.official_onboarding), before)

    def test_a_contract_naming_the_official_repo_as_its_worktree_is_refused(self) -> None:
        official = self.enclosure.contract.memory_repo_path
        assert official is not None
        pointed = self.enclosure.contract.contract_path.parent / "official-contract.md"
        write_contract(
            pointed,
            replace(self.enclosure.contract, memory_worktree=official, contract_path=pointed),
        )

        with self.assertRaises(AuthorityError) as raised:
            citation_fix_tool(self.enclosure.config, repo_id=REPO, contract_path=pointed.as_posix())

        self.assertIn("refuses to write into the OFFICIAL memory repo", str(raised.exception))
        self.assertIn(official.as_posix(), str(raised.exception))

    def test_a_contract_for_another_repo_is_refused_rather_than_resolved(self) -> None:
        foreign = self.enclosure.contract.contract_path.parent / "foreign-contract.md"
        write_contract(
            foreign,
            replace(self.enclosure.contract, repo_name="other-repo", contract_path=foreign),
        )

        with self.assertRaises(AuthorityError) as raised:
            citation_fix_tool(self.enclosure.config, repo_id=REPO, contract_path=foreign.as_posix())

        self.assertIn("names repo 'other-repo'", str(raised.exception))

    def test_a_contract_path_outside_the_coordinator_root_is_refused(self) -> None:
        outside = (Path(self._tmp.name) / "elsewhere" / "series-contract.md").as_posix()

        with self.assertRaises(AuthorityError) as raised:
            citation_fix_tool(self.enclosure.config, repo_id=REPO, contract_path=outside)

        self.assertIn("contract_path must stay inside coordination_root", str(raised.exception))

    def test_an_invalid_build_contract_never_reaches_the_cache_builder(self) -> None:
        outside = (Path(self._tmp.name) / "elsewhere" / "series-contract.md").as_posix()

        with (
            mock.patch.object(source_index, "build_repository_index") as build,
            self.assertRaises(AuthorityError),
        ):
            citation_source_index_build_tool(
                self.enclosure.config,
                repo_id=REPO,
                contract_path=outside,
            )

        build.assert_not_called()

    def test_an_internal_memory_contract_has_no_leaf_memory_tree_to_write(self) -> None:
        internal = self.enclosure.contract.contract_path.parent / "internal-contract.md"
        write_contract(
            internal,
            replace(
                self.enclosure.contract,
                memory_mode="internal",
                memory_worktree=None,
                ledger_path=None,
                contract_path=internal,
            ),
        )

        with self.assertRaises(ValueError) as raised:
            citation_fix_tool(
                self.enclosure.config, repo_id=REPO, contract_path=internal.as_posix()
            )

        self.assertIn("carries no memory worktree", str(raised.exception))

    def test_a_dry_run_through_the_tool_writes_nothing(self) -> None:
        self.plant()
        before = self.snapshot(self.enclosure.leaf_onboarding)

        payload = citation_fix_tool(
            self.enclosure.config, repo_id=REPO, contract_path=self.contract_path, dry_run=True
        )

        self.assertTrue(payload["dryRun"])
        self.assertEqual(self.snapshot(self.enclosure.leaf_onboarding), before)

    def test_expected_snapshot_reaches_scoped_check_fix_and_postcheck_without_a_walk(
        self,
    ) -> None:
        self.plant()
        cache = Path(self._tmp.name) / "citation-cache"
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": cache.as_posix()}):
            built = citation_source_index_build_tool(
                self.enclosure.config,
                repo_id=REPO,
                contract_path=self.contract_path,
            )
            snapshot = cast(dict[str, Any], built["sourceIndex"])["snapshotId"]
            with (
                mock.patch.object(
                    Path,
                    "rglob",
                    side_effect=AssertionError("frozen document operation scanned memory"),
                ),
                mock.patch.object(
                    source_index.os,
                    "walk",
                    side_effect=AssertionError("frozen document operation walked source"),
                ),
                mock.patch.object(
                    source_index,
                    "_tree_state",
                    side_effect=AssertionError("frozen document operation inspected source"),
                ),
                mock.patch.object(
                    source_index,
                    "_reclaim_legacy_cache_roots",
                    side_effect=AssertionError("frozen document operation reclaimed cache"),
                ),
                mock.patch.object(
                    source_index,
                    "_build_and_publish",
                    side_effect=AssertionError("frozen document operation rebuilt/fell back"),
                ),
                mock.patch.object(
                    source_index_database.Database,
                    "validate_application_integrity",
                    side_effect=AssertionError("frozen document operation traversed integrity"),
                ),
            ):
                checked = citation_check_tool(
                    self.enclosure.config,
                    repo_id=REPO,
                    contract_path=self.contract_path,
                    operation_scope=CitationOperationScope(
                        document="leaf_only.py.fix.md",
                        expected_snapshot=cast(str, snapshot),
                    ),
                )
                fixed = citation_fix_tool(
                    self.enclosure.config,
                    repo_id=REPO,
                    contract_path=self.contract_path,
                    dry_run=True,
                    operation_scope=CitationOperationScope(
                        document="leaf_only.py.fix.md",
                        expected_snapshot=cast(str, snapshot),
                    ),
                )
                migrated = citation_migrate_tool(
                    self.enclosure.config,
                    repo_id=REPO,
                    contract_path=self.contract_path,
                    operation_scope=CitationOperationScope(
                        document="leaf_only.py.fix.md",
                        expected_snapshot=cast(str, snapshot),
                    ),
                )

        self.assertEqual(checked["filesChecked"], 1)
        self.assertEqual(checked["sourceIndex"]["state"], "frozen")
        self.assertEqual(fixed["documentsScanned"], 1)
        self.assertEqual(fixed["sourceIndex"]["state"], "frozen")
        self.assertTrue(fixed["sourceIndex"]["postFixRecheck"]["reusedLease"])
        self.assertEqual(migrated["documentsScanned"], 1)
        self.assertEqual(migrated["sourceIndex"]["state"], "frozen")
        self.assertTrue(migrated["sourceIndex"]["postFixRecheck"]["reusedLease"])
        for payload in (checked, fixed, migrated):
            telemetry = payload["sourceIndex"]
            self.assertEqual(telemetry["metadataTreeEnumerations"], 0)
            self.assertEqual(telemetry["metadataFilesStat"], 0)
            self.assertEqual(telemetry["metadataDirectoriesStat"], 0)
            self.assertEqual(telemetry["sourceFilesRead"], 0)
            self.assertEqual(telemetry["sourceFilesTokenized"], 0)
            self.assertEqual(telemetry["sourceFilesParsed"], 0)

    def test_frozen_refusals_across_all_tools_do_no_discovery_or_acquisition_work(self) -> None:
        self.plant()
        cache = Path(self._tmp.name) / "citation-refusal-cache"
        tools = (citation_check_tool, citation_fix_tool, citation_migrate_tool)

        def invoke(tool: Any, snapshot: str) -> None:
            keywords: dict[str, Any] = {
                "repo_id": REPO,
                "contract_path": self.contract_path,
                "operation_scope": CitationOperationScope(
                    document="leaf_only.py.fix.md", expected_snapshot=snapshot
                ),
            }
            if tool is not citation_check_tool:
                keywords["dry_run"] = True
            with self.assertRaises(source_index.SourceIndexError):
                tool(self.enclosure.config, **keywords)

        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": cache.as_posix()}):
            first = cast(
                dict[str, Any],
                citation_source_index_build_tool(
                    self.enclosure.config,
                    repo_id=REPO,
                    contract_path=self.contract_path,
                )["sourceIndex"],
            )["snapshotId"]
            paths = source_index.cache_paths(
                Trees(
                    self.enclosure.contract.code_worktree,
                    self.enclosure.leaf_onboarding.parent,
                    cache_authority=source_index_cache.contract_cache_authority(
                        self.enclosure.contract
                    ),
                )
            )
            paths.readiness.unlink()
            with _frozen_no_discovery():
                for tool in tools:
                    invoke(tool, cast(str, first))

            rebuilt = cast(
                dict[str, Any],
                citation_source_index_build_tool(
                    self.enclosure.config,
                    repo_id=REPO,
                    contract_path=self.contract_path,
                )["sourceIndex"],
            )["snapshotId"]
            wrong = "f" * 64 if rebuilt != "f" * 64 else "e" * 64
            with _frozen_no_discovery():
                for tool in tools:
                    invoke(tool, wrong)

            source = self.enclosure.contract.code_worktree / "leaf_only.py"
            source.write_text("VALUE = 2\n", encoding="utf-8")
            replaced = cast(
                dict[str, Any],
                citation_source_index_build_tool(
                    self.enclosure.config,
                    repo_id=REPO,
                    contract_path=self.contract_path,
                )["sourceIndex"],
            )["snapshotId"]
            self.assertNotEqual(rebuilt, replaced)
            with _frozen_no_discovery():
                for tool in tools:
                    invoke(tool, cast(str, rebuilt))

        with _frozen_no_discovery(), self.assertRaises(ValueError):
            CitationOperationScope(document="leaf_only.py.fix.md", expected_snapshot="MALFORMED")


class CommandLineTests(unittest.TestCase):
    """Command-line scope and write-mode contract."""

    def test_the_contract_is_not_optional(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["memory-citations", "--repo", REPO])

    def test_fix_is_a_flag_and_defaults_off(self) -> None:
        args = build_parser().parse_args(
            ["memory-citations", "--repo", REPO, "--contract", "/coord/leaf.md"]
        )

        self.assertFalse(args.fix)
        self.assertFalse(args.build_index)
        self.assertIs(args.func, memory_citations.run)

    def test_document_uses_the_work_order_spelling(self) -> None:
        args = build_parser().parse_args(
            [
                "memory-citations",
                "--repo",
                REPO,
                "--contract",
                "/coord/leaf.md",
                "--document",
                "kernel/store.py.md",
            ]
        )

        self.assertEqual(args.document, "kernel/store.py.md")

    def test_expected_snapshot_uses_the_explicit_frozen_generation_spelling(self) -> None:
        args = build_parser().parse_args(
            [
                "memory-citations",
                "--repo",
                REPO,
                "--contract",
                "/coord/leaf.md",
                "--document",
                "kernel/store.py.md",
                "--expected-snapshot",
                "abc123",
            ]
        )

        self.assertEqual(args.expected_snapshot, "abc123")

    def test_half_scopes_are_refused_before_application_or_cli_discovery(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires --document and --expected-snapshot"):
            CitationOperationScope(document="kernel/store.py.md")
        with self.assertRaisesRegex(ValueError, "requires --document and --expected-snapshot"):
            CitationOperationScope(expected_snapshot="a" * 64)

        malformed = object.__new__(CitationOperationScope)
        object.__setattr__(malformed, "document", "kernel/store.py.md")
        object.__setattr__(malformed, "expected_snapshot", None)
        with mock.patch.object(
            memory_tools,
            "_memory_scope",
            side_effect=AssertionError("half application scope reached discovery"),
        ):
            for tool in (citation_check_tool, citation_fix_tool, citation_migrate_tool):
                with self.subTest(tool=tool.__name__), self.assertRaises(ValueError):
                    tool(
                        cast(Any, "config"),
                        repo_id=REPO,
                        contract_path="/coord/leaf.md",
                        operation_scope=malformed,
                    )

        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config=None,
            build_index=False,
            fix=False,
            migrate=False,
            dry_run=False,
            document="kernel/store.py.md",
            expected_snapshot=None,
        )
        with (
            mock.patch.object(
                memory_citations,
                "discover_config",
                side_effect=AssertionError("half CLI scope reached config discovery"),
            ),
            mock.patch.object(
                memory_citations,
                "citation_check_tool",
                side_effect=AssertionError("half CLI scope reached acquisition"),
            ),
        ):
            self.assertEqual(memory_citations.run(args), 1)

    def test_the_fix_flag_reaches_the_guarded_tool(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config="/settings.json",
            fix=True,
            migrate=False,
            dry_run=True,
            document=None,
            expected_snapshot=None,
        )
        with (
            mock.patch.object(memory_citations, "load_config", return_value="config") as loaded,
            mock.patch.object(
                memory_citations, "citation_fix_tool", return_value={"ok": True}
            ) as called,
        ):
            self.assertEqual(memory_citations.run(args), 0)

        loaded.assert_called_once_with("/settings.json")
        called.assert_called_once_with(
            "config",
            repo_id=REPO,
            contract_path="/coord/leaf.md",
            dry_run=True,
            operation_scope=CitationOperationScope(),
        )

    def test_build_index_reaches_the_contract_scoped_tool(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config="/settings.json",
            build_index=True,
            fix=False,
            migrate=False,
            dry_run=False,
            document=None,
        )
        with (
            mock.patch.object(memory_citations, "load_config", return_value="config"),
            mock.patch.object(
                memory_citations,
                "citation_source_index_build_tool",
                return_value={"ok": True},
            ) as called,
        ):
            self.assertEqual(memory_citations.run(args), 0)

        called.assert_called_once_with("config", repo_id=REPO, contract_path="/coord/leaf.md")

    def test_build_index_refuses_document_scope_without_touching_the_tool(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config="/settings.json",
            build_index=True,
            fix=False,
            migrate=False,
            dry_run=False,
            document="kernel/store.py.md",
        )
        with (
            mock.patch.object(
                memory_citations,
                "load_config",
                side_effect=AssertionError("invalid build scope reached config loading"),
            ),
            mock.patch.object(memory_citations, "citation_source_index_build_tool") as called,
        ):
            self.assertEqual(memory_citations.run(args), 1)

        called.assert_not_called()

    def test_build_index_refuses_an_expected_snapshot_without_touching_the_tool(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config="/settings.json",
            build_index=True,
            fix=False,
            migrate=False,
            dry_run=False,
            document=None,
            expected_snapshot="already-built",
        )
        with (
            mock.patch.object(
                memory_citations,
                "load_config",
                side_effect=AssertionError("invalid build scope reached config loading"),
            ),
            mock.patch.object(memory_citations, "citation_source_index_build_tool") as called,
        ):
            self.assertEqual(memory_citations.run(args), 1)

        called.assert_not_called()

    def test_without_fix_it_reports_and_exits_nonzero_when_findings_remain(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config=None,
            fix=False,
            migrate=False,
            dry_run=False,
            document="kernel/store.py.md",
            expected_snapshot="a" * 64,
        )
        with (
            mock.patch.object(memory_citations, "discover_config", return_value=Path("/s.json")),
            mock.patch.object(memory_citations, "load_config", return_value="config"),
            mock.patch.object(
                memory_citations, "citation_check_tool", return_value={"ok": False}
            ) as called,
        ):
            self.assertEqual(memory_citations.run(args), 1)

        self.assertEqual(called.call_args.kwargs["contract_path"], "/coord/leaf.md")
        self.assertEqual(
            called.call_args.kwargs["operation_scope"].document,
            "kernel/store.py.md",
        )

    def test_expected_snapshot_reaches_the_document_check_tool(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config="/settings.json",
            build_index=False,
            fix=False,
            migrate=False,
            dry_run=False,
            document="kernel/store.py.md",
            expected_snapshot="a" * 64,
        )
        with (
            mock.patch.object(memory_citations, "load_config", return_value="config"),
            mock.patch.object(
                memory_citations, "citation_check_tool", return_value={"ok": True}
            ) as called,
        ):
            self.assertEqual(memory_citations.run(args), 0)

        self.assertEqual(
            called.call_args.kwargs["operation_scope"],
            CitationOperationScope(
                document="kernel/store.py.md",
                expected_snapshot="a" * 64,
            ),
        )

    def test_a_missing_expected_generation_is_a_clean_cli_refusal(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config="/settings.json",
            build_index=False,
            fix=False,
            migrate=False,
            dry_run=False,
            document="kernel/store.py.md",
            expected_snapshot="b" * 64,
        )
        with (
            mock.patch.object(memory_citations, "load_config", return_value="config"),
            mock.patch.object(
                memory_citations,
                "citation_check_tool",
                side_effect=source_index.SourceIndexError("expected generation is not published"),
            ),
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(memory_citations.run(args), 1)

        printed.assert_called_once_with("expected generation is not published")

    def test_an_undiscoverable_settings_file_fails_loudly_rather_than_guessing(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/coord/leaf.md",
            config=None,
            fix=True,
            migrate=False,
            dry_run=False,
            document=None,
        )
        with mock.patch.object(
            memory_citations,
            "discover_config",
            side_effect=memory_citations.ConfigDiscoveryError("no trusted settings found"),
        ):
            self.assertEqual(memory_citations.run(args), 1)
