from __future__ import annotations

from pathlib import Path
from unittest import mock

from agents_remember.memory_quality.style.citations import (
    fixer,
    range_resolution,
    source_index,
)
from test_memory_citation_fix import TreeCase


class LiveCitedFileRetargetTests(TreeCase):
    """`--fix` retargets across the tree only when the cited file is GONE and continuity holds.

    Recorded from production: a claim cited two adjacent tool names in
    ``mcp/tools/base.py:50-51``, the tuple holding them was relocated, and the cited file kept
    existing without those names. The wider-tree lookup found the single DEFINITION of each
    name -- the ``@server.tool()`` registrar functions that declared them -- and rebound the
    claim to those declarations, a different fact that never supported the claim, recording
    ``No content impact``. Two independent conditions refuse that now: the cited file still
    being live, and the extent the claim was verified against not being readable at its stamp.
    Both directions are pinned on one tree shape so the rule cannot be widened into "ban
    cross-file moves".
    """

    def moved(self, *, cited_exists: bool, stamp: bool = True) -> None:
        self.tree.history()
        self.tree.source(
            "mcp/tools/base.py",
            "OTHER_TOOLS = ('a', 'b')\n"
            if cited_exists
            else "def register_tool():\n    return 0\n",
        )
        verified = self.tree.stamp() if stamp else None
        self.tree.source("mcp/registration/closeout.py", "def register_tool():\n    return 1\n")
        if not cited_exists:
            self.tree.remove_source("mcp/tools/base.py")
        self.tree.card(
            "mcp/tools/caller.py",
            "| The registration entry point. | `register_tool` | mcp/tools/base.py:1-2 |",
            stamp=verified,
        )

    def test_an_anchor_that_left_a_live_cited_file_is_declined_not_retargeted(self) -> None:
        self.moved(cited_exists=True)

        result = self.tree.fix()

        self.assertEqual(result["claimsRepaired"], 0, result["repairs"])
        declined = self.declined(result)
        self.assertEqual(declined["code"], "anchor_left_live_file")
        self.assertEqual(declined["anchor"], "`register_tool`")
        self.assertIn("mcp/tools/base.py", declined["message"])
        self.assertIn("still exists in the tree", declined["message"])
        self.assertIn("mcp/registration/closeout.py:1-2", declined["message"])

    def test_a_deleted_cited_file_without_provable_continuity_is_declined(self) -> None:
        """A deleted file is not evidence that its replacement supports the claim."""
        self.moved(cited_exists=False, stamp=False)

        result = self.tree.fix()

        self.assertEqual(result["claimsRepaired"], 0, result["repairs"])
        declined = self.declined(result)
        self.assertEqual(declined["code"], "anchor_continuity_unproven")
        self.assertIn("lastVerifiedCommitHash", declined["message"])
        self.assertIn("mcp/registration/closeout.py:1-2", declined["message"])

    def test_the_same_anchor_relocates_once_the_cited_file_is_gone(self) -> None:
        self.moved(cited_exists=False)

        result = self.tree.fix()

        self.assertEqual(result["declinedCount"], 0, result["declined"])
        self.assertEqual(self.sources(result), "mcp/registration/closeout.py:1-2")
        self.assert_check_clean()

    def test_a_mention_rebound_to_that_names_declaration_is_declined(self) -> None:
        """L32: the anchor was a MENTION in a relocated tuple, and its name still exists.

        The tuple moved as a whole, so the cited file is gone and the exact name still matches
        -- now as the DEFINITION of the registrar function that declares it. That is the
        production misbinding: the name matched, the fact did not. Provenance refuses it,
        because the extent the claim was verified against was an occurrence and the only
        tree-wide match is a definition.
        """
        self.tree.history()
        self.tree.source(
            "mcp/tools/base.py",
            "PUBLIC_TOOLS = (\n    register_tool,\n    persist,\n)\n",
        )
        stamp = self.tree.stamp()
        self.tree.source("mcp/registration/closeout.py", "def register_tool():\n    return 1\n")
        self.tree.remove_source("mcp/tools/base.py")
        self.tree.card(
            "mcp/tools/caller.py",
            "| The registration entry point. | `register_tool` | mcp/tools/base.py:1-4 |",
            stamp=stamp,
        )

        result = self.tree.fix()

        self.assertEqual(result["claimsRepaired"], 0, result["repairs"])
        declined = self.declined(result)
        self.assertEqual(declined["code"], "anchor_kind_changed")
        self.assertIn("an occurrence in mcp/tools/base.py", declined["message"])
        self.assertIn("a definition at mcp/registration/closeout.py:1-2", declined["message"])


class DocumentScopeTests(TreeCase):
    """`--document` exists because a curator wave shares one memory worktree.

    A tree-wide ``--fix`` rewrites documents anywhere in it, so one curator's run can rewrite
    another's document mid-edit -- measured on the pilot, where two of four curators avoided
    it only by dry-running first or by copying their document to a throwaway tree.
    """

    def _two_failing_cards(self) -> None:
        """Two anchors that left one cited file, each still declared where the move put it.

        ``kernel/gone.py`` held both at the stamp, so each card's relocation proves the same
        extent kind at the same commit; without that the fixer refuses rather than follows the
        name into whichever file declares it now.
        """
        self.tree.history()
        self.tree.source(
            "kernel/gone.py",
            "def build_route_indexes():\n    return 1\n\n\ndef persist():\n    return 2\n",
        )
        stamp = self.tree.stamp()
        self.tree.source("kernel/indexes.py", "def build_route_indexes():\n    return 1\n")
        self.tree.source("kernel/store.py", "def persist():\n    return 2\n")
        self.tree.remove_source("kernel/gone.py")
        self.tree.card(
            "kernel/a.py",
            "| The census. | `build_route_indexes` | kernel/gone.py:1-2 |",
            stamp=stamp,
        )
        self.tree.card(
            "kernel/b.py",
            "| The write path. | `persist` | kernel/gone.py:5-6 |",
            stamp=stamp,
        )

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


class ScopedNormalisationTests(TreeCase):
    """A curator's provisional passing range is generated away inside its one document."""

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
