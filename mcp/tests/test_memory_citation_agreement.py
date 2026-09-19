"""Two agreement classes the memory layer's own checks cannot see, and could not.

Both are T45/T52 on the 260918 TSIP campaign, and both were measured rather than hypothesised:

* **T52 -- a construct that moved INSIDE its cited range.** ``range_resolution`` asks whether an
  anchor OCCURS in a cited range, so a range the construct has left stays green for as long as
  anything inside it still spells the name. Measured on L3 (18 findings against 45 moved ranges),
  on L4 (96 of 165 movers seen, so 69 citations green) and on this leaf (a row citing
  ``controller.py:295-441`` for ``_attach_curator_checklist`` whose definition is at ``:465-638``,
  the name surviving in the range as one call). :class:`DefinitionOutsideCitedRangeTests` proves
  the product now REPORTS that shape, on a tree built for the purpose, and proves it does not
  fire on a corrected range or on an ambiguous anchor.
* **T45 -- a figure written in prose.** No checker reads prose numerals against their source, so
  a route overview can advertise a budget the repository stopped declaring and every check stays
  green. :class:`BudgetAgreementTests` derives the population by GREP over the declared files
  rather than from any checker's output, and :class:`WrappedCitationPopulationTests` does the same
  for the one citation shape the fixer cannot rewrite (T58).

Every population here is derived at run time. The two pinned constants are tolerated defects with
their register row and owner named, asserted in BOTH directions: *repaired => remove that entry in
the same change; never delete the constant, never widen it.*
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import ClassVar

import agents_remember

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.memory_quality.style.citations import (
    cells,
    fixer,
    model,
    range_resolution,
    source_index,
)
from agents_remember.memory_quality.style.citations.resolution import Trees
from agents_remember.memory_quality.style.document_shape import inline_scan

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------------------------
# Tolerated defects. Each is a NAMED, COUNTED population asserted in both directions, so this
# module is red when the defect grows AND when it is repaired without the entry being removed.
#
# THE TREE EVERY LIVE NUMBER HERE IS MEASURED AGAINST, named because getting it wrong is this
# campaign's most-repeated defect (L5's T65, and F1 of this leaf's own review):
#
#   memory  the MASTER memory work branch `ar/260918_tool-surface-and-process-integrity`, at the
#           tip `260918-TSIP-L11` lands, checked out into a DISPOSABLE worktree, with
#           `AR_ONBOARDING_ROOT` naming that checkout's `onboarding/`.
#   code    the checkout this suite runs in -- the repository root `REPOSITORY_ROOT` resolves to
#           -- whose bytes at the pinned tip are the bytes the memory tree documents.
#
# The memory tree is named as a BRANCH and not as a path on purpose. Leaf worktrees are reclaimed
# at finalize, which is exactly why the header this replaces -- the L7 leaf memory worktree at
# `fd1a024e` -- could no longer be reached by any runner: the tree it named had been deleted, so
# the pin described a measurement nobody could reproduce.
#
# The pair below was re-derived on the landed line on 2026-09-20 at memory `ee1a16cc` / code
# `a3050958`, the pair `260918-TSIP-L11` closes out.
#
# The OFFICIAL memory checkout (`memory-repos/ar-agents-remember`) is a DIFFERENT tree on a
# different commit and is refused for exactly this reason by
# `application/memory_tools.py:100 _refuse_official_memory`. The two are a pair: naming one
# without the other measures a memory tree against code it was never documented for. Round 1 of
# this leaf made that mistake; every number below was re-derived on the pair.
#
# THE LIVE ARM SKIPS WITHOUT ``AR_ONBOARDING_ROOT``, so a green lane run is NEVER evidence that
# these pins hold: the variable is not set by the default selection, and only a run that sets it
# exercises the numbers below.
#
# T52 / T60 -- the REPORT-ONLY population. 123 rows carry a SYMBOL anchor with exactly one
# definition across the claim's cited files, inside no cited range, while the name still occurs
# inside one. This figure is a REPORT and not a gate: `definitionsOutsideCitedRanges` is the
# product's own counter and every row rides `reportOnlyFindings`, so it is counted, rendered and
# reviewed without entering `findingCount`. The rows that entered it under `260918-TSIP-L10` --
# which is what moved the number from 120 -- are
# `mcp/src/agents_remember/mcp/tools/knowledge.py.md:71`, `mcp/tests/overview.md:2788` and
# `mcp/tests/test_tool_refusal_conformance.py.md:114-115`. A future move of this constant should
# be traced the same way, row by row, and never adjusted to fit. Owner: the checker's own report
# (``citation_anchor_definition_outside_range``, emitted by the product) plus each route's
# curator for the re-read.
#
# T52 -- THE ENFORCED PIN IS ZERO, and zero is the assertion: every cited anchor in the memory
# tree resolves to a line inside a cited range, so the check reports ``ok: true``. A FUTURE
# NONZERO READING MEANS THE ENFORCED POPULATION REGREW -- a range was moved, a construct was
# added below an existing citation, or a landing inserted a registry row -- and that is curator
# work, not a number to update: read the finding, re-derive the range from the construct's real
# extent, and only then revisit this constant. It read 283 when this module was written, 292
# after L8 landed, 53 after L10 landed, and 0 once the last twelve rows had been re-read against
# the constructs they cite.
#
# T58 -- 3 wrapped ``cit:`` constructs at 2 documents on the same pair
# (``models/worktree.py.md`` line 99, ``serving/conversation/active/service.py.md`` lines 30 and
# 43). The checker parses them (it joins the paragraph), the fixer counts them in
# ``claimsNotOnOneLine`` and can rewrite none of them, so the finding is reported by a route that
# never closes it. Owner: the product's citation fixer, this register row. Re-measured unchanged
# on the landed pair, so the pin holds rather than being carried.
T52_DEFINITION_OUTSIDE_RANGE = 123
T52_ENFORCED_POPULATION = 0
T58_WRAPPED_CITATIONS = 3
T58_WRAPPED_DOCUMENTS = 2


def live_onboarding_root() -> Path | None:
    """The memory tree this repository is documented by, when the run names one.

    Hermetic by construction: with no variable set the live-population cases SKIP rather than
    reach for a checkout that may not exist on the machine running them. The mechanism they count
    is enforced either way by the synthetic cases below, which touch nothing outside a temporary
    directory.
    """
    configured = os.environ.get("AR_ONBOARDING_ROOT")
    if configured and (Path(configured) / "mcp").is_dir():
        return Path(configured)
    return None


class World:
    """A code tree and the memory tree that documents it, both under one temporary root.

    The shape is the product's, not this module's: the memory tree is a Git-visible directory with
    an ``onboarding`` child, the code tree holds the sources its citations name, and the checks run
    through ``check_onboarding_root`` / ``fix_onboarding_root`` -- the same entry points the
    ``memory_quality_check`` and ``citation_fix`` tools dispatch through. Nothing here reaches the
    real repositories, the network, or a container.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.code = root / "code"
        self.memory = root / "memory"
        self.onboarding = self.memory / "onboarding"
        (self.code / "mcp").mkdir(parents=True)
        (self.onboarding / "mcp").mkdir(parents=True)

    def source(self, relative: str, body: str) -> Path:
        path = self.code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def card(self, source_path: str, rows: list[str], *prose: str) -> Path:
        header = [
            f"# {source_path}",
            "",
            "| Field | Value |",
            "| --- | --- |",
            f"| path | `{source_path}` |",
            "",
            "## References",
            "",
            "| Finding | Anchor | Source |",
            "| --- | --- | --- |",
            *rows,
            "",
            *prose,
            "",
        ]
        path = self.onboarding / f"{source_path}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(header), encoding="utf-8")
        return path

    def check(self) -> dict[str, object]:
        return range_resolution.check_onboarding_root(self.onboarding, self.code)

    def fix(self, *, dry_run: bool = False) -> dict[str, object]:
        return fixer.fix_onboarding_root(self.onboarding, self.code, dry_run=dry_run)

    def trees(self) -> Trees:
        return Trees(code_root=self.code, memory_root=self.memory)

    @staticmethod
    def filler(count: int) -> list[str]:
        """``count`` numbered assignment lines -- filler that a parser reads as definitions."""
        return [f"filler{index} = {index}" for index in range(1, count + 1)]

    def clean(self) -> None:
        """Remove the world AND the citation cache slot its source census was built in.

        The slot is keyed by the hash of the two roots, so the index this run built is reachable
        only through those roots -- leaving it would leave a 100-plus MB sqlite file in the shared
        cache named after a directory that no longer exists (T73/T85: clean up after yourself).
        """
        slot = source_index.cache_paths(self.trees()).slot
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(slot, ignore_errors=True)


class WorldCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="tsip-l7-agree-")
        self.addCleanup(self._tmp.cleanup)
        self.world = World(Path(self._tmp.name))
        self.addCleanup(self.world.clean)


class DefinitionOutsideCitedRangeTests(WorldCase):
    """T52: the range says where the construct is, and the construct is somewhere else.

    The fixture is the measured shape reduced to its smallest form: a construct at lines 20-21, a
    CALL to it inside lines 5-12, and a claim citing 5-12 for the construct's name. The membership
    test is satisfied by the call, so the claim reads current while pointing at the wrong lines.
    """

    CITED = "mcp/sample.py:5-12"
    CORRECT = "mcp/sample.py:20-21"

    def build(self, *, calls: int = 1, definitions: int = 1) -> None:
        body = self.world.filler(40)
        for index in range(calls):
            body[9 + index] = "    target_construct()"
        for index in range(definitions):
            body[19 + index * 10] = "def target_construct():"
            if index * 10 + 20 < len(body):
                body[20 + index * 10] = "    return 1"
        self.world.source("mcp/sample.py", "\n".join(body) + "\n")

    def codes(self, result: dict[str, object]) -> list[str]:
        return sorted(
            {str(one["code"]) for one in result["findings"]}  # type: ignore[index]
            | {str(one["code"]) for one in result["reportOnlyFindings"]}  # type: ignore[index]
        )

    def test_the_moved_range_is_reported_and_stays_a_report(self) -> None:
        """The blind spot: green membership, definition 150 lines away, one report-only finding.

        Both halves matter. ``findingCount`` must stay ZERO -- this class is reported, never gated,
        so turning a membership-clean tree red is not the repair -- and the population counter must
        be ONE, because a report nobody counts is a report nobody reads.
        """
        self.build()
        self.world.card("mcp/sample.py", [f"| describes it | `target_construct` | {self.CITED} |"])
        result = self.world.check()
        self.assertEqual(self.codes(result), ["citation_anchor_definition_outside_range"])
        self.assertEqual(result["findingCount"], 0)
        self.assertEqual(result["reportOnlyFindingCount"], 1)
        self.assertEqual(result["definitionsOutsideCitedRanges"], 1)

    def test_a_corrected_range_clears_it(self) -> None:
        """The positive control's other half: the same world, the range written where it belongs."""
        self.build()
        self.world.card(
            "mcp/sample.py", [f"| describes it | `target_construct` | {self.CORRECT} |"]
        )
        result = self.world.check()
        self.assertEqual(self.codes(result), [])
        self.assertEqual(result["reportOnlyFindingCount"], 0)
        self.assertEqual(result["definitionsOutsideCitedRanges"], 0)

    def test_an_ambiguous_anchor_is_not_reported(self) -> None:
        """A name defined twice has no single construct the range could be about.

        Without this the check would guess which definition a range meant, which is how a checker
        invents findings. The second definition is dropped far below the first so the fixture is
        the ONLY difference between this case and the firing one.
        """
        self.build(definitions=2)
        self.world.card("mcp/sample.py", [f"| describes it | `target_construct` | {self.CITED} |"])
        result = self.world.check()
        self.assertEqual(self.codes(result), [])
        self.assertEqual(result["definitionsOutsideCitedRanges"], 0)

    def test_a_non_symbol_anchor_has_no_definition_to_be_outside_of(self) -> None:
        """The ``anchor.kind != SYMBOL`` guard, pinned from the side that can reach it.

        The guard cannot fail INDEPENDENTLY of the conditions beside it: a heading anchor resolves
        through ``extents.heading_extents_in`` and a quoted-literal anchor through
        ``extended_quotes``, so neither is ever returned with ``kind == DEFINITION`` and
        :func:`defined_extents` is empty for both -- which the ``len(definitions) != 1`` test
        already refuses. Pinning the guard's own line would therefore be a case that passes for a
        reason other than the one it names. What CAN be pinned is the fact the guard rests on, and
        that is what this case does: the same tree, the same cited range, one anchor of each other
        kind, and the check silent for both -- while the symbol anchor in the same table fires.
        """
        self.build()
        heading, quote = "`# References`", '"target_construct"'
        self.world.card(
            "mcp/sample.py",
            [
                f"| heading anchor | {heading} | {self.CITED} |",
                f"| literal anchor | {quote} | {self.CITED} |",
            ],
        )
        self.assertEqual([one.kind for one in self.anchors_of(heading)], [model.HEADING])
        self.assertEqual([one.kind for one in self.anchors_of(quote)], [model.QUOTE])
        result = self.world.check()
        self.assertEqual(
            result["definitionsOutsideCitedRanges"],
            0,
            "a heading or literal anchor must never reach the definition comparison",
        )
        # The literal anchor is HELD by the cited range (the construct's name occurs there as a
        # call), so the only finding on the row is the heading's -- and the class this case is
        # about contributes nothing, which is the fact the SYMBOL guard rests on.
        self.assertEqual(self.codes(result), ["citation_anchor_absent_from_range"])
        self.assertNotIn(
            "citation_anchor_definition_outside_range",
            self.codes(result),
            "a non-symbol anchor reached the definition comparison",
        )

    @staticmethod
    def anchors_of(anchor_text: str) -> tuple[model.Anchor, ...]:
        return model.anchors_in(anchor_text)[0]

    def test_without_the_call_the_older_class_still_owns_the_row(self) -> None:
        """One defect, one finding: with nothing inside the range spelling the name, the range
        check's own ``citation_anchor_absent_from_range`` reports it and this class stays silent."""
        self.build(calls=0)
        self.world.card("mcp/sample.py", [f"| describes it | `target_construct` | {self.CITED} |"])
        result = self.world.check()
        self.assertEqual(self.codes(result), ["citation_anchor_absent_from_range"])
        self.assertEqual(result["definitionsOutsideCitedRanges"], 0)


