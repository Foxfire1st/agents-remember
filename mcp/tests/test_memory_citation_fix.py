"""L6-R27/R28: what ``--fix`` repairs, what it refuses, and where it is allowed to write.

Four repair classes, one probe each (L6-R16), and the three refusals matter more than the
repair: a confident wrong answer here silently repoints a claim at code that does not do
what the claim says.

    PURE MOVE      the anchor kept its name and changed file       -> applied
    RENAME         a differently-named function does the job now   -> refused
    DELETION       the anchor exists nowhere                       -> refused
    AMBIGUOUS      the anchor resolves in more than one place      -> refused

:class:`NoSimilarityMatchingTests` is the one that would fail loudest if somebody added
"did you mean": it plants the recorded pair, ``_map_command_lifecycle`` cited against a tree
holding only ``_require_command_lifecycle``, and requires the refusal not to name it.

:class:`WriteGuardTests` proves the other half of R27 by filesystem: the official memory
repo is untouched, and a contract that names it is refused rather than followed.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

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
    fixer,
    migration,
    model,
    range_resolution,
    repair,
    source_index,
    source_index_cache,
    source_index_database,
    symbol_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.worktrees.worktree_contract import write_contract
from test_memory_tool_enclosure_scope import REPO, _enclosure

CARD_HEADER = (
    "# {path}",
    "",
    "| Field | Value |",
    "| --- | --- |",
    "| repository | agents-remember |",
    "| path | `{path}` |",
    "",
    "## Repo-Internal References",
    "",
    "| Finding | Anchor | Source |",
    "| --- | --- | --- |",
)
FIRST_ROW = len(CARD_HEADER) + 1


@contextmanager
def _frozen_no_discovery() -> Iterator[None]:
    with (
        mock.patch.object(
            Path,
            "rglob",
            side_effect=AssertionError("frozen refusal scanned memory"),
        ),
        mock.patch.object(
            source_index.os,
            "walk",
            side_effect=AssertionError("frozen refusal walked source"),
        ),
        mock.patch.object(
            source_index,
            "_tree_state",
            side_effect=AssertionError("frozen refusal inspected source"),
        ),
        mock.patch.object(
            source_index,
            "_reclaim_legacy_cache_roots",
            side_effect=AssertionError("frozen refusal reclaimed cache"),
        ),
        mock.patch.object(
            source_index,
            "_build_and_publish",
            side_effect=AssertionError("frozen refusal rebuilt/fell back"),
        ),
        mock.patch.object(
            source_index_database.Database,
            "validate_application_integrity",
            side_effect=AssertionError("frozen refusal traversed integrity"),
        ),
    ):
        yield


def document(*rows: str, path: str) -> str:
    header = "\n".join(line.format(path=path) for line in CARD_HEADER)
    return header + "\n" + "\n".join(rows) + "\n"


def filler(count: int, *, marker: str = "line") -> str:
    return "\n".join(f"{marker}{index} = {index}" for index in range(1, count + 1)) + "\n"


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

    def card(self, source_path: str, *rows: str, at: str | None = None) -> Path:
        return self.write(
            self.onboarding, at or f"{source_path}.md", document(*rows, path=source_path)
        )

    def trees(self) -> Trees:
        return Trees(code_root=self.code, memory_root=self.memory)

    def source_snapshot_id(self) -> str:
        with source_index.open_repository_index(self.trees()) as index:
            return index.snapshot_id

    def fix(self, *, dry_run: bool = False) -> dict[str, Any]:
        return fixer.fix_onboarding_root(self.onboarding, self.code, dry_run=dry_run)

    def check(self) -> dict[str, Any]:
        return range_resolution.check_onboarding_root(self.onboarding, self.code)

    def card_text(self, relative: str) -> str:
        return (self.onboarding / relative).read_text(encoding="utf-8")

    def row(self, relative: str, offset: int = 0) -> str:
        return self.card_text(relative).splitlines()[FIRST_ROW - 1 + offset]


class TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Tree(Path(self._tmp.name))

    def sources(self, result: dict[str, Any], index: int = 0) -> str:
        self.assertTrue(result["repairs"], result["declined"])
        return str(result["repairs"][index]["now"])

    def declined(self, result: dict[str, Any], index: int = 0) -> dict[str, Any]:
        self.assertTrue(result["declined"], result["repairs"])
        return dict(result["declined"][index])

    def assert_check_clean(self) -> None:
        result = self.tree.check()
        self.assertTrue(result["ok"], result["findings"])


class PureMoveTests(TreeCase):
    """A symbol that kept its name and changed file. The only class that auto-repairs."""

    def test_a_vanished_file_is_repointed_to_where_the_symbol_now_lives(self) -> None:
        self.tree.source("kernel/indexes.py", "def build_route_indexes():\n    return 1\n")
        self.tree.card(
            "kernel/caller.py",
            "| The census freezes membership. | `build_route_indexes` "
            "| kernel/route_index.py:1-2 |",
        )

        result = self.tree.fix()

        self.assertEqual(self.sources(result), "kernel/indexes.py:1-2")
        self.assertEqual(result["claimsRepaired"], 1)
        self.assertEqual(result["declinedCount"], 0)
        self.assertEqual(result["findingsRemaining"], 0)
        self.assertTrue(result["ok"])
        self.assert_check_clean()

    def test_a_symbol_that_left_a_file_that_still_exists_replaces_that_source(self) -> None:
        """The old module survived the extraction; the citation must not survive with it."""
        self.tree.source("kernel/route_index.py", filler(20))
        self.tree.source("kernel/indexes.py", filler(4) + "def build_route_indexes():\n    x = 1\n")
        self.tree.card(
            "kernel/caller.py",
            "| The census freezes membership. | `build_route_indexes` "
            "| kernel/route_index.py:1-2 |",
        )

        result = self.tree.fix()

        self.assertEqual(self.sources(result), "kernel/indexes.py:5-6")
        self.assertNotIn("route_index.py", self.tree.row("kernel/caller.py.md"))
        self.assert_check_clean()

    def test_a_move_is_found_even_when_forty_other_files_mention_the_name(self) -> None:
        """Uniqueness is judged on DEFINITIONS: callers move nothing."""
        self.tree.source("kernel/indexes.py", "def build_route_indexes():\n    return 1\n")
        for index in range(40):
            self.tree.source(f"callers/use{index}.py", "build_route_indexes()\n")
        self.tree.card(
            "kernel/caller.py",
            "| The census. | `build_route_indexes` | kernel/route_index.py:1-2 |",
        )

        self.assertEqual(self.sources(self.tree.fix()), "kernel/indexes.py:1-2")

    def test_the_whole_construct_is_stamped_decorators_included(self) -> None:
        self.tree.source(
            "kernel/indexes.py",
            "import functools\n\n\n@functools.cache\ndef build_route_indexes():\n    return 1\n",
        )
        self.tree.card(
            "kernel/caller.py",
            "| The census. | `build_route_indexes` | kernel/indexes.py:1-1 |",
        )

        self.assertEqual(self.sources(self.tree.fix()), "kernel/indexes.py:4-6")


class ReflowTests(TreeCase):
    """The bulk of the churn: the file changed shape and the number went stale."""

    def test_a_range_that_stops_short_of_its_own_construct_is_regenerated(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card("kernel/store.py", "| The write path. | `persist` | kernel/store.py:12-12 |")

        result = self.tree.fix()

        self.assertEqual(self.sources(result), "kernel/store.py:11-12")

    def test_a_range_past_the_end_of_the_file_is_regenerated(self) -> None:
        self.tree.source("kernel/store.py", "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py", "| The write path. | `persist` | kernel/store.py:400-480 |"
        )

        self.assertEqual(self.sources(self.tree.fix()), "kernel/store.py:1-2")

    def test_a_citation_that_is_already_right_is_left_byte_identical(self) -> None:
        self.tree.source("kernel/store.py", "def persist():\n    return 2\n")
        self.tree.card("kernel/store.py", "| The write path. | `persist` | kernel/store.py:1-2 |")
        before = self.tree.card_text("kernel/store.py.md")

        result = self.tree.fix()

        self.assertEqual(result["failingClaims"], 0)
        self.assertEqual(result["documentsWritten"], 0)
        self.assertEqual(self.tree.card_text("kernel/store.py.md"), before)

    def test_a_second_run_is_a_no_op(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card("kernel/store.py", "| The write path. | `persist` | kernel/store.py:12-12 |")
        self.tree.fix()
        after = self.tree.card_text("kernel/store.py.md")

        again = self.tree.fix()

        self.assertEqual(again["claimsRepaired"], 0)
        self.assertEqual(self.tree.card_text("kernel/store.py.md"), after)

    def test_a_dry_run_reports_the_rewrite_and_writes_nothing(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card("kernel/store.py", "| The write path. | `persist` | kernel/store.py:12-12 |")
        before = self.tree.card_text("kernel/store.py.md")

        result = self.tree.fix(dry_run=True)

        self.assertEqual(self.sources(result), "kernel/store.py:11-12")
        self.assertEqual(result["documentsWritten"], 1)
        self.assertEqual(self.tree.card_text("kernel/store.py.md"), before)

    def test_the_row_around_the_source_cell_survives_the_rewrite(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py",
            "| The write path, and `persist` is named here too. | `persist` "
            "|   kernel/store.py:12-12   |",
        )

        self.tree.fix()

        self.assertEqual(
            self.tree.row("kernel/store.py.md"),
            "| The write path, and `persist` is named here too. | `persist` "
            "|   kernel/store.py:11-12   |",
        )


class NoSimilarityMatchingTests(TreeCase):
    """Refuse similarity guesses when an anchor is absent."""

    def rename(self) -> dict[str, Any]:
        self.tree.source(
            "controlplane/lifecycle.py",
            "def _require_command_lifecycle(command):\n    raise ValueError(command)\n",
        )
        self.tree.card(
            "controlplane/caller.py",
            "| The command palette maps its lifecycle. | `_map_command_lifecycle` "
            "| controlplane/lifecycle.py:1-2 |",
        )
        return self.tree.fix()

    def test_a_rename_is_refused_rather_than_guessed_at(self) -> None:
        result = self.rename()

        self.assertEqual(result["claimsRepaired"], 0)
        self.assertEqual(self.declined(result)["code"], repair.ANCHOR_ABSENT)

    def test_the_refusal_never_names_the_similar_symbol(self) -> None:
        """One maps and the other validates; a string distance cannot tell them apart."""
        message = self.declined(self.rename())["message"]

        self.assertNotIn("_require_command_lifecycle", message)
        self.assertIn("exists NOWHERE", message)
        self.assertIn("Read the CLAIM", message)

    def test_the_row_is_left_exactly_as_written(self) -> None:
        self.rename()

        self.assertIn("controlplane/lifecycle.py:1-2", self.tree.row("controlplane/caller.py.md"))


class DeletionAndAmbiguityTests(TreeCase):
    """The other two refusals, each with the work order R28 requires."""

    def test_a_deleted_anchor_is_refused_and_says_the_claim_is_what_changed(self) -> None:
        self.tree.source("serving/live.py", "def kept():\n    return 1\n")
        self.tree.card(
            "serving/caller.py",
            "| The palette runs turn.stop. | `stop_turn` | serving/live.py:1-2 |",
        )

        declined = self.declined(self.tree.fix())

        self.assertEqual(declined["code"], repair.ANCHOR_ABSENT)
        self.assertEqual(declined["anchor"], "`stop_turn`")
        self.assertIn("rename or a deletion", declined["message"])

    def test_an_ambiguous_anchor_is_refused_and_names_every_candidate(self) -> None:
        self.tree.source("serving/left.py", "def emit():\n    return 1\n")
        self.tree.source("serving/right.py", "def emit():\n    return 2\n")
        self.tree.card("serving/caller.py", "| The emitter. | `emit` | serving/gone.py:1-2 |")

        declined = self.declined(self.tree.fix())

        self.assertEqual(declined["code"], repair.ANCHOR_AMBIGUOUS)
        self.assertIn("2 file(s) hold it", declined["message"])
        self.assertIn("serving/left.py:1-2", declined["message"])
        self.assertIn("serving/right.py:1-2", declined["message"])
        self.assertIn("never picks between candidates by similarity", declined["message"])

    def test_two_constructs_of_the_same_name_in_the_cited_file_are_refused(self) -> None:
        self.tree.source(
            "serving/live.py",
            "def emit():\n    return 1\n" + filler(20) + "def emit():\n    return 2\n",
        )
        self.tree.card("serving/caller.py", "| The emitter. | `emit` | serving/live.py:40-45 |")

        declined = self.declined(self.tree.fix())

        self.assertEqual(declined["code"], repair.ANCHOR_AMBIGUOUS)
        self.assertIn("occurs 2 times", declined["message"])
        self.assertIn("picks out none of them", declined["message"])

    def test_the_cited_range_picks_between_two_constructs_when_it_overlaps_one(self) -> None:
        """The author's own range is the tiebreaker inside a file, never a similarity score."""
        self.tree.source(
            "serving/live.py",
            "def emit():\n    return 1\n" + filler(20) + "def emit():\n    return 2\n",
        )
        self.tree.card("serving/caller.py", "| The emitter. | `emit` | serving/live.py:24-24 |")

        self.assertEqual(self.sources(self.tree.fix()), "serving/live.py:23-24")

    def test_a_tree_left_with_findings_it_has_no_rule_for_does_not_report_ok(self) -> None:
        """The halfway state must not be indistinguishable from a finished one."""
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py",
            "| The write path. | `persist` | kernel/store.py:12-12 |",
            "| Unanchored. | — | kernel/store.py:1-2 |",
        )

        result = self.tree.fix()

        self.assertEqual(result["claimsRepaired"], 1)
        self.assertEqual(result["declinedCount"], 0)
        self.assertEqual(result["findingsRemaining"], 1)
        self.assertFalse(result["ok"])

    def test_a_declined_claim_leaves_a_complete_work_order(self) -> None:
        self.tree.source("serving/live.py", "def kept():\n    return 1\n")
        self.tree.card(
            "serving/caller.py",
            "| The palette runs turn.stop. | `stop_turn` | serving/live.py:1-2 |",
        )

        declined = self.declined(self.tree.fix())

        self.assertEqual(declined["path"], "serving/caller.py.md")
        self.assertEqual(declined["line"], FIRST_ROW)
        self.assertTrue(declined["message"])


