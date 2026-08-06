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
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    ledger_to_text,
)
from agents_remember.memory_quality.check import STYLE_CHECKS
from agents_remember.memory_quality.style.citations import (
    claim_change_router,
    claim_reopen,
    provenance,
)


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

    def run(self) -> dict[str, object]:
        return claim_reopen.check_onboarding_root(self.onboarding, self.code)


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

    def test_a_source_absent_at_the_stamp_is_invalid_provenance(self) -> None:
        self.tree.code_file("README.md", "baseline\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.code_file("pkg/later.py", "def later():\n    return 1\n")
        self.tree.card(
            "pkg/later.py",
            ["| Added later. | `later` | pkg/later.py:1-2 |"],
            last_verified=baseline,
        )

        result = self.tree.run()

        self.assertEqual(self.codes(result), ["citation_provenance_invalid"])
        self.assertIn("did not exist", result["findings"][0]["message"])  # type: ignore[index]

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


class RegistrationAndLimitsTests(unittest.TestCase):
    def test_the_change_detector_is_in_the_memory_quality_gate(self) -> None:
        self.assertIn(claim_reopen.CHECK_NAME, STYLE_CHECKS)

    def test_the_known_dishonest_stamp_limit_is_explicit(self) -> None:
        self.assertIn("dishonest", (claim_reopen.__doc__ or "").lower())

    def test_parsed_source_revisions_are_bounded_after_the_measured_rss_spike(self) -> None:
        views = claim_reopen.SourceViews()
        anchor = claim_reopen.model.Anchor(kind=claim_reopen.model.QUOTE, text="value")
        for index in range(claim_reopen.SOURCE_VIEW_CACHE_LIMIT + 2):
            citation = claim_reopen.model.Citation(
                text=f"source{index}.toml:1-1",
                path=f"source{index}.toml",
                start=1,
                end=1,
            )
            source = claim_reopen.LocalSource(
                citation=citation,
                kind="code commit",
                current=["value"],
                historical=["value"],
                provenance_label="code commit abcdef0",
            )
            views.candidates(anchor, source, historical=False)

        self.assertEqual(len(views.cache), claim_reopen.SOURCE_VIEW_CACHE_LIMIT)


class ChangeRoutingTests(ChangeDetectionCase):
    def baseline(self, relative: str = "pkg/stable.py") -> str:
        self.tree.code_file(relative, "def stable():\n    return 1\n")
        return self.tree.commit(self.tree.code, "baseline")

    def test_multiple_cards_and_stamps_share_one_working_census_and_object_delta_per_stamp(
        self,
    ) -> None:
        first = self.baseline()
        self.tree.code_file("pkg/unrelated.py", "value = 1\n")
        second = self.tree.commit(self.tree.code, "unrelated")
        self.tree.card(
            "cards/first.py",
            ["| Stable. | `stable` | pkg/stable.py:1-2 |"],
            last_verified=first,
        )
        self.tree.card(
            "cards/second.py",
            ["| Stable. | `stable` | pkg/stable.py:1-2 |"],
            last_verified=second,
        )
        calls: list[tuple[str, ...]] = []
        original = claim_change_router.run_git

        def observed(
            root: Path,
            args: list[str],
            *,
            work_dir: Path | None = None,
            input_text: str | None = None,
            timeout: float,
        ):
            calls.append(tuple(args))
            return original(
                root,
                args,
                work_dir=work_dir,
                input_text=input_text,
                timeout=timeout,
            )

        with (
            mock.patch.object(claim_change_router, "run_git", new=observed),
            mock.patch.object(
                provenance.GitHistory,
                "file",
                side_effect=AssertionError("unchanged route read a historical blob"),
            ),
            mock.patch.object(
                claim_reopen.SourceViews,
                "candidates",
                side_effect=AssertionError("unchanged route built structural views"),
            ),
        ):
            result = self.tree.run()

        self.assert_clean(result)
        self.assertEqual(sum(call[0] == "status" for call in calls), 1)
        self.assertEqual(sum(call[0] == "ls-tree" for call in calls), 1)
        diff_calls = [call for call in calls if call[0] == "diff-tree"]
        self.assertEqual(len(diff_calls), 2)
        self.assertEqual({call[-2] for call in diff_calls}, {first, second})
        routing = result["changeRouting"]
        self.assertEqual(routing["localClaimsProvenUnchanged"], 2)  # type: ignore[index]
        code = routing["repositories"]["code"]  # type: ignore[index]
        self.assertEqual(code["workingTreeCensuses"], 1)
        self.assertEqual(code["historicalComparisons"], 2)

    def test_unrelated_dirty_path_skips_history_and_structural_parsing(self) -> None:
        baseline = self.baseline()
        self.tree.card(
            "cards/stable.py",
            ["| Stable. | `stable` | pkg/stable.py:1-2 |"],
            last_verified=baseline,
        )
        self.tree.code_file("pkg/unrelated.py", "dirty = 1\n")
        with (
            mock.patch.object(
                provenance.GitHistory,
                "file",
                side_effect=AssertionError("unrelated dirt triggered history"),
            ),
            mock.patch.object(
                claim_reopen.SourceViews,
                "candidates",
                side_effect=AssertionError("unrelated dirt triggered structural parsing"),
            ),
        ):
            result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(result["changeRouting"]["localClaimsProvenUnchanged"], 1)  # type: ignore[index]

    def test_abbreviated_card_stamp_routes_with_the_fully_resolved_commit(self) -> None:
        baseline = self.baseline()
        self.tree.card(
            "cards/stable.py",
            ["| Stable. | `stable` | pkg/stable.py:1-2 |"],
            last_verified=baseline[:8],
        )
        calls: list[tuple[str, ...]] = []
        original = claim_change_router.run_git

        def observed(
            root: Path,
            args: list[str],
            *,
            work_dir: Path | None = None,
            input_text: str | None = None,
            timeout: float,
        ):
            calls.append(tuple(args))
            return original(
                root,
                args,
                work_dir=work_dir,
                input_text=input_text,
                timeout=timeout,
            )

        with mock.patch.object(claim_change_router, "run_git", new=observed):
            result = self.tree.run()
        self.assert_clean(result)
        diff = next(call for call in calls if call[0] == "diff-tree")
        self.assertEqual(diff[-2], baseline)
        self.assertNotEqual(diff[-2], baseline[:8])

    def test_relevant_dirty_shapes_all_route_to_semantic_authority(self) -> None:
        mutations = {
            "unstaged": lambda path: path.write_text(
                "def stable():\n    return 2\n", encoding="utf-8"
            ),
            "staged": lambda path: (
                path.write_text("def stable():\n    return 2\n", encoding="utf-8"),
                git(self.tree.code, "add", path.relative_to(self.tree.code).as_posix()),
            ),
            "deleted": lambda path: path.unlink(),
            "renamed": lambda path: git(
                self.tree.code,
                "mv",
                path.relative_to(self.tree.code).as_posix(),
                "pkg/renamed.py",
            ),
            "mode": lambda path: path.chmod(0o755),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                self.tree = ProvenanceTree(Path(temporary))
                path = self.tree.code_file("pkg/stable.py", "def stable():\n    return 1\n")
                baseline = self.tree.commit(self.tree.code, "baseline")
                self.tree.card(
                    "cards/stable.py",
                    ["| Stable. | `stable` | pkg/stable.py:1-2 |"],
                    last_verified=baseline,
                )
                mutate(path)
                calls: list[str] = []
                original = claim_change_router.run_git

                def observed(
                    root: Path,
                    args: list[str],
                    calls: list[str] = calls,
                    original=original,
                    **kwargs: object,
                ):
                    calls.append(args[0])
                    return original(root, args, **kwargs)  # type: ignore[arg-type]

                with mock.patch.object(claim_change_router, "run_git", new=observed):
                    result = self.tree.run()
                routing = result["changeRouting"]
                self.assertEqual(routing["localClaimsSemanticRequired"], 1)  # type: ignore[index]
                self.assertEqual(routing["localClaimsProvenUnchanged"], 0)  # type: ignore[index]
                self.assertEqual(calls, ["status"])

    def test_untracked_and_ignored_local_paths_are_never_proven_unchanged(self) -> None:
        for name, ignored in (("untracked", False), ("ignored", True)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                self.tree = ProvenanceTree(Path(temporary))
                self.tree.code_file("pkg/base.py", "value = 1\n")
                if ignored:
                    self.tree.code_file(".gitignore", "generated/\n")
                baseline = self.tree.commit(self.tree.code, "baseline")
                self.tree.code_file("generated/local.py", "def local():\n    return 1\n")
                self.tree.card(
                    "cards/local.py",
                    ["| Local. | `local` | generated/local.py:1-2 |"],
                    last_verified=baseline,
                )
                calls: list[str] = []
                original = claim_change_router.run_git

                def observed(
                    root: Path,
                    args: list[str],
                    calls: list[str] = calls,
                    original=original,
                    **kwargs: object,
                ):
                    calls.append(args[0])
                    return original(root, args, **kwargs)  # type: ignore[arg-type]

                with mock.patch.object(claim_change_router, "run_git", new=observed):
                    result = self.tree.run()
                self.assertEqual(self.codes(result), [claim_reopen.INVALID])
                routing = result["changeRouting"]
                self.assertEqual(routing["localClaimsSemanticRequired"], 1)  # type: ignore[index]
                self.assertEqual(routing["localClaimsProvenUnchanged"], 0)  # type: ignore[index]
                self.assertEqual(calls, ["status", "ls-tree"] if ignored else ["status"])

    def test_git_census_failure_is_explicit_and_never_runs_semantic_fallback(self) -> None:
        baseline = self.baseline()
        self.tree.card(
            "cards/stable.py",
            ["| Stable. | `stable` | pkg/stable.py:1-2 |"],
            last_verified=baseline,
        )

        original = claim_change_router.run_git

        def routed(
            root: Path,
            args: list[str],
            *,
            work_dir: Path | None = None,
            input_text: str | None = None,
            timeout: float,
        ):
            if args[0] == "status":
                return SimpleNamespace(returncode=1, stdout="", stderr="status exploded")
            return original(
                root,
                args,
                work_dir=work_dir,
                input_text=input_text,
                timeout=timeout,
            )

        with (
            mock.patch.object(claim_change_router, "run_git", new=routed),
            mock.patch.object(
                claim_reopen.SourceViews,
                "candidates",
                side_effect=AssertionError("routing error fell back to structural parsing"),
            ),
        ):
            result = self.tree.run()
        self.assertEqual(self.codes(result), [claim_reopen.INVALID])
        self.assertIn("status exploded", result["findings"][0]["message"])  # type: ignore[index]
        self.assertEqual(result["changeRouting"]["routingErrors"], 1)  # type: ignore[index]

    def test_unchanged_local_plus_dependency_still_reads_exact_dependency_version(self) -> None:
        self.tree.code_file("pkg/stable.py", "def stable():\n    return 1\n")
        self.tree.code_file("mcp/requirements.txt", "uvicorn==0.30.6\n")
        baseline = self.tree.commit(self.tree.code, "baseline")
        self.tree.card(
            "cards/mixed.py",
            ['| Mixed. | `stable`; "import string" | pkg/stable.py:1-2; uvicorn/main.py:1-2 |'],
            last_verified=baseline,
        )
        reads: list[str] = []
        original = provenance.GitHistory.file

        def read(history: provenance.GitHistory, commit: str, path: str):
            reads.append(path)
            return original(history, commit, path)

        with mock.patch.object(provenance.GitHistory, "file", new=read):
            result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(reads, [provenance.REQUIREMENTS_PATH])
        self.assertEqual(result["changeRouting"]["localHistoricalBlobReadsSkipped"], 1)  # type: ignore[index]

    def test_code_memory_and_dependency_route_against_their_own_histories(self) -> None:
        self.tree.code_file("pkg/stable.py", "def stable():\n    return 1\n")
        self.tree.code_file("mcp/requirements.txt", "uvicorn==0.30.6\n")
        code_commit = self.tree.commit(self.tree.code, "code baseline")
        self.tree.memory_file("notes/fact.md", "# Fact\n\nMemory truth.\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(code_commit, memory_commit)
        self.tree.card(
            "cards/all.py",
            [
                '| All. | `stable`; "Memory truth"; "import string" '
                "| pkg/stable.py:1-2; notes/fact.md:1-3; uvicorn/main.py:1-2 |"
            ],
            last_verified=code_commit,
        )
        result = self.tree.run()
        self.assert_clean(result)
        routing = result["changeRouting"]
        self.assertEqual(routing["localHistoricalBlobReadsSkipped"], 2)  # type: ignore[index]
        self.assertEqual(routing["repositories"]["code"]["workingTreeCensuses"], 1)  # type: ignore[index]
        self.assertEqual(routing["repositories"]["memory"]["workingTreeCensuses"], 1)  # type: ignore[index]

    def test_memory_ledger_mapping_is_loaded_once_per_resolved_code_commit(self) -> None:
        self.tree.code_file("pkg/base.py", "value = 1\n")
        code_commit = self.tree.commit(self.tree.code, "code baseline")
        self.tree.memory_file("notes/fact.md", "# Fact\n\nMemory truth.\n")
        memory_commit = self.tree.commit(self.tree.memory, "memory baseline")
        self.tree.map_memory(code_commit, memory_commit)
        for name in ("first", "second"):
            self.tree.card(
                f"cards/{name}.py",
                [f'| {name}. | "Memory truth" | notes/fact.md:1-3 |'],
                last_verified=code_commit,
            )
        calls = 0
        original = provenance.Histories.memory_commit

        def mapped(histories: provenance.Histories, commit: str):
            nonlocal calls
            calls += 1
            return original(histories, commit)

        with mock.patch.object(provenance.Histories, "memory_commit", new=mapped):
            result = self.tree.run()
        self.assert_clean(result)
        self.assertEqual(calls, 1)
        self.assertEqual(result["changeRouting"]["localHistoricalBlobReadsSkipped"], 2)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