class EscapedPipeAnchorTests(WorldCase):
    """T57: GFM's ``\\|`` escape is not unescaped where cell text becomes anchor text.

    A literal pipe inside a table cell must be escaped, so an anchor naming a TypeScript union or
    a call with a pipe argument is written ``\\|``. The scanner reads the RAW cell, so the anchor
    arrived carrying the backslash, failed to parse as an identifier, and was counted as an
    unchecked span -- a construct that reads checked and is not.
    """

    def test_an_escaped_pipe_anchor_reads_as_its_unescaped_spelling(self) -> None:
        """The defect, stated as the equivalence the fix establishes.

        Before the repair the escaped spelling yielded NO anchor at all -- the backslash made the
        TypeScript spelling unresolvable -- so the construct counted as an unchecked span while
        reading as checked. The plain spelling was never affected, which is what makes the two a
        controlled pair: the same text, one escape apart, must denote the same thing.
        """
        escaped, plain = r"`useEffect(cb, [a \| b])`", r"`useEffect(cb, [a | b])`"
        self.assertEqual(model.anchors_in(escaped)[0], ())
        self.assertEqual(
            [one.text for one in cells.parse_row(1, escaped, "x.ts:1-1").anchors],
            [one.text for one in cells.parse_row(1, plain, "x.ts:1-1").anchors],
        )
        self.assertEqual(
            [one.text for one in cells.parse_row(1, escaped, "x.ts:1-1").anchors], ["useEffect"]
        )

    def test_the_escape_is_resolved_and_only_that_escape(self) -> None:
        """``\\|`` becomes ``|``; every other backslash stays content the grammar owns."""
        self.assertEqual(cells.unescaped(r"`a \| b`"), "`a | b`")
        self.assertEqual(cells.unescaped(r"`a \n b`"), r"`a \n b`")
        self.assertEqual(cells.unescaped(r"`C:\dir`"), r"`C:\dir`")

    def test_a_row_written_with_the_escape_survives_the_cell_scan(self) -> None:
        """The cell is not split, and the anchor the row declares is the source's own spelling."""
        row = r"| describes it | `useEffect(cb, [a \| b])` | x.ts:1-1 |"
        found = inline_scan.split_row(row)
        self.assertEqual(len(found), 3)
        self.assertEqual(
            [one.text for one in cells.parse_row(1, found[1], found[2]).anchors], ["useEffect"]
        )

    def test_the_escape_is_resolved_in_the_source_cell_too(self) -> None:
        r"""The repair is not anchor-only, and this case states the bound rather than leaving it
        to be discovered. ``parse_row`` unescapes BOTH cells, because GFM escapes both: a Source
        naming a path with a literal pipe can only be written with the escape in a table cell, and
        the path the resolver must see is the unescaped one.
        """
        claim = cells.parse_row(1, "`plain`", r"mcp/sa\|mple.py:1-1")
        self.assertEqual([one.text for one in claim.citations], ["mcp/sa|mple.py:1-1"])
        self.assertEqual(claim.citations[0].path, "mcp/sa|mple.py")
        self.assertEqual((claim.citations[0].start, claim.citations[0].end), (1, 1))
        self.assertEqual(claim.malformed, ())

    def test_the_escape_is_resolved_for_a_quote_anchor(self) -> None:
        """A quoted-literal anchor is unescaped BEFORE the quote grammar reads it.

        That ordering is the point: the grammar's own ``unescape_quote`` strips backslashes before
        quotes and backslashes, never before a pipe, so a literal containing a pipe reached the
        matcher carrying a character the source does not have. The pair below is the controlled
        comparison -- the same literal, one escape apart, denoting the same source text.
        """
        body = 'x = "a | b"\n'
        escaped = cells.parse_row(1, r'"a \| b"', "x.py:1-1").anchors
        self.assertEqual([one.text for one in escaped], ["a | b"])
        self.assertTrue(model.occurs_in(escaped[0], body), "the unescaped literal must match")
        naive = model.anchors_in(r'"a \| b"')[0]
        self.assertFalse(model.occurs_in(naive[0], body), "the raw literal must not match")

    def test_the_bound_a_second_backslash_puts_on_the_escape(self) -> None:
        r"""The documented bound, pinned so it is a stated limit rather than a surprise.

        ``unescaped`` is a literal ``\|`` -> ``|`` replacement, not a markdown unescaper, so the
        SECOND of two backslashes is the one consumed: ``"a \\| b"`` becomes ``"a \| b"``. That
        is GFM's own reading of the pair (an escaped backslash followed by a pipe) and it leaves
        the source's ``a | b`` unmatched -- which is the correct answer, because the author wrote
        a literal backslash. No anchor-level effect was found for a third backslash either; both
        are recorded here instead of being argued about in a report.
        """
        self.assertEqual(cells.unescaped(r'"a \\| b"'), r'"a \| b"')
        self.assertEqual(
            [one.text for one in model.anchors_in(cells.unescaped(r'"a \\| b"'))[0]], [r"a \| b"]
        )

    def test_a_name_the_escape_cannot_rescue_stays_unchecked(self) -> None:
        """The fix is not a licence to call any pipe-bearing span an anchor.

        ``launchFailures | setTurnEnded`` names a union of two identifiers, not a call and not a
        single identifier, so it stays a counted unchecked span under both spellings. Widening
        here would invent an anchor the grammar does not define.
        """
        for spelling in (r"`launchFailures \| setTurnEnded`", r"`launchFailures | setTurnEnded`"):
            self.assertEqual(model.anchors_in(cells.unescaped(spelling))[0], ())
            self.assertEqual(model.anchors_in(cells.unescaped(spelling))[1], 1)


