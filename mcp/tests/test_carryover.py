"""Tests for branch-memory carryover planning and apply (memory/carryover.py)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_remember.errors import AuthorityError
from agents_remember.kernel.memory_ledger import (
    create_initial_ledger,
    parse_ledger_text,
    write_ledger,
)
from agents_remember.memory.carryover import (
    ENTITY_CATALOG_KEY,
    ENTITY_CATALOG_KIND,
    FILE_SIDECAR_KIND,
    MEMORY_ONLY_DOC_KIND,
    ROUTE_OVERVIEW_KIND,
    CarryoverRequest,
    apply_carryover_for_request,
    build_plan_for_request,
)
from agents_remember.memory_quality.integrity.onboarding_drift_check.git_ops import (
    compute_git_blob_set_fingerprint,
)


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, **(env or {})},
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


def write_memory_settings(
    memory_root: Path,
    *,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
) -> Path:
    path = memory_root / "system" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "onboarding": {
                    "storage": {"mode": "memory-repo"},
                    "pathRules": {
                        "include": {
                            "paths": includes or ["README.md", "src/**"],
                            "fileTypes": [".md", ".py"],
                        },
                        "exclude": {"paths": excludes or ["coverage/**"]},
                    },
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def repository_snapshot(repo: Path) -> tuple[str, str, dict[str, bytes]]:
    return git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"), tree_snapshot(repo)


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
        write_memory_settings(self.official_memory)
        git(self.official_memory, "add", "memory.md", "system/settings.json")
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


def carryover_snapshot(
    fixture: CarryoverFixture,
) -> tuple[tuple[str, str, dict[str, bytes]], dict[str, bytes]]:
    return repository_snapshot(fixture.official_memory), tree_snapshot(fixture.source_memory)


def overview_candidates_of(plan: dict[str, object]) -> list[dict[str, object]]:
    candidates = plan["candidates"]
    assert isinstance(candidates, list)
    return [candidate for candidate in candidates if candidate["kind"] == ROUTE_OVERVIEW_KIND]


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

    def test_missing_official_settings_refuses_before_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            git(fixture.official_memory, "rm", "system/settings.json")
            git(fixture.official_memory, "commit", "-m", "Remove settings authority")
            before = carryover_snapshot(fixture)

            with self.assertRaisesRegex(AuthorityError, "must provide route-index authority"):
                apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved sidecar carryover",
                )

            self.assertEqual(carryover_snapshot(fixture), before)

    def test_invalid_official_settings_refuses_before_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            (fixture.official_memory / "system" / "settings.json").write_text(
                "{not-json\n", encoding="utf-8"
            )
            fixture.commit_official("Commit invalid settings authority")
            before = carryover_snapshot(fixture)

            with self.assertRaisesRegex(AuthorityError, "invalid official-memory"):
                apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved sidecar carryover",
                )

            self.assertEqual(carryover_snapshot(fixture), before)

    def test_settings_without_route_authority_refuse_before_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            (fixture.official_memory / "system" / "settings.json").write_text(
                json.dumps({"version": 2, "crossRepo": {"allow": []}}) + "\n",
                encoding="utf-8",
            )
            fixture.commit_official("Commit settings without route authority")
            before = carryover_snapshot(fixture)

            with self.assertRaisesRegex(AuthorityError, "do not declare storage/path authority"):
                apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved sidecar carryover",
                )

            self.assertEqual(carryover_snapshot(fixture), before)

    def test_semantically_empty_json_authority_refuses_before_any_mutation(self) -> None:
        settings = [
            {"version": 2, "onboarding": {"storage": {}}},
            {"version": 2, "onboarding": {"storage": {"mode": ""}}},
            {"version": 2, "onboarding": {"storage": {"layout": "   "}}},
            {"version": 2, "onboarding": {"pathRules": None}},
            {"version": 2, "onboarding": {"pathRules": []}},
        ]
        for index, setting in enumerate(settings):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                (fixture.official_memory / "system" / "settings.json").write_text(
                    json.dumps(setting, indent=2) + "\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit semantically empty JSON authority {index}")
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "do not declare storage/path authority"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)

    def test_null_onboarding_without_root_authority_refuses_before_any_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            (fixture.official_memory / "system" / "settings.json").write_text(
                json.dumps({"version": 2, "onboarding": None}, indent=2) + "\n",
                encoding="utf-8",
            )
            fixture.commit_official("Commit null onboarding authority")
            route_index = fixture.official_memory / "onboarding" / "overview.index.json"
            self.assertFalse(route_index.exists())
            before = carryover_snapshot(fixture)

            with self.assertRaisesRegex(AuthorityError, "do not declare storage/path authority"):
                apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved sidecar carryover",
                )

            self.assertEqual(carryover_snapshot(fixture), before)
            self.assertFalse(route_index.exists())

    def test_nonnull_invalid_onboarding_delegates_to_typed_parser_before_mutation(
        self,
    ) -> None:
        for index, onboarding in enumerate([[], "invalid", 1]):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                (fixture.official_memory / "system" / "settings.json").write_text(
                    json.dumps({"version": 2, "onboarding": onboarding}, indent=2) + "\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit invalid onboarding shape {index}")
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "invalid official-memory.*onboarding must be an object"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)

    def test_supported_root_storage_fallback_remains_authoritative(self) -> None:
        onboarding_values: list[object] = [None, {"storage": {}}]
        for index, onboarding in enumerate(onboarding_values):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                (fixture.official_memory / "system" / "settings.json").write_text(
                    json.dumps(
                        {
                            "version": 2,
                            "onboarding": onboarding,
                            "storage": {"mode": "memory-repo"},
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit root storage fallback {index}")

                payload = apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved root storage authority",
                )

                self.assertEqual(payload["state"], "carried-over")
                index_refresh = payload["route_index_refresh"]
                assert isinstance(index_refresh, dict)
                self.assertEqual(index_refresh["state"], "refreshed")
                self.assertEqual(git(fixture.official_memory, "status", "--porcelain"), "")

    def test_json_storage_without_effective_selected_name_refuses_before_mutation(
        self,
    ) -> None:
        storage_values = [
            {"mode": False},
            {"mode": 0},
            {"mode": []},
            {"mode": {}},
            {"mode": "   ", "layout": "memory-repo"},
            {"mode": '""', "layout": "memory-repo"},
        ]
        for index, storage in enumerate(storage_values):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                (fixture.official_memory / "system" / "settings.json").write_text(
                    json.dumps(
                        {"version": 2, "onboarding": {"storage": storage}},
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit ineffective selected storage {index}")
                route_index = fixture.official_memory / "onboarding" / "overview.index.json"
                self.assertFalse(route_index.exists())
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "do not declare storage/path authority"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)
                self.assertFalse(route_index.exists())

    def test_falsey_json_mode_falls_through_to_effective_layout(self) -> None:
        mode_values: list[object] = [None, False, 0, "", [], {}]
        for index, mode in enumerate(mode_values):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                (fixture.official_memory / "system" / "settings.json").write_text(
                    json.dumps(
                        {
                            "version": 2,
                            "onboarding": {"storage": {"mode": mode, "layout": "memory-repo"}},
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit effective layout fallthrough {index}")

                payload = apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved layout authority",
                )

                self.assertEqual(payload["state"], "carried-over")
                index_refresh = payload["route_index_refresh"]
                assert isinstance(index_refresh, dict)
                self.assertEqual(index_refresh["state"], "refreshed")
                self.assertEqual(git(fixture.official_memory, "status", "--porcelain"), "")

    def test_truthy_nonstring_json_mode_delegates_to_typed_parser_before_mutation(
        self,
    ) -> None:
        mode_values: list[object] = [1, ["memory-repo"], {"name": "memory-repo"}]
        for index, mode in enumerate(mode_values):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                (fixture.official_memory / "system" / "settings.json").write_text(
                    json.dumps(
                        {
                            "version": 2,
                            "onboarding": {"storage": {"mode": mode}},
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit invalid selected storage type {index}")
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "invalid official-memory.*mode/layout must be a string"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)

    def test_semantically_empty_json_path_rule_members_refuse_before_mutation(
        self,
    ) -> None:
        settings = [
            {"pathRules": [{}]},
            {"pathRules": [{"path": ""}]},
            {"storage": {"mode": "memory-repo"}, "pathRules": [{}]},
        ]
        for index, onboarding in enumerate(settings):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                (fixture.official_memory / "system" / "settings.json").write_text(
                    json.dumps(
                        {"version": 2, "onboarding": onboarding},
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit empty path-rule member {index}")
                route_index = fixture.official_memory / "onboarding" / "overview.index.json"
                self.assertFalse(route_index.exists())
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "do not declare storage/path authority"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)
                self.assertFalse(route_index.exists())

    def test_blank_markdown_path_rule_member_refuses_before_mutation(self) -> None:
        settings_blocks = [
            "onboarding:\n  pathRules:\n    - path:\n",
            ("onboarding:\n  storage:\n    mode: memory-repo\n  pathRules:\n    - path:\n"),
        ]
        for index, block in enumerate(settings_blocks):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                git(fixture.official_memory, "rm", "system/settings.json")
                markdown_settings = fixture.official_memory / "system" / "settings.md"
                markdown_settings.parent.mkdir(parents=True, exist_ok=True)
                markdown_settings.write_text(
                    f"# Settings\n\n```yaml\n{block}```\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit blank Markdown path-rule member {index}")
                route_index = fixture.official_memory / "onboarding" / "overview.index.json"
                self.assertFalse(route_index.exists())
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "do not declare storage/path authority"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)
                self.assertFalse(route_index.exists())

    def test_markdown_reset_lists_remove_final_rule_contribution_before_mutation(
        self,
    ) -> None:
        settings_blocks = [
            (
                "standalone include paths reset empty",
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      include:\n"
                "        paths:\n"
                "          - src/**\n"
                "        paths:\n",
            ),
            (
                "standalone include paths reset quoted empty",
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      include:\n"
                "        paths:\n"
                "          - src/**\n"
                "        paths:\n"
                '          - ""\n',
            ),
            (
                "standalone exclude paths reset empty",
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      exclude:\n"
                "        paths:\n"
                "          - coverage/**\n"
                "        paths:\n",
            ),
            (
                "storage includes reset empty",
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        includes:\n"
                "          - src/**\n"
                "        includes:\n",
            ),
            (
                "storage includes reset quoted empty",
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        includes:\n"
                "          - src/**\n"
                "        includes:\n"
                '          - ""\n',
            ),
            (
                "storage excludes reset empty",
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        excludes:\n"
                "          - coverage/**\n"
                "        excludes:\n",
            ),
        ]
        for name, block in settings_blocks:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                git(fixture.official_memory, "rm", "system/settings.json")
                markdown_settings = fixture.official_memory / "system" / "settings.md"
                markdown_settings.parent.mkdir(parents=True, exist_ok=True)
                markdown_settings.write_text(
                    f"# Settings\n\n```yaml\n{block}```\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit {name}")
                route_index = fixture.official_memory / "onboarding" / "overview.index.json"
                self.assertFalse(route_index.exists())
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "do not declare storage/path authority"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)
                self.assertFalse(route_index.exists())

    def test_markdown_rule_contributions_follow_final_parser_state(self) -> None:
        settings_blocks = [
            (
                "per-rule storage reset empty refuses",
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        storage: memory-repo\n"
                "        storage:\n",
            ),
        ]
        for name, block in settings_blocks:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                git(fixture.official_memory, "rm", "system/settings.json")
                markdown_settings = fixture.official_memory / "system" / "settings.md"
                markdown_settings.parent.mkdir(parents=True, exist_ok=True)
                markdown_settings.write_text(
                    f"# Settings\n\n```yaml\n{block}```\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit {name}")
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "do not declare storage/path authority"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)

    def test_markdown_parser_retained_and_repopulated_contributions_remain_authoritative(
        self,
    ) -> None:
        settings_blocks = [
            (
                "global paths retained after repeated heading",
                "onboarding:\n"
                "  pathRules:\n"
                "    include:\n"
                "      paths:\n"
                "        - src/**\n"
                "      paths:\n",
            ),
            (
                "global file types retained after repeated heading",
                "onboarding:\n"
                "  pathRules:\n"
                "    exclude:\n"
                "      fileTypes:\n"
                "        - .md\n"
                "      fileTypes:\n",
            ),
            (
                "scoped include file types retained after repeated heading",
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      include:\n"
                "        fileTypes:\n"
                "          - .py\n"
                "        fileTypes:\n",
            ),
            (
                "scoped exclude file types retained after repeated heading",
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      exclude:\n"
                "        fileTypes:\n"
                "          - .md\n"
                "        fileTypes:\n",
            ),
            (
                "standalone paths reset then repopulated",
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      include:\n"
                "        paths:\n"
                "          - docs/**\n"
                "        paths:\n"
                "          - src/**\n",
            ),
            (
                "storage includes reset then repopulated",
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        includes:\n"
                "          - docs/**\n"
                "        includes:\n"
                "          - src/**\n",
            ),
            (
                "explicit path survives exclude reset",
                "onboarding:\n"
                "  pathRules:\n"
                "    - path: src\n"
                "      exclude:\n"
                "        paths:\n"
                "          - coverage/**\n"
                "        paths:\n",
            ),
            (
                "per-rule storage survives excludes reset",
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        storage: memory-repo\n"
                "        excludes:\n"
                "          - coverage/**\n"
                "        excludes:\n",
            ),
            (
                "per-rule storage reset then repopulated",
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        storage:\n"
                "        storage: memory-repo\n",
            ),
        ]
        for name, block in settings_blocks:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                git(fixture.official_memory, "rm", "system/settings.json")
                markdown_settings = fixture.official_memory / "system" / "settings.md"
                markdown_settings.parent.mkdir(parents=True, exist_ok=True)
                markdown_settings.write_text(
                    f"# Settings\n\n```yaml\n{block}```\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit {name}")

                payload = apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved Markdown rule authority",
                )

                self.assertEqual(payload["state"], "carried-over")
                index_refresh = payload["route_index_refresh"]
                assert isinstance(index_refresh, dict)
                self.assertEqual(index_refresh["state"], "refreshed")
                self.assertEqual(git(fixture.official_memory, "status", "--porcelain"), "")

    def test_markdown_unsupported_rule_lists_refuse_before_mutation(self) -> None:
        settings_blocks = [
            ("onboarding:\n  pathRules:\n    include:\n      nonsense:\n        - src/**\n"),
            (
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      nonsense:\n"
                "        values:\n"
                "          - src/**\n"
            ),
            (
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        nonsense:\n"
                "          - src/**\n"
            ),
            (
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      include:\n"
                "        paths:\n"
                "    - path:\n"
                "      nonsense:\n"
                "        values:\n"
                "          - src/**\n"
            ),
        ]
        for index, block in enumerate(settings_blocks):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                git(fixture.official_memory, "rm", "system/settings.json")
                markdown_settings = fixture.official_memory / "system" / "settings.md"
                markdown_settings.parent.mkdir(parents=True, exist_ok=True)
                markdown_settings.write_text(
                    f"# Settings\n\n```yaml\n{block}```\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit unsupported Markdown list {index}")
                route_index = fixture.official_memory / "onboarding" / "overview.index.json"
                self.assertFalse(route_index.exists())
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "do not declare storage/path authority"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)
                self.assertFalse(route_index.exists())

    def test_markdown_recognized_rule_lists_remain_authoritative(self) -> None:
        settings_blocks = [
            (
                "onboarding:\n"
                "  pathRules:\n"
                "    include:\n"
                "      paths:\n"
                "        # parser retains the active list across comments\n"
                "        - src/**\n"
            ),
            (
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      include:\n"
                "        paths:\n"
                "        unknownButRetained:\n"
                "          - src/**\n"
            ),
            (
                "onboarding:\n"
                "  storage:\n"
                "    pathRules:\n"
                "      - path:\n"
                "        includes:\n"
                "          # parser retains the active storage list across comments\n"
                "          - src/**\n"
            ),
            (
                "onboarding:\n"
                "  pathRules:\n"
                "    - path:\n"
                "      include:\n"
                "        paths:\n"
                "          - src/**\n"
                "    - path:\n"
                "      include:\n"
                "        fileTypes:\n"
                "          - .py\n"
            ),
        ]
        for index, block in enumerate(settings_blocks):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                git(fixture.official_memory, "rm", "system/settings.json")
                markdown_settings = fixture.official_memory / "system" / "settings.md"
                markdown_settings.parent.mkdir(parents=True, exist_ok=True)
                markdown_settings.write_text(
                    f"# Settings\n\n```yaml\n{block}```\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit recognized Markdown list {index}")

                payload = apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved Markdown list authority",
                )

                self.assertEqual(payload["state"], "carried-over")
                index_refresh = payload["route_index_refresh"]
                assert isinstance(index_refresh, dict)
                self.assertEqual(index_refresh["state"], "refreshed")
                self.assertEqual(git(fixture.official_memory, "status", "--porcelain"), "")

    def test_supported_nonempty_path_rules_remain_authoritative(self) -> None:
        settings_documents = [
            (
                "json",
                json.dumps(
                    {
                        "version": 2,
                        "onboarding": {"pathRules": [{"path": "src"}]},
                    },
                    indent=2,
                )
                + "\n",
            ),
            (
                "markdown",
                "# Settings\n\n```yaml\nonboarding:\n  pathRules:\n    - path: src\n```\n",
            ),
        ]
        for kind, content in settings_documents:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                if kind == "json":
                    settings_path = fixture.official_memory / "system" / "settings.json"
                else:
                    git(fixture.official_memory, "rm", "system/settings.json")
                    settings_path = fixture.official_memory / "system" / "settings.md"
                    settings_path.parent.mkdir(parents=True, exist_ok=True)
                settings_path.write_text(content, encoding="utf-8")
                fixture.commit_official(f"Commit valid {kind} path-rule authority")

                payload = apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved explicit path-rule authority",
                )

                self.assertEqual(payload["state"], "carried-over")
                index_refresh = payload["route_index_refresh"]
                assert isinstance(index_refresh, dict)
                self.assertEqual(index_refresh["state"], "refreshed")
                self.assertEqual(git(fixture.official_memory, "status", "--porcelain"), "")

    def test_unsupported_json_storage_labels_refuse_before_any_mutation(self) -> None:
        settings = [
            {
                "version": 2,
                "onboarding": {"storage": {"mode": "unsupported-mode"}},
            },
            {
                "version": 2,
                "onboarding": {
                    "storage": {
                        "mode": "memory-repo",
                        "default": "unsupported-default",
                    }
                },
            },
            {
                "version": 2,
                "onboarding": {
                    "storage": {"mode": "memory-repo"},
                    "pathRules": [{"path": "src", "storage": "unsupported-rule-storage"}],
                },
            },
        ]
        for index, setting in enumerate(settings):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                (fixture.official_memory / "system" / "settings.json").write_text(
                    json.dumps(setting, indent=2) + "\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit unsupported JSON authority {index}")
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(AuthorityError, "unsupported official-memory"):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)

    def test_semantically_empty_markdown_authority_refuses_before_any_mutation(
        self,
    ) -> None:
        settings_blocks = [
            "onboarding:\n  storage:\n",
            "onboarding:\n  storage:\n    mode:\n",
            "onboarding:\n  storage:\n    layout:   \n",
            "onboarding:\n  pathRules:\n",
        ]
        for index, block in enumerate(settings_blocks):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                git(fixture.official_memory, "rm", "system/settings.json")
                markdown_settings = fixture.official_memory / "system" / "settings.md"
                markdown_settings.parent.mkdir(parents=True, exist_ok=True)
                markdown_settings.write_text(
                    f"# Settings\n\n```yaml\n{block}```\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit semantically empty Markdown authority {index}")
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(
                    AuthorityError, "do not declare storage/path authority"
                ):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)

    def test_unsupported_markdown_storage_labels_refuse_before_any_mutation(
        self,
    ) -> None:
        settings_blocks = [
            "onboarding:\n  storage:\n    mode: unsupported-mode\n",
            ("onboarding:\n  storage:\n    mode: memory-repo\n    default: unsupported-default\n"),
            (
                "onboarding:\n"
                "  storage:\n"
                "    mode: memory-repo\n"
                "    pathRules:\n"
                "      - path: src\n"
                "        storage: unsupported-rule-storage\n"
            ),
        ]
        for index, block in enumerate(settings_blocks):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                fixture = CarryoverFixture(Path(tmp))
                git(fixture.official_memory, "rm", "system/settings.json")
                markdown_settings = fixture.official_memory / "system" / "settings.md"
                markdown_settings.parent.mkdir(parents=True, exist_ok=True)
                markdown_settings.write_text(
                    f"# Settings\n\n```yaml\n{block}```\n",
                    encoding="utf-8",
                )
                fixture.commit_official(f"Commit unsupported Markdown authority {index}")
                before = carryover_snapshot(fixture)

                with self.assertRaisesRegex(AuthorityError, "unsupported official-memory"):
                    apply_carryover_for_request(
                        fixture.request(),
                        intent_note="developer approved sidecar carryover",
                    )

                self.assertEqual(carryover_snapshot(fixture), before)

    def test_official_settings_override_conflicting_source_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            write_memory_settings(fixture.official_memory, includes=["README.md"])
            fixture.commit_official("Limit official source authority")
            write_memory_settings(fixture.source_memory, includes=["*"])
            write_route_overview(
                fixture.source_memory / "onboarding",
                "repo-a",
                ".",
                fixture.source_head,
            )

            payload = apply_carryover_for_request(
                fixture.request(),
                intent_note="developer approved official authority proof",
                include_review_required=["."],
            )

            self.assertEqual(payload["state"], "carried-over")
            index = json.loads(
                (fixture.official_memory / "onboarding" / "overview.index.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(index["coverageCounts"]["sourceFilesInScope"], 1)

    def test_ambient_git_index_cannot_redirect_carryover_guards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = CarryoverFixture(Path(tmp))
            alternate_index = Path(tmp) / "alternate-memory-index"
            git(
                fixture.official_memory,
                "read-tree",
                "--empty",
                env={"GIT_INDEX_FILE": str(alternate_index)},
            )

            with patch.dict(os.environ, {"GIT_INDEX_FILE": str(alternate_index)}):
                payload = apply_carryover_for_request(
                    fixture.request(),
                    intent_note="developer approved sidecar carryover",
                )

            self.assertEqual(payload["state"], "carried-over")
            self.assertEqual(git(fixture.official_memory, "status", "--porcelain"), "")


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


if __name__ == "__main__":
    unittest.main()
