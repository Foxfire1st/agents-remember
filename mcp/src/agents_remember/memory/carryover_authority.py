"""Required target-memory storage authority for carryover writes."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.errors import AuthorityError
from agents_remember.kernel.coordination_context.models import StorageSettings
from agents_remember.kernel.coordination_context.paths import (
    clean_scalar,
    extract_yaml_blocks,
    path_settings_path_for,
)
from agents_remember.kernel.coordination_context.settings import (
    parse_coordination_settings,
    parse_settings_block,
)

SUPPORTED_STORAGE_MODES = {
    "disabled",
    "external",
    "hybrid",
    "inline",
    "memory-repo",
    "repo-sidecar",
}
SUPPORTED_STORAGE_DESTINATIONS = SUPPORTED_STORAGE_MODES - {"hybrid"}


def required_target_storage(target_memory: Path) -> StorageSettings:
    """Resolve explicit recovery-leaf storage settings or refuse carryover."""

    settings_path = target_memory / "system" / "settings.md"
    json_path = path_settings_path_for(settings_path)
    if not json_path.exists() and not settings_path.exists():
        raise AuthorityError(
            "target memory must provide route-index authority in "
            f"{json_path.as_posix()} or {settings_path.as_posix()}"
        )

    try:
        if json_path.exists():
            raw_settings = json.loads(json_path.read_text(encoding="utf-8"))
            if not _json_declares_effective_route_index_authority(raw_settings):
                raise AuthorityError(
                    f"target memory settings do not declare storage/path authority: {json_path}"
                )
            storage, _cross_repo = parse_coordination_settings(settings_path, "external")
        else:
            blocks = extract_yaml_blocks(settings_path.read_text(encoding="utf-8"))
            storage = _effective_markdown_storage(blocks)
            if storage is None:
                raise AuthorityError(
                    f"target memory settings do not declare storage/path authority: {settings_path}"
                )
        _validate_storage_labels(storage, json_path if json_path.exists() else settings_path)
    except AuthorityError:
        raise
    except (OSError, ValueError) as error:
        authority_path = json_path if json_path.exists() else settings_path
        raise AuthorityError(
            f"invalid target-memory route-index authority in {authority_path}: {error}"
        ) from error
    return storage


def _json_declares_effective_route_index_authority(raw_settings: object) -> bool:
    if not isinstance(raw_settings, dict):
        return True  # The typed settings parser reports the invalid root shape.
    raw_onboarding = raw_settings.get("onboarding", raw_settings)
    if raw_onboarding is None:
        onboarding: dict[str, object] = {}
    elif isinstance(raw_onboarding, dict):
        onboarding = raw_onboarding
    else:
        return True  # The typed settings parser reports the invalid onboarding shape.
    # Mirror parse_json_storage_settings exactly: a null/empty onboarding storage
    # value falls back to root storage. This guard is an authority boundary, not
    # optional hardening: parser defaults must never authorize a carryover write.
    raw_storage = onboarding.get("storage") or raw_settings.get("storage")
    raw_path_rules = (
        onboarding.get("pathRules") if "pathRules" in onboarding else raw_settings.get("pathRules")
    )
    path_rule_state = _json_path_rule_state(raw_path_rules)
    if path_rule_state == "invalid":
        return True  # The typed settings parser reports the invalid rule shape.
    if path_rule_state == "empty-member":
        return False
    return _json_has_storage_selection(raw_storage) or path_rule_state == "effective"


def _json_has_storage_selection(raw_storage: object) -> bool:
    if raw_storage is None:
        return False
    if not isinstance(raw_storage, dict):
        return True  # The typed settings parser reports the invalid storage shape.
    # apply_json_storage_mode selects exactly this value. A truthy mode shadows
    # layout even when clean_scalar later empties it; a falsey mode falls through.
    selected = raw_storage.get("mode") or raw_storage.get("layout")
    if selected is None:
        return False
    if not isinstance(selected, str):
        return True  # The typed settings parser reports the selected invalid type.
    return bool(clean_scalar(selected))


def _json_path_rule_state(raw_path_rules: object) -> str:
    if raw_path_rules is None:
        return "absent"
    if isinstance(raw_path_rules, dict):
        return _json_path_rule_member_state(raw_path_rules)
    if not isinstance(raw_path_rules, list):
        return "invalid"
    return _json_path_rule_list_state(raw_path_rules)


def _json_path_rule_list_state(raw_rules: list[object]) -> str:
    """Fold a rule list into one state; the weakest member decides, and no rules is absent."""
    if not raw_rules:
        return "absent"
    states = [_json_path_rule_member_state(rule) for rule in raw_rules]
    if "invalid" in states:
        return "invalid"
    if "empty-member" in states:
        return "empty-member"
    return "effective"


def _json_path_rule_member_state(raw_rule: object) -> str:
    if not isinstance(raw_rule, dict):
        return "invalid"

    states = [
        _json_rule_path_state(raw_rule),
        _json_rule_storage_state(raw_rule),
        _json_rule_sections_state(raw_rule),
    ]
    if "invalid" in states:
        return "invalid"
    return "effective" if "effective" in states else "empty-member"


def _json_rule_path_state(raw_rule: dict[str, object]) -> str:
    path_value = raw_rule.get("path", raw_rule.get("repo", ""))
    if not isinstance(path_value, str):
        return "invalid"
    return "effective" if clean_scalar(path_value) else "absent"


def _json_rule_storage_state(raw_rule: dict[str, object]) -> str:
    raw_storage = raw_rule.get("storage")
    if raw_storage is None:
        return "absent"
    if not isinstance(raw_storage, str):
        return "invalid"
    return "effective" if clean_scalar(raw_storage) else "absent"


def _json_rule_sections_state(raw_rule: dict[str, object]) -> str:
    states = [_json_rule_section_state(raw_rule.get(name)) for name in ("include", "exclude")]
    if "invalid" in states:
        return "invalid"
    return "effective" if "effective" in states else "absent"


def _json_rule_section_state(raw_section: object) -> str:
    if raw_section is None:
        return "absent"
    if not isinstance(raw_section, dict):
        return "invalid"
    states = [_json_rule_list_state(raw_section.get(name)) for name in ("paths", "fileTypes")]
    if "invalid" in states:
        return "invalid"
    return "effective" if "effective" in states else "absent"


def _json_rule_list_state(raw_values: object) -> str:
    if raw_values is None:
        return "absent"
    if isinstance(raw_values, str):
        return "effective" if clean_scalar(raw_values) else "absent"
    if not isinstance(raw_values, list):
        return "invalid"
    if any(not isinstance(value, str) for value in raw_values):
        return "invalid"
    return (
        "effective"
        if any(clean_scalar(value) for value in raw_values if isinstance(value, str))
        else "absent"
    )


def _effective_markdown_storage(blocks: list[str]) -> StorageSettings | None:
    selected: StorageSettings | None = None
    selected_is_effective = False
    for block in blocks:
        storage, _cross_repo, _saw_settings = parse_settings_block(block, "external")
        if storage is None:
            continue
        selected = storage
        path_rule_state = _markdown_path_rule_state(block)
        selected_is_effective = path_rule_state != "empty-member" and (
            path_rule_state == "effective" or _markdown_has_storage_selection(block)
        )
    return selected if selected_is_effective else None


def _markdown_path_rule_state(block: str) -> str:
    """Classify raw rules without treating parser-injected ``*`` as authority."""

    return _MarkdownRuleAuthorityScanner().scan(block)


@dataclass
class _ScopedRuleAuthority:
    explicit_path: bool
    per_rule_storage: bool = False
    lists: dict[str, bool] = field(default_factory=dict)

    def reset_list(self, name: str) -> None:
        self.lists[name] = False

    def add_list_value(self, name: str, value: str) -> None:
        if clean_scalar(value):
            self.lists[name] = True

    def effective(self) -> bool:
        return self.explicit_path or self.per_rule_storage or any(self.lists.values())


@dataclass
class _MarkdownRuleAuthorityScanner:
    in_onboarding: bool = False
    in_storage: bool = False
    rules_indent: int | None = None
    scoped_rules: list[_ScopedRuleAuthority] = field(default_factory=list)
    current_rule: int | None = None
    current_eligibility: str | None = None
    current_list: str | None = None
    global_rule_started: bool = False
    global_rule_effective: bool = False

    def scan(self, block: str) -> str:
        for raw_line in block.splitlines():
            line = raw_line.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            self.handle_line(indent, line.strip())
        return self.result()

    def handle_line(self, indent: int, stripped: str) -> None:
        if indent == 0:
            self.in_onboarding = stripped == "onboarding:"
            self.in_storage = False
            self._leave_rules()
            return
        if not self.in_onboarding:
            return
        if indent == 2 and stripped in {"storage:", "pathRules:"}:
            self._enter_onboarding_section(stripped)
            return
        if self.in_storage and indent == 4 and stripped == "pathRules:":
            self.rules_indent = 4
            self.current_rule = None
            self.current_list = None
            return
        if self.rules_indent is not None:
            self._handle_rule_line(indent, stripped)

    def _enter_onboarding_section(self, stripped: str) -> None:
        self._leave_rules()
        self.in_storage = stripped == "storage:"
        if stripped == "pathRules:":
            self.rules_indent = 2

    def _leave_rules(self) -> None:
        self.rules_indent = None
        self.current_rule = None
        self.current_eligibility = None
        self.current_list = None

    def _handle_rule_line(self, indent: int, stripped: str) -> None:
        if self._try_start_scoped_rule(indent, stripped):
            return
        if self.current_rule is not None:
            self._handle_scoped_rule_line(indent, stripped)
        else:
            self._handle_global_rule_line(indent, stripped)

    def _try_start_scoped_rule(self, indent: int, stripped: str) -> bool:
        assert self.rules_indent is not None
        prefixes = ("- path:",) if self.rules_indent == 4 else ("- path:", "- repo:")
        if indent != self.rules_indent + 2 or not stripped.startswith(prefixes):
            return False
        self.scoped_rules.append(
            _ScopedRuleAuthority(explicit_path=bool(clean_scalar(stripped.split(":", 1)[1])))
        )
        self.current_rule = len(self.scoped_rules) - 1
        self.current_eligibility = None
        self.current_list = None
        return True

    def _handle_scoped_rule_line(self, indent: int, stripped: str) -> None:
        assert self.current_rule is not None
        if self.rules_indent == 4:
            self._handle_storage_rule_line(indent, stripped)
        else:
            self._handle_standalone_rule_line(indent, stripped)

    def _handle_standalone_rule_line(self, indent: int, stripped: str) -> None:
        if indent == 6 and stripped in {"include:", "exclude:"}:
            self.current_eligibility = "include" if stripped == "include:" else "exclude"
            self.current_list = None
            return
        if indent == 8 and stripped in {"paths:", "fileTypes:"}:
            self.current_list = self._eligibility_list(stripped)
            if self.current_list in {"includes", "excludes"}:
                self._current_scoped_rule().reset_list(self.current_list)
            return
        self._mark_current_rule_from_list_item(indent, stripped)

    def _handle_storage_rule_line(self, indent: int, stripped: str) -> None:
        if indent == 8 and stripped.startswith("storage:"):
            self._current_scoped_rule().per_rule_storage = bool(
                clean_scalar(stripped.split(":", 1)[1])
            )
            return
        if indent == 8 and stripped in {"includes:", "excludes:"}:
            self.current_list = "includes" if stripped == "includes:" else "excludes"
            self._current_scoped_rule().reset_list(self.current_list)
            return
        self._mark_current_rule_from_list_item(indent, stripped)

    def _handle_global_rule_line(self, indent: int, stripped: str) -> None:
        assert self.rules_indent is not None
        if self.rules_indent != 2:
            return
        if indent == 4 and stripped in {"include:", "exclude:"}:
            self.current_eligibility = "include" if stripped == "include:" else "exclude"
            self.current_list = None
            self.global_rule_started = True
            return
        if indent == 6 and stripped in {"paths:", "fileTypes:"}:
            self.current_list = self._eligibility_list(stripped)
            self.global_rule_started = True
            return
        if self.current_list is not None and self._is_nonblank_list_item(indent, stripped, 8):
            self.global_rule_effective = True

    def _eligibility_list(self, stripped: str) -> str | None:
        if self.current_eligibility is None:
            return None
        if self.current_eligibility == "include":
            return "includes" if stripped == "paths:" else "include_file_types"
        return "excludes" if stripped == "paths:" else "exclude_file_types"

    def _mark_current_rule_from_list_item(self, indent: int, stripped: str) -> None:
        if self.current_list is not None and self._is_nonblank_list_item(indent, stripped, 10):
            self._current_scoped_rule().add_list_value(self.current_list, stripped[2:])

    def _current_scoped_rule(self) -> _ScopedRuleAuthority:
        assert self.current_rule is not None
        return self.scoped_rules[self.current_rule]

    @staticmethod
    def _is_nonblank_list_item(indent: int, stripped: str, expected_indent: int) -> bool:
        return (
            indent == expected_indent
            and stripped.startswith("- ")
            and bool(clean_scalar(stripped[2:]))
        )

    def result(self) -> str:
        empty_global_rule = self.global_rule_started and not self.global_rule_effective
        if any(not rule.effective() for rule in self.scoped_rules) or empty_global_rule:
            return "empty-member"
        if self.scoped_rules or self.global_rule_started:
            return "effective"
        return "absent"


def _markdown_has_storage_selection(block: str) -> bool:
    in_onboarding = False
    in_storage = False
    for raw_line in block.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            in_onboarding = stripped == "onboarding:"
            in_storage = False
        elif in_onboarding and indent == 2:
            in_storage = stripped == "storage:"
        elif in_storage and indent == 4 and stripped.startswith(("mode:", "layout:")):
            if clean_scalar(stripped.split(":", 1)[1]):
                return True
    return False


def _validate_storage_labels(storage: StorageSettings, authority_path: Path) -> None:
    if storage.mode not in SUPPORTED_STORAGE_MODES:
        raise AuthorityError(
            f"unsupported target-memory storage mode {storage.mode!r} in {authority_path}"
        )
    if storage.default not in SUPPORTED_STORAGE_DESTINATIONS:
        raise AuthorityError(
            f"unsupported target-memory storage default {storage.default!r} in {authority_path}"
        )
    for index, rule in enumerate(storage.path_rules):
        label = rule.get("storage")
        if label is not None and label not in SUPPORTED_STORAGE_DESTINATIONS:
            raise AuthorityError(
                "unsupported target-memory path-rule storage "
                f"{label!r} at index {index} in {authority_path}"
            )
