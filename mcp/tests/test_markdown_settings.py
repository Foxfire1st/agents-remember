from __future__ import annotations

import sys
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.coordination_context.markdown_settings import (
    _MarkdownSettingsParser,
    parse_settings_block,
)


class MarkdownSettingsStorageTests(unittest.TestCase):
    def test_full_internal_storage_block_parses_mode_and_default(self) -> None:
        block = "onboarding:\n  storage:\n    mode: repo-sidecar\n    default: repo-sidecar\n"

        storage, cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(storage.mode, "repo-sidecar")
        self.assertEqual(storage.default, "repo-sidecar")
        self.assertEqual(storage.path_rules, [])
        self.assertEqual(cross_repo.allow, [])

    def test_mode_drives_default_when_default_absent(self) -> None:
        # Setting only `mode:` should propagate to `default:` (try_apply_storage_mode
        # assigns settings.default = settings.mode).
        block = "onboarding:\n  storage:\n    mode: memory-repo\n"

        storage, _cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(storage.mode, "memory-repo")
        self.assertEqual(storage.default, "memory-repo")

    def test_explicit_default_after_mode_overrides_default_only(self) -> None:
        block = "onboarding:\n  storage:\n    mode: repo-sidecar\n    default: memory-repo\n"

        storage, _cross_repo, _saw = parse_settings_block(block, "internal")

        assert storage is not None
        self.assertEqual(storage.mode, "repo-sidecar")
        self.assertEqual(storage.default, "memory-repo")

    def test_layout_alias_is_accepted_for_mode(self) -> None:
        block = "onboarding:\n  storage:\n    layout: memory-repo\n"

        storage, _cross_repo, _saw = parse_settings_block(block, "internal")

        assert storage is not None
        self.assertEqual(storage.mode, "memory-repo")
        self.assertEqual(storage.default, "memory-repo")

    def test_bare_mode_keeps_internal_topology_default(self) -> None:
        # Regression: a bare `mode:` with no value must keep the topology-derived
        # default ("repo-sidecar" for internal), NOT fall back to a literal value.
        block = "onboarding:\n  storage:\n    mode:\n"

        storage, _cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(storage.mode, "repo-sidecar")
        self.assertEqual(storage.default, "repo-sidecar")

    def test_bare_mode_keeps_external_topology_default(self) -> None:
        block = "onboarding:\n  storage:\n    mode:\n"

        storage, _cross_repo, saw_settings = parse_settings_block(block, "external")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(storage.mode, "memory-repo")
        self.assertEqual(storage.default, "memory-repo")

    def test_internal_and_external_topology_defaults_differ_without_storage(self) -> None:
        # With no storage block but a pathRules section the parser still returns a
        # StorageSettings whose mode reflects the topology default.
        path_rules_only = "onboarding:\n  pathRules:\n    - path: src\n"

        internal_storage, _c1, _s1 = parse_settings_block(path_rules_only, "internal")
        external_storage, _c2, _s2 = parse_settings_block(path_rules_only, "external")

        assert internal_storage is not None
        assert external_storage is not None
        self.assertEqual(internal_storage.mode, "repo-sidecar")
        self.assertEqual(external_storage.mode, "memory-repo")

    def test_empty_block_reports_no_settings_and_no_storage(self) -> None:
        storage, cross_repo, saw_settings = parse_settings_block("", "internal")

        self.assertFalse(saw_settings)
        self.assertIsNone(storage)
        self.assertEqual(cross_repo.allow, [])

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        block = (
            "# top comment\nonboarding:\n\n  storage:\n    mode: memory-repo  # inline comment\n"
        )

        storage, _cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(storage.mode, "memory-repo")


