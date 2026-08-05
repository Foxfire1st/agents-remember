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
import unittest.mock
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
from agents_remember.memory_quality.integrity.onboarding_drift_check import (
    sidecar as sidecar_module,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.entities import (
    EntityCatalog,
    classify_entity_fingerprint,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    compute_git_blob_set_fingerprint,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.models import (
    GIT_BLOB_SET_ALGORITHM,
    EntityFingerprint,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.sidecar import (
    classify_external_onboarding,
    classify_overview_onboarding,
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


def write_overview(
    path: Path,
    *,
    source_route: str,
    last_hash: str,
    last_date: str = "2026-07-31",
    repository: str = "repo",
) -> Path:
    """A route overview in the table format the drift checker parses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_route}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repository} |",
                f"| sourceRoute | `{source_route}` |",
                "| doc_type | `route-overview` |",
                f"| lastVerifiedCommitHash | `{last_hash}` |",
                f"| lastVerifiedCommitDate | {last_date} |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class OverviewClassificationTests(unittest.TestCase):
    """``classify_overview_onboarding`` verdicts, which are the route-level twins of the
    sidecar verdicts above.

    260731-EFA-L6 split the classifier's unverifiable-overview arms out of a 112-line
    function into ``_unverifiable_overview``. These pin the four verdicts that split
    exposed: no stamp, a route that no longer exists, a stamp git cannot resolve, and a
    committed route that is clean but has uncommitted edits on disk.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = initialize_repo(self.root)
        self.onboarding = self.root / "onboarding"
        self.settings = StorageSettings(mode="external", default="external")

    def classify(self, overview: Path):
        return classify_overview_onboarding(overview, self.repo, self.onboarding, self.settings)

    def commit_route(self, relative: str, body: str = "value = 1\n") -> str:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        run_git(self.repo, ["add", relative])
        run_git(self.repo, ["commit", "-m", f"add {relative}"])
        return head_hash(self.repo)

    def test_overview_without_a_verification_stamp_is_unverified(self) -> None:
        self.commit_route("src/a.py")
        overview = write_overview(
            self.onboarding / "src" / "overview.md", source_route="src", last_hash=""
        )

        row = self.classify(overview)

        self.assertEqual(row.classification, "missing verification")
        self.assertEqual(row.trust, "medium")
        self.assertEqual(row.affected_sections, "metadata; verification")
        self.assertIn("lastVerifiedCommitHash", row.note)
        self.assertEqual(row.source_file, "src")

    def test_overview_without_a_verification_date_is_unverified(self) -> None:
        last_hash = self.commit_route("src/a.py")
        overview = write_overview(
            self.onboarding / "src" / "overview.md",
            source_route="src",
            last_hash=last_hash,
            last_date="",
        )

        row = self.classify(overview)

        self.assertEqual(row.classification, "missing verification")

    def test_overview_for_a_deleted_route_is_orphaned(self) -> None:
        last_hash = self.commit_route("gone/a.py")
        run_git(self.repo, ["rm", "-r", "gone"])
        overview = write_overview(
            self.onboarding / "gone" / "overview.md", source_route="gone", last_hash=last_hash
        )

        row = self.classify(overview)

        self.assertEqual(row.classification, "orphaned")
        self.assertEqual(row.trust, "low")
        self.assertEqual(row.affected_sections, "all; source route missing")
        self.assertEqual(row.note, "Overview sourceRoute no longer exists.")

    def test_overview_stamped_with_a_commit_git_does_not_have_is_drifted(self) -> None:
        self.commit_route("src/a.py")
        overview = write_overview(
            self.onboarding / "src" / "overview.md", source_route="src", last_hash="0" * 40
        )

        row = self.classify(overview)

        self.assertEqual(row.classification, "drifted")
        self.assertEqual(row.affected_sections, "overview; metadata")
        self.assertEqual(
            row.note, "Recorded overview verification commit is not available in git history."
        )

    def test_an_unchanged_route_is_up_to_date(self) -> None:
        last_hash = self.commit_route("src/a.py")
        overview = write_overview(
            self.onboarding / "src" / "overview.md", source_route="src", last_hash=last_hash
        )

        row = self.classify(overview)

        self.assertEqual(row.classification, "up to date")
        self.assertEqual(row.trust, "high")
        self.assertEqual(row.affected_sections, "none")

    def test_uncommitted_edits_drift_a_route_whose_committed_source_is_unchanged(self) -> None:
        last_hash = self.commit_route("src/a.py")
        (self.repo / "src" / "a.py").write_text("value = 2\n", encoding="utf-8")
        overview = write_overview(
            self.onboarding / "src" / "overview.md", source_route="src", last_hash=last_hash
        )

        row = self.classify(overview)

        self.assertEqual(row.classification, "drifted")
        self.assertEqual(row.affected_sections, "overview; route summary; invariants")

    def test_a_changed_route_is_drifted_against_its_stamp(self) -> None:
        last_hash = self.commit_route("src/a.py")
        self.commit_route("src/a.py", body="value = 3\n")
        overview = write_overview(
            self.onboarding / "src" / "overview.md", source_route="src", last_hash=last_hash
        )

        row = self.classify(overview)

        self.assertEqual(row.classification, "drifted")
        self.assertEqual(
            row.note, "Source route changed since recorded overview verification commit."
        )


class EntityFingerprintClassificationTests(unittest.TestCase):
    """``classify_entity_fingerprint`` verdicts for a catalog row that cannot be trusted.

    An entity fingerprint is a hash over several evidence files rather than one source, so
    it has failure shapes a sidecar does not: an algorithm this build cannot compute, a row
    with no evidence to hash, and evidence that has been deleted since it was recorded.
    260731-EFA-L6 split those out of a 124-line function into
    ``_structurally_invalid_entity``; these pin each verdict to the instruction it gives.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = initialize_repo(self.root)
        self.onboarding = self.root / "onboarding"
        self.catalog = EntityCatalog(
            onboarding_file=self.onboarding / "entities.md",
            onboarding_root=self.onboarding,
            repository="repo",
            settings=StorageSettings(mode="external", default="external"),
            last_updated="2026-07-31",
        )

    def commit_evidence(self, relative: str, body: str = "value = 1\n") -> None:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        run_git(self.repo, ["add", relative])
        run_git(self.repo, ["commit", "-m", f"add {relative}"])

    def row(self, **overrides: object) -> EntityFingerprint:
        fields: dict[str, object] = {
            "entity": "Widget",
            "algorithm": GIT_BLOB_SET_ALGORITHM,
            "fingerprint": "sha256:0",
            "evidence_paths": ["src/a.py"],
        }
        fields.update(overrides)
        return EntityFingerprint(**fields)  # type: ignore[arg-type]

    def test_an_algorithm_this_build_cannot_compute_is_unsupported_not_drifted(self) -> None:
        # Reporting an unimplemented algorithm as drift would send the reader looking for a
        # code change that never happened.
        self.commit_evidence("src/a.py")

        result = classify_entity_fingerprint(self.catalog, self.repo, self.row(algorithm="md5"))

        self.assertEqual(result.classification, "unsupported")
        self.assertEqual(result.trust, "low")
        self.assertEqual(result.affected_sections, "entity catalog; Widget")
        self.assertEqual(result.note, "Unsupported entity fingerprint algorithm 'md5'.")
        self.assertEqual(result.source_file, "entity:Widget")

    def test_a_row_with_no_fingerprint_is_unverified(self) -> None:
        self.commit_evidence("src/a.py")

        result = classify_entity_fingerprint(self.catalog, self.repo, self.row(fingerprint=""))

        self.assertEqual(result.classification, "missing verification")
        self.assertEqual(result.trust, "medium")
        self.assertIn("missing a fingerprint value or evidence paths", result.note)

    def test_a_row_with_no_evidence_paths_is_unverified(self) -> None:
        result = classify_entity_fingerprint(self.catalog, self.repo, self.row(evidence_paths=[]))

        self.assertEqual(result.classification, "missing verification")

    def test_deleted_evidence_is_drifted_and_names_every_missing_path(self) -> None:
        result = classify_entity_fingerprint(
            self.catalog, self.repo, self.row(evidence_paths=["src/gone.py", "src/also.py"])
        )

        self.assertEqual(result.classification, "drifted")
        self.assertEqual(result.trust, "low")
        self.assertIn("src/gone.py, src/also.py", result.note)
        self.assertIn("removed, renamed, or moved", result.note)

    def test_evidence_git_cannot_hash_reports_the_computation_failure(self) -> None:
        # The path exists on disk but has never been committed, so `git rev-parse` on the
        # blob fails. That is a computation failure, not a mismatch, and it says so.
        (self.repo / "src").mkdir(parents=True, exist_ok=True)
        (self.repo / "src" / "a.py").write_text("value = 1\n", encoding="utf-8")

        result = classify_entity_fingerprint(self.catalog, self.repo, self.row())

        self.assertEqual(result.classification, "drifted")
        self.assertEqual(result.trust, "low")
        self.assertIn("Unable to compute entity fingerprint", result.note)

    def test_matching_evidence_with_no_local_edits_is_up_to_date(self) -> None:
        self.commit_evidence("src/a.py")
        fingerprint = compute_git_blob_set_fingerprint(self.repo, ["src/a.py"])

        result = classify_entity_fingerprint(
            self.catalog, self.repo, self.row(fingerprint=fingerprint)
        )

        self.assertEqual(result.classification, "up to date")
        self.assertEqual(result.trust, "high")
        self.assertEqual(result.affected_sections, "none")

    def test_matching_evidence_with_uncommitted_edits_is_drifted(self) -> None:
        # HEAD still hashes to the recorded fingerprint, but the working tree is not what
        # was verified -- and the working tree is what closeout is about to commit.
        self.commit_evidence("src/a.py")
        fingerprint = compute_git_blob_set_fingerprint(self.repo, ["src/a.py"])
        (self.repo / "src" / "a.py").write_text("value = 2\n", encoding="utf-8")

        result = classify_entity_fingerprint(
            self.catalog, self.repo, self.row(fingerprint=fingerprint)
        )

        self.assertEqual(result.classification, "drifted")
        self.assertEqual(result.trust, "medium")
        self.assertIn("src/a.py", result.note)

    def test_changed_evidence_is_drifted_against_the_recorded_fingerprint(self) -> None:
        self.commit_evidence("src/a.py")

        result = classify_entity_fingerprint(self.catalog, self.repo, self.row())

        self.assertEqual(result.classification, "drifted")
        self.assertEqual(
            result.note, "Entity evidence fingerprint changed since the catalog was refreshed."
        )

    def test_changed_evidence_with_local_edits_names_both(self) -> None:
        self.commit_evidence("src/a.py")
        (self.repo / "src" / "a.py").write_text("value = 9\n", encoding="utf-8")

        result = classify_entity_fingerprint(self.catalog, self.repo, self.row())

        self.assertEqual(result.classification, "drifted")
        self.assertIn("Local changes also exist", result.note)


class OverviewGitFailureTests(unittest.TestCase):
    """The verdict when ``git diff`` itself fails rather than answering.

    ``git diff --quiet`` answers 0 for "no change" and 1 for "changed"; anything else is
    git refusing -- a corrupt object, a broken index, a repository the process cannot read.
    That is not a clean overview and not a drifted one, and reporting it as either would be
    a lie about what was checked, so it is drifted with git's own words in the note. Git
    cannot be asked to fail on demand, so the runner is substituted for this one case.
    """

    def test_a_failing_git_diff_is_reported_as_drift_carrying_gits_message(self) -> None:
        subject = sidecar_module._OverviewSubject(
            onboarding_ref="src/overview.md",
            source_route="src",
            repository="repo",
            storage_mode="external",
            last_hash="0" * 40,
            last_date="2026-07-31",
            doc_type="route-overview",
        )

        def fake_run_git(_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
            if args[0] == "cat-file":
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 128, "", "fatal: bad object\n")

        with (
            tempfile.TemporaryDirectory() as tmp,
            unittest.mock.patch.object(sidecar_module, "run_git", fake_run_git),
            unittest.mock.patch.object(sidecar_module, "_overview_subject", lambda *_args: subject),
        ):
            # The route must exist on disk, or the orphan arm answers before the diff.
            (Path(tmp) / "src").mkdir()
            row = classify_overview_onboarding(
                Path(tmp) / "overview.md",
                Path(tmp),
                Path(tmp),
                StorageSettings(mode="external", default="external"),
            )

        self.assertEqual(row.classification, "drifted")
        self.assertEqual(row.trust, "medium")
        self.assertEqual(row.affected_sections, "overview; metadata")
        self.assertEqual(row.note, "git diff failed: fatal: bad object")
