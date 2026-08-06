from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.memory_quality.style.citations import (
    fixer,
    migration,
    model,
    range_resolution,
    source_index,
)
from test_memory_citation_fix import TreeCase, filler


class DocumentScopeTests(TreeCase):
    """`--document` exists because a curator wave shares one memory worktree.

    A tree-wide ``--fix`` rewrites documents anywhere in it, so one curator's run can rewrite
    another's document mid-edit -- measured on the pilot, where two of four curators avoided
    it only by dry-running first or by copying their document to a throwaway tree.
    """

    def _two_failing_cards(self) -> None:
        self.tree.source("kernel/indexes.py", "def build_route_indexes():\n    return 1\n")
        self.tree.source("kernel/store.py", "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/a.py", "| The census. | `build_route_indexes` | kernel/gone.py:1-2 |"
        )
        self.tree.card("kernel/b.py", "| The write path. | `persist` | kernel/gone.py:1-2 |")

    def test_a_scoped_fix_leaves_every_other_document_byte_identical(self) -> None:
        self._two_failing_cards()
        before = self.tree.card_text("kernel/b.py.md")
        snapshot = self.tree.source_snapshot_id()

        result = fixer.fix_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/a.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(result["claimsRepaired"], 1)
        self.assertEqual(self.tree.card_text("kernel/b.py.md"), before)
        self.assertIn("kernel/indexes.py", self.tree.row("kernel/a.py.md"))

    def test_a_name_matching_no_document_is_refused_rather_than_checking_nothing(self) -> None:
        """A filter that matches nothing and reports clean is the defect, not the fix."""
        self._two_failing_cards()
        snapshot = self.tree.source_snapshot_id()

        with self.assertRaises(ValueError) as raised:
            fixer.fix_onboarding_root(
                self.tree.onboarding,
                self.tree.code,
                only="kernel/typo.py.md",
                expected_snapshot=snapshot,
            )

        self.assertIn("names no document", str(raised.exception))

    def test_only_the_exact_work_order_spelling_matches(self) -> None:
        self._two_failing_cards()
        snapshot = self.tree.source_snapshot_id()

        with self.assertRaises(ValueError):
            fixer.fix_onboarding_root(
                self.tree.onboarding,
                self.tree.code,
                only="./kernel/a.py.md",
                expected_snapshot=snapshot,
            )

    def test_invalid_exact_paths_refuse_without_memory_discovery_or_source_acquisition(
        self,
    ) -> None:
        self._two_failing_cards()
        outside = self.tree.memory / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (self.tree.onboarding / "escape.md").symlink_to(outside)

        for selected in (
            "/absolute.md",
            "../outside.md",
            "kernel/a.py",
            "./kernel/a.py.md",
            "kernel//a.py.md",
            "kernel/missing.py.md",
            "escape.md",
        ):
            with (
                self.subTest(selected=selected),
                mock.patch.object(
                    Path,
                    "rglob",
                    side_effect=AssertionError("exact selection used memory rglob"),
                ),
                mock.patch.object(
                    source_index,
                    "open_repository_index",
                    side_effect=AssertionError("invalid document acquired source index"),
                ),
                self.assertRaises(ValueError),
            ):
                range_resolution.check_onboarding_root(
                    self.tree.onboarding,
                    self.tree.code,
                    only=selected,
                    expected_snapshot="a" * 64,
                )

    def test_the_check_scopes_to_the_same_one_document(self) -> None:
        self._two_failing_cards()
        snapshot = self.tree.source_snapshot_id()

        scoped = range_resolution.check_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/a.py.md",
            expected_snapshot=snapshot,
        )
        whole = range_resolution.check_onboarding_root(self.tree.onboarding, self.tree.code)

        self.assertEqual(scoped["filesChecked"], 1)
        self.assertEqual(whole["filesChecked"], 2)
        self.assertLess(scoped["findingCount"], whole["findingCount"])


