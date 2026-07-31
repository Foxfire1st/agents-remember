"""Onboarding-integrity verdicts for the states a healthy repository does not reach.

Two checkers own the "is this file's onboarding trustworthy?" question, and both have
verdicts that only appear when something is wrong: a sidecar whose verification stamp is
absent, whose source has been deleted, or whose recorded commit is no longer in history;
a newly added file whose onboarding lives inline in the source rather than beside it, or
under a storage mode neither checker can verify.

Those are the verdicts that matter -- the clean paths are already covered -- and each one
is a distinct instruction to the developer reading the report, so each is pinned to the
classification, trust level and note it produces rather than to "something was returned".
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.kernel.coordination_context_resolver import StorageSettings
from agents_remember.memory_quality.integrity.check_missing_onboarding import (
    check_missing_onboarding,
    missing_onboarding_for_source,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.sidecar import (
    classify_external_onboarding,
)

INLINE_BLOCK = "\n".join(
    [
        '"""',
        "@ar-onboarding",
        "verifiedAt: 2026-07-31T12:00+02:00",
        "@ar-onboarding-end",
        '"""',
    ]
)


def run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


def initialize_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    run_git(repo, ["init"])
    run_git(repo, ["config", "user.email", "agents-remember@example.invalid"])
    run_git(repo, ["config", "user.name", "Agents Remember"])
    (repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
    run_git(repo, ["add", "README.md"])
    run_git(repo, ["commit", "-m", "init"])
    return repo


def head_hash(repo: Path) -> str:
    return run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()


def write_sidecar(
    path: Path,
    *,
    source_path: str,
    last_hash: str,
    last_date: str = "2026-07-31",
    repository: str = "repo",
) -> Path:
    """A file-level onboarding sidecar in the table format the drift checker parses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_path}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repository} |",
                f"| path | `{source_path}` |",
                "| doc_type | `file-level-onboarding` |",
                f"| lastVerifiedCommitHash | `{last_hash}` |",
                f"| lastVerifiedCommitDate | {last_date} |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class ExternalSidecarClassificationTests(unittest.TestCase):
    """``classify_external_onboarding`` verdicts before and around the source diff."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = initialize_repo(self.root)
        self.onboarding = self.root / "onboarding"

    def commit_source(self, relative: str, body: str = "value = 1\n") -> str:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        run_git(self.repo, ["add", relative])
        run_git(self.repo, ["commit", "-m", f"add {relative}"])
        return head_hash(self.repo)

    def test_sidecar_without_a_verification_hash_is_unverified_not_up_to_date(self) -> None:
        """No stamp means nothing was ever verified: the sidecar must not read as clean
        just because its source happens to be unchanged."""
        self.commit_source("src/a.py")
        sidecar = write_sidecar(
            self.onboarding / "src" / "a.py.md", source_path="src/a.py", last_hash=""
        )

        row = classify_external_onboarding(sidecar, self.repo)

        self.assertEqual(row.classification, "missing verification")
        self.assertEqual(row.trust, "medium")
        self.assertEqual(row.affected_sections, "metadata; verification")
        self.assertEqual(row.note, "Missing source path or lastVerifiedCommitHash.")
        self.assertEqual(row.source_file, "src/a.py")
        self.assertEqual(row.storage_mode, "external")

    def test_sidecar_without_a_source_path_is_unverified(self) -> None:
        sidecar = write_sidecar(
            self.onboarding / "src" / "a.py.md",
            source_path="",
            last_hash=head_hash(self.repo),
        )

        row = classify_external_onboarding(sidecar, self.repo)

        self.assertEqual(row.classification, "missing verification")
        self.assertEqual(row.source_file, "")

    def test_sidecar_for_a_deleted_source_is_orphaned_and_untrusted(self) -> None:
        """The source is gone, so nothing in the sidecar can be checked against it: the
        verdict is orphaned at the lowest trust, and every section is suspect."""
        last_hash = self.commit_source("src/gone.py")
        run_git(self.repo, ["rm", "src/gone.py"])
        sidecar = write_sidecar(
            self.onboarding / "src" / "gone.py.md",
            source_path="src/gone.py",
            last_hash=last_hash,
        )

        row = classify_external_onboarding(sidecar, self.repo)

        self.assertEqual(row.classification, "orphaned")
        self.assertEqual(row.trust, "low")
        self.assertEqual(row.affected_sections, "all; source missing")
        self.assertEqual(row.note, "Source file no longer exists.")

    def test_sidecar_stamped_with_a_commit_git_does_not_have_is_drifted(self) -> None:
        """A stamp naming a commit this clone cannot resolve -- a rewritten or never-pushed
        history -- proves nothing, so it is reported as drift rather than trusted."""
        self.commit_source("src/a.py")
        sidecar = write_sidecar(
            self.onboarding / "src" / "a.py.md",
            source_path="src/a.py",
            last_hash="0" * 40,
        )

        row = classify_external_onboarding(sidecar, self.repo)

        self.assertEqual(row.classification, "drifted")
        self.assertEqual(row.trust, "medium")
        self.assertEqual(row.affected_sections, "logic; invariants; metadata")
        self.assertEqual(row.note, "Recorded verification commit is not available in git history.")

    def test_uncommitted_edits_drift_a_sidecar_whose_committed_source_is_unchanged(
        self,
    ) -> None:
        """``git diff <stamp> HEAD`` is clean, but the file on disk is not what was
        verified -- the working tree is the code the developer is about to close out on."""
        last_hash = self.commit_source("src/a.py")
        (self.repo / "src" / "a.py").write_text("value = 2\n", encoding="utf-8")
        sidecar = write_sidecar(
            self.onboarding / "src" / "a.py.md",
            source_path="src/a.py",
            last_hash=last_hash,
        )

        row = classify_external_onboarding(sidecar, self.repo)

        self.assertEqual(row.classification, "drifted")
        self.assertEqual(row.trust, "medium")
        self.assertEqual(row.affected_sections, "logic; invariants; conventions; docs references")
        self.assertEqual(row.note, "Source has local unstaged changes not represented in HEAD.")

    def test_a_clean_committed_source_is_up_to_date(self) -> None:
        """The contrast case for the two above: stamp resolves, no committed diff, no local
        edits -- only then is the sidecar trusted."""
        last_hash = self.commit_source("src/a.py")
        sidecar = write_sidecar(
            self.onboarding / "src" / "a.py.md",
            source_path="src/a.py",
            last_hash=last_hash,
        )

        row = classify_external_onboarding(sidecar, self.repo)

        self.assertEqual(row.classification, "up to date")
        self.assertEqual(row.trust, "high")
        self.assertEqual(row.affected_sections, "none")

    def test_a_diff_git_cannot_compute_is_reported_as_drift_with_the_git_error(self) -> None:
        """On an orphan branch HEAD names no commit, so the stamp resolves but the diff
        cannot run. The checker must surface git's own message as drift instead of
        letting an uncomputable comparison pass as clean."""
        last_hash = self.commit_source("src/a.py")
        run_git(self.repo, ["checkout", "--orphan", "fresh"])
        sidecar = write_sidecar(
            self.onboarding / "src" / "a.py.md",
            source_path="src/a.py",
            last_hash=last_hash,
        )

        row = classify_external_onboarding(sidecar, self.repo)

        self.assertEqual(row.classification, "drifted")
        self.assertEqual(row.trust, "medium")
        self.assertEqual(row.affected_sections, "logic; invariants; metadata")
        self.assertTrue(
            row.note.startswith("git diff failed: "),
            f"expected the git error to be quoted, got {row.note!r}",
        )
        self.assertNotEqual(row.note, "git diff failed: unknown git error")


class MissingOnboardingStorageModeTests(unittest.TestCase):
    """Which onboarding a newly added file is expected to carry, per storage mode."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True)
        self.onboarding = self.root / "onboarding"
        self.onboarding.mkdir(parents=True)

    def write_source(self, relative: str, body: str = "value = 1\n") -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def check(self, relative: str, settings: StorageSettings):
        return missing_onboarding_for_source(self.repo, self.onboarding, settings, "repo", relative)

    def test_inline_mode_expects_the_block_in_the_source_not_a_sidecar(self) -> None:
        """Under inline storage the expected onboarding is the source file itself, so the
        report points at ``inline:<path>`` rather than at a sidecar that will never exist."""
        self.write_source("src/a.py")

        row = self.check("src/a.py", StorageSettings(mode="inline", default="inline"))

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.state, "missing")
        self.assertEqual(row.storage_mode, "inline")
        self.assertEqual(row.expected_onboarding, "inline:src/a.py")
        self.assertEqual(
            row.note, "Inline onboarding block is missing for this newly added worktree file."
        )

    def test_inline_mode_accepts_a_source_carrying_its_own_block(self) -> None:
        self.write_source("src/a.py", body=f"value = 1\n{INLINE_BLOCK}\n")

        row = self.check("src/a.py", StorageSettings(mode="inline", default="inline"))

        self.assertIsNone(row)

    def test_inline_mode_cannot_check_a_non_utf8_source(self) -> None:
        """A binary or non-UTF-8 file has no readable inline block; that is reported as
        unsupported -- a fact about the checker -- not as missing onboarding the developer
        is expected to write."""
        path = self.write_source("src/a.py")
        path.write_bytes(b"value = 1\n\xff\xfe not utf-8\n")

        row = self.check("src/a.py", StorageSettings(mode="inline", default="inline"))

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.state, "unsupported")
        self.assertEqual(row.expected_onboarding, "inline:src/a.py")
        self.assertEqual(
            row.note,
            "Inline onboarding could not be checked because the source is not UTF-8 text.",
        )

    def test_a_storage_mode_the_checker_does_not_understand_is_unsupported(self) -> None:
        """Hybrid storage can route a path to a mode this checker knows nothing about. It
        names the mode instead of guessing at an expected onboarding path."""
        self.write_source("src/a.py")
        settings = StorageSettings(mode="hybrid", default="external-service")

        row = self.check("src/a.py", settings)

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.state, "unsupported")
        self.assertEqual(row.storage_mode, "external-service")
        self.assertEqual(row.expected_onboarding, "")
        self.assertEqual(
            row.note,
            "Unsupported storage mode 'external-service' for newly added worktree file.",
        )

    def test_disabled_storage_reports_nothing_at_all(self) -> None:
        self.write_source("src/a.py")

        row = self.check("src/a.py", StorageSettings(mode="disabled", default="disabled"))

        self.assertIsNone(row)

    def test_the_report_counts_an_unsupported_row_as_a_failure(self) -> None:
        """``check_missing_onboarding`` folds the per-file verdicts into the gate result;
        an unsupported mode blocks just like a missing sidecar does."""
        repo = initialize_repo(self.root / "counted")
        (repo / "src").mkdir(parents=True, exist_ok=True)
        (repo / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")

        result = check_missing_onboarding(
            code_repository_root=repo,
            onboarding_root=self.onboarding,
            settings=StorageSettings(mode="hybrid", default="external-service"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["missingCount"], 1)
        self.assertEqual(result["missing"][0]["state"], "unsupported")
        self.assertEqual(result["missing"][0]["storageMode"], "external-service")


if __name__ == "__main__":
    unittest.main()
