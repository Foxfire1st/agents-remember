from __future__ import annotations

from pathlib import Path
from typing import Literal

from agents_remember.kernel.coordination_context.json_settings import (
    apply_json_storage_mode,
    parse_json_cross_repo_settings,
    parse_json_path_rule,
    parse_json_path_rules,
    parse_json_settings,
    parse_json_storage_settings,
)
from agents_remember.kernel.coordination_context.markdown_settings import parse_settings_block
from agents_remember.kernel.coordination_context.models import CrossRepoSettings, StorageSettings
from agents_remember.kernel.coordination_context.paths import (
    default_storage_mode,
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
    "parse_settings_block",
    "require_mapping",
    "required_clean_string",
    "string_list",
]


def parse_coordination_settings(
    settings_path: Path,
    topology: Literal["internal", "external"],
) -> tuple[StorageSettings, CrossRepoSettings]:
    mode = default_storage_mode(topology)
    fallback_storage = StorageSettings(mode=mode, default=mode)
    fallback_cross_repo = CrossRepoSettings()
    path_settings_path = path_settings_path_for(settings_path)
    if path_settings_path.exists():
        return parse_json_settings(path_settings_path, topology)

    if not settings_path.exists():
        return fallback_storage, fallback_cross_repo

    selected_storage: StorageSettings | None = None
    selected_cross_repo = CrossRepoSettings()
    for block in extract_yaml_blocks(settings_path.read_text(encoding="utf-8")):
        storage, cross_repo, saw_settings = parse_settings_block(block, topology)
        if not saw_settings:
            continue
        selected_storage = storage or selected_storage
        selected_cross_repo = cross_repo if cross_repo.allow else selected_cross_repo
    return selected_storage or fallback_storage, selected_cross_repo