class CoreOperationScopeTests(TreeCase):
    """The deepest exported operations enforce acquisition and leased-index authority."""

    def _invoke(
        self,
        operation: str,
        *,
        only: str | None,
        expected_snapshot: str | None,
    ) -> dict[str, Any]:
        if operation == "check":
            return range_resolution.check_onboarding_root(
                self.tree.onboarding,
                None,
                only=only,
                expected_snapshot=expected_snapshot,
            )
        if operation == "fix":
            return fixer.fix_onboarding_root(
                self.tree.onboarding,
                self.tree.code,
                only=only,
                expected_snapshot=expected_snapshot,
            )
        return migration.migrate_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only=only,
            expected_snapshot=expected_snapshot,
        )

    def test_all_six_core_half_scopes_refuse_before_any_work(self) -> None:
        half_scopes = (
            ("document_only", "one.py.md", None),
            ("snapshot_only", None, "a" * 64),
        )
        for operation in ("check", "fix", "migrate"):
            for shape, only, expected_snapshot in half_scopes:
                with (
                    self.subTest(operation=operation, shape=shape),
                    mock.patch.object(
                        model,
                        "documents_in",
                        side_effect=AssertionError("half scope reached document selection"),
                    ),
                    mock.patch.object(
                        Path,
                        "rglob",
                        side_effect=AssertionError("half scope scanned memory"),
                    ),
                    mock.patch.object(
                        source_index,
                        "open_repository_index",
                        side_effect=AssertionError("half scope opened an index"),
                    ),
                    mock.patch.object(
                        source_index,
                        "_tree_state",
                        side_effect=AssertionError("half scope inspected source"),
                    ),
                    self.assertRaisesRegex(
                        source_index.SourceIndexError,
                        "requires --document and --expected-snapshot",
                    ),
                ):
                    self._invoke(
                        operation,
                        only=only,
                        expected_snapshot=expected_snapshot,
                    )

    def test_leased_check_rejects_same_or_different_expected_identity_before_work(self) -> None:
        self.tree.source("one.py", "VALUE = 1\n")
        self.tree.card("one.py", "| The value. | `VALUE` | one.py:1-1 |")
        with source_index.open_repository_index(self.tree.trees()) as index:
            different = "f" * 64 if index.snapshot_id != "f" * 64 else "e" * 64
            for expected_snapshot in (index.snapshot_id, different):
                with (
                    self.subTest(expected_snapshot=expected_snapshot),
                    mock.patch.object(
                        model,
                        "documents_in",
                        side_effect=AssertionError("ambiguous lease reached document selection"),
                    ),
                    mock.patch.object(
                        Path,
                        "rglob",
                        side_effect=AssertionError("ambiguous lease scanned memory"),
                    ),
                    mock.patch.object(
                        source_index,
                        "open_repository_index",
                        side_effect=AssertionError("ambiguous lease opened another index"),
                    ),
                    mock.patch.object(
                        source_index,
                        "_tree_state",
                        side_effect=AssertionError("ambiguous lease inspected source"),
                    ),
                    self.assertRaisesRegex(
                        source_index.SourceIndexError,
                        "already-open.*cannot be combined",
                    ),
                ):
                    range_resolution.check_onboarding_root(
                        self.tree.onboarding,
                        None,
                        only="one.py.md",
                        index=index,
                        expected_snapshot=expected_snapshot,
                    )

            selected = range_resolution.check_onboarding_root(
                self.tree.onboarding,
                self.tree.code,
                only="one.py.md",
                index=index,
            )
            default = range_resolution.check_onboarding_root(
                self.tree.onboarding,
                self.tree.code,
                index=index,
            )

        self.assertEqual(selected["filesChecked"], 1)
        self.assertEqual(default["filesChecked"], 1)


