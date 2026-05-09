#!/usr/bin/env python3
"""Resolve the active Agents Remember ar-management context for one repository.

Requires Python 3.9+. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypedDict


SHARED_MODULE_ROOT = Path(__file__).resolve().parents[2] / "_shared"
if SHARED_MODULE_ROOT.exists():
    sys.path.insert(0, str(SHARED_MODULE_ROOT))

try:
    from agents_remember.memory_ledger import LedgerError, load_ledger
    from agents_remember.worktree_contract import (
        ContractError,
        WorktreeContract,
        load_contract,
        task_root_candidates,
        task_root_for,
        worktree_group_for,
    )
except ImportError:  # pragma: no cover - keeps compatibility if shared helpers are unavailable during repair.
    LedgerError = ValueError
    ContractError = ValueError
    WorktreeContract = None  # type: ignore[assignment]
    load_ledger = None  # type: ignore[assignment]
    load_contract = None  # type: ignore[assignment]
    task_root_candidates = None  # type: ignore[assignment]
    task_root_for = None  # type: ignore[assignment]
    worktree_group_for = None  # type: ignore[assignment]


class StorageRule(TypedDict, total=False):
    path: str
    storage: str
    includes: list[str]
    excludes: list[str]
    include_file_types: list[str]
    exclude_file_types: list[str]


@dataclass
class StorageSettings:
    mode: str = "repo-sidecar"
    default: str = "repo-sidecar"
    path_rules: list[StorageRule] = field(default_factory=list)


@dataclass
class CrossRepoAllowEntry:
    repo: str
    expected_branch: str
    include_code: bool = True
    include_memory: bool = False
    state: str = ""
    reason: str = ""
    code: dict[str, str] = field(default_factory=dict)
    memory: dict[str, str] = field(default_factory=dict)


@dataclass
class CrossRepoSettings:
    allow: list[CrossRepoAllowEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ManagementSelection:
    topology: Literal["internal", "shared"]
    management_root: Path
    coordination_root: Path
    memory_root: Path
    settings_path: Path


@dataclass
class ManagementContext:
    topology: Literal["internal", "shared"]
    repo_name: str
    target_repo: Path
    coordination_root: Path
    memory_root: Path
    management_root: Path
    onboarding_root: Path
    settings_path: Path
    path_settings_path: Path | None
    task_root: Path
    temp_root: Path
    docs_root: Path
    system_root: Path
    sources_path: Path
    tools_path: Path
    storage: StorageSettings
    path_rules: list[StorageRule]
    cross_repo: CrossRepoSettings
    memory_mode: Literal["internal", "shared", "disabled"]
    contract_path: Path | None = None
    worktree_group: Path | None = None
    code_worktree: Path | None = None
    memory_worktree: Path | None = None
    ledger_path: Path | None = None


def agents_repo_from_script() -> Path:
    return Path(__file__).resolve().parents[4]


def clean_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1]
    if value.startswith(("\"", "'")) and value.endswith(("\"", "'")) and len(value) >= 2:
        value = value[1:-1]
    return value.strip()


def normalize_rel_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def extract_yaml_blocks(markdown_text: str) -> list[str]:
    return [match.group(1) for match in re.finditer(r"```(?:yaml|yml)?\n(.*?)```", markdown_text, re.DOTALL)]


def default_storage_mode(topology: Literal["internal", "shared"]) -> str:
    return "repo-sidecar" if topology == "internal" else "memory-repo"


def internal_memory_root(target_repo: Path) -> Path:
    return (target_repo / "ar-memory").resolve()


def internal_coordination_root(target_repo: Path) -> Path:
    return (target_repo / "ar-management").resolve()


def shared_memory_root(coordination_root: Path, repo_name: str) -> Path:
    return (coordination_root / "memory-repos" / f"ar-{repo_name}").resolve()


def existing_memory_or_legacy_settings_path(
    memory_root: Path,
    coordination_root: Path,
    topology: Literal["internal", "shared"],
) -> Path:
    memory_settings = memory_root / "system" / "settings.md"
    coordination_settings = coordination_root / "system" / "settings.md"
    if memory_settings.exists():
        return memory_settings
    if coordination_settings.exists():
        return coordination_settings
    return memory_settings


def memory_roots_from_settings(
    settings_path: Path,
    target_repo: Path,
    repo_name: str,
    topology: Literal["internal", "shared"],
) -> tuple[Path, Path]:
    settings_root = settings_path.resolve().parent.parent
    if topology == "shared":
        if settings_root.name == f"ar-{repo_name}" and settings_root.parent.name == "memory-repos":
            return settings_root.parent.parent, settings_root
        return settings_root, shared_memory_root(settings_root, repo_name)
    if settings_root.name == "ar-memory":
        return internal_coordination_root(target_repo), settings_root
    return settings_root, internal_memory_root(target_repo)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = clean_scalar(value.split("#", 1)[0])
    return values


def resolve_path_from_declaring_file(value: str, declaring_file: Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (declaring_file.parent / candidate).resolve()


def resolve_shared_root_hint(shared_root: Path | None, agents_repo: Path | None = None) -> Path | None:
    if shared_root is not None:
        return shared_root.resolve()

    resolved_agents_repo = (agents_repo or agents_repo_from_script()).resolve()
    for env_path in (resolved_agents_repo / ".env", resolved_agents_repo / ".env.example"):
        values = parse_env_file(env_path)
        root = values.get("AR_MANAGEMENT_ROOT")
        if root:
            return resolve_path_from_declaring_file(root, env_path)
    return None


def find_repo(workspace_root: Path, repo_name: str) -> Path:
    repo_path = Path(repo_name).expanduser()
    if repo_path.is_absolute() and repo_path.exists():
        return repo_path.resolve()

    direct = (workspace_root / repo_name).resolve()
    if direct.exists():
        return direct

    matches = [path for path in workspace_root.iterdir() if path.is_dir() and path.name == repo_name]
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        raise ValueError(f"multiple repositories named {repo_name!r} found under {workspace_root}")
    raise ValueError(f"repository {repo_name!r} was not found under {workspace_root}")


def infer_settings_path(onboarding_root: Path) -> Path:
    if onboarding_root.name == "onboarding":
        management_root = onboarding_root.parent
    elif onboarding_root.parent.name == "onboarding":
        management_root = onboarding_root.parent.parent
    else:
        management_root = onboarding_root.parent
    return management_root / "system" / "settings.md"


def path_settings_path_for(settings_path: Path) -> Path:
    return settings_path.with_suffix(".json")


def infer_topology_from_onboarding_root(onboarding_root: Path) -> Literal["internal", "shared"]:
    return "shared" if onboarding_root.parent.name == "onboarding" else "internal"


def parse_management_settings(
    settings_path: Path,
    topology: Literal["internal", "shared"],
) -> tuple[StorageSettings, CrossRepoSettings]:
    mode = default_storage_mode(topology)
    fallback_storage = StorageSettings(mode=mode, default=mode)
    fallback_cross_repo = CrossRepoSettings()
    path_settings_path = path_settings_path_for(settings_path)
    if path_settings_path.exists():
        return parse_json_settings(path_settings_path, topology)

    if not settings_path.exists():
        return fallback_storage, fallback_cross_repo

    text = settings_path.read_text(encoding="utf-8")
    selected_storage: StorageSettings | None = None
    selected_cross_repo = CrossRepoSettings()
    for block in extract_yaml_blocks(text):
        storage, cross_repo, saw_settings = parse_settings_block(block, topology)
        if not saw_settings:
            continue
        if storage is not None:
            selected_storage = storage
        if cross_repo.allow:
            selected_cross_repo = cross_repo
    return selected_storage or fallback_storage, selected_cross_repo


def require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def optional_mapping(value: object, label: str) -> dict[str, object]:
    if value is None:
        return {}
    return require_mapping(value, label)


def string_list(value: object, label: str, default: list[str] | None = None) -> list[str]:
    if value is None:
        return default.copy() if default is not None else []
    if isinstance(value, str):
        cleaned = clean_scalar(value)
        return [cleaned] if cleaned else []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a string or list of strings")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} must contain only strings")
        cleaned = clean_scalar(item)
        if cleaned:
            values.append(cleaned)
    return values


def optional_bool(value: object, label: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def parse_cross_repo_allow(value: object, label: str) -> tuple[list[CrossRepoAllowEntry], list[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    entries: list[CrossRepoAllowEntry] = []
    errors: list[str] = []
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if isinstance(item, str):
            repo = clean_scalar(item)
            reason = "legacy string crossRepo.allow entries are invalid for v2; expectedBranch is required"
            errors.append(f"{item_label}: {reason}")
            entries.append(CrossRepoAllowEntry(repo=repo, expected_branch="", state="excluded", reason=reason))
            continue
        if not isinstance(item, dict):
            reason = "crossRepo.allow entries must be objects"
            errors.append(f"{item_label}: {reason}")
            entries.append(CrossRepoAllowEntry(repo="", expected_branch="", state="excluded", reason=reason))
            continue
        repo = item.get("repo")
        expected_branch = item.get("expectedBranch")
        reason_parts: list[str] = []
        if not isinstance(repo, str) or not clean_scalar(repo):
            reason_parts.append("repo is required")
            repo_value = ""
        else:
            repo_value = clean_scalar(repo)
        if not isinstance(expected_branch, str) or not clean_scalar(expected_branch):
            reason_parts.append("expectedBranch is required")
            expected_branch_value = ""
        else:
            expected_branch_value = clean_scalar(expected_branch)
        try:
            include_code = optional_bool(item.get("includeCode"), f"{item_label}.includeCode", True)
            include_memory = optional_bool(item.get("includeMemory"), f"{item_label}.includeMemory", False)
        except ValueError as error:
            include_code = True
            include_memory = False
            reason_parts.append(str(error))
        reason = "; ".join(reason_parts)
        entries.append(
            CrossRepoAllowEntry(
                repo=repo_value,
                expected_branch=expected_branch_value,
                include_code=include_code,
                include_memory=include_memory,
                state="excluded" if reason else "",
                reason=reason,
            )
        )
        if reason:
            errors.append(f"{item_label}: {reason}")
    return entries, errors


def parse_json_settings(
    settings_path: Path,
    topology: Literal["internal", "shared"],
) -> tuple[StorageSettings, CrossRepoSettings]:
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON settings in {settings_path}: {error}") from error

    root = require_mapping(data, "settings.json root")
    onboarding = optional_mapping(root.get("onboarding"), "onboarding") if "onboarding" in root else root
    mode = default_storage_mode(topology)
    settings = StorageSettings(mode=mode, default=mode)
    storage = optional_mapping(onboarding.get("storage") or root.get("storage"), "storage")
    configured_mode = storage.get("mode") or storage.get("layout")
    if configured_mode is not None:
        if not isinstance(configured_mode, str):
            raise ValueError("storage mode/layout must be a string")
        settings.mode = clean_scalar(configured_mode) or settings.mode
        settings.default = settings.mode
    configured_default = storage.get("default")
    if configured_default is not None:
        if not isinstance(configured_default, str):
            raise ValueError("storage default must be a string")
        settings.default = clean_scalar(configured_default) or settings.default

    raw_path_rules = onboarding.get("pathRules") if "pathRules" in onboarding else root.get("pathRules")
    settings.path_rules = parse_json_path_rules(raw_path_rules)

    cross_repo = CrossRepoSettings()
    cross_repo_mapping = optional_mapping(root.get("crossRepo"), "crossRepo")
    cross_repo.allow, cross_repo.errors = parse_cross_repo_allow(cross_repo_mapping.get("allow"), "crossRepo.allow")
    return settings, cross_repo


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
    storage = rule.get("storage")
    if storage is not None:
        if not isinstance(storage, str):
            raise ValueError(f"{label}.storage must be a string")
        parsed_rule["storage"] = clean_scalar(storage)

    include = optional_mapping(rule.get("include"), f"{label}.include")
    exclude = optional_mapping(rule.get("exclude"), f"{label}.exclude")
    parsed_rule["includes"] = string_list(include.get("paths"), f"{label}.include.paths", ["*"])
    parsed_rule["excludes"] = string_list(exclude.get("paths"), f"{label}.exclude.paths")
    parsed_rule["include_file_types"] = string_list(include.get("fileTypes"), f"{label}.include.fileTypes")
    parsed_rule["exclude_file_types"] = string_list(exclude.get("fileTypes"), f"{label}.exclude.fileTypes")
    return parsed_rule


def parse_settings_block(
    block: str,
    topology: Literal["internal", "shared"],
) -> tuple[StorageSettings | None, CrossRepoSettings, bool]:
    mode = default_storage_mode(topology)
    settings = StorageSettings(mode=mode, default=mode)
    cross_repo = CrossRepoSettings()
    in_onboarding = False
    in_storage = False
    in_legacy_path_rules = False
    in_path_rules = False
    in_cross_repo = False
    in_cross_repo_allow = False
    current_rule: StorageRule | None = None
    current_list: Literal["includes", "excludes", "include_file_types", "exclude_file_types"] | None = None
    current_eligibility_section: Literal["include", "exclude"] | None = None
    include_paths: list[str] = []
    exclude_paths: list[str] = []
    include_file_types: list[str] = []
    exclude_file_types: list[str] = []
    saw_storage = False
    saw_path_rules = False
    saw_global_path_rule = False
    saw_cross_repo = False

    for raw_line in block.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            in_onboarding = stripped == "onboarding:"
            in_cross_repo = stripped == "crossRepo:"
            in_storage = False
            in_legacy_path_rules = False
            in_path_rules = False
            in_cross_repo_allow = False
            current_rule = None
            current_list = None
            current_eligibility_section = None
            continue

        if in_cross_repo:
            if indent == 2 and stripped.startswith("allow:"):
                saw_cross_repo = True
                in_cross_repo_allow = True
                raw_value = stripped.split(":", 1)[1].strip()
                reason = "legacy string crossRepo.allow entries are invalid for v2; expectedBranch is required"
                if raw_value.startswith("[") and raw_value.endswith("]"):
                    inner = raw_value[1:-1].strip()
                    if inner:
                        for value in inner.split(","):
                            repo = clean_scalar(value)
                            if repo:
                                cross_repo.allow.append(CrossRepoAllowEntry(repo=repo, expected_branch="", state="excluded", reason=reason))
                                cross_repo.errors.append(reason)
                elif raw_value and raw_value != "[]":
                    repo = clean_scalar(raw_value)
                    cross_repo.allow.append(CrossRepoAllowEntry(repo=repo, expected_branch="", state="excluded", reason=reason))
                    cross_repo.errors.append(reason)
                continue
            if indent == 4 and in_cross_repo_allow and stripped.startswith("- "):
                value = clean_scalar(stripped[2:])
                if value:
                    reason = "legacy string crossRepo.allow entries are invalid for v2; expectedBranch is required"
                    cross_repo.allow.append(CrossRepoAllowEntry(repo=value, expected_branch="", state="excluded", reason=reason))
                    cross_repo.errors.append(reason)
                continue
            continue

        if not in_onboarding:
            continue

        if indent == 2 and stripped == "storage:":
            in_storage = True
            in_legacy_path_rules = False
            in_path_rules = False
            saw_storage = True
            current_rule = None
            current_list = None
            current_eligibility_section = None
            continue
        if indent == 2 and stripped == "pathRules:":
            in_storage = False
            in_legacy_path_rules = False
            in_path_rules = True
            saw_path_rules = True
            current_rule = None
            current_list = None
            current_eligibility_section = None
            continue

        if in_path_rules:
            if indent == 4 and (stripped.startswith("- path:") or stripped.startswith("- repo:")):
                current_rule = {
                    "path": clean_scalar(stripped.split(":", 1)[1]),
                    "includes": ["*"],
                    "excludes": [],
                }
                settings.path_rules.append(current_rule)
                current_list = None
                current_eligibility_section = None
                continue
            if current_rule is not None:
                if indent == 6 and stripped in {"include:", "exclude:"}:
                    current_eligibility_section = "include" if stripped == "include:" else "exclude"
                    current_list = None
                    continue
                if indent == 8 and stripped in {"paths:", "fileTypes:"} and current_eligibility_section:
                    if current_eligibility_section == "include":
                        current_list = "includes" if stripped == "paths:" else "include_file_types"
                    else:
                        current_list = "excludes" if stripped == "paths:" else "exclude_file_types"
                    if current_list == "includes":
                        current_rule["includes"] = []
                    elif current_list == "excludes":
                        current_rule["excludes"] = []
                    continue
                if indent == 10 and stripped.startswith("- ") and current_list:
                    value = clean_scalar(stripped[2:])
                    if current_list == "includes":
                        current_rule.setdefault("includes", []).append(value)
                    elif current_list == "excludes":
                        current_rule.setdefault("excludes", []).append(value)
                    elif current_list == "include_file_types":
                        current_rule.setdefault("include_file_types", []).append(value)
                    else:
                        current_rule.setdefault("exclude_file_types", []).append(value)
                    continue
                continue
            if indent == 4 and stripped in {"include:", "exclude:"}:
                current_eligibility_section = "include" if stripped == "include:" else "exclude"
                current_list = None
                saw_global_path_rule = True
                continue
            if indent == 6 and stripped in {"paths:", "fileTypes:"} and current_eligibility_section:
                if current_eligibility_section == "include":
                    current_list = "includes" if stripped == "paths:" else "include_file_types"
                else:
                    current_list = "excludes" if stripped == "paths:" else "exclude_file_types"
                saw_global_path_rule = True
                continue
            if indent == 8 and stripped.startswith("- ") and current_list:
                value = clean_scalar(stripped[2:])
                saw_global_path_rule = True
                if current_list == "includes":
                    include_paths.append(value)
                elif current_list == "excludes":
                    exclude_paths.append(value)
                elif current_list == "include_file_types":
                    include_file_types.append(value)
                else:
                    exclude_file_types.append(value)
                continue
            continue

        if not in_storage:
            continue

        if indent == 4 and stripped.startswith("mode:"):
            settings.mode = clean_scalar(stripped.split(":", 1)[1]) or "external"
            settings.default = settings.mode
            continue
        if indent == 4 and stripped.startswith("layout:"):
            settings.mode = clean_scalar(stripped.split(":", 1)[1]) or settings.mode
            settings.default = settings.mode
            continue
        if indent == 4 and stripped.startswith("default:"):
            settings.default = clean_scalar(stripped.split(":", 1)[1]) or "external"
            continue
        if indent == 4 and stripped == "pathRules:":
            in_legacy_path_rules = True
            current_rule = None
            current_list = None
            continue
        if not in_legacy_path_rules:
            continue
        if indent == 6 and stripped.startswith("- path:"):
            current_rule = {
                "path": clean_scalar(stripped.split(":", 1)[1]),
                "includes": ["*"],
                "excludes": [],
            }
            settings.path_rules.append(current_rule)
            current_list = None
            continue
        if current_rule is None:
            continue
        if indent == 8 and stripped.startswith("storage:"):
            current_rule["storage"] = clean_scalar(stripped.split(":", 1)[1])
            continue
        if indent == 8 and stripped in {"includes:", "excludes:"}:
            current_list = "includes" if stripped == "includes:" else "excludes"
            current_rule[current_list] = []
            continue
        if indent == 10 and stripped.startswith("- ") and current_list:
            value = clean_scalar(stripped[2:])
            if current_list == "includes":
                current_rule.setdefault("includes", []).append(value)
            elif current_list == "excludes":
                current_rule.setdefault("excludes", []).append(value)
            elif current_list == "include_file_types":
                current_rule.setdefault("include_file_types", []).append(value)
            else:
                current_rule.setdefault("exclude_file_types", []).append(value)

    if saw_global_path_rule:
        settings.path_rules.append(
            {
                "path": "",
                "includes": include_paths or ["*"],
                "excludes": exclude_paths,
                "include_file_types": include_file_types,
                "exclude_file_types": exclude_file_types,
            }
        )

    return settings if saw_storage or saw_path_rules else None, cross_repo, saw_storage or saw_path_rules or saw_cross_repo


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


def rule_patterns(rule: StorageRule, key: Literal["includes", "excludes"], default: list[str]) -> list[str]:
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
    return storage_mode in {"external", "repo-sidecar", "shared-root", "memory-repo"}


def resolve_storage_for_source(source_file: str, settings: StorageSettings, scoped_repo_path: str) -> str:
    normalized_source = normalize_rel_path(source_file)
    rules = settings.path_rules or []

    if not rules:
        return (settings.default or "external") if settings.mode == "hybrid" else settings.mode

    for rule in rules:
        rule_path = str(rule.get("path", ""))
        relative_source = relative_to_rule_base(normalized_source, rule_path, scoped_repo_path)
        if relative_source is None:
            continue

        includes = rule_patterns(rule, "includes", ["*"])
        excludes = rule_patterns(rule, "excludes", [])
        if not matches_any(includes, relative_source):
            continue
        if not matches_file_type(rule, relative_source):
            continue
        if (excludes and matches_any(excludes, relative_source)) or excludes_file_type(rule, relative_source):
            return "disabled"
        if settings.mode == "hybrid":
            return str(rule.get("storage", settings.default or "external"))
        return settings.mode

    return (settings.default or "external") if settings.mode == "hybrid" else "disabled"


def rule_selects_repo(rule: StorageRule, repo_name: str) -> bool:
    rule_path = normalize_rel_path(str(rule.get("path", "")))
    return rule_path == repo_name or rule_path.startswith(f"{repo_name}/")


def shared_repo_selected(repo_name: str, shared_root: Path) -> bool:
    if not shared_root.exists():
        return False
    if (shared_root / "memory-repos" / f"ar-{repo_name}").exists():
        return True
    if (shared_root / "onboarding" / repo_name).exists():
        return True

    settings_path = shared_root / "system" / "settings.md"
    storage, _ = parse_management_settings(settings_path, "shared")
    return any(rule_selects_repo(rule, repo_name) for rule in storage.path_rules)


def require_shared_root(shared_root: Path | None, agents_repo: Path | None = None) -> Path:
    resolved = resolve_shared_root_hint(shared_root, agents_repo)
    if resolved is None:
        raise ValueError("shared topology requires a shared root when no selected shared context can be inferred")
    return resolved


def resolve_contract(
    contract_path: Path | None,
    coordination_root: Path,
    repo_name: str,
    task_name: str | None,
) -> tuple[Any | None, Path | None]:
    candidate: Path | None = contract_path.resolve() if contract_path else None
    if candidate is None and task_name:
        candidates: list[Path] = []
        if task_root_candidates is not None:
            candidates = task_root_candidates(coordination_root, repo_name, task_name)
        elif task_root_for is not None:
            candidates = [task_root_for(coordination_root, repo_name, task_name)]
        for task_root in candidates:
            possible = task_root / "contract.md"
            if possible.exists():
                candidate = possible
                break
    if candidate is None:
        return None, None
    if not candidate.exists():
        return None, candidate
    if load_contract is None:
        return None, candidate
    try:
        return load_contract(candidate), candidate
    except ContractError:
        return None, candidate


def run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={repo_root.as_posix()}", *args],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def git_branch(repo_root: Path) -> str:
    result = run_git(repo_root, ["branch", "--show-current"])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def git_head(repo_root: Path) -> str:
    result = run_git(repo_root, ["rev-parse", "HEAD"])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def resolve_cross_repo_settings(
    settings: CrossRepoSettings,
    workspace_root: Path,
    coordination_root: Path,
) -> CrossRepoSettings:
    resolved = CrossRepoSettings(errors=list(settings.errors))
    for entry in settings.allow:
        resolved.allow.append(resolve_cross_repo_entry(entry, workspace_root, coordination_root))
    return resolved


def resolve_cross_repo_entry(
    entry: CrossRepoAllowEntry,
    workspace_root: Path,
    coordination_root: Path,
) -> CrossRepoAllowEntry:
    if entry.state == "excluded" and entry.reason:
        return entry
    if not entry.repo or not entry.expected_branch:
        return CrossRepoAllowEntry(
            repo=entry.repo,
            expected_branch=entry.expected_branch,
            include_code=entry.include_code,
            include_memory=entry.include_memory,
            state="excluded",
            reason="repo and expectedBranch are required",
        )
    if not entry.include_code:
        return CrossRepoAllowEntry(
            repo=entry.repo,
            expected_branch=entry.expected_branch,
            include_code=entry.include_code,
            include_memory=entry.include_memory,
            state="excluded",
            reason="includeCode=false leaves no minimum code source to validate",
        )
    code_path = (workspace_root / entry.repo).resolve()
    if not code_path.exists():
        return _entry_with_state(entry, "excluded", f"external code path missing: {code_path.as_posix()}")
    code_branch = git_branch(code_path)
    code_head = git_head(code_path)
    code_info = {"path": code_path.as_posix(), "branch": code_branch, "head": code_head}
    if not code_branch:
        return _entry_with_state(entry, "excluded", "external code repo is detached or not a git repository", code_info)
    if code_branch != entry.expected_branch:
        return _entry_with_state(entry, "excluded", f"external code repo is on branch {code_branch}, expected {entry.expected_branch}", code_info)
    if not entry.include_memory:
        return _entry_with_state(entry, "included-code-only", "memory inclusion disabled for this entry", code_info)

    memory_path = (coordination_root / "memory-repos" / f"ar-{entry.repo}").resolve()
    if not memory_path.exists():
        return _entry_with_state(entry, "included-code-only", f"external memory repo missing: {memory_path.as_posix()}", code_info)
    memory_branch = git_branch(memory_path)
    memory_info = {"path": memory_path.as_posix(), "branch": memory_branch, "ledgerPath": (memory_path / "memory.md").as_posix()}
    if memory_branch != entry.expected_branch:
        return _entry_with_state(entry, "included-code-only", f"external memory repo is on branch {memory_branch or 'detached'}, expected {entry.expected_branch}", code_info, memory_info)
    if load_ledger is None:
        return _entry_with_state(entry, "included-code-only", "memory ledger helper is unavailable", code_info, memory_info)
    try:
        ledger = load_ledger(memory_path / "memory.md")
    except LedgerError as error:
        return _entry_with_state(entry, "included-code-only", str(error), code_info, memory_info)
    memory_info.update(
        {
            "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
            "lastMemoryContentCommit": ledger.last_memory_content_commit,
            "trackedCodeBranch": ledger.tracked_code_branch,
            "memoryBranch": ledger.memory_branch,
        }
    )
    if ledger.tracked_code_branch != entry.expected_branch:
        return _entry_with_state(entry, "included-code-only", f"memory.md trackedCodeBranch is {ledger.tracked_code_branch}, expected {entry.expected_branch}", code_info, memory_info)
    if ledger.memory_branch != entry.expected_branch:
        return _entry_with_state(entry, "included-code-only", f"memory.md memoryBranch is {ledger.memory_branch}, expected {entry.expected_branch}", code_info, memory_info)
    return _entry_with_state(entry, "included", "", code_info, memory_info)


def _entry_with_state(
    entry: CrossRepoAllowEntry,
    state: str,
    reason: str,
    code: dict[str, str] | None = None,
    memory: dict[str, str] | None = None,
) -> CrossRepoAllowEntry:
    return CrossRepoAllowEntry(
        repo=entry.repo,
        expected_branch=entry.expected_branch,
        include_code=entry.include_code,
        include_memory=entry.include_memory,
        state=state,
        reason=reason,
        code=code or {},
        memory=memory or {},
    )


def detect_management_selection(
    repo_name: str,
    target_repo: Path,
    requested_topology: Literal["internal", "shared"] | None = None,
    shared_root_hint: Path | None = None,
    settings_path: Path | None = None,
    agents_repo: Path | None = None,
) -> ManagementSelection:
    if settings_path is not None:
        resolved_settings = settings_path.resolve()
        inferred_topology = requested_topology
        if inferred_topology is None:
            settings_root = resolved_settings.parent.parent
            inferred_topology = "shared" if settings_root != internal_coordination_root(target_repo) and settings_root != internal_memory_root(target_repo) else "internal"
        coordination_root, memory_root = memory_roots_from_settings(resolved_settings, target_repo, repo_name, inferred_topology)
        return ManagementSelection(
            topology=inferred_topology,
            management_root=coordination_root,
            coordination_root=coordination_root,
            memory_root=memory_root,
            settings_path=resolved_settings,
        )

    if requested_topology == "internal":
        coordination_root = internal_coordination_root(target_repo)
        memory_root = internal_memory_root(target_repo)
        settings = existing_memory_or_legacy_settings_path(memory_root, coordination_root, "internal")
        return ManagementSelection("internal", coordination_root, coordination_root, memory_root, settings)
    if requested_topology == "shared":
        coordination_root = require_shared_root(shared_root_hint, agents_repo)
        memory_root = shared_memory_root(coordination_root, repo_name)
        settings = existing_memory_or_legacy_settings_path(memory_root, coordination_root, "shared")
        return ManagementSelection("shared", coordination_root, coordination_root, memory_root, settings)

    shared_root = resolve_shared_root_hint(shared_root_hint, agents_repo)
    if shared_root is not None and shared_repo_selected(repo_name, shared_root):
        coordination_root = shared_root
        memory_root = shared_memory_root(coordination_root, repo_name)
        settings = existing_memory_or_legacy_settings_path(memory_root, coordination_root, "shared")
        return ManagementSelection("shared", coordination_root, coordination_root, memory_root, settings)
    coordination_root = internal_coordination_root(target_repo)
    memory_root = internal_memory_root(target_repo)
    settings = existing_memory_or_legacy_settings_path(memory_root, coordination_root, "internal")
    return ManagementSelection("internal", coordination_root, coordination_root, memory_root, settings)


def resolve_management_context(
    repo_name: str | None = None,
    workspace_root: Path | None = None,
    requested_topology: Literal["internal", "shared"] | None = None,
    shared_root: Path | None = None,
    settings_path: Path | None = None,
    onboarding_root: Path | None = None,
    target_repo: Path | None = None,
    agents_repo: Path | None = None,
    contract_path: Path | None = None,
    task_name: str | None = None,
    worktree_name: str | None = None,
) -> ManagementContext:
    resolved_workspace_root = (workspace_root or Path.cwd()).resolve()
    resolved_agents_repo = (agents_repo or agents_repo_from_script()).resolve()

    if target_repo is not None:
        resolved_target_repo = target_repo.resolve()
        resolved_repo_name = repo_name or resolved_target_repo.name
        resolved_workspace_root = workspace_root.resolve() if workspace_root else resolved_target_repo.parent
    else:
        if not repo_name:
            raise ValueError("repo_name is required when target_repo is not supplied")
        resolved_repo_name = repo_name
        resolved_target_repo = find_repo(resolved_workspace_root, resolved_repo_name)

    if onboarding_root is not None:
        resolved_onboarding_root = onboarding_root.resolve()
        resolved_settings_path = settings_path.resolve() if settings_path else infer_settings_path(resolved_onboarding_root)
        resolved_topology = requested_topology or infer_topology_from_onboarding_root(resolved_onboarding_root)
        coordination_root, memory_root = memory_roots_from_settings(
            resolved_settings_path,
            resolved_target_repo,
            resolved_repo_name,
            resolved_topology,
        )
        storage, cross_repo = parse_management_settings(resolved_settings_path, resolved_topology)
        return build_management_context(
            repo_name=resolved_repo_name,
            target_repo=resolved_target_repo,
            topology=resolved_topology,
            coordination_root=coordination_root,
            memory_root=memory_root,
            onboarding_root=resolved_onboarding_root,
            settings_path=resolved_settings_path,
            storage=storage,
            cross_repo=cross_repo,
            contract_path=contract_path,
            task_name=task_name,
            worktree_name=worktree_name,
            workspace_root=resolved_workspace_root,
        )

    selection = detect_management_selection(
        repo_name=resolved_repo_name,
        target_repo=resolved_target_repo,
        requested_topology=requested_topology,
        shared_root_hint=shared_root,
        settings_path=settings_path,
        agents_repo=resolved_agents_repo,
    )
    resolved_settings_path = settings_path.resolve() if settings_path else selection.settings_path
    legacy_onboarding_root = (
        selection.coordination_root / "onboarding"
        if selection.topology == "internal"
        else selection.coordination_root / "onboarding" / resolved_repo_name
    )
    memory_onboarding_root = selection.memory_root / "onboarding"
    resolved_onboarding_root = memory_onboarding_root if memory_onboarding_root.exists() or not legacy_onboarding_root.exists() else legacy_onboarding_root
    storage, cross_repo = parse_management_settings(resolved_settings_path, selection.topology)
    return build_management_context(
        repo_name=resolved_repo_name,
        target_repo=resolved_target_repo,
        topology=selection.topology,
        coordination_root=selection.coordination_root,
        memory_root=selection.memory_root,
        onboarding_root=resolved_onboarding_root,
        settings_path=resolved_settings_path,
        storage=storage,
        cross_repo=cross_repo,
        contract_path=contract_path,
        task_name=task_name,
        worktree_name=worktree_name,
        workspace_root=resolved_workspace_root,
    )


def build_management_context(
    repo_name: str,
    target_repo: Path,
    topology: Literal["internal", "shared"],
    coordination_root: Path,
    memory_root: Path,
    onboarding_root: Path,
    settings_path: Path,
    storage: StorageSettings,
    cross_repo: CrossRepoSettings,
    contract_path: Path | None = None,
    task_name: str | None = None,
    worktree_name: str | None = None,
    workspace_root: Path | None = None,
) -> ManagementContext:
    memory_system_root = memory_root / "system"
    legacy_system_root = coordination_root / "system"
    system_root = memory_system_root if memory_system_root.exists() else legacy_system_root
    path_settings_path = path_settings_path_for(settings_path)
    resolved_contract = resolve_contract(contract_path, coordination_root, repo_name, task_name)
    contract = resolved_contract[0]
    resolved_contract_path = resolved_contract[1]
    task_root = contract.task_root if contract is not None else (
        task_root_for(coordination_root, repo_name, task_name) if task_name and task_root_for is not None else coordination_root / "tasks"
    )
    temp_root = coordination_root / "temp"
    worktree_group = contract.worktree_group if contract is not None else (
        worktree_group_for(coordination_root, repo_name, worktree_name) if worktree_name and worktree_group_for is not None else None
    )
    code_worktree = contract.code_worktree if contract is not None else None
    memory_worktree = contract.memory_worktree if contract is not None else None
    ledger_path = contract.ledger_path if contract is not None else (
        memory_root / "memory.md" if topology == "shared" else None
    )
    memory_mode = contract.memory_mode if contract is not None else ("internal" if topology == "internal" else "shared")
    if memory_worktree is not None:
        effective_memory_root = memory_worktree
    elif memory_mode == "disabled":
        effective_memory_root = memory_root
    else:
        effective_memory_root = memory_root
    effective_onboarding_root = effective_memory_root / "onboarding" if (effective_memory_root / "onboarding").exists() else onboarding_root
    effective_docs_root = effective_memory_root / "docs" if (effective_memory_root / "docs").exists() else coordination_root / "docs"
    resolved_cross_repo = resolve_cross_repo_settings(
        cross_repo,
        workspace_root or target_repo.parent,
        coordination_root,
    )
    return ManagementContext(
        topology=topology,
        repo_name=repo_name,
        target_repo=target_repo,
        coordination_root=coordination_root,
        memory_root=effective_memory_root,
        management_root=coordination_root,
        onboarding_root=effective_onboarding_root,
        settings_path=settings_path,
        path_settings_path=path_settings_path if path_settings_path.exists() else None,
        task_root=task_root,
        temp_root=temp_root,
        docs_root=effective_docs_root,
        system_root=system_root,
        sources_path=system_root / "sources.md",
        tools_path=system_root / "tools.md",
        storage=storage,
        path_rules=storage.path_rules,
        cross_repo=resolved_cross_repo,
        memory_mode=memory_mode,
        contract_path=resolved_contract_path,
        worktree_group=worktree_group,
        code_worktree=code_worktree,
        memory_worktree=memory_worktree,
        ledger_path=ledger_path,
    )


def path_to_string(path: Path) -> str:
    return path.resolve().as_posix()


def path_rule_to_dict(rule: StorageRule) -> dict[str, object]:
    return {
        "path": str(rule.get("path", "")),
        "storage": str(rule.get("storage", "")) if rule.get("storage") else "",
        "include": {
            "paths": list(rule.get("includes", [])),
            "fileTypes": list(rule.get("include_file_types", [])),
        },
        "exclude": {
            "paths": list(rule.get("excludes", [])),
            "fileTypes": list(rule.get("exclude_file_types", [])),
        },
    }


def storage_to_dict(storage: StorageSettings) -> dict[str, object]:
    return {
        "mode": storage.mode,
        "default": storage.default,
        "pathRules": [path_rule_to_dict(rule) for rule in storage.path_rules],
    }


def cross_repo_entry_to_dict(entry: CrossRepoAllowEntry) -> dict[str, object]:
    payload: dict[str, object] = {
        "repo": entry.repo,
        "expectedBranch": entry.expected_branch,
        "includeCode": entry.include_code,
        "includeMemory": entry.include_memory,
    }
    if entry.state:
        payload["state"] = entry.state
    if entry.reason:
        payload["reason"] = entry.reason
    if entry.code:
        payload["code"] = entry.code
    if entry.memory:
        payload["memory"] = entry.memory
    return payload


def cross_repo_to_dict(cross_repo: CrossRepoSettings) -> dict[str, object]:
    payload: dict[str, object] = {"allow": [cross_repo_entry_to_dict(entry) for entry in cross_repo.allow]}
    if cross_repo.errors:
        payload["errors"] = cross_repo.errors
    return payload


def context_to_dict(context: ManagementContext) -> dict[str, object]:
    return {
        "topology": context.topology,
        "repo_name": context.repo_name,
        "target_repo": path_to_string(context.target_repo),
        "coordination_root": path_to_string(context.coordination_root),
        "memory_root": path_to_string(context.memory_root),
        "memory_mode": context.memory_mode,
        "management_root": path_to_string(context.management_root),
        "onboarding_root": path_to_string(context.onboarding_root),
        "settings_path": path_to_string(context.settings_path),
        "path_settings_path": path_to_string(context.path_settings_path) if context.path_settings_path else "",
        "task_root": path_to_string(context.task_root),
        "temp_root": path_to_string(context.temp_root),
        "docs_root": path_to_string(context.docs_root),
        "system_root": path_to_string(context.system_root),
        "sources_path": path_to_string(context.sources_path),
        "tools_path": path_to_string(context.tools_path),
        "contract_path": path_to_string(context.contract_path) if context.contract_path else "",
        "worktree_group": path_to_string(context.worktree_group) if context.worktree_group else "",
        "code_worktree": path_to_string(context.code_worktree) if context.code_worktree else "",
        "memory_worktree": path_to_string(context.memory_worktree) if context.memory_worktree else "",
        "ledger_path": path_to_string(context.ledger_path) if context.ledger_path else "",
        "storage": storage_to_dict(context.storage),
        "pathRules": [path_rule_to_dict(rule) for rule in context.path_rules],
        "crossRepo": cross_repo_to_dict(context.cross_repo),
    }


def print_text(context: ManagementContext) -> None:
    print(f"topology\t{context.topology}")
    print(f"repo_name\t{context.repo_name}")
    print(f"target_repo\t{context.target_repo.as_posix()}")
    print(f"coordination_root\t{context.coordination_root.as_posix()}")
    print(f"memory_root\t{context.memory_root.as_posix()}")
    print(f"memory_mode\t{context.memory_mode}")
    print(f"management_root\t{context.management_root.as_posix()}")
    print(f"onboarding_root\t{context.onboarding_root.as_posix()}")
    print(f"settings_path\t{context.settings_path.as_posix()}")
    if context.path_settings_path is not None:
        print(f"path_settings_path\t{context.path_settings_path.as_posix()}")
    print(f"task_root\t{context.task_root.as_posix()}")
    print(f"temp_root\t{context.temp_root.as_posix()}")
    print(f"docs_root\t{context.docs_root.as_posix()}")
    print(f"storage_mode\t{context.storage.mode}")
    if context.contract_path is not None:
        print(f"contract_path\t{context.contract_path.as_posix()}")
    if context.worktree_group is not None:
        print(f"worktree_group\t{context.worktree_group.as_posix()}")
    if context.code_worktree is not None:
        print(f"code_worktree\t{context.code_worktree.as_posix()}")
    if context.memory_worktree is not None:
        print(f"memory_worktree\t{context.memory_worktree.as_posix()}")
    if context.ledger_path is not None:
        print(f"ledger_path\t{context.ledger_path.as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-name", help="Repository name to resolve. This is the normal agent-facing input.")
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd(), help="Workspace root used to find --repo-name.")
    parser.add_argument("--repo", type=Path, help="Compatibility input for callers that already have the repository root path.")
    parser.add_argument("--topology", choices=("internal", "shared"), help="Optional topology override.")
    parser.add_argument("--shared-root", type=Path, help="Optional shared ar-management root hint or override.")
    parser.add_argument("--settings-path", type=Path, help="Optional active settings.md override. A sibling settings.json is preferred for machine-readable path settings when present.")
    parser.add_argument("--onboarding-root", type=Path, help="Compatibility override for an already resolved repo onboarding root.")
    parser.add_argument("--contract-path", type=Path, help="Optional worktree task contract.md path to resolve task/worktree context.")
    parser.add_argument("--task-name", help="Optional task name used to locate ar-management/tasks/<repo>/<task-name>-ar/contract.md.")
    parser.add_argument("--worktree-name", help="Optional worktree name used to compute the worktree group when no contract exists.")
    parser.add_argument("--agents-repo", type=Path, help="Optional agents-remember-md checkout path for .env discovery.")
    parser.add_argument("--format", choices=("json", "text"), default="json", help="Output format.")
    args = parser.parse_args(argv)

    try:
        context = resolve_management_context(
            repo_name=args.repo_name,
            workspace_root=args.workspace_root,
            requested_topology=args.topology,
            shared_root=args.shared_root,
            settings_path=args.settings_path,
            onboarding_root=args.onboarding_root,
            target_repo=args.repo,
            agents_repo=args.agents_repo,
            contract_path=args.contract_path,
            task_name=args.task_name,
            worktree_name=args.worktree_name,
        )
    except ValueError as error:
        parser.error(str(error))

    if args.format == "json":
        print(json.dumps(context_to_dict(context), indent=2))
    else:
        print_text(context)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
