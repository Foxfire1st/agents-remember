"""Per-claim change-detection bites over real code, memory, and dependency history."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    ledger_to_text,
)
from agents_remember.memory_quality.style.citations import claim_reopen


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", repo.as_posix(), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


class ProvenanceTree:
    def __init__(self, root: Path) -> None:
        self.code = root / "code"
        self.memory = root / "memory"
        self.onboarding = self.memory / "onboarding"
        for repository in (self.code, self.memory):
            repository.mkdir(parents=True)
            git(repository, "init")
            git(repository, "config", "user.email", "agents-remember@example.invalid")
            git(repository, "config", "user.name", "Agents Remember")
        self.onboarding.mkdir(parents=True)

    def write(self, root: Path, relative: str, body: str) -> Path:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        return target

    def code_file(self, relative: str, body: str) -> Path:
        return self.write(self.code, relative, body)

    def memory_file(self, relative: str, body: str) -> Path:
        return self.write(self.memory, relative, body)

    def commit(self, repository: Path, message: str) -> str:
        git(repository, "add", "-A")
        git(repository, "commit", "-m", message)
        return git(repository, "rev-parse", "HEAD")

    def card(
        self,
        relative: str,
        rows: list[str],
        *,
        last_verified: str | None,
    ) -> Path:
        metadata = (
            [] if last_verified is None else [f"| lastVerifiedCommitHash | `{last_verified}` |"]
        )
        body = "\n".join(
            [
                f"# {relative}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                "| repository | agents-remember |",
                f"| path | `{relative}` |",
                "| doc_type | `file-level-onboarding` |",
                *metadata,
                "",
                "## Repo-Internal References",
                "",
                "| Finding | Anchor | Source |",
                "| --- | --- | --- |",
                *rows,
                "",
            ]
        )
        return self.write(self.onboarding, f"{relative}.md", body)

    def map_memory(self, code_commit: str, memory_commit: str) -> None:
        ledger = create_initial_ledger("agents-remember", code_commit, memory_commit)
        self.memory_file("memory.md", ledger_to_text(ledger))
        self.commit(self.memory, "record ledger")

    def run(self, *, unstamped_code_commit: str | None = None) -> dict[str, object]:
        return claim_reopen.check_onboarding_root(
            self.onboarding,
            self.code,
            unstamped_code_commit=unstamped_code_commit,
        )


class ChangeDetectionCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.tree = ProvenanceTree(Path(self._temporary.name))

    def codes(self, result: dict[str, object]) -> list[str]:
        return [one["code"] for one in result["findings"]]  # type: ignore[index]

    def assert_clean(self, result: dict[str, object]) -> None:
        self.assertTrue(result["ok"], result["findings"])
        self.assertEqual(result["findingCount"], 0)


class CodeProvenanceTests(ChangeDetectionCase):
    def baseline(self) -> str:
        self.tree.code_file(
            "pkg/rules.py",
            "def stable(\n    value: int,\n) -> int:\n    return value + 1\n\n"
            "def changing(value: int) -> int:\n    return value + 1\n",
        )
        return self.tree.commit(self.tree.code, "baseline")

    def test_format_only_reflow_does_not_reopen_a_claim(self) -> None:
        baseline = self.baseline()
        self.tree.card(
            "pkg/rules.py",
            ["| Stable behaviour. | `stable` | pkg/rules.py:1-4 |"],
            last_verified=baseline,
        )
        self.tree.code_file(
            "pkg/rules.py",
            "def stable(value: int) -> int:\n    return value + 1\n\n"
            "def changing(value: int) -> int:\n    return value + 1\n",
        )

        self.assert_clean(self.tree.run())

    def test_only_the_claim_whose_construct_changed_is_reopened(self) -> None:
        baseline = self.baseline()
        self.tree.card(
            "pkg/rules.py",
            [
                "| Stable behaviour. | `stable` | pkg/rules.py:1-4 |",
                "| Changing behaviour. | `changing` | pkg/rules.py:6-7 |",
            ],
            last_verified=baseline,
        )
        self.tree.code_file(
            "pkg/rules.py",
            "def stable(value: int) -> int:\n    return value + 1\n\n"
            "def changing(value: int) -> int:\n    return value + 2\n",
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_claim_reopened"])
        finding = result["findings"][0]  # type: ignore[index]
        self.assertEqual(finding["line"], 15)
        self.assertIn("`changing`", finding["message"])
        self.assertNotIn("`stable`", finding["message"])

    def test_an_unrelated_construct_change_in_the_same_file_does_not_reopen(self) -> None:
        baseline = self.baseline()
        self.tree.card(
            "pkg/rules.py",
            ["| Stable behaviour. | `stable` | pkg/rules.py:1-4 |"],
            last_verified=baseline,
        )
        self.tree.code_file(
            "pkg/rules.py",
            "def stable(\n    value: int,\n) -> int:\n    return value + 1\n\n"
            "def changing(value: int) -> int:\n    return value + 999\n",
        )

        self.assert_clean(self.tree.run())

    def test_line_movement_does_not_participate_in_current_anchor_resolution(self) -> None:
        baseline = self.baseline()
        self.tree.card(
            "pkg/rules.py",
            ["| Stable behaviour. | `stable` | pkg/rules.py:1-4 |"],
            last_verified=baseline,
        )
        self.tree.code_file(
            "pkg/rules.py",
            "# unrelated heading\n" * 20
            + "def stable(\n    value: int,\n) -> int:\n    return value + 1\n\n"
            + "def changing(value: int) -> int:\n    return value + 1\n",
        )

        self.assert_clean(self.tree.run())

    def test_typescript_layout_and_comment_reflow_are_not_structural_changes(self) -> None:
        self.tree.code_file(
            "dashboard/stable.ts",
            "export function stable(\n  value: number,\n): number {\n"
            "  // Return the stable value.\n  return value + 1;\n}\n",
        )
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.card(
            "dashboard/stable.ts",
            ["| Stable behaviour. | `stable` | dashboard/stable.ts:1-6 |"],
            last_verified=baseline,
        )
        self.tree.code_file(
            "dashboard/stable.ts",
            "export function stable(value: number): number {\n"
            "  // Return the\n  // stable value.\n  return value + 1\n}\n",
        )

        self.assert_clean(self.tree.run())

    def test_missing_and_invalid_stamps_are_reported_for_every_claim(self) -> None:
        self.baseline()
        rows = [
            "| First. | `stable` | pkg/rules.py:1-4 |",
            "| Second. | `changing` | pkg/rules.py:6-7 |",
        ]
        self.tree.card("pkg/missing.py", rows, last_verified=None)
        self.tree.card("pkg/invalid.py", rows, last_verified="not-a-commit")

        result = self.tree.run()

        self.assertEqual(
            self.codes(result),
            ["citation_provenance_invalid"] * 2 + ["citation_provenance_missing"] * 2,
        )
        self.assertEqual(result["findingCount"], 4)

    def test_closeout_preflight_uses_the_base_only_for_a_dirty_unstamped_card(self) -> None:
        self.tree.code_file("README.md", "baseline\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file("pkg/later.py", "def later():\n    return 1\n")
        self.tree.card(
            "pkg/later.py",
            ["| Added later. | `later` | pkg/later.py:1-2 |"],
            last_verified=None,
        )

        result = self.tree.run(unstamped_code_commit=baseline)

        self.assert_clean(result)
        self.assertEqual(result["surfacedFindings"][0]["code"], "citation_claim_reopened")  # type: ignore[index]

    def test_closeout_preflight_does_not_forgive_committed_unstamped_debt(self) -> None:
        baseline = self.baseline()
        self.tree.card(
            "pkg/rules.py",
            ["| Stable behaviour. | `stable` | pkg/rules.py:1-4 |"],
            last_verified=None,
        )
        self.tree.commit(self.tree.memory, "commit unstamped debt")

        result = self.tree.run(unstamped_code_commit=baseline)

        self.assertEqual(self.codes(result), ["citation_provenance_missing"])

    def test_a_new_source_surfaces_report_only_when_current(self) -> None:
        """Whole new file added after the stamp: once-resolving-in-range is report-only."""
        self.tree.code_file("README.md", "baseline\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file("pkg/later.py", "def later():\n    return 1\n")
        self.tree.card(
            "pkg/later.py",
            ["| Added later. | `later` | pkg/later.py:1-2 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assert_clean(result)
        surfaced = result["surfacedFindings"][0]  # type: ignore[index]
        self.assertEqual(surfaced["code"], "citation_claim_reopened")  # type: ignore[index]
        self.assertEqual(surfaced["severity"], "warning")  # type: ignore[index]
        self.assertIn("did not exist", surfaced["message"])  # type: ignore[index]

    def test_a_new_source_is_enforced_when_stale(self) -> None:
        """Whole new file whose anchor resolves once but outside the cited range: hard."""
        self.tree.code_file("README.md", "baseline\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file("pkg/later.py", "def later():\n    return 1\n")
        self.tree.card(
            "pkg/later.py",
            ["| Added later. | `later` | pkg/later.py:2-2 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_claim_reopened"])
        self.assertIn("did not exist", result["findings"][0]["message"])  # type: ignore[index]

    def test_a_new_source_is_invalid_when_ambiguous(self) -> None:
        """Whole new file whose anchor resolves more than once now: hard invalid."""
        self.tree.code_file("README.md", "baseline\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file("pkg/later.py", "later = 1\n\n\ndef later():\n    return 2\n")
        self.tree.card(
            "pkg/later.py",
            ["| Added later. | `later` | pkg/later.py:1-4 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        self.assertIn(
            "no exact candidate is unique",
            result["findings"][0]["message"],  # type: ignore[index]
        )

    def test_a_new_source_is_invalid_when_absent_from_the_working_tree(self) -> None:
        """Whole new file that never existed at the stamp and is gone now: hard invalid."""
        self.tree.code_file("README.md", "baseline\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.card(
            "pkg/later.py",
            ["| Added later. | `later` | pkg/later.py:1-2 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        self.assertIn(
            "cannot be compared with its verification provenance",
            result["findings"][0]["message"],  # type: ignore[index]
        )

    def test_a_construct_added_after_the_stamp_surfaces_when_current(self) -> None:
        """File existed at the stamp, construct added later: the absent-at-stamp change rule."""
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file("pkg/subject.py", "subject = 1\n\n\ndef added():\n    return 2\n")
        self.tree.memory_file("system/rules.md", "x\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(baseline, memory_commit)
        self.tree.card(
            "pkg/subject.py",
            ["| The new helper. | `added` | pkg/subject.py:4-5 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assert_clean(result)
        surfaced = result["surfacedFindings"][0]  # type: ignore[index]
        self.assertEqual(surfaced["code"], "citation_claim_reopened")  # type: ignore[index]
        self.assertIn("did not exist", surfaced["message"])  # type: ignore[index]

    def test_a_construct_added_after_the_stamp_is_enforced_when_stale(self) -> None:
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file("pkg/subject.py", "subject = 1\n\n\ndef added():\n    return 2\n")
        self.tree.memory_file("system/rules.md", "x\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(baseline, memory_commit)
        self.tree.card(
            "pkg/subject.py",
            ["| The new helper. | `added` | pkg/subject.py:1-1 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_claim_reopened"])
        self.assertIn("did not exist", result["findings"][0]["message"])  # type: ignore[index]

    def test_a_construct_added_after_the_stamp_and_ambiguous_now_is_invalid(self) -> None:
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file(
            "pkg/subject.py",
            "subject = 1\nadded = 1\n\n\ndef added():\n    return 2\n",
        )
        self.tree.memory_file("system/rules.md", "x\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(baseline, memory_commit)
        self.tree.card(
            "pkg/subject.py",
            ["| The new helper. | `added` | pkg/subject.py:4-5 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        self.assertIn("no exact candidate is unique", result["findings"][0]["message"])  # type: ignore[index]

    def test_ambiguous_provenance_in_an_untouched_document_is_debt(self) -> None:
        self.tree.code_file("pkg/dupes.py", "value = 1\n\n\ndef duplicate():\n    return 1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file(
            "pkg/dupes.py", "value = 1\nduplicate = 0\n\n\ndef duplicate():\n    return 1\n"
        )
        self.tree.memory_file("system/rules.md", "x\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(baseline, memory_commit)
        self.tree.card(
            "pkg/dupes.py",
            ["| The helper. | `duplicate` | pkg/dupes.py:1-2 |"],
            last_verified=baseline,
        )
        self.tree.commit(self.tree.memory, "commit the card: the document is untouched now")

        result = self.tree.run()

        self.assert_clean(result)
        self.assertEqual(len(result["debtFindings"]), 1)  # type: ignore[arg-type]
        self.assertEqual(result["debtFindings"][0]["code"], "citation_provenance_invalid")  # type: ignore[index]

    def test_ambiguous_provenance_in_a_touched_document_stays_enforced(self) -> None:
        self.tree.code_file("pkg/dupes.py", "value = 1\n\n\ndef duplicate():\n    return 1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file(
            "pkg/dupes.py", "value = 1\nduplicate = 0\n\n\ndef duplicate():\n    return 1\n"
        )
        self.tree.memory_file("system/rules.md", "x\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(baseline, memory_commit)
        self.tree.card(
            "pkg/dupes.py",
            ["| The helper. | `duplicate` | pkg/dupes.py:1-2 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])

    def test_demote_preexisting_debt_holds_everything_enforced_without_a_git_repo(self) -> None:
        findings = [
            claim_reopen.QualityFinding(
                check=claim_reopen.CHECK_NAME,
                path="a.md",
                line=1,
                severity="error",
                code=claim_reopen.INVALID,
                message="ambiguous",
            )
        ]
        with tempfile.TemporaryDirectory() as not_a_repo:
            enforced, debt = claim_reopen._demote_preexisting_provenance_debt(
                findings, Path(not_a_repo)
            )
        self.assertEqual(len(enforced), 1)
        self.assertEqual(debt, [])

    def test_demote_preexisting_debt_never_demotes_a_missing_stamp(self) -> None:
        findings = [
            claim_reopen.QualityFinding(
                check=claim_reopen.CHECK_NAME,
                path="a.md",
                line=1,
                severity="error",
                code=claim_reopen.MISSING,
                message="no stamp",
            )
        ]
        enforced, debt = claim_reopen._demote_preexisting_provenance_debt(
            findings, self.tree.memory
        )
        self.assertEqual(len(enforced), 1)
        self.assertEqual(debt, [])

    def test_demote_preexisting_debt_follows_renames(self) -> None:
        """A `R old -> new` porcelain row maps the demote judgement to the new path."""
        self.tree.code_file("pkg/dupes.py", "value = 1\n\n\ndef duplicate():\n    return 1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file(
            "pkg/dupes.py", "value = 1\nduplicate = 0\n\n\ndef duplicate():\n    return 1\n"
        )
        self.tree.memory_file("system/rules.md", "x\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(baseline, memory_commit)
        self.tree.card(
            "pkg/dupes.py",
            ["| The helper. | `duplicate` | pkg/dupes.py:1-2 |"],
            last_verified=baseline,
        )
        self.tree.commit(self.tree.memory, "track the card")
        git(
            self.tree.memory,
            "mv",
            "onboarding/pkg/dupes.py.md",
            "onboarding/pkg/renamed.py.md",
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])

    def test_mapping_pending_returns_false_when_head_is_not_in_the_error(self) -> None:
        evaluation = cast(
            claim_reopen.Evaluation,
            SimpleNamespace(
                trees=SimpleNamespace(code_root=self.tree.code, memory_root=self.tree.memory)
            ),
        )
        self.assertFalse(
            claim_reopen._mapping_pending_for_code_head(
                "no ledger mapping for code commit " + "f" * 40, evaluation
            )
        )

    def test_mapping_pending_returns_false_for_an_invalid_ledger(self) -> None:
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        self.tree.commit(self.tree.code, "baseline")
        (self.tree.memory / "memory.md").write_text("not a ledger\n", encoding="utf-8")
        evaluation = cast(
            claim_reopen.Evaluation,
            SimpleNamespace(
                trees=SimpleNamespace(code_root=self.tree.code, memory_root=self.tree.memory)
            ),
        )
        head = git(self.tree.code, "rev-parse", "HEAD")
        self.assertFalse(
            claim_reopen._mapping_pending_for_code_head(
                f"no ledger mapping for code commit {head}", evaluation
            )
        )

    def test_a_mapping_pending_for_the_code_head_is_not_a_fault(self) -> None:
        """A stamp naming the code HEAD with no ledger mapping yet: the interrupted-closeout resume."""
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file("pkg/subject.py", "subject = 2\n")
        head = self.tree.commit(self.tree.code, "second")
        self.tree.memory_file("system/rules.md", "## Rule\n\nKeep one.\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        # The ledger maps the baseline only; HEAD's mapping is the closeout's pending write.
        self.tree.map_memory(baseline, memory_commit)
        self.tree.card(
            "pkg/subject.py",
            ["| The rule. | `## Rule` | system/rules.md:1-3 |"],
            last_verified=head,
        )

        result = self.tree.run()

        self.assert_clean(result)

    def test_a_bogus_anchor_that_never_resolves_is_enforced(self) -> None:
        """before==0 and now==0: the anchor resolves nowhere — stale-pointer finding, not invalid."""
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        # The cited file changes, so the claim pays for semantic evaluation; the anchor still
        # resolves nowhere, in either revision.
        self.tree.code_file("pkg/subject.py", "subject = 1\nother = 2\n")
        self.tree.memory_file("system/rules.md", "x\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(baseline, memory_commit)
        self.tree.card(
            "pkg/subject.py",
            ["| Imaginary helper. | `nonexistent_helper` | pkg/subject.py:1-1 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_claim_reopened"])
        self.assertIn("no longer resolves", result["findings"][0]["message"])  # type: ignore[index]

    def test_a_parentless_commit_object_is_not_code_history(self) -> None:
        baseline = self.baseline()
        tree = git(self.tree.code, "rev-parse", f"{baseline}^{{tree}}")
        dangling = git(self.tree.code, "commit-tree", tree, "-m", "dangling object")
        self.tree.card(
            "pkg/rules.py",
            ["| Stable behaviour. | `stable` | pkg/rules.py:1-4 |"],
            last_verified=dangling,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        message = result["findings"][0]["message"]  # type: ignore[index]
        self.assertIn("code history stamp", message)
        self.assertIn("not reachable", message)

    def test_a_future_commit_object_is_not_current_code_history(self) -> None:
        baseline = self.baseline()
        tree = git(self.tree.code, "rev-parse", f"{baseline}^{{tree}}")
        future = git(
            self.tree.code,
            "commit-tree",
            tree,
            "-p",
            baseline,
            "-m",
            "unreferenced future",
        )
        self.tree.card(
            "pkg/rules.py",
            ["| Stable behaviour. | `stable` | pkg/rules.py:1-4 |"],
            last_verified=future,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        self.assertIn("current code history", result["findings"][0]["message"])  # type: ignore[index]


class MemoryProvenanceTests(ChangeDetectionCase):
    def test_a_memory_relative_source_uses_the_separate_memory_history(self) -> None:
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        code_commit = self.tree.commit(self.tree.code, "code baseline")
        self.tree.memory_file("system/rules.md", "## Rule\n\nKeep one.\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(code_commit, memory_commit)
        self.tree.card(
            "pkg/subject.py",
            ["| The rule. | `## Rule` | system/rules.md:1-3 |"],
            last_verified=code_commit,
        )
        self.tree.memory_file("system/rules.md", "## Rule\n\nKeep two.\n")

        result = self.tree.run()

        # The construct changed AND the citation still covers it: the review surface lands in
        # the surfaced bucket, not in enforced findings, and the memory history proves the diff.
        self.assert_clean(result)
        surfaced = result["surfacedFindings"][0]  # type: ignore[index]
        self.assertEqual(surfaced["code"], "citation_claim_reopened")  # type: ignore[index]
        self.assertIn("memory commit", surfaced["message"])  # type: ignore[index]

    def test_a_missing_code_to_memory_mapping_is_reported(self) -> None:
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        code_commit = self.tree.commit(self.tree.code, "code baseline")
        self.tree.memory_file("system/rules.md", "## Rule\n\nKeep one.\n")
        other_memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory("f" * 40, other_memory_commit)
        self.tree.card(
            "pkg/subject.py",
            ["| The rule. | `## Rule` | system/rules.md:1-3 |"],
            last_verified=code_commit,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        self.assertIn("ledger mapping", result["findings"][0]["message"])  # type: ignore[index]

    def unreachable_memory_result(self, *, parent: bool) -> dict[str, object]:
        self.tree.code_file("pkg/subject.py", "subject = 1\n")
        code_commit = self.tree.commit(self.tree.code, "code baseline")
        self.tree.memory_file("system/rules.md", "## Rule\n\nKeep one.\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        tree = git(self.tree.memory, "rev-parse", f"{memory_commit}^{{tree}}")
        arguments = ["commit-tree", tree]
        if parent:
            arguments.extend(("-p", memory_commit))
        arguments.extend(("-m", "unreachable memory object"))
        unreachable = git(self.tree.memory, *arguments)
        self.tree.map_memory(code_commit, unreachable)
        self.tree.card(
            "pkg/subject.py",
            ["| The rule. | `## Rule` | system/rules.md:1-3 |"],
            last_verified=code_commit,
        )
        return self.tree.run()

    def test_a_parentless_memory_commit_object_is_not_memory_history(self) -> None:
        result = self.unreachable_memory_result(parent=False)

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        message = result["findings"][0]["message"]  # type: ignore[index]
        self.assertIn("memory history stamp", message)
        self.assertIn("not reachable", message)

    def test_a_future_memory_commit_object_is_not_current_memory_history(self) -> None:
        result = self.unreachable_memory_result(parent=True)

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        self.assertIn("current memory history", result["findings"][0]["message"])  # type: ignore[index]


class DependencyProvenanceTests(ChangeDetectionCase):
    def card(self, commit: str, source: str = "tiktoken/load.py:35-53") -> None:
        self.tree.card(
            "pkg/subject.py",
            [f'| Dependency behaviour. | "cache key" | {source} |'],
            last_verified=commit,
        )

    def test_an_unchanged_exact_python_resolution_does_not_reopen(self) -> None:
        self.tree.code_file("mcp/requirements.txt", "tiktoken==0.13.0\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.card(baseline)

        self.assert_clean(self.tree.run())

    def test_a_changed_exact_python_resolution_reopens_the_claim(self) -> None:
        self.tree.code_file("mcp/requirements.txt", "tiktoken==0.13.0\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.card(baseline)
        self.tree.code_file("mcp/requirements.txt", "tiktoken==0.14.0\n")

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_claim_reopened"])
        message = result["findings"][0]["message"]  # type: ignore[index]
        self.assertIn("tiktoken 0.13.0 -> 0.14.0", message)

    def test_a_permissive_python_pin_is_not_a_resolved_version(self) -> None:
        self.tree.code_file("mcp/requirements.txt", "tiktoken>=0.12,<1\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.card(baseline)

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        self.assertIn("permissive", result["findings"][0]["message"])  # type: ignore[index]

    def test_only_one_concrete_pep440_equality_is_resolved_python_provenance(self) -> None:
        self.tree.code_file(
            "mcp/requirements.txt",
            "wildcard==1.2.*\n"
            "composite==1.2,!=1.2.4\n"
            "ranged~=1.2\n"
            'marked==1.2.3; python_version >= "3.11"\n'
            "extra[security]==1.2.3\n"
            "duplicate==1.2.3\n"
            "duplicate==1.2.3\n"
            "arbitrary===1.2.3\n"
            "malformed==not-a-version\n",
        )
        baseline = self.tree.commit(self.tree.code, "non-concrete requirements")
        self.tree.card(
            "pkg/subject.py",
            [
                f'| {package}. | "cache key" | {package}/core.py:1-2 |'
                for package in (
                    "wildcard",
                    "composite",
                    "ranged",
                    "marked",
                    "extra",
                    "duplicate",
                    "arbitrary",
                    "malformed",
                )
            ],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"] * 8)
        findings = result["findings"]
        assert isinstance(findings, list)
        for finding in findings:
            assert isinstance(finding, dict)
            message = finding.get("message")
            assert isinstance(message, str)
            self.assertIn("permissive", message)

    def test_package_lock_records_the_resolved_npm_version(self) -> None:
        lock = {"packages": {"node_modules/jsdom": {"version": "25.0.1"}}}
        self.tree.code_file("dashboard/package-lock.json", json.dumps(lock))
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.card(baseline, "jsdom/lib/api.js:1-4")
        lock["packages"]["node_modules/jsdom"]["version"] = "26.0.0"
        self.tree.code_file("dashboard/package-lock.json", json.dumps(lock))

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_claim_reopened"])
        message = result["findings"][0]["message"]  # type: ignore[index]
        self.assertIn("jsdom 25.0.1 -> 26.0.0", message)

    def test_a_dependency_absent_from_both_lock_surfaces_is_reported(self) -> None:
        self.tree.code_file("README.md", "baseline\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.card(baseline, "unknown-package/source.js:1-4")

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        message = result["findings"][0]["message"]  # type: ignore[index]
        self.assertIn("resolved npm version", message)
        self.assertIn("dashboard/package-lock.json", message)

    def test_a_python_requirement_cannot_certify_a_same_named_js_dependency(self) -> None:
        self.tree.code_file("mcp/requirements.txt", "shared==1.0.0\n")
        baseline = self.tree.commit(self.tree.code, "python lock only")
        self.card(baseline, "shared/lib/api.js:1-4")

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        message = result["findings"][0]["message"]  # type: ignore[index]
        self.assertIn("resolved npm version", message)
        self.assertIn("dashboard/package-lock.json", message)

    def test_an_npm_lock_cannot_certify_a_same_named_python_dependency(self) -> None:
        lock = {"packages": {"node_modules/shared": {"version": "1.0.0"}}}
        self.tree.code_file("dashboard/package-lock.json", json.dumps(lock))
        baseline = self.tree.commit(self.tree.code, "npm lock only")
        self.card(baseline, "shared/api.py:1-4")

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        message = result["findings"][0]["message"]  # type: ignore[index]
        self.assertIn("resolved python version", message)
        self.assertIn("mcp/requirements.txt", message)

    def test_a_missing_python_resolved_surface_is_reported_without_npm_fallback(self) -> None:
        self.tree.code_file("README.md", "baseline\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.card(baseline, "unknown-package/source.py:1-4")

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        message = result["findings"][0]["message"]  # type: ignore[index]
        self.assertIn("resolved python version", message)
        self.assertIn("mcp/requirements.txt", message)

    def test_local_and_dependency_anchors_can_pool_in_one_claim(self) -> None:
        self.tree.code_file("pkg/rules.py", "def stable():\n    return 1\n")
        self.tree.code_file("mcp/requirements.txt", "uvicorn==0.30.6\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.card(
            "pkg/rules.py",
            [
                '| Both sources matter. | `stable`; "import string" '
                "| pkg/rules.py:1-2; uvicorn/main.py:604-607 |"
            ],
            last_verified=baseline,
        )

        self.assert_clean(self.tree.run())