class DuplicateCitationTests(TreeCase):
    """Exact source repetition gates within one Claim, never across separate Claims."""

    def test_a_table_claim_with_an_exact_duplicate_source_fails_the_gate(self) -> None:
        self.tree.source("kernel/store.py", "def persist():\n    return 1\n")
        self.tree.card(
            "kernel/store.py",
            "| The write path. | `persist` | kernel/store.py:1-2; kernel/store.py:1-2 |",
        )

        result = self.tree.check()

        self.assertFalse(result["ok"])
        self.assertEqual(
            [finding["code"] for finding in result["findings"]],
            ["citation_source_duplicate"],
        )
        self.assertIn("kernel/store.py:1-2", result["findings"][0]["message"])
        self.assertIn("2 times", result["findings"][0]["message"])

    def test_two_prose_claims_may_cite_the_same_source_once_each(self) -> None:
        self.tree.source("kernel/store.py", "def persist():\n    return 1\n")
        self.tree.card(
            "kernel/store.py",
            "| Nothing here. | — | — |",
            "",
            "The first clause cit:([`persist`], kernel/store.py:1-2) and the second clause "
            "cit:([`persist`], kernel/store.py:1-2) are separate claims.",
        )

        result = self.tree.check()

        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["proseCitations"], 2)

    def test_tree_wide_fix_deduplicates_local_and_dependency_claims_only(self) -> None:
        self.tree.source("kernel/store.py", "def persist():\n    return 1\n")
        self.tree.card(
            "kernel/store.py",
            "| Local duplicate. | `persist` | kernel/store.py:1-2; kernel/store.py:1-2 |",
            '| Dependency duplicate. | "You must pass the application" | '
            "uvicorn/main.py:604-607; uvicorn/main.py:604-607 |",
            "| Passing provisional range. | `persist` | kernel/store.py:1-1 |",
        )

        first = self.tree.fix()

        self.assertEqual(first["failingClaims"], 2)
        self.assertEqual(first["claimsRepaired"], 2)
        self.assertEqual(first["claimsNormalised"], 0)
        self.assertEqual(
            self.tree.row("kernel/store.py.md"),
            "| Local duplicate. | `persist` | kernel/store.py:1-2 |",
        )
        self.assertEqual(
            self.tree.row("kernel/store.py.md", 1),
            '| Dependency duplicate. | "You must pass the application" | uvicorn/main.py:604-607 |',
        )
        self.assertIn("kernel/store.py:1-1", self.tree.row("kernel/store.py.md", 2))
        self.assertTrue(first["ok"], first)
        after_first = self.tree.card_text("kernel/store.py.md")

        second = self.tree.fix()

        self.assertEqual(second["documentsWritten"], 0)
        self.assertTrue(second["ok"], second)
        self.assertEqual(self.tree.card_text("kernel/store.py.md"), after_first)


