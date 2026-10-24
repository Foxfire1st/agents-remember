from __future__ import annotations

import json
from pathlib import Path

from agents_remember.kernel.coordination_context.json_settings import (
    apply_json_storage_mode,
    parse_json_cross_repo_settings,
    parse_json_landing_settings,
    parse_json_path_rule,
    parse_json_path_rules,
    parse_json_settings,
    parse_json_storage_settings,
)
from agents_remember.kernel.coordination_context.markdown_settings import parse_settings_block
from agents_remember.kernel.coordination_context.models import (
    CrossRepoSettings,
    LandingSettings,
    StorageSettings,
)
from agents_remember.kernel.coordination_context.paths import (
    extract_yaml_blocks,
    path_settings_path_for,
)
from agents_remember.kernel.coordination_context.setting_values import (
    optional_bool,
    optional_mapping,
    parse_cross_repo_allow,
    parse_cross_repo_allow_entry,
    require_mapping,
    required_clean_string,
    string_list,
)
from agents_remember.kernel.recorded_reads import observed_exists, observed_text

__all__ = [
    "apply_json_storage_mode",
    "optional_bool",
    "optional_mapping",
    "parse_coordination_settings",
    "parse_cross_repo_allow",
    "parse_cross_repo_allow_entry",
    "parse_json_cross_repo_settings",
    "parse_json_path_rule",
    "parse_json_path_rules",
    "parse_json_settings",
    "parse_json_storage_settings",
    "parse_landing_settings",
    "parse_settings_block",
    "require_mapping",
    "required_clean_string",
    "string_list",
]


def parse_landing_settings(
    settings_path: Path,
    *,
    coordination_root: Path | None = None,
) -> LandingSettings:
    """The landing workflow this repository's memory settings choose (MIK-R202@v2 rule 13).

    Read from the same JSON settings file the storage and cross-repo settings come from: the path
    settings file when a coordination root supplies one, otherwise ``settings.json`` beside the
    onboarding file. A repository that declares nothing lands as it always has, ``local``; a value
    this product does not implement is refused by name at the parse, never carried this far.
    """
    path_settings_path = path_settings_path_for(settings_path, coordination_root=coordination_root)
    for candidate in (path_settings_path, settings_path):
        if candidate.suffix != ".json" or not observed_exists(candidate):
            # Markdown settings predate the landing setting and carry no key for it; a repository
            # still on that form lands locally until it moves to JSON settings.
            continue
        try:
            data = json.loads(observed_text(candidate))
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid JSON settings in {candidate}: {error}") from error
        root = require_mapping(data, "settings.json root")
        return parse_json_landing_settings(root)
    return LandingSettings()


def parse_coordination_settings(
    settings_path: Path,
    *,
    coordination_root: Path | None = None,
) -> tuple[StorageSettings, CrossRepoSettings]:
    fallback_storage = StorageSettings()
    fallback_cross_repo = CrossRepoSettings()
    path_settings_path = path_settings_path_for(settings_path, coordination_root=coordination_root)
    if observed_exists(path_settings_path):
        return parse_json_settings(path_settings_path)

    if not observed_exists(settings_path):
        return fallback_storage, fallback_cross_repo

    selected_storage: StorageSettings | None = None
    selected_cross_repo = CrossRepoSettings()
    for block in extract_yaml_blocks(observed_text(settings_path)):
        storage, cross_repo, saw_settings = parse_settings_block(block)
        if not saw_settings:
            continue
        selected_storage = storage or selected_storage
        selected_cross_repo = cross_repo if cross_repo.allow else selected_cross_repo
    return selected_storage or fallback_storage, selected_cross_repo