class MarkdownSettingsPathRulesTests(unittest.TestCase):
    def test_global_path_rule_collects_include_exclude_paths_and_types(self) -> None:
        block = (
            "onboarding:\n"
            "  pathRules:\n"
            "    include:\n"
            "      paths:\n"
            '        - "**/*.py"\n'
            "      fileTypes:\n"
            "        - .py\n"
            "    exclude:\n"
            "      paths:\n"
            "        - vendor/**\n"
            "      fileTypes:\n"
            "        - .min.js\n"
        )

        storage, _cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(len(storage.path_rules), 1)
        rule = storage.path_rules[0]
        self.assertEqual(rule.get("path"), "")
        self.assertEqual(rule.get("includes"), ["**/*.py"])
        self.assertEqual(rule.get("excludes"), ["vendor/**"])
        self.assertEqual(rule.get("include_file_types"), [".py"])
        self.assertEqual(rule.get("exclude_file_types"), [".min.js"])

    def test_global_path_rule_defaults_includes_to_star_when_absent(self) -> None:
        block = "onboarding:\n  pathRules:\n    exclude:\n      paths:\n        - vendor/**\n"

        storage, _cross_repo, _saw = parse_settings_block(block, "internal")

        assert storage is not None
        self.assertEqual(len(storage.path_rules), 1)
        rule = storage.path_rules[0]
        self.assertEqual(rule.get("includes"), ["*"])
        self.assertEqual(rule.get("excludes"), ["vendor/**"])

    def test_scoped_path_rule_uses_nested_eligibility_sections(self) -> None:
        block = (
            "onboarding:\n"
            "  pathRules:\n"
            "    - path: docs\n"
            "      include:\n"
            "        paths:\n"
            '          - "**/*.md"\n'
            "        fileTypes:\n"
            "          - .md\n"
            "      exclude:\n"
            "        paths:\n"
            "          - drafts/**\n"
        )

        storage, _cross_repo, _saw = parse_settings_block(block, "internal")

        assert storage is not None
        self.assertEqual(len(storage.path_rules), 1)
        rule = storage.path_rules[0]
        self.assertEqual(rule.get("path"), "docs")
        self.assertEqual(rule.get("includes"), ["**/*.md"])
        self.assertEqual(rule.get("excludes"), ["drafts/**"])
        self.assertEqual(rule.get("include_file_types"), [".md"])

    def test_storage_path_rules_capture_storage_destination_and_lists(self) -> None:
        # pathRules nested *under* storage use direct includes:/excludes: keys
        # (indent 8) and a per-rule storage: override.
        block = (
            "onboarding:\n"
            "  storage:\n"
            "    mode: repo-sidecar\n"
            "    pathRules:\n"
            "      - path: src/generated\n"
            "        storage: external\n"
            "        includes:\n"
            '          - "**/*.py"\n'
            "        excludes:\n"
            "          - src/generated/tmp/**\n"
        )

        storage, _cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(storage.mode, "repo-sidecar")
        self.assertEqual(len(storage.path_rules), 1)
        rule = storage.path_rules[0]
        self.assertEqual(rule.get("path"), "src/generated")
        self.assertEqual(rule.get("storage"), "external")
        self.assertEqual(rule.get("includes"), ["**/*.py"])
        self.assertEqual(rule.get("excludes"), ["src/generated/tmp/**"])

    def test_repo_keyed_scoped_rule_is_accepted_like_path(self) -> None:
        block = (
            "onboarding:\n"
            "  pathRules:\n"
            "    - repo: neighbor-lib\n"
            "      include:\n"
            "        paths:\n"
            "          - lib/**\n"
        )

        storage, _cross_repo, _saw = parse_settings_block(block, "internal")

        assert storage is not None
        self.assertEqual(len(storage.path_rules), 1)
        rule = storage.path_rules[0]
        self.assertEqual(rule.get("path"), "neighbor-lib")
        self.assertEqual(rule.get("includes"), ["lib/**"])


class MarkdownSettingsCrossRepoTests(unittest.TestCase):
    def test_legacy_string_cross_repo_entry_is_recorded_as_excluded_error(self) -> None:
        # v2 requires expectedBranch; a bare string allow entry must be flagged
        # excluded with the legacy error rather than silently accepted.
        block = "crossRepo:\n  allow:\n    - repo-b\n"

        storage, cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        # crossRepo-only blocks don't carry storage settings.
        self.assertIsNone(storage)
        self.assertEqual(len(cross_repo.allow), 1)
        entry = cross_repo.allow[0]
        self.assertEqual(entry.repo, "repo-b")
        self.assertEqual(entry.expected_branch, "")
        self.assertEqual(entry.state, "excluded")
        self.assertTrue(cross_repo.errors)
        self.assertIn("expectedBranch is required", cross_repo.errors[0])

    def test_inline_list_cross_repo_allow_records_each_repo(self) -> None:
        block = "crossRepo:\n  allow: [repo-b, repo-c]\n"

        _storage, cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        self.assertEqual([entry.repo for entry in cross_repo.allow], ["repo-b", "repo-c"])
        for entry in cross_repo.allow:
            self.assertEqual(entry.state, "excluded")

    def test_empty_cross_repo_allow_list_records_no_entries(self) -> None:
        block = "crossRepo:\n  allow: []\n"

        _storage, cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        self.assertEqual(cross_repo.allow, [])
        self.assertEqual(cross_repo.errors, [])


class MarkdownSettingsCombinedTests(unittest.TestCase):
    def test_full_block_with_storage_path_rules_and_cross_repo(self) -> None:
        block = (
            "onboarding:\n"
            "  storage:\n"
            "    mode: repo-sidecar\n"
            "    default: repo-sidecar\n"
            "  pathRules:\n"
            "    - path: src\n"
            "      include:\n"
            "        paths:\n"
            '          - "**/*.py"\n'
            "      exclude:\n"
            "        paths:\n"
            "          - src/generated/**\n"
            "crossRepo:\n"
            "  allow:\n"
            "    - repo-b\n"
        )

        storage, cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(storage.mode, "repo-sidecar")
        self.assertEqual(storage.default, "repo-sidecar")
        self.assertEqual(len(storage.path_rules), 1)
        rule = storage.path_rules[0]
        self.assertEqual(rule.get("path"), "src")
        self.assertEqual(rule.get("includes"), ["**/*.py"])
        self.assertEqual(rule.get("excludes"), ["src/generated/**"])
        self.assertEqual([entry.repo for entry in cross_repo.allow], ["repo-b"])


