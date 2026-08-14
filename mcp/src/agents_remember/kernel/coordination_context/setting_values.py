from __future__ import annotations

from agents_remember.kernel.coordination_context.models import CrossRepoAllowEntry
from agents_remember.kernel.coordination_context.paths import clean_scalar


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
    return [cleaned for item in value if (cleaned := clean_list_item(item, label))]


def clean_list_item(item: object, label: str) -> str:
    if not isinstance(item, str):
        raise ValueError(f"{label} must contain only strings")
    return clean_scalar(item)


def optional_bool(value: object, label: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def parse_cross_repo_allow(
    value: object, label: str
) -> tuple[list[CrossRepoAllowEntry], list[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    entries: list[CrossRepoAllowEntry] = []
    errors: list[str] = []
    for index, item in enumerate(value):
        entry, error = parse_cross_repo_allow_entry(item, f"{label}[{index}]")
        entries.append(entry)
        if error:
            errors.append(error)
    return entries, errors


def parse_cross_repo_allow_entry(item: object, item_label: str) -> tuple[CrossRepoAllowEntry, str]:
    if isinstance(item, str):
        return legacy_cross_repo_allow_entry(item, item_label)
    if not isinstance(item, dict):
        reason = "crossRepo.allow entries must be objects"
        return (
            CrossRepoAllowEntry(repo="", expected_branch="", state="excluded", reason=reason),
            f"{item_label}: {reason}",
        )
    return parsed_cross_repo_allow_entry(item, item_label)


def legacy_cross_repo_allow_entry(item: str, item_label: str) -> tuple[CrossRepoAllowEntry, str]:
    reason = "legacy string crossRepo.allow entries are invalid for v2; expectedBranch is required"
    return (
        CrossRepoAllowEntry(
            repo=clean_scalar(item),
            expected_branch="",
            state="excluded",
            reason=reason,
        ),
        f"{item_label}: {reason}",
    )


def parsed_cross_repo_allow_entry(
    item: dict[str, object], item_label: str
) -> tuple[CrossRepoAllowEntry, str]:
    repo_value, repo_reason = required_clean_string(item.get("repo"), "repo is required")
    expected_value, expected_reason = required_clean_string(
        item.get("expectedBranch"), "expectedBranch is required"
    )
    include_code, include_memory, bool_reason = cross_repo_entry_booleans(item, item_label)
    reason = "; ".join(reason for reason in [repo_reason, expected_reason, bool_reason] if reason)
    entry = CrossRepoAllowEntry(
        repo=repo_value,
        expected_branch=expected_value,
        include_code=include_code,
        include_memory=include_memory,
        state="excluded" if reason else "",
        reason=reason,
    )
    return entry, f"{item_label}: {reason}" if reason else ""


def cross_repo_entry_booleans(item: dict[str, object], item_label: str) -> tuple[bool, bool, str]:
    try:
        return (
            optional_bool(item.get("includeCode"), f"{item_label}.includeCode", True),
            optional_bool(item.get("includeMemory"), f"{item_label}.includeMemory", False),
            "",
        )
    except ValueError as error:
        return True, False, str(error)


def required_clean_string(value: object, missing_reason: str) -> tuple[str, str]:
    if not isinstance(value, str) or not clean_scalar(value):
        return "", missing_reason
    return clean_scalar(value), ""
