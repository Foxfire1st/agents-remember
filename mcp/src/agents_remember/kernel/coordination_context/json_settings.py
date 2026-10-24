from __future__ import annotations

import json
from pathlib import Path

from agents_remember.kernel.coordination_context.models import (
    LANDING_WORKFLOWS,
    CrossRepoSettings,
    LandingSettings,
    StorageRule,
    StorageSettings,
)
from agents_remember.kernel.coordination_context.paths import (
    clean_scalar,
    normalize_rel_path,
)
from agents_remember.kernel.coordination_context.setting_values import (
    optional_mapping,
    parse_cross_repo_allow,
    require_mapping,
    string_list,
)
from agents_remember.kernel.recorded_reads import observed_text


def parse_json_settings(
    settings_path: Path,
) -> tuple[StorageSettings, CrossRepoSettings]:
    try:
        data = json.loads(observed_text(settings_path))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON settings in {settings_path}: {error}") from error

    root = require_mapping(data, "settings.json root")
    onboarding = (
        optional_mapping(root.get("onboarding"), "onboarding") if "onboarding" in root else root
    )
    return parse_json_storage_settings(root, onboarding), parse_json_cross_repo_settings(root)


def parse_json_landing_settings(root: dict[str, object]) -> LandingSettings:
    """The one landing-workflow value this file holds (MIK-R202@v2 rule 13).

    Read from ``landing.workflow`` at the root of the settings file, the same scope
    ``parse_json_cross_repo_settings`` reads ``crossRepo`` from: the settings contract in
    ``parse_json_settings`` normalizes ``onboarding`` for *storage* (wrapper first, root as fallback)
    and reads policy blocks at the root, so the policy block added here is read where policy lives and
    nowhere else. A repo that writes ``landing`` inside ``onboarding`` is therefore not silently
    honoured - it lands locally, exactly as it did before the setting existed.

    A missing key means ``local``, the behavior every repository had before the setting existed. A
    value this product does not implement is refused by name here, at the one place the value is read,
    so no later stage can act on a workflow nobody defined.
    """
    if "landing" not in root:
        return LandingSettings()
    landing = require_mapping(root.get("landing"), "landing")
    if "workflow" not in landing:
        return LandingSettings()
    raw = landing["workflow"]
    if raw is None:
        # An explicit null is a configured value, not an absent one: a repository that writes the key
        # meant to set it, so the reader names the mistake instead of reading it as "land locally".
        raise ValueError(
            "landing.workflow is present but null; a file that sets the key must name one of "
            f"{', '.join(LANDING_WORKFLOWS)} (omit the key, or omit 'landing' entirely, to land "
            "locally)"
        )
    if not isinstance(raw, str):
        raise ValueError(
            f"landing.workflow must be a string, not {type(raw).__name__}; expected one of "
            f"{', '.join(LANDING_WORKFLOWS)}"
        )
    value = clean_scalar(raw)
    if value not in LANDING_WORKFLOWS:
        raise ValueError(
            f"landing.workflow {value!r} is not a landing workflow this product implements; "
            f"expected one of: {', '.join(LANDING_WORKFLOWS)}"
        )
    return LandingSettings(workflow=value)


def parse_json_storage_settings(
    root: dict[str, object],
    onboarding: dict[str, object],
) -> StorageSettings:
    settings = StorageSettings()
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