class MultiAnchorRangeTests(TreeCase):
    """Several anchors pool, so the generated source list is a UNION, never a span."""

    def two_anchors(self) -> dict[str, Any]:
        self.tree.source(
            "kernel/store.py",
            "def persist():\n    return 1\n" + filler(30) + "def reload():\n    return 2\n",
        )
        self.tree.card(
            "kernel/store.py",
            "| Both halves of the store. | `persist`; `reload` | kernel/store.py:1-1 |",
        )
        return self.tree.fix()

    def test_two_distant_anchors_produce_two_ranges_not_one_span(self) -> None:
        self.assertEqual(
            self.sources(self.two_anchors()), "kernel/store.py:1-2; kernel/store.py:33-34"
        )

    def test_the_union_is_not_the_enclosing_span(self) -> None:
        self.assertNotIn("kernel/store.py:1-34", self.sources(self.two_anchors()))

    def test_discontiguous_anchors_are_repaired_rather_than_refused(self) -> None:
        self.assertEqual(self.two_anchors()["declinedCount"], 0)
        self.assert_check_clean()

    def test_abutting_extents_are_merged_into_one_range(self) -> None:
        self.tree.source(
            "kernel/store.py", "def persist():\n    return 1\ndef reload():\n    return 2\n"
        )
        self.tree.card(
            "kernel/store.py",
            "| Both halves. | `persist`; `reload` | kernel/store.py:99-99 |",
        )

        self.assertEqual(self.sources(self.tree.fix()), "kernel/store.py:1-4")

    def test_anchors_in_two_files_produce_one_source_each(self) -> None:
        self.tree.source("kernel/left.py", "def persist():\n    return 1\n")
        self.tree.source("kernel/right.py", "def reload():\n    return 2\n")
        self.tree.card(
            "kernel/caller.py",
            "| Both halves. | `persist`; `reload` | kernel/left.py:2-2; kernel/right.py:2-2 |",
        )

        self.assertEqual(self.sources(self.tree.fix()), "kernel/left.py:1-2; kernel/right.py:1-2")