class ScopedNormalisationTests(TreeCase):
    """A curator's provisional passing range is generated away inside its one document."""

    def test_a_passing_single_line_provisional_range_expands_to_the_declaration(self) -> None:
        """Normalise a contained provisional range to the declaration extent."""
        self.tree.source("kernel/store.py", filler(4) + "def persist():\n    return 2\n")
        self.tree.card("kernel/store.py", "| The write path. | `persist` | kernel/store.py:5-5 |")
        snapshot = self.tree.source_snapshot_id()

        result = fixer.fix_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/store.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(result["failingClaims"], 0)
        self.assertEqual(result["claimsRepaired"], 0)
        self.assertEqual(result["claimsNormalised"], 1)
        self.assertEqual(result["repairs"][0]["kind"], "normalise")
        self.assertIn("kernel/store.py:5-6", self.tree.row("kernel/store.py.md"))

    def test_a_verified_block_is_preserved_when_generation_finds_only_a_mention(self) -> None:
        """Keep a verified block when generation finds only a mention."""
        body = ["filler"] * 100
        body[9] = "used = FIELD"
        self.tree.source("kernel/index.py", "\n".join(body) + "\n")
        self.tree.card("kernel/index.py", "| The `FIELD` read. | `FIELD` | kernel/index.py:5-20 |")
        before = self.tree.card_text("kernel/index.py.md")
        snapshot = self.tree.source_snapshot_id()

        result = fixer.fix_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/index.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(result["claimsNormalised"], 0)
        self.assertEqual(result["documentsWritten"], 0)
        self.assertEqual(self.tree.card_text("kernel/index.py.md"), before)

    def test_a_multi_source_claim_preserves_each_verified_mention_block(self) -> None:
        """Preservation is per citation; a second Source must not disable the 87-row rule."""
        body = ["filler"] * 100
        body[9] = "used = FIELD"
        self.tree.source("kernel/index.py", "\n".join(body) + "\n")
        self.tree.source("kernel/context.py", "\n".join(["context"] * 100) + "\n")
        self.tree.card(
            "kernel/index.py",
            "| The FIELD-bearing block also depends on context. | `FIELD` | "
            "kernel/index.py:5-20; kernel/context.py:25-40 |",
        )
        before = self.tree.card_text("kernel/index.py.md")
        snapshot = self.tree.source_snapshot_id()

        result = fixer.fix_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/index.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(result["claimsNormalised"], 0)
        self.assertEqual(result["declinedCount"], 0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.tree.card_text("kernel/index.py.md"), before)

    def test_expanded_sources_are_deduplicated_and_the_second_run_is_byte_identical(self) -> None:
        self.tree.source(
            "kernel/store.py",
            "def persist():\n    return 1\n\n\ndef reload():\n    return 2\n",
        )
        self.tree.card(
            "kernel/store.py",
            "| The two operations. | `persist`; `reload` | "
            "kernel/store.py:1-6; kernel/store.py:1-6 |",
        )
        snapshot = self.tree.source_snapshot_id()

        first = fixer.fix_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/store.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(self.sources(first), "kernel/store.py:1-2; kernel/store.py:5-6")
        self.assertEqual(first["claimsRepaired"], 1)
        self.assertEqual(first["claimsNormalised"], 0)
        self.assertTrue(first["ok"], first)
        after_first = self.tree.card_text("kernel/store.py.md")

        second = fixer.fix_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/store.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(second["claimsNormalised"], 0)
        self.assertEqual(second["documentsWritten"], 0)
        self.assertTrue(second["ok"], second)
        self.assertEqual(self.tree.card_text("kernel/store.py.md"), after_first)

    def test_a_malformed_source_segment_blocks_normalisation_without_deleting_evidence(
        self,
    ) -> None:
        """A formatter may not make a malformed dependency pointer disappear to become green."""
        self.tree.source("kernel/store.py", "a=1\nb=2\nc=3\nd=4\ndef persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py",
            "| Local behavior plus external evidence. | `persist` | "
            "kernel/store.py:5-5; [dependency](https://example.test/source.py#L10) |",
        )
        before = self.tree.card_text("kernel/store.py.md")
        snapshot = self.tree.source_snapshot_id()

        result = fixer.fix_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/store.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(result["claimsNormalised"], 0)
        self.assertEqual(result["documentsWritten"], 0)
        self.assertEqual(result["findingsRemaining"], 1)
        self.assertFalse(result["ok"])
        self.assertEqual(self.tree.card_text("kernel/store.py.md"), before)
        self.assertEqual(
            [one["code"] for one in self.tree.check()["findings"]],
            ["citation_source_malformed"],
        )

    def test_tree_wide_fix_still_does_not_normalise_a_passing_provisional_range(self) -> None:
        self.tree.source("kernel/store.py", filler(4) + "def persist():\n    return 2\n")
        self.tree.card("kernel/store.py", "| The write path. | `persist` | kernel/store.py:5-5 |")

        result = self.tree.fix()

        self.assertEqual(result["claimsNormalised"], 0)
        self.assertIn("kernel/store.py:5-5", self.tree.row("kernel/store.py.md"))


