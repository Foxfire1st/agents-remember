from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents_remember.errors import AuthorityError
from agents_remember.memory.carryover import apply_carryover_for_request
from test_carryover import (
    CarryoverFixture,
    carryover_snapshot,
    git,
    write_memory_settings,
    write_route_overview,
)


class CarryoverOverviewApplyTests2(unittest.TestCase):
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
