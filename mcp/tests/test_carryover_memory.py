from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agents_remember.memory.carryover import (
    ENTITY_CATALOG_KEY,
    ENTITY_CATALOG_KIND,
    FILE_SIDECAR_KIND,
    MEMORY_ONLY_DOC_KIND,
    apply_carryover_for_request,
    build_plan_for_request,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    compute_git_blob_set_fingerprint,
)
from test_carryover import (
    CarryoverFixture,
    commit_file,
    git,
    read_onboarding_field,
    write_file_onboarding,
)


def write_entity_catalog(
    onboarding_root: Path, fingerprint: str, *, note: str = "Catalog body."
) -> Path:
    path = onboarding_root / "entities.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Repo Entity Catalog",
                "",
                note,
                "",
                "## Entity Fingerprints",
                "",
                "| Entity | Algorithm | Fingerprint | Evidence Paths |",
                "| --- | --- | --- | --- |",
                f"| Test Entity | `git-blob-set-v1` | `{fingerprint}` | `README.md` |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def clone_memory(official: Path, dest: Path) -> None:
    subprocess.run(
        ["git", "clone", "--quiet", official.as_posix(), dest.as_posix()],
        check=True,
        capture_output=True,
        text=True,
    )
    git(dest, "config", "user.email", "agents-remember-tests@example.invalid")
    git(dest, "config", "user.name", "Agents Remember Tests")


def candidates_of_kind(plan: dict[str, object], kind: str) -> list[dict[str, object]]:
    candidates = plan["candidates"]
    assert isinstance(candidates, list)
    return [candidate for candidate in candidates if candidate["kind"] == kind]


class MemoryMainAdvanceTests(unittest.TestCase):
    """Issue #54: carryover fast-forwards memory main after code landed officially."""

    def test_apply_fast_forwards_memory_main_from_non_main_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            git(fixture.official_memory, "checkout", "-b", "cycle/source")
            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved sidecar carryover",
            )
            self.assertEqual(payload["state"], "carried-over")
            advance = payload["memory_main_advance"]
            assert isinstance(advance, dict)
            self.assertEqual(advance["state"], "fast-forwarded")
            self.assertEqual(
                git(fixture.official_memory, "rev-parse", "main"),
                git(fixture.official_memory, "rev-parse", "cycle/source"),
            )

    def test_nothing_to_carry_still_advances_memory_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            (fixture.source_memory / "onboarding" / "src" / "app" / "feature.py.md").unlink()
            git(fixture.official_memory, "checkout", "-b", "cycle/source")
            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved carryover check",
            )
            self.assertEqual(payload["state"], "ledger-mapped-head")
            advance = payload["memory_main_advance"]
            assert isinstance(advance, dict)
            self.assertEqual(advance["state"], "fast-forwarded")
            self.assertEqual(
                git(fixture.official_memory, "rev-parse", "main"),
                git(fixture.official_memory, "rev-parse", "cycle/source"),
            )

    def test_apply_on_main_checkout_reports_already_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved sidecar carryover",
            )
            advance = payload["memory_main_advance"]
            assert isinstance(advance, dict)
            self.assertEqual(advance["state"], "already-current")

    def test_diverged_memory_main_is_reported_and_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            git(fixture.official_memory, "checkout", "-b", "cycle/source")
            git(fixture.official_memory, "checkout", "main")
            commit_file(
                fixture.official_memory,
                "onboarding/other.md",
                "# independent official change\n",
                "Independent change on main",
            )
            main_before = git(fixture.official_memory, "rev-parse", "main")
            git(fixture.official_memory, "checkout", "cycle/source")
            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved sidecar carryover",
            )
            advance = payload["memory_main_advance"]
            assert isinstance(advance, dict)
            self.assertEqual(advance["state"], "diverged")
            self.assertEqual(git(fixture.official_memory, "rev-parse", "main"), main_before)

    def test_missing_main_branch_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            git(fixture.official_memory, "branch", "-m", "main", "trunk")
            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved sidecar carryover",
            )
            advance = payload["memory_main_advance"]
            assert isinstance(advance, dict)
            self.assertEqual(advance["state"], "skipped")


class EntityCatalogCarryoverTests(unittest.TestCase):
    def test_identical_catalog_yields_no_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            fingerprint = compute_git_blob_set_fingerprint(
                fixture.code_repo, ["README.md"], ref="main"
            )
            write_entity_catalog(fixture.official_memory / "onboarding", fingerprint)
            write_entity_catalog(fixture.source_memory / "onboarding", fingerprint)
            fixture.commit_official()
            plan = build_plan_for_request(fixture.request())
            self.assertEqual(candidates_of_kind(plan, ENTITY_CATALOG_KIND), [])

    def test_differing_catalog_is_review_required_and_validates_on_carry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            fingerprint = compute_git_blob_set_fingerprint(
                fixture.code_repo, ["README.md"], ref="main"
            )
            write_entity_catalog(
                fixture.official_memory / "onboarding", fingerprint, note="Old body."
            )
            fixture.commit_official()
            branch_catalog = write_entity_catalog(
                fixture.source_memory / "onboarding", fingerprint, note="Refreshed body."
            )
            plan = build_plan_for_request(fixture.request())
            catalogs = candidates_of_kind(plan, ENTITY_CATALOG_KIND)
            self.assertEqual(len(catalogs), 1)
            self.assertEqual(catalogs[0]["source_path"], ENTITY_CATALOG_KEY)
            self.assertEqual(catalogs[0]["decision"], "review-required")

            result = apply_carryover_for_request(
                fixture.request(),
                intent_note="carry the reviewed entity catalog",
                include_review_required=[ENTITY_CATALOG_KEY],
            )
            self.assertEqual(result["state"], "carried-over")
            official_catalog = fixture.official_memory / "onboarding" / "entities.md"
            self.assertEqual(
                official_catalog.read_text(encoding="utf-8"),
                branch_catalog.read_text(encoding="utf-8"),
            )
            validation = result["entity_fingerprint_validation"]
            assert isinstance(validation, dict)
            self.assertEqual(validation["state"], "validated")
            self.assertEqual(validation["rows"], 1)
            self.assertEqual(validation["mismatches"], [])

    def test_carried_catalog_reports_fingerprint_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_entity_catalog(
                fixture.official_memory / "onboarding", "sha256:" + "0" * 64, note="Old."
            )
            fixture.commit_official()
            write_entity_catalog(
                fixture.source_memory / "onboarding", "sha256:" + "f" * 64, note="New."
            )
            result = apply_carryover_for_request(
                fixture.request(),
                intent_note="carry catalog with stale fingerprint",
                include_review_required=[ENTITY_CATALOG_KEY],
            )
            validation = result["entity_fingerprint_validation"]
            assert isinstance(validation, dict)
            self.assertEqual(validation["state"], "mismatch")
            mismatches = validation["mismatches"]
            assert isinstance(mismatches, list)
            self.assertEqual(len(mismatches), 1)
            self.assertEqual(mismatches[0]["entity"], "Test Entity")