class AnchorKindTests(TreeCase):
    """One extent rule per anchor kind: AST construct, markdown section, quoted lines."""

    def test_a_heading_anchor_spans_to_the_next_heading_of_equal_or_higher_level(self) -> None:
        self.tree.source(
            "docs/path-rules.md",
            "# Rules\n\n## Scoping\n\nPrefixes win.\n\n### Detail\n\nMore.\n\n## Other\n\nNo.\n",
        )
        self.tree.card(
            "kernel/paths.py",
            "| Path rules scope by prefix. | `## Scoping` | docs/path-rules.md:1-1 |",
        )

        self.assertEqual(self.sources(self.tree.fix()), "docs/path-rules.md:3-10")

    def test_a_quoted_literal_spans_the_lines_it_occupies(self) -> None:
        self.tree.source(
            "serving/refusal.py",
            '"""Refusal banner.\n\nYou must pass the application\nas an import string.\n"""\n',
        )
        self.tree.card(
            "cli/dashboard.py",
            '| The refusal is loud. | "You must pass the application as an import string" '
            "| serving/refusal.py:1-1 |",
        )

        self.assertEqual(self.sources(self.tree.fix()), "serving/refusal.py:3-4")

    def test_a_typescript_construct_is_stamped_from_its_declaration(self) -> None:
        self.tree.source("dashboard/rail.ts", filler(5) + "export class RailRow {}\n" + filler(5))
        self.tree.card("dashboard/panel.tsx", "| The row. | `RailRow` | dashboard/rail.ts:1-1 |")

        self.assertEqual(self.sources(self.tree.fix()), "dashboard/rail.ts:6-6")

    def test_a_language_that_is_not_parsed_falls_back_to_occurrence_runs(self) -> None:
        self.tree.source("dashboard/rail.css", ".x {}\n.rail-row {\n  color: red;\n}\n")
        self.tree.card(
            "dashboard/panel.tsx",
            '| The row colour. | "color: red" | dashboard/rail.css:1-1 |',
        )

        self.assertEqual(self.sources(self.tree.fix()), "dashboard/rail.css:3-3")

    def test_a_python_file_that_does_not_parse_falls_back_the_same_way(self) -> None:
        self.tree.source("kernel/broken.py", filler(3) + "def persist(:\n")
        self.tree.card("kernel/caller.py", "| The write path. | `persist` | kernel/broken.py:1-1 |")

        self.assertEqual(self.sources(self.tree.fix()), "kernel/broken.py:4-4")


