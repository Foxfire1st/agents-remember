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

Every population here is derived at run time, and each LIVE one is asserted against that
derivation rather than against a remembered figure: the pair every live number is measured
against -- a commit pair, not a branch, and the reason for that -- is named in the block
below. Each pinned constant is a tolerated defect with its register row and owner named, asserted
in BOTH directions: *repaired => remove that entry in the same change; never delete the constant,
never widen it.* A case that cannot fail is not a check -- the acceptance test for every pin here
is the mutation that reds it (`T112`, `T123`, law 4).
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
# Tolerated defects. Each is a NAMED, COUNTED population asserted against a DERIVATION rather
# than against its own literal, so this module is red when the defect grows, when it is repaired
# without the entry being removed, and when the derivation itself stops seeing (`T112`, `law 4`).
#
# THE PAIR EVERY LIVE NUMBER HERE IS MEASURED AGAINST, named because getting it wrong is this
# campaign's most-repeated defect (L5's T65, and F1 of this leaf's own review):
#
#   memory  the pair `260918-TSIP-L12` LANDS: memory `ec1cebe5` -- the
#           `260918_tool-surface-and-process-integrity` memory tip after its THIRD SYNC, which
#           merged the concurrent `260915_knowledge-substrate` line into this master -- plus this
#           leaf's memory change set; paired with code `15e10084` (the same master's code tip)
#           plus this leaf's code change set. Every number below was RE-DERIVED on that pair, not
#           carried forward from the pair this leaf was cut from, and the closeout commits it.
#           Check it out into a DISPOSABLE worktree and point `AR_ONBOARDING_ROOT` at that
#           checkout's `onboarding/`.
#   code    the checkout this suite runs in -- the repository root `REPOSITORY_ROOT` resolves to
#           -- whose bytes at the pinned tip are the bytes the memory tree documents.
#
# THE BRANCH IS ONLY WHERE THE COMMIT MAY BE FOUND, NOT THE PIN'S ADDRESS, and that is the
# correction `F3` needed twice. The header this replaces named a leaf worktree (`fd1a024e`) that a
# leaf finalize reclaims; the header this repairs named the MASTER work branch
# `ar/260918_tool-surface-and-process-integrity`, which the master's own cleanup reclaims in turn
# (the register's `T89`: *cleanup retired the master's work branch*). And the branch itself is
# written by another line besides this master -- it moved three times while this leaf lived:
# `7dcec036`/`66b2ae8a`, then `756c47b3`/`da33325c`, then this master's own sync onto it. Each
# move changed the tree under these pins: on the pre-sync sprint branch the superseded-pair
# population was 66 and two qualified `## Update History...` headings returned, so those pins are
# red THERE and correct HERE. `git -C memory-repos/ar-agents-remember rev-parse
# ar/260713_improved-agentic-system` therefore tells you where to find the commit once this
# master's integration has landed it there; it is NOT the pin's address, and a red pin on a moved
# or merged tree is not a regression until it is re-derived on that tree. That is `T122`/law 7 --
# a pin is a claim about a moment.
#
# `260918-TSIP-L11` re-derived these constants at memory `ee1a16cc` / code `a3050958`; this leaf
# re-derived them three times -- once on the pair it was cut from (`4d0fc20a`/`47570cd8`), once
# after repairing the corpus, and finally ON THE SYNCED TREE, which is the pair that ships. The
# superseded-pair constant moved from an asserted `20` to 47 and then to the 55 the merged tree
# derives; the two citation constants moved with the merge, and both of those moves are stated
# beneath the constants with what they MEAN rather than only with their value.
#
# The OFFICIAL memory checkout (`memory-repos/ar-agents-remember`) is a DIFFERENT tree on a
# different commit and is refused for exactly this reason by
# `application/memory_tools.py:105 _refuse_official_memory` (called at `:140`). The two are a
# pair: naming one without the other measures a memory tree against code it was never documented
# for. Round 1 of this leaf made that mistake; every number below was re-derived on the pair.
#
# THE LIVE ARM SKIPS WITHOUT ``AR_ONBOARDING_ROOT``, so a green lane run is NEVER evidence that
# these pins hold: the variable is not set by the default selection, and only a run that sets it
# exercises the numbers below.
#
# T52 / T60 -- the REPORT-ONLY population. 130 rows carry a SYMBOL anchor with exactly one
# definition across the claim's cited files, inside no cited range, while the name still occurs
# inside one. This figure is a REPORT and not a gate: `definitionsOutsideCitedRanges` is the
# product's own counter and every row rides `reportOnlyFindings`, so it is counted, rendered and
# reviewed without entering `findingCount`. It read 120 when this module was written, 123 on the
# pair this leaf was cut from, and 130 ON THE MERGED TREE -- the +7 arrived with the third sync,
# whose TWO legs both moved (the sibling `260915_knowledge-substrate` line's documents arrived and
# the code they document arrived with them), so this module does not split the increase between
# the legs; what it asserts is the count and that the population stays a REPORT.
#
# The rows are traced, never adjusted to fit. Those that entered it under `260918-TSIP-L10` --
# which is what moved the number from 120 -- are `mcp/src/agents_remember/mcp/tools/knowledge.py.md:71`,
# `mcp/tests/overview.md:2788` and `mcp/tests/test_tool_refusal_conformance.py.md:114-115`. The
# merged population's own shape, measured with the product's counter on the pinned pair: 130 rows
# over 95 documents, densest in `mcp/src/agents_remember/application/overview.md` (4),
# `mcp/tests/overview.md` (4), `mcp/overview.md` (3), and 3 each in
# `application/knowledge_view_render.py.md`, `memory_quality/style/citations/migration.py.md`,
# `memory_quality/style/citations/source_index_cache.py.md`,
# `worktrees/integration/organizational_completion_repair.py.md` and `worktrees/modules/integrate.py.md`.
# Owner: the checker's own report (``citation_anchor_definition_outside_range``, emitted by the
# product) plus each route's curator for the re-read.
#
# T52 -- THE ENFORCED PIN IS 97, AND 97 IS INHERITED DEBT, NOT THIS MASTER'S. Every one of these
# rows is an ENFORCED finding -- 96 `citation_anchor_absent_from_range` and 1
# `citation_range_out_of_bounds` -- so the check reports ``ok: false`` on the merged tree, and the
# module says so rather than pinning a zero it cannot see. The 97 arrived with the THIRD SYNC: it
# is the sibling `260915_knowledge-substrate` line's citation debt, merged into this master's
# memory tree on 2026-09-20, and it is the same 97 that register row `T141` measured independently
# (392 failing / 337 repairable / 55 declined tree-wide, 97 enforced remaining). The densest
# documents are `mcp/src/agents_remember/application/knowledge_curator_ingest.py.md` (19),
# `mcp/tests/overview.md` (14), `mcp/tests/test_knowledge_curator_ingest_list.py.md` (12),
# `mcp/tests/diff_scope_test_support.py.md` (10) and `mcp/src/agents_remember/mcp/tools/knowledge.py.md` (9).
#
# A PIN THAT ASSERTS A DEBT MUST SAY SO, which is why this paragraph exists: the number is not a
# budget to grow into and not a target to keep. A CHANGE IN EITHER DIRECTION IS A FINDING --
# DOWN means somebody cleared rows (name them, and remove this entry only when the count reaches
# the value the cleared tree derives), UP means somebody added them (find which landing, and
# re-read the construct rather than the number). It read 283 when this module was written, 292
# after L8 landed, 53 after L10 landed, 0 once the last twelve rows had been re-read against the
# constructs they cite on the pair this leaf was cut from -- and 97 here, which is the merge's,
# measured on the tree this leaf ships.
#
# T58 -- 3 wrapped ``cit:`` constructs at 2 documents, RE-MEASURED on the merged tree rather than
# carried: ``models/worktree.py.md`` (1) and ``serving/conversation/active/service.py.md`` (2),
# the same two documents and the same three constructs as on the pre-merge pair, so this pin
# holds across the sync. The checker parses them (it joins the paragraph), the fixer counts them
# in ``claimsNotOnOneLine`` and can rewrite none of them, so the finding is reported by a route
# that never closes it. Owner: the product's citation fixer, this register row.
T52_DEFINITION_OUTSIDE_RANGE = 130
T52_ENFORCED_POPULATION = 97
T58_WRAPPED_CITATIONS = 3
T58_WRAPPED_DOCUMENTS = 2

