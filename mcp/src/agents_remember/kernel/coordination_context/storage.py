from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath
from typing import Literal

from agents_remember.kernel.coordination_context.models import StorageRule, StorageSettings
from agents_remember.kernel.coordination_context.paths import clean_scalar, normalize_rel_path


def normalize_rule_base(rule_path: str, scoped_repo_path: str) -> str:
    normalized_rule = normalize_rel_path(rule_path)
    normalized_repo = normalize_rel_path(scoped_repo_path)
    if not normalized_rule or normalized_rule == normalized_repo:
        return ""
    if normalized_rule.startswith(f"{normalized_repo}/"):
        return normalized_rule[len(normalized_repo) + 1 :]
    return normalized_rule


def relative_to_rule_base(source_file: str, rule_path: str, scoped_repo_path: str) -> str | None:
    normalized_source = normalize_rel_path(source_file)
    base = normalize_rule_base(rule_path, scoped_repo_path)
    if not base:
        return normalized_source

    source_parts = PurePosixPath(normalized_source).parts
    base_parts = PurePosixPath(base).parts
    if source_parts[: len(base_parts)] != base_parts:
        return None
    remainder = source_parts[len(base_parts) :]
    return "/".join(remainder) if remainder else PurePosixPath(normalized_source).name


def expand_pattern_variants(pattern: str) -> set[str]:
    variants = {pattern}
    queue = [pattern]
    while queue:
        current = queue.pop()
        index = current.find("**/")
        if index == -1:
            continue
        reduced = current[:index] + current[index + 3 :]
        if reduced not in variants:
            variants.add(reduced)
            queue.append(reduced)
    return variants


def matches_any(patterns: list[str], candidate: str) -> bool:
    normalized_candidate = normalize_rel_path(candidate)
    return any(
        fnmatch.fnmatchcase(normalized_candidate, variant)
        for pattern in patterns
        for variant in expand_pattern_variants(pattern)
    )


def rule_patterns(
    rule: StorageRule, key: Literal["includes", "excludes"], default: list[str]
) -> list[str]:
    values = rule.get(key)
    if isinstance(values, list):
        return [str(value) for value in values]
    return default.copy()


def normalize_file_type(value: str) -> str:
    normalized = clean_scalar(value).lower()
    if not normalized:
        return ""
    return normalized if normalized.startswith(".") else f".{normalized}"


def rule_file_types(
    rule: StorageRule,
    key: Literal["include_file_types", "exclude_file_types"],
) -> set[str]:
    values = rule.get(key)
    if not isinstance(values, list):
        return set()
    return {normalized for value in values if (normalized := normalize_file_type(str(value)))}


def source_file_type(source_file: str) -> str:
    return PurePosixPath(normalize_rel_path(source_file)).suffix.lower()


def matches_file_type(rule: StorageRule, source_file: str) -> bool:
    included = rule_file_types(rule, "include_file_types")
    return not included or source_file_type(source_file) in included


def excludes_file_type(rule: StorageRule, source_file: str) -> bool:
    excluded = rule_file_types(rule, "exclude_file_types")
    return source_file_type(source_file) in excluded


def sidecar_storage_label(storage_mode: str) -> bool:
    return storage_mode in {"repo-sidecar", "memory-repo"}


def resolve_storage_for_source(
    source_file: str, settings: StorageSettings, scoped_repo_path: str
) -> str:
    normalized_source = normalize_rel_path(source_file)
    if not settings.path_rules:
        return default_rule_storage(settings)

    for rule in settings.path_rules:
        storage = resolve_storage_from_rule(rule, normalized_source, settings, scoped_repo_path)
        if storage:
            return storage
    return default_unmatched_storage(settings)


def default_rule_storage(settings: StorageSettings) -> str:
    return (settings.default or "external") if settings.mode == "hybrid" else settings.mode


def default_unmatched_storage(settings: StorageSettings) -> str:
    return (settings.default or "external") if settings.mode == "hybrid" else "disabled"


def resolve_storage_from_rule(
    rule: StorageRule,
    normalized_source: str,
    settings: StorageSettings,
    scoped_repo_path: str,
) -> str:
    relative_source = relative_to_rule_base(
        normalized_source, str(rule.get("path", "")), scoped_repo_path
    )
    if relative_source is None or not rule_includes_source(rule, relative_source):
        return ""
    if rule_excludes_source(rule, relative_source):
        return "disabled"
    if settings.mode == "hybrid":
        return str(rule.get("storage", settings.default or "external"))
    return settings.mode


def rule_includes_source(rule: StorageRule, relative_source: str) -> bool:
    includes = rule_patterns(rule, "includes", ["*"])
    return matches_any(includes, relative_source) and matches_file_type(rule, relative_source)


def rule_excludes_source(rule: StorageRule, relative_source: str) -> bool:
    excludes = rule_patterns(rule, "excludes", [])
    return (excludes and matches_any(excludes, relative_source)) or excludes_file_type(
        rule, relative_source
    )