class SourcePreservationTests(TreeCase):
    """What `--fix` must not delete, and the one thing it must."""

    def test_a_citation_into_a_dependency_is_carried_through_untouched(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.card(
            "kernel/store.py",
            "| The write path and its warning. | `persist` "
            "| kernel/store.py:12-12; uvicorn/main.py:604-607 |",
        )

        self.assertEqual(
            self.sources(self.tree.fix()), "kernel/store.py:11-12; uvicorn/main.py:604-607"
        )

    def test_an_unanchored_live_source_survives_a_pure_reflow(self) -> None:
        self.tree.source("kernel/store.py", filler(10) + "def persist():\n    return 2\n")
        self.tree.source("kernel/context.py", filler(9))
        self.tree.card(
            "kernel/store.py",
            "| The write path. | `persist` | kernel/store.py:12-12; kernel/context.py:1-9 |",
        )

        self.assertEqual(
            self.sources(self.tree.fix()), "kernel/store.py:11-12; kernel/context.py:1-9"
        )

    def test_an_unanchored_live_source_is_dropped_once_an_anchor_has_moved(self) -> None:
        """Keeping it would leave the vacated pointer sitting beside its own replacement."""
        self.tree.source("kernel/indexes.py", "def build_route_indexes():\n    return 1\n")
        self.tree.source("kernel/route_index.py", filler(9))
        self.tree.card(
            "kernel/caller.py",
            "| The census. | `build_route_indexes` | kernel/route_index.py:1-9 |",
        )

        self.assertEqual(self.sources(self.tree.fix()), "kernel/indexes.py:1-2")


class UnresolvableOnlyClaimTests(TreeCase):
    """A claim whose every source is a dependency is SATISFIED, not permanently failing.

    Found by a curator pilot, and it made ``--fix`` useless on the third-party form this leaf
    mandates: ``unsatisfied`` folded over an empty ``bodies``, so ``any(...)`` was False for
    every anchor and the claim reported as failing on every run. Since ``ok`` is ``not refused
    and not remaining``, ``--fix`` could never report ok on a tree carrying one of these.
    """

    def _dependency_only_card(self) -> None:
        self.tree.source("kernel/store.py", filler(10))
        self.tree.card(
            "kernel/store.py",
            '| The refusal is loud, not silent. | "You must pass the application as an '
            'import string" | uvicorn/main.py:604-607 |',
        )

    def test_a_claim_citing_only_a_dependency_is_not_a_failing_claim(self) -> None:
        self._dependency_only_card()

        result = self.tree.fix()

        self.assertEqual(result["failingClaims"], 0, result["declined"])
        self.assertEqual(result["declinedCount"], 0, result["declined"])
        self.assertTrue(result["ok"], result)

    def test_the_checker_and_the_fixer_agree_that_nothing_resolved_means_nothing_unheld(
        self,
    ) -> None:
        """The guard lives in ``unsatisfied`` so a third caller cannot reintroduce the bug."""
        self._dependency_only_card()

        self.assert_check_clean()
        self.assertEqual(self.tree.fix()["failingClaims"], 0)

    def test_scoped_normalisation_does_not_make_a_dependency_permanently_unfixable(self) -> None:
        self._dependency_only_card()
        snapshot = self.tree.source_snapshot_id()

        result = fixer.fix_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            only="kernel/store.py.md",
            expected_snapshot=snapshot,
        )

        self.assertEqual(result["declinedCount"], 0)
        self.assertEqual(result["documentsWritten"], 0)
        self.assertTrue(result["ok"], result)

    def test_an_unheld_anchor_is_still_reported_when_something_did_resolve(self) -> None:
        """The guard must not swallow the real finding it sits next to."""
        self.tree.source("kernel/store.py", filler(10))
        self.tree.card(
            "kernel/store.py",
            "| The write path. | `persist` | kernel/store.py:1-9; uvicorn/main.py:604-607 |",
        )

        codes = {finding["code"] for finding in self.tree.check()["findings"]}

        self.assertIn("citation_anchor_absent_from_range", codes)


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

            def __getitem__(self, index: int) -> extents.WordMark:
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


if __name__ == "__main__":
    unittest.main()