# ---------------------------------------------------------------------------------------------
# T112 / F2 -- the superseded-pair worklist, DERIVED from the tree rather than remembered.
#
# The constant this replaces (`SUPERSEDED_DECLARED_SITES = 20`) was compared with its own literal,
# so no change to any tree could move it: a hard-coded fact wearing the clothes of a measurement,
# in the module written to enforce agreement (`law 4` -- the acceptance test for a pin is the
# mutation that reds it, never the case that passes). Its named producer,
# `notes/reports/tsip-instruments/l7-curator-stale-pair-enum.py`, read 64 outside
# `## Update History` on the very tree the module pins, and `F1` of this master's end review is
# the class it was blind to. The rule below is that producer's, carried in-tree so the case can
# fail, and the producer is named so a second seat can re-derive the count independently:
#
#   a LINE outside a `## Update History` section whose figures include the SUPERSEDED unit
#   ceiling (2300 -- the comma-grouped `2,300` included, which a plain `grep 2300` under-counts),
#   or which states 400 as the integration ceiling.
#
# The section rule is the producer's `startswith("update history")` narrowed to the exact title
# `update_history_sections` itself accepts (`history_order.py`: ``title.strip() == "Update
# History"``). The two agree on the tree named above because `QualifiedHistoryHeadingTests` pins
# that tree's qualified `## Update History...` headings as the NAMED population
# `mcp/tests/overview.md:4833` and asserts it in BOTH directions -- a NEW qualified heading is a
# finding there, and so is the disappearance of the one the third sync's merge brought in, which is
# an entry to remove in the same change rather than a silence. Where the two rules would differ,
# this rule is the one that cannot be blinded by a mangled heading.
SUPERSEDED_PAIR = (2300, 400)
HISTORY_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
FIGURES_IN_LINE = re.compile(r"[\d,]{3,}")


