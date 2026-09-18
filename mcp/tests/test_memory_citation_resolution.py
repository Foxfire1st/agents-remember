"""Citation resolution rejects missing, escaped and out-of-range sources while preserving valid pooled claims."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
from datetime import UTC, datetime
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_attribution import render_memory_content_message
from agents_remember.kernel.memory_cache import refresh_memory_cache
from agents_remember.memory_quality.check import (
    run_memory_quality_check,
)
from agents_remember.memory_quality.style.citations import (
    claim_reopen,
    deterministic_projection,
    range_resolution,
)
from agents_remember.memory_quality.style.citations.editing import rewritten
from agents_remember.memory_quality.style.update_history import history_order

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
SUPERSEDED_HEADER = (
    "| Finding | Citations | Source Path |",
    "| --- | --- | --- |",
)
EVERY_CODE = frozenset(
    {
        "citation_table_columns_wrong",
        "citation_source_malformed",
        "citation_source_duplicate",
        "citation_anchor_missing",
        "citation_source_missing",
        "citation_range_out_of_bounds",
        "citation_anchor_absent_from_range",
        "citation_prose_malformed",
        "citation_prose_not_in_cit_form",
        "citation_prose_form_in_table_cell",
        "citation_source_vanished",
    }
)


def document(*rows: str, path: str) -> str:
    header = "\n".join(line.format(path=path) for line in CARD_HEADER)
    return header + "\n" + "\n".join(rows) + "\n"


def numbered(count: int, *, marker: str = "line") -> str:
    return "\n".join(f"const {marker}{index} = {index};" for index in range(1, count + 1)) + "\n"


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

    def memory_file(self, relative: str, body: str) -> Path:
        return self.write(self.memory, relative, body)

    def card(self, source_path: str, *rows: str, at: str | None = None) -> Path:
        return self.write(
            self.onboarding, at or f"{source_path}.md", document(*rows, path=source_path)
        )

    def run(self) -> dict:
        return range_resolution.check_onboarding_root(self.onboarding, self.code)


class TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tree = Tree(Path(self._tmp.name))

    def assert_clean(self, result: dict) -> None:
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["findingCount"], 0, result["findings"])
        self.assertEqual(result["reportOnlyFindingCount"], 0, result["reportOnlyFindings"])

    def codes(self, result: dict) -> list[str]:
        return [one["code"] for one in result["findings"]]


class FalsePositiveFixtures(TreeCase):
    """Every mode the module docstring enumerates, on a construct that exists in the tree."""

    def test_1_a_word_boundary_is_not_satisfied_by_a_longer_identifier(self) -> None:
        """`SERVED` must not pass on `SERVED_LIFECYCLE`, nor `taskName` on `enclosureTaskName`."""
        self.tree.source("serving/lifecycle.py", "SERVED_LIFECYCLE = 1\nenclosureTaskName = 2\n")
        self.tree.card(
            "serving/caller.py",
            "| The two names. | `SERVED`; `taskName` | serving/lifecycle.py:1-2 |",
        )
        result = self.tree.run()
        self.assertEqual(self.codes(result), ["citation_anchor_absent_from_range"] * 2)
        messages = " ".join(one["message"] for one in result["findings"])
        self.assertIn("the range holds ['SERVED_LIFECYCLE']", messages)
        self.assertIn("`taskName`", messages)

    def test_1b_the_same_names_pass_when_the_range_really_holds_them(self) -> None:
        self.tree.source("serving/lifecycle.py", "SERVED = 1\ntaskName = 2\n")
        self.tree.card(
            "serving/caller.py",
            "| The two names. | `SERVED`; `taskName` | serving/lifecycle.py:1-2 |",
        )
        self.assert_clean(self.tree.run())

    def test_4_two_ranges_one_anchor_are_pooled_not_paired(self) -> None:
        """``PUBLIC_TOOLS`` cited with a sub-range pointing into its own body."""
        body = numbered(20) + "export const PUBLIC_TOOLS = [\n" + numbered(60) + "];\n"
        self.tree.source("mcp/tools/base.py", body)
        self.tree.card(
            "mcp/tools/base.py",
            "| The two terminal-catalog public tools. | `PUBLIC_TOOLS` "
            "| mcp/tools/base.py:21-77; mcp/tools/base.py:23-24 |",
        )
        self.assert_clean(self.tree.run())


class ProseGrammarTests(TreeCase):
    """`cit:([anchors], path:start-end)` in running text, sharing every table rule."""

    def prose(self, *body: str) -> dict:
        self.tree.source("kernel/route_index.py", "def build_route_indexes():\n    pass\n" * 30)
        self.tree.card("kernel/caller.py", "| Nothing here. | — | — |", "", *body)
        return self.tree.run()

    def test_a_well_formed_citation_resolves_and_passes(self) -> None:
        result = self.prose(
            "The census freezes membership cit:([`build_route_indexes`], "
            "kernel/route_index.py:1-2) and nothing else does."
        )
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 1)
        self.assertEqual(result["resolvedCitations"], 1)

    def test_an_out_of_bounds_prose_range_fails_with_the_shared_code(self) -> None:
        result = self.prose(
            "The census cit:([`build_route_indexes`], kernel/route_index.py:1-900)."
        )
        self.assertEqual(self.codes(result), ["citation_range_out_of_bounds"])

    def test_an_absent_prose_anchor_fails_with_the_shared_code(self) -> None:
        result = self.prose("The census cit:([`absentName`], kernel/route_index.py:1-2).")
        self.assertEqual(self.codes(result), ["citation_anchor_absent_from_range"])

    def test_a_citation_inside_a_fence_is_not_scanned(self) -> None:
        result = self.prose("```", "cit:([`gone`], kernel/route_index.py:900-999)", "```")
        self.assert_clean(result)
        self.assertEqual(result["proseCitations"], 0)


class MisplacedSerialisationTests(TreeCase):
    """A `cit:` in a table cell is the wrong serialisation, and silence there is the defect."""

    def test_a_cit_written_into_a_finding_cell_is_reported(self) -> None:
        self.tree.source("kernel/route_index.py", "def build_route_indexes():\n    pass\n")
        self.tree.card(
            "kernel/caller.py",
            "| The census cit:([`build_route_indexes`], kernel/route_index.py:1-2). | `x` | — |",
        )
        result = self.tree.run()
        codes = self.codes(result)
        self.assertIn("citation_prose_form_in_table_cell", codes)
        message = next(one["message"] for one in result["findings"] if one["code"] == codes[0])
        self.assertIn("Anchor and Source columns", message)
        self.assertIn("delete the `cit:`", message)


class DeletedClassTests(TreeCase):
    """L6-R13: the two classes R27 made unrepresentable are gone, not dormant."""

    def test_a_parent_step_can_no_longer_reach_a_file_at_a_shallower_depth(self) -> None:
        """The link-depth class needed a relative link that climbs. The grammar refuses one."""
        self.tree.source("dashboard/webtui-scope.config.cjs", "x\n")
        self.tree.card(
            "dashboard/src/styles/webtui.css",
            "| The scoping options. | `x` | ../../../webtui-scope.config.cjs:1-1 |",
        )
        self.assertEqual(self.codes(self.tree.run()), ["citation_source_malformed"])


class StyleSurfaceTests(TreeCase):
    """How the check reaches the gate, and what it says when it cannot resolve."""

    def test_full_and_selected_walks_share_canonical_document_validation(self) -> None:
        outside = self.tree.memory / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (self.tree.onboarding / "escape.md").symlink_to(outside)

        for selected in (None, "escape.md"):
            with (
                self.subTest(selected=selected),
                self.assertRaisesRegex(ValueError, "must name one regular canonical document"),
            ):
                if selected is None:
                    range_resolution.check_onboarding_root(
                        self.tree.onboarding, self.tree.code, only=None
                    )
                else:
                    range_resolution.check_onboarding_root(
                        self.tree.onboarding,
                        self.tree.code,
                        only=selected,
                        expected_snapshot="a" * 64,
                    )

    def test_without_a_code_root_the_result_says_so_instead_of_passing_quietly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "onboarding"
            root.mkdir(parents=True)
            result = run_memory_quality_check(root)
            block = result["checks"][range_resolution.CHECK_NAME]
            self.assertEqual(block["status"], "no-code-repository-root")
            self.assertEqual(block["filesChecked"], 0)


class RetainedPreparedProvenanceTests(TreeCase):
    """A stale prepared tree may anchor history while claims resolve current bytes."""

    def git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return result.stdout.strip()

    def setUp(self) -> None:
        super().setUp()
        for root in (self.tree.code, self.tree.memory):
            self.git(root, "init", "--quiet")
            self.git(root, "config", "user.email", "fixture@example.invalid")
            self.git(root, "config", "user.name", "Fixture")
        (self.tree.memory / "memory.md").write_text("# Memory\n", encoding="utf-8")
        self.git(self.tree.memory, "add", "memory.md")
        self.git(self.tree.memory, "commit", "--quiet", "-m", "init")

    def card(self, stamp: str) -> None:
        self.tree.memory_file(
            "onboarding/current.md",
            "\n".join(
                (
                    "# Current",
                    "",
                    "| Field | Value |",
                    "| --- | --- |",
                    f"| lastVerifiedCommitHash | `{stamp}` |",
                    "| lastVerifiedCommitDate | 2026-09-10 |",
                    "",
                    "## Repo-Internal References",
                    "",
                    "| Finding | Anchor | Source |",
                    "| --- | --- | --- |",
                    "| Current value | `VALUE` | src.py:1-1 |",
                    "",
                )
            ),
        )

    def commits(self) -> tuple[str, str, str]:
        self.tree.source("src.py", "VALUE = 1\n")
        self.git(self.tree.code, "add", "src.py")
        self.git(self.tree.code, "commit", "--quiet", "-m", "old")
        old = self.git(self.tree.code, "rev-parse", "HEAD")
        self.git(self.tree.code, "checkout", "--quiet", "--orphan", "current")
        self.git(self.tree.code, "rm", "--quiet", "-rf", ".")
        self.tree.source("src.py", "VALUE = 2\n")
        self.git(self.tree.code, "add", "src.py")
        self.git(self.tree.code, "commit", "--quiet", "-m", "current")
        current = self.git(self.tree.code, "rev-parse", "HEAD")
        self.git(self.tree.code, "checkout", "--quiet", "--orphan", "unrelated")
        self.git(self.tree.code, "rm", "--quiet", "-rf", ".")
        self.tree.source("src.py", "VALUE = 3\n")
        self.git(self.tree.code, "add", "src.py")
        self.git(self.tree.code, "commit", "--quiet", "-m", "unrelated")
        unrelated = self.git(self.tree.code, "rev-parse", "HEAD")
        self.git(self.tree.code, "checkout", "--quiet", "current")
        return old, current, unrelated

    def check(
        self,
        stamp: str,
        anchor: str | None = None,
        anchors: tuple[str, ...] | None = None,
    ) -> dict:
        self.card(stamp)
        retained = anchors if anchors is not None else (() if anchor is None else (anchor,))
        return claim_reopen.check_onboarding_root(
            self.tree.onboarding,
            self.tree.code,
            retained_code_history_commits=retained,
        )

    def test_retained_prepared_commit_accepts_current_tree_and_rejects_other_history(self) -> None:
        old, current, unrelated = self.commits()

        retained = self.check(old, anchors=(old, current))
        self.assertTrue(retained["ok"], retained["findings"])
        self.assertEqual(len(retained["surfacedFindings"]), 1)
        self.assertEqual(retained["surfacedFindings"][0]["code"], "citation_claim_reopened")

        refused = self.check(unrelated, old)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["findings"][0]["code"], "citation_provenance_invalid")

        current_only = self.check(old, anchors=(current,))
        self.assertFalse(current_only["ok"])
        self.assertEqual(current_only["findings"][0]["code"], "citation_provenance_invalid")

        without_anchor = self.check(old)
        self.assertFalse(without_anchor["ok"])
        self.assertEqual(without_anchor["findings"][0]["code"], "citation_provenance_invalid")

        ordinary = self.check(current)
        self.assertTrue(ordinary["ok"], ordinary["findings"])

    def test_memory_citation_provenance_uses_git_when_the_cache_is_missing_or_malformed(
        self,
    ) -> None:
        self.tree.source("src.py", "VALUE = 1\n")
        self.git(self.tree.code, "add", "src.py")
        self.git(self.tree.code, "commit", "-qm", "code")
        code = self.git(self.tree.code, "rev-parse", "HEAD")
        self.tree.memory_file("system/policy.py", "VALUE = 1\n")
        self.git(self.tree.memory, "add", "system/policy.py")
        self.git(self.tree.memory, "commit", "-qm", render_memory_content_message("memory", code))
        memory = self.git(self.tree.memory, "rev-parse", "HEAD")
        self.card(code)
        card = self.tree.onboarding / "current.md"
        card.write_text(card.read_text().replace("src.py:1-1", "system/policy.py:1-1"))
        refresh_memory_cache(self.tree.memory)
        cache = self.tree.memory / "memory.md"
        for contents in (cache.read_text(), None, "malformed consumer cache\n"):
            with self.subTest(cache=contents):
                if contents is None:
                    cache.unlink()
                else:
                    cache.write_text(contents)
                result = claim_reopen.check_onboarding_root(self.tree.onboarding, self.tree.code)
                self.assert_clean(result)
                self.assertEqual(self.git(self.tree.code, "rev-parse", "HEAD"), code)
                self.assertEqual(self.git(self.tree.memory, "rev-parse", "HEAD"), memory)
        self.tree.memory_file("system/policy.py", "VALUE = 2\n")
        result = claim_reopen.check_onboarding_root(self.tree.onboarding, self.tree.code)
        self.assertEqual(result["surfacedFindings"][0]["code"], "citation_claim_reopened")


class InheritedProvenanceDebtTests(TreeCase):
    """A row the task INHERITED is debt or closeout-owned; a row it created or edited is its own.

    D-24 measured the guard: ``_demote_preexisting_provenance_debt`` keyed on document dirtiness, so
    a curation pass that CORRECTED a document made every pre-existing ambiguous row in it enforced
    debt -- measured 0 of 4 demoted -- and those rows cannot be cleared by any edit, because anchor
    multiplicity is decided per cited FILE (narrowing changes no occurrence count and splitting adds
    a row). These cases pin both halves of the fix: the demotion keys on the ROW's pre-task
    revision, and the multiplicity class is reported as closeout-owned instead of repairable.
    """

    AMBIGUOUS = "VALUE = 1\nVALUE = 2\nOTHER = 3\nTAIL = 4\n"
    ROWS = (
        "| The doubled value | `VALUE` | src.py:1-2 |",
        "| The other value | `OTHER` | src.py:3-3 |",
    )
    EDITED_ROW = "| The other value | `OTHER` | src.py:3-4 |"
    ADDED_ROW = "| The tail value | `TAIL` | src.py:4-4 |"

    def git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return result.stdout.strip()

    def setUp(self) -> None:
        super().setUp()
        for root in (self.tree.code, self.tree.memory):
            self.git(root, "init", "--quiet")
            self.git(root, "config", "user.email", "fixture@example.invalid")
            self.git(root, "config", "user.name", "Fixture")
        (self.tree.memory / "memory.md").write_text("# Memory\n", encoding="utf-8")
        self.git(self.tree.memory, "add", "memory.md")
        self.git(self.tree.memory, "commit", "--quiet", "-m", "init")
        self.tree.source("src.py", self.AMBIGUOUS)
        self.git(self.tree.code, "add", "src.py")
        self.git(self.tree.code, "commit", "--quiet", "-m", "source")
        self.stamp = self.git(self.tree.code, "rev-parse", "HEAD")
        # The semantic route only opens when the cited path MOVED since the stamp: a path proven
        # unchanged is skipped without resolving any anchor at all, which is the cheap routing the
        # module documents. One uncommitted line is enough, and it leaves both anchors' own
        # occurrence counts intact.
        self.tree.source("src.py", self.AMBIGUOUS.replace("TAIL = 4", "TAIL = 5"))

    def card(self, rows: tuple[str, ...], *, stamp: str | None = None, commit: bool) -> Path:
        card = self.tree.memory_file(
            "onboarding/src.py.md",
            "\n".join(
                (
                    "# src.py",
                    "",
                    "| Field | Value |",
                    "| --- | --- |",
                    f"| lastVerifiedCommitHash | `{self.stamp if stamp is None else stamp}` |",
                    "| lastVerifiedCommitDate | 2026-09-10 |",
                    "",
                    "## Repo-Internal References",
                    "",
                    "| Finding | Anchor | Source |",
                    "| --- | --- | --- |",
                    *rows,
                    "",
                )
            ),
        )
        if commit:
            self.git(self.tree.memory, "add", "onboarding/src.py.md")
            self.git(self.tree.memory, "commit", "--quiet", "-m", "card")
        return card

    def rewrite(self, card: Path, old: str, new: str) -> None:
        card.write_text(card.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def check(self) -> dict:
        return claim_reopen.check_onboarding_root(self.tree.onboarding, self.tree.code)

    def test_an_ambiguous_row_in_a_document_the_task_corrected_is_closeout_owned(self) -> None:
        """The acceptance case: correct an UNRELATED range and the inherited row is not promoted.

        `VALUE` binds twice in ``src.py``, so no edit can make it unique. Red before the fix: the
        document becomes dirty, the row stays in ``findings`` as enforced curator debt the curator
        cannot discharge.
        """

        card = self.card(self.ROWS, commit=True)
        self.rewrite(card, self.ROWS[1], self.EDITED_ROW)

        result = self.check()

        self.assertEqual(result["findings"], [], result["findings"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["debtFindings"], [])
        self.assertEqual(result["closeoutOwnedCount"], 1)
        owned = result["closeoutOwnedFindings"]
        self.assertEqual(owned[0]["code"], "citation_provenance_invalid")
        self.assertEqual(owned[0]["path"], "src.py.md")
        self.assertTrue(owned[0]["closeoutOwned"])
        self.assertIn("resolved 2 times at verification", owned[0]["message"])

    def test_an_unrelated_new_range_does_not_promote_the_documents_inherited_rows(self) -> None:
        """The demotion keys on the ROW's pre-task revision, not on the document's dirtiness.

        The stamp is not a Git commit, which is inherited rather than introduced here, so every
        inherited row is a ``citation_provenance_invalid`` on the non-multiplicity path. The task
        corrects the document by adding one unrelated citation range: that row is its own and stays
        enforced, while the two it inherited stay inherited. Red before the fix -- document
        dirtiness made all three enforced, so a curator was charged with rows no edit can clear.
        """

        card = self.card(self.ROWS, stamp="zzz", commit=True)
        self.rewrite(card, self.ROWS[1], self.ROWS[1] + "\n" + self.ADDED_ROW)

        result = self.check()

        self.assertEqual(len(result["findings"]), 1, result["findings"])
        self.assertIn("`TAIL`", result["findings"][0]["message"])
        self.assertEqual(result["closeoutOwnedFindings"], [])
        self.assertEqual(len(result["debtFindings"]), 2, result["debtFindings"])
        self.assertEqual(
            {row["code"] for row in result["debtFindings"]}, {"citation_provenance_invalid"}
        )
        self.assertEqual({row["severity"] for row in result["debtFindings"]}, {"warning"})
        for inherited, anchor in zip(result["debtFindings"], ("`VALUE`", "`OTHER`"), strict=True):
            self.assertIn(anchor, inherited["message"])

    def test_a_row_the_task_created_stays_enforced(self) -> None:
        """A card this task wrote has no pre-existing anything, so its rows are the task's own.

        The document is untracked, so the pre-task revision does not carry it at all. Red if the
        demotion ever falls back to reading presence in the working tree.
        """

        self.card(self.ROWS, stamp="zzz", commit=False)

        result = self.check()

        self.assertEqual(len(result["findings"]), 2, result["findings"])
        self.assertEqual(result["debtFindings"], [])
        self.assertFalse(result["ok"])

    def test_a_row_the_task_edited_stays_enforced(self) -> None:
        """Touch it, own it: editing the row makes the row the task's, whatever it said before.

        Red if the demotion degrades to "the document was tracked before", which would let a leaf
        clear an inherited row by editing it and still be told the row is not its problem. The
        untouched sibling row stays inherited debt, so this is a per-ROW decision rather than a
        per-document one in both directions.
        """

        card = self.card(self.ROWS, stamp="zzz", commit=True)
        self.rewrite(card, self.ROWS[0], self.ROWS[0].replace("doubled", "doubled and restated"))

        result = self.check()

        self.assertEqual(len(result["findings"]), 1, result["findings"])
        self.assertIn("`VALUE`", result["findings"][0]["message"])
        self.assertEqual(len(result["debtFindings"]), 1, result["debtFindings"])
        self.assertIn("`OTHER`", result["debtFindings"][0]["message"])


class InsertedRegistrationRangeDriftTests(TreeCase):
    """D-23: a landing's new registration must not bill a pure move as curator debt.

    `mcp/tests/test-evidence-lanes.toml` keeps one member list per lane and `mcp/tests/
    evidence-lifecycle.toml` keeps a table per contract, so a registration inserted anywhere but
    the end of its list moves every row below it. Cards cite those rows by line, so one landing
    staled **90** citations tree-wide at L19 -- 74 already stale before the leaf's own rows and 16
    shifted by them -- and every one of those rows landed in the curator's repairable set although
    the anchor was still exactly where it always was, one line down.
    """

    REGISTRY = (
        "[files]\n"
        "unit-regression = [\n"
        '  "mcp/tests/test_alpha.py",\n'
        '  "mcp/tests/test_beta.py",\n'
        "]\n"
    )
    ROW = '| The beta row. | "mcp/tests/test_beta.py" | mcp/tests/test-evidence-lanes.toml:4-4 |'

    def registry(self, body: str) -> None:
        self.tree.memory_file("mcp/tests/test-evidence-lanes.toml", body)

    def test_an_inserted_registration_is_reported_as_a_stale_range_not_curator_work(self) -> None:
        """The anchor still resolves once, so the row is a MOVE and never enters ``findings``.

        Red if the classification goes back to billing every absent anchor the same: the curator
        is handed a row whose only defect is that somebody else's landing inserted a line above it,
        and no edit of the claim can clear it.
        """

        self.registry(
            self.REGISTRY.replace("test_beta", "test_inserted", 1).replace(
                '  "mcp/tests/test_inserted.py",\n',
                '  "mcp/tests/test_inserted.py",\n  "mcp/tests/test_beta.py",\n',
            )
        )
        self.tree.card("mcp/tests/test_beta.py", self.ROW)

        result = self.tree.run()

        self.assertEqual(result["findings"], [], result["findings"])
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["reportOnlyFindings"]), 1, result["reportOnlyFindings"])
        reported = result["reportOnlyFindings"][0]
        self.assertEqual(reported["code"], "citation_anchor_absent_from_range")
        self.assertTrue(reported["reportOnly"])
        self.assertIn("STALE BY A MOVE", reported["message"])

    def test_an_anchor_that_moved_out_of_the_file_stays_enforced(self) -> None:
        """A row whose anchor is gone from the cited file is a broken claim, not a stale range.

        The registry carries no ``test_beta.py`` row at all, so the anchor resolves NOWHERE. This
        is the control that keeps the classification from swallowing the class it exists to
        separate: red if 'resolves nowhere' is ever treated as a move.
        """

        self.registry(self.REGISTRY.replace('  "mcp/tests/test_beta.py",\n', ""))
        self.tree.card("mcp/tests/test_beta.py", self.ROW)

        result = self.tree.run()

        self.assertEqual(result["reportOnlyFindings"], [])
        self.assertEqual(self.codes(result), ["citation_anchor_absent_from_range"])
        self.assertFalse(result["ok"])

    def test_an_ambiguous_anchor_is_a_broken_citation_rather_than_a_moved_one(self) -> None:
        """Two rows naming the module are not a pointer that moved; they are an ambiguity.

        Red if the classification accepts anything but EXACTLY ONE resolved construct: a projection
        that "repaired" an ambiguous row would pick one of two sites on the curator's behalf, which
        is the guess the check exists to refuse.
        """

        self.registry(
            "[files]\n"
            "unit-regression = [\n"
            '  "mcp/tests/test_beta.py",\n'
            '  "mcp/tests/test_gamma.py",\n'
            '  "mcp/tests/test_beta.py",\n'
            "]\n"
        )
        self.tree.card("mcp/tests/test_beta.py", self.ROW)

        result = self.tree.run()

        self.assertEqual(result["reportOnlyFindings"], [])
        self.assertEqual(self.codes(result), ["citation_anchor_absent_from_range"])

    def test_the_shipped_registries_append_point_is_the_end_of_its_own_list(self) -> None:
        """Half (a): appending at that point leaves every earlier row of the list at its line.

        A citation into those rows cannot be staled by the next registration, which is what
        "append at the end of its lane list or table" buys. This case appends a synthetic row to
        the SHIPPED manifest's first list and re-parses it, so it measures the real file rather
        than a fixture: red if the list stops being a shape whose end is a line-stable append
        point, and red for the mid-list insertion it contrasts with.
        """

        manifest = (REPO_ROOT / "mcp/tests/test-evidence-lanes.toml").read_text(encoding="utf-8")
        before = tomllib.loads(manifest)["files"]["unit-regression"]
        original = manifest.splitlines()
        lines_of = {row: original.index(f'  "{row}",') + 1 for row in before}

        appended = manifest.replace(
            f'  "{before[-1]}",\n',
            f'  "{before[-1]}",\n  "mcp/tests/test_appended_registration.py",\n',
            1,
        )
        after = tomllib.loads(appended)["files"]["unit-regression"]
        shifted = appended.splitlines()
        for row, line in lines_of.items():
            self.assertEqual(shifted.index(f'  "{row}",') + 1, line, row)
        self.assertEqual(after[:-1], before)

        inserted = manifest.replace(
            f'  "{before[-1]}",\n',
            f'  "mcp/tests/test_inserted_registration.py",\n  "{before[-1]}",\n',
            1,
        ).splitlines()
        self.assertEqual(
            inserted.index(f'  "{before[-1]}",') + 1,
            lines_of[before[-1]] + 1,
            "the contrast case must move a row, or this case proves nothing",
        )


class DecoratedDeclarationCitationTests(TreeCase):
    """A card citing a decorated declaration at ITS OWN lines must not reopen against itself.

    ``grammars`` widens a definition's extent outwards through ``decorated_definition`` so the
    range covers the decorator, which is deliberate: a projected range should quote the whole
    construct. What D-21 measured is the consequence for the reopen rule -- an extent whose start
    is the DECORATOR line (57 for a declaration at 58-85) made ``:58-83`` reopen while ``:57-83``
    passed, so three curator seats learned to include the decorator line instead of reporting the
    phantom. The rule this class pins: coverage is judged against the declaration's own line, and
    a range that begins inside the construct still reopens.
    """

    DECORATED = "import functools\n\n\n@functools.cache\ndef build():\n    return 1\n"
    CHANGED = "import functools\n\n\n@functools.cache\ndef build():\n    return 2\n"

    def git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return result.stdout.strip()

    def setUp(self) -> None:
        super().setUp()
        for root in (self.tree.code, self.tree.memory):
            self.git(root, "init", "--quiet")
            self.git(root, "config", "user.email", "fixture@example.invalid")
            self.git(root, "config", "user.name", "Fixture")
        (self.tree.memory / "memory.md").write_text("# Memory\n", encoding="utf-8")
        self.git(self.tree.memory, "add", "memory.md")
        self.git(self.tree.memory, "commit", "--quiet", "-m", "init")

    def card(self, citation: str) -> str:
        """Commit the declaration, change it, and cite it at ``citation``."""
        self.tree.source("src.py", self.DECORATED)
        self.git(self.tree.code, "add", "src.py")
        self.git(self.tree.code, "commit", "--quiet", "-m", "declaration")
        stamp = self.git(self.tree.code, "rev-parse", "HEAD")
        self.tree.source("src.py", self.CHANGED)
        self.tree.memory_file(
            "onboarding/src.py.md",
            "\n".join(
                (
                    "# src.py",
                    "",
                    "| Field | Value |",
                    "| --- | --- |",
                    f"| lastVerifiedCommitHash | `{stamp}` |",
                    "| lastVerifiedCommitDate | 2026-09-10 |",
                    "",
                    "## Repo-Internal References",
                    "",
                    "| Finding | Anchor | Source |",
                    "| --- | --- | --- |",
                    f"| Builds the cached value | `build` | {citation} |",
                    "",
                )
            ),
        )
        return claim_reopen.check_onboarding_root(self.tree.onboarding, self.tree.code)

    def test_citing_the_declarations_own_lines_is_current_not_a_reopen(self) -> None:
        """The decorator at 4 is not the declaration; the declaration's own lines are 5-6.

        Red if coverage goes back to the widened extent's start: the row returns to ``findings``
        as enforced curator debt for a citation that is exactly right.
        """

        result = self.card("src.py:5-6")
        self.assertEqual([one["code"] for one in result["findings"]], [])
        self.assertEqual(
            [one["code"] for one in result["surfacedFindings"]], ["citation_claim_reopened"]
        )

    def test_including_the_decorator_line_still_reads_as_current(self) -> None:
        """The range a curator learned to write keeps working, so the fix adds no new demand."""

        result = self.card("src.py:4-6")
        self.assertEqual([one["code"] for one in result["findings"]], [])
        self.assertEqual(
            [one["code"] for one in result["surfacedFindings"]], ["citation_claim_reopened"]
        )

    def test_a_range_that_begins_inside_the_construct_still_reopens(self) -> None:
        """The reopen rule itself is not relaxed: a range below the declaration is not current.

        Red if the comparison stops bounding the range's start, which would make any range that
        merely overlaps the construct -- including one starting in its body -- pass.
        """

        result = self.card("src.py:6-6")
        self.assertEqual([one["code"] for one in result["findings"]], ["citation_claim_reopened"])
        self.assertEqual(result["surfacedFindings"], [])


class GeneratedHistoryInsertionOrderTests(TreeCase):
    """The engine inserts its generated bullet by the INSTANT, not by the block's top.

    A generated bullet is stamped in UTC while the document's own entries may carry another offset,
    so "directly under the heading" and "newest first" are different claims: an entry stamped
    ``+02:00`` can be newer than the bullet (D-27 measured exactly that block --
    ``05:29:42+00:00`` sitting below ``06:05+02:00``). These cases drive the two calls
    ``DocumentTransaction.render`` makes -- ``history_edit`` then ``rewritten`` -- and judge the
    result with the shipped checker plus a direct read of the instants.
    """

    ENTRIES = (
        "- 2026-09-18T06:50+02:00: curator entry A",
        "- 2026-09-18T06:35+02:00: curator entry B",
        "- 2026-09-18T06:05+02:00: curator entry C",
        "- 2026-09-18T05:15+02:00: older entry D",
    )

    def render(self, at: datetime) -> str:
        document = "\n".join(("# card", "", "## Update History", "", *self.ENTRIES, ""))
        lines = document.split("\n")
        heading = deterministic_projection.history_section_line(lines)
        assert heading is not None
        bullet = deterministic_projection.history_bullet(
            at=at,
            snapshot_id="b" * 64,
            extents=(
                deterministic_projection.ResolvedExtentInfo(
                    anchor="`build_route_indexes`",
                    path="kernel/build.py",
                    start=1,
                    end=2,
                    kind="definition",
                ),
            ),
        )
        site, text = deterministic_projection.history_edit(lines, heading, [bullet])
        return "\n".join(rewritten(lines, [(site, text)]))

    def bullets(self, rendered: str) -> list[str]:
        return [line for line in rendered.splitlines() if line.startswith("- ")]

    def instants(self, rendered: str) -> list[datetime]:
        stamps: list[datetime] = []
        for bullet in self.bullets(rendered):
            parsed = history_order.parse_timestamp(bullet[2:])
            assert parsed is not None, bullet
            stamps.append(history_order.datetime_value(parsed))
        return stamps

    def test_a_bullet_older_than_the_blocks_entries_is_inserted_below_them(self) -> None:
        """04:00:00Z is 06:00+02:00, so it belongs BELOW the 06:05+02:00 entry, not above it.

        Red before the fix: the bullet was always inserted directly under the heading, so the
        block read ``04:00Z`` above ``06:05+02:00`` and the checker reported
        ``update_history_not_newest_first`` on a document nobody edited wrongly.
        """

        rendered = self.render(datetime(2026, 9, 18, 4, 0, 0, tzinfo=UTC))
        stamps = self.instants(rendered)
        self.assertEqual(stamps, sorted(stamps, reverse=True), rendered)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "onboarding").mkdir()
            (root / "onboarding" / "card.md").write_text(rendered, encoding="utf-8")
            result = history_order.check_onboarding_root(root / "onboarding")
        self.assertEqual(result["findings"], [])
        self.assertTrue(result["ok"])

    def test_a_bullet_newer_than_the_block_is_still_inserted_at_the_top(self) -> None:
        """07:29:42+02:00 is the newest instant, so the common case is unchanged.

        This is the control for the case above: it must stay green, or the fix would have moved a
        genuinely newest bullet out of the position the section's own order demands.
        """

        rendered = self.render(datetime(2026, 9, 18, 5, 29, 42, tzinfo=UTC))
        stamps = self.instants(rendered)
        self.assertEqual(stamps, sorted(stamps, reverse=True), rendered)
        self.assertIn("Generated citation repair", self.bullets(rendered)[0])


class MechanicallyProjectedRangeTests(TreeCase):
    """A range the mechanical repair wrote is not evidence that the citation is current.

    The currency test -- the anchor resolves exactly once and some cited range still holds the
    changed construct's declaration line -- is satisfied BY CONSTRUCTION by a projected range,
    because the projection chose the declaration it wrote. Production recorded where that ends:
    a claim about two adjacent tool names was rebound by ``--fix`` to the registrar definitions
    that declared them, recorded ``No content impact: ... claim bytes unchanged``, and the
    review item then asserted the citation was current -- the one question whose answer cannot
    repair the damage. The generated Update History bullet is the only surviving record of HOW
    the range arrived, so the item reads it and asks the support question instead.

    That item is ENFORCED, not surfaced: a mechanically projected range is unverified evidence,
    nothing records whether anyone reviewed it, and this check cannot prove that a review
    happened. So it forces an explicit disposition instead of offering an ignorable note. The
    ordinary (non-projected) evidence-change item below keeps its warning, because there the
    currency test IS evidence.
    """

    PROJECTED_AT = datetime(2026, 9, 11, 8, 30, tzinfo=UTC)
    CARD = "onboarding/current.md"

    def setUp(self) -> None:
        super().setUp()
        self.git(self.tree.code, "init", "--quiet")
        self.git(self.tree.code, "config", "user.email", "fixture@example.invalid")
        self.git(self.tree.code, "config", "user.name", "Fixture")

    def git(self, root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=root, text=True, capture_output=True, check=False
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)
        return result.stdout.strip()

    def commit(self) -> str:
        self.tree.source("kernel/build.py", "def build_route_indexes():\n    return 1\n")
        self.git(self.tree.code, "add", "kernel/build.py")
        self.git(self.tree.code, "commit", "--quiet", "-m", "stamp")
        return self.git(self.tree.code, "rev-parse", "HEAD")

    def card(self, stamp: str, history: str = "- 2026-08-01T00:00:00+00:00: Original.") -> Path:
        return self.tree.memory_file(
            self.CARD,
            "\n".join(
                (
                    "# Current",
                    "",
                    "| Field | Value |",
                    "| --- | --- |",
                    f"| lastVerifiedCommitHash | `{stamp}` |",
                    "| lastVerifiedCommitDate | 2026-09-10 |",
                    "",
                    "## Repo-Internal References",
                    "",
                    "| Finding | Anchor | Source |",
                    "| --- | --- | --- |",
                    "| The census build. | `build_route_indexes` | kernel/build.py:1-2 |",
                    "",
                    "## Update History",
                    "",
                    history,
                    "",
                )
            ),
        )

    def projection_bullet(self, card: Path) -> str:
        """The real generated bullet for this card's own anchors, exactly as `--fix` writes it."""
        _lines, claims = claim_reopen.claims_in(card)
        return deterministic_projection.history_bullet(
            at=self.PROJECTED_AT,
            snapshot_id="a" * 64,
            extents=tuple(
                deterministic_projection.ResolvedExtentInfo(
                    anchor=anchor.written,
                    path="kernel/build.py",
                    start=1,
                    end=2,
                    kind="definition",
                )
                for anchor in claims[0].anchors
            ),
        )

    def changed_construct(self) -> None:
        """The body changed while its declaration line, and the cited range, held still."""
        self.tree.source("kernel/build.py", "def build_route_indexes():\n    return 2\n")

    def test_a_projected_range_is_enforced_with_the_support_question_not_currency(self) -> None:
        stamp = self.commit()
        card = self.card(stamp)
        bullet = self.projection_bullet(card)
        self.card(stamp, bullet)
        self.changed_construct()

        result = claim_reopen.check_onboarding_root(self.tree.onboarding, self.tree.code)

        # The projected range is UNVERIFIED EVIDENCE, so it stays in the gated set rather than
        # dropping into the report-only bucket: a curator may not read it and still pass.
        self.assertEqual(result["surfacedFindings"], [], result["findings"])
        self.assertEqual(len(result["findings"]), 1, result["findings"])
        enforced = result["findings"][0]
        message = enforced["message"]
        self.assertEqual(enforced["code"], "citation_claim_reopened")
        self.assertEqual(enforced["severity"], "error")
        self.assertFalse(result["ok"], result["findings"])
        # The memory root here is not a Git tree, so the pre-existing-debt demotion has no
        # view to demote from and returns every finding enforced -- the fail-closed direction.
        # Nothing about that path can swallow this item either: it demotes ``INVALID`` alone.
        self.assertIsNone(claim_reopen._modified_onboarding_paths(self.tree.memory))
        self.assertNotIn("the citation is current", message)
        self.assertIn("NOT shown to be current", message)
        self.assertIn(bullet, message)
        self.assertIn("it now reads kernel/build.py:1-2", message)
        self.assertIn("does the construct the new range covers support", message)
        self.assertIn("mechanical anchor-range projection", message)
        self.assertIn("re-cite the location the claim is about", message)
        self.assertIn("only then advance the stamp", message)

    def test_history_without_this_claims_bullet_keeps_the_currency_assertion(self) -> None:
        stamp = self.commit()
        card = self.card(stamp)
        other = self.projection_bullet(card).replace("`build_route_indexes`", "`some_other_name`")
        for history in ("- 2026-08-01T00:00:00+00:00: Original.", other):
            with self.subTest(history=history):
                self.card(stamp, history)
                self.changed_construct()

                result = claim_reopen.check_onboarding_root(self.tree.onboarding, self.tree.code)

                self.assertEqual(len(result["surfacedFindings"]), 1, result["findings"])
                # The ordinary evidence-change item is unchanged: report-only, severity warning.
                self.assertEqual(result["surfacedFindings"][0]["severity"], "warning")
                self.assertEqual(result["findings"], [], result["findings"])
                message = result["surfacedFindings"][0]["message"]
                self.assertIn("the citation is current", message)
                self.assertNotIn("NOT shown to be current", message)


if __name__ == "__main__":
    unittest.main()
