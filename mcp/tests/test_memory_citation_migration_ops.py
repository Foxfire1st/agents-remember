from __future__ import annotations

from agents_remember.memory_quality.style.citations import drafts, migration, repair, work_order
from test_memory_citation_migration import FIRST_ROW, TreeCase, link


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
