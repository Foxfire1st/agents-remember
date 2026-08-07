from __future__ import annotations

import argparse
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agents_remember.application.memory_tools import CitationOperationScope, citation_migrate_tool
from agents_remember.cli import memory_citations
from agents_remember.cli.__main__ import build_parser
from agents_remember.errors import AuthorityError
from agents_remember.memory_quality.style.citations import migration, old_form
from agents_remember.worktrees.worktree_contract import write_contract
from test_memory_citation_migration import card
from test_memory_tool_enclosure_scope import REPO, _enclosure


class OldFormTests(unittest.TestCase):
    """The reader for the format being replaced, at the edges the tree actually holds."""

    def test_a_cell_holding_no_link_and_no_backticks_names_nothing(self) -> None:
        self.assertEqual(old_form.link_targets("(task notes) see the design doc"), [])

    def test_a_backticked_bare_path_is_a_target(self) -> None:
        self.assertEqual(
            old_form.link_targets("`dashboard/public/podstage.html`"),
            ["dashboard/public/podstage.html"],
        )

    def test_several_links_in_one_cell_are_all_targets(self) -> None:
        self.assertEqual(
            old_form.link_targets("[a.py](x/a.py) · [b.py](x/b.py)"), ["x/a.py", "x/b.py"]
        )

    def test_the_four_no_citation_markers_read_as_one(self) -> None:
        for marker in ("", "-", "\u2013", "\u2014", "n/a", "N/A"):
            self.assertTrue(old_form.is_marker(marker), marker)

    def test_a_table_s_own_marker_is_preferred_to_the_fallback(self) -> None:
        self.assertEqual(old_form.marker_of(["", "—", "n/a"]), "—")
        self.assertEqual(old_form.marker_of(["[a.py](x/a.py)"]), "")

    def test_a_single_line_range_and_a_span_both_parse(self) -> None:
        self.assertEqual(old_form.old_span("L47"), old_form.Span(47, 47))
        self.assertEqual(old_form.old_span("L70-L106"), old_form.Span(70, 106))
        self.assertEqual(old_form.old_span("L70\u2013106"), old_form.Span(70, 106))
        self.assertIsNone(old_form.old_span("Source discovery checked"))

    def test_a_range_past_the_end_of_the_file_is_never_a_verified_hint(self) -> None:
        anchors = (migration.model.Anchor(kind=migration.model.SYMBOL, text="FIELD"),)

        self.assertEqual(
            old_form.verified_hint(anchors, old_form.Span(1, 1), ["FIELD"]), old_form.Span(1, 1)
        )
        self.assertIsNone(old_form.verified_hint(anchors, old_form.Span(1, 90), ["FIELD"]))
        self.assertIsNone(old_form.verified_hint(anchors, old_form.Span(0, 1), ["FIELD"]))
        self.assertIsNone(old_form.verified_hint((), old_form.Span(1, 1), ["FIELD"]))
        self.assertIsNone(old_form.verified_hint(anchors, None, ["FIELD"]))
        self.assertIsNone(old_form.verified_hint(anchors, old_form.Span(1, 1), ["other"]))

    def test_an_absolute_target_is_never_read_against_the_card_s_directory(self) -> None:
        found = old_form.path_candidates(
            "/etc/passwd", Path("/m/onboarding/x.md"), Path("/m/onboarding"), "repo"
        )

        self.assertEqual(found, ["etc/passwd"])

    def test_a_path_outside_the_onboarding_tree_yields_no_mirrored_spelling(self) -> None:
        found = old_form.path_candidates(
            "../../../elsewhere/a.py", Path("/m/onboarding/x.md"), Path("/m/onboarding"), "repo"
        )

        self.assertNotIn("elsewhere/a.py", found[:1])


class WriteGuardTests(unittest.TestCase):
    """L6-R27: the migration writes into a leaf's memory worktree or it does not write."""

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
        (self.enclosure.leaf_onboarding / "leaf_only.py.migrate.md").write_text(
            card(
                "leaf_only.py",
                "| The `VALUE` constant. | L1 | [leaf_only.py](agents-remember/leaf_only.py) |",
            ),
            encoding="utf-8",
        )

    def test_a_contract_scoped_migration_leaves_the_official_repo_alone(self) -> None:
        self.plant()
        before = self.snapshot(self.enclosure.official_onboarding)

        payload = citation_migrate_tool(
            self.enclosure.config, repo_id=REPO, contract_path=self.contract_path
        )

        self.assertEqual(payload["onboardingRoot"], self.enclosure.leaf_onboarding.as_posix())
        self.assertEqual(self.snapshot(self.enclosure.official_onboarding), before)
        self.assertEqual(payload["rowsConverted"], 1)

    def test_a_contract_naming_the_official_repo_as_its_worktree_is_refused(self) -> None:
        official = self.enclosure.contract.memory_repo_path
        assert official is not None
        pointed = self.enclosure.contract.contract_path.parent / "official-migrate.md"
        write_contract(
            pointed,
            replace(self.enclosure.contract, memory_worktree=official, contract_path=pointed),
        )

        with self.assertRaises(AuthorityError) as raised:
            citation_migrate_tool(
                self.enclosure.config, repo_id=REPO, contract_path=pointed.as_posix()
            )

        self.assertIn("refuses to write into the OFFICIAL memory repo", str(raised.exception))

    def test_a_dry_run_through_the_tool_writes_nothing(self) -> None:
        self.plant()
        before = self.snapshot(self.enclosure.leaf_onboarding)

        payload = citation_migrate_tool(
            self.enclosure.config, repo_id=REPO, contract_path=self.contract_path, dry_run=True
        )

        self.assertTrue(payload["dryRun"])
        self.assertEqual(self.snapshot(self.enclosure.leaf_onboarding), before)


class CommandLineTests(unittest.TestCase):
    """One command per mode, and no argument list that names the official memory repo."""

    def test_migrate_is_a_flag_and_defaults_off(self) -> None:
        args = build_parser().parse_args(
            ["memory-citations", "--repo", REPO, "--contract", "/c/leaf.md"]
        )

        self.assertFalse(args.migrate)

    def test_the_migrate_flag_reaches_the_guarded_tool(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/c/leaf.md",
            config="/s.json",
            fix=False,
            migrate=True,
            dry_run=True,
            document=None,
            expected_snapshot=None,
        )
        with (
            mock.patch.object(memory_citations, "load_config", return_value="config"),
            mock.patch.object(
                memory_citations, "citation_migrate_tool", return_value={"ok": True}
            ) as called,
        ):
            self.assertEqual(memory_citations.run(args), 0)

        called.assert_called_once_with(
            "config",
            repo_id=REPO,
            contract_path="/c/leaf.md",
            dry_run=True,
            operation_scope=CitationOperationScope(),
        )

    def test_fix_and_migrate_together_are_refused_rather_than_ordered(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/c/leaf.md",
            config="/s.json",
            fix=True,
            migrate=True,
            dry_run=False,
            document=None,
        )
        with mock.patch.object(memory_citations, "load_config", return_value="config"):
            self.assertEqual(memory_citations.run(args), 1)

    def test_a_migration_leaving_work_exits_nonzero(self) -> None:
        args = argparse.Namespace(
            repo=REPO,
            contract="/c/leaf.md",
            config="/s.json",
            fix=False,
            migrate=True,
            dry_run=False,
            document=None,
        )
        with (
            mock.patch.object(memory_citations, "load_config", return_value="config"),
            mock.patch.object(
                memory_citations, "citation_migrate_tool", return_value={"ok": False}
            ),
        ):
            self.assertEqual(memory_citations.run(args), 1)