class MarkdownSettingsIgnoredLineTests(unittest.TestCase):
    """Lines the parser must read past without acting on them.

    Each step of the parser is a chain of guarded branches whose LAST arm is "none of
    these -- do nothing", and a parser that silently does something on an unrecognised
    line is how a settings file starts meaning two things. These pin the do-nothing arms,
    which the happy-path tests above never reach.
    """

    def test_a_storage_line_at_an_unknown_depth_is_ignored(self) -> None:
        # Indent 6 under `storage:` with no `pathRules:` open: not a config line and not a
        # rule line, so it must change nothing.
        block = "onboarding:\n  storage:\n    mode: repo-sidecar\n      stray: value\n"

        storage, _cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(storage.mode, "repo-sidecar")
        self.assertEqual(storage.path_rules, [])

    def test_an_unrecognised_storage_config_key_is_ignored(self) -> None:
        block = "onboarding:\n  storage:\n    mode: repo-sidecar\n    unknownKey: value\n"

        storage, _cross_repo, _saw = parse_settings_block(block, "internal")

        assert storage is not None
        self.assertEqual(storage.mode, "repo-sidecar")
        self.assertEqual(storage.default, "repo-sidecar")
        self.assertEqual(storage.path_rules, [])

    def test_a_storage_path_rule_body_before_any_rule_is_ignored(self) -> None:
        # `includes:` arrives while no `- path:` has opened a rule, so there is nothing to
        # attach it to and it must not invent one.
        block = (
            "onboarding:\n  storage:\n    mode: repo-sidecar\n    pathRules:\n        includes:\n"
        )

        storage, _cross_repo, _saw = parse_settings_block(block, "internal")

        assert storage is not None
        self.assertEqual(storage.path_rules, [])

    def test_an_unrecognised_line_inside_an_open_storage_rule_is_ignored(self) -> None:
        # A rule is open, so the line reaches the rule-body chain -- but it is neither a
        # `storage:` value, nor a list selector, nor a list item, and must fall through.
        block = (
            "onboarding:\n"
            "  storage:\n"
            "    mode: repo-sidecar\n"
            "    pathRules:\n"
            "      - path: src\n"
            "        unknownKey: value\n"
        )

        storage, _cross_repo, _saw = parse_settings_block(block, "internal")

        assert storage is not None
        self.assertEqual(storage.path_rules, [{"path": "src", "includes": ["*"], "excludes": []}])

    def test_a_global_rule_list_outside_an_eligibility_section_selects_nothing(self) -> None:
        # `paths:` at the global-rule depth with no `include:`/`exclude:` above it has no
        # list to name, so the values under it are dropped rather than guessed into one.
        block = "onboarding:\n  pathRules:\n      paths:\n        - src/**\n"

        storage, _cross_repo, saw_settings = parse_settings_block(block, "internal")

        self.assertTrue(saw_settings)
        assert storage is not None
        self.assertEqual(
            storage.path_rules,
            [
                {
                    "path": "",
                    "includes": ["*"],
                    "excludes": [],
                    "include_file_types": [],
                    "exclude_file_types": [],
                }
            ],
        )

    def test_appending_a_global_value_records_the_sighting_before_it_needs_a_list(self) -> None:
        """The seam method's own contract, asserted where the state machine cannot produce it.

        `append_global_rule_value` is public because `markdown_global_rules` -- the block's
        steps, which live in a sibling module -- calls it. Through `parse` that caller has
        already checked `current_list is not None` (`is_global_rule_list_item`), so the
        guard inside the method is unreachable from a document: it is there because
        `_global_target_list` is typed `list[str] | None` and the type checker requires the
        narrowing. What the guard PINS is the order: the sighting is recorded whatever
        happens, and only the append depends on a list being selected. A method that
        recorded nothing when it could not append would lose the global rule for a
        `pathRules:` block whose eligibility section named no list.
        """
        parser = _MarkdownSettingsParser("internal")
        self.assertIsNone(parser.current_list)

        parser.append_global_rule_value("src/**")

        self.assertTrue(parser.saw_global_path_rule)
        self.assertEqual(
            (parser.include_paths, parser.exclude_paths),
            ([], []),
            "the value was filed into a list that was never selected",
        )
        # And with a list selected it lands there, so the guard is a precondition rather
        # than a switch that turns the method off.
        parser.current_list = "includes"
        parser.append_global_rule_value("src/**")
        self.assertEqual(parser.include_paths, ["src/**"])


if __name__ == "__main__":
    unittest.main()
