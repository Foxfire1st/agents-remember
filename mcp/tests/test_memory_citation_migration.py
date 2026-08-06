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

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.memory_quality.style.citations import (
    drafts,
    migration,
    range_resolution,
    source_index,
    work_order,
)
from agents_remember.memory_quality.style.citations.resolution import Trees

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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
