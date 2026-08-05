"""The settings block of a coordination-context markdown document.

A line-at-a-time state machine over four blocks -- `onboarding.storage:`,
`onboarding.pathRules:`, its global-rule form, and `crossRepo:`. Every step reads and
writes one shared cursor (`current_rule`, `current_list`, `current_eligibility_section`),
so a step's guards are ordered against the steps around it and none of them means anything
on its own.

`markdown_cross_repo` and `markdown_global_rules` hold the steps of the two blocks that do
not live in this file. They call `rule_list_name` and `append_global_rule_value`, which is
why those two are public alongside `parse`; everything else here is internal to the machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agents_remember.kernel.coordination_context import (
    markdown_cross_repo,
    markdown_global_rules,
)
from agents_remember.kernel.coordination_context.models import (
    CrossRepoSettings,
    StorageRule,
    StorageSettings,
)
from agents_remember.kernel.coordination_context.paths import clean_scalar, default_storage_mode

RuleListName = Literal["includes", "excludes", "include_file_types", "exclude_file_types"]
EligibilitySection = Literal["include", "exclude"]


def parse_settings_block(
    block: str,
    topology: Literal["internal", "external"],
) -> tuple[StorageSettings | None, CrossRepoSettings, bool]:
    return _MarkdownSettingsParser(topology).parse(block)


@dataclass
class _MarkdownSettingsParser:
    topology: Literal["internal", "external"]
    settings: StorageSettings = field(init=False)
    cross_repo: CrossRepoSettings = field(default_factory=CrossRepoSettings)
    in_onboarding: bool = False
    in_storage: bool = False
    in_storage_path_rules: bool = False
    in_path_rules: bool = False
    in_cross_repo: bool = False
    in_cross_repo_allow: bool = False
    current_rule: StorageRule | None = None
    current_list: RuleListName | None = None
    current_eligibility_section: EligibilitySection | None = None
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    include_file_types: list[str] = field(default_factory=list)
    exclude_file_types: list[str] = field(default_factory=list)
    saw_storage: bool = False
    saw_path_rules: bool = False
    saw_global_path_rule: bool = False
    saw_cross_repo: bool = False

    def __post_init__(self) -> None:
        mode = default_storage_mode(self.topology)
        self.settings = StorageSettings(mode=mode, default=mode)

    def parse(self, block: str) -> tuple[StorageSettings | None, CrossRepoSettings, bool]:
        for raw_line in block.splitlines():
            self._handle_line(raw_line)
        markdown_global_rules.append_global_path_rule(self)
        saw_settings = self.saw_storage or self.saw_path_rules or self.saw_cross_repo
        return (
            self.settings if self.saw_storage or self.saw_path_rules else None,
            self.cross_repo,
            saw_settings,
        )

    def rule_list_name(self, stripped: str) -> RuleListName | None:
        if not self.current_eligibility_section:
            return None
        if self.current_eligibility_section == "include":
            return "includes" if stripped == "paths:" else "include_file_types"
        return "excludes" if stripped == "paths:" else "exclude_file_types"

    def append_global_rule_value(self, value: str) -> None:
        self.saw_global_path_rule = True
        target = self._global_target_list()
        if target is not None:
            target.append(value)

    def _handle_line(self, raw_line: str) -> None:
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            return
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            self._handle_top_level(stripped)
        elif self.in_cross_repo:
            markdown_cross_repo.handle_cross_repo_line(self, indent, stripped)
        elif self.in_onboarding:
            self._handle_onboarding_line(indent, stripped)

    def _handle_top_level(self, stripped: str) -> None:
        self.in_onboarding = stripped == "onboarding:"
        self.in_cross_repo = stripped == "crossRepo:"
        self.in_storage = False
        self.in_storage_path_rules = False
        self.in_path_rules = False
        self.in_cross_repo_allow = False
        self._reset_current_rule()

    def _handle_onboarding_line(self, indent: int, stripped: str) -> None:
        if self._try_enter_onboarding_section(indent, stripped):
            return
        if self.in_path_rules:
            self._handle_path_rules_line(indent, stripped)
        elif self.in_storage:
            self._handle_storage_line(indent, stripped)

    def _try_enter_onboarding_section(self, indent: int, stripped: str) -> bool:
        if indent != 2:
            return False
        if stripped == "storage:":
            self._enter_storage()
            return True
        if stripped == "pathRules:":
            self._enter_path_rules()
            return True
        return False

    def _enter_storage(self) -> None:
        self.in_storage = True
        self.in_storage_path_rules = False
        self.in_path_rules = False
        self.saw_storage = True
        self._reset_current_rule()

    def _enter_path_rules(self) -> None:
        self.in_storage = False
        self.in_storage_path_rules = False
        self.in_path_rules = True
        self.saw_path_rules = True
        self._reset_current_rule()

    def _reset_current_rule(self) -> None:
        self.current_rule = None
        self.current_list = None
        self.current_eligibility_section = None

    def _handle_path_rules_line(self, indent: int, stripped: str) -> None:
        if indent == 4 and (stripped.startswith("- path:") or stripped.startswith("- repo:")):
            self.current_rule = self._new_rule_from_line(stripped)
            self.settings.path_rules.append(self.current_rule)
            self.current_list = None
            self.current_eligibility_section = None
        elif self.current_rule is not None:
            self._handle_nested_rule_line(indent, stripped)
        else:
            markdown_global_rules.handle_global_rule_line(self, indent, stripped)

    def _new_rule_from_line(self, stripped: str) -> StorageRule:
        return {
            "path": clean_scalar(stripped.split(":", 1)[1]),
            "includes": ["*"],
            "excludes": [],
        }

    def _handle_nested_rule_line(self, indent: int, stripped: str) -> None:
        if self._try_enter_eligibility_section(indent, stripped):
            return
        if self._try_select_rule_list(indent, stripped):
            return
        if self._is_rule_list_item(indent, stripped):
            self._append_current_rule_value(clean_scalar(stripped[2:]))

    def _try_enter_eligibility_section(self, indent: int, stripped: str) -> bool:
        if indent != 6 or stripped not in {"include:", "exclude:"}:
            return False
        self.current_eligibility_section = "include" if stripped == "include:" else "exclude"
        self.current_list = None
        return True

    def _try_select_rule_list(self, indent: int, stripped: str) -> bool:
        if indent != 8 or stripped not in {"paths:", "fileTypes:"}:
            return False
        self.current_list = self.rule_list_name(stripped)
        self._reset_current_path_list()
        return True

    def _is_rule_list_item(self, indent: int, stripped: str) -> bool:
        """The list-item depth and marker shared by the `pathRules:` and `storage:` blocks."""
        return indent == 10 and stripped.startswith("- ") and self.current_list is not None

    def _reset_current_path_list(self) -> None:
        if self.current_rule is not None and self.current_list in {"includes", "excludes"}:
            self.current_rule[self.current_list] = []

    def _append_current_rule_value(self, value: str) -> None:
        if self.current_rule is not None and self.current_list:
            self.current_rule.setdefault(self.current_list, []).append(value)

    def _global_target_list(self) -> list[str] | None:
        if self.current_list is None:
            return None
        return {
            "includes": self.include_paths,
            "excludes": self.exclude_paths,
            "include_file_types": self.include_file_types,
            "exclude_file_types": self.exclude_file_types,
        }.get(self.current_list)

    def _handle_storage_line(self, indent: int, stripped: str) -> None:
        if indent == 4:
            self._handle_storage_config_line(stripped)
        elif self.in_storage_path_rules:
            self._handle_storage_path_rule_line(indent, stripped)

    def _handle_storage_config_line(self, stripped: str) -> None:
        if self._try_apply_storage_mode(stripped):
            return
        if self._try_apply_storage_default(stripped):
            return
        if stripped == "pathRules:":
            self.in_storage_path_rules = True
            self.current_rule = None
            self.current_list = None

    def _try_apply_storage_mode(self, stripped: str) -> bool:
        if stripped.startswith(("mode:", "layout:")):
            self.settings.mode = clean_scalar(stripped.split(":", 1)[1]) or self.settings.mode
        else:
            return False
        self.settings.default = self.settings.mode
        return True

    def _try_apply_storage_default(self, stripped: str) -> bool:
        if not stripped.startswith("default:"):
            return False
        self.settings.default = clean_scalar(stripped.split(":", 1)[1]) or self.settings.default
        return True

    def _handle_storage_path_rule_line(self, indent: int, stripped: str) -> None:
        """`storage:` carries its own `pathRules:`, at indent 6 rather than 4.

        Its rules also take a `storage:` value and name `includes:`/`excludes:` directly
        instead of through an `include:`/`exclude:` eligibility section, which is why this
        is a separate chain from `_handle_nested_rule_line` rather than a shared one.
        """
        if indent == 6 and stripped.startswith("- path:"):
            self._start_storage_rule(stripped)
        elif self.current_rule is not None:
            self._handle_existing_storage_rule_line(indent, stripped)

    def _start_storage_rule(self, stripped: str) -> None:
        self.current_rule = self._new_rule_from_line(stripped)
        self.settings.path_rules.append(self.current_rule)
        self.current_list = None

    def _handle_existing_storage_rule_line(self, indent: int, stripped: str) -> None:
        if self._try_apply_storage_rule_value(indent, stripped):
            return
        if self._try_select_storage_rule_list(indent, stripped):
            return
        if self._is_rule_list_item(indent, stripped):
            self._append_current_rule_value(clean_scalar(stripped[2:]))

    def _try_apply_storage_rule_value(self, indent: int, stripped: str) -> bool:
        if indent != 8 or not stripped.startswith("storage:") or self.current_rule is None:
            return False
        self.current_rule["storage"] = clean_scalar(stripped.split(":", 1)[1])
        return True

    def _try_select_storage_rule_list(self, indent: int, stripped: str) -> bool:
        if indent != 8 or stripped not in {"includes:", "excludes:"} or self.current_rule is None:
            return False
        self.current_list = "includes" if stripped == "includes:" else "excludes"
        self.current_rule[self.current_list] = []
        return True