class MemoryOnlyDocCarryoverTests(unittest.TestCase):
    def _git_backed_fixture(self, tmp: Path) -> CarryoverFixture:
        """Fixture whose source memory is a clone of official (real worktree shape)."""
        fixture = CarryoverFixture(tmp)
        write_file_onboarding(
            fixture.official_memory / "onboarding", "repo-a", "README.md", fixture.old_base
        )
        fixture.commit_official()
        clone = tmp / "memory-branch-git"
        clone_memory(fixture.official_memory, clone)
        # Keep the diff-derived sidecar the original plain-dir fixture provides.
        write_file_onboarding(
            clone / "onboarding", "repo-a", "src/app/feature.py", fixture.source_head
        )
        fixture.source_memory = clone
        return fixture

    def test_reverified_doc_auto_carries_when_nothing_diverged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._git_backed_fixture(Path(tmp))
            branch_doc = fixture.source_memory / "onboarding" / "README.md.md"
            branch_doc.write_text(
                branch_doc.read_text(encoding="utf-8") + "\nRicher branch insight.\n",
                encoding="utf-8",
            )
            plan = build_plan_for_request(fixture.request())
            docs = candidates_of_kind(plan, MEMORY_ONLY_DOC_KIND)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["source_path"], "README.md")
            self.assertEqual(docs[0]["decision"], "auto-carry")
            self.assertEqual(docs[0]["evidence"], "memory-only-reverification-valid")

            result = apply_carryover_for_request(
                fixture.request(), intent_note="carry memory-only re-verification"
            )
            self.assertEqual(result["state"], "carried-over")
            official_doc = fixture.official_memory / "onboarding" / "README.md.md"
            self.assertIn("Richer branch insight.", official_doc.read_text(encoding="utf-8"))
            self.assertEqual(
                read_onboarding_field(official_doc, "lastVerifiedCommitHash"),
                fixture.official_head,
            )
            validation = result["entity_fingerprint_validation"]
            assert isinstance(validation, dict)
            self.assertEqual(validation["state"], "skipped")

    def test_source_diverged_doc_is_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._git_backed_fixture(Path(tmp))
            commit_file(fixture.code_repo, "README.md", "# Test Repo (edited)\n", "Edit readme")
            branch_doc = fixture.source_memory / "onboarding" / "README.md.md"
            branch_doc.write_text(
                branch_doc.read_text(encoding="utf-8") + "\nStale branch insight.\n",
                encoding="utf-8",
            )
            plan = build_plan_for_request(fixture.request())
            docs = candidates_of_kind(plan, MEMORY_ONLY_DOC_KIND)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["decision"], "review-required")
            self.assertEqual(docs[0]["evidence"], "source-diverged")

    def test_official_memory_moved_doc_is_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._git_backed_fixture(Path(tmp))
            official_doc = fixture.official_memory / "onboarding" / "README.md.md"
            official_doc.write_text(
                official_doc.read_text(encoding="utf-8") + "\nParallel official change.\n",
                encoding="utf-8",
            )
            fixture.commit_official("Independent official doc change")
            branch_doc = fixture.source_memory / "onboarding" / "README.md.md"
            branch_doc.write_text(
                branch_doc.read_text(encoding="utf-8") + "\nBranch change.\n",
                encoding="utf-8",
            )
            plan = build_plan_for_request(fixture.request())
            docs = candidates_of_kind(plan, MEMORY_ONLY_DOC_KIND)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["decision"], "review-required")
            self.assertEqual(docs[0]["evidence"], "official-memory-moved")

    def test_plain_dir_source_memory_doc_is_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_file_onboarding(
                fixture.source_memory / "onboarding", "repo-a", "README.md", fixture.old_base
            )
            plan = build_plan_for_request(fixture.request())
            docs = candidates_of_kind(plan, MEMORY_ONLY_DOC_KIND)
            self.assertEqual(len(docs), 1)
            self.assertEqual(docs[0]["decision"], "review-required")
            self.assertEqual(docs[0]["evidence"], "official-memory-moved")

    def test_diff_covered_doc_is_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            plan = build_plan_for_request(fixture.request())
            candidates = plan["candidates"]
            assert isinstance(candidates, list)
            feature_candidates = [
                candidate
                for candidate in candidates
                if candidate["source_path"] == "src/app/feature.py"
            ]
            self.assertEqual(len(feature_candidates), 1)
            self.assertEqual(feature_candidates[0]["kind"], FILE_SIDECAR_KIND)
