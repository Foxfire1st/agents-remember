from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agents_remember.errors import AuthorityError
from agents_remember.kernel.memory_ledger import parse_ledger_text
from agents_remember.memory.carryover import apply_carryover_for_request
from test_carryover import (
    CarryoverFixture,
    carryover_snapshot,
    git,
    read_onboarding_field,
    write_route_overview,
)


class CarryoverOverviewApplyTests1(unittest.TestCase):
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
