"""Tests for branch-memory carryover planning and apply (memory/carryover.py)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    parse_ledger_text,
    write_ledger,
)
from agents_remember.memory.carryover import (
    FILE_SIDECAR_KIND,
    ROUTE_OVERVIEW_KIND,
    CarryoverRequest,
    apply_carryover_for_request,
    build_plan_for_request,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def init_repo(repo: Path, branch: str = "main") -> str:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", branch)
    git(repo, "config", "user.email", "agents-remember-tests@example.invalid")
    git(repo, "config", "user.name", "Agents Remember Tests")
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "Initial commit")
    return git(repo, "rev-parse", "HEAD")


def commit_file(repo: Path, path: str, content: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def write_file_onboarding(
    onboarding_root: Path, repo_name: str, source_path: str, commit_hash: str
) -> Path:
    path = onboarding_root / f"{source_path}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# {source_path}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repo_name} |",
                f"| path | `{source_path}` |",
                "| doc_type | `file-level-onboarding` |",
                f"| lastVerifiedCommitHash | `{commit_hash}` |",
                "| lastVerifiedCommitDate | 2026-05-09T00:00:00+00:00 |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def write_route_overview(
    onboarding_root: Path,
    repo_name: str,
    source_route: str,
    commit_hash: str,
    body: str = "Route purpose.",
) -> Path:
    path = (
        onboarding_root / source_route / "overview.md"
        if source_route != "."
        else onboarding_root / "overview.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    doc_type = "repo-overview" if source_route == "." else "route-local-overview"
    path.write_text(
        "\n".join(
            [
                f"# {source_route} Overview",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| repository | {repo_name} |",
                f"| doc_type | `{doc_type}` |",
                f"| sourceRoute | `{source_route}` |",
                f"| lastVerifiedCommitHash | `{commit_hash}` |",
                "| lastVerifiedCommitDate | 2026-05-09T00:00:00+00:00 |",
                "",
                "## Purpose",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def read_onboarding_field(path: Path, field: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {field} |"):
            return line.split("|", 3)[2].strip().strip("`")
    raise AssertionError(f"{field} was not found in {path}")


class CarryoverFixture:
    """Code repo with a landed task branch plus official and source memory roots."""

    def __init__(self, workspace: Path) -> None:
        self.code_repo = workspace / "repo-a"
        self.old_base = init_repo(self.code_repo, "main")
        git(self.code_repo, "checkout", "-b", "task/one")
        self.source_head = commit_file(
            self.code_repo, "src/app/feature.py", "VALUE = 'landed'\n", "Add feature"
        )
        git(self.code_repo, "checkout", "main")
        git(self.code_repo, "merge", "--ff-only", "task/one")
        self.official_head = git(self.code_repo, "rev-parse", "main")

        self.official_memory = workspace / "memory-official"
        memory_seed = init_repo(self.official_memory, "main")
        write_ledger(
            self.official_memory / "memory.md",
            create_initial_ledger("repo-a", self.old_base, memory_seed),
        )
        git(self.official_memory, "add", "memory.md")
        git(self.official_memory, "commit", "-m", "Add memory ledger")

        self.source_memory = workspace / "memory-branch"
        self.source_memory.mkdir(parents=True)
        write_file_onboarding(
            self.source_memory / "onboarding", "repo-a", "src/app/feature.py", self.source_head
        )

    def commit_official(self, message: str = "Seed official onboarding") -> None:
        git(self.official_memory, "add", "-A")
        git(self.official_memory, "commit", "-m", message)

    def request(self) -> CarryoverRequest:
        return CarryoverRequest(
            code_repository_root=self.code_repo,
            official_code_ref="main",
            source_code_ref="task/one",
            old_base=self.old_base,
            official_memory=self.official_memory,
            source_memory=self.source_memory,
            code_repository_name="repo-a",
        )


def overview_candidates_of(plan: dict[str, object]) -> list[dict[str, object]]:
    candidates = plan["candidates"]
    assert isinstance(candidates, list)
    return [
        candidate for candidate in candidates if candidate["kind"] == ROUTE_OVERVIEW_KIND
    ]


class CarryoverOverviewPlanTests(unittest.TestCase):
    def test_plan_includes_differing_overview_as_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_route_overview(
                fixture.source_memory / "onboarding",
                "repo-a",
                "src/app",
                fixture.source_head,
                body="Branch-learned route behavior.",
            )
            plan = build_plan_for_request(fixture.request())
            overviews = overview_candidates_of(plan)
            self.assertEqual(len(overviews), 1)
            self.assertEqual(overviews[0]["source_path"], "src/app")
            self.assertEqual(overviews[0]["decision"], "review-required")
            self.assertEqual(overviews[0]["evidence"], "route-covers-landed-paths")
            self.assertFalse(overviews[0]["official_exists"])

    def test_plan_skips_overview_without_landed_path_under_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_route_overview(
                fixture.source_memory / "onboarding", "repo-a", "docs", fixture.source_head
            )
            plan = build_plan_for_request(fixture.request())
            self.assertEqual(overview_candidates_of(plan), [])

    def test_plan_auto_carries_identical_overview_for_reverification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_route_overview(
                fixture.source_memory / "onboarding", "repo-a", ".", fixture.old_base
            )
            write_route_overview(
                fixture.official_memory / "onboarding", "repo-a", ".", fixture.old_base
            )
            fixture.commit_official()
            plan = build_plan_for_request(fixture.request())
            overviews = overview_candidates_of(plan)
            self.assertEqual(len(overviews), 1)
            self.assertEqual(overviews[0]["source_path"], ".")
            self.assertEqual(overviews[0]["decision"], "auto-carry")

    def test_sidecar_candidates_keep_default_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            plan = build_plan_for_request(fixture.request())
            candidates = plan["candidates"]
            assert isinstance(candidates, list)
            sidecars = [c for c in candidates if c["kind"] == FILE_SIDECAR_KIND]
            self.assertEqual(len(sidecars), 1)
            self.assertEqual(sidecars[0]["source_path"], "src/app/feature.py")


class CarryoverOverviewApplyTests(unittest.TestCase):
    def test_apply_carries_reviewed_overview_and_regenerates_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_route_overview(
                fixture.source_memory / "onboarding",
                "repo-a",
                "src/app",
                fixture.source_head,
                body="Branch-learned route behavior.",
            )
            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved overview carryover",
                include_review_required=["src/app"],
            )
            self.assertEqual(payload["state"], "carried-over")
            carried = payload["carried"]
            assert isinstance(carried, list)
            carried_keys = {candidate["source_path"] for candidate in carried}
            self.assertEqual(carried_keys, {"src/app/feature.py", "src/app"})
            official_overview = (
                fixture.official_memory / "onboarding" / "src" / "app" / "overview.md"
            )
            self.assertIn(
                "Branch-learned route behavior.",
                official_overview.read_text(encoding="utf-8"),
            )
            self.assertEqual(
                read_onboarding_field(official_overview, "lastVerifiedCommitHash"),
                fixture.official_head,
            )
            index_refresh = payload["route_index_refresh"]
            assert isinstance(index_refresh, dict)
            self.assertEqual(index_refresh["state"], "refreshed")
            index_path = (
                fixture.official_memory / "onboarding" / "src" / "app" / "overview.index.json"
            )
            self.assertTrue(index_path.exists())
            committed = git(
                fixture.official_memory,
                "show",
                "--name-only",
                "--format=",
                str(payload["memory_content_commit"]),
            )
            self.assertIn("onboarding/src/app/overview.index.json", committed)
            ledger = parse_ledger_text(
                (fixture.official_memory / "memory.md").read_text(encoding="utf-8")
            )
            self.assertEqual(ledger.rows[0].code_commit, fixture.official_head)

    def test_apply_skips_index_refresh_off_official_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            git(fixture.code_repo, "checkout", fixture.old_base)
            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved sidecar carryover",
            )
            self.assertEqual(payload["state"], "carried-over")
            index_refresh = payload["route_index_refresh"]
            assert isinstance(index_refresh, dict)
            self.assertEqual(index_refresh["state"], "skipped")
            self.assertIn("clean checkout", str(index_refresh["reason"]))

    def test_apply_without_carry_reports_skipped_index_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            (fixture.source_memory / "onboarding" / "src" / "app" / "feature.py.md").unlink()
            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved carryover check",
            )
            self.assertIn(payload["state"], {"nothing-to-carryover", "ledger-mapped-head"})
            index_refresh = payload["route_index_refresh"]
            assert isinstance(index_refresh, dict)
            self.assertEqual(index_refresh["state"], "skipped")
            self.assertIn("no onboarding was carried over", str(index_refresh["reason"]))


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


if __name__ == "__main__":
    unittest.main()
