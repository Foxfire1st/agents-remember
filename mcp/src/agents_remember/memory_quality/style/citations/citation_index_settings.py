"""The shared citation-index register: the memory layer's own settings keys.

Two settings blocks decide what the citation source index may read and how much of it:

``onboarding.pathRules.exclude.paths``
    The exclusion register the exclusion review agrees with the user **before** memory
    closeout and persists (ruled 2026-08-21: judgement first, no new MCP tool). It is the
    same key the storage resolver and the drift check already honour, read here through the
    same matcher, so one register means one thing across the quality surface.

``onboarding.citationIndex``
    Optional cap overrides. The module constants in :mod:`source_index_state` remain the
    defaults; this block exists so an operator can move a bound deliberately, in the same
    file as ``pathRules``, instead of editing code.

Both blocks are read from the memory layer's ``system/settings.json``. The file is optional
and either key may appear at the ``onboarding`` level or at the document root, exactly as the
existing JSON settings parser accepts ``pathRules`` in both places. A malformed *value* is a
typed refusal naming the key -- never a silent fallback to the default, because a cap that
silently ignores its configuration is the failure mode this whole change exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from agents_remember.memory_quality.style.citations.source_index_state import (
    CitationIndexCaps,
    SourceIndexError,
)

SETTINGS_RELATIVE_PATH = "system/settings.json"
CITATION_INDEX_KEY = "citationIndex"
PATH_RULES_KEY = "pathRules"
ONBOARDING_KEY = "onboarding"
# The exact accepted override keys, in the order they are reported.
CAP_KEYS = ("maxFileBytes", "maxSourceBytes", "maxSourceFiles", "hardStopBytes")


@dataclass(frozen=True)
class CitationIndexSettings:
    """The two settings blocks one acquisition reads, or their defaults when unset."""

    excludes: tuple[str, ...] = ()
    caps: CitationIndexCaps = field(default_factory=CitationIndexCaps)
    settings_path: str | None = None

    @property
    def configured(self) -> bool:
        """Whether the memory layer's settings supplied anything at all."""
        return bool(self.excludes) or bool(self.caps.overridden)


def read_citation_index_settings(memory_root: Path) -> CitationIndexSettings:
    """Read ``system/settings.json`` under ``memory_root``, or the defaults when it is absent.

    A missing file is the ordinary state of a fresh memory layer and means "no register yet",
    which is a legitimate answer. An unreadable or malformed file is refused by name: falling
    back to the defaults would silently index paths the user excluded.
    """
    path = memory_root / SETTINGS_RELATIVE_PATH
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CitationIndexSettings()
    except OSError as error:
        raise SourceIndexError(f"citation index settings {path} is unreadable: {error}") from error
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SourceIndexError(
            f"citation index settings {path} is not valid JSON: {error}"
        ) from error
    if not isinstance(document, dict):
        raise SourceIndexError(f"citation index settings {path} must be a JSON object")
    onboarding = document.get(ONBOARDING_KEY)
    if not isinstance(onboarding, dict):
        onboarding = document
    return CitationIndexSettings(
        excludes=read_path_rule_excludes(onboarding, document, path),
        caps=read_citation_index_caps(onboarding, document, path),
        settings_path=path.as_posix(),
    )


def read_path_rule_excludes(
    onboarding: dict[str, object],
    document: dict[str, object],
    settings_path: Path,
) -> tuple[str, ...]:
    """``onboarding.pathRules.exclude.paths``, for either the object or the list rule form."""
    rules = onboarding.get(PATH_RULES_KEY, document.get(PATH_RULES_KEY))
    if rules is None:
        return ()
    entries = rules if isinstance(rules, list) else [rules]
    patterns: list[str] = []
    for index, rule in enumerate(entries):
        if not isinstance(rule, dict):
            raise SourceIndexError(
                f"citation index settings {settings_path} has a non-object "
                f"{PATH_RULES_KEY}[{index}] rule"
            )
        exclude = rule.get("exclude")
        if exclude is None:
            continue
        if not isinstance(exclude, dict):
            raise SourceIndexError(
                f"citation index settings {settings_path} has a non-object "
                f"{PATH_RULES_KEY}[{index}].exclude"
            )
        patterns.extend(_string_list(exclude.get("paths"), settings_path, "exclude.paths"))
    return tuple(patterns)


def read_citation_index_caps(
    onboarding: dict[str, object],
    document: dict[str, object],
    settings_path: Path,
) -> CitationIndexCaps:
    """The optional ``onboarding.citationIndex`` block, over the module constants."""
    block = onboarding.get(CITATION_INDEX_KEY, document.get(CITATION_INDEX_KEY))
    if block is None:
        return CitationIndexCaps()
    if not isinstance(block, dict):
        raise SourceIndexError(
            f"citation index settings {settings_path} has a non-object {CITATION_INDEX_KEY} block"
        )
    unknown = sorted(set(block) - set(CAP_KEYS))
    if unknown:
        raise SourceIndexError(
            f"citation index settings {settings_path} has unknown {CITATION_INDEX_KEY} key(s): "
            f"{', '.join(unknown)}; accepted: {', '.join(CAP_KEYS)}"
        )
    caps = CitationIndexCaps()
    supplied: list[str] = []
    mapping = {
        "maxFileBytes": "max_file_bytes",
        "maxSourceBytes": "max_source_bytes",
        "maxSourceFiles": "max_source_files",
        "hardStopBytes": "hard_stop_bytes",
    }
    for key in CAP_KEYS:
        if key not in block:
            continue
        value = _positive_integer(block[key], settings_path, key)
        caps = replace(caps, **{mapping[key]: value})
        supplied.append(key)
    if caps.hard_stop_bytes < caps.max_source_bytes:
        raise SourceIndexError(
            f"citation index settings {settings_path}: {CITATION_INDEX_KEY}.hardStopBytes "
            f"({caps.hard_stop_bytes}) must not be below maxSourceBytes "
            f"({caps.max_source_bytes}). The hard stop is the bound past which no index is "
            f"built at all; raise it, or lower maxSourceBytes in the same block."
        )
    if caps.hard_stop_bytes < caps.max_file_bytes:
        raise SourceIndexError(
            f"citation index settings {settings_path}: {CITATION_INDEX_KEY}.hardStopBytes "
            f"({caps.hard_stop_bytes}) must not be below maxFileBytes ({caps.max_file_bytes}). "
            f"Set both keys together."
        )
    return replace(caps, overridden=tuple(supplied))


def _string_list(value: object, settings_path: Path, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(one, str) for one in value):
        raise SourceIndexError(
            f"citation index settings {settings_path} has a non-string-list {label}"
        )
    return [str(one) for one in value]


def _positive_integer(value: object, settings_path: Path, key: str) -> int:
    if type(value) is not int or value <= 0:
        raise SourceIndexError(
            f"citation index settings {settings_path}: {CITATION_INDEX_KEY}.{key} must be a "
            f"positive integer, not {value!r}"
        )
    return value
