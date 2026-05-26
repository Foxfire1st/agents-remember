from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from agents_remember.kernel.coordination_context.models import (
    CrossRepoSettings,
    StorageRule,
    StorageSettings,
)
from agents_remember.kernel.coordination_context.paths import (
    clean_scalar,
    default_storage_mode,
    normalize_rel_path,
)
from agents_remember.kernel.coordination_context.setting_values import (
    optional_mapping,
    parse_cross_repo_allow,
    require_mapping,
    string_list,
)


def parse_json_settings(
    settings_path: Path,
    topology: Literal["internal", "external"],
) -> tuple[StorageSettings, CrossRepoSettings]:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON settings in {settings_path}: {error}") from error

    root = require_mapping(data, "settings.json root")
    onboarding = (
        optional_mapping(root.get("onboarding"), "onboarding") if "onboarding" in root else root
    )
    return parse_json_storage_settings(root, onboarding, topology), parse_json_cross_repo_settings(
        root
    )


def parse_json_storage_settings(
    root: dict[str, object],
    onboarding: dict[str, object],
    topology: Literal["internal", "external"],
) -> StorageSettings:
    mode = default_storage_mode(topology)
    settings = StorageSettings(mode=mode, default=mode)
    storage = optional_mapping(onboarding.get("storage") or root.get("storage"), "storage")
    apply_json_storage_mode(settings, storage)
    raw_path_rules = (
        onboarding.get("pathRules") if "pathRules" in onboarding else root.get("pathRules")
    )
    settings.path_rules = parse_json_path_rules(raw_path_rules)
    return settings


def apply_json_storage_mode(settings: StorageSettings, storage: dict[str, object]) -> None:
    apply_json_storage_name(settings, storage.get("mode") or storage.get("layout"))
    configured_default = storage.get("default")
    if configured_default is not None:
        if not isinstance(configured_default, str):
            raise ValueError("storage default must be a string")
        settings.default = clean_scalar(configured_default) or settings.default


def apply_json_storage_name(settings: StorageSettings, configured_mode: object) -> None:
    if configured_mode is None:
        return
    if not isinstance(configured_mode, str):
        raise ValueError("storage mode/layout must be a string")
    settings.mode = clean_scalar(configured_mode) or settings.mode
    settings.default = settings.mode


def parse_json_cross_repo_settings(root: dict[str, object]) -> CrossRepoSettings:
    cross_repo = CrossRepoSettings()
    cross_repo_mapping = optional_mapping(root.get("crossRepo"), "crossRepo")
    cross_repo.allow, cross_repo.errors = parse_cross_repo_allow(
        cross_repo_mapping.get("allow"), "crossRepo.allow"
    )
    return cross_repo


def parse_json_path_rules(value: object) -> list[StorageRule]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [parse_json_path_rule(value, "pathRules")]
    if not isinstance(value, list):
        raise ValueError("pathRules must be an object or list of objects")
    return [parse_json_path_rule(rule, f"pathRules[{index}]") for index, rule in enumerate(value)]


def parse_json_path_rule(value: object, label: str) -> StorageRule:
    rule = require_mapping(value, label)
    path_value = rule.get("path", rule.get("repo", ""))
    if not isinstance(path_value, str):
        raise ValueError(f"{label}.path must be a string")
    parsed_rule: StorageRule = {
        "path": normalize_rel_path(path_value),
        "includes": ["*"],
        "excludes": [],
    }
    apply_json_path_rule_storage(parsed_rule, rule.get("storage"), label)
    apply_json_path_rule_eligibility(parsed_rule, rule, label)
    return parsed_rule


def apply_json_path_rule_storage(rule: StorageRule, storage: object, label: str) -> None:
    if storage is None:
        return
    if not isinstance(storage, str):
        raise ValueError(f"{label}.storage must be a string")
    rule["storage"] = clean_scalar(storage)


def apply_json_path_rule_eligibility(
    parsed_rule: StorageRule, rule: dict[str, object], label: str
) -> None:
    include = optional_mapping(rule.get("include"), f"{label}.include")
    exclude = optional_mapping(rule.get("exclude"), f"{label}.exclude")
    parsed_rule["includes"] = string_list(include.get("paths"), f"{label}.include.paths", ["*"])
    parsed_rule["excludes"] = string_list(exclude.get("paths"), f"{label}.exclude.paths")
    parsed_rule["include_file_types"] = string_list(
        include.get("fileTypes"), f"{label}.include.fileTypes"
    )
    parsed_rule["exclude_file_types"] = string_list(
        exclude.get("fileTypes"), f"{label}.exclude.fileTypes"
    )
