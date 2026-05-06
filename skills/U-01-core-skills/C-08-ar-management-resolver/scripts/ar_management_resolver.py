#!/usr/bin/env python3
"""Resolve the active Agents Remember ar-management context for one repository.

Requires Python 3.9+. Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict


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
class CrossRepoSettings:
    allow: list[str] = field(default_factory=list)


@dataclass
class ManagementSelection:
    topology: Literal["internal", "shared"]
    management_root: Path


@dataclass
class ManagementContext:
    topology: Literal["internal", "shared"]
    repo_name: str
    target_repo: Path
    management_root: Path
    onboarding_root: Path
    settings_path: Path
    path_settings_path: Path | None
    task_root: Path
    docs_root: Path
    system_root: Path
    sources_path: Path
    tools_path: Path
    storage: StorageSettings
    path_rules: list[StorageRule]
    cross_repo: CrossRepoSettings


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
    return "repo-sidecar" if topology == "internal" else "shared-root"


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
    configured_default = storage.get("default")
    if configured_default is not None:
        if not isinstance(configured_default, str):
            raise ValueError("storage default must be a string")
        settings.default = clean_scalar(configured_default) or settings.default

    raw_path_rules = onboarding.get("pathRules") if "pathRules" in onboarding else root.get("pathRules")
    settings.path_rules = parse_json_path_rules(raw_path_rules)

    cross_repo = CrossRepoSettings()
    cross_repo_mapping = optional_mapping(root.get("crossRepo"), "crossRepo")
    cross_repo.allow = string_list(cross_repo_mapping.get("allow"), "crossRepo.allow")
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
                if raw_value.startswith("[") and raw_value.endswith("]"):
                    inner = raw_value[1:-1].strip()
                    if inner:
                        cross_repo.allow.extend(clean_scalar(value) for value in inner.split(",") if clean_scalar(value))
                elif raw_value and raw_value != "[]":
                    cross_repo.allow.append(clean_scalar(raw_value))
                continue
            if indent == 4 and in_cross_repo_allow and stripped.startswith("- "):
                value = clean_scalar(stripped[2:])
                if value:
                    cross_repo.allow.append(value)
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
            continue
        if indent == 4 and stripped.startswith("layout:"):
            settings.mode = clean_scalar(stripped.split(":", 1)[1]) or settings.mode
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
    return storage_mode in {"external", "repo-sidecar", "shared-root"}


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


def detect_management_selection(
    repo_name: str,
    target_repo: Path,
    requested_topology: Literal["internal", "shared"] | None = None,
    shared_root_hint: Path | None = None,
    settings_path: Path | None = None,
    agents_repo: Path | None = None,
) -> ManagementSelection:
    if settings_path is not None:
        settings_management_root = settings_path.resolve().parent.parent
        if settings_management_root != (target_repo / "ar-management").resolve():
            return ManagementSelection(topology="shared", management_root=settings_management_root)

    if requested_topology == "internal":
        return ManagementSelection(topology="internal", management_root=(target_repo / "ar-management").resolve())
    if requested_topology == "shared":
        return ManagementSelection(topology="shared", management_root=require_shared_root(shared_root_hint, agents_repo))

    shared_root = resolve_shared_root_hint(shared_root_hint, agents_repo)
    if shared_root is not None and shared_repo_selected(repo_name, shared_root):
        return ManagementSelection(topology="shared", management_root=shared_root)
    return ManagementSelection(topology="internal", management_root=(target_repo / "ar-management").resolve())


def resolve_management_context(
    repo_name: str | None = None,
    workspace_root: Path | None = None,
    requested_topology: Literal["internal", "shared"] | None = None,
    shared_root: Path | None = None,
    settings_path: Path | None = None,
    onboarding_root: Path | None = None,
    target_repo: Path | None = None,
    agents_repo: Path | None = None,
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
        resolved_management_root = resolved_settings_path.parent.parent
        resolved_topology = requested_topology or infer_topology_from_onboarding_root(resolved_onboarding_root)
        storage, cross_repo = parse_management_settings(resolved_settings_path, resolved_topology)
        return build_management_context(
            repo_name=resolved_repo_name,
            target_repo=resolved_target_repo,
            topology=resolved_topology,
            management_root=resolved_management_root,
            onboarding_root=resolved_onboarding_root,
            settings_path=resolved_settings_path,
            storage=storage,
            cross_repo=cross_repo,
        )

    selection = detect_management_selection(
        repo_name=resolved_repo_name,
        target_repo=resolved_target_repo,
        requested_topology=requested_topology,
        shared_root_hint=shared_root,
        settings_path=settings_path,
        agents_repo=resolved_agents_repo,
    )
    resolved_settings_path = settings_path.resolve() if settings_path else selection.management_root / "system" / "settings.md"
    resolved_onboarding_root = (
        selection.management_root / "onboarding"
        if selection.topology == "internal"
        else selection.management_root / "onboarding" / resolved_repo_name
    )
    storage, cross_repo = parse_management_settings(resolved_settings_path, selection.topology)
    return build_management_context(
        repo_name=resolved_repo_name,
        target_repo=resolved_target_repo,
        topology=selection.topology,
        management_root=selection.management_root,
        onboarding_root=resolved_onboarding_root,
        settings_path=resolved_settings_path,
        storage=storage,
        cross_repo=cross_repo,
    )


def build_management_context(
    repo_name: str,
    target_repo: Path,
    topology: Literal["internal", "shared"],
    management_root: Path,
    onboarding_root: Path,
    settings_path: Path,
    storage: StorageSettings,
    cross_repo: CrossRepoSettings,
) -> ManagementContext:
    system_root = management_root / "system"
    path_settings_path = path_settings_path_for(settings_path)
    return ManagementContext(
        topology=topology,
        repo_name=repo_name,
        target_repo=target_repo,
        management_root=management_root,
        onboarding_root=onboarding_root,
        settings_path=settings_path,
        path_settings_path=path_settings_path if path_settings_path.exists() else None,
        task_root=management_root / "tasks",
        docs_root=management_root / "docs",
        system_root=system_root,
        sources_path=system_root / "sources.md",
        tools_path=system_root / "tools.md",
        storage=storage,
        path_rules=storage.path_rules,
        cross_repo=cross_repo,
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


def context_to_dict(context: ManagementContext) -> dict[str, object]:
    return {
        "topology": context.topology,
        "repo_name": context.repo_name,
        "target_repo": path_to_string(context.target_repo),
        "management_root": path_to_string(context.management_root),
        "onboarding_root": path_to_string(context.onboarding_root),
        "settings_path": path_to_string(context.settings_path),
        "path_settings_path": path_to_string(context.path_settings_path) if context.path_settings_path else "",
        "task_root": path_to_string(context.task_root),
        "docs_root": path_to_string(context.docs_root),
        "system_root": path_to_string(context.system_root),
        "sources_path": path_to_string(context.sources_path),
        "tools_path": path_to_string(context.tools_path),
        "storage": storage_to_dict(context.storage),
        "pathRules": [path_rule_to_dict(rule) for rule in context.path_rules],
        "crossRepo": {"allow": context.cross_repo.allow},
    }


def print_text(context: ManagementContext) -> None:
    print(f"topology\t{context.topology}")
    print(f"repo_name\t{context.repo_name}")
    print(f"target_repo\t{context.target_repo.as_posix()}")
    print(f"management_root\t{context.management_root.as_posix()}")
    print(f"onboarding_root\t{context.onboarding_root.as_posix()}")
    print(f"settings_path\t{context.settings_path.as_posix()}")
    if context.path_settings_path is not None:
        print(f"path_settings_path\t{context.path_settings_path.as_posix()}")
    print(f"task_root\t{context.task_root.as_posix()}")
    print(f"docs_root\t{context.docs_root.as_posix()}")
    print(f"storage_mode\t{context.storage.mode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-name", help="Repository name to resolve. This is the normal agent-facing input.")
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd(), help="Workspace root used to find --repo-name.")
    parser.add_argument("--repo", type=Path, help="Compatibility input for callers that already have the repository root path.")
    parser.add_argument("--topology", choices=("internal", "shared"), help="Optional topology override.")
    parser.add_argument("--shared-root", type=Path, help="Optional shared ar-management root hint or override.")
    parser.add_argument("--settings-path", type=Path, help="Optional active settings.md override. A sibling settings.json is preferred for machine-readable path settings when present.")
    parser.add_argument("--onboarding-root", type=Path, help="Compatibility override for an already resolved repo onboarding root.")
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
