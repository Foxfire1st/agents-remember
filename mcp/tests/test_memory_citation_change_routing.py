"""Registration, limits, and repository-routing coverage for citation change checks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.memory_quality.check import STYLE_CHECKS
from agents_remember.memory_quality.style.citations import (
    claim_change_router,
    claim_reopen,
    provenance,
)
from test_memory_citation_change_detection import ChangeDetectionCase, ProvenanceTree, git


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
                # The new-file rule (developer correction): an untracked/ignored source that
                # did not exist at the stamp and resolves exactly once inside the cited range
                # is the report-only surface -- never "proven unchanged", but not invalid.
                self.assertEqual(self.codes(result), [])
                surfaced = result["surfacedFindings"]
                assert isinstance(surfaced, list)
                self.assertEqual(len(surfaced), 1)
                self.assertEqual(surfaced[0]["code"], claim_reopen.REOPENED)
                self.assertIn("did not exist", surfaced[0]["message"])
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
