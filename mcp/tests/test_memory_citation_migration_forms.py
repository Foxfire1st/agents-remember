from __future__ import annotations

from agents_remember.memory_quality.style.citations import drafts, migration, repair, work_order
from test_memory_citation_migration import TreeCase, link


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