def history_section_flags(text: str) -> list[bool]:
    """Per line: does it sit inside a level-2 `Update History` section?

    The exact-title rule, not a prefix: `history_order.update_history_sections` recognises only
    the bare form, so a line under a qualified heading is NOT a dated record and stays in the
    worklist.

    THE BOUNDS, stated because both are real, one of them is deliberate, and the earlier
    sentence about them was wrong in the direction it named -- measured, not reasoned
    (`notes/reports/tsip-instruments/l12-sv3-bound.py`):

    * vs THE PRODUCT (`history_order`, pattern ``^(#{1,6})``, a section ending at any heading of
      level <= 2): a level-1 `# ...` heading INSIDE an `## Update History` section ends the
      product's section and does NOT end this flag (nor the producer's, whose pattern is
      ``^(#{2,6})``). A line beneath such a heading is OUTSIDE the product's section and INSIDE
      mine, so this worklist can UNDER-count against what the product treats as outside history.
    * vs THE PRODUCER (prefix match on the title): a QUALIFIED `## Update History...` heading is
      history to the producer and not to this rule, so this worklist can OVER-count against the
      producer. That direction is deliberate: it is the one that cannot be blinded by a mangled
      heading.

    Neither bound is reached on the pinned pair -- `QualifiedHistoryHeadingTests` asserts zero
    qualified headings, and the successor verification measured 0 of 2,345 documents differing --
    which is why the derivation and the producer agreed there. They are written down so a tree
    that does reach one is recognised instead of absorbed, and they are NOT fixed here: widening
    either would change the derivation, and therefore the pin, for a case the pinned pair does
    not have.
    """
    flags: list[bool] = []
    inside = False
    for line in text.splitlines():
        match = HISTORY_HEADING.match(line)
        if match is not None and len(match.group(1)) == 2:
            inside = match.group(2).strip() == "Update History"
        flags.append(inside)
    return flags


