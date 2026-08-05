"""L6-R27: converting a memory tree from the superseded citation format to the anchored one.

Four things this has to get right, and each has cost something already:

    SHAPE      header, delimiter and every body row in ONE write. Two of the three leaves a
               construct GFM does not read as a table, at which point every citation finding
               in the document disappears and the tree looks migrated.
    HONESTY    a row it cannot convert keeps its own evidence verbatim and stays a finding.
               It is never marked done, never guessed at, and never deleted.
    RANGES     generated from the anchor, never carried across from the old cell. The old
               range votes on WHICH occurrence only after the anchor is proven inside it.
    REPEATS    a second pass over a converted tree writes nothing at all.

:class:`NoInventedFactTests` is the one that would fail loudest if somebody made this pass
"try harder": every case in it is a row whose anchor a reader must choose, and the required
outcome is a work order rather than a conversion.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.memory_tools import (
    CitationOperationScope,
    citation_migrate_tool,
)
from agents_remember.cli import memory_citations
from agents_remember.cli.__main__ import build_parser
from agents_remember.errors import AuthorityError
from agents_remember.memory_quality.style.citations import (
    drafts,
    migration,
    old_form,
    range_resolution,
    repair,
    source_index,
    work_order,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.worktrees.worktree_contract import write_contract
from test_memory_tool_enclosure_scope import REPO, _enclosure

CARD = (
    "# {path}",
    "",
    "| Field | Value |",
    "| --- | --- |",
    "| repository | agents-remember |",
    "| path | `{path}` |",
    "",
    "## Repo-Internal References",
    "",
)
HEADER = ("| Finding | Citations | Source Path |", "| --- | --- | --- |")
FIRST_ROW = len(CARD) + len(HEADER) + 1


def card(path: str, *body: str, header: tuple[str, ...] = HEADER) -> str:
    return "\n".join([*(one.format(path=path) for one in CARD), *header, *body, ""])


def link(path: str) -> str:
    """A Source Path cell in the superseded spelling, repo-name prefixed as the tree writes it."""
    return f"[{path.rsplit('/', maxsplit=1)[-1]}](agents-remember/{path})"


class Tree:
    """A memory repository and the code repository it documents, both on disk."""

    def __init__(self, root: Path) -> None:
        self.code = root / "code"
        self.memory = root / "memory"
        self.onboarding = self.memory / "onboarding"
        self.code.mkdir(parents=True)
        self.onboarding.mkdir(parents=True)

    def write(self, base: Path, relative: str, body: str) -> Path:
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def source(self, relative: str, body: str) -> Path:
        return self.write(self.code, relative, body)

    def card(self, path: str, *body: str, at: str | None = None, **kwargs: Any) -> Path:
        return self.write(self.onboarding, at or f"{path}.md", card(path, *body, **kwargs))

    def migrate(self, *, dry_run: bool = False) -> dict[str, Any]:
        return migration.migrate_onboarding_root(self.onboarding, self.code, dry_run=dry_run)

    def check(self) -> dict[str, Any]:
        return range_resolution.check_onboarding_root(self.onboarding, self.code)

    def source_snapshot_id(self) -> str:
        with source_index.open_repository_index(
            Trees(code_root=self.code, memory_root=self.memory)
        ) as index:
            return index.snapshot_id

    def text(self, relative: str) -> str:
        return (self.onboarding / relative).read_text(encoding="utf-8")

    def line(self, relative: str, offset: int = 0) -> str:
        return self.text(relative).splitlines()[FIRST_ROW - 1 + offset]

    def snapshot(self) -> dict[str, str]:
        return {
            path.relative_to(self.onboarding).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(self.onboarding.rglob("*.md"))
        }


class TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Tree(Path(self._tmp.name))

    def declined(self, result: dict[str, Any], index: int = 0) -> dict[str, Any]:
        self.assertTrue(result["workOrders"], result)
        return dict(result["workOrders"][0]["items"][index])

    def only_code(self, result: dict[str, Any]) -> str:
        return str(self.declined(result)["code"])


class TableShapeTests(TreeCase):
    """The header, the delimiter and the rows move together or the table stops being one."""

    def setUp(self) -> None:
        super().setUp()
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")

    def test_all_three_edits_land_in_one_write(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            f"| The `build_route_indexes` census freezes membership. | L1-L2 "
            f"| {link('kernel/index.py')} |",
        )

        self.tree.migrate()

        self.assertEqual(self.tree.line("kernel/caller.py.md", -2), migration.HEADER_ROW)
        self.assertEqual(self.tree.line("kernel/caller.py.md", -1), migration.DELIMITER_ROW)
        self.assertEqual(
            self.tree.line("kernel/caller.py.md"),
            "| The `build_route_indexes` census freezes membership. "
            "| `build_route_indexes` | kernel/index.py:1-2 |",
        )

    def test_the_rewritten_table_is_still_a_table_gfm_can_read(self) -> None:
        """The failure mode this whole write ordering exists for, asserted rather than assumed.

        A three-cell header over a two-cell delimiter is not a table, and the citation check
        then reports nothing at all -- so a passing check would mean the format was gone
        rather than fixed.
        """
        self.tree.card(
            "kernel/caller.py",
            f"| The census. | L1-L2 | {link('kernel/index.py')} |",
            "| A second claim. | n/a | n/a |",
        )

        self.tree.migrate()

        found = range_resolution.cells.citation_tables(
            self.tree.text("kernel/caller.py.md").split("\n")
        )
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].conforming)
        self.assertEqual(found[0].row_count, 2)

    def test_a_two_column_table_gains_the_anchor_column_too(self) -> None:
        """397 tables in this tree never had a Citations column at all."""
        self.tree.card(
            "kernel/caller.py",
            f"| The `build_route_indexes` census. | {link('kernel/index.py')} |",
            header=("| Finding | Source Path |", "| --- | --- |"),
        )

        self.tree.migrate()

        self.assertEqual(self.tree.line("kernel/caller.py.md", -2), migration.HEADER_ROW)
        self.assertEqual(
            self.tree.line("kernel/caller.py.md"),
            "| The `build_route_indexes` census. | `build_route_indexes` | kernel/index.py:1-2 |",
        )

    def test_a_short_row_is_padded_to_the_new_width_with_the_table_s_own_marker(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            "| Nothing to cite here. | — | — |",
            f"| The census. | L1-L2 | {link('kernel/index.py')} |",
        )

        result = self.tree.migrate()

        self.assertEqual(self.tree.line("kernel/caller.py.md"), "| Nothing to cite here. | — | — |")
        self.assertEqual(result["placeholderRowsPadded"], 1)

    def test_a_table_with_no_marker_of_its_own_uses_the_documented_fallback(self) -> None:
        self.tree.card("kernel/caller.py", "| Nothing to cite. |  |  |")

        self.tree.migrate()

        self.assertEqual(
            self.tree.line("kernel/caller.py.md"),
            f"| Nothing to cite. | {migration.FALLBACK_MARKER} | {migration.FALLBACK_MARKER} |",
        )

    def test_a_table_that_is_already_converted_is_not_read_again(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            "| The census. | `build_route_indexes` | kernel/index.py:1-2 |",
            header=(migration.HEADER_ROW, migration.DELIMITER_ROW),
        )

        result = self.tree.migrate()

        self.assertEqual(result["supersededTables"], 0)
        self.assertEqual(result["documentsWritten"], 0)


class SourcePathTests(TreeCase):
    """Four live spellings of a markdown link, all resolved rather than rewritten."""

    def setUp(self) -> None:
        super().setUp()
        self.tree.source("dashboard/src/data/store.ts", "export const ACTIVE = 1;\n")

    def migrate_with(self, cell: str, at: str | None = None) -> dict[str, Any]:
        self.tree.card(
            "dashboard/src/data/store.ts", f"| The `ACTIVE` store. | L1 | {cell} |", at=at
        )
        return self.tree.migrate()

    def test_a_repo_name_prefixed_link_loses_the_prefix(self) -> None:
        self.migrate_with("[store.ts](agents-remember/dashboard/src/data/store.ts)")

        self.assertIn(
            "dashboard/src/data/store.ts:1-1", self.tree.line("dashboard/src/data/store.ts.md")
        )

    def test_a_bare_repo_relative_link_is_taken_as_written(self) -> None:
        self.migrate_with("[store.ts](dashboard/src/data/store.ts)")

        self.assertIn(
            "dashboard/src/data/store.ts:1-1", self.tree.line("dashboard/src/data/store.ts.md")
        )

    def test_a_link_climbing_out_of_the_card_s_own_directory_is_read_against_the_mirror(
        self,
    ) -> None:
        """927 links in this tree are relative to where the CARD sits, not to the repo root."""
        self.migrate_with("[store.ts](../data/store.ts)", at="dashboard/src/panels/pane.tsx.md")

        self.assertIn(
            "dashboard/src/data/store.ts:1-1", self.tree.line("dashboard/src/panels/pane.tsx.md")
        )

    def test_a_link_naming_the_sidecar_resolves_to_the_file_it_documents(self) -> None:
        self.migrate_with("[store.ts](agents-remember/dashboard/src/data/store.ts.md)")

        self.assertIn(
            "dashboard/src/data/store.ts:1-1", self.tree.line("dashboard/src/data/store.ts.md")
        )

    def test_a_link_to_a_sidecar_resolves_to_the_code_it_documents_not_the_card(self) -> None:
        """The memory tree holds ``store.ts.md`` and the code tree holds ``store.ts``, so the
        unstripped spelling resolves -- to a card whose own prose holds every anchor the
        claim names, which is how a citation ends up pointing at the document citing it."""
        self.tree.write(
            self.tree.onboarding,
            "dashboard/src/data/store.ts.md",
            card(
                "dashboard/src/data/store.ts",
                "| The `ACTIVE` store. | L1 | [store.ts](store.ts.md) |",
            ),
        )

        self.tree.migrate()

        row = self.tree.line("dashboard/src/data/store.ts.md")
        self.assertIn("dashboard/src/data/store.ts:1-1", row)
        self.assertNotIn("onboarding/", row)

    def test_a_memory_document_with_no_code_counterpart_is_still_cited_as_itself(self) -> None:
        self.tree.write(
            self.tree.onboarding, "notes/overview.md", "# Overview\n\nThe `ACTIVE` rule.\n"
        )
        self.tree.card(
            "dashboard/src/data/store.ts",
            "| The `ACTIVE` rule. | L3 | [overview](../../../notes/overview.md) |",
        )

        self.tree.migrate()

        self.assertIn(
            "onboarding/notes/overview.md:3-3", self.tree.line("dashboard/src/data/store.ts.md")
        )

    def test_a_url_is_declined_because_the_format_cannot_express_one(self) -> None:
        """A URL claims to be verifiable and is not: nothing offline can check it, and the
        gate must never fetch. The work order names the two honest destinations."""
        result = self.migrate_with("[Pydantic](https://docs.pydantic.dev/latest/)")

        item = self.declined(result)
        self.assertEqual(item["code"], drafts.SOURCE_NOT_A_PATH)
        self.assertEqual(item["tier"], work_order.CURATOR_TIER)
        self.assertIn("pyproject.toml", item["action"])
        self.assertIn("A URL IS NOT EVIDENCE", item["action"])

    def test_a_path_that_names_no_file_in_either_tree_is_declined_not_invented(self) -> None:
        result = self.migrate_with("[gone.ts](agents-remember/dashboard/src/data/gone.ts)")

        self.assertEqual(self.only_code(result), drafts.SOURCE_UNRESOLVABLE)

    def test_a_range_with_no_file_at_all_is_declined(self) -> None:
        self.tree.card("dashboard/src/data/store.ts", "| The `ACTIVE` store. | L1-L4 | n/a |")

        result = self.tree.migrate()

        self.assertEqual(self.only_code(result), drafts.SOURCE_MISSING)


class AnchorSelectionTests(TreeCase):
    """Where an anchor comes from, and the exact point at which choosing one is a judgement."""

    def setUp(self) -> None:
        super().setUp()
        self.tree.source(
            "kernel/index.py",
            "FIELD = 1\n\n\ndef build_route_indexes():\n    return FIELD\n",
        )

    def test_an_anchor_written_beside_its_own_range_is_the_author_s_pairing(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            f"| Several things happen here to `FIELD` and `build_route_indexes`. "
            f"| `FIELD` L1 | {link('kernel/index.py')} |",
        )

        self.tree.migrate()

        self.assertEqual(
            self.tree.line("kernel/caller.py.md"),
            "| Several things happen here to `FIELD` and `build_route_indexes`. "
            "| `FIELD` | kernel/index.py:1-1 |",
        )

    def test_one_candidate_in_the_finding_is_promoted_because_there_is_no_choice(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            f"| The census in `build_route_indexes`. | L1 | {link('kernel/index.py')} |",
        )

        self.tree.migrate()

        self.assertIn(
            "`build_route_indexes` | kernel/index.py:4-5", self.tree.line("kernel/caller.py.md")
        )

    def test_a_heading_anchor_and_a_quoted_literal_are_both_carried(self) -> None:
        self.tree.source("docs/rules.md", "# Top\n\n## Scoping\n\nPath rules scope by prefix.\n")
        self.tree.card(
            "docs/rules.md",
            f"| The section. | `## Scoping` | {link('docs/rules.md')} |",
            f'| The sentence. | "Path rules scope by prefix." | {link("docs/rules.md")} |',
        )

        self.tree.migrate()

        self.assertIn("`## Scoping` | docs/rules.md:3-5", self.tree.line("docs/rules.md.md"))
        self.assertIn(
            '"Path rules scope by prefix." | docs/rules.md:5-5',
            self.tree.line("docs/rules.md.md", 1),
        )


class NoInventedFactTests(TreeCase):
    """Every case here is a row a reader must decide. The required outcome is a work order."""

    def setUp(self) -> None:
        super().setUp()
        self.tree.source(
            "kernel/index.py", "FIELD = 1\n\n\ndef build_route_indexes():\n    return FIELD\n"
        )

    def test_two_candidates_in_the_finding_are_never_picked_between(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            f"| `FIELD` is read by `build_route_indexes`. | L1 | {link('kernel/index.py')} |",
        )

        result = self.tree.migrate()

        item = self.declined(result)
        self.assertEqual(item["code"], drafts.ANCHOR_CHOICE_NEEDED)
        self.assertIn("`FIELD`", item["message"])
        self.assertIn("`build_route_indexes`", item["message"])
        self.assertEqual(result["rowsConverted"], 0)

    def test_a_row_naming_no_anchor_at_all_goes_to_the_curator(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            f"| The browser client for these endpoints. | L1 | {link('kernel/index.py')} |",
        )

        self.assertEqual(self.only_code(self.tree.migrate()), drafts.ANCHOR_ABSENT_FROM_ROW)

    def test_a_backticked_span_that_is_not_identifier_shaped_is_not_an_anchor(self) -> None:
        """``--check`` and ``package_data/dashboard`` are live in this tree and neither is one."""
        self.tree.card(
            "kernel/caller.py", f"| The `--check` flag is gone. | L1 | {link('kernel/index.py')} |"
        )

        self.assertEqual(self.only_code(self.tree.migrate()), drafts.ANCHOR_ABSENT_FROM_ROW)

    def test_an_anchor_occurring_twice_is_not_picked_by_position(self) -> None:
        self.tree.source("kernel/twice.py", "FIELD = 1\n\n\ndef other():\n    FIELD = 2\n")
        self.tree.card(
            "kernel/twice.py", f"| The `FIELD` constant. | n/a | {link('kernel/twice.py')} |"
        )

        self.assertEqual(self.only_code(self.tree.migrate()), repair.ANCHOR_AMBIGUOUS)

    def test_a_note_in_the_citations_cell_is_not_silently_dropped(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            f"| The `build_route_indexes` census. | Source discovery checked | {link('kernel/index.py')} |",
        )

        self.assertEqual(self.only_code(self.tree.migrate()), drafts.CITATIONS_NOTE_DROPPED)

    def test_there_is_no_similarity_matching(self) -> None:
        """The recorded pair: one maps and the other validates, and no distance may join them."""
        self.tree.source("kernel/lifecycle.py", "def _require_command_lifecycle():\n    return 1\n")
        self.tree.card(
            "kernel/caller.py",
            "| The `_map_command_lifecycle` hook. | n/a | [gone.py](agents-remember/kernel/gone.py) |",
        )

        item = self.declined(self.tree.migrate())

        self.assertEqual(item["code"], drafts.SOURCE_UNRESOLVABLE)
        self.assertNotIn("_require_command_lifecycle", item["message"])

    def test_an_anchor_that_exists_nowhere_is_the_developer_s_tier(self) -> None:
        self.tree.card(
            "kernel/caller.py", f"| The `vanished_thing` hook. | n/a | {link('kernel/index.py')} |"
        )

        item = self.declined(self.tree.migrate())

        self.assertEqual(item["code"], repair.ANCHOR_ABSENT)
        self.assertEqual(item["tier"], work_order.DEVELOPER_TIER)

    def test_a_declined_row_keeps_its_own_evidence_and_stays_a_finding(self) -> None:
        self.tree.card(
            "kernel/caller.py", f"| The browser client. | L1 | {link('kernel/index.py')} |"
        )

        self.tree.migrate()

        row = self.tree.line("kernel/caller.py.md")
        self.assertIn(link("kernel/index.py"), row)
        self.assertEqual(
            [one["code"] for one in self.tree.check()["findings"]],
            ["citation_anchor_missing", "citation_source_malformed"],
        )

    def test_a_declined_row_citing_no_file_keeps_the_range_it_had(self) -> None:
        self.tree.card("kernel/caller.py", "| The browser client. | L1-L4 | n/a |")

        self.tree.migrate()

        self.assertIn("| L1-L4 |", self.tree.line("kernel/caller.py.md"))

    def test_nothing_is_ever_deleted(self) -> None:
        self.tree.card(
            "kernel/caller.py",
            "| One. | L1 | n/a |",
            "| Two. | n/a | n/a |",
            f"| Three. | L1 | {link('kernel/index.py')} |",
        )

        result = self.tree.migrate()

        self.assertEqual(
            len(self.tree.text("kernel/caller.py.md").strip().splitlines()), FIRST_ROW + 2
        )
        self.assertEqual(
            result["rowsConverted"]
            + result["rowsKeepingOldEvidence"]
            + result["placeholderRowsPadded"],
            result["supersededRows"],
        )


class RangeProvenanceTests(TreeCase):
    """The range is generated. The old one votes only once it has been proven correct."""

    def test_a_wrong_old_range_is_not_carried_across(self) -> None:
        self.tree.source(
            "kernel/index.py", "x = 0\n" * 40 + "def build_route_indexes():\n    return 1\n"
        )
        self.tree.card(
            "kernel/caller.py",
            f"| The `build_route_indexes` census. | L1-L9 | {link('kernel/index.py')} |",
        )

        self.tree.migrate()

        self.assertIn("kernel/index.py:41-42", self.tree.line("kernel/caller.py.md"))

    def test_a_verified_old_range_picks_between_two_declarations(self) -> None:
        body = ["filler"] * 40
        body[2] = "FIELD = 1"
        body[11] = "FIELD = 2"
        self.tree.source("kernel/index.py", "\n".join(body) + "\n")
        self.tree.card(
            "kernel/index.py", f"| The `FIELD` constant. | L10-L14 | {link('kernel/index.py')} |"
        )

        self.tree.migrate()

        self.assertIn("kernel/index.py:12-12", self.tree.line("kernel/index.py.md"))

    def test_an_unverified_old_range_gets_no_vote(self) -> None:
        """The anchor is not inside the cited range, so the range says nothing about which
        occurrence was meant and the row goes to a curator instead."""
        self.tree.source("kernel/notes.md", "FIELD\n" + "filler\n" * 8 + "FIELD\n")
        self.tree.card(
            "kernel/notes.md", f"| The `FIELD` note. | L4-L6 | {link('kernel/notes.md')} |"
        )

        self.assertEqual(self.only_code(self.tree.migrate()), repair.ANCHOR_AMBIGUOUS)

    def test_a_declaration_extent_beats_the_verified_span_it_sits_inside(self) -> None:
        """Case 1: the construct the claim names is a tighter quotation than the span."""
        body = ["filler"] * 100
        body[9] = "FIELD = 1"
        self.tree.source("kernel/index.py", "\n".join(body) + "\n")
        self.tree.card(
            "kernel/index.py", f"| The `FIELD` constant. | L5-L20 | {link('kernel/index.py')} |"
        )

        self.tree.migrate()

        self.assertIn("kernel/index.py:10-10", self.tree.line("kernel/index.py.md"))

    def test_a_verified_block_is_kept_when_the_anchor_is_only_mentioned(self) -> None:
        """Case 2: the claim is about the block, not about the line the name appears on."""
        body = ["filler"] * 100
        body[9] = "used = FIELD"
        self.tree.source("kernel/index.py", "\n".join(body) + "\n")
        self.tree.card(
            "kernel/index.py", f"| The `FIELD` read. | L5-L20 | {link('kernel/index.py')} |"
        )

        self.tree.migrate()

        self.assertIn("kernel/index.py:5-20", self.tree.line("kernel/index.py.md"))

    def test_a_span_covering_most_of_the_file_is_an_anchor_defect_not_a_range_one(self) -> None:
        """Case 3: a mention-anchored quotation of most of a file names the wrong subject."""
        body = ["filler"] * 20
        body[9] = "used = FIELD"
        self.tree.source("kernel/index.py", "\n".join(body) + "\n")
        self.tree.card(
            "kernel/index.py", f"| The `FIELD` read. | L1-L15 | {link('kernel/index.py')} |"
        )

        result = self.tree.migrate()

        item = self.declined(result)
        self.assertEqual(item["code"], drafts.ANCHOR_NOT_THE_SUBJECT)
        self.assertEqual(item["tier"], work_order.CURATOR_TIER)
        self.assertTrue(item["parserDependent"])
        self.assertIn("Pick a better anchor", item["action"])
        self.assertIn(link("kernel/index.py"), self.tree.line("kernel/index.py.md"))

    def test_the_share_of_a_file_that_stops_being_a_quotation_is_one_number(self) -> None:
        """A judgement, so it is one constant with its reasoning beside it, not a rule
        rediscovered per call site."""
        self.assertEqual(migration.VERIFIED_SPAN_FILE_SHARE, 0.5)

    def test_a_generated_range_wider_than_its_verified_span_is_left_alone(self) -> None:
        body = ["filler"] * 40
        body[9] = body[10] = "used = FIELD"
        self.tree.source("kernel/index.py", "\n".join(body) + "\n")
        self.tree.card(
            "kernel/index.py", f"| The `FIELD` read. | L10-L11 | {link('kernel/index.py')} |"
        )

        self.tree.migrate()

        self.assertIn("kernel/index.py:10-11", self.tree.line("kernel/index.py.md"))

    def test_a_pure_move_is_repointed_and_the_dead_path_dropped(self) -> None:
        self.tree.source("kernel/moved.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card(
            "kernel/caller.py",
            "| The `build_route_indexes` census. | L1-L2 "
            "| [old.py](agents-remember/kernel/old.py) |",
        )

        result = self.tree.migrate()

        self.assertEqual(self.only_code(result), drafts.SOURCE_UNRESOLVABLE)

    def test_a_row_citing_a_file_that_holds_none_of_its_anchors_is_declined(self) -> None:
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")
        self.tree.source("kernel/other.py", "def unrelated():\n    return 2\n")
        self.tree.card(
            "kernel/caller.py",
            f"| The `build_route_indexes` census. | n/a | {link('kernel/index.py')} · {link('kernel/other.py')} |",
        )

        self.assertEqual(self.only_code(self.tree.migrate()), drafts.SOURCE_HOLDS_NO_ANCHOR)


class ProseMigrationTests(TreeCase):
    """``X (L47)`` becomes ``cit:([X], path:start-end)``, on one line, path always explicit."""

    def setUp(self) -> None:
        super().setUp()
        self.tree.source(
            "kernel/index.py", "FIELD = 1\n\n\ndef build_route_indexes():\n    return FIELD\n"
        )

    def prose(self, *body: str, path: str = "kernel/index.py") -> str:
        self.tree.card(path, "| Nothing. | n/a | n/a |", at="card.md")
        text = (self.tree.onboarding / "card.md").read_text(encoding="utf-8")
        (self.tree.onboarding / "card.md").write_text(
            text + "\n".join(body) + "\n", encoding="utf-8"
        )
        self.tree.migrate()
        return self.tree.text("card.md")

    def test_the_dominant_shape_is_rewritten_with_the_card_s_own_path(self) -> None:
        found = self.prose("The census is `build_route_indexes` (L4-L5) and it holds.")

        self.assertIn(
            "The census is cit:([`build_route_indexes`], kernel/index.py:4-5) and it holds.", found
        )

    def test_the_anchor_inside_the_parentheses_shape_keeps_its_brackets_balanced(self) -> None:
        found = self.prose("The census (`build_route_indexes` L4-L5) holds.")

        self.assertIn("The census cit:([`build_route_indexes`], kernel/index.py:4-5) holds.", found)

    def test_a_comma_separated_variant_is_the_same_construct(self) -> None:
        found = self.prose("The census (`build_route_indexes`, L4-L5) holds.")

        self.assertIn("cit:([`build_route_indexes`], kernel/index.py:4-5)", found)

    def test_a_citation_is_emitted_on_one_line(self) -> None:
        found = self.prose("The census is `build_route_indexes` (L4-L5).")

        written = next(one for one in found.splitlines() if migration.prose.CIT_MARK in one)
        self.assertEqual(written.count(migration.prose.CIT_MARK), 1)
        self.assertIn(")", written)

    def test_a_range_with_no_anchor_beside_it_denotes_nothing_and_is_declined(self) -> None:
        self.tree.card("kernel/index.py", "| Nothing. | n/a | n/a |", at="card.md")
        text = (self.tree.onboarding / "card.md").read_text(encoding="utf-8")
        (self.tree.onboarding / "card.md").write_text(
            text + "The census (L4-L5) holds.\n", encoding="utf-8"
        )

        self.assertEqual(self.only_code(self.tree.migrate()), drafts.PROSE_ANCHOR_MISSING)

    def test_a_leaf_identifier_is_not_read_as_a_line(self) -> None:
        """``(since L11)`` and ``(slice L5)`` are live here and neither is a citation."""
        found = self.prose("Recorded in (since L11) and again (slice L5).")

        self.assertNotIn(migration.prose.CIT_MARK, found)

    def test_an_anchor_elsewhere_in_the_same_file_reports_a_stale_range(self) -> None:
        self.tree.card("kernel/index.py", "| Nothing. | n/a | n/a |", at="card.md")
        text = (self.tree.onboarding / "card.md").read_text(encoding="utf-8")
        (self.tree.onboarding / "card.md").write_text(
            text + "The census is `build_route_indexes` (L1-L2).\n", encoding="utf-8"
        )

        item = self.declined(self.tree.migrate())

        self.assertEqual(item["code"], drafts.PROSE_ANCHOR_NOT_IN_CITED_RANGE)
        self.assertIn("The RANGE is stale, not the path", item["message"])
        self.assertIn("the range is stale and the path is right", item["action"])
        self.assertIn("stale range is the common case", item["action"])

    def test_an_anchor_absent_from_the_current_file_leaves_the_cause_unresolved(self) -> None:
        self.tree.source("kernel/index.py", "first = 1\nsecond = 2\n")
        self.tree.card("kernel/index.py", "| Nothing. | n/a | n/a |", at="card.md")
        path = self.tree.onboarding / "card.md"
        path.write_text(
            path.read_text(encoding="utf-8") + "The old behavior is `deleted_anchor` (L1-L2).\n",
            encoding="utf-8",
        )

        item = self.declined(self.tree.migrate())

        self.assertEqual(item["code"], drafts.PROSE_ANCHOR_NOT_IN_CITED_RANGE)
        self.assertIn("leaves the cause unresolved", item["message"])
        self.assertIn("wrong path", item["message"])
        self.assertIn("renamed", item["message"])
        self.assertIn("deleted", item["message"])
        self.assertIn("stale", item["message"])
        self.assertNotIn("did mean another file", item["message"])
        self.assertIn("Search the tree for the exact anchor", item["action"])
        self.assertIn("Tier 2 or Tier 3", item["action"])
        self.assertNotIn("citation meant a different file", item["action"])

    def test_a_card_declaring_no_usable_path_cannot_supply_one(self) -> None:
        self.tree.write(
            self.tree.onboarding,
            "overview.md",
            "# Overview\n\n| Field | Value |\n| --- | --- |\n| repository | agents-remember |\n\n"
            "The census is `build_route_indexes` (L4-L5).\n",
        )

        self.assertEqual(self.only_code(self.tree.migrate()), drafts.PROSE_PATH_UNKNOWN)

    def test_a_wrapped_construct_is_counted_rather_than_rewritten(self) -> None:
        self.tree.card("kernel/index.py", "| Nothing. | n/a | n/a |", at="card.md")
        text = (self.tree.onboarding / "card.md").read_text(encoding="utf-8")
        (self.tree.onboarding / "card.md").write_text(
            text + "The census is `build_route_indexes`\n(L4-L5) and it holds.\n", encoding="utf-8"
        )

        result = self.tree.migrate()

        self.assertEqual(result["proseNotOnOneLine"], 1)
        self.assertEqual(result["proseConverted"], 0)

    def test_a_bare_range_under_an_ordinary_sentence_is_not_read_as_a_wrapped_tail(self) -> None:
        """Only a line ENDING in an anchor can have had its range pushed onto the next one."""
        self.tree.card("kernel/index.py", "| Nothing. | n/a | n/a |", at="card.md")
        text = (self.tree.onboarding / "card.md").read_text(encoding="utf-8")
        (self.tree.onboarding / "card.md").write_text(
            text + "The census holds throughout.\n(L4-L5) is where it lives.\n", encoding="utf-8"
        )

        result = self.tree.migrate()

        self.assertEqual(result["proseNotOnOneLine"], 0)
        self.assertEqual(self.only_code(result), drafts.PROSE_ANCHOR_MISSING)

    def test_a_citation_quoted_inside_a_fence_is_documentation_not_a_citation(self) -> None:
        found = self.prose("```", "The census is `build_route_indexes` (L4-L5).", "```")

        self.assertNotIn(migration.prose.CIT_MARK, found)


class IdempotenceTests(TreeCase):
    """L6-R16: a second pass over a converted tree is a byte-for-byte no-op."""

    def setUp(self) -> None:
        super().setUp()
        self.tree.source(
            "kernel/index.py", "FIELD = 1\n\n\ndef build_route_indexes():\n    return FIELD\n"
        )
        self.tree.card(
            "kernel/index.py",
            f"| The `build_route_indexes` census. | L4-L5 | {link('kernel/index.py')} |",
            "| Nothing to cite. | — | — |",
            "| No anchor anywhere. | L1 | " + link("kernel/index.py") + " |",
        )
        text = (self.tree.onboarding / "kernel/index.py.md").read_text(encoding="utf-8")
        (self.tree.onboarding / "kernel/index.py.md").write_text(
            text + "\nThe census is `build_route_indexes` (L4-L5).\n", encoding="utf-8"
        )

    def test_a_second_pass_writes_nothing(self) -> None:
        self.tree.migrate()
        after = self.tree.snapshot()

        result = self.tree.migrate()

        self.assertEqual(self.tree.snapshot(), after)
        self.assertEqual(result["documentsWritten"], 0)
        self.assertEqual(result["supersededTables"], 0)
        self.assertEqual(result["supersededRows"], 0)

    def test_a_dry_run_writes_nothing_and_re_measures_nothing(self) -> None:
        before = self.tree.snapshot()

        result = self.tree.migrate(dry_run=True)

        self.assertEqual(self.tree.snapshot(), before)
        self.assertTrue(result["dryRun"])
        self.assertIsNone(result["findingsRemaining"])
        self.assertFalse(result["findingsRemeasured"])
        self.assertFalse(result["ok"])

    def test_a_converted_citation_passes_the_check_it_was_converted_for(self) -> None:
        self.tree.migrate()

        codes = {one["code"] for one in self.tree.check()["findings"]}
        self.assertNotIn("citation_table_columns_wrong", codes)
        self.assertNotIn("citation_anchor_absent_from_range", codes)


class WorkOrderTests(TreeCase):
    """L6-R28: one work order per DOCUMENT, so a parallel dispatch takes one each."""

    def test_declines_are_grouped_by_document_and_ordered_by_line(self) -> None:
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")
        for name in ("a", "b"):
            self.tree.card(
                f"kernel/{name}.py",
                f"| No anchor here. | L1 | {link('kernel/index.py')} |",
                f"| `FIELD` and `build_route_indexes` both. | L1 | {link('kernel/index.py')} |",
            )

        orders = self.tree.migrate()["workOrders"]

        self.assertEqual([one["document"] for one in orders], ["kernel/a.py.md", "kernel/b.py.md"])
        for one in orders:
            self.assertEqual(one["cardPath"], one["document"].removesuffix(".md"))
            self.assertEqual(
                [item["line"] for item in one["items"]],
                sorted(item["line"] for item in one["items"]),
            )
            self.assertEqual(one["itemCount"], 2)

    def test_every_item_names_the_anchor_the_source_and_the_next_edit(self) -> None:
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card(
            "kernel/caller.py", f"| The browser client. | L1 | {link('kernel/index.py')} |"
        )

        item = dict(self.tree.migrate()["workOrders"][0]["items"][0])

        self.assertEqual(item["source"], "kernel/index.py")
        self.assertEqual(item["subject"], "The browser client.")
        self.assertEqual(item["tier"], work_order.CURATOR_TIER)
        self.assertTrue(item["action"])
        self.assertIn("parserDependent", item)

    def test_anchor_choice_names_every_construct_instead_of_picking_one(self) -> None:
        self.tree.source("kernel/index.py", "def left():\n    pass\n\ndef right():\n    pass\n")
        self.tree.card(
            "kernel/caller.py",
            f"| The `left` and `right` pair. | L1-L4 | {link('kernel/index.py')} |",
        )

        item = self.declined(self.tree.migrate())

        self.assertEqual(item["code"], drafts.ANCHOR_CHOICE_NEEDED)
        self.assertIn("Name every construct the claim is about", item["action"])
        self.assertIn("Anchors pool", item["action"])
        self.assertNotIn("PICK ONE", item["action"])

    def test_the_decline_tally_is_complete_rather_than_a_sample(self) -> None:
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card(
            "kernel/caller.py",
            *[f"| No anchor {n}. | L1 | {link('kernel/index.py')} |" for n in range(30)],
        )

        result = self.tree.migrate()

        self.assertEqual(result["declinedByReason"], {drafts.ANCHOR_ABSENT_FROM_ROW: 30})
        self.assertEqual(len(result["workOrders"][0]["items"]), 30)

    def test_every_row_of_a_table_is_placed_before_any_is_judged(self) -> None:
        """The complete offender list for a document, not the first refusal in each table."""
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card(
            "kernel/caller.py",
            f"| No anchor here. | L1 | {link('kernel/index.py')} |",
            f"| The `build_route_indexes` census. | L1-L2 | {link('kernel/index.py')} |",
            "| Another with none. | L1 | [gone.py](agents-remember/kernel/gone.py) |",
        )

        result = self.tree.migrate()

        self.assertEqual(
            [one["code"] for one in result["workOrders"][0]["items"]],
            [drafts.ANCHOR_ABSENT_FROM_ROW, drafts.SOURCE_UNRESOLVABLE],
        )
        self.assertEqual(result["rowsConverted"], 1)


class ParserSplitTests(TreeCase):
    """Which numbers are final and which move when the extent layer does."""

    def test_a_range_from_a_declaration_and_one_from_a_mention_are_counted_apart(self) -> None:
        """TypeScript is on the parsed side of this line and JSON is not, so the split is
        asserted across that boundary rather than across a language list."""
        self.tree.source("web/app.ts", "export function buildRouteIndexes() { return 1; }\n")
        self.tree.source("web/fixture.json", '{\n  "buildRouteIndexes": 1\n}\n')
        self.tree.card(
            "web/app.ts", f"| The `buildRouteIndexes` census. | L1 | {link('web/app.ts')} |"
        )
        self.tree.card(
            "web/fixture.json",
            f"| The `buildRouteIndexes` fixture key. | L2 | {link('web/fixture.json')} |",
        )

        result = self.tree.migrate()

        self.assertEqual(result["rangesFromDeclaration"], 1)
        self.assertEqual(result["rangesFromOccurrenceOnly"], 1)

    def test_a_quoted_literal_does_not_depend_on_a_parser(self) -> None:
        self.tree.source("docs/rules.md", "# Top\n\nPath rules scope by prefix.\n")
        self.tree.card(
            "docs/rules.md",
            f'| The sentence. | "Path rules scope by prefix." | {link("docs/rules.md")} |',
        )

        result = self.tree.migrate()

        self.assertEqual(result["rangesFromLiteralMatch"], 1)
        self.assertEqual(result["rangesFromOccurrenceOnly"], 0)

    def test_a_refusal_from_locating_the_anchor_is_marked_provisional(self) -> None:
        self.tree.source("kernel/twice.py", "FIELD = 1\n\n\ndef other():\n    FIELD = 2\n")
        self.tree.card(
            "kernel/twice.py", f"| The `FIELD` constant. | n/a | {link('kernel/twice.py')} |"
        )

        result = self.tree.migrate()

        self.assertEqual(result["declinedParserDependent"], {repair.ANCHOR_AMBIGUOUS: 1})

    def test_a_refusal_decided_before_any_lookup_is_final(self) -> None:
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card("kernel/caller.py", f"| No anchor here. | L1 | {link('kernel/index.py')} |")

        self.assertEqual(self.tree.migrate()["declinedParserDependent"], {})


class WalkTests(TreeCase):
    """What the tree walk does and does not treat as a document."""

    def test_a_directory_named_like_a_document_is_not_read_as_one(self) -> None:
        (self.tree.onboarding / "not-a-card.md").mkdir()
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card(
            "kernel/index.py",
            f"| The `build_route_indexes` census. | L1 | {link('kernel/index.py')} |",
        )

        result = self.tree.migrate()

        self.assertEqual(result["documentsScanned"], 1)
        self.assertEqual(result["rowsConverted"], 1)

    def test_an_anchor_never_looked_for_answers_nowhere_rather_than_raising(self) -> None:
        """The sieve locates only anchors no cited file holds, and the resolver reads an
        entry only for those. An anchor it never asked about must still answer."""
        anchor = migration.model.Anchor(kind=migration.model.SYMBOL, text="never_searched")

        self.assertEqual(migration._Sightings({})[anchor], migration.symbol_index.Sightings())

    def test_scoped_migration_rechecks_only_its_document_and_is_idempotent(self) -> None:
        self.tree.source("kernel/index.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card(
            "kernel/a.py",
            f"| The `build_route_indexes` census. | L1-L2 | {link('kernel/index.py')} |",
        )
        self.tree.card("kernel/b.py", f"| No anchor. | L1-L2 | {link('kernel/index.py')} |")
        outside = self.tree.text("kernel/b.py.md")
        snapshot = self.tree.source_snapshot_id()

        result = migration.migrate_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/a.py.md",
            expected_snapshot=snapshot,
        )
        after = self.tree.text("kernel/a.py.md")

        self.assertEqual(result["documentsScanned"], 1)
        self.assertEqual(result["documentsWritten"], 1)
        self.assertEqual(result["findingsRemaining"], 0)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.tree.text("kernel/b.py.md"), outside)

        again = migration.migrate_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/a.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(again["documentsScanned"], 1)
        self.assertEqual(again["documentsWritten"], 0)
        self.assertEqual(again["findingsRemaining"], 0)
        self.assertTrue(again["ok"], again)
        self.assertEqual(self.tree.text("kernel/a.py.md"), after)
        self.assertEqual(self.tree.text("kernel/b.py.md"), outside)


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


if __name__ == "__main__":
    unittest.main()