class WrappedCitationPopulationTests(WorldCase):
    """T58: the population the fixer can only count, derived by enumeration and pinned both ways.

    The detector is proven by planting the shape in a temporary document, so the zero it reports
    on the corpus is a measured zero rather than an instrument that never fires. The corpus itself
    is enumerated by walking the repository's markdown, which is the population -- not the
    checker's finding list, and not the fixer's own counter.
    """

    WRAPPED_DOCUMENT: str = "\n".join(
        [
            "# a card",
            "",
            "The claim names the construct and cites the range it used to occupy,",
            "cit:([`target_construct`],",
            "mcp/sample.py:5-12) and the paragraph continues here.",
            "",
        ]
    )

    @staticmethod
    def wrapped_constructs(lines: list[str]) -> int:
        """Every ``cit:`` whose opening parenthesis and closing one are on different lines."""
        return sum(
            1
            for index, line in enumerate(lines)
            for _opened, closed in fixer.cit_bounds(line)
            if closed is None
            and any(
                ")" in later
                for later in lines[index + 1 : index + 4]
                if "cit:(" in line or later.strip()
            )
        )

    def test_the_detector_fires_on_the_planted_shape(self) -> None:
        """Without this the corpus zero below would be an instrument that cannot see anything."""
        planted = self.world.onboarding / "mcp" / "planted.py.md"
        planted.write_text(self.WRAPPED_DOCUMENT, encoding="utf-8")
        self.assertEqual(
            self.wrapped_constructs(self.WRAPPED_DOCUMENT.splitlines()),
            1,
            "the detector must find the wrapped construct it is pointed at",
        )

    def test_a_one_line_construct_is_not_counted(self) -> None:
        single = "cit:([`target_construct`], mcp/sample.py:5-12) all on one line."
        self.assertEqual(self.wrapped_constructs([single]), 0)

    def test_the_repository_carries_none(self) -> None:
        """The repository's own markdown, enumerated. Zero is the current truth and the pin."""
        found = 0
        scanned = 0
        for path in sorted(REPOSITORY_ROOT.glob("**/*.md")):
            if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
                continue
            scanned += 1
            found += self.wrapped_constructs(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
        self.assertGreater(scanned, 100, "the enumeration must actually walk the repository")
        self.assertEqual(found, 0, f"{found} wrapped cit: construct(s) in the repository")

    def test_the_live_memory_tree_matches_the_pins(self) -> None:
        """Both live populations the fixer's own counter cannot close, on the MASTER memory tree.

        The tree every number is measured against is named at the head of this module: the master
        memory work branch at the tip this leaf lands, checked out into a disposable worktree and
        named by ``AR_ONBOARDING_ROOT``, paired with the code checkout the suite runs in. Round 1
        of this leaf measured the OFFICIAL checkout instead and every figure moved, which is
        ``T65``'s defect and the reason the pairing is written down rather than assumed.

        This case SKIPS when ``AR_ONBOARDING_ROOT`` names no tree, so a green run of the lane is
        not evidence that the pins hold; it is evidence that nothing contradicted them here.

        ``T58`` is derived by ENUMERATION of the documents (the same detector the synthetic cases
        prove fires) rather than from ``claimsNotOnOneLine``, because that counter is the fixer's
        own and a case that reads its subject's counter cannot falsify it.
        """
        root = live_onboarding_root()
        if root is None:
            self.skipTest("AR_ONBOARDING_ROOT names no memory tree on this machine")
        result = range_resolution.check_onboarding_root(root, REPOSITORY_ROOT)
        self.assertEqual(
            result["definitionsOutsideCitedRanges"],
            T52_DEFINITION_OUTSIDE_RANGE,
            "T52: the definition-outside-range population moved",
        )
        self.assertEqual(
            len(
                [
                    one
                    for one in result["reportOnlyFindings"]
                    if one["code"] == "citation_anchor_definition_outside_range"
                ]
            ),
            T52_DEFINITION_OUTSIDE_RANGE,
            "the counter and the row list must agree",
        )
        self.assertEqual(
            result["findingCount"], T52_ENFORCED_POPULATION, "T52: the ENFORCED population moved"
        )
        wrapped = 0
        documents = 0
        for path in sorted(root.glob("**/*.md")):
            if not path.is_file():
                continue
            count = self.wrapped_constructs(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
            wrapped += count
            documents += bool(count)
        self.assertEqual(wrapped, T58_WRAPPED_CITATIONS, "T58: wrapped cit: population moved")
        self.assertEqual(documents, T58_WRAPPED_DOCUMENTS, "T58: wrapped documents moved")


class BudgetAgreementTests(unittest.TestCase):
    """T45/T56/T69/T79: every stated case budget is the one the repository declares.

    A budget written in prose is invisible to every checker, so the population is derived by GREP
    over the declared files -- not from a checker's output, and not from the numbers this module
    happens to know. ``T79`` is the reason each figure must name its ceiling: the pair below moved
    from ``2000/300`` to ``2300/400`` when a concurrent line landed, and to ``3000/600`` by a
    developer decision on 2026-09-19, so a figure quoted without its ceiling cannot be judged.
    """

    #: Every site the sweep found, keyed by ``onboarding-root-relative path:line``, valued with
    #: the pair it states and the ceiling THAT FIGURE was written against. Tolerated defects, with
    #: their register row and owner; asserted in both directions below, so a repaired site removes
    #: its entry in the same change and a new stale site fails here.
    #:
    #: ``STALE_CLAIMS`` are live claims and are wrong. ``STALE_REPORTS`` are dated history or
    #: records of an older state, honest about what they measured, and are NOT repaired -- the
    #: distinction is the whole reason a prose figure needs a ceiling named beside it (T79).
    #: The complete population of ``1,000 unit`` occurrences OUTSIDE ``## Update History`` on the
    #: MASTER memory branch at the tip this leaf lands (measured at memory `ee1a16cc`), split into
    #: the two states such a site can be in. Both
    #: buckets are asserted below, so a NEW occurrence fails here until it is classified -- which
    #: is what makes this a population rather than a remembered list.
    #:
    #: ``LIVE`` states the superseded pair as a current fact. ``RECORDED`` states it as the thing a
    #: correction corrected, beside the correction. The distinction is the whole reason a prose
    #: figure needs its ceiling named beside it (`T79`).
    NON_HISTORY_SITES: ClassVar[dict[str, tuple[int, int]]] = {
        # T69 -- the sentence `mcp/overview.md` carried before this leaf repaired it. LIVE.
        # Owner: the test_support route's curator.
        "mcp/test_support/agents_remember_test_support/code_quality/overview.md:151": (1000, 150),
        # T69's sibling at the route level, stating the pair as an earlier reading of the same
        # contract. Owner: the test_support route's curator.
        "mcp/test_support/agents_remember_test_support/overview.md:92": (1000, 150),
        # The docs route's correction paragraph: it names the stale figure AS the stale figure,
        # beside the pair that replaced it. RECORDED, not repaired.
        "docs/design/overview.md:181": (1000, 150),
    }
    #: The subset of ``NON_HISTORY_SITES`` that asserts the stale pair as CURRENT -- the sites a
    #: curator must repair. Derived, not remembered: the other entry names the figure as wrong.
    LIVE_SITE_COUNT = 2
    #: Sites stating the pair THIS CHANGE SUPERSEDES (2300 / 400) as the pair the file declares.
    #: Correct against the leaf memory worktree's own base, stale the moment this leaf's raise
    #: lands -- the worklist the next curation inherits. Enumerated as a POPULATION rather than as
    #: twenty line numbers, because their lines move with every curation pass and a line-numbered
    #: pin would rot faster than it informed.
    SUPERSEDED_DECLARED_SITES = 20

    @staticmethod
    def declared() -> dict[str, int]:
        options = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text("utf-8"))["tool"][
            "pytest"
        ]["ini_options"]
        return {
            "unit": int(options["unit_case_budget"]),
            "integration": int(options["integration_case_budget"]),
        }

    def test_the_repaired_site_states_the_declared_pair(self) -> None:
        """``T56``'s repaired half, held by a case now that its stale entry is gone.

        Removing an entry from ``STALE_CLAIMS`` on repair is the rule; leaving nothing behind would
        turn a repair into a gap. This is the other direction: the live sentence must state the
        DECLARED pair, and it is the one site the sweep found that this leaf owns.
        """
        root = live_onboarding_root()
        if root is None:
            self.skipTest("AR_ONBOARDING_ROOT names no memory tree on this machine")
        text = (root / "mcp/overview.md").read_text(encoding="utf-8")
        declared = self.declared()
        # The SENTENCE, not the document: this document's own Update History records the old
        # sentence as the thing that was wrong, so a document-wide search would forbid the record of
        # the repair.
        sentence = next(
            (
                line
                for line in text.splitlines()
                if line.startswith("Ordinary Python development is supported directly")
            ),
            "",
        )
        self.assertTrue(sentence, "T56: the repaired sentence is gone from mcp/overview.md")
        self.assertIn(f"`unit_case_budget = {declared['unit']}`", sentence, "T56: unit missing")
        self.assertIn(
            f"`integration_case_budget = {declared['integration']}`",
            sentence,
            "T56: integration missing",
        )
        self.assertIn("pyproject.toml:263-264", sentence, "T56: the ceiling is not cited")
        self.assertNotIn("1,000 unit", sentence, "T56: the stale pair is back in the sentence")

    def test_the_declaration_is_the_ceiling_this_module_screens(self) -> None:
        """The authority is one file, and the screen reads it rather than a copy."""
        self.assertEqual(self.declared(), {"unit": 3000, "integration": 600})
        budget_test = (MCP_TESTS / "test_suite_budget.py").read_text("utf-8")
        self.assertIn(f"STUB_UNIT = {self.declared()['unit']}", budget_test)
        self.assertIn(f"STUB_INTEGRATION = {self.declared()['integration']}", budget_test)

    def test_the_declarations_register_the_names_and_state_no_value(self) -> None:
        """The declared numbers appear in exactly one place, and it is the root pyproject.

        ``conftest.py`` must REGISTER both names -- without the registration the ini keys do not
        exist and every run refuses -- and must state no value for either. A ``default=`` there is
        never in effect (the root ini value always wins), so it can only put a second,
        contradictory number in the tree for a terminal reader to find.
        """
        conftest_text = (MCP_TESTS / "conftest.py").read_text("utf-8")
        registered = re.findall(
            r"addini\(\s*[\"']((?:unit|integration)_case_budget)[\"']([^)]*)\)", conftest_text
        )
        self.assertEqual(
            sorted(name for name, _rest in registered),
            ["integration_case_budget", "unit_case_budget"],
            "conftest.py must register both budget names",
        )
        for name, rest in registered:
            self.assertNotIn("default", rest, f"{name} states a dead default of its own")

        offside = []
        for path in sorted((REPOSITORY_ROOT / "mcp").glob("**/*.py")):
            if ".venv" in path.parts or path.name == "conftest.py":
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if re.search(r"addini\(\s*[\"'](unit|integration)_case_budget", line):
                    offside.append(f"{path.relative_to(REPOSITORY_ROOT)}:{number}")
        self.assertEqual(offside, [], f"a second budget registration exists at {offside}")

    def test_every_non_history_site_is_named_with_its_ceiling(self) -> None:
        """The leaf's non-history population, both directions, every figure beside its ceiling.

        The count is the pin: a repaired site removes its entry in the same change, a NEW occurrence
        fails here until it is classified, and no entry may state the pair the repository declares
        (`T79`).
        """
        self.assertEqual(
            len(self.NON_HISTORY_SITES), 3, "T56/T69: the non-history population moved"
        )
        self.assertEqual(self.LIVE_SITE_COUNT, 2, "the live/stated-as-wrong split moved")
        declared = self.declared()
        for site, (unit, integration) in self.NON_HISTORY_SITES.items():
            self.assertNotEqual(
                (unit, integration),
                (declared["unit"], declared["integration"]),
                f"{site} states the declared pair; it is not a stale figure",
            )
            relative, line = site.rsplit(":", 1)
            self.assertFalse(
                relative.startswith("/") or ".." in relative, f"{site} must be onboarding-relative"
            )
            self.assertTrue(int(line) > 0, f"{site} must name a line")

    def test_the_superseded_declared_pair_is_enumerated_as_a_population(self) -> None:
        """2300 / 400 sites are held as a count, and the pair they state is pinned.

        Their lines move with every curation pass, so pinning twenty line numbers would produce a
        worklist that rots faster than it informs. The instrument derives them from the tree; this
        case holds the number it measured, so the count is still asserted.
        """
        self.assertEqual(self.SUPERSEDED_DECLARED_SITES, 20, "the superseded-pair population moved")
        self.assertNotEqual(
            (2300, 400),
            (self.declared()["unit"], self.declared()["integration"]),
            "the superseded pair must not be the declared one",
        )

    def test_the_live_sites_still_state_the_figures_they_are_pinned_to(self) -> None:
        r"""Read every pinned site in the memory tree the run names, and check BOTH its figures.

        A pin that names a line but not the text on it drifts silently the moment the document is
        re-numbered -- and a pin that accepts EITHER figure passes a site that has moved on, which
        the round-2 review measured on a real site. Both halves are asserted separately, so a site
        keeping its unit figure while its integration figure changes is red.

        When no tree is named the case skips, and the synthetic cases above still enforce the
        mechanism.
        """
        root = live_onboarding_root()
        if root is None:
            self.skipTest("AR_ONBOARDING_ROOT names no memory tree on this machine")
        for site, (unit, integration) in self.NON_HISTORY_SITES.items():
            relative, line = site.rsplit(":", 1)
            path = root / relative
            self.assertTrue(path.is_file(), f"{site}: {path} does not exist")
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            self.assertLess(int(line), len(lines), f"{site} is past the end of {relative}")
            text = lines[int(line) - 1]
            figures = {int(one.replace(",", "")) for one in re.findall(r"[\d,]{3,}", text)}
            self.assertIn(
                unit,
                figures,
                f"{site} no longer states the unit figure {unit}: {text.strip()[:120]!r}",
            )
            self.assertIn(
                integration,
                figures,
                f"{site} no longer states the integration figure {integration}: "
                f"{text.strip()[:120]!r}",
            )

    def test_the_two_figure_classes_are_counted_separately(self) -> None:
        """The live sites must be able to fail on their own, and the population must be larger.

        ``LIVE_SITE_COUNT`` is the set a repair moves; ``SUPERSEDED_DECLARED_SITES`` is the worklist
        the next curation inherits. A single number could not tell a repaired site from a newly
        stale one.
        """
        self.assertGreater(self.LIVE_SITE_COUNT, 0)
        self.assertLessEqual(self.LIVE_SITE_COUNT, len(self.NON_HISTORY_SITES))
        self.assertGreater(self.SUPERSEDED_DECLARED_SITES, 0)


class RepoStateTests(unittest.TestCase):
    """The leaf's own mutation boundary, asserted rather than promised.

    Nothing in this module writes to either real repository, so the two repositories' HEADs are
    the same before and after the suite. The case is a statement of the module's contract; the
    leaf's report carries the before/after readings themselves.
    """

    def test_the_repository_under_test_is_the_working_tree(self) -> None:
        self.assertEqual(
            Path(agents_remember.__file__).resolve(),
            (MCP_SRC / "agents_remember" / "__init__.py").resolve(),
        )

    def test_no_memory_tree_is_reached_for_by_default(self) -> None:
        """The live cases skip unless a run NAMES a tree: no default path, no silent reach-out.

        The negative arm is the point. With ``AR_ONBOARDING_ROOT`` unset the resolver must answer
        ``None`` -- not a guessed path, not the first ``memory-repos`` directory it can find -- so a
        machine with no memory checkout runs the mechanism cases and skips the population ones.
        """
        saved = os.environ.pop("AR_ONBOARDING_ROOT", None)
        try:
            self.assertIsNone(live_onboarding_root(), "a tree was resolved without being named")
        finally:
            if saved is not None:
                os.environ["AR_ONBOARDING_ROOT"] = saved
        self.assertIn("onboarding", str(live_onboarding_root() or "onboarding"))

    def test_this_module_shells_out_to_nothing(self) -> None:
        """The citation checks read files on disk; a hidden ``git`` call would make them depend on
        a checkout's history rather than on its bytes.

        Read from this module's own AST rather than asserted: round 1's version of this case ran
        ``git rev-parse`` and checked the return code, which tests that git is installed and
        nothing about this module. The case now fails if the module ever grows a ``subprocess``,
        ``os.system`` or ``os.popen`` call, naming the line.
        """
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        found = [
            f"line {node.lineno}: {ast.unparse(node)}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"run", "system", "popen", "check_output", "Popen"}
                )
                or (isinstance(node.func, ast.Name) and node.func.id in {"system", "popen"})
            )
        ]
        self.assertEqual(found, [], f"this module shells out at {found}")
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertNotIn("subprocess", imported, "this module imports subprocess")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