class ProseSerialisationTests(TreeCase):
    """`cit:` in running text shares every rule, and says so when it cannot be rewritten."""

    def test_a_cit_construct_is_rewritten_in_place(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py",
            "| Nothing here. | — | — |",
            "",
            "The write path cit:([`persist`], kernel/store.py:12-12) is the only one.",
        )

        result = self.tree.fix()

        self.assertEqual(self.sources(result), "kernel/store.py:11-12")
        self.assertIn(
            "cit:([`persist`], kernel/store.py:11-12) is the only one.",
            self.tree.card_text("kernel/store.py.md"),
        )

    def test_a_wrapped_construct_is_counted_rather_than_silently_skipped(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py",
            "| Nothing here. | — | — |",
            "",
            "The write path cit:([`persist`],",
            "kernel/store.py:12-12) wraps.",
        )

        result = self.tree.fix()

        self.assertEqual(result["claimsNotOnOneLine"], 1)
        self.assertEqual(result["claimsRepaired"], 0)

    def test_a_cit_that_closes_but_does_not_parse_is_left_for_the_check_to_report(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py",
            "| Nothing here. | — | — |",
            "",
            "The write path cit:([`persist`] kernel/store.py:12-12) drops its comma.",
        )

        result = self.tree.fix()

        self.assertEqual(result["failingClaims"], 0)
        self.assertEqual(
            [one["code"] for one in self.tree.check()["findings"]], ["citation_prose_malformed"]
        )

    def test_a_cit_inside_a_code_span_is_a_quotation_and_is_not_rewritten(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py",
            "| Nothing here. | — | — |",
            "",
            "The grammar is `cit:([`sym`], path:1-2)` and nothing else.",
        )

        self.assertEqual(self.tree.fix()["failingClaims"], 0)


class TreeShapeTests(TreeCase):
    """Shapes the memory tree really holds that the rewriter must walk past."""

    def test_a_table_still_in_the_superseded_shape_is_reported_never_rewritten(self) -> None:
        self.tree.source("kernel/store.py", filler(4))
        self.tree.write(
            self.tree.onboarding,
            "kernel/store.py.md",
            "# card\n\n| Finding | Citations | Source Path |\n| --- | --- | --- |\n"
            "| Old. | L1-L4 | [store.py](store.py) |\n",
        )

        result = self.tree.fix()

        self.assertEqual(result["failingClaims"], 0)
        self.assertEqual(
            [one["code"] for one in self.tree.check()["findings"]],
            ["citation_table_columns_wrong"],
        )

    def test_a_directory_named_like_a_document_is_not_read_as_one(self) -> None:
        (self.tree.onboarding / "kernel.md").mkdir(parents=True)

        self.assertEqual(self.tree.fix()["documentsScanned"], 0)

    def test_a_move_in_a_language_that_is_not_parsed_still_resolves_uniquely(self) -> None:
        """No grammar, so uniqueness falls back to "mentioned in exactly one file"."""
        self.tree.source("scripts/deploy.sh", filler(3, marker="step") + "rail_row=1\n")
        self.tree.card("dashboard/panel.tsx", "| The row. | `rail_row` | scripts/gone.sh:1-2 |")

        self.assertEqual(self.sources(self.tree.fix()), "scripts/deploy.sh:4-4")


class FindingEnrichmentTests(TreeCase):
    """L6-R28: the CHECK names every location in the tree, not only the row's own files."""

    def test_an_absent_anchor_finding_names_where_the_anchor_really_is(self) -> None:
        self.tree.source("kernel/store.py", filler(4))
        self.tree.source("kernel/indexes.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card(
            "kernel/caller.py",
            "| The census. | `build_route_indexes` | kernel/store.py:1-4 |",
        )

        message = self.tree.check()["findings"][0]["message"]

        self.assertIn("In the tree, 1 file(s) hold it", message)
        self.assertIn("kernel/indexes.py:1-2", message)

    def test_an_anchor_that_exists_nowhere_says_so_in_the_finding(self) -> None:
        self.tree.source("kernel/store.py", filler(4))
        self.tree.card("kernel/caller.py", "| Gone. | `stop_turn` | kernel/store.py:1-4 |")

        self.assertIn(
            "exists NOWHERE in the code tree", self.tree.check()["findings"][0]["message"]
        )

    def test_a_vanished_source_is_reported_and_a_dependency_source_is_not(self) -> None:
        self.tree.source("kernel/store.py", filler(4))
        self.tree.card(
            "kernel/caller.py",
            "| Moved. | `line1` | kernel/gone.py:1-2 |",
            '| Third party. | "no-op" | uvicorn/main.py:604-607 |',
        )

        codes = [one["code"] for one in self.tree.check()["findings"]]

        self.assertEqual(codes, ["citation_source_vanished"])