def superseded_pair_sites(onboarding_root: Path) -> list[str]:
    """Every ``path:line`` stating the superseded pair outside `## Update History`.

    The producer's rule, in-tree. Ordered by path then line, so two runs on one tree return the
    same list in the same order and a diff between two trees is a real diff.
    """
    sites: list[str] = []
    for path in sorted(onboarding_root.glob("**/*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        in_history = history_section_flags(text)
        for number, line in enumerate(text.splitlines(), 1):
            if in_history[number - 1]:
                continue
            figures = {int(one.replace(",", "")) for one in FIGURES_IN_LINE.findall(line)}
            states_unit = SUPERSEDED_PAIR[0] in figures
            states_integration = SUPERSEDED_PAIR[1] in figures and "integration" in line.lower()
            if states_unit or states_integration:
                sites.append(f"{path.relative_to(onboarding_root)}:{number}")
    return sites


def qualified_history_headings(onboarding_root: Path) -> list[str]:
    """Every level-2 heading whose title BEGINS with `Update History` and is not exactly it.

    `T125`/`F8`: the corpus writes `## Update History` and the checker accepts only that spelling,
    so a qualified title renders as a section boundary while every entry beneath it is invisible
    to the order check -- and the corpus carried two of them with no diagnostic at all.
    """
    found: list[str] = []
    for path in sorted(onboarding_root.glob("**/*.md")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            match = HISTORY_HEADING.match(line)
            if match is None or len(match.group(1)) != 2:
                continue
            title = match.group(2).strip()
            if title.startswith("Update History") and title != "Update History":
                found.append(f"{path.relative_to(onboarding_root)}:{number}")
    return found


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
    #: The superseded-pair worklist -- every line outside `## Update History` on the pair named at
    #: the head of this module that still states the pair THIS MASTER'S RAISE SUPERSEDES
    #: (``SUPERSEDED_PAIR``), DERIVED by ``superseded_pair_sites`` and pinned as the count that
    #: function returns. The producer is `notes/reports/tsip-instruments/l7-curator-stale-pair-
    #: enum.py`, whose rule the function carries (`T112`). The constant this replaces was compared
    #: with its own literal `20`, so no tree could move it, while the producer read 64 outside
    #: `## Update History` on the very tree it pinned.
    #:
    #: IT NOW READS 55, AND THE MOVES ARE THE POINT: 64 at base, 47 once this leaf had repaired the
    #: seventeen lines that stated the superseded pair as current (`F1`), 55 on the MERGED tree.
    #: The +8 arrived with the third sync and every one of them is the sibling
    #: `260915_knowledge-substrate` line's own budget prose -- its documents arrived carrying the
    #: pair as it stood when they were written. This is a WORKLIST, not a verdict: each line is a
    #: dated or as-of reading reviewed row by row (`F1`'s classification, §4.5 of this leaf's
    #: report) and none of them is asserted to be wrong by being counted. A change in either
    #: direction is a finding: UP means a landing added a line that states the superseded pair,
    #: DOWN means somebody repaired one -- and in both cases the entry moves in the SAME change,
    #: with the line read against the declaration it names rather than shifted.
    #: Owner: each route's curator, for the re-read; the register row is `T112`.
    NON_HISTORY_SUPERSEDED_SITES = 55

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

    def test_the_superseded_pair_population_is_derived_from_the_tree(self) -> None:
        """`T112`/`F2`: the worklist is COUNTED ON THE TREE, so a landing that moves it reds here.

        The constant this replaces read ``20`` and was compared with ``20``: it passed whatever
        the corpus said, and the master's own producer for the population read 64 outside
        `## Update History` on the same tree. The pair is still pinned as the superseded one, and
        the count is asserted against the derivation rather than against itself, so both failure
        directions are live -- a repair that removes a site (55 down) and a landing that adds one
        (55 up) red this case with the sites named in the message.

        The case SKIPS when no tree is named, like every other live case here; the derivation's
        own ability to fire is proved hermetically by ``SupersededPairDerivationTests``.
        """
        self.assertNotEqual(
            SUPERSEDED_PAIR,
            (self.declared()["unit"], self.declared()["integration"]),
            "the superseded pair must not be the declared one",
        )
        root = live_onboarding_root()
        if root is None:
            self.skipTest("AR_ONBOARDING_ROOT names no memory tree on this machine")
        sites = superseded_pair_sites(root)
        self.assertEqual(
            len(sites),
            self.NON_HISTORY_SUPERSEDED_SITES,
            f"T112: the superseded-pair population moved; first sites {sites[:8]}",
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

        ``LIVE_SITE_COUNT`` is the set a repair moves; ``NON_HISTORY_SUPERSEDED_SITES`` is the
        worklist the next curation inherits. A single number could not tell a repaired site from a
        newly stale one.
        """
        self.assertGreater(self.LIVE_SITE_COUNT, 0)
        self.assertLessEqual(self.LIVE_SITE_COUNT, len(self.NON_HISTORY_SITES))
        self.assertGreater(self.NON_HISTORY_SUPERSEDED_SITES, 0)


class SupersededPairDerivationTests(unittest.TestCase):
    """`T112`: the worklist rule itself, proven to fire on planted shapes.

    The live case above skips without a named tree, so without these arms a lane run would say
    nothing at all about the derivation -- which is exactly the shape `F2` found: the old
    constant was asserted, and nothing could move it. Each arm is a document built for the
    purpose, and the rule is the named producer's (`l7-curator-stale-pair-enum.py`).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="tsip-l12-pair-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def document(self, name: str, body: str) -> None:
        path = self.root / "mcp" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_a_live_site_outside_history_is_counted(self) -> None:
        """The rule fires, and it names the site rather than only counting it."""
        self.document("live.md", "# card\n\nThe pair declared now is `unit_case_budget = 2300`.\n")
        self.assertEqual(superseded_pair_sites(self.root), ["mcp/live.md:3"])

    def test_the_same_line_inside_update_history_is_a_dated_record(self) -> None:
        """A history entry states what was true then, so it is not part of the worklist."""
        self.document(
            "dated.md",
            "# card\n\n## Update History\n\n- 2026-01-01T00:00+00:00: raised to 2300 unit.\n",
        )
        self.assertEqual(superseded_pair_sites(self.root), [])

    def test_a_qualified_heading_cannot_hide_a_live_line(self) -> None:
        """`T125`/`F8`'s class, at the rule that consumes it.

        The producer's own section test is a prefix match, so a mangled `## Update History...`
        heading would hide the lines beneath it from the worklist. This rule uses the exact title
        the product itself accepts, so it cannot; the two agree on the pinned pair because
        ``QualifiedHistoryHeadingTests`` asserts that tree carries no qualified heading.
        """
        self.document(
            "qualified.md",
            "# card\n\n## Update History` bullets\n\n- 2026-01-01T00:00+00:00: it was 2300 unit.\n",
        )
        self.assertEqual(superseded_pair_sites(self.root), ["mcp/qualified.md:5"])

    def test_the_comma_grouped_spelling_is_counted(self) -> None:
        """`T112`'s own under-count: a plain ``grep 2300`` misses ``2,300``."""
        self.document("grouped.md", "# card\n\nThe pair was 2,300 unit and 400 integration.\n")
        self.assertEqual(superseded_pair_sites(self.root), ["mcp/grouped.md:3"])

    def test_the_declared_pair_is_not_a_site(self) -> None:
        """The other direction: the pair the repository declares now must not be counted."""
        self.document("declared.md", "# card\n\nThe pair is `unit_case_budget = 3000` / 600.\n")
        self.assertEqual(superseded_pair_sites(self.root), [])


class QualifiedHistoryHeadingTests(unittest.TestCase):
    """`T125`/`F8`: a level-2 heading that only BEGINS with `Update History` is not a section.

    ``history_order.update_history_sections`` accepts one spelling -- a level-2 heading whose
    title is exactly ``Update History`` -- so a qualified title still renders as a section
    boundary while every entry under it is invisible to the order check, and the corpus carried
    two of them with no diagnostic anywhere. The detector is proven by planting the shape, then
    asserted against the tree the pinned pair names as a NAMED, COUNTED population -- so a landing
    that adds one reds here, AND a repair that removes one reds until its entry is removed in the
    same change, which is the discipline every other pin in this module follows.
    """

    #: The qualified headings the merged tree carries, NAMED rather than counted away. It read
    #: ZERO on the pair this leaf was cut from -- this leaf normalised the two the master-end
    #: review found -- and ONE here, because the third sync merged the sibling
    #: `260915_knowledge-substrate` line's document in with it. `mcp/tests/overview.md:4833` is
    #: `## Update History — 260915-KS-L31`, a DATED heading on a memory document that is NOT this
    #: leaf's and is NOT repaired here. THE CURATOR READ IT AND DECIDED, and the decision is on
    #: the record rather than left as a question: at its originating commit `beaee93bb` the
    #: heading stood WITH its own `260915-KS-L31` bullet beneath it, in the corpus's uniform dated
    #: form shared with four sibling dated headings in the same file; the third sync's
    #: bullet-granular union relocated that bullet (now at `:4766`) and orphaned the heading. It is
    #: therefore NOT the wrap damage this leaf repaired for `F8` -- it is a structurally complete
    #: standalone line, and the `F8` reasoning does not transfer to it -- and removing it and
    #: promoting it are BOTH output-neutral (`history_section_line` stays 1705; 849 entries, 0
    #: malformed either way). So it was KEPT deliberately, and this module pins it as a named,
    #: counted population instead of asserting it away.
    QUALIFIED_HEADINGS: ClassVar[tuple[str, ...]] = ("mcp/tests/overview.md:4833",)

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="tsip-l12-heading-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def document(self, name: str, body: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_the_detector_fires_on_the_planted_shape(self) -> None:
        self.document("card.md", "# card\n\n## Update History` bullets\n\n- something\n")
        self.assertEqual(qualified_history_headings(self.root), ["card.md:3"])

    def test_the_detector_fires_on_a_dated_title(self) -> None:
        """The spelling `T125` named, on the same detector: a dated heading is invisible too."""
        self.document("dated.md", "# card\n\n## Update History — 260918-TSIP-L7\n\n- entry\n")
        self.assertEqual(qualified_history_headings(self.root), ["dated.md:3"])

    def test_an_exact_heading_is_not_reported(self) -> None:
        """The negative arm, and the one that keeps \"exactly\" exact: a deeper heading is fine."""
        self.document("good.md", "# card\n\n## Update History\n\n- entry\n\n### Update History\n")
        self.assertEqual(qualified_history_headings(self.root), [])

    def test_the_named_tree_carries_exactly_the_pinned_ones(self) -> None:
        """The pin, in both directions. Skips without a named tree, like every other live case."""
        root = live_onboarding_root()
        if root is None:
            self.skipTest("AR_ONBOARDING_ROOT names no memory tree on this machine")
        found = qualified_history_headings(root)
        self.assertEqual(
            found,
            list(self.QUALIFIED_HEADINGS),
            "T125: the qualified Update History population moved. UP means a landing introduced "
            "one -- normalise it, or add its entry with the reading that says why it stays. DOWN "
            f"means one was repaired -- remove its entry in the same change. Found: {found}",
        )


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
